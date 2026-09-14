"""Durable incremental finalization and write-ahead transaction log store.

Enforces:
1. Schema-v5 identity-bound manifests and entries.
2. Single-intent write-ahead transaction log with crash reconciliation.
3. Bounded finalization of dirty tail only (zero historical RAW file reads).
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
import fcntl
import json
import os
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from bithumb_coin_trader.evidence_hashing import (
    canonical_json_bytes,
    canonical_sha256,
    file_sha256,
)

if TYPE_CHECKING:
    from bithumb_coin_trader.microstructure_storage import (
        PartitionManifest,
        RawMicrostructureStorage,
    )

TERMINAL_RECEIPT_STATES = frozenset({
    "ARCHIVED",
    "CLEANUP_ELIGIBLE",
    "CLEANED",
    "SUCCESS",
    "COMPLETED",
    "TERMINAL",
})


class FinalizationState(str, Enum):
    PENDING = "PENDING"
    REUSED = "REUSED"
    RECOMPUTED = "RECOMPUTED"
    FAILED = "FAILED"


class FinalizationEvidenceError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class FinalizationIdentity:
    environment_id: str
    collector_epoch: str
    collector_run_id: str
    cohort: str
    exchange: str
    stream: str
    market: str
    feed_identity: str
    raw_relative_path: str

    @property
    def entry_id(self) -> str:
        return canonical_sha256(asdict(self))


@dataclass(frozen=True)
class ArtifactBinding:
    source_size: int
    source_sha256: str
    source_record_count: int
    manifest_relative_path: str
    manifest_file_sha256: str
    receipt_relative_path: str | None = None
    receipt_file_sha256: str | None = None
    receipt_state: str | None = None
    artifact_kind: str = "RAW_DATA"


@dataclass(frozen=True)
class FinalizationEntry:
    schema_version: int
    entry_id: str
    identity: FinalizationIdentity
    state: FinalizationState
    created_at_utc: str
    started_at_utc: str | None
    completed_at_utc: str | None
    source_size: int | None
    source_sha256: str | None
    source_record_count: int | None
    manifest_relative_path: str | None
    manifest_file_sha256: str | None
    receipt_relative_path: str | None
    receipt_file_sha256: str | None
    receipt_state: str | None
    artifact_kind: str | None
    failure_reason_code: str | None
    entry_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "entry_id": self.entry_id,
            **asdict(self.identity),
            "state": self.state.value,
            "created_at_utc": self.created_at_utc,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "source_size": self.source_size,
            "source_sha256": self.source_sha256,
            "source_record_count": self.source_record_count,
            "manifest_relative_path": self.manifest_relative_path,
            "manifest_file_sha256": self.manifest_file_sha256,
            "receipt_relative_path": self.receipt_relative_path,
            "receipt_file_sha256": self.receipt_file_sha256,
            "receipt_state": self.receipt_state,
            "artifact_kind": self.artifact_kind,
            "failure_reason_code": self.failure_reason_code,
            "entry_sha256": self.entry_sha256,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FinalizationEntry:
        identity = FinalizationIdentity(
            environment_id=str(data["environment_id"]),
            collector_epoch=str(data["collector_epoch"]),
            collector_run_id=str(data["collector_run_id"]),
            cohort=str(data["cohort"]),
            exchange=str(data["exchange"]),
            stream=str(data["stream"]),
            market=str(data["market"]),
            feed_identity=str(data["feed_identity"]),
            raw_relative_path=str(data["raw_relative_path"]),
        )
        entry_id = str(data["entry_id"])
        if entry_id != identity.entry_id:
            raise FinalizationEvidenceError("FINALIZATION_PROGRESS_CORRUPT")

        state_val = data["state"]
        state = FinalizationState(state_val) if isinstance(state_val, str) else state_val
        entry_sha256 = str(data["entry_sha256"])

        computed_sha256 = canonical_sha256(data, excluded=("entry_sha256",))
        if entry_sha256 != computed_sha256:
            raise FinalizationEvidenceError("FINALIZATION_PROGRESS_CORRUPT")

        return cls(
            schema_version=int(data.get("schema_version", 5)),
            entry_id=entry_id,
            identity=identity,
            state=state,
            created_at_utc=str(data["created_at_utc"]),
            started_at_utc=str(data["started_at_utc"]) if data.get("started_at_utc") is not None else None,
            completed_at_utc=str(data["completed_at_utc"]) if data.get("completed_at_utc") is not None else None,
            source_size=int(data["source_size"]) if data.get("source_size") is not None else None,
            source_sha256=str(data["source_sha256"]) if data.get("source_sha256") is not None else None,
            source_record_count=int(data["source_record_count"]) if data.get("source_record_count") is not None else None,
            manifest_relative_path=str(data["manifest_relative_path"]) if data.get("manifest_relative_path") is not None else None,
            manifest_file_sha256=str(data["manifest_file_sha256"]) if data.get("manifest_file_sha256") is not None else None,
            receipt_relative_path=str(data["receipt_relative_path"]) if data.get("receipt_relative_path") is not None else None,
            receipt_file_sha256=str(data["receipt_file_sha256"]) if data.get("receipt_file_sha256") is not None else None,
            receipt_state=str(data["receipt_state"]) if data.get("receipt_state") is not None else None,
            artifact_kind=str(data["artifact_kind"]) if data.get("artifact_kind") is not None else None,
            failure_reason_code=str(data["failure_reason_code"]) if data.get("failure_reason_code") is not None else None,
            entry_sha256=entry_sha256,
        )


@dataclass(frozen=True)
class FinalizationSummary:
    schema_version: int
    state: str
    generation: int
    total_count: int
    pending_count: int
    reused_count: int
    recomputed_count: int
    failed_count: int
    historical_raw_files_opened: int
    historical_raw_bytes_read: int
    last_applied_transaction_id: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FinalizationSummary:
        return cls(
            schema_version=int(data.get("schema_version", 5)),
            state=str(data.get("state", "IN_PROGRESS")),
            generation=int(data.get("generation", 0)),
            total_count=int(data.get("total_count", 0)),
            pending_count=int(data.get("pending_count", 0)),
            reused_count=int(data.get("reused_count", 0)),
            recomputed_count=int(data.get("recomputed_count", 0)),
            failed_count=int(data.get("failed_count", 0)),
            historical_raw_files_opened=int(data.get("historical_raw_files_opened", 0)),
            historical_raw_bytes_read=int(data.get("historical_raw_bytes_read", 0)),
            last_applied_transaction_id=str(data["last_applied_transaction_id"]) if data.get("last_applied_transaction_id") is not None else None,
        )


def _fsync_dir(dir_path: Path) -> None:
    try:
        dfd = os.open(str(dir_path), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def _atomic_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    data_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    try:
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp_path), str(path))
        _fsync_dir(path.parent)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


class FinalizationProgressStore:
    """Run-scoped, crash-safe, write-ahead transaction store for partition finalization progress."""

    def __init__(
        self,
        root: Path,
        *,
        crash_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.root = Path(root)
        self.entries_dir = self.root / "entries"
        self.lock_file = self.root / ".lock"
        self.tx_file = self.root / "transaction.json"
        self.pending_file = self.root / "pending.json"
        self.summary_file = self.root / "summary.json"
        self.crash_hook = crash_hook
        self.root.mkdir(parents=True, exist_ok=True)
        self.entries_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.entries_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.lock_file), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _ensure_initialized(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.entries_dir.mkdir(parents=True, exist_ok=True)
        if not self.summary_file.exists() and not self.pending_file.exists() and not self.tx_file.exists():
            initial_summary = FinalizationSummary(
                schema_version=5,
                state="IN_PROGRESS",
                generation=0,
                total_count=0,
                pending_count=0,
                reused_count=0,
                recomputed_count=0,
                failed_count=0,
                historical_raw_files_opened=0,
                historical_raw_bytes_read=0,
                last_applied_transaction_id=None,
            )
            _atomic_json(self.summary_file, initial_summary.to_dict())
            _atomic_json(self.pending_file, {
                "schema_version": 5,
                "generation": 0,
                "pending_ids": [],
            })

    def _reconcile_locked(self) -> FinalizationSummary:
        self._ensure_initialized()
        if self.tx_file.exists():
            self._reconcile_transaction_locked()

        if not self.summary_file.exists() or not self.pending_file.exists():
            raise FinalizationEvidenceError("FINALIZATION_PROGRESS_CORRUPT")

        try:
            summary_dict = json.loads(self.summary_file.read_text(encoding="utf-8"))
            pending_dict = json.loads(self.pending_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise FinalizationEvidenceError("FINALIZATION_PROGRESS_CORRUPT") from exc

        if summary_dict.get("generation") != pending_dict.get("generation"):
            raise FinalizationEvidenceError("FINALIZATION_PROGRESS_CORRUPT")

        pending_ids = list(pending_dict.get("pending_ids", []))
        cleaned_pending: list[str] = []
        has_terminal = False

        for pid in pending_ids:
            entry_file = self.entries_dir / f"{pid}.json"
            if not entry_file.exists():
                raise FinalizationEvidenceError("FINALIZATION_PROGRESS_CORRUPT")
            try:
                entry_data = json.loads(entry_file.read_text(encoding="utf-8"))
            except Exception as exc:
                raise FinalizationEvidenceError("FINALIZATION_PROGRESS_CORRUPT") from exc

            state = entry_data.get("state")
            if state == FinalizationState.PENDING.value:
                cleaned_pending.append(pid)
            elif state in (
                FinalizationState.REUSED.value,
                FinalizationState.RECOMPUTED.value,
                FinalizationState.FAILED.value,
            ):
                has_terminal = True
            else:
                raise FinalizationEvidenceError("FINALIZATION_PROGRESS_CORRUPT")

        if has_terminal:
            new_gen = summary_dict["generation"] + 1
            summary_dict["generation"] = new_gen
            summary_dict["pending_count"] = len(cleaned_pending)
            pending_dict["generation"] = new_gen
            pending_dict["pending_ids"] = cleaned_pending
            _atomic_json(self.pending_file, pending_dict)
            _atomic_json(self.summary_file, summary_dict)

        return FinalizationSummary.from_dict(summary_dict)

    def _reconcile_transaction_locked(self) -> None:
        try:
            tx_data = json.loads(self.tx_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise FinalizationEvidenceError("FINALIZATION_PROGRESS_CORRUPT") from exc

        entry_id = tx_data["entry_id"]
        before_state = tx_data["before_state"]
        before_hash = tx_data.get("before_hash")
        after_hash = tx_data["after_hash"]
        before_pending_ids = tx_data["before_pending_ids"]
        after_pending_ids = tx_data["after_pending_ids"]
        before_summary = tx_data["before_summary"]
        after_summary = tx_data["after_summary"]
        target_generation = tx_data["target_generation"]

        entry_file = self.entries_dir / f"{entry_id}.json"

        if not entry_file.exists():
            if before_state == "ABSENT":
                # Intent was written, but entry file was not yet created. Roll back!
                _atomic_json(self.pending_file, {
                    "schema_version": 5,
                    "generation": before_summary["generation"],
                    "pending_ids": before_pending_ids,
                })
                _atomic_json(self.summary_file, before_summary)
                self.tx_file.unlink()
                _fsync_dir(self.root)
                return
            else:
                raise FinalizationEvidenceError("FINALIZATION_PROGRESS_CORRUPT")

        # Entry file exists: inspect hash
        try:
            entry_data = json.loads(entry_file.read_text(encoding="utf-8"))
            computed_hash = canonical_sha256(entry_data, excluded=("entry_sha256",))
        except Exception as exc:
            raise FinalizationEvidenceError("FINALIZATION_PROGRESS_CORRUPT") from exc

        if computed_hash != entry_data.get("entry_sha256"):
            raise FinalizationEvidenceError("FINALIZATION_PROGRESS_CORRUPT")

        if before_hash is not None and computed_hash == before_hash:
            # Matches before_hash: rollback!
            _atomic_json(self.pending_file, {
                "schema_version": 5,
                "generation": before_summary["generation"],
                "pending_ids": before_pending_ids,
            })
            _atomic_json(self.summary_file, before_summary)
            self.tx_file.unlink()
            _fsync_dir(self.root)
        elif computed_hash == after_hash:
            # Matches after_hash: rollforward!
            _atomic_json(self.pending_file, {
                "schema_version": 5,
                "generation": target_generation,
                "pending_ids": after_pending_ids,
            })
            _atomic_json(self.summary_file, after_summary)
            self.tx_file.unlink()
            _fsync_dir(self.root)
        else:
            raise FinalizationEvidenceError("FINALIZATION_PROGRESS_CORRUPT")

    def _apply_transition_locked(
        self,
        entry_id: str,
        before_entry: FinalizationEntry | None,
        after_entry: FinalizationEntry,
        pending_ids_updater: Callable[[list[str]], list[str]],
        summary_updater: Callable[[dict[str, Any]], None],
    ) -> FinalizationEntry:
        summary_dict = json.loads(self.summary_file.read_text(encoding="utf-8"))
        pending_dict = json.loads(self.pending_file.read_text(encoding="utf-8"))

        current_gen = summary_dict["generation"]
        target_gen = current_gen + 1

        before_pending_ids = list(pending_dict.get("pending_ids", []))
        after_pending_ids = pending_ids_updater(list(before_pending_ids))

        before_summary = dict(summary_dict)
        after_summary = dict(summary_dict)
        after_summary["generation"] = target_gen
        summary_updater(after_summary)

        tx_id = f"tx-{entry_id[:8]}-{target_gen}-{time.time_ns()}"
        after_summary["last_applied_transaction_id"] = tx_id

        tx_intent = {
            "transaction_id": tx_id,
            "entry_id": entry_id,
            "before_state": "ABSENT" if before_entry is None else before_entry.state.value,
            "before_hash": None if before_entry is None else before_entry.entry_sha256,
            "after_state": after_entry.state.value,
            "after_hash": after_entry.entry_sha256,
            "before_pending_ids": before_pending_ids,
            "after_pending_ids": after_pending_ids,
            "before_summary": before_summary,
            "after_summary": after_summary,
            "target_generation": target_gen,
        }

        # Step 1: Write and fsync transaction.json
        _atomic_json(self.tx_file, tx_intent)
        if self.crash_hook:
            self.crash_hook("intent")

        # Step 2: Atomically write/replace entries/<entry_id>.json
        entry_path = self.entries_dir / f"{entry_id}.json"
        _atomic_json(entry_path, after_entry.to_dict())
        if self.crash_hook:
            self.crash_hook("entry")

        # Step 3: Atomically write/replace pending.json
        _atomic_json(self.pending_file, {
            "schema_version": 5,
            "generation": target_gen,
            "pending_ids": after_pending_ids,
        })
        if self.crash_hook:
            self.crash_hook("pending")

        # Step 4: Atomically write/replace summary.json
        _atomic_json(self.summary_file, after_summary)
        if self.crash_hook:
            self.crash_hook("summary")

        # Step 5: Unlink transaction.json and fsync dir
        self.tx_file.unlink()
        _fsync_dir(self.root)

        return after_entry

    def reconcile(self) -> FinalizationSummary:
        with self._lock():
            return self._reconcile_locked()

    def register_pending(self, identity: FinalizationIdentity) -> FinalizationEntry:
        with self._lock():
            self._reconcile_locked()
            entry_id = identity.entry_id
            entry_file = self.entries_dir / f"{entry_id}.json"
            if entry_file.exists():
                return FinalizationEntry.from_dict(json.loads(entry_file.read_text(encoding="utf-8")))

            now_utc = datetime.now(timezone.utc).isoformat()
            raw_dict = {
                "schema_version": 5,
                "entry_id": entry_id,
                **asdict(identity),
                "state": FinalizationState.PENDING.value,
                "created_at_utc": now_utc,
                "started_at_utc": None,
                "completed_at_utc": None,
                "source_size": None,
                "source_sha256": None,
                "source_record_count": None,
                "manifest_relative_path": None,
                "manifest_file_sha256": None,
                "receipt_relative_path": None,
                "receipt_file_sha256": None,
                "receipt_state": None,
                "artifact_kind": None,
                "failure_reason_code": None,
            }
            raw_dict["entry_sha256"] = canonical_sha256(raw_dict)
            after_entry = FinalizationEntry.from_dict(raw_dict)

            def update_pending(ids: list[str]) -> list[str]:
                if entry_id not in ids:
                    return ids + [entry_id]
                return ids

            def update_summary(s: dict[str, Any]) -> None:
                s["total_count"] += 1
                s["pending_count"] += 1

            return self._apply_transition_locked(
                entry_id=entry_id,
                before_entry=None,
                after_entry=after_entry,
                pending_ids_updater=update_pending,
                summary_updater=update_summary,
            )

    def mark_recomputed(self, entry_id: str, binding: ArtifactBinding) -> FinalizationEntry:
        if binding.source_size is None or binding.source_size < 0:
            raise FinalizationEvidenceError("INVALID_BINDING")
        if not binding.source_sha256 or len(binding.source_sha256) != 64:
            raise FinalizationEvidenceError("INVALID_BINDING")
        if binding.source_record_count is None or binding.source_record_count < 0:
            raise FinalizationEvidenceError("INVALID_BINDING")
        if not binding.manifest_relative_path:
            raise FinalizationEvidenceError("INVALID_BINDING")
        if not binding.manifest_file_sha256 or len(binding.manifest_file_sha256) != 64:
            raise FinalizationEvidenceError("INVALID_BINDING")

        with self._lock():
            self._reconcile_locked()
            entry_file = self.entries_dir / f"{entry_id}.json"
            if not entry_file.exists():
                raise FinalizationEvidenceError("ENTRY_NOT_FOUND")
            before_entry = FinalizationEntry.from_dict(json.loads(entry_file.read_text(encoding="utf-8")))

            now_utc = datetime.now(timezone.utc).isoformat()
            raw_dict = {
                "schema_version": 5,
                "entry_id": entry_id,
                **asdict(before_entry.identity),
                "state": FinalizationState.RECOMPUTED.value,
                "created_at_utc": before_entry.created_at_utc,
                "started_at_utc": before_entry.started_at_utc or now_utc,
                "completed_at_utc": now_utc,
                "source_size": binding.source_size,
                "source_sha256": binding.source_sha256,
                "source_record_count": binding.source_record_count,
                "manifest_relative_path": binding.manifest_relative_path,
                "manifest_file_sha256": binding.manifest_file_sha256,
                "receipt_relative_path": binding.receipt_relative_path,
                "receipt_file_sha256": binding.receipt_file_sha256,
                "receipt_state": binding.receipt_state,
                "artifact_kind": binding.artifact_kind,
                "failure_reason_code": None,
            }
            raw_dict["entry_sha256"] = canonical_sha256(raw_dict)
            after_entry = FinalizationEntry.from_dict(raw_dict)

            def update_pending(ids: list[str]) -> list[str]:
                return [pid for pid in ids if pid != entry_id]

            def update_summary(s: dict[str, Any]) -> None:
                if before_entry.state is FinalizationState.PENDING:
                    s["pending_count"] = max(0, s["pending_count"] - 1)
                s["recomputed_count"] += 1

            return self._apply_transition_locked(
                entry_id=entry_id,
                before_entry=before_entry,
                after_entry=after_entry,
                pending_ids_updater=update_pending,
                summary_updater=update_summary,
            )

    def mark_reused(self, entry_id: str, binding: ArtifactBinding) -> FinalizationEntry:
        if binding.source_size is None or binding.source_size < 0:
            raise FinalizationEvidenceError("INVALID_BINDING")
        if not binding.source_sha256 or len(binding.source_sha256) != 64:
            raise FinalizationEvidenceError("INVALID_BINDING")
        if binding.source_record_count is None or binding.source_record_count < 0:
            raise FinalizationEvidenceError("INVALID_BINDING")
        if not binding.manifest_relative_path:
            raise FinalizationEvidenceError("INVALID_BINDING")
        if not binding.manifest_file_sha256 or len(binding.manifest_file_sha256) != 64:
            raise FinalizationEvidenceError("INVALID_BINDING")
        if not binding.receipt_relative_path or not binding.receipt_file_sha256:
            raise FinalizationEvidenceError("INVALID_BINDING")
        if binding.receipt_state not in TERMINAL_RECEIPT_STATES:
            raise FinalizationEvidenceError("INVALID_BINDING")
        if binding.artifact_kind != "RAW_DATA":
            raise FinalizationEvidenceError("INVALID_BINDING")

        with self._lock():
            self._reconcile_locked()
            entry_file = self.entries_dir / f"{entry_id}.json"
            if not entry_file.exists():
                raise FinalizationEvidenceError("ENTRY_NOT_FOUND")
            before_entry = FinalizationEntry.from_dict(json.loads(entry_file.read_text(encoding="utf-8")))

            now_utc = datetime.now(timezone.utc).isoformat()
            raw_dict = {
                "schema_version": 5,
                "entry_id": entry_id,
                **asdict(before_entry.identity),
                "state": FinalizationState.REUSED.value,
                "created_at_utc": before_entry.created_at_utc,
                "started_at_utc": before_entry.started_at_utc or now_utc,
                "completed_at_utc": now_utc,
                "source_size": binding.source_size,
                "source_sha256": binding.source_sha256,
                "source_record_count": binding.source_record_count,
                "manifest_relative_path": binding.manifest_relative_path,
                "manifest_file_sha256": binding.manifest_file_sha256,
                "receipt_relative_path": binding.receipt_relative_path,
                "receipt_file_sha256": binding.receipt_file_sha256,
                "receipt_state": binding.receipt_state,
                "artifact_kind": binding.artifact_kind,
                "failure_reason_code": None,
            }
            raw_dict["entry_sha256"] = canonical_sha256(raw_dict)
            after_entry = FinalizationEntry.from_dict(raw_dict)

            def update_pending(ids: list[str]) -> list[str]:
                return [pid for pid in ids if pid != entry_id]

            def update_summary(s: dict[str, Any]) -> None:
                if before_entry.state is FinalizationState.PENDING:
                    s["pending_count"] = max(0, s["pending_count"] - 1)
                s["reused_count"] += 1

            return self._apply_transition_locked(
                entry_id=entry_id,
                before_entry=before_entry,
                after_entry=after_entry,
                pending_ids_updater=update_pending,
                summary_updater=update_summary,
            )

    def mark_failed(self, entry_id: str, reason_code: str) -> FinalizationEntry:
        with self._lock():
            self._reconcile_locked()
            entry_file = self.entries_dir / f"{entry_id}.json"
            if not entry_file.exists():
                raise FinalizationEvidenceError("ENTRY_NOT_FOUND")
            before_entry = FinalizationEntry.from_dict(json.loads(entry_file.read_text(encoding="utf-8")))

            now_utc = datetime.now(timezone.utc).isoformat()
            raw_dict = {
                "schema_version": 5,
                "entry_id": entry_id,
                **asdict(before_entry.identity),
                "state": FinalizationState.FAILED.value,
                "created_at_utc": before_entry.created_at_utc,
                "started_at_utc": before_entry.started_at_utc,
                "completed_at_utc": now_utc,
                "source_size": before_entry.source_size,
                "source_sha256": before_entry.source_sha256,
                "source_record_count": before_entry.source_record_count,
                "manifest_relative_path": before_entry.manifest_relative_path,
                "manifest_file_sha256": before_entry.manifest_file_sha256,
                "receipt_relative_path": before_entry.receipt_relative_path,
                "receipt_file_sha256": before_entry.receipt_file_sha256,
                "receipt_state": before_entry.receipt_state,
                "artifact_kind": before_entry.artifact_kind,
                "failure_reason_code": reason_code,
            }
            raw_dict["entry_sha256"] = canonical_sha256(raw_dict)
            after_entry = FinalizationEntry.from_dict(raw_dict)

            def update_pending(ids: list[str]) -> list[str]:
                return [pid for pid in ids if pid != entry_id]

            def update_summary(s: dict[str, Any]) -> None:
                if before_entry.state is FinalizationState.PENDING:
                    s["pending_count"] = max(0, s["pending_count"] - 1)
                s["failed_count"] += 1

            return self._apply_transition_locked(
                entry_id=entry_id,
                before_entry=before_entry,
                after_entry=after_entry,
                pending_ids_updater=update_pending,
                summary_updater=update_summary,
            )

    def mark_complete(self) -> FinalizationSummary:
        with self._lock():
            self._reconcile_locked()
            summary_dict = json.loads(self.summary_file.read_text(encoding="utf-8"))
            if summary_dict["pending_count"] == 0 and summary_dict["failed_count"] == 0:
                summary_dict["state"] = "COMPLETE"
                new_gen = summary_dict["generation"] + 1
                summary_dict["generation"] = new_gen
                pending_dict = json.loads(self.pending_file.read_text(encoding="utf-8"))
                pending_dict["generation"] = new_gen
                _atomic_json(self.pending_file, pending_dict)
                _atomic_json(self.summary_file, summary_dict)
            return FinalizationSummary.from_dict(summary_dict)

    def complete_if_terminal(self) -> FinalizationSummary:
        return self.mark_complete()

    def summary(self) -> FinalizationSummary:
        with self._lock():
            return self._reconcile_locked()

    def pending_entries(self) -> Sequence[FinalizationEntry]:
        with self._lock():
            self._reconcile_locked()
            pending_dict = json.loads(self.pending_file.read_text(encoding="utf-8"))
            pids = pending_dict.get("pending_ids", [])
            result: list[FinalizationEntry] = []
            for pid in pids:
                efile = self.entries_dir / f"{pid}.json"
                if efile.exists():
                    result.append(FinalizationEntry.from_dict(json.loads(efile.read_text(encoding="utf-8"))))
            return result

    def get_entry(self, entry_id: str) -> FinalizationEntry:
        with self._lock():
            efile = self.entries_dir / f"{entry_id}.json"
            if not efile.exists():
                raise FinalizationEvidenceError("ENTRY_NOT_FOUND")
            return FinalizationEntry.from_dict(json.loads(efile.read_text(encoding="utf-8")))


class IncrementalManifestFinalizer:
    """Orchestrates bounded incremental finalization of dirty tail only."""

    def __init__(
        self,
        store: FinalizationProgressStore,
        storage: RawMicrostructureStorage,
        receipt_root: Path,
    ) -> None:
        self.store = store
        self.storage = storage
        self.receipt_root = Path(receipt_root)

    def _find_receipt(self, entry: FinalizationEntry) -> tuple[Path | None, dict[str, Any] | None]:
        raw_rel = entry.identity.raw_relative_path
        clean_rel = raw_rel
        if clean_rel.startswith("raw/"):
            clean_rel = clean_rel[4:]
        elif clean_rel.startswith("/raw/"):
            clean_rel = clean_rel[5:]
        raw_name = Path(clean_rel).name

        candidates = [
            self.receipt_root / Path(clean_rel).parent / f"{raw_name}.archive-receipt.json",
            self.receipt_root / f"{raw_name}.archive-receipt.json",
            self.receipt_root / Path(raw_rel).parent / f"{raw_name}.archive-receipt.json",
        ]

        for cand in candidates:
            if cand.exists() and cand.is_file():
                try:
                    data = json.loads(cand.read_text(encoding="utf-8"))
                    return cand, data
                except Exception:
                    return cand, None

        matches = list(self.receipt_root.glob(f"**/{raw_name}.archive-receipt.json"))
        if matches:
            cand = matches[0]
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                return cand, data
            except Exception:
                return cand, None

        return None, None

    def _validate_trust_chain(
        self,
        entry: FinalizationEntry,
        receipt_file: Path,
        receipt: dict[str, Any],
    ) -> ArtifactBinding:
        # Check epoch & run_id
        receipt_epoch = receipt.get("collector_epoch")
        receipt_run = receipt.get("collector_run_id") or receipt.get("run_id")
        if receipt_epoch != entry.identity.collector_epoch or receipt_run != entry.identity.collector_run_id:
            raise FinalizationEvidenceError("CONTRADICTORY_RECEIPT")

        # Check cohort
        if receipt.get("cohort") != entry.identity.cohort:
            raise FinalizationEvidenceError("CONTRADICTORY_RECEIPT")

        # Check exchange, stream, market, feed if present in receipt
        if "exchange" in receipt and receipt["exchange"] != entry.identity.exchange:
            raise FinalizationEvidenceError("CONTRADICTORY_RECEIPT")
        if "stream" in receipt and receipt["stream"] != entry.identity.stream:
            raise FinalizationEvidenceError("CONTRADICTORY_RECEIPT")
        if "market" in receipt and receipt["market"] != entry.identity.market:
            raise FinalizationEvidenceError("CONTRADICTORY_RECEIPT")
        if "feed_identity" in receipt and receipt["feed_identity"] != entry.identity.feed_identity:
            raise FinalizationEvidenceError("CONTRADICTORY_RECEIPT")

        # Terminal state
        receipt_state = receipt.get("state")
        if receipt_state not in TERMINAL_RECEIPT_STATES:
            raise FinalizationEvidenceError("CONTRADICTORY_RECEIPT")

        # Artifact kind
        artifact_kind = receipt.get("artifact_kind", "RAW_DATA")
        if artifact_kind != "RAW_DATA":
            raise FinalizationEvidenceError("CONTRADICTORY_RECEIPT")

        # Source size & SHA
        source_size = receipt.get("source_size") if receipt.get("source_size") is not None else receipt.get("raw_size")
        source_sha256 = receipt.get("source_sha256") or receipt.get("raw_sha256")
        source_records = (
            receipt.get("source_record_count")
            if receipt.get("source_record_count") is not None
            else receipt.get("raw_record_count")
        )

        if source_size is None or not isinstance(source_size, int) or source_size < 0:
            raise FinalizationEvidenceError("CONTRADICTORY_RECEIPT")
        if not source_sha256 or not isinstance(source_sha256, str) or len(source_sha256) != 64:
            raise FinalizationEvidenceError("CONTRADICTORY_RECEIPT")
        if source_records is None or not isinstance(source_records, int) or source_records < 0:
            raise FinalizationEvidenceError("CONTRADICTORY_RECEIPT")

        # Manifest binding
        manifest_rel = receipt.get("manifest_relative_path")
        manifest_sha = receipt.get("manifest_file_sha256")
        if not manifest_rel or not manifest_sha or len(manifest_sha) != 64:
            raw_stem = Path(entry.identity.raw_relative_path).stem
            cand_manifest = self.storage.manifest_dir / f"manifest_{raw_stem}.json"
            if cand_manifest.exists():
                manifest_rel = str(cand_manifest.relative_to(self.storage.manifest_dir.parent))
                manifest_sha = file_sha256(cand_manifest)
            else:
                raise FinalizationEvidenceError("CONTRADICTORY_RECEIPT")

        # Stat check on RAW file if present (DO NOT OPEN OR READ!)
        raw_path = self.storage.resolve_raw(entry.identity.raw_relative_path)
        if raw_path.exists():
            st = raw_path.stat()
            if st.st_size != source_size:
                raise FinalizationEvidenceError("CONTRADICTORY_RECEIPT")

        try:
            receipt_rel = str(receipt_file.relative_to(self.receipt_root))
        except ValueError:
            receipt_rel = str(receipt_file)

        receipt_sha = file_sha256(receipt_file)

        return ArtifactBinding(
            source_size=source_size,
            source_sha256=source_sha256,
            source_record_count=source_records,
            manifest_relative_path=manifest_rel,
            manifest_file_sha256=manifest_sha,
            receipt_relative_path=receipt_rel,
            receipt_file_sha256=receipt_sha,
            receipt_state=receipt_state,
            artifact_kind=artifact_kind,
        )

    def finalize_pending(self) -> FinalizationSummary:
        for entry in self.store.pending_entries():
            try:
                receipt_file, receipt_dict = self._find_receipt(entry)
                if receipt_dict is not None and receipt_file is not None:
                    binding = self._validate_trust_chain(entry, receipt_file, receipt_dict)
                    self.store.mark_reused(entry.entry_id, binding)
                    continue

                raw_path = self.storage.resolve_raw(entry.identity.raw_relative_path)
                if not raw_path.exists():
                    raise FinalizationEvidenceError("RAW_NOT_FOUND")
                if raw_path.is_symlink():
                    raise FinalizationEvidenceError("SYMLINK_RAW_REJECTED")
                if not raw_path.is_file():
                    raise FinalizationEvidenceError("RAW_NOT_REGULAR_FILE")

                manifest = self.storage.generate_partition_manifest(raw_path, identity=entry.identity)
                manifest_file = self.storage.manifest_dir / f"manifest_{raw_path.stem}.json"
                if not manifest_file.exists():
                    raise FinalizationEvidenceError("MANIFEST_WRITE_FAILED")

                try:
                    manifest_rel = str(manifest_file.relative_to(self.storage.manifest_dir.parent))
                except ValueError:
                    manifest_rel = str(manifest_file)

                binding = ArtifactBinding(
                    source_size=manifest.bytes,
                    source_sha256=manifest.sha256,
                    source_record_count=manifest.record_count,
                    manifest_relative_path=manifest_rel,
                    manifest_file_sha256=file_sha256(manifest_file),
                    receipt_relative_path=None,
                    receipt_file_sha256=None,
                    receipt_state=None,
                    artifact_kind="RAW_DATA",
                )
                self.store.mark_recomputed(entry.entry_id, binding)
            except FinalizationEvidenceError as error:
                self.store.mark_failed(entry.entry_id, error.reason_code)
            except Exception:
                self.store.mark_failed(entry.entry_id, "FINALIZATION_FAILED")

        summary = self.store.summary()
        if summary.pending_count == 0 and summary.failed_count == 0:
            self.store.mark_complete()
        return self.store.summary()
