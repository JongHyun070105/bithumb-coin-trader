"""Governed Experiment Runner and Cryptographic Hash-Chain Ledger (P8 - P8.6).

FORENSIC HARDENING (Phase 2.5):
- BUG-1 FIXED: reserve_trial() now writes to a durable .reservations file.
  Prior to this fix, reservations were memory-only and lost on process crash.
- BUG-4 FIXED: record_trial() now requires a prior durable reservation.
  Prior to this fix, record_trial() bypassed reservation entirely.
- BUG-9 FIXED: count_family_trials() now counts ALL states (RESERVED,
  RUNNING, FAILED, ABORTED, COMPLETED), not only completed ledger entries.
- BUG-10 FIXED: _save_ledger() now uses temp-write -> fsync -> atomic rename
  instead of direct write_text() which risked file corruption on crash.
- BUG-11 FIXED: access_dataset() now requires explicit ResearchCycleState
  lifecycle progression instead of a boolean bypass flag.

Features:
- Mandatory preregistration gating: No experiment runs without a valid manifest.
- Durable atomic trial reservation (persisted to .reservations file).
- Cryptographic hash-chain ledger (tamper-evident SHA-256 links).
- Family budget enforcement counting ALL attempt states (not only COMPLETED).
- Dataset role gating (TRAIN, VALIDATION, HOLDOUT) via lifecycle state machine.
"""

from __future__ import annotations

import fcntl
import contextlib
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence


