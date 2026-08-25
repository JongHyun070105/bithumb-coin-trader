"""Append-only, observational audit records for market-universe scans.

This module deliberately has no dependency on strategy or execution code.  A
scan record describes what the scanner observed; it cannot authorize an order.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
GENESIS_SHA256 = "0" * 64


class ScanLedgerError(ValueError):
    """Raised when scan evidence is invalid or its hash chain is broken."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _utc_timestamp(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise ScanLedgerError(f"{field} must be an ISO timestamp string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScanLedgerError(f"{field} must be a valid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ScanLedgerError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_value(value: Any, field: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ScanLedgerError(f"{field} cannot contain non-finite numbers")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ScanLedgerError(f"{field} keys must be non-empty strings")
            normalized[key] = _json_value(item, f"{field}.{key}")
        return normalized
    if isinstance(value, (list, tuple)):
        return [_json_value(item, field) for item in value]
    raise ScanLedgerError(f"{field} must contain JSON-compatible values")


@dataclass(frozen=True, slots=True)
class ScanAuditSnapshot:
    """One completed scan, including explicit feed health and ranked evidence."""

    observed_at: str
    scan_id: str
    scan_started_at: str
    scan_completed_at: str
    data_timestamp: str | None
    universe_size: int
    markets_scanned: tuple[str, ...]
    markets_skipped: Mapping[str, str]
    feed_health: Mapping[str, Any]
    candidates: tuple[Mapping[str, Any], ...]
    errors: tuple[str, ...] = ()
    source: str = "bithumb-public-api"
    schema_version: int = SCHEMA_VERSION
    previous_sha256: str = GENESIS_SHA256
    canonical_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _utc_timestamp(self.observed_at, "observed_at"))
        object.__setattr__(
            self, "scan_started_at", _utc_timestamp(self.scan_started_at, "scan_started_at")
        )
        object.__setattr__(
            self, "scan_completed_at", _utc_timestamp(self.scan_completed_at, "scan_completed_at")
        )
        if self.data_timestamp is not None:
            object.__setattr__(
                self, "data_timestamp", _utc_timestamp(self.data_timestamp, "data_timestamp")
            )
        if self.scan_completed_at < self.scan_started_at:
            raise ScanLedgerError("scan_completed_at cannot precede scan_started_at")
        if self.schema_version != SCHEMA_VERSION:
            raise ScanLedgerError("unsupported scan snapshot schema")
        if not isinstance(self.scan_id, str) or not self.scan_id.strip():
            raise ScanLedgerError("scan_id must be a non-empty string")
        if (
            isinstance(self.universe_size, bool)
            or not isinstance(self.universe_size, int)
            or self.universe_size < 0
        ):
            raise ScanLedgerError("universe_size must be a non-negative integer")
        markets = tuple(self.markets_scanned)
        if len(set(markets)) != len(markets) or any(
            not isinstance(market, str) or not market.startswith("KRW-") for market in markets
        ):
            raise ScanLedgerError("markets_scanned must contain unique KRW markets")
        object.__setattr__(self, "markets_scanned", markets)
        skipped = _json_value(self.markets_skipped, "markets_skipped")
        if any(not market.startswith("KRW-") or not isinstance(reason, str) for market, reason in skipped.items()):
            raise ScanLedgerError("markets_skipped must map KRW markets to reasons")
        object.__setattr__(self, "markets_skipped", skipped)
        health = _json_value(self.feed_health, "feed_health")
        required_health = {"warning_feed_ok", "ticker_feed_ok", "orderbook_feed_ok", "mcp_ok"}
        if not required_health.issubset(health) or any(
            not isinstance(health[field], bool) for field in required_health
        ):
            raise ScanLedgerError("feed_health requires explicit boolean feed states")
        object.__setattr__(self, "feed_health", health)
        normalized_candidates = tuple(_json_value(candidate, "candidates") for candidate in self.candidates)
        ranks: list[int] = []
        for candidate in normalized_candidates:
            if not isinstance(candidate, Mapping) or not isinstance(candidate.get("market"), str):
                raise ScanLedgerError("each candidate requires a market")
            if not candidate["market"].startswith("KRW-"):
                raise ScanLedgerError("candidate market must be a KRW market")
            rank = candidate.get("rank")
            if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
                raise ScanLedgerError("each candidate requires a positive rank")
            reasons = candidate.get("pass_reasons", [])
            failures = candidate.get("fail_reasons", [])
            if not isinstance(reasons, list) or not isinstance(failures, list):
                raise ScanLedgerError("candidate reasons must be lists")
            ranks.append(rank)
        if ranks != sorted(ranks) or len(set(ranks)) != len(ranks):
            raise ScanLedgerError("candidates must have unique ascending ranks")
        object.__setattr__(self, "candidates", normalized_candidates)
        if isinstance(self.errors, (str, bytes)) or any(
            not isinstance(error, str) or not error.strip() for error in self.errors
        ):
            raise ScanLedgerError("errors must be non-empty strings")
        object.__setattr__(self, "errors", tuple(self.errors))
        if not isinstance(self.source, str) or not self.source.strip():
            raise ScanLedgerError("source must be a non-empty string")
        if len(self.previous_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.previous_sha256):
            raise ScanLedgerError("previous_sha256 must be a lowercase SHA-256 digest")
        if self.canonical_sha256 and (
            len(self.canonical_sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.canonical_sha256)
        ):
            raise ScanLedgerError("canonical_sha256 must be a lowercase SHA-256 digest")


