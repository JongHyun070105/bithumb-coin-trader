"""Tests for ProspectiveDataset — including Phase 2.5 adversarial tests.

FORENSIC HARDENING TESTS:
- test_unsorted_input_raises_not_silently_sorted: BUG-8 critical regression
- test_invalid_fractions_raise: fraction validation
- test_dq_fail_rejected: DQ gate enforcement
- test_explicit_partition_counts: count transparency
"""
import pytest
import json
from pathlib import Path
from bithumb_coin_trader.canonical_market_data import CanonicalOrderBook
from bithumb_coin_trader.experiment_runner import DatasetRole
from bithumb_coin_trader.prospective_dataset import (
    DqQualificationEvidence,
    DqQualificationStatus,
    DqRejectedError,
    partition_records_temporally,
    build_and_export_dataset,
)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _make_books(count: int, interval_ms: int = 10_000, start_ms: int = 0):
    return [
        CanonicalOrderBook(
            exchange="bithumb",
            market="KRW-BTC",
            exchange_timestamp_ms=start_ms + i * interval_ms,
            receive_timestamp_ms=start_ms + i * interval_ms,
            bids=((100.0, 1.0),),
            asks=((101.0, 1.0),),
        )
        for i in range(count)
    ]


# ─── existing tests (updated for new signature) ──────────────────────────────

def test_temporal_partitioning_with_purge_window():
    # 100 records spaced 10 seconds apart (0s to 990s = 990,000ms)
    records = _make_books(100, interval_ms=10_000)
    # Purge window of 50 seconds (50,000ms = 5 records)
    splits, counts = partition_records_temporally(
        records, train_frac=0.60, val_frac=0.20, purge_window_ms=50_000
    )

    train = splits[DatasetRole.TRAIN]
    val = splits[DatasetRole.VALIDATION]
    holdout = splits[DatasetRole.HOLDOUT]

    assert len(train) == 60
    assert train[-1].receive_timestamp_ms == 590_000

    # Validation must start at or after 590,000 + 50,000 = 640,000ms
    assert val[0].receive_timestamp_ms >= 640_000
    val_end_ts = val[-1].receive_timestamp_ms

    # Holdout must start at or after val_end_ts + 50,000ms
    assert holdout[0].receive_timestamp_ms >= val_end_ts + 50_000

    # counts must be consistent
    assert counts.source_record_count == 100
    assert counts.train_record_count == len(train)
    assert counts.validation_record_count == len(val)
    assert counts.holdout_record_count == len(holdout)


def test_build_and_export_dataset(tmp_path: Path):
    out_dir = tmp_path / "prospective_dataset"
    records = _make_books(50, interval_ms=10_000)
    manifest = build_and_export_dataset(
        "ds_test_01", out_dir, records,
        dq_evidence=DqQualificationEvidence(status=DqQualificationStatus.DQ_PASS, auditor_version="1.0", audit_code_commit="1", source_manifest_hash="1", report_hash="1", created_at="1", criteria_version="1", hard_fail_count=0, unknown_count=0, degraded_count=0, justification="ok" if "DQ_PASS" == "DQ_DEGRADED" else "", approved_policy=""),
        purge_window_ms=20_000,
    )

    assert manifest.dataset_id == "ds_test_01"
    assert (out_dir / "manifest.json").exists()
    assert (out_dir / "train.ndjson.zst").exists()
    assert (out_dir / "validation.ndjson.zst").exists()
    assert (out_dir / "holdout.ndjson.zst").exists()

    manifest_data = json.loads((out_dir / "manifest.json").read_text())
    assert manifest_data["total_records"] == 50
    assert "TRAIN" in manifest_data["partitions"]
    assert manifest_data["dq_status"] == "DQ_PASS"


# ─── BUG-8: unsorted input must FAIL ─────────────────────────────────────────

def test_unsorted_input_raises_not_silently_sorted():
    """BUG-8 CRITICAL: Unsorted input [100ms, 300ms, 200ms] must raise ValueError.

    Prior to the fix, partition_records_temporally() silently sorted the input,
    hiding clock reversals and data quality problems. This test ensures that
    behavior is gone.
    """
    # Three records: 100ms, 300ms, 200ms — clock reversal at index 2
    records = [
        CanonicalOrderBook("bithumb", "KRW-BTC", 100, 100, ((100.0, 1.0),), ((101.0, 1.0),)),
        CanonicalOrderBook("bithumb", "KRW-BTC", 300, 300, ((100.0, 1.0),), ((101.0, 1.0),)),
        CanonicalOrderBook("bithumb", "KRW-BTC", 200, 200, ((100.0, 1.0),), ((101.0, 1.0),)),
    ]
    with pytest.raises(ValueError, match="not sorted"):
        partition_records_temporally(records)


def test_reverse_sorted_input_raises():
    """BUG-8: Fully reversed input must also raise ValueError."""
    records = _make_books(10, interval_ms=1000)
    records_reversed = list(reversed(records))
    with pytest.raises(ValueError, match="not sorted"):
        partition_records_temporally(records_reversed)


def test_single_clock_reversal_at_end_raises():
    """BUG-8: A single clock reversal at the very end must raise ValueError."""
    records = _make_books(20, interval_ms=1000)
    # Swap last two to create one clock reversal
    records[-1], records[-2] = records[-2], records[-1]
    with pytest.raises(ValueError, match="not sorted"):
        partition_records_temporally(records)


def test_correctly_sorted_input_succeeds():
    """BUG-8 REGRESSION: Correctly sorted input must still work."""
    records = _make_books(30, interval_ms=1000)
    splits, counts = partition_records_temporally(records, purge_window_ms=0)
    assert counts.source_record_count == 30
    assert counts.train_record_count > 0


