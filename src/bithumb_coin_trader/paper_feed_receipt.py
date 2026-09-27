"""Append-only, hash-chained receipts for a local public PAPER feed session."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


_ZERO_HASH = "0" * 64
_RECORD_TYPES = {
    "CONNECTION",
    "OBSERVATION",
    "VALIDATION_ERROR",
    "SESSION_TERMINAL",
}


class PaperFeedReceiptError(ValueError):
    """Raised when a public feed receipt is malformed or cannot be appended."""


class PaperFeedReceiptWriter:
    """Write one unique session receipt without retaining raw market payloads."""

    def __init__(
        self,
        path: Path,
        *,
        session_id: str,
        market: str,
        candidate_binding: Mapping[str, str],
        subscription: list[dict[str, Any]],
        started_at_ms: int | None = None,
    ) -> None:
        self.path = Path(path)
        self._sequence = 0
        self._previous_sha256 = _ZERO_HASH
        self._closed = False
        if self.path.is_symlink() or self.path.exists():
            raise PaperFeedReceiptError("feed receipt path must be new and must not be a symlink")
        if not isinstance(session_id, str) or not session_id.strip():
            raise PaperFeedReceiptError("feed receipt session id must be non-empty")
        if not isinstance(market, str) or not market.startswith("KRW-"):
            raise PaperFeedReceiptError("feed receipt market must be an explicit KRW spot market")
        candidate_id = candidate_binding.get("candidate_id")
        experiment_id = candidate_binding.get("experiment_id")
        freeze_hash = candidate_binding.get("freeze_hash")
        if (
            not isinstance(candidate_id, str)
            or not candidate_id
            or not isinstance(experiment_id, str)
            or not experiment_id
            or not _is_sha256(freeze_hash)
        ):
            raise PaperFeedReceiptError("feed receipt requires a verified frozen candidate binding")
        if started_at_ms is not None and (
            isinstance(started_at_ms, bool)
            or not isinstance(started_at_ms, int)
            or started_at_ms < 0
        ):
            raise PaperFeedReceiptError("feed receipt start time must be a non-negative integer")
        if not isinstance(subscription, list) or not subscription:
            raise PaperFeedReceiptError("feed receipt subscription must be a non-empty JSON list")
        try:
            subscription_sha256 = hashlib.sha256(_canonical(subscription)).hexdigest()
            self._fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except (OSError, TypeError, ValueError) as exc:
            raise PaperFeedReceiptError(f"unable to create unique feed receipt: {exc}") from exc
        try:
            self._append({
                "record_type": "SESSION_STARTED",
                "schema_version": 1,
                "session_id": session_id,
                "provider": "BITHUMB_PUBLIC_WEBSOCKET_V1",
                "market": market,
                "candidate_id": candidate_id,
                "experiment_id": experiment_id,
                "candidate_freeze_sha256": freeze_hash,
                "subscription_sha256": subscription_sha256,
                "started_at_ms": started_at_ms if started_at_ms is not None else _now_ms(),
            })
        except Exception:
            self._closed = True
            os.close(self._fd)
            raise

    def record(self, payload: Mapping[str, Any]) -> None:
        if self._closed:
            raise PaperFeedReceiptError("feed receipt is already terminal")
        if not isinstance(payload, Mapping) or payload.get("record_type") not in _RECORD_TYPES:
            raise PaperFeedReceiptError("feed receipt record type is unsupported")
        record_type = payload["record_type"]
        if record_type == "OBSERVATION" and not _is_sha256(payload.get("raw_payload_sha256")):
            raise PaperFeedReceiptError("observation receipt requires a raw-frame SHA-256")
        if record_type in {"CONNECTION", "OBSERVATION", "VALIDATION_ERROR", "SESSION_TERMINAL"}:
            received_at_ms = payload.get("received_at_ms", payload.get("ended_at_ms"))
            if isinstance(received_at_ms, bool) or not isinstance(received_at_ms, int) or received_at_ms < 0:
                raise PaperFeedReceiptError("feed receipt timestamp must be a non-negative integer")
        self._append(dict(payload))
        if record_type == "SESSION_TERMINAL":
            self._closed = True
            os.close(self._fd)

    def finish(self, *, status: str, reason_code: str, ended_at_ms: int | None = None) -> None:
        if self._closed:
            return
        if status not in {"STOPPED", "HALTED", "ERROR", "INTERRUPTED"}:
            raise PaperFeedReceiptError("feed receipt terminal status is invalid")
        if not isinstance(reason_code, str) or not reason_code.strip():
            raise PaperFeedReceiptError("feed receipt terminal reason code must be non-empty")
        self.record({
            "record_type": "SESSION_TERMINAL",
            "status": status,
            "reason_code": reason_code,
            "ended_at_ms": ended_at_ms if ended_at_ms is not None else _now_ms(),
        })

    def __enter__(self) -> "PaperFeedReceiptWriter":
        return self

    def __exit__(self, exc_type: object, _exc: object, _traceback: object) -> None:
        if not self._closed:
            self.finish(
                status="INTERRUPTED" if exc_type is not None else "STOPPED",
                reason_code="PROCESS_EXIT_WITHOUT_FEED_TERMINAL",
            )

    def _append(self, payload: dict[str, Any]) -> None:
        record = {
            **payload,
            "sequence": self._sequence,
            "previous_sha256": self._previous_sha256,
        }
        record["record_sha256"] = hashlib.sha256(_canonical(record)).hexdigest()
        encoded = _canonical(record) + b"\n"
        try:
            offset = 0
            while offset < len(encoded):
                offset += os.write(self._fd, encoded[offset:])
            os.fsync(self._fd)
        except OSError as exc:
            raise PaperFeedReceiptError(f"unable to append durable feed receipt: {exc}") from exc
        self._previous_sha256 = record["record_sha256"]
        self._sequence += 1


def verify_paper_feed_receipt(path: Path) -> dict[str, Any]:
    """Verify one immutable JSONL chain and report whether it has a terminal seal."""
    receipt_path = Path(path)
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise PaperFeedReceiptError("feed receipt must be a regular non-symlink file")
    try:
        encoded_lines = receipt_path.read_bytes().splitlines(keepends=True)
    except OSError as exc:
        raise PaperFeedReceiptError(f"unable to read feed receipt: {exc}") from exc
    if not encoded_lines:
        raise PaperFeedReceiptError("feed receipt is empty")
    previous = _ZERO_HASH
    session_id: str | None = None
    records: list[dict[str, Any]] = []
    for sequence, encoded in enumerate(encoded_lines):
        if not encoded.endswith(b"\n"):
            raise PaperFeedReceiptError("feed receipt has an incomplete final record")
        try:
            payload = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise PaperFeedReceiptError("feed receipt contains invalid JSON") from exc
        if not isinstance(payload, dict) or _canonical(payload) + b"\n" != encoded:
            raise PaperFeedReceiptError("feed receipt record is not canonical JSON")
        recorded_hash = payload.get("record_sha256")
        core = {key: value for key, value in payload.items() if key != "record_sha256"}
        expected_hash = hashlib.sha256(_canonical(core)).hexdigest()
        if (
            payload.get("sequence") != sequence
            or payload.get("previous_sha256") != previous
            or recorded_hash != expected_hash
        ):
            raise PaperFeedReceiptError(f"feed receipt chain integrity failed at sequence {sequence}")
        if sequence == 0:
            if payload.get("record_type") != "SESSION_STARTED" or payload.get("schema_version") != 1:
                raise PaperFeedReceiptError("feed receipt does not begin with a supported session header")
            session_id = payload.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise PaperFeedReceiptError("feed receipt session identity is missing")
        elif payload.get("session_id", session_id) != session_id:
            raise PaperFeedReceiptError("feed receipt session identity changed within the chain")
        if sequence > 0 and payload.get("record_type") not in _RECORD_TYPES:
            raise PaperFeedReceiptError("feed receipt contains an unsupported record")
        records.append(payload)
        previous = str(recorded_hash)
    if any(record.get("record_type") == "SESSION_TERMINAL" for record in records[:-1]):
        raise PaperFeedReceiptError("feed receipt has records after its terminal seal")
    terminal = records[-1].get("record_type") == "SESSION_TERMINAL"
    if terminal and records[-1].get("status") not in {"STOPPED", "HALTED", "ERROR", "INTERRUPTED"}:
        raise PaperFeedReceiptError("feed receipt terminal status is invalid")
    return {
        "session_id": session_id,
        "provider": records[0].get("provider"),
        "market": records[0].get("market"),
        "candidate_id": records[0].get("candidate_id"),
        "candidate_freeze_sha256": records[0].get("candidate_freeze_sha256"),
        "subscription_sha256": records[0].get("subscription_sha256"),
        "record_count": len(records),
        "observation_count": sum(record.get("record_type") == "OBSERVATION" for record in records),
        "terminal_status": records[-1].get("status") if terminal else "OPEN",
        "terminal_reason": records[-1].get("reason_code") if terminal else None,
        "complete": terminal,
        "head_sha256": previous,
    }


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)
