"""Persist official Bithumb market-warning snapshots as reference-only evidence."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
_MARKET_RE = re.compile(r"KRW-[A-Z0-9]{2,15}")
_WARNING_RE = re.compile(r"[A-Z][A-Z0-9_]{2,80}")
_STEPS = {"CAUTION", "WARNING", "DANGER"}


class MarketWarningSignalError(ValueError):
    """Raised when a warning snapshot cannot be represented safely."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _timestamp(value: str, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise MarketWarningSignalError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketWarningSignalError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _source_kst_timestamp(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    if not isinstance(value, str):
        raise MarketWarningSignalError("end_at must be an ISO timestamp")
    normalized = value.strip()
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MarketWarningSignalError("end_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        normalized += "+09:00"
    return _timestamp(normalized, "end_at")


@dataclass(frozen=True, slots=True, order=True)
class MarketWarning:
    market: str
    warning_type: str
    warning_step: str
    end_at: str | None = None

    def __post_init__(self) -> None:
        if not _MARKET_RE.fullmatch(self.market):
            raise MarketWarningSignalError("warning market must be a KRW market")
        if not _WARNING_RE.fullmatch(self.warning_type):
            raise MarketWarningSignalError("warning_type is invalid")
        if self.warning_step not in _STEPS:
            raise MarketWarningSignalError("warning_step is invalid")
        if self.end_at is not None:
            object.__setattr__(self, "end_at", _timestamp(self.end_at, "end_at"))


@dataclass(frozen=True, slots=True)
class MarketWarningSnapshot:
    observed_at: str
    warnings: tuple[MarketWarning, ...]
    snapshot_sha256: str
    source: str = "bithumb-official-market-warning"
    executable: bool = False
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at, "observed_at"))
        normalized = tuple(
            item if isinstance(item, MarketWarning) else MarketWarning(**item)
            for item in self.warnings
        )
        object.__setattr__(self, "warnings", normalized)
        if tuple(sorted(set(normalized))) != normalized:
            raise MarketWarningSignalError("warnings must be sorted and unique")
        if self.schema_version != SCHEMA_VERSION:
            raise MarketWarningSignalError("unsupported warning snapshot schema")
        if self.executable is not False:
            raise MarketWarningSignalError("market warnings are reference-only")
        expected = _snapshot_identity(normalized)
        if self.snapshot_sha256 != expected:
            raise MarketWarningSignalError("warning snapshot digest is invalid")


def _snapshot_identity(warnings: Sequence[MarketWarning]) -> str:
    payload = [asdict(warning) for warning in warnings]
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _unwrap_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MarketWarningSignalError("warning payload is invalid JSON") from exc
    if isinstance(payload, list):
        if not all(isinstance(item, Mapping) for item in payload):
            raise MarketWarningSignalError("warning items must be objects")
        return list(payload)
    if not isinstance(payload, Mapping):
        raise MarketWarningSignalError("warning payload must be an object or list")
    content = payload.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                return _unwrap_items(block["text"])
    current: Any = payload
    for _ in range(5):
        if isinstance(current, list):
            return _unwrap_items(current)
        if not isinstance(current, Mapping) or "data" not in current:
            break
        current = current["data"]
    raise MarketWarningSignalError("warning payload wrapper is missing a list")


def parse_market_warning_snapshot(payload: Any, *, observed_at: str) -> MarketWarningSnapshot:
    warnings: set[MarketWarning] = set()
    for item in _unwrap_items(payload):
        market = item.get("market")
        warning_type = item.get("warning_type")
        warning_step = item.get("warning_step")
        if (
            not isinstance(market, str)
            or not isinstance(warning_type, str)
            or not isinstance(warning_step, str)
        ):
            raise MarketWarningSignalError("warning item fields are invalid")
        warnings.add(
            MarketWarning(
                market=market.upper(),
                warning_type=warning_type.upper(),
                warning_step=warning_step.upper(),
                end_at=_source_kst_timestamp(item.get("end_date") or item.get("end_at")),
            )
        )
    ordered = tuple(sorted(warnings))
    return MarketWarningSnapshot(
        observed_at=observed_at,
        warnings=ordered,
        snapshot_sha256=_snapshot_identity(ordered),
    )


def _decode(raw: bytes) -> list[MarketWarningSnapshot]:
    if raw and not raw.endswith(b"\n"):
        raise MarketWarningSignalError("warning snapshot store has an unterminated record")
    snapshots: list[MarketWarningSnapshot] = []
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        try:
            payload = json.loads(line)
            snapshot = MarketWarningSnapshot(**payload)
        except (json.JSONDecodeError, TypeError, MarketWarningSignalError) as exc:
            raise MarketWarningSignalError(
                f"warning snapshot line {line_number} is invalid"
            ) from exc
        snapshots.append(snapshot)
    return snapshots


def _read_descriptor(descriptor: int) -> list[MarketWarningSnapshot]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while chunk := os.read(descriptor, 1024 * 1024):
        chunks.append(chunk)
    return _decode(b"".join(chunks))


def read_market_warning_snapshots(path: str | Path) -> list[MarketWarningSnapshot]:
    source = Path(path)
    if not source.exists():
        return []
    descriptor = os.open(source, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        return _read_descriptor(descriptor)
    finally:
        os.close(descriptor)


def append_market_warning_snapshot(
    path: str | Path, snapshot: MarketWarningSnapshot
) -> bool:
    """Append only when the official warning state changed."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        existing = _read_descriptor(descriptor)
        if existing and existing[-1].snapshot_sha256 == snapshot.snapshot_sha256:
            return False
        payload = (_canonical_json(asdict(snapshot)) + "\n").encode("utf-8")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("warning snapshot append made no progress")
            view = view[written:]
        os.fsync(descriptor)
        return True
    finally:
        os.close(descriptor)


def format_warning_lines(snapshot: MarketWarningSnapshot, *, limit: int = 3) -> list[str]:
    severity = {"DANGER": 0, "WARNING": 1, "CAUTION": 2}
    ordered = sorted(snapshot.warnings, key=lambda item: (severity[item.warning_step], item.market))
    return [
        f"[빗썸 경보/{item.warning_step}] {item.market} · {item.warning_type}"
        for item in ordered[: max(0, limit)]
    ]
