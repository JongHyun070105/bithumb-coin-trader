"""Prospective Research Dataset Builder and Temporal Partitioner (P9 - P9.5).

Builds leakage-free research datasets:
- Strict temporal partitioning: TRAIN -> EMBARGO -> VALIDATION -> EMBARGO -> HOLDOUT.
- Enforced purge windows (embargo) between partitions to eliminate autocorrelation bleed.
- Cryptographic SHA-256 checksums and immutable dataset manifests.
- Export to zstandard compressed canonical ndjson.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .canonical_market_data import (
    CanonicalOrderBook,
    write_canonical_ndjson_zstd,
)
from .experiment_runner import DatasetRole


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
class ProspectiveDatasetManifest:
    dataset_id: str
    exchange: str
    market: str
    total_records: int
    purge_window_ms: int
    partitions: dict[str, PartitionMetadata]
    created_at_utc: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "exchange": self.exchange,
            "market": self.market,
            "total_records": self.total_records,
            "purge_window_ms": self.purge_window_ms,
            "partitions": {k: v.to_dict() for k, v in self.partitions.items()},
            "created_at_utc": self.created_at_utc,
        }


def partition_records_temporally(
    records: Sequence[CanonicalOrderBook],
    train_frac: float = 0.60,
    val_frac: float = 0.20,
    purge_window_ms: int = 900_000,  # 15 minutes default
) -> dict[DatasetRole, list[CanonicalOrderBook]]:
    """Splits chronologically sorted records into TRAIN, VALIDATION, HOLDOUT with purge embargoes."""
    if not records:
        return {DatasetRole.TRAIN: [], DatasetRole.VALIDATION: [], DatasetRole.HOLDOUT: []}

    sorted_recs = sorted(records, key=lambda r: r.receive_timestamp_ms)
    n = len(sorted_recs)

    train_end_idx = int(n * train_frac)
    train_records = sorted_recs[:train_end_idx]

    train_end_ts = train_records[-1].receive_timestamp_ms if train_records else 0
    val_start_ts = train_end_ts + purge_window_ms

    # Find start of validation after purge window
    val_start_idx = train_end_idx
    while val_start_idx < n and sorted_recs[val_start_idx].receive_timestamp_ms < val_start_ts:
        val_start_idx += 1

    val_target_count = int(n * val_frac)
    val_end_idx = min(n, val_start_idx + val_target_count)
    val_records = sorted_recs[val_start_idx:val_end_idx]

    val_end_ts = val_records[-1].receive_timestamp_ms if val_records else val_start_ts
    holdout_start_ts = val_end_ts + purge_window_ms

    holdout_start_idx = val_end_idx
    while holdout_start_idx < n and sorted_recs[holdout_start_idx].receive_timestamp_ms < holdout_start_ts:
        holdout_start_idx += 1

    holdout_records = sorted_recs[holdout_start_idx:]

    return {
        DatasetRole.TRAIN: train_records,
        DatasetRole.VALIDATION: val_records,
        DatasetRole.HOLDOUT: holdout_records,
    }


def build_and_export_dataset(
    dataset_id: str,
    output_dir: Path | str,
    records: Sequence[CanonicalOrderBook],
    purge_window_ms: int = 900_000,
) -> ProspectiveDatasetManifest:
    """Partitions, compresses, and writes manifest for prospective dataset."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    splits = partition_records_temporally(records, purge_window_ms=purge_window_ms)

    partition_meta: dict[str, PartitionMetadata] = {}

    for role, recs in splits.items():
        fname = f"{role.value.lower()}.ndjson.zst"
        fpath = out / fname
        write_canonical_ndjson_zstd(fpath, recs)

        content_bytes = fpath.read_bytes()
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

    from datetime import datetime, timezone
    manifest = ProspectiveDatasetManifest(
        dataset_id=dataset_id,
        exchange=records[0].exchange if records else "unknown",
        market=records[0].market if records else "unknown",
        total_records=len(records),
        purge_window_ms=purge_window_ms,
        partitions=partition_meta,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
    )

    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2))
    return manifest