# ─── fraction validation ─────────────────────────────────────────────────────

def test_invalid_train_frac_raises():
    records = _make_books(20)
    with pytest.raises(ValueError, match="train_frac"):
        partition_records_temporally(records, train_frac=0.0)
    with pytest.raises(ValueError, match="train_frac"):
        partition_records_temporally(records, train_frac=1.0)
    with pytest.raises(ValueError, match="train_frac"):
        partition_records_temporally(records, train_frac=-0.1)


def test_invalid_val_frac_raises():
    records = _make_books(20)
    with pytest.raises(ValueError, match="val_frac"):
        partition_records_temporally(records, train_frac=0.5, val_frac=-0.1)
    with pytest.raises(ValueError, match="val_frac"):
        partition_records_temporally(records, train_frac=0.5, val_frac=1.0)


def test_sum_exceeds_one_raises():
    records = _make_books(20)
    with pytest.raises(ValueError, match=r"train_frac \+ val_frac"):
        partition_records_temporally(records, train_frac=0.6, val_frac=0.5)
    with pytest.raises(ValueError, match=r"train_frac \+ val_frac"):
        partition_records_temporally(records, train_frac=0.5, val_frac=0.5)


def test_negative_purge_window_raises():
    records = _make_books(20)
    with pytest.raises(ValueError, match="purge_window_ms"):
        partition_records_temporally(records, purge_window_ms=-1)


# ─── DQ gate ─────────────────────────────────────────────────────────────────

def test_dq_fail_rejected(tmp_path):
    """BUG-ADD: build_and_export_dataset must reject DQ_FAIL status."""
    records = _make_books(20)
    with pytest.raises(DqRejectedError, match="DQ_FAIL"):
        build_and_export_dataset(
            "bad_ds", tmp_path / "out", records,
            dq_evidence=DqQualificationEvidence(status=DqQualificationStatus.DQ_FAIL, auditor_version="1.0", audit_code_commit="1", source_manifest_hash="1", report_hash="1", created_at="1", criteria_version="1", hard_fail_count=0, unknown_count=0, degraded_count=0, justification="ok" if "DQ_FAIL" == "DQ_DEGRADED" else "", approved_policy=""),
        )


def test_dq_unknown_rejected(tmp_path):
    """BUG-ADD: build_and_export_dataset must reject DQ_UNKNOWN status."""
    records = _make_books(20)
    with pytest.raises(DqRejectedError, match="DQ_UNKNOWN"):
        build_and_export_dataset(
            "unknown_ds", tmp_path / "out", records,
            dq_evidence=DqQualificationEvidence(status=DqQualificationStatus.DQ_UNKNOWN, auditor_version="1.0", audit_code_commit="1", source_manifest_hash="1", report_hash="1", created_at="1", criteria_version="1", hard_fail_count=0, unknown_count=0, degraded_count=0, justification="ok" if "DQ_UNKNOWN" == "DQ_DEGRADED" else "", approved_policy=""),
        )


def test_dq_degraded_allowed(tmp_path):
    """BUG-ADD: DQ_DEGRADED is allowed (with documented justification)."""
    records = _make_books(20, interval_ms=1000)
    manifest = build_and_export_dataset(
        "degraded_ds", tmp_path / "out", records,
        dq_evidence=DqQualificationEvidence(status=DqQualificationStatus.DQ_DEGRADED, auditor_version="1.0", audit_code_commit="1", source_manifest_hash="1", report_hash="1", created_at="1", criteria_version="1", hard_fail_count=0, unknown_count=0, degraded_count=0, justification="ok" if "DQ_DEGRADED" == "DQ_DEGRADED" else "", approved_policy=""),
        purge_window_ms=0,
    )
    assert manifest.dq_status == "DQ_DEGRADED"


# ─── explicit partition counts ────────────────────────────────────────────────

def test_explicit_partition_counts_reported():
    """BUG-ADD: Partition counts must be individually reported."""
    records = _make_books(100, interval_ms=10_000)
    splits, counts = partition_records_temporally(
        records, train_frac=0.60, val_frac=0.20, purge_window_ms=50_000
    )
    assert counts.source_record_count == 100
    assert counts.train_record_count == 60
    assert counts.embargo1_dropped_count >= 0
    assert counts.validation_record_count >= 0
    assert counts.embargo2_dropped_count >= 0
    assert counts.holdout_record_count >= 0
    # All records accounted for
    assert counts.total_assigned == 100


def test_manifest_includes_split_counts(tmp_path):
    """BUG-ADD: manifest.json must include per-partition record counts."""
    records = _make_books(50, interval_ms=1000)
    manifest = build_and_export_dataset(
        "count_test", tmp_path / "counts_ds", records,
        dq_evidence=DqQualificationEvidence(status=DqQualificationStatus.DQ_PASS, auditor_version="1.0", audit_code_commit="1", source_manifest_hash="1", report_hash="1", created_at="1", criteria_version="1", hard_fail_count=0, unknown_count=0, degraded_count=0, justification="ok" if "DQ_PASS" == "DQ_DEGRADED" else "", approved_policy=""),
        purge_window_ms=0,
    )
    assert manifest.train_records >= 0
    assert manifest.validation_records >= 0
    assert manifest.holdout_records >= 0
    data = json.loads((tmp_path / "counts_ds" / "manifest.json").read_text())
    assert "train_records" in data
    assert "validation_records" in data
    assert "holdout_records" in data
    assert "embargo1_dropped" in data
    assert "embargo2_dropped" in data


def test_empty_records_return_zero_counts():
    """Edge case: empty records must return zeros without error."""
    splits, counts = partition_records_temporally([])
    assert counts.source_record_count == 0
    assert counts.total_assigned == 0
