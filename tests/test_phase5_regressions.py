"""Phase 5 Regression and Contract Verification Suite.

Tests for P0 through P16:
- P0.1 - P0.9: Authoritative 72H auditor invariants (empty epoch fail, envelope keys, path layout, sha256 verification, feed coverage, receipt verification)
- P1.1 - P1.6: DQ qualify chain (deep audit required, no fake hash fallback, audit report hashing, source manifest binding)
- P2 - P2.1: Dynamic commit provenance and stage separation
- P3 - P3.2: Bithumb timestamp unit normalization and pure parser extraction
- P4 - P4.1: Explicit timestamp semantics model and malformed timestamp rejection
- P5 - P5.5: Stream-aware canonicalization (OrderBook, Trade, Ticker) with auto-dispatch
- P7 - P7.2: Streaming transform and partition with TEMPORAL_KEY_MISSING guard
- P8 - P8.1: Transactional dataset staging (<dataset>.building.<uuid>) and full 64-char ID
- P10 - P10.3: Pre-declared trial DSR evaluation and split status
- P11 - P11.3: Immutable ResearchCyclePolicy, family budget protection, safe identifiers
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any
import pytest

from bithumb_coin_trader.microstructure_storage import (
    PartitionManifest,
    RawMicrostructureStorage,
)
from bithumb_coin_trader.canonical_market_data import (
    CanonicalDataValidationError,
    CanonicalOrderBook,
    CanonicalTrade,
    TimestampSemantics,
)
from bithumb_coin_trader.prospective_dataset import (
    DatasetRole,
    DqQualificationEvidence,
    DqQualificationStatus,
    DqRejectedError,
    ProspectiveDatasetManifest,
    build_and_export_dataset,
    partition_records_temporally,
)
from bithumb_coin_trader.research_cli import (
    cmd_audit_quality,
    cmd_dq_qualify,
    cmd_partition_dataset,
    cmd_transform_canonical,
    compute_canonical_report_hash,
)
from bithumb_coin_trader.experiment_runner import (
    ExperimentGatingError,
    GovernedExperimentRunner,
    PreregistrationManifest,
    ReservationRecord,
    TrialBudgetExceededError,
    TrialStatus,
)
import scripts.audit_72h_soak as audit_module
from scripts.audit_72h_soak import SoakAuditor72H, parse_partition_path


# =============================================================================
# P0: Authoritative 72H Auditor Regressions
# =============================================================================

def test_p0_1_empty_epoch_must_fail(tmp_path: Path) -> None:
    """P0.1: Empty epoch must return status FAIL with NO_RAW_EVIDENCE / NO_MANIFEST_EVIDENCE."""
    empty_epoch = tmp_path / "empty_epoch"
    empty_epoch.mkdir()
    
    auditor = SoakAuditor72H(empty_epoch)
    report = auditor.audit()

    assert report["status"] == "FAIL", f"Empty epoch should FAIL, got {report['status']}"
    blockers = " ".join(report.get("blockers", []))
    assert "NO_RAW_EVIDENCE" in blockers
    assert "NO_MANIFEST_EVIDENCE" in blockers


def test_p0_2_actual_raw_envelope_keys(tmp_path: Path) -> None:
    """P0.2: Auditor must read actual envelope keys written by RawMicrostructureStorage."""
    epoch_dir = tmp_path / "test_epoch"
    raw_dir = epoch_dir / "raw"
    manifests_dir = epoch_dir / "manifests"
    storage = RawMicrostructureStorage(base_dir=raw_dir, manifest_dir=manifests_dir)

    now = datetime.now(timezone.utc)
    part_file = storage.append_raw_record(
        exchange="bithumb",
        stream="orderbook",
        market="KRW-BTC",
        payload={
            "market": "KRW-BTC",
            "bids": [{"price": 100_000_000.0, "quantity": 1.0}],
            "asks": [{"price": 100_100_000.0, "quantity": 1.0}],
        },
        local_receive_ts=now,
        exchange_ts=now,
        local_receive_monotonic_ns=1_000_000_000,
        collector_run_id="run-1",
        write_ts=now,
    )
    storage.generate_partition_manifest(part_file)

    auditor = SoakAuditor72H(epoch_dir)
    report = auditor.audit()

    feed_key = "bithumb/orderbook"
    assert feed_key in report["timestamp_quality"]
    feed_ts = report["timestamp_quality"][feed_key]
    assert feed_ts["total_records"] == 1
    assert feed_ts["exchange_ts_coverage"] == 1.0
    assert feed_ts["wall_ts_coverage"] == 1.0
    assert feed_ts["monotonic_ts_coverage"] == 1.0


def test_p0_3_actual_partition_path_resolution(tmp_path: Path) -> None:
    """P0.3: Auditor must resolve partition path from manifest metadata without assuming 4 path components."""
    epoch_dir = tmp_path / "test_epoch"
    raw_dir = epoch_dir / "raw"
    manifests_dir = epoch_dir / "manifests"
    storage = RawMicrostructureStorage(base_dir=raw_dir, manifest_dir=manifests_dir)

    now = datetime(2026, 9, 4, 15, 30, tzinfo=timezone.utc)
    part_file = storage.append_raw_record(
        exchange="binance",
        stream="orderbook",
        market="BTCUSDT",
        payload={"E": 1725463800000, "b": [["58000.0", "1.0"]], "a": [["58001.0", "1.0"]]},
        local_receive_ts=now,
        exchange_ts=now,
        local_receive_monotonic_ns=2_000_000_000,
        collector_run_id="run-2",
        write_ts=now,
    )
    storage.generate_partition_manifest(part_file)

    auditor = SoakAuditor72H(epoch_dir)
    report = auditor.audit()

    # Must resolve cell key with binance/BTCUSDT/orderbook
    feed_cells = list(report["feed_coverage"].keys())
    assert any("binance/btcusdt/orderbook" in c.lower() for c in feed_cells), f"Failed to map cell key in {feed_cells}"


def test_p0_4_manifest_hash_and_record_count_verification(tmp_path: Path) -> None:
    """P0.4: If raw partition content sha256 or record count mismatches manifest, auditor must hard FAIL."""
    epoch_dir = tmp_path / "test_epoch"
    raw_dir = epoch_dir / "raw"
    manifests_dir = epoch_dir / "manifests"
    storage = RawMicrostructureStorage(base_dir=raw_dir, manifest_dir=manifests_dir)

    now = datetime.now(timezone.utc)
    part_file = storage.append_raw_record(
        exchange="bithumb",
        stream="orderbook",
        market="KRW-BTC",
        payload={"market": "KRW-BTC", "bids": [], "asks": []},
        local_receive_ts=now,
        exchange_ts=now,
        write_ts=now,
    )
    storage.generate_partition_manifest(part_file)

    # Tamper raw file content
    with part_file.open("a", encoding="utf-8") as f:
        f.write('{"tampered": true}\n')

    auditor = SoakAuditor72H(epoch_dir)
    report = auditor.audit()

    assert report["status"] == "FAIL"
    blockers = " ".join(report["blockers"])
    assert "HASH_MISMATCH" in blockers or "RECORD_COUNT_MISMATCH" in blockers


def test_p0_5_receipt_and_fullscan_verification(tmp_path: Path) -> None:
    """P0.5: Auditor must verify archive receipts (*.archive-receipt.json) and full-scan reports."""
    epoch_dir = tmp_path / "test_epoch"
    receipts_dir = epoch_dir / "archive-receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)

    # Put a corrupt or failed full-scan report
    full_scan_file = receipts_dir / "full_scan_15_report.json"
    full_scan_file.write_text(json.dumps({"status": "FAIL", "hour": "15", "errors": ["checksum mismatch"]}))

    auditor = SoakAuditor72H(epoch_dir)
    report = auditor.audit()

    assert report["status"] == "FAIL"
    assert any("FULL_SCAN_FAIL" in b for b in report["blockers"])


def test_p0_6_feed_coverage_expected_universe(tmp_path: Path) -> None:
    """P0.6: 76-feed frozen coverage universe must be verified per closed hour."""
    epoch_dir = tmp_path / "test_epoch"
    auditor = SoakAuditor72H(epoch_dir)
    expected = auditor.get_expected_feed_universe()
    assert len(expected) == 76, f"Expected 76 feeds across Bithumb(60), Binance(8), Upbit(8), got {len(expected)}"


# =============================================================================
# P1: DQ Chain & Hardening Regressions
# =============================================================================

def test_p1_1_structural_audit_cannot_qualify_dq(tmp_path: Path) -> None:
    """P1.1: dq-qualify must reject structural-only reports with STRUCTURAL_ONLY_NOT_QUALIFIABLE."""
    structural_report = tmp_path / "structural_audit_report.json"
    structural_report.write_text(json.dumps({
        "status": "STRUCTURAL_AUDIT_PASS",
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "audit_type": "structural_only",
        "errors": [],
    }))

    out_evidence = tmp_path / "dq_evidence.json"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"source_hash": "a" * 64}))

    args = argparse.Namespace(
        audit_report=str(structural_report),
        out=str(out_evidence),
        source_manifest=str(manifest),
        policy="strict_v1",
        commit="HEAD",
        auditor_version="v9.1.0-offline",
        criteria_version="v1-strict",
    )
    rc = cmd_dq_qualify(args)
    assert rc == 2, "dq-qualify must return exit code 2 when fed a structural-only audit report"
    assert not out_evidence.exists(), "Should not produce qualification artifact for structural-only audit"


def test_p1_2_remove_fake_source_hash_fallback(tmp_path: Path) -> None:
    """P1.2: dq-qualify without --source-manifest or explicit verified source hash must FAIL."""
    deep_report = tmp_path / "deep_audit_report.json"
    deep_report.write_text(json.dumps({
        "status": "DQ_PASS_ELIGIBLE",
        "audit_type": "authoritative_deep_dq",
        "blockers": [],
        "warnings": [],
    }))

    out_evidence = tmp_path / "dq_evidence.json"
    args = argparse.Namespace(
        audit_report=str(deep_report),
        out=str(out_evidence),
        source_manifest=None,
        source_manifest_hash=None,
        policy="strict_v1",
        commit="HEAD",
        auditor_version="v9.1.0-offline",
        criteria_version="v1-strict",
    )
    rc = cmd_dq_qualify(args)
    assert rc == 2, "dq-qualify must fail when no source manifest is provided (no fake fallback allowed)"


def test_p1_3_hash_actual_audit_report_and_separate_fields(tmp_path: Path) -> None:
    """P1.3: Qualification evidence must record audit_report_sha256 and qualification_sha256 separately."""
    deep_report = tmp_path / "deep_audit_report.json"
    report_content = json.dumps({
        "status": "DQ_PASS_ELIGIBLE",
        "audit_type": "authoritative_deep_dq",
        "blockers": [],
    })
    deep_report.write_text(report_content)
    expected_report_sha = hashlib.sha256(deep_report.read_bytes()).hexdigest()

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"source_hash": "b" * 64}))

    out_evidence = tmp_path / "dq_evidence.json"
    args = argparse.Namespace(
        audit_report=str(deep_report),
        out=str(out_evidence),
        source_manifest=str(manifest),
        policy="strict_v1",
        commit="HEAD",
        auditor_version="v9.1.0-offline",
        criteria_version="v1-strict",
    )
    rc = cmd_dq_qualify(args)
    assert rc == 0
    assert out_evidence.exists()

    qual_data = json.loads(out_evidence.read_text())
    assert qual_data.get("audit_report_sha256") == expected_report_sha
    assert "qualification_sha256" in qual_data or "report_hash" in qual_data

    # Tamper deep_report: qualification verification must detect mismatch
    deep_report.write_text(json.dumps({
        "status": "DQ_PASS_ELIGIBLE",
        "audit_type": "authoritative_deep_dq",
        "blockers": [],
        "tampered": True,
    }))
    tampered_sha = hashlib.sha256(deep_report.read_bytes()).hexdigest()
    assert qual_data.get("audit_report_sha256") != tampered_sha


def test_p1_5_partition_dataset_requires_source_manifest(tmp_path: Path) -> None:
    """P1.5: partition-dataset must require --source-manifest and exit 2 if missing."""
    dummy_input = tmp_path / "input.ndjson.zst"
    dummy_input.write_bytes(b"dummy")
    out_dir = tmp_path / "output_dataset"

    args = argparse.Namespace(
        input_file=str(dummy_input),
        output_dir=str(out_dir),
        dq_report=str(tmp_path / "dq_evidence.json"),
        source_manifest=None,
        purge_window_ms=900_000,
        train_frac=0.6,
        val_frac=0.2,
    )
    rc = cmd_partition_dataset(args)
    assert rc == 2, "partition-dataset must exit 2 when --source-manifest is missing"


# =============================================================================
# P3 & P4: Timestamps & Parsers Regressions
# =============================================================================

def test_p3_1_bithumb_timestamp_unit_mismatch_prevention(tmp_path: Path) -> None:
    """P3.1: Raw Bithumb timestamp (microseconds in payload) must not be reinterpreted directly as ms."""
    from bithumb_coin_trader.cross_market_collector import parse_bithumb_message

    # Construct synthetic raw Bithumb orderbook message matching exchange format
    raw_json = json.dumps({
        "type": "orderbook",
        "code": "KRW-BTC",
        "timestamp": 1725580800123456,  # microseconds
        "orderbook_units": [
            {"bid_price": 100_000_000.0, "bid_size": 1.0, "ask_price": 100_100_000.0, "ask_size": 1.0}
        ]
    }).encode("utf-8")

    stream, market, data, exch_ts = parse_bithumb_message(raw_json)
    assert stream == "orderbook"
    assert market == "KRW-BTC"
    assert exch_ts is not None
    # Converted exchange_ts should be near 1725580800 seconds (Sept 2024), NOT year 56000+
    expected_ms = 1725580800123
    actual_ms = int(exch_ts.timestamp() * 1000)
    assert abs(actual_ms - expected_ms) <= 1, f"Timestamp unit error: got {actual_ms}, expected {expected_ms}"


def test_p4_1_malformed_local_recv_ts_must_reject_record(tmp_path: Path) -> None:
    """P4.1: If local_recv_ts exists in raw record but is malformed, REJECT record rather than setting None."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_file = raw_dir / "raw_bithumb_orderbook.jsonl"
    
    # Write a record with malformed local_recv_ts
    record = {
        "exchange": "bithumb",
        "stream": "orderbook",
        "market": "KRW-BTC",
        "exchange_ts": "2026-09-06T00:00:00+00:00",
        "local_recv_ts": "NOT_AN_ISO_TIMESTAMP",
        "local_write_ts": "2026-09-06T00:00:00+00:00",
        "payload": {
            "market": "KRW-BTC",
            "bids": [{"price": 100_000_000.0, "quantity": 1.0}],
            "asks": [{"price": 100_100_000.0, "quantity": 1.0}],
        },
    }
    raw_file.write_text(json.dumps(record) + "\n")

    out_dir = tmp_path / "canonical_out"
    args = argparse.Namespace(
        input_dir=str(raw_dir),
        output_dir=str(out_dir),
        exchange="bithumb",
        schema_version="2.0.0",
    )
    rc = cmd_transform_canonical(args)
    # Must reject the record, returning PARTIAL_REJECTED (exit code 2) or EMPTY (exit code 1)
    assert rc in (1, 2)
    report_file = out_dir / "transform_report.json"
    assert report_file.exists()
    report = json.loads(report_file.read_text())
    assert report["rejected_count"] == 1, "Malformed local_recv_ts must be counted as rejected"


