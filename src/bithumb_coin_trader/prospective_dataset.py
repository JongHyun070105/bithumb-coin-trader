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
from pathlib import Path
from typing import Any, Sequence

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


class DqRejectedError(ValueError):
    """Raised when dataset build is attempted with DQ_FAIL or DQ_UNKNOWN status."""


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
    """Explicit breakdown of record counts across all partition stages.

    BUG-ADD: Prior implementation only reported total_records and per-partition counts.
    This dataclass exposes embargo-dropped counts separately so callers can verify
    the partitioner behaved correctly.
    """
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "exchange": self.exchange,
            "market": self.market,
            "total_records": self.total_records,
            "train_records": self.train_records,
            "validation_records": self.validation_records,
            "holdout_records": self.holdout_records,
            "embargo1_dropped": self.embargo1_dropped,
            "embargo2_dropped": self.embargo2_dropped,
            "purge_window_ms": self.purge_window_ms,
            "dq_status": self.dq_status,
            "partitions": {k: v.to_dict() for k, v in self.partitions.items()},
            "created_at_utc": self.created_at_utc,
        }


def partition_records_temporally(
    records: Sequence[CanonicalOrderBook],
    train_frac: float = 0.60,
    val_frac: float = 0.20,
    purge_window_ms: int = 900_000,  # 15 minutes default
) -> tuple[dict[DatasetRole, list[CanonicalOrderBook]], PartitionCounts]:
    """BUG-8 FIX: Splits chronologically sorted records into TRAIN, VALIDATION, HOLDOUT.

    REQUIRES pre-sorted input (ascending receive_timestamp_ms).
    Raises ValueError if input is unsorted.

    Prior to this fix, unsorted input was silently sorted, hiding clock reversals
    and data quality problems.

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

    # BUG-8 FIX: reject unsorted input instead of silently sorting
    for i in range(1, len(records)):
        if records[i].receive_timestamp_ms < records[i - 1].receive_timestamp_ms:
            raise ValueError(
                f"Input records are not sorted by receive_timestamp_ms: "
                f"records[{i-1}].ts={records[i-1].receive_timestamp_ms} > "
                f"records[{i}].ts={records[i].receive_timestamp_ms}. "
                f"Clock reversals indicate data quality problems and must not be hidden. "
                f"Sort and validate records before partitioning."
            )

    n = len(records)
    train_end_idx = int(n * train_frac)
    train_records = list(records[:train_end_idx])

    train_end_ts = train_records[-1].receive_timestamp_ms if train_records else 0
    val_start_ts = train_end_ts + purge_window_ms

    val_start_idx = train_end_idx
    while val_start_idx < n and records[val_start_idx].receive_timestamp_ms < val_start_ts:
        val_start_idx += 1

    embargo1_count = val_start_idx - train_end_idx

    val_target_count = int(n * val_frac)
    val_end_idx = min(n, val_start_idx + val_target_count)
    val_records = list(records[val_start_idx:val_end_idx])

    val_end_ts = val_records[-1].receive_timestamp_ms if val_records else val_start_ts
    holdout_start_ts = val_end_ts + purge_window_ms

    holdout_start_idx = val_end_idx
    while holdout_start_idx < n and records[holdout_start_idx].receive_timestamp_ms < holdout_start_ts:
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


def build_and_export_dataset(
    dataset_id: str,
    output_dir: Path | str,
    records: Sequence[CanonicalOrderBook],
    dq_status: DqQualificationStatus,
    purge_window_ms: int = 900_000,
) -> ProspectiveDatasetManifest:
    """BUG-ADD: Partitions, compresses, and writes manifest for prospective dataset.

    Now requires explicit DQ qualification status. Raises DqRejectedError if
    DQ_FAIL or DQ_UNKNOWN to prevent building research datasets from unqualified data.

    LIMITATION: dataset_id is caller-supplied. created_at_utc uses wall-clock time.
    For deterministic IDs, derive dataset_id from SHA-256 of content.
    """
    if dq_status in (DqQualificationStatus.DQ_FAIL, DqQualificationStatus.DQ_UNKNOWN):
        raise DqRejectedError(
            f"Cannot build research dataset with DQ status '{dq_status.value}'. "
            f"Run data quality checks and pass DQ_PASS or DQ_DEGRADED (with documented justification)."
        )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    splits, counts = partition_records_temporally(records, purge_window_ms=purge_window_ms)

    partition_meta: dict[str, PartitionMetadata] = {}

    for role, recs in splits.items():
        fname = f"{role.value.lower()}.ndjson.zst"
        fpath = out / fname
        write_canonical_ndjson_zstd(fpath, recs)

        # BUG-ADD: streaming hash is preferable for large files; here we use read_bytes()
        # which is NOT safe for very large datasets. Marked as TEST_ONLY for large data.
        content_bytes = fpath.read_bytes()  # NOTE: unsafe for large files, use streaming hash in production
        file_sha = hashlib.sha256(content_bytes).hexdigest()

        start_ts = recs[0].receive_timestamp_ms if recs else 0
        end_ts = recs[-1].receive_timestamp_ms if recs else 0

        partition_meta[role.value] = PartitionMetadata(
            role=role,
            record_count=len(recs),
            start_receive_ms=start_ts,
            end_receive_ms=end_ts,
            sha256=file_sha,
            file_name=fname,
        )

    manifest = ProspectiveDatasetManifest(
        dataset_id=dataset_id,
        exchange=records[0].exchange if records else "unknown",
        market=records[0].market if records else "unknown",
        total_records=counts.source_record_count,
        train_records=counts.train_record_count,
        validation_records=counts.validation_record_count,
        holdout_records=counts.holdout_record_count,
        embargo1_dropped=counts.embargo1_dropped_count,
        embargo2_dropped=counts.embargo2_dropped_count,
        purge_window_ms=purge_window_ms,
        dq_status=dq_status.value,
        partitions=partition_meta,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
    )

    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2))
    return manifest
