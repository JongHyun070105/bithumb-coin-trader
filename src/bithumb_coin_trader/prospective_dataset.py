"""Prospective Research Dataset Builder and Temporal Partitioner (P9 - P9.5).

FORENSIC HARDENING (Phase 2.5):
- BUG-8 FIXED: partition_records_temporally() now REJECTS unsorted input with
  ValueError instead of silently sorting. Prior to this fix, clock reversals
  (a hallmark of data quality problems) were hidden by sorting.
- ADDED: fraction validation (0 < train_frac < 1, 0 <= val_frac < 1, sum < 1,
  purge_window_ms >= 0). Invalid parameters now raise ValueError.
- ADDED: DqQualificationStatus enum — callers must explicitly pass a DQ status
  instead of an optional boolean bypass. Rejected if DQ_FAIL or DQ_UNKNOWN.
- ADDED: Explicit partition counts (source_record_count, train_record_count,
  validation_record_count, holdout_record_count, embargo counts).
- ADDED: Source provenance fields in manifest.

Builds leakage-free research datasets:
- Strict temporal partitioning: TRAIN -> EMBARGO -> VALIDATION -> EMBARGO -> HOLDOUT.
- Enforced purge windows (embargo) between partitions to eliminate autocorrelation bleed.
- Cryptographic SHA-256 checksums and tamper-evident dataset manifests.
- Export to zstandard compressed canonical ndjson.

LIMITATIONS:
- "leakage-free" claim scoped to temporal ordering. Does not prevent feature-level
  leakage if caller computes features across partition boundaries.
- "immutable manifest" claim: manifest is a local JSON file and can be modified
  post-creation. Tamper-evidence requires external write-once storage.
- dataset_id and created_at_utc depend on wall-clock time (non-deterministic).
  For full reproducibility, use content-hash-derived dataset IDs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Sequence
import uuid

from .canonical_market_data import (
    CanonicalOrderBook,
    write_canonical_ndjson_zstd,
)
from .experiment_runner import DatasetRole


class DqQualificationStatus(str, Enum):
    """Explicit Data Quality qualification status.

    BUG-ADD: Replaces the optional boolean bypass that allowed callers to skip
    DQ checks. All callers must now pass an explicit DQ status.
    """
    DQ_PASS = "DQ_PASS"        # All DQ checks passed
    DQ_DEGRADED = "DQ_DEGRADED"  # Some issues, documented and accepted
    DQ_FAIL = "DQ_FAIL"         # DQ checks failed — dataset not usable
    DQ_UNKNOWN = "DQ_UNKNOWN"   # DQ not yet run

@dataclass
class DqQualificationEvidence:
    status: DqQualificationStatus
    auditor_version: str
    audit_code_commit: str
    source_manifest_hash: str
    report_hash: str
    created_at: str
    criteria_version: str
    hard_fail_count: int
    unknown_count: int
    degraded_count: int
    justification: str
    approved_policy: str
    audit_report_sha256: str = ""
    qualification_sha256: str = ""
    auditor_commit: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = DqQualificationStatus(self.status)

    def validate(self) -> None:
        if self.status in (DqQualificationStatus.DQ_FAIL, DqQualificationStatus.DQ_UNKNOWN):
            raise DqRejectedError(f"DQ status '{self.status.value}' cannot be qualified.")

        # P1.3: Reject unknown/default provenance
        critical_fields = {
            "auditor_version": self.auditor_version,
            "audit_code_commit": self.audit_code_commit,
            "source_manifest_hash": self.source_manifest_hash,
            "report_hash": self.report_hash,
            "criteria_version": self.criteria_version,
        }
        for field_name, val in critical_fields.items():
            if not val or not str(val).strip() or str(val).strip().lower() in {"unknown", "default"}:
                raise DqRejectedError(
                    f"P1.3 VIOLATION: DqQualificationEvidence field '{field_name}' "
                    f"has invalid or placeholder provenance value: {val!r}"
                )

        if self.approved_policy and self.approved_policy.strip().lower() in {"unknown", "default"}:
            raise DqRejectedError(
                f"P1.3 VIOLATION: DqQualificationEvidence field 'approved_policy' "
                f"has invalid or placeholder provenance value: {self.approved_policy!r}"
            )

        if self.hard_fail_count > 0:
            raise DqRejectedError(f"hard_fail_count must be 0, got {self.hard_fail_count}")

        if self.status == DqQualificationStatus.DQ_DEGRADED:
            if not self.justification or not self.justification.strip():
                raise DqRejectedError("DQ_DEGRADED requires non-empty justification")


class DqRejectedError(ValueError):
    """Raised when dataset build is attempted with invalid or disqualified DQ status."""


@dataclass(frozen=True, slots=True)
class PartitionMetadata:
    role: DatasetRole
    record_count: int
    start_receive_ms: int
    end_receive_ms: int
    sha256: str
    file_name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "record_count": self.record_count,
            "start_receive_ms": self.start_receive_ms,
            "end_receive_ms": self.end_receive_ms,
            "sha256": self.sha256,
            "file_name": self.file_name,
        }


@dataclass
class PartitionCounts:
    """Explicit breakdown of record counts across all partition stages."""
    source_record_count: int
    train_record_count: int
    embargo1_dropped_count: int  # records dropped in embargo between train and validation
    validation_record_count: int
    embargo2_dropped_count: int  # records dropped in embargo between validation and holdout
    holdout_record_count: int

    @property
    def total_assigned(self) -> int:
        return (
            self.train_record_count
            + self.embargo1_dropped_count
            + self.validation_record_count
            + self.embargo2_dropped_count
            + self.holdout_record_count
        )


@dataclass
class ProspectiveDatasetManifest:
    dataset_id: str
    exchange: str
    market: str
    total_records: int
    train_records: int
    validation_records: int
    holdout_records: int
    embargo1_dropped: int
    embargo2_dropped: int
    purge_window_ms: int
    dq_status: str
    partitions: dict[str, PartitionMetadata]
    created_at_utc: str
    source_epoch_id: str = "synthetic"
    source_run_id: str = "offline"
    source_manifest_hash: str = ""
    source_manifest_sha256: str = ""
    dq_report_hash: str = ""
    deep_dq_report_sha256: str = ""
    dq_qualification_sha256: str = ""
    deep_dq_auditor_commit: str = ""
    dataset_builder_commit: str = ""
    dq_criteria_version: str = ""
    canonicalizer_commit: str = "HEAD"
    canonical_schema_version: str = "2.0.0"
    partition_config_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "exchange": self.exchange,
            "market": self.market,
            "total_records": self.total_records,
            "source_record_count": self.total_records,
            "train_records": self.train_records,
            "validation_records": self.validation_records,
            "holdout_records": self.holdout_records,
            "embargo1_dropped": self.embargo1_dropped,
            "embargo2_dropped": self.embargo2_dropped,
            "purge_window_ms": self.purge_window_ms,
            "dq_status": self.dq_status,
            "partitions": {k: v.to_dict() for k, v in self.partitions.items()},
            "created_at_utc": self.created_at_utc,
            "source_epoch_id": self.source_epoch_id,
            "source_run_id": self.source_run_id,
            "source_manifest_hash": self.source_manifest_hash,
            "source_manifest_sha256": self.source_manifest_sha256 or self.source_manifest_hash,
            "dq_report_hash": self.dq_report_hash,
            "deep_dq_report_sha256": self.deep_dq_report_sha256 or self.dq_report_hash,
            "dq_qualification_sha256": self.dq_qualification_sha256 or self.dq_report_hash,
            "deep_dq_auditor_commit": self.deep_dq_auditor_commit,
            "dataset_builder_commit": self.dataset_builder_commit,
            "dq_criteria_version": self.dq_criteria_version,
            "canonicalizer_commit": self.canonicalizer_commit,
            "canonical_schema_version": self.canonical_schema_version,
            "partition_config_hash": self.partition_config_hash,
        }


def _extract_clock_ts(rec: Any, clock: str) -> int:
    clock_norm = clock.lower()
    if clock_norm in ("receive_wall_clock", "receive"):
        ts = getattr(rec, "receive_timestamp_ms", None)
        if ts is None and isinstance(rec, dict):
            ts = rec.get("receive_timestamp_ms")
        if ts is None:
            raise ValueError(
                "TEMPORAL_KEY_MISSING: Record is missing receive_timestamp_ms required for receive_wall_clock partition"
            )
        return int(ts)
    elif clock_norm in ("exchange_event_time", "exchange"):
        ts = getattr(rec, "exchange_timestamp_ms", None)
        if ts is None and isinstance(rec, dict):
            ts = rec.get("exchange_timestamp_ms")
        if ts is None:
            raise ValueError(
                "TEMPORAL_KEY_MISSING: Record is missing exchange_timestamp_ms required for exchange_event_time partition"
            )
        return int(ts)
    else:
        raise ValueError(f"UNSUPPORTED_CLOCK: {clock}")


def partition_records_temporally(
    records: Sequence[Any],
    train_frac: float = 0.60,
    val_frac: float = 0.20,
    purge_window_ms: int = 900_000,  # 15 minutes default
    clock: str = "receive_wall_clock",
) -> tuple[dict[DatasetRole, list[Any]], PartitionCounts]:
    """BUG-8 FIX: Splits chronologically sorted records into TRAIN, VALIDATION, HOLDOUT.

    REQUIRES pre-sorted input.
    Raises ValueError with TEMPORAL_KEY_MISSING if partition clock is None.
    Raises ValueError if input is unsorted.

    Returns:
        (splits dict, PartitionCounts)
    """
    # Validate fractions
    if not (0 < train_frac < 1):
        raise ValueError(f"train_frac must be in (0, 1), got {train_frac}")
    if not (0 <= val_frac < 1):
        raise ValueError(f"val_frac must be in [0, 1), got {val_frac}")
    if train_frac + val_frac >= 1:
        raise ValueError(
            f"train_frac + val_frac must be < 1, got {train_frac + val_frac:.4f}"
        )
    if purge_window_ms < 0:
        raise ValueError(f"purge_window_ms must be >= 0, got {purge_window_ms}")

    empty_splits = {DatasetRole.TRAIN: [], DatasetRole.VALIDATION: [], DatasetRole.HOLDOUT: []}
    if not records:
        return empty_splits, PartitionCounts(0, 0, 0, 0, 0, 0)

    # Validate clock keys and sorting
    _extract_clock_ts(records[0], clock)
    for i in range(1, len(records)):
        prev_ts = _extract_clock_ts(records[i - 1], clock)
        curr_ts = _extract_clock_ts(records[i], clock)
        if curr_ts < prev_ts:
            raise ValueError(
                f"Input records are not sorted by {clock}: "
                f"records[{i-1}].ts={prev_ts} > "
                f"records[{i}].ts={curr_ts}. "
                f"Clock reversals indicate data quality problems and must not be hidden. "
                f"Sort and validate records before partitioning."
            )

    n = len(records)
    train_end_idx = int(n * train_frac)
    train_records = list(records[:train_end_idx])

    train_end_ts = _extract_clock_ts(train_records[-1], clock) if train_records else 0
    val_start_ts = train_end_ts + purge_window_ms

    val_start_idx = train_end_idx
    while val_start_idx < n and _extract_clock_ts(records[val_start_idx], clock) < val_start_ts:
        val_start_idx += 1

    embargo1_count = val_start_idx - train_end_idx

    val_target_count = int(n * val_frac)
    val_end_idx = min(n, val_start_idx + val_target_count)
    val_records = list(records[val_start_idx:val_end_idx])

    val_end_ts = _extract_clock_ts(val_records[-1], clock) if val_records else val_start_ts
    holdout_start_ts = val_end_ts + purge_window_ms

    holdout_start_idx = val_end_idx
    while holdout_start_idx < n and _extract_clock_ts(records[holdout_start_idx], clock) < holdout_start_ts:
        holdout_start_idx += 1

    embargo2_count = holdout_start_idx - val_end_idx
    holdout_records = list(records[holdout_start_idx:])

    splits = {
        DatasetRole.TRAIN: train_records,
        DatasetRole.VALIDATION: val_records,
        DatasetRole.HOLDOUT: holdout_records,
    }
    counts = PartitionCounts(
        source_record_count=n,
        train_record_count=len(train_records),
        embargo1_dropped_count=embargo1_count,
        validation_record_count=len(val_records),
        embargo2_dropped_count=embargo2_count,
        holdout_record_count=len(holdout_records),
    )
    return splits, counts

def _streaming_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()

def build_and_export_dataset(
    dataset_id: str | None,
    output_dir: Path | str,
    records: Sequence[Any],
    dq_evidence: DqQualificationEvidence,
    purge_window_ms: int = 900_000,
    train_frac: float = 0.60,
    val_frac: float = 0.20,
    allow_overwrite: bool = False,
    source_epoch_id: str = "synthetic",
    source_run_id: str = "offline",
    clock: str = "receive_wall_clock",
    canonicalizer_commit: str | None = None,
    dataset_builder_commit: str | None = None,
) -> ProspectiveDatasetManifest:
    """Partitions, compresses, and writes manifest for prospective research dataset.

    Enforces:
    - Transactional staging (<dataset>.building.<uuid>/) (P8)
    - Full 64-char SHA-256 dataset identity (P8.1)
    - Sealing / overwrite protection (P0)
    - Full cryptographic provenance (P1, P3, P9)
    - Clock key missing check (P7.2)
    """
    dq_evidence.validate()

    if records:
        exchange = getattr(records[0], "exchange", None)
        market = getattr(records[0], "market", None)
        for r in records:
            if getattr(r, "exchange", None) != exchange or getattr(r, "market", None) != market:
                raise ValueError("MIXED_DATASET: All records must share the same exchange and market")

    target_out = Path(output_dir)
    if target_out.exists() and any(target_out.iterdir()) and not allow_overwrite:
        raise FileExistsError(f"Output directory {target_out} already exists and is non-empty")

    target_out.parent.mkdir(parents=True, exist_ok=True)
    staging_id = uuid.uuid4().hex
    staging_dir = target_out.parent / f"{target_out.name}.building.{staging_id}"
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        splits, counts = partition_records_temporally(
            records, train_frac=train_frac, val_frac=val_frac, purge_window_ms=purge_window_ms, clock=clock
        )

        partition_meta: dict[str, PartitionMetadata] = {}
        content_hash_parts: list[str] = []

        for role, recs in splits.items():
            fname = f"{role.value.lower()}.ndjson.zst"
            fpath = staging_dir / fname
            write_canonical_ndjson_zstd(fpath, recs)

            file_sha = _streaming_sha256(fpath)
            content_hash_parts.append(f"{role.value}:{file_sha}")

            start_ts = _extract_clock_ts(recs[0], clock) if recs else 0
            end_ts = _extract_clock_ts(recs[-1], clock) if recs else 0

            partition_meta[role.value] = PartitionMetadata(
                role=role,
                record_count=len(recs),
                start_receive_ms=start_ts,
                end_receive_ms=end_ts,
                sha256=file_sha,
                file_name=fname,
            )

        partition_config_str = json.dumps({
            "purge_window_ms": purge_window_ms,
            "train_frac": train_frac,
            "val_frac": val_frac,
            "partition_clock": clock,
        }, sort_keys=True)
        partition_config_hash = hashlib.sha256(partition_config_str.encode("utf-8")).hexdigest()
        canonical_content_hash = hashlib.sha256(";".join(sorted(content_hash_parts)).encode("utf-8")).hexdigest()

        canon_commit = canonicalizer_commit or dq_evidence.audit_code_commit
        builder_commit = dataset_builder_commit or dq_evidence.audit_code_commit

        canon_schema_ver = getattr(records[0], "schema_version", "2.0.0") if records else "2.0.0"

        if not dataset_id:
            id_source = json.dumps({
                "canonical_content_hash": canonical_content_hash,
                "canonical_schema_version": canon_schema_ver,
                "canonicalizer_commit": canon_commit,
                "dataset_builder_commit": builder_commit,
                "dq_criteria_version": dq_evidence.criteria_version,
                "dq_report_hash": dq_evidence.report_hash,
                "partition_config_hash": partition_config_hash,
                "source_manifest_hash": dq_evidence.source_manifest_hash,
            }, sort_keys=True)
            # Full 64-char SHA256 authoritative dataset_id (P8.1)
            dataset_id = hashlib.sha256(id_source.encode("utf-8")).hexdigest()

        manifest = ProspectiveDatasetManifest(
            dataset_id=dataset_id,
            exchange=getattr(records[0], "exchange", "unknown") if records else "unknown",
            market=getattr(records[0], "market", "unknown") if records else "unknown",
            total_records=counts.source_record_count,
            train_records=counts.train_record_count,
            validation_records=counts.validation_record_count,
            holdout_records=counts.holdout_record_count,
            embargo1_dropped=counts.embargo1_dropped_count,
            embargo2_dropped=counts.embargo2_dropped_count,
            purge_window_ms=purge_window_ms,
            dq_status=dq_evidence.status.value,
            partitions=partition_meta,
            created_at_utc=datetime.now(timezone.utc).isoformat(),
            source_epoch_id=source_epoch_id,
            source_run_id=source_run_id,
            source_manifest_hash=dq_evidence.source_manifest_hash,
            source_manifest_sha256=dq_evidence.source_manifest_hash,
            dq_report_hash=dq_evidence.report_hash,
            deep_dq_report_sha256=getattr(dq_evidence, "audit_report_sha256", "") or dq_evidence.report_hash,
            dq_qualification_sha256=getattr(dq_evidence, "qualification_sha256", "") or dq_evidence.report_hash,
            deep_dq_auditor_commit=getattr(dq_evidence, "auditor_commit", "") or dq_evidence.audit_code_commit,
            dataset_builder_commit=builder_commit,
            dq_criteria_version=dq_evidence.criteria_version,
            canonicalizer_commit=canon_commit,
            canonical_schema_version=canon_schema_ver,
            partition_config_hash=partition_config_hash,
        )

        manifest_path = staging_dir / "manifest.json"
        tmp = manifest_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(manifest.to_dict(), indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, manifest_path)

        # Verify staged partition integrity
        for role_val, meta in partition_meta.items():
            staged_part = staging_dir / meta.file_name
            if _streaming_sha256(staged_part) != meta.sha256:
                raise ValueError(f"Corrupt staged partition: {meta.file_name}")

        # Atomic move/publish to target_out
        if target_out.exists():
            if allow_overwrite:
                shutil.rmtree(target_out)
                staging_dir.rename(target_out)
            else:
                raise FileExistsError(f"Output directory {target_out} already exists and is non-empty")
        else:
            staging_dir.rename(target_out)

        try:
            dir_fd = os.open(str(target_out), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except (OSError, AttributeError):
            pass

        return manifest

    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
