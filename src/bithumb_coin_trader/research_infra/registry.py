"""Dataset Registry for Microstructure Research.

Manages dataset registration, role enforcement, and provenance tracking.
Each dataset carries explicit scientific role and capability flags to prevent
accidental misuse (e.g., running final-holdout claims against development data).

Dataset roles:
    DEVELOPMENT_EXPLORATORY  - Old72H, V2: for development and hypothesis generation only
    PROSPECTIVE_RESEARCH     - Future prospectively collected data (V4-after-PASS)
    FROZEN_HOLDOUT           - Untouched future holdout for final alpha claims
    INFRA_VALIDATION_ONLY    - Fresh45/45m: infrastructure validation reference
    QUARANTINED              - V4 while running or before infrastructure PASS
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


class DatasetRole(str, Enum):
    DEVELOPMENT_EXPLORATORY = "DEVELOPMENT_EXPLORATORY"
    PROSPECTIVE_RESEARCH = "PROSPECTIVE_RESEARCH"
    FROZEN_HOLDOUT = "FROZEN_HOLDOUT"
    INFRA_VALIDATION_ONLY = "INFRA_VALIDATION_ONLY"
    QUARANTINED = "QUARANTINED"


class DatasetValidationError(ValueError):
    """Raised when dataset registration or access violates scientific constraints."""


@dataclass(frozen=True)
class DatasetRegistration:
    """Immutable registration record for a research dataset."""

    dataset_id: str
    dataset_role: DatasetRole
    description: str
    source_type: str  # "jsonl_raw", "parquet", "mixed"
    source_roots: tuple[str, ...]  # Local or S3 paths
    time_range_start: str | None  # ISO 8601
    time_range_end: str | None
    exchange_universe: tuple[str, ...]  # e.g., ("bithumb", "binance", "upbit")
    feed_universe: tuple[str, ...]  # e.g., ("trade", "orderbook", "ticker")
    raw_schema_version: str
    manifest_schema_version: str
    known_integrity_status: str  # "PASS", "FAIL", "UNKNOWN", "PARTIAL"
    known_data_quality_issues: tuple[str, ...]
    allowed_for_exploration: bool
    allowed_for_candidate_selection: bool
    allowed_for_final_holdout: bool
    immutable_source: bool
    notes: str = ""
    source_run_id: str | None = None
    collector_epoch: str | None = None
    registered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["dataset_role"] = self.dataset_role.value
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> DatasetRegistration:
        return cls(
            dataset_id=d["dataset_id"],
            dataset_role=DatasetRole(d["dataset_role"]),
            description=d["description"],
            source_type=d["source_type"],
            source_roots=tuple(d["source_roots"]),
            time_range_start=d.get("time_range_start"),
            time_range_end=d.get("time_range_end"),
            exchange_universe=tuple(d.get("exchange_universe", [])),
            feed_universe=tuple(d.get("feed_universe", [])),
            raw_schema_version=d.get("raw_schema_version", "unknown"),
            manifest_schema_version=d.get("manifest_schema_version", "unknown"),
            known_integrity_status=d.get("known_integrity_status", "UNKNOWN"),
            known_data_quality_issues=tuple(d.get("known_data_quality_issues", [])),
            allowed_for_exploration=d.get("allowed_for_exploration", False),
            allowed_for_candidate_selection=d.get("allowed_for_candidate_selection", False),
            allowed_for_final_holdout=d.get("allowed_for_final_holdout", False),
            immutable_source=d.get("immutable_source", True),
            notes=d.get("notes", ""),
            source_run_id=d.get("source_run_id"),
            collector_epoch=d.get("collector_epoch"),
            registered_at=d.get("registered_at", ""),
        )


class DatasetRegistry:
    """Manages dataset registrations with role-based access control."""

    def __init__(self) -> None:
        self._datasets: dict[str, DatasetRegistration] = {}

    def register(self, dataset: DatasetRegistration) -> None:
        if dataset.dataset_id in self._datasets:
            raise DatasetValidationError(
                f"Dataset '{dataset.dataset_id}' already registered"
            )
        self._datasets[dataset.dataset_id] = dataset

    def get(self, dataset_id: str) -> DatasetRegistration:
        if dataset_id not in self._datasets:
            raise DatasetValidationError(
                f"Dataset '{dataset_id}' not found. Available: {list(self._datasets)}"
            )
        return self._datasets[dataset_id]

    def list_datasets(self) -> list[DatasetRegistration]:
        return list(self._datasets.values())

    def require_exploration_allowed(self, dataset_id: str) -> DatasetRegistration:
        ds = self.get(dataset_id)
        if not ds.allowed_for_exploration:
            raise DatasetValidationError(
                f"Dataset '{dataset_id}' (role={ds.dataset_role.value}) "
                f"is not allowed for exploration"
            )
        return ds

    def require_candidate_selection_allowed(self, dataset_id: str) -> DatasetRegistration:
        ds = self.get(dataset_id)
        if not ds.allowed_for_candidate_selection:
            raise DatasetValidationError(
                f"Dataset '{dataset_id}' (role={ds.dataset_role.value}) "
                f"is not allowed for candidate selection"
            )
        return ds

    def require_final_holdout_allowed(self, dataset_id: str) -> DatasetRegistration:
        ds = self.get(dataset_id)
        if not ds.allowed_for_final_holdout:
            raise DatasetValidationError(
                f"Dataset '{dataset_id}' (role={ds.dataset_role.value}) "
                f"is not allowed for final holdout"
            )
        return ds

    def update_role(self, dataset_id: str, new_role: DatasetRole) -> None:
        ds = self.get(dataset_id)
        updated = DatasetRegistration(
            dataset_id=ds.dataset_id,
            dataset_role=new_role,
            description=ds.description,
            source_type=ds.source_type,
            source_roots=ds.source_roots,
            time_range_start=ds.time_range_start,
            time_range_end=ds.time_range_end,
            exchange_universe=ds.exchange_universe,
            feed_universe=ds.feed_universe,
            raw_schema_version=ds.raw_schema_version,
            manifest_schema_version=ds.manifest_schema_version,
            known_integrity_status=ds.known_integrity_status,
            known_data_quality_issues=ds.known_data_quality_issues,
            allowed_for_exploration=ds.allowed_for_exploration,
            allowed_for_candidate_selection=ds.allowed_for_candidate_selection,
            allowed_for_final_holdout=ds.allowed_for_final_holdout,
            immutable_source=ds.immutable_source,
            notes=ds.notes,
            source_run_id=ds.source_run_id,
            collector_epoch=ds.collector_epoch,
            registered_at=ds.registered_at,
        )
        self._datasets[dataset_id] = updated

    def save(self, path: Path) -> None:
        data = [ds.to_dict() for ds in self._datasets.values()]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True))

    @classmethod
    def load(cls, path: Path) -> DatasetRegistry:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        reg = cls()
        for d in data:
            reg.register(DatasetRegistration.from_dict(d))
        return reg


def register_default_datasets(registry: DatasetRegistry) -> None:
    """Register the known historical datasets with correct scientific roles."""

    registry.register(DatasetRegistration(
        dataset_id="old72h",
        dataset_role=DatasetRole.DEVELOPMENT_EXPLORATORY,
        description="Historical ~72h attempt. FAIL CASE B. ~49.87M messages, 73 RAW cohorts. "
                    "Only ~3 date cohorts / 228 receipts due to hour-only cohort-key collision.",
        source_type="jsonl_raw",
        source_roots=("ec2://old72h",),  # Primarily on EC2, not locally available
        time_range_start=None,  # Exact range unknown without full scan
        time_range_end=None,
        exchange_universe=("bithumb", "binance", "upbit"),
        feed_universe=("trade", "orderbook", "ticker"),
        raw_schema_version="v9.1.0-quarantine-hardened",
        manifest_schema_version="4",
        known_integrity_status="FAIL",
        known_data_quality_issues=(
            "FAIL_CASE_B: Historical attempt, not prospective validation",
            "Only ~3 date cohorts verified out of 73 expected",
            "Hour-only cohort-key collision in early lifecycle",
            "~66.19 GiB total, ~66.12 GiB RAW on EC2",
        ),
        allowed_for_exploration=True,
        allowed_for_candidate_selection=False,
        allowed_for_final_holdout=False,
        immutable_source=True,
        notes="DEVELOPMENT / REFERENCE / EXPLORATORY RESEARCH ONLY. "
              "MUST NOT be presented as successful prospective validation.",
    ))

    registry.register(DatasetRegistration(
        dataset_id="v2",
        dataset_role=DatasetRole.DEVELOPMENT_EXPLORATORY,
        description="V2 30H collection. Completed duration but failed validation/finalization. "
                    "~28.59M records, 30 candidate hours, 2272/2280 slots.",
        source_type="jsonl_raw",
        source_roots=("s3://bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433/"
                      "market-data/temporary/aws-validation-30h-20260912-6576f63/",),
        time_range_start="2026-09-12T00:00:00Z",
        time_range_end="2026-09-13T05:59:59Z",
        exchange_universe=("bithumb", "binance", "upbit"),
        feed_universe=("trade", "orderbook", "ticker"),
        raw_schema_version="v9.1.0-quarantine-hardened",
        manifest_schema_version="4",
        known_integrity_status="PARTIAL",
        known_data_quality_issues=(
            "27/30 candidate hours had exact 76/76 RAW coverage",
            "8 missing feed-hours classified as UNKNOWN_MISSING",
            "UNKNOWN_MISSING: 2026-09-12_17 Bithumb KRW-MANA ticker+trade",
            "UNKNOWN_MISSING: 2026-09-12_18 Bithumb KRW-AXS ticker+trade, KRW-MANA ticker+trade",
            "UNKNOWN_MISSING: 2026-09-12_19 Bithumb KRW-MANA ticker+trade",
            "writer_errors=0, queue_drop=0, unpersisted=0",
            "No immutable proof that missing slots were zero-event",
        ),
        allowed_for_exploration=True,
        allowed_for_candidate_selection=False,
        allowed_for_final_holdout=False,
        immutable_source=True,
        notes="DEVELOPMENT / RESEARCH ONLY. NOT final alpha evidence. "
              "8 missing slots classified UNKNOWN_MISSING, NOT verified zero-event.",
    ))

    registry.register(DatasetRegistration(
        dataset_id="v4",
        dataset_role=DatasetRole.QUARANTINED,
        description="V4 30H validation run. CURRENTLY RUNNING as of 2026-09-15. "
                    "aws-validation-30h-20260915-v4.",
        source_type="jsonl_raw",
        source_roots=("s3://bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433/"
                      "market-data/temporary/aws-validation-30h-20260915-v4/",),
        time_range_start="2026-09-15T10:26:33Z",
        time_range_end=None,
        exchange_universe=("bithumb", "binance", "upbit"),
        feed_universe=("trade", "orderbook", "ticker"),
        raw_schema_version="v9.1.0-quarantine-hardened",
        manifest_schema_version="4",
        known_integrity_status="UNKNOWN",
        known_data_quality_issues=(
            "RUNNING: Do not use for research until infrastructure PASS",
            "Must complete collection, finalize, and receive independent PASS",
        ),
        allowed_for_exploration=False,
        allowed_for_candidate_selection=False,
        allowed_for_final_holdout=False,
        immutable_source=True,
        notes="QUARANTINED until infrastructure PASS. Then PROSPECTIVE_RESEARCH.",
        source_run_id="aws-validation-30h-run-20260915T061253Z-v4",
        collector_epoch="aws-validation-30h-20260915-v4",
    ))

    registry.register(DatasetRegistration(
        dataset_id="fresh45",
        dataset_role=DatasetRole.INFRA_VALIDATION_ONLY,
        description="45-minute validation reference data (Aug 25-28, 2026). "
                    "~5,396 JSONL files, ~74 GB. 3 exchanges, 76 feeds.",
        source_type="jsonl_raw",
        source_roots=("data/microstructure/raw/",),
        time_range_start="2026-08-25T15:00:00Z",
        time_range_end="2026-08-28T17:00:00Z",
        exchange_universe=("bithumb", "binance", "upbit"),
        feed_universe=("trade", "orderbook", "ticker"),
        raw_schema_version="v9.1.0-quarantine-hardened",
        manifest_schema_version="4",
        known_integrity_status="PASS",
        known_data_quality_issues=(),
        allowed_for_exploration=True,
        allowed_for_candidate_selection=False,
        allowed_for_final_holdout=False,
        immutable_source=True,
        notes="INFRA_VALIDATION reference. May use for schema/adapter compatibility tests. "
              "Used as local development dataset for pipeline testing.",
    ))