def _snapshot_unsigned(snapshot: ScanAuditSnapshot) -> dict[str, Any]:
    payload = asdict(snapshot)
    payload.pop("canonical_sha256")
    return payload


def _decode_snapshot_lines(raw: bytes) -> list[ScanAuditSnapshot]:
    if raw and not raw.endswith(b"\n"):
        raise ScanLedgerError("scan snapshot ledger has an unterminated final record")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ScanLedgerError("scan snapshot ledger is not valid UTF-8") from exc
    snapshots: list[ScanAuditSnapshot] = []
    previous = GENESIS_SHA256
    for line_number, line in enumerate(lines, start=1):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ScanLedgerError(f"scan snapshot line {line_number} is invalid JSON") from exc
        if not isinstance(payload, dict) or set(payload) != set(ScanAuditSnapshot.__dataclass_fields__):
            raise ScanLedgerError(f"scan snapshot line {line_number} has an invalid schema")
        try:
            snapshot = ScanAuditSnapshot(**payload)
        except TypeError as exc:
            raise ScanLedgerError(f"scan snapshot line {line_number} has invalid fields") from exc
        if snapshot.previous_sha256 != previous:
            raise ScanLedgerError(f"scan snapshot line {line_number} breaks the hash chain")
        if snapshot.canonical_sha256 != _sha256(_snapshot_unsigned(snapshot)):
            raise ScanLedgerError(f"scan snapshot line {line_number} has an invalid canonical hash")
        previous = snapshot.canonical_sha256
        snapshots.append(snapshot)
    return snapshots


def _read_snapshot_descriptor(descriptor: int) -> list[ScanAuditSnapshot]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    return _decode_snapshot_lines(b"".join(chunks))


def read_scan_snapshots(path: str | Path) -> list[ScanAuditSnapshot]:
    source = Path(path)
    if not source.exists():
        return []
    descriptor = os.open(source, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        return _read_snapshot_descriptor(descriptor)
    finally:
        os.close(descriptor)


def append_scan_snapshot(path: str | Path, snapshot: ScanAuditSnapshot) -> ScanAuditSnapshot:
    """Append exactly one completed scan; callers never need to read this in order paths."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        existing = _read_snapshot_descriptor(descriptor)
        previous = existing[-1].canonical_sha256 if existing else GENESIS_SHA256
        unsigned_snapshot = replace(snapshot, previous_sha256=previous, canonical_sha256="")
        signed = replace(
            unsigned_snapshot,
            canonical_sha256=_sha256(_snapshot_unsigned(unsigned_snapshot)),
        )
        payload = (_canonical_json(asdict(signed)) + "\n").encode("utf-8")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("scan snapshot append made no progress")
            view = view[written:]
        os.fsync(descriptor)
        return signed
    finally:
        os.close(descriptor)