class DatasetRole(str, Enum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    HOLDOUT = "HOLDOUT"


class TrialStatus(str, Enum):
    """Lifecycle states for a trial attempt.

    Transitions:
        RESERVED -> RUNNING -> COMPLETED
                            -> FAILED
                            -> ABORTED
    A trial in any state counts against the family budget.
    """
    RESERVED = "RESERVED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class ResearchCycleState(str, Enum):
    """Lifecycle states for the research cycle controlling holdout access.

    Only HOLDOUT_AUTHORIZED state allows access to HOLDOUT partition.
    Transition to HOLDOUT_AUTHORIZED requires explicit advance_research_state().
    """
    PREREGISTERED = "PREREGISTERED"
    DISCOVERY_ACTIVE = "DISCOVERY_ACTIVE"
    VALIDATION_ACTIVE = "VALIDATION_ACTIVE"
    MODEL_FROZEN = "MODEL_FROZEN"
    HOLDOUT_AUTHORIZED = "HOLDOUT_AUTHORIZED"
    HOLDOUT_CONSUMED = "HOLDOUT_CONSUMED"
    CLOSED = "CLOSED"


_STATE_ORDER = [
    ResearchCycleState.PREREGISTERED,
    ResearchCycleState.DISCOVERY_ACTIVE,
    ResearchCycleState.VALIDATION_ACTIVE,
    ResearchCycleState.MODEL_FROZEN,
    ResearchCycleState.HOLDOUT_AUTHORIZED,
    ResearchCycleState.HOLDOUT_CONSUMED,
    ResearchCycleState.CLOSED,
]


class ExperimentGatingError(Exception):
    """Base exception for experiment governance violations."""


class TrialBudgetExceededError(ExperimentGatingError):
    """Raised when family trial budget is exhausted."""


class HoldoutContaminationError(ExperimentGatingError):
    """Raised when holdout partition is accessed prematurely."""


class PreregistrationMissingError(ExperimentGatingError):
    """Raised when an experiment is attempted without preregistration."""


class LedgerTamperError(ExperimentGatingError):
    """Raised when hash-chain ledger verification fails."""


class ReservationRequiredError(ExperimentGatingError):
    """Raised when record_trial() is called without a prior reservation.

    BUG-4 FIX: Prior to Phase 2.5, record_trial() did not check for a
    prior durable reservation, allowing governance bypass.
    """


class InvalidResearchCycleStateError(ExperimentGatingError):
    """Raised when a lifecycle state transition is invalid."""

class TrialAlreadyTerminalError(ExperimentGatingError):
    pass

class ManifestMismatchError(ExperimentGatingError):
    pass

class InvalidStatusTransitionError(ExperimentGatingError):
    pass

class HoldoutAlreadyConsumedError(ExperimentGatingError):
    pass


@dataclass(frozen=True, slots=True)
class PreregistrationManifest:
    trial_id: str
    family_id: str
    hypothesis: str
    features: tuple[str, ...]
    target_horizon_ms: int
    sample_budget: int
    max_trials_in_family: int = 9

    def compute_sha256(self) -> str:
        d = {
            "trial_id": self.trial_id,
            "family_id": self.family_id,
            "hypothesis": self.hypothesis,
            "features": list(self.features),
            "target_horizon_ms": self.target_horizon_ms,
            "sample_budget": self.sample_budget,
            "max_trials_in_family": self.max_trials_in_family,
        }
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    entry_index: int
    trial_id: str
    family_id: str
    timestamp_utc: str
    manifest_hash: str
    results: dict[str, Any]
    previous_hash: str
    entry_hash: str

    @classmethod
    def create(
        cls,
        entry_index: int,
        manifest: PreregistrationManifest,
        results: dict[str, Any],
        previous_hash: str,
    ) -> LedgerEntry:
        now_utc = datetime.now(timezone.utc).isoformat()
        manifest_hash = manifest.compute_sha256()
        payload = {
            "entry_index": entry_index,
            "trial_id": manifest.trial_id,
            "family_id": manifest.family_id,
            "timestamp_utc": now_utc,
            "manifest_hash": manifest_hash,
            "results": results,
            "previous_hash": previous_hash,
        }
        entry_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return cls(
            entry_index=entry_index,
            trial_id=manifest.trial_id,
            family_id=manifest.family_id,
            timestamp_utc=now_utc,
            manifest_hash=manifest_hash,
            results=results,
            previous_hash=previous_hash,
            entry_hash=entry_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_index": self.entry_index,
            "trial_id": self.trial_id,
            "family_id": self.family_id,
            "timestamp_utc": self.timestamp_utc,
            "manifest_hash": self.manifest_hash,
            "results": self.results,
            "previous_hash": self.previous_hash,
            "entry_hash": self.entry_hash,
        }


@dataclass
class ReservationRecord:
    """Durable record of a trial reservation (BUG-1 fix).

    Persisted to disk so that process crashes do not lose reservation state.
    """
    trial_id: str
    family_id: str
    status: TrialStatus
    reserved_at_utc: str
    manifest_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "family_id": self.family_id,
            "status": self.status.value,
            "reserved_at_utc": self.reserved_at_utc,
            "manifest_hash": self.manifest_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'ReservationRecord':
        return cls(
            trial_id=d["trial_id"],
            family_id=d["family_id"],
            status=TrialStatus(d["status"]),
            reserved_at_utc=d["reserved_at_utc"],
            manifest_hash=d.get("manifest_hash", ""),
        )



@contextlib.contextmanager
def _exclusive_lock(lock_file: Path):
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fd = lock_file.open('w')
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def _atomic_write_json(path: Path, data: Any) -> None:
    """BUG-10 FIX: Writes JSON atomically using temp-write -> fsync -> rename.

    Prior to this fix, write_text() was used directly. A crash mid-write
    could leave the file in a partially-written state, corrupting the ledger.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class GovernedExperimentRunner:
    """Governs experimental execution, trial budgets, and ledger verification.

    LIMITATION (documented): The hash-chain ledger is tamper-EVIDENT,
    not immutable. SHA-256 chain in a mutable local file cannot prevent
    deletion of the entire file. Immutability requires external write-once
    storage (e.g., append-only S3 bucket with versioning).
    """

    GENESIS_HASH = "0" * 64

    def __init__(self, ledger_file: Path | str) -> None:
        self.ledger_file = Path(ledger_file)
        self._reservations_file = (
            self.ledger_file.parent / (self.ledger_file.stem + ".reservations.json")
        )
        self._cycle_state_file = (
            self.ledger_file.parent / (self.ledger_file.stem + ".cycle_state.json")
        )
        self._lock_file = (
            self.ledger_file.parent / (self.ledger_file.stem + ".lock")
        )
        self._entries: list[LedgerEntry] = []
        self._reservations: dict[str, ReservationRecord] = {}
        self._cycle_state: ResearchCycleState = ResearchCycleState.PREREGISTERED
        
        self._load_or_init_ledger()
        self._load_or_init_reservations()
        self._load_cycle_state()
        self._recover_intents()


    def _load_cycle_state(self) -> None:
        if self._cycle_state_file.exists():
            data = json.loads(self._cycle_state_file.read_text())
            self._cycle_state = ResearchCycleState(data["state"])

    def _save_cycle_state(self, justification: str = "") -> None:
        data = {
            "state": self._cycle_state.value,
            "transition_timestamp": datetime.now(timezone.utc).isoformat(),
            "justification": justification
        }
        _atomic_write_json(self._cycle_state_file, data)

    def _recover_intents(self) -> None:
        for intent_file in self.ledger_file.parent.glob("*.intent"):
            try:
                data = json.loads(intent_file.read_text())
                tid = data["trial_id"]
                if tid in self._reservations:
                    rec = self._reservations[tid]
                    if rec.status not in {TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.ABORTED}:
                        self.update_trial_status(tid, TrialStatus.ABORTED)
                intent_file.unlink()
            except Exception:
                pass

    def _load_or_init_ledger(self) -> None:
        if self.ledger_file.exists():
            data = json.loads(self.ledger_file.read_text())
            self._entries.clear()
            for item in data:
                entry = LedgerEntry(
                    entry_index=item["entry_index"],
                    trial_id=item["trial_id"],
                    family_id=item["family_id"],
                    timestamp_utc=item["timestamp_utc"],
                    manifest_hash=item["manifest_hash"],
                    results=item["results"],
                    previous_hash=item["previous_hash"],
                    entry_hash=item["entry_hash"],
                )
                self._entries.append(entry)
            self.verify_ledger_chain()
        else:
            self.ledger_file.parent.mkdir(parents=True, exist_ok=True)
            self._save_ledger()

    def _load_or_init_reservations(self) -> None:
        """BUG-1 FIX: Load durable reservations from disk on startup."""
        if self._reservations_file.exists():
            data = json.loads(self._reservations_file.read_text())
            for item in data:
                rec = ReservationRecord.from_dict(item)
                self._reservations[rec.trial_id] = rec
        else:
            _atomic_write_json(self._reservations_file, [])

    def _save_ledger(self) -> None:
        """BUG-10 FIX: Atomic write via temp -> fsync -> rename."""
        _atomic_write_json(self.ledger_file, [e.to_dict() for e in self._entries])

    def _save_reservations(self) -> None:
        """BUG-1 FIX: Persist reservation state atomically."""
        _atomic_write_json(
            self._reservations_file,
            [r.to_dict() for r in self._reservations.values()],
        )

    def verify_ledger_chain(self) -> bool:
        """Cryptographically verifies that no entry in the ledger has been tampered with.

        LIMITATION: Tamper-evident, not immutable. Verifies SHA-256 chain
        integrity only. Cannot detect deletion of the entire file.
        """
        prev_hash = self.GENESIS_HASH
        for idx, e in enumerate(self._entries):
            if e.entry_index != idx:
                raise LedgerTamperError(f"Entry index mismatch at index {idx}: got {e.entry_index}")
            if e.previous_hash != prev_hash:
                raise LedgerTamperError(
                    f"Hash link broken at index {idx}: expected prev {prev_hash}, got {e.previous_hash}"
                )
            payload = {
                "entry_index": e.entry_index,
                "trial_id": e.trial_id,
                "family_id": e.family_id,
                "timestamp_utc": e.timestamp_utc,
                "manifest_hash": e.manifest_hash,
                "results": e.results,
                "previous_hash": e.previous_hash,
            }
            computed_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
            if computed_hash != e.entry_hash:
                raise LedgerTamperError(
                    f"Content tampered at index {idx}: expected hash {computed_hash}, got {e.entry_hash}"
                )
            prev_hash = e.entry_hash
        return True

    def count_family_trials(self, family_id: str) -> int:
        """BUG-9 FIX: Counts ALL trial attempts for a family, regardless of state.

        Prior to this fix, only COMPLETED ledger entries were counted.
        Budget bypass was possible: crash 9 trials before record_trial(),
        restart, and reserve 9 more since the ledger was empty.
        """
        return sum(1 for r in self._reservations.values() if r.family_id == family_id)

    def reserve_trial(self, manifest: PreregistrationManifest) -> str:
        with _exclusive_lock(self._lock_file):
            # Refresh from disk
            self._load_or_init_reservations()

            if not manifest.trial_id or not manifest.family_id:
                raise PreregistrationMissingError("trial_id and family_id must be provided")

            family_count = self.count_family_trials(manifest.family_id)
            if family_count >= manifest.max_trials_in_family:
                raise TrialBudgetExceededError(
                    f"Family {manifest.family_id} trial budget ({manifest.max_trials_in_family}) "
                    f"exhausted (current count: {family_count})"
                )

            if manifest.trial_id in self._reservations:
                raise ExperimentGatingError(f"trial_id {manifest.trial_id} is already registered/reserved")

            manifest_hash = manifest.compute_sha256()

            rec = ReservationRecord(
                trial_id=manifest.trial_id,
                family_id=manifest.family_id,
                status=TrialStatus.RESERVED,
                reserved_at_utc=datetime.now(timezone.utc).isoformat(),
                manifest_hash=manifest_hash,
            )
            self._reservations[manifest.trial_id] = rec
            self._save_reservations()
            return manifest.trial_id

    def update_trial_status(self, trial_id: str, status: TrialStatus) -> None:
        with _exclusive_lock(self._lock_file):
            self._load_or_init_reservations()
            if trial_id not in self._reservations:
                raise ReservationRequiredError(
                    f"Cannot update status for unreserved trial_id '{trial_id}'"
                )
            
            rec = self._reservations[trial_id]

            valid_transitions = {
                TrialStatus.RESERVED: {TrialStatus.RUNNING, TrialStatus.ABORTED},
                TrialStatus.RUNNING: {TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.ABORTED},
                TrialStatus.COMPLETED: set(),
                TrialStatus.FAILED: set(),
                TrialStatus.ABORTED: set()
            }
            if status not in valid_transitions[rec.status]:
                raise InvalidStatusTransitionError(f"Invalid transition from {rec.status} to {status}")

            updated = ReservationRecord(
                trial_id=rec.trial_id,
                family_id=rec.family_id,
                status=status,
                reserved_at_utc=rec.reserved_at_utc,
                manifest_hash=rec.manifest_hash,
            )
            self._reservations[trial_id] = updated
            self._save_reservations()

    def record_trial(
        self,
        manifest: PreregistrationManifest,
        results: dict[str, Any],
    ) -> LedgerEntry:
        with _exclusive_lock(self._lock_file):
            self._load_or_init_reservations()
            self._load_or_init_ledger()

            if manifest.trial_id not in self._reservations:
                raise ReservationRequiredError(
                    f"record_trial() called for trial_id '{manifest.trial_id}' without "
                    f"a prior reserve_trial() call. Governance bypass is not permitted."
                )

            rec = self._reservations[manifest.trial_id]
            
            if rec.status in {TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.ABORTED}:
                raise TrialAlreadyTerminalError("Trial is already in a terminal state.")

            if manifest.compute_sha256() != rec.manifest_hash:
                raise ManifestMismatchError("Manifest does not match reserved manifest.")

            other_family_count = sum(
                1 for r in self._reservations.values()
                if r.family_id == manifest.family_id and r.trial_id != manifest.trial_id
            )
            if other_family_count >= manifest.max_trials_in_family:
                raise TrialBudgetExceededError(
                    f"Family {manifest.family_id} trial budget exhausted"
                )

            # Intent file for crash recovery
            intent_path = self.ledger_file.parent / f"{manifest.trial_id}.intent"
            _atomic_write_json(intent_path, {
                "trial_id": manifest.trial_id,
                "manifest_hash": manifest.compute_sha256(),
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

            prev_hash = self._entries[-1].entry_hash if self._entries else self.GENESIS_HASH
            entry_idx = len(self._entries)

            entry = LedgerEntry.create(
                entry_index=entry_idx,
                manifest=manifest,
                results=results,
                previous_hash=prev_hash,
            )
            self._entries.append(entry)
            
            # Since update_trial_status also locks, we inline it or bypass lock
            rec = self._reservations[manifest.trial_id]
            updated = ReservationRecord(
                trial_id=rec.trial_id,
                family_id=rec.family_id,
                status=TrialStatus.COMPLETED,
                reserved_at_utc=rec.reserved_at_utc,
                manifest_hash=rec.manifest_hash,
            )
            self._reservations[manifest.trial_id] = updated
            self._save_reservations()

            self._save_ledger()

            if intent_path.exists():
                intent_path.unlink()

            return entry

    def advance_research_state(
        self,
        target_state: ResearchCycleState,
        justification: str,
    ) -> ResearchCycleState:
        """BUG-11 FIX: Advances the research lifecycle state machine (strictly forward).

        Replaces the is_final_verification=True boolean bypass in access_dataset().
        Requires explicit justification for each transition.

        LIMITATION: State is in-memory only. For multi-process durability,
        persist _cycle_state to a separate state file.
        """
        current_idx = _STATE_ORDER.index(self._cycle_state)
        target_idx = _STATE_ORDER.index(target_state)

        if target_idx <= current_idx:
            raise InvalidResearchCycleStateError(
                f"Cannot regress from {self._cycle_state.value} to {target_state.value}. "
                f"Research cycle states are strictly forward-progressing."
            )
        if target_idx != current_idx + 1:
            raise InvalidResearchCycleStateError(
                f"Cannot skip states: {self._cycle_state.value} -> {target_state.value}. "
                f"Must advance one state at a time."
            )
        if not justification or not justification.strip():
            raise InvalidResearchCycleStateError(
                "Justification is required for state advancement."
            )

        self._cycle_state = target_state
        self._save_cycle_state(justification)
        return self._cycle_state

    @property
    def research_cycle_state(self) -> ResearchCycleState:
        return self._cycle_state

    def access_dataset(
        self,
        dataset_name: str,
        role: DatasetRole,
        is_final_verification: bool = False,  # DEPRECATED: ignored, kept for compat
    ) -> str:
        """BUG-11 FIX: Guards dataset access via lifecycle state machine.

        HOLDOUT access is only permitted when research_cycle_state is
        HOLDOUT_AUTHORIZED. Use advance_research_state() to progress.

        DEPRECATION: is_final_verification boolean is IGNORED.
        It was a bypass mechanism. Use advance_research_state() instead.
        """
        if role == DatasetRole.HOLDOUT:
            if self._cycle_state == ResearchCycleState.HOLDOUT_CONSUMED:
                raise HoldoutAlreadyConsumedError("Holdout dataset already consumed.")
            if self._cycle_state != ResearchCycleState.HOLDOUT_AUTHORIZED:
                raise HoldoutContaminationError(
                    f"Forbidden access to HOLDOUT dataset '{dataset_name}'. "
                    f"Current research cycle state: {self._cycle_state.value}. "
                    f"Required state: {ResearchCycleState.HOLDOUT_AUTHORIZED.value}. "
                    f"Use advance_research_state(HOLDOUT_AUTHORIZED, justification=...) "
                    f"to authorize holdout access."
                )
            self._cycle_state = ResearchCycleState.HOLDOUT_CONSUMED
            self._save_cycle_state("Consumed holdout dataset")
            
        return f"ACCESS_GRANTED:{dataset_name}:{role.value}" 
