"""Durable, hash-chained native evidence for closed-hour finalization."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any


TRACE_SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class FinalizationTraceError(RuntimeError):
    """Raised when native finalization evidence cannot be trusted or persisted."""


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _strict_json_loads(payload: bytes | str) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON number: {value}")

    return json.loads(payload, object_pairs_hook=unique_object, parse_constant=reject_constant)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def producer_source_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class FinalizationTrace:
    """Append-only run-scoped event chain, fsynced before each call returns."""

    def __init__(self, root: Path, *, run_id: str, epoch: str) -> None:
        if not run_id or not epoch:
            raise ValueError("finalization trace requires exact run_id and epoch")
        self.root = Path(root)
        self.events_path = self.root / "events.jsonl"
        self.lock_path = self.root / ".trace.lock"
        self.summary_path = self.root.parent / "terminal" / "finalization-trace.json"
        self.run_id = run_id
        self.epoch = epoch
        self.root.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            self._next_sequence, self._previous_hash, self._known_size = self._scan_chain()
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    @staticmethod
    def _event_hash(event: dict[str, Any]) -> str:
        body = {key: value for key, value in event.items() if key != "event_sha256"}
        return hashlib.sha256(_canonical_bytes(body)).hexdigest()

    def _scan_chain(self) -> tuple[int, str | None, int]:
        if not self.events_path.exists():
            return 1, None, 0
        if self.events_path.is_symlink():
            raise FinalizationTraceError("finalization trace event log must not be a symlink")
        raw = self.events_path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise FinalizationTraceError("finalization trace has an incomplete trailing event")
        previous: str | None = None
        expected_sequence = 1
        for line_number, line in enumerate(raw.splitlines(), start=1):
            try:
                event = _strict_json_loads(line)
            except Exception as exc:
                raise FinalizationTraceError(f"invalid finalization event at line {line_number}") from exc
            if not isinstance(event, dict):
                raise FinalizationTraceError(f"invalid finalization event at line {line_number}")
            if event.get("schema_version") != TRACE_SCHEMA_VERSION:
                raise FinalizationTraceError(f"unsupported finalization event schema at line {line_number}")
            if event.get("run_id") != self.run_id or event.get("epoch") != self.epoch:
                raise FinalizationTraceError(f"finalization event identity mismatch at line {line_number}")
            if type(event.get("sequence")) is not int or event["sequence"] != expected_sequence:
                raise FinalizationTraceError(f"finalization event sequence mismatch at line {line_number}")
            if event.get("previous_event_sha256") != previous:
                raise FinalizationTraceError(f"finalization event chain mismatch at line {line_number}")
            digest = event.get("event_sha256")
            if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest) or digest != self._event_hash(event):
                raise FinalizationTraceError(f"finalization event hash mismatch at line {line_number}")
            previous = digest
            expected_sequence += 1
        return expected_sequence, previous, len(raw)

    def append(self, event_type: str, **payload: Any) -> dict[str, Any]:
        if not event_type or not isinstance(event_type, str):
            raise ValueError("event_type must be a non-empty string")
        self.root.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            current_size = self.events_path.stat().st_size if self.events_path.exists() else 0
            if current_size != self._known_size:
                self._next_sequence, self._previous_hash, self._known_size = self._scan_chain()
            event: dict[str, Any] = {
                "schema_version": TRACE_SCHEMA_VERSION,
                "run_id": self.run_id,
                "epoch": self.epoch,
                "sequence": self._next_sequence,
                "event_type": event_type,
                "at_utc": _utc_now(),
                "previous_event_sha256": self._previous_hash,
                "payload": payload,
            }
            event["event_sha256"] = self._event_hash(event)
            encoded = _canonical_bytes(event) + b"\n"
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            created = not self.events_path.exists()
            event_fd = os.open(self.events_path, flags, 0o600)
            try:
                offset = 0
                while offset < len(encoded):
                    written = os.write(event_fd, encoded[offset:])
                    if written <= 0:
                        raise OSError("short write while appending finalization evidence")
                    offset += written
                os.fsync(event_fd)
            finally:
                os.close(event_fd)
            if created:
                dir_fd = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            self._next_sequence += 1
            self._previous_hash = event["event_sha256"]
            self._known_size += len(encoded)
            return event
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def read_events(self) -> list[dict[str, Any]]:
        lock_fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            self._next_sequence, self._previous_hash, self._known_size = self._scan_chain()
            if not self.events_path.exists():
                return []
            return [
                _strict_json_loads(line)
                for line in self.events_path.read_bytes().splitlines()
            ]
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def terminal_summary(self) -> dict[str, Any]:
        events = self.read_events()
        scheduler_retries = 0
        scheduler_pending: Counter[str] = Counter()
        scheduler_failed: set[str] = set()
        finalizer_attempts: Counter[str] = Counter()
        finalized_slots: Counter[str] = Counter()
        recovery_invocations = 0
        closed_at: dict[str, set[str]] = defaultdict(set)
        evidence_hashes: dict[str, set[str]] = defaultdict(set)
        has_recovery_path = False
        terminalization_seen = False
        completed_flushes: set[str] = set()
        for event in events:
            kind = event["event_type"]
            payload = event.get("payload", {})
            if kind == "scheduler_attempt_started":
                cohort = str(payload.get("cohort", ""))
                if scheduler_pending[cohort] or cohort in scheduler_failed:
                    scheduler_retries += 1
                    scheduler_failed.discard(cohort)
                scheduler_pending[cohort] += 1
            elif kind in ("scheduler_attempt_failed", "scheduler_attempt_completed"):
                cohort = str(payload.get("cohort", ""))
                if scheduler_pending[cohort]:
                    scheduler_pending[cohort] -= 1
                if kind == "scheduler_attempt_failed" or payload.get("status") in ("FAIL", "ERROR"):
                    scheduler_failed.add(cohort)
            elif kind == "finalizer_slot_started":
                key = f"{payload.get('cohort', '')}|{payload.get('feed_identity', '')}"
                finalizer_attempts[key] += 1
            elif kind == "recovery_invocation_started":
                recovery_invocations += 1
            elif kind == "recovery_path_available":
                has_recovery_path = (
                    payload.get("callable") is True
                    and payload.get("method") == "FinalizationProgressStore.reconcile"
                )
            elif kind == "terminalization_boundary":
                terminalization_seen = True
            elif kind == "final_flush_completed":
                completed_flushes.add(str(payload.get("cohort", "")))
            elif kind == "slot_finalized":
                cohort = str(payload.get("cohort", ""))
                slot_key = f"{cohort}|{payload.get('feed_identity', '')}"
                finalized_slots[slot_key] += 1
                closed = payload.get("closed_at_utc")
                digest = payload.get("evidence_sha256")
                if isinstance(closed, str) and closed:
                    closed_at[cohort].add(closed)
                if isinstance(digest, str) and SHA256_RE.fullmatch(digest):
                    evidence_hashes[f"{cohort}|{payload.get('feed_identity', '')}"].add(digest)

        finalizer_retries = sum(max(0, count - 1) for count in finalizer_attempts.values())
        duplicates = sum(max(0, count - 1) for count in finalized_slots.values())
        all_slots = [event for event in events if event["event_type"] == "slot_finalized"]
        all_cohorts = {str(event.get("payload", {}).get("cohort", "")) for event in all_slots}
        closed_stable = bool(all_slots) and all(
            len(closed_at.get(cohort, set())) == 1 for cohort in all_cohorts
        ) and all_cohorts.issubset(completed_flushes) and terminalization_seen
        hashes_stable = bool(all_slots) and all(
            SHA256_RE.fullmatch(str(event.get("payload", {}).get("evidence_sha256", "")))
            for event in all_slots
        ) and all(len(values) == 1 for values in evidence_hashes.values()) and terminalization_seen
        event_bytes = b"".join(_canonical_bytes(event) + b"\n" for event in events)
        event_log_hash = hashlib.sha256(event_bytes).hexdigest()
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "epoch": self.epoch,
            "source_classification": "NATIVE",
            "evidence_classification": "NATIVE_INSTRUMENTATION_PRESENT",
            "producer": "bithumb_coin_trader.finalization_trace.FinalizationTrace",
            "producer_schema_version": TRACE_SCHEMA_VERSION,
            "producer_source_sha256": producer_source_sha256(),
            "event_count": len(events),
            "first_event_sha256": events[0]["event_sha256"] if events else None,
            "last_event_sha256": events[-1]["event_sha256"] if events else None,
            "event_log_sha256": event_log_hash,
            "event_log_path": "finalization-trace/events.jsonl",
            "scheduler_retries": scheduler_retries,
            "finalizer_retries": finalizer_retries,
            "recovery_invocations": recovery_invocations,
            "duplicate_finalization": duplicates,
            "closed_at_utc_stable": closed_stable,
            "evidence_hash_stable": hashes_stable,
            "restart_idempotency_path_exposed": has_recovery_path,
            "events": events,
        }

    def write_terminal_summary(self) -> dict[str, Any]:
        summary = self.terminal_summary()
        _atomic_json(self.summary_path, summary)
        return summary
