import sys

with open("src/bithumb_coin_trader/experiment_runner.py", "r") as f:
    content = f.read()

import re

# 1. Add missing exceptions
exceptions = """
class TrialAlreadyTerminalError(ExperimentGatingError):
    pass

class ManifestMismatchError(ExperimentGatingError):
    pass

class InvalidStatusTransitionError(ExperimentGatingError):
    pass

class HoldoutAlreadyConsumedError(ExperimentGatingError):
    pass
"""
content = content.replace("class InvalidResearchCycleStateError(ExperimentGatingError):\n    \"\"\"Raised when a lifecycle state transition is invalid.\"\"\"\n", 
                          "class InvalidResearchCycleStateError(ExperimentGatingError):\n    \"\"\"Raised when a lifecycle state transition is invalid.\"\"\"\n" + exceptions)


# 2. ReservationRecord needs manifest_hash
res_record = """
@dataclass
class ReservationRecord:
    \"\"\"Durable record of a trial reservation (BUG-1 fix).

    Persisted to disk so that process crashes do not lose reservation state.
    \"\"\"
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
"""
content = re.sub(r'@dataclass\nclass ReservationRecord:.*?(?=\n\n\ndef _atomic_write_json)', res_record.strip(), content, flags=re.DOTALL)


# 3. Add file lock and imports
imports = """
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
"""
content = re.sub(r'import os\nimport tempfile.*?from typing import Any, Sequence', imports.strip(), content, flags=re.DOTALL)

lock_code = """
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
"""
content = content.replace("def _atomic_write_json(path: Path, data: Any) -> None:", lock_code + "\n\ndef _atomic_write_json(path: Path, data: Any) -> None:")


# 4. Init changes
init_method_orig = """    def __init__(self, ledger_file: Path | str) -> None:
        self.ledger_file = Path(ledger_file)
        self._reservations_file = (
            self.ledger_file.parent / (self.ledger_file.stem + ".reservations.json")
        )
        self._entries: list[LedgerEntry] = []
        self._reservations: dict[str, ReservationRecord] = {}
        self._cycle_state: ResearchCycleState = ResearchCycleState.PREREGISTERED
        self._load_or_init_ledger()
        self._load_or_init_reservations()"""

init_method_new = """    def __init__(self, ledger_file: Path | str) -> None:
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
        self._recover_intents()"""

content = content.replace(init_method_orig, init_method_new)

add_methods = """
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
"""
content = content.replace("    def _load_or_init_ledger(self) -> None:", add_methods + "\n    def _load_or_init_ledger(self) -> None:")

# 5. Reserve trial changes
reserve_orig = """    def reserve_trial(self, manifest: PreregistrationManifest) -> str:
        \"\"\"BUG-1 FIX: Reserves a trial slot and persists the reservation durably.

        Prior to this fix: reservation was in-memory only (_reserved_trials set).
        A process crash between reserve_trial() and record_trial() would lose
        the reservation, allowing budget bypass on restart.

        Now: reservation is written to .reservations.json before returning.
        On restart, _load_or_init_reservations() restores all reservations.
        \"\"\"
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

        rec = ReservationRecord(
            trial_id=manifest.trial_id,
            family_id=manifest.family_id,
            status=TrialStatus.RESERVED,
            reserved_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        self._reservations[manifest.trial_id] = rec
        self._save_reservations()  # BUG-1 FIX: persist before returning
        return manifest.trial_id"""

reserve_new = """    def reserve_trial(self, manifest: PreregistrationManifest) -> str:
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
            return manifest.trial_id"""

content = content.replace(reserve_orig, reserve_new)

# 6. Update status changes
update_status_orig = """    def update_trial_status(self, trial_id: str, status: TrialStatus) -> None:
        \"\"\"Updates the durable status of a reserved trial (RESERVED->RUNNING->COMPLETED/FAILED/ABORTED).\"\"\"
        if trial_id not in self._reservations:
            raise ReservationRequiredError(
                f"Cannot update status for unreserved trial_id '{trial_id}'"
            )
        rec = self._reservations[trial_id]
        updated = ReservationRecord(
            trial_id=rec.trial_id,
            family_id=rec.family_id,
            status=status,
            reserved_at_utc=rec.reserved_at_utc,
        )
        self._reservations[trial_id] = updated
        self._save_reservations()"""

update_status_new = """    def update_trial_status(self, trial_id: str, status: TrialStatus) -> None:
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
            self._save_reservations()"""

content = content.replace(update_status_orig, update_status_new)


# 7. Record trial changes
record_orig = """    def record_trial(
        self,
        manifest: PreregistrationManifest,
        results: dict[str, Any],
    ) -> LedgerEntry:
        \"\"\"BUG-4 FIX: Appends a completed trial. Requires prior durable reservation.

        Prior to this fix: record_trial() did not verify a prior reservation,
        allowing anyone to record a trial result without governance gating.

        Now: raises ReservationRequiredError if reserve_trial() was not called first.
        \"\"\"
        # BUG-4 FIX: require prior reservation
        if manifest.trial_id not in self._reservations:
            raise ReservationRequiredError(
                f"record_trial() called for trial_id '{manifest.trial_id}' without "
                f"a prior reserve_trial() call. Governance bypass is not permitted."
            )

        # Budget check: count other trials in family (this trial is already in reservations)
        other_family_count = sum(
            1 for r in self._reservations.values()
            if r.family_id == manifest.family_id and r.trial_id != manifest.trial_id
        )
        if other_family_count >= manifest.max_trials_in_family:
            raise TrialBudgetExceededError(
                f"Family {manifest.family_id} trial budget exhausted"
            )

        prev_hash = self._entries[-1].entry_hash if self._entries else self.GENESIS_HASH
        entry_idx = len(self._entries)

        entry = LedgerEntry.create(
            entry_index=entry_idx,
            manifest=manifest,
            results=results,
            previous_hash=prev_hash,
        )
        self._entries.append(entry)
        self.update_trial_status(manifest.trial_id, TrialStatus.COMPLETED)
        self._save_ledger()  # BUG-10 FIX: atomic write
        return entry"""

record_new = """    def record_trial(
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

            return entry"""

content = content.replace(record_orig, record_new)

# 8. Advance research state
adv_orig = """        self._cycle_state = target_state
        return self._cycle_state"""
adv_new = """        self._cycle_state = target_state
        self._save_cycle_state(justification)
        return self._cycle_state"""
content = content.replace(adv_orig, adv_new)

# 9. Holdout access
hold_orig = """        if role == DatasetRole.HOLDOUT:
            if self._cycle_state != ResearchCycleState.HOLDOUT_AUTHORIZED:
                raise HoldoutContaminationError(
                    f"Forbidden access to HOLDOUT dataset '{dataset_name}'. "
                    f"Current research cycle state: {self._cycle_state.value}. "
                    f"Required state: {ResearchCycleState.HOLDOUT_AUTHORIZED.value}. "
                    f"Use advance_research_state(HOLDOUT_AUTHORIZED, justification=...) "
                    f"to authorize holdout access."
                )
        return f"ACCESS_GRANTED:{dataset_name}:{role.value}" """

hold_new = """        if role == DatasetRole.HOLDOUT:
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

        return f"ACCESS_GRANTED:{dataset_name}:{role.value}" """

content = content.replace(hold_orig, hold_new)

with open("src/bithumb_coin_trader/experiment_runner.py", "w") as f:
    f.write(content)