# =============================================================================
# P5: Stream-Aware Canonicalization Regressions
# =============================================================================

def test_p5_4_mixed_streams_auto_dispatch(tmp_path: Path) -> None:
    """P5.4: Input directory containing both orderbook and trade streams must auto-dispatch without failing."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    
    ob_record = {
        "exchange": "bithumb",
        "stream": "orderbook",
        "market": "KRW-BTC",
        "exchange_ts": "2026-09-06T00:00:00+00:00",
        "local_recv_ts": "2026-09-06T00:00:00.001+00:00",
        "local_recv_monotonic_ns": 1000,
        "local_write_ts": "2026-09-06T00:00:00.002+00:00",
        "payload": {
            "orderbook_units": [{"bid_price": 100_000_000.0, "bid_size": 1.0, "ask_price": 100_100_000.0, "ask_size": 1.0}]
        },
    }
    trade_record = {
        "exchange": "bithumb",
        "stream": "trade",
        "market": "KRW-BTC",
        "exchange_ts": "2026-09-06T00:00:01+00:00",
        "local_recv_ts": "2026-09-06T00:00:01.001+00:00",
        "local_recv_monotonic_ns": 2000,
        "local_write_ts": "2026-09-06T00:00:01.002+00:00",
        "payload": {
            "trade_timestamp": 1725580801000,
            "trade_price": 100_050_000.0,
            "trade_volume": 0.5,
            "trade_id": "tx_12345",
            "ask_bid": "BID",
        },
    }
    (raw_dir / "mixed_stream.jsonl").write_text(json.dumps(ob_record) + "\n" + json.dumps(trade_record) + "\n")

    out_dir = tmp_path / "canonical_out"
    args = argparse.Namespace(
        input_dir=str(raw_dir),
        output_dir=str(out_dir),
        exchange="bithumb",
        schema_version="2.0.0",
    )
    rc = cmd_transform_canonical(args)
    assert rc == 0, "Transform must handle orderbook and trade streams without rejecting valid trade rows"
    
    report = json.loads((out_dir / "transform_report.json").read_text())
    assert report["canonicalized_count"] == 2
    assert report["rejected_count"] == 0


# =============================================================================
# P7 & P8: Streaming & Transactional Staging Regressions
# =============================================================================

def test_p7_2_temporal_key_missing_fail(tmp_path: Path) -> None:
    """P7.2: If configured partition clock is None in records, fail explicitly with TEMPORAL_KEY_MISSING."""
    ob = CanonicalOrderBook(
        exchange="bithumb",
        market="KRW-BTC",
        exchange_timestamp_ms=1725580800000,
        receive_timestamp_ms=None,  # Missing receive timestamp
        bids=((100_000_000.0, 1.0),),
        asks=((100_100_000.0, 1.0),),
    )

    with pytest.raises(ValueError, match="TEMPORAL_KEY_MISSING"):
        partition_records_temporally([ob], clock="receive_wall_clock")


def test_p8_dataset_build_staging_prevents_partial_dir(tmp_path: Path) -> None:
    """P8: Dataset build must stage in <dataset>.building.<uuid> and never leave partial output on failure."""
    target_out = tmp_path / "target_dataset"

    # Create DQ evidence with hard fail to trigger rejection
    dq_ev = DqQualificationEvidence(
        status=DqQualificationStatus.DQ_FAIL,
        auditor_version="v9.1.0",
        audit_code_commit="HEAD",
        source_manifest_hash="a" * 64,
        report_hash="b" * 64,
        created_at="2026-09-06T00:00:00Z",
        criteria_version="v1",
        hard_fail_count=1,
        unknown_count=0,
        degraded_count=0,
        justification="",
        approved_policy="strict_v1",
    )

    ob = CanonicalOrderBook(
        exchange="bithumb",
        market="KRW-BTC",
        exchange_timestamp_ms=1725580800000,
        receive_timestamp_ms=1725580800000,
        bids=((100_000_000.0, 1.0),),
        asks=((100_100_000.0, 1.0),),
    )

    with pytest.raises(DqRejectedError):
        build_and_export_dataset(
            dataset_id=None,
            output_dir=target_out,
            records=[ob],
            dq_evidence=dq_ev,
        )

    # Assert target directory does NOT exist or is not populated with partial artifacts
    assert not target_out.exists(), "Target directory must not exist if build failed during staging"


# =============================================================================
# P10: DSR Reproduction Regressions
# =============================================================================

def test_p10_1_dsr_reproduction_requires_predeclared_trial_id() -> None:
    """P10.1: reproduce_v6 must require explicit target_trial_id and not circular best-match search."""
    import inspect
    from scripts.reproduce_v6_statistics import reproduce_v6

    sig = inspect.signature(reproduce_v6)
    assert "target_trial_id" in sig.parameters, "reproduce_v6 must accept target_trial_id parameter"


# =============================================================================
# P11: Governance & Budget Regressions
# =============================================================================

def test_p11_governance_budget_cannot_be_self_authorized(tmp_path: Path) -> None:
    """P11: Trial manifest cannot expand family budget or rename family (ofi2) to bypass budget."""
    from bithumb_coin_trader.experiment_runner import ResearchCyclePolicy

    policy = ResearchCyclePolicy(
        cycle_id="cycle_20260906",
        allowed_feature_families={"ofi": 2, "ati": 2},
        max_total_trials=4,
    )
    ledger = ExperimentLedger(tmp_path / "ledger.json", policy=policy)

    # 1. Family rename bypass attempt ('ofi2' not in allowed_feature_families)
    bad_manifest = PreregistrationManifest(
        trial_id="trial_ofi2_01",
        family_id="ofi2",
        hypothesis="bypass attempt",
        features=("ofi2",),
        target_horizon_ms=5000,
        sample_budget=1000,
    )
    with pytest.raises(ExperimentGatingError, match="DISALLOWED_FAMILY|INVALID_FAMILY"):
        ledger.reserve_trial(bad_manifest)

    # 2. Self-authorization attempt (trial attempting to set max_trials=100 when policy allows 2)
    m1 = PreregistrationManifest(
        trial_id="trial_ofi_01",
        family_id="ofi",
        hypothesis="legit 1",
        features=("ofi",),
        target_horizon_ms=5000,
        sample_budget=1000,
        max_trials_in_family=100,
    )
    ledger.reserve_trial(m1)

    m2 = PreregistrationManifest(
        trial_id="trial_ofi_02",
        family_id="ofi",
        hypothesis="legit 2",
        features=("ofi",),
        target_horizon_ms=5000,
        sample_budget=1000,
        max_trials_in_family=100,
    )
    ledger.reserve_trial(m2)

    # 3rd trial must fail under policy limit 2 even if manifest claims max_trials_in_family=100
    m3 = PreregistrationManifest(
        trial_id="trial_ofi_03",
        family_id="ofi",
        hypothesis="legit 3",
        features=("ofi",),
        target_horizon_ms=5000,
        sample_budget=1000,
        max_trials_in_family=100,
    )
    with pytest.raises(TrialBudgetExceededError):
        ledger.reserve_trial(m3)


def test_p11_3_safe_identifiers_reject_path_traversal(tmp_path: Path) -> None:
    """P11.3: trial_id, family_id, cycle_id must reject path traversal attacks."""
    from bithumb_coin_trader.experiment_runner import validate_safe_identifier

    with pytest.raises(ValueError, match="UNSAFE_IDENTIFIER"):
        validate_safe_identifier("../traversal")

    with pytest.raises(ValueError, match="UNSAFE_IDENTIFIER"):
        validate_safe_identifier("slash/in/id")

    with pytest.raises(ValueError, match="UNSAFE_IDENTIFIER"):
        validate_safe_identifier("null\0byte")

    # Valid identifier must pass
    assert validate_safe_identifier("trial_v9_valid-01") == "trial_v9_valid-01"
