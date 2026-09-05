"""Governed Experiment Runner and Cryptographic Hash-Chain Ledger (P8 - P8.6).

Features:
- Mandatory preregistration gating: No experiment runs without a valid manifest.
- Append-only atomic trial reservation.
- Cryptographic hash-chain ledger (tamper-evident SHA-256 links).
- Family budget enforcement (hard stop at N <= 9 trials per hypothesis family).
- Dataset role gating (TRAIN, VALIDATION, HOLDOUT) preventing lookahead/holdout leakage.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


class DatasetRole(str, Enum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    HOLDOUT = "HOLDOUT"


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


class GovernedExperimentRunner:
    """Governs experimental execution, trial budgets, and ledger verification."""

    GENESIS_HASH = "0" * 64

    def __init__(self, ledger_file: Path | str) -> None:
        self.ledger_file = Path(ledger_file)
        self._entries: list[LedgerEntry] = []
        self._reserved_trials: set[str] = set()
        self._load_or_init_ledger()

    def _load_or_init_ledger(self) -> None:
        if self.ledger_file.exists():
            data = json.loads(self.ledger_file.read_text())
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
                self._reserved_trials.add(entry.trial_id)
            self.verify_ledger_chain()
        else:
            self.ledger_file.parent.mkdir(parents=True, exist_ok=True)
            self._save_ledger()

    def _save_ledger(self) -> None:
        serialized = [e.to_dict() for e in self._entries]
        self.ledger_file.write_text(json.dumps(serialized, indent=2))

    def verify_ledger_chain(self) -> bool:
        """Cryptographically verifies that no entry in the ledger has been tampered with."""
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
        return sum(1 for e in self._entries if e.family_id == family_id)

    def reserve_trial(self, manifest: PreregistrationManifest) -> str:
        """Reserves a trial slot, enforcing family budget and preregistration existence."""
        if not manifest.trial_id or not manifest.family_id:
            raise PreregistrationMissingError("trial_id and family_id must be provided")

        family_count = self.count_family_trials(manifest.family_id)
        if family_count >= manifest.max_trials_in_family:
            raise TrialBudgetExceededError(
                f"Family {manifest.family_id} trial budget ({manifest.max_trials_in_family}) "
                f"exhausted (current count: {family_count})"
            )

        if manifest.trial_id in self._reserved_trials:
            raise ExperimentGatingError(f"trial_id {manifest.trial_id} is already registered/reserved")

        self._reserved_trials.add(manifest.trial_id)
        return manifest.trial_id

    def record_trial(
        self,
        manifest: PreregistrationManifest,
        results: dict[str, Any],
    ) -> LedgerEntry:
        """Appends a completed trial to the immutable ledger."""
        family_count = self.count_family_trials(manifest.family_id)
        if family_count >= manifest.max_trials_in_family:
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
        self._reserved_trials.add(manifest.trial_id)
        self._save_ledger()
        return entry

    def access_dataset(
        self,
        dataset_name: str,
        role: DatasetRole,
        is_final_verification: bool = False,
    ) -> str:
        """Guards dataset access to prevent holdout contamination."""
        if role == DatasetRole.HOLDOUT and not is_final_verification:
            raise HoldoutContaminationError(
                f"Forbidden access to HOLDOUT dataset '{dataset_name}' during exploratory research. "
                "Holdout partition is strictly isolated until final verification."
            )
        return f"ACCESS_GRANTED:{dataset_name}:{role.value}"
