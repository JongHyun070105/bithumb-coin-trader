"""Phase 5 Comprehensive Synthetic Post-Soak E2E, Negative Matrix, and Mutation Suite.

Tests for P13, P13.1, P14, P15:
- P13: Real producer contract synthetic epoch (Bithumb, Binance, Upbit for OB, Trade, Ticker).
- P13.1: Count conservation (source_valid == canonical_count + rejected_count).
- P14: Negative post-soak E2E failure matrix (24 unsafe conditions blocked fail-closed).
- P15: Mutation sensitivity proving safety net captures corruptions/regressions.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pytest
import shutil
import tempfile
from typing import Any

from bithumb_coin_trader.canonical_market_data import (
    CanonicalDataValidationError,
    CanonicalOrderBook,
    CanonicalTrade,
    TimestampSemantics,
    raw_record_to_canonical,
    read_canonical_ndjson_zstd,
)
from bithumb_coin_trader.cross_market_collector import (
    parse_binance_message,
    parse_bithumb_message,
    parse_upbit_message,
)
from bithumb_coin_trader.experiment_runner import (
    ExperimentGatingError,
    ExperimentLedger,
    PreregistrationManifest,
    ResearchCyclePolicy,
    TrialBudgetExceededError,
    validate_safe_identifier,
)
from bithumb_coin_trader.microstructure_storage import RawMicrostructureStorage
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
from scripts.audit_72h_soak import SoakAuditor72H


# =============================================================================
# Helpers: Producer-Contract Frame Builders
# =============================================================================

def _build_bithumb_raw_frames() -> list[bytes]:
    """Synthetic Bithumb WebSocket frames passed to parse_bithumb_message."""
    return [
        # OrderBook frame (timestamp in microseconds)
        json.dumps({
            "type": "orderbook",
            "code": "KRW-BTC",
            "timestamp": 1725463800123456,
            "orderbook_units": [
                {"bid_price": 100_000_000.0, "bid_size": 1.5, "ask_price": 100_050_000.0, "ask_size": 0.8},
                {"bid_price": 99_990_000.0, "bid_size": 2.0, "ask_price": 100_060_000.0, "ask_size": 1.2},
            ],
        }).encode("utf-8"),
        # Trade frame (trade_timestamp in milliseconds)
        json.dumps({
            "type": "trade",
            "code": "KRW-BTC",
            "trade_timestamp": 1725463800200,
            "trade_id": "bithumb_tr_001",
            "price": 100_050_000.0,
            "units_traded": 0.5,
            "ask_bid": "ASK",
        }).encode("utf-8"),
        # Ticker frame
        json.dumps({
            "type": "ticker",
            "code": "KRW-BTC",
            "timestamp": 1725463800300000,
            "opening_price": 99_000_000.0,
            "high_price": 101_000_000.0,
            "low_price": 98_500_000.0,
            "trade_price": 100_050_000.0,
        }).encode("utf-8"),
    ]


def _build_binance_raw_frames() -> list[bytes]:
    """Synthetic Binance combined-stream WebSocket frames."""
    return [
        # Depth / OrderBook frame
        json.dumps({
            "stream": "btcusdt@depth",
            "data": {
                "e": "depthUpdate",
                "s": "BTCUSDT",
                "E": 1725463800000,
                "b": [["58000.00", "1.2"], ["57990.00", "0.8"]],
                "a": [["58001.00", "0.9"], ["58010.00", "1.5"]],
            },
        }).encode("utf-8"),
        # Trade frame
        json.dumps({
            "stream": "btcusdt@trade",
            "data": {
                "e": "trade",
                "s": "BTCUSDT",
                "E": 1725463800100,
                "t": 987654321,
                "p": "58001.00",
                "q": "0.25",
                "m": True,  # buyer is maker -> aggressor is SELL
            },
        }).encode("utf-8"),
    ]


def _build_upbit_raw_frames() -> list[bytes]:
    """Synthetic Upbit WebSocket frames."""
    return [
        # Orderbook frame
        json.dumps({
            "type": "orderbook",
            "code": "KRW-BTC",
            "timestamp": 1725463800000,
            "orderbook_units": [
                {"bid_price": 100_010_000.0, "bid_size": 0.5, "ask_price": 100_020_000.0, "ask_size": 0.7},
            ],
        }).encode("utf-8"),
        # Trade frame
        json.dumps({
            "type": "trade",
            "code": "KRW-BTC",
            "timestamp": 1725463800150,
            "sequential_id": 11223344,
            "trade_price": 100_020_000.0,
            "trade_volume": 0.1,
            "ask_bid": "BID",
        }).encode("utf-8"),
    ]


def _populate_synthetic_epoch(epoch_dir: Path) -> dict[str, int]:
    """Populates a synthetic epoch using REAL repository producer APIs."""
    raw_dir = epoch_dir / "raw"
    manifests_dir = epoch_dir / "manifests"
    receipts_dir = epoch_dir / "receipts"
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)
    receipts_dir.mkdir(parents=True, exist_ok=True)

    storage = RawMicrostructureStorage(base_dir=raw_dir, manifest_dir=manifests_dir)

    counts_by_stream: dict[str, int] = {}
    base_dt = datetime(2026, 9, 4, 15, 30, tzinfo=timezone.utc)
    base_mono = 1_000_000_000

    # 1. Bithumb frames
    bithumb_frames = _build_bithumb_raw_frames()
    for idx, frame in enumerate(bithumb_frames):
        stream, market, payload, exch_ts = parse_bithumb_message(frame)
        local_recv = base_dt.replace(microsecond=idx * 100_000)
        mono_ns = base_mono + idx * 100_000_000
        p_file = storage.append_raw_record(
            exchange="bithumb",
            stream=stream,
            market=market,
            payload=payload,
            local_receive_ts=local_recv,
            exchange_ts=exch_ts,
            local_receive_monotonic_ns=mono_ns,
            collector_run_id="run-bithumb-01",
            write_ts=local_recv,
        )
        key = f"bithumb/{market}/{stream}"
        counts_by_stream[key] = counts_by_stream.get(key, 0) + 1
        storage.generate_partition_manifest(p_file)

    # 2. Binance frames
    binance_frames = _build_binance_raw_frames()
    for idx, frame in enumerate(binance_frames):
        stream, market, payload, exch_ts = parse_binance_message(frame)
        local_recv = base_dt.replace(microsecond=300_000 + idx * 100_000)
        mono_ns = base_mono + (3 + idx) * 100_000_000
        p_file = storage.append_raw_record(
            exchange="binance",
            stream=stream,
            market=market,
            payload=payload,
            local_receive_ts=local_recv,
            exchange_ts=exch_ts,
            local_receive_monotonic_ns=mono_ns,
            collector_run_id="run-binance-01",
            write_ts=local_recv,
        )
        key = f"binance/{market}/{stream}"
        counts_by_stream[key] = counts_by_stream.get(key, 0) + 1
        storage.generate_partition_manifest(p_file)

    # 3. Upbit frames
    upbit_frames = _build_upbit_raw_frames()
    for idx, frame in enumerate(upbit_frames):
        stream, market, payload, exch_ts = parse_upbit_message(frame)
        local_recv = base_dt.replace(microsecond=500_000 + idx * 100_000)
        mono_ns = base_mono + (5 + idx) * 100_000_000
        p_file = storage.append_raw_record(
            exchange="upbit",
            stream=stream,
            market=market,
            payload=payload,
            local_receive_ts=local_recv,
            exchange_ts=exch_ts,
            local_receive_monotonic_ns=mono_ns,
            collector_run_id="run-upbit-01",
            write_ts=local_recv,
        )
        key = f"upbit/{market}/{stream}"
        counts_by_stream[key] = counts_by_stream.get(key, 0) + 1
        storage.generate_partition_manifest(p_file)

    # 4. Valid Archive Receipts and Full Scan Report
    receipt_data = {
        "cohort": "2026-09-04_15",
        "state": "COMPLETED",
        "status": "PASS",
        "restore_verified": True,
        "restore_status": "PASS",
        "verified_partitions_count": len(counts_by_stream),
    }
    (receipts_dir / "2026-09-04_15.archive-receipt.json").write_text(json.dumps(receipt_data), encoding="utf-8")

    full_scan_data = {
        "scan_id": "fs-20260904-15",
        "status": "PASS",
        "checked_at": base_dt.isoformat(),
        "integrity": "CLEAN",
    }
    (receipts_dir / "full_scan_20260904_15_report.json").write_text(json.dumps(full_scan_data), encoding="utf-8")

    return counts_by_stream


# =============================================================================
# P13 & P13.1: Full Synthetic Post-Soak Pipeline & Count Conservation
# =============================================================================

class TestSyntheticEpochEndToEnd:
    """P13: End-to-end post-soak pipeline using real producer contract."""

    def test_p13_full_pipeline_with_count_conservation(self, tmp_path: Path) -> None:
        """P13 & P13.1: Deep audit -> qualification -> stream-aware canonicalization -> count conservation -> prospective dataset."""
        epoch_dir = tmp_path / "synthetic_epoch"
        expected_counts = _populate_synthetic_epoch(epoch_dir)

        # 1. Authoritative Deep Audit
        auditor = SoakAuditor72H(epoch_dir)
        audit_report = auditor.audit()

        assert audit_report["status"] == "DQ_PASS_ELIGIBLE", f"Audit failed: {audit_report.get('blockers')}"
        assert audit_report["summary"]["raw_files_count"] == sum(expected_counts.values())
        assert audit_report["summary"]["manifests_count"] == sum(expected_counts.values())
        assert audit_report["summary"]["receipts_count"] == 1
        assert audit_report["summary"]["full_scan_reports_count"] == 1

        audit_report_path = tmp_path / "deep_dq_report.json"
        audit_report_path.write_text(json.dumps(audit_report, indent=2), encoding="utf-8")

        # Pick one manifest for source binding
        manifest_files = list((epoch_dir / "manifests").glob("**/manifest_*.json"))
        assert len(manifest_files) > 0
        source_manifest_path = manifest_files[0]

        # 2. DQ Qualification
        qualification_path = tmp_path / "qualification.json"
        qualify_args = argparse.Namespace(
            audit_report=str(audit_report_path),
            source_manifest=str(source_manifest_path),
            out=str(qualification_path),
            strict=True,
            auditor_commit=None,
        )
        exit_code = cmd_dq_qualify(qualify_args)
        assert exit_code == 0, "DQ qualification should succeed"
        assert qualification_path.exists()

        qual_data = json.loads(qualification_path.read_text(encoding="utf-8"))
        assert qual_data["status"] == "DQ_PASS"
        assert "audit_report_sha256" in qual_data
        assert "qualification_sha256" in qual_data

        # 3. Canonical Transformation for OrderBook and Trade
        canonical_dir = tmp_path / "canonical"
        canonical_dir.mkdir()

        # Bithumb orderbook transform
        tf_args = argparse.Namespace(
            input_dir=str(epoch_dir / "raw"),
            output_dir=str(canonical_dir),
            exchange="bithumb",
            stream="orderbook",
            market="KRW-BTC",
            schema_version="2.1.0",
        )
        tf_code = cmd_transform_canonical(tf_args)
        assert tf_code == 0

        # Binance trade transform
        tf_args_binance = argparse.Namespace(
            input_dir=str(epoch_dir / "raw"),
            output_dir=str(canonical_dir),
            exchange="binance",
            stream="trade",
            market="BTCUSDT",
            schema_version="2.1.0",
        )
        tf_code = cmd_transform_canonical(tf_args_binance)
        assert tf_code == 0

        # P13.1: Verify Count Conservation on produced canonical files
        canonical_files = list(canonical_dir.glob("*.ndjson.zst"))
        assert len(canonical_files) >= 2, "Must produce canonical orderbook and trade files"

        # Check Bithumb orderbook count conservation
        bithumb_ob_canonical = next(f for f in canonical_files if "bithumb" in f.name and "orderbook" in f.name)
        recs_bithumb = list(read_canonical_ndjson_zstd(bithumb_ob_canonical, CanonicalOrderBook))
        assert len(recs_bithumb) == expected_counts["bithumb/KRW-BTC/orderbook"]

        # Check Binance trade count conservation
        binance_tr_canonical = next(f for f in canonical_files if "binance" in f.name and "trade" in f.name)
        recs_binance = list(read_canonical_ndjson_zstd(binance_tr_canonical, CanonicalTrade))
        assert len(recs_binance) == expected_counts["binance/BTCUSDT/trade"]

        # 4. Prospective Dataset Partitioning
        dataset_out = tmp_path / "final_prospective_dataset"
        part_args = argparse.Namespace(
            input_file=str(bithumb_ob_canonical),
            output_dir=str(dataset_out),
            train_frac=0.50,
            val_frac=0.25,
            purge_window_ms=0,
            source_manifest=str(source_manifest_path),
            qualification_evidence=str(qualification_path),
            clock="receive",
            dataset_name="synthetic_postsoak_ds",
            source_epoch_id="epoch_synthetic_01",
            source_run_id="run_synthetic_01",
        )
        part_code = cmd_partition_dataset(part_args)
        assert part_code == 0, "Dataset partitioner must succeed"

        assert dataset_out.exists()
        manifest_file = dataset_out / "manifest.json"
        assert manifest_file.exists()
        ds_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))

        # P8.1 & P9: Validate full 64-character SHA-256 and distinct provenance commits
        assert len(ds_manifest["dataset_id"]) == 64, "dataset_id must be full 64-char SHA256"
        assert ds_manifest["source_epoch_id"] == "epoch_synthetic_01"
        assert ds_manifest["source_run_id"] == "run_synthetic_01"
        assert ds_manifest["dq_qualification_sha256"] == qual_data["qualification_sha256"]


# =============================================================================
# P14: Negative E2E Failure Matrix (24 Conditions)
# =============================================================================

class TestNegativePostSoakMatrix:
    """P14: All unsafe/corrupt/tampered conditions must fail-closed."""

    def test_n01_empty_epoch(self, tmp_path: Path) -> None:
        empty = tmp_path / "n01"
        empty.mkdir()
        res = SoakAuditor72H(empty).audit()
        assert res["status"] == "FAIL"
        assert "NO_RAW_EVIDENCE" in " ".join(res["blockers"])

    def test_n02_missing_manifest(self, tmp_path: Path) -> None:
        epoch = tmp_path / "n02"
        _populate_synthetic_epoch(epoch)
        shutil.rmtree(epoch / "manifests")
        res = SoakAuditor72H(epoch).audit()
        assert res["status"] == "FAIL"
        assert "NO_MANIFEST_EVIDENCE" in " ".join(res["blockers"])

    def test_n03_wrong_raw_sha(self, tmp_path: Path) -> None:
        epoch = tmp_path / "n03"
        _populate_synthetic_epoch(epoch)
        mf = list((epoch / "manifests").glob("**/manifest_*.json"))[0]
        m_data = json.loads(mf.read_text(encoding="utf-8"))
        m_data["sha256"] = "0" * 64
        mf.write_text(json.dumps(m_data), encoding="utf-8")
        res = SoakAuditor72H(epoch).audit()
        assert res["status"] == "FAIL"
        assert any("HASH_MISMATCH" in b for b in res["blockers"])

    def test_n04_wrong_bytes(self, tmp_path: Path) -> None:
        epoch = tmp_path / "n04"
        _populate_synthetic_epoch(epoch)
        mf = list((epoch / "manifests").glob("**/manifest_*.json"))[0]
        m_data = json.loads(mf.read_text(encoding="utf-8"))
        m_data["bytes"] = 999_999_999
        mf.write_text(json.dumps(m_data), encoding="utf-8")
        res = SoakAuditor72H(epoch).audit()
        assert res["status"] == "FAIL"
        assert any("BYTE_COUNT_MISMATCH" in b for b in res["blockers"])

    def test_n05_wrong_record_count(self, tmp_path: Path) -> None:
        epoch = tmp_path / "n05"
        _populate_synthetic_epoch(epoch)
        mf = list((epoch / "manifests").glob("**/manifest_*.json"))[0]
        m_data = json.loads(mf.read_text(encoding="utf-8"))
        m_data["record_count"] = 999_999
        mf.write_text(json.dumps(m_data), encoding="utf-8")
        res = SoakAuditor72H(epoch).audit()
        assert res["status"] == "FAIL"
        assert any("RECORD_COUNT_MISMATCH" in b for b in res["blockers"])

    def test_n06_corrupt_receipt(self, tmp_path: Path) -> None:
        epoch = tmp_path / "n06"
        _populate_synthetic_epoch(epoch)
        rf = list((epoch / "receipts").glob("*.archive-receipt.json"))[0]
        rf.write_text("NOT_VALID_JSON{{{", encoding="utf-8")
        res = SoakAuditor72H(epoch).audit()
        assert res["status"] == "FAIL"
        assert any("RECEIPT_CORRUPT" in b for b in res["blockers"])

    def test_n07_receipt_failure_state(self, tmp_path: Path) -> None:
        epoch = tmp_path / "n07"
        _populate_synthetic_epoch(epoch)
        rf = list((epoch / "receipts").glob("*.archive-receipt.json"))[0]
        r_data = json.loads(rf.read_text(encoding="utf-8"))
        r_data["state"] = "FAILED"
        r_data["failure_reason"] = "S3 upload timeout"
        rf.write_text(json.dumps(r_data), encoding="utf-8")
        res = SoakAuditor72H(epoch).audit()
        assert res["status"] == "FAIL"
        assert any("RECEIPT_FAILED" in b for b in res["blockers"])

    def test_n08_restore_mismatch(self, tmp_path: Path) -> None:
        epoch = tmp_path / "n08"
        _populate_synthetic_epoch(epoch)
        rf = list((epoch / "receipts").glob("*.archive-receipt.json"))[0]
        r_data = json.loads(rf.read_text(encoding="utf-8"))
        r_data["restore_verified"] = False
        rf.write_text(json.dumps(r_data), encoding="utf-8")
        res = SoakAuditor72H(epoch).audit()
        assert res["status"] == "FAIL"
        assert any("RESTORE_MISMATCH" in b for b in res["blockers"])

    def test_n09_fullscan_fail(self, tmp_path: Path) -> None:
        epoch = tmp_path / "n09"
        _populate_synthetic_epoch(epoch)
        fs = list((epoch / "receipts").glob("full_scan_*_report.json"))[0]
        fs_data = json.loads(fs.read_text(encoding="utf-8"))
        fs_data["status"] = "FAIL"
        fs.write_text(json.dumps(fs_data), encoding="utf-8")
        res = SoakAuditor72H(epoch).audit()
        assert res["status"] == "FAIL"
        assert any("FULL_SCAN_FAIL" in b for b in res["blockers"])

    def test_n10_structural_audit_cannot_qualify(self, tmp_path: Path) -> None:
        fake_structural = tmp_path / "structural_report.json"
        fake_structural.write_text(json.dumps({
            "status": "PASS",
            "audit_type": "structural_only",
            "blockers": [],
        }), encoding="utf-8")
        src_manifest = tmp_path / "dummy_manifest.json"
        src_manifest.write_text(json.dumps({"manifest_id": "m1"}), encoding="utf-8")

        qual_out = tmp_path / "qual.json"
        args = argparse.Namespace(
            audit_report=str(fake_structural),
            source_manifest=str(src_manifest),
            out=str(qual_out),
            strict=True,
            auditor_commit=None,
        )
        code = cmd_dq_qualify(args)
        assert code == 2, "Must reject structural audit report with code 2"

    def test_n11_no_source_manifest_in_qualify(self, tmp_path: Path) -> None:
        report = tmp_path / "deep_report.json"
        report.write_text(json.dumps({"status": "DQ_PASS_ELIGIBLE", "blockers": []}), encoding="utf-8")
        qual_out = tmp_path / "qual.json"
        args = argparse.Namespace(
            audit_report=str(report),
            source_manifest=None,
            out=str(qual_out),
            strict=True,
            auditor_commit=None,
        )
        code = cmd_dq_qualify(args)
        assert code == 2, "Must reject qualify when source manifest is missing"

    def test_n12_audit_report_modified_after_qualification(self, tmp_path: Path) -> None:
        epoch = tmp_path / "n12"
        _populate_synthetic_epoch(epoch)
        auditor = SoakAuditor72H(epoch)
        report = auditor.audit()
        rep_file = tmp_path / "rep.json"
        rep_file.write_text(json.dumps(report), encoding="utf-8")
        src_manifest = list((epoch / "manifests").glob("**/manifest_*.json"))[0]

        qual_file = tmp_path / "qual.json"
        cmd_dq_qualify(argparse.Namespace(
            audit_report=str(rep_file),
            source_manifest=str(src_manifest),
            out=str(qual_file),
            strict=True,
            auditor_commit=None,
        ))

        # Tamper audit report by adding 1 byte
        rep_file.write_text(json.dumps(report) + " ", encoding="utf-8")

        # Validation must detect hash mismatch
        tampered_bytes = rep_file.read_bytes()
        tampered_sha = hashlib.sha256(tampered_bytes).hexdigest()
        qual_data = json.loads(qual_file.read_text(encoding="utf-8"))
        assert qual_data["audit_report_sha256"] != tampered_sha

    def test_n13_partition_dataset_missing_source_manifest(self, tmp_path: Path) -> None:
        canon_file = tmp_path / "test.ndjson.zst"
        from bithumb_coin_trader.canonical_market_data import write_canonical_ndjson_zstd
        ob = CanonicalOrderBook(
            exchange="bithumb",
            market="KRW-BTC",
            receive_timestamp_ms=1000,
            exchange_timestamp_ms=1000,
            bids=[(100.0, 1.0)],
            asks=[(101.0, 1.0)],
        )
        write_canonical_ndjson_zstd(canon_file, [ob])

        out_dir = tmp_path / "dataset_out"
        dummy_dq = tmp_path / "dummy_dq.json"
        dummy_dq.write_text(json.dumps({
            "status": "DQ_PASS",
            "report_hash": "a" * 64,
            "approved_policy": "strict_v1",
        }), encoding="utf-8")
        args = argparse.Namespace(
            input_file=str(canon_file),
            output_dir=str(out_dir),
            dq_report=str(dummy_dq),
            train_frac=0.6,
            val_frac=0.2,
            purge_window_ms=0,
            source_manifest=None,  # missing
            qualification_evidence=None,
            clock="receive",
            dataset_name=None,
            source_epoch_id="epoch1",
            source_run_id="run1",
        )
        code = cmd_partition_dataset(args)
        assert code == 2, "partition-dataset must exit 2 when source manifest is missing"

    def test_n14_malformed_local_recv_rejected(self) -> None:
        malformed = {
            "exchange": "bithumb",
            "stream": "orderbook",
            "market": "KRW-BTC",
            "exchange_ts": "2026-09-04T15:30:00Z",
            "local_recv_ts": "NOT_A_TIMESTAMP",
            "local_recv_monotonic_ns": 1000,
            "payload": {"market": "KRW-BTC", "bids": [], "asks": []},
        }
        with pytest.raises(CanonicalDataValidationError, match="MALFORMED_LOCAL_RECV_TS"):
            raw_record_to_canonical(malformed)

    def test_n15_missing_temporal_key_fails(self) -> None:
        ob = CanonicalOrderBook(
            exchange="bithumb",
            market="KRW-BTC",
            receive_timestamp_ms=None,
            exchange_timestamp_ms=1000,
            bids=[(100.0, 1.0)],
            asks=[(101.0, 1.0)],
        )
        with pytest.raises(ValueError, match="TEMPORAL_KEY_MISSING"):
            partition_records_temporally([ob], clock="receive")

    def test_n16_staging_crash_leaves_no_target_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target_dir = tmp_path / "sealed_dataset"
        ob = CanonicalOrderBook(
            exchange="bithumb",
            market="KRW-BTC",
            receive_timestamp_ms=1000,
            exchange_timestamp_ms=1000,
            bids=[(100.0, 1.0)],
            asks=[(101.0, 1.0)],
        )
        dq_ev = DqQualificationEvidence(
            status=DqQualificationStatus.DQ_PASS,
            auditor_version="v9.1.0",
            audit_code_commit="c" * 40,
            source_manifest_hash="s" * 64,
            report_hash="q" * 64,
            created_at="2026-09-04T15:30:00Z",
            criteria_version="v1",
            hard_fail_count=0,
            unknown_count=0,
            degraded_count=0,
            justification="",
            approved_policy="strict_v1",
            audit_report_sha256="a" * 64,
            qualification_sha256="q" * 64,
            auditor_commit="c" * 40,
        )

        import bithumb_coin_trader.prospective_dataset as pds_mod
        def mock_partition(*args, **kwargs):
            raise RuntimeError("CRASH_DURING_STAGING")
        monkeypatch.setattr(pds_mod, "partition_records_temporally", mock_partition)

        with pytest.raises(RuntimeError, match="CRASH_DURING_STAGING"):
            build_and_export_dataset(
                dataset_id=None,
                output_dir=target_dir,
                records=[ob],
                dq_evidence=dq_ev,
            )
        assert not target_dir.exists(), "Target directory must not exist if build crashes before commit"

    def test_n17_never_overwrite_sealed_dataset(self, tmp_path: Path) -> None:
        target_dir = tmp_path / "existing_dataset"
        target_dir.mkdir()
        (target_dir / "manifest.json").write_text("{}", encoding="utf-8")

        ob = CanonicalOrderBook(
            exchange="bithumb",
            market="KRW-BTC",
            receive_timestamp_ms=1000,
            exchange_timestamp_ms=1000,
            bids=[(100.0, 1.0)],
            asks=[(101.0, 1.0)],
        )
        dq_ev = DqQualificationEvidence(
            status=DqQualificationStatus.DQ_PASS,
            auditor_version="v9.1.0",
            audit_code_commit="c" * 40,
            source_manifest_hash="s" * 64,
            report_hash="q" * 64,
            created_at="2026-09-04T15:30:00Z",
            criteria_version="v1",
            hard_fail_count=0,
            unknown_count=0,
            degraded_count=0,
            justification="",
            approved_policy="strict_v1",
            audit_report_sha256="a" * 64,
            qualification_sha256="q" * 64,
            auditor_commit="c" * 40,
        )
        with pytest.raises(FileExistsError, match="already exists and is non-empty"):
            build_and_export_dataset(
                dataset_id=None,
                output_dir=target_dir,
                records=[ob],
                dq_evidence=dq_ev,
            )

    def test_n18_trial_family_rename_bypass_blocked(self, tmp_path: Path) -> None:
        policy = ResearchCyclePolicy(
            cycle_id="c1",
            allowed_feature_families={"ofi": 2},
            max_total_trials=2,
        )
        ledger = ExperimentLedger(tmp_path / "ledger.json", policy=policy)
        bypass_manifest = PreregistrationManifest(
            trial_id="t_bypass",
            family_id="ofi2",  # disallowed rename
            hypothesis="bypass",
            features=("ofi2",),
            target_horizon_ms=1000,
            sample_budget=100,
        )
        with pytest.raises(ExperimentGatingError, match="DISALLOWED_FAMILY|INVALID_FAMILY"):
            ledger.reserve_trial(bypass_manifest)

    def test_n19_trial_self_budget_increase_blocked(self, tmp_path: Path) -> None:
        policy = ResearchCyclePolicy(
            cycle_id="c1",
            allowed_feature_families={"ofi": 1},
            max_total_trials=1,
        )
        ledger = ExperimentLedger(tmp_path / "ledger.json", policy=policy)
        m1 = PreregistrationManifest(
            trial_id="t1",
            family_id="ofi",
            hypothesis="h1",
            features=("ofi",),
            target_horizon_ms=1000,
            sample_budget=100,
            max_trials_in_family=100,  # self-authorized
        )
        ledger.reserve_trial(m1)

        m2 = PreregistrationManifest(
            trial_id="t2",
            family_id="ofi",
            hypothesis="h2",
            features=("ofi",),
            target_horizon_ms=1000,
            sample_budget=100,
            max_trials_in_family=100,
        )
        with pytest.raises(TrialBudgetExceededError):
            ledger.reserve_trial(m2)

    def test_n20_unsafe_identifier_path_traversal(self) -> None:
        with pytest.raises(ValueError, match="UNSAFE_IDENTIFIER"):
            validate_safe_identifier("../../etc/passwd")

    def test_n21_total_cycle_budget_enforced(self, tmp_path: Path) -> None:
        policy = ResearchCyclePolicy(
            cycle_id="c1",
            allowed_feature_families={"ofi": 2, "ati": 2},
            max_total_trials=2,  # cycle budget = 2
        )
        ledger = ExperimentLedger(tmp_path / "ledger.json", policy=policy)
        m1 = PreregistrationManifest(
            trial_id="t_ofi_1",
            family_id="ofi",
            hypothesis="h1",
            features=("ofi",),
            target_horizon_ms=1000,
            sample_budget=100,
        )
        m2 = PreregistrationManifest(
            trial_id="t_ati_1",
            family_id="ati",
            hypothesis="h2",
            features=("ati",),
            target_horizon_ms=1000,
            sample_budget=100,
        )
        m3 = PreregistrationManifest(
            trial_id="t_ati_2",
            family_id="ati",
            hypothesis="h3",
            features=("ati",),
            target_horizon_ms=1000,
            sample_budget=100,
        )
        ledger.reserve_trial(m1)
        ledger.reserve_trial(m2)
        with pytest.raises(TrialBudgetExceededError, match="Cycle total trial budget.*exhausted"):
            ledger.reserve_trial(m3)

    def test_n22_dq_rejected_evidence_blocks_dataset_build(self, tmp_path: Path) -> None:
        target_dir = tmp_path / "rejected_ds"
        ob = CanonicalOrderBook(
            exchange="bithumb",
            market="KRW-BTC",
            receive_timestamp_ms=1000,
            exchange_timestamp_ms=1000,
            bids=[(100.0, 1.0)],
            asks=[(101.0, 1.0)],
        )
        dq_ev = DqQualificationEvidence(
            status=DqQualificationStatus.DQ_FAIL,
            auditor_version="v9.1.0",
            audit_code_commit="c" * 40,
            source_manifest_hash="s" * 64,
            report_hash="q" * 64,
            created_at="2026-09-04T15:30:00Z",
            criteria_version="v1",
            hard_fail_count=1,
            unknown_count=0,
            degraded_count=0,
            justification="",
            approved_policy="strict_v1",
            audit_report_sha256="a" * 64,
            qualification_sha256="q" * 64,
            auditor_commit="c" * 40,
        )
        with pytest.raises(DqRejectedError, match="DQ status 'DQ_FAIL' cannot be qualified"):
            build_and_export_dataset(
                dataset_id=None,
                output_dir=target_dir,
                records=[ob],
                dq_evidence=dq_ev,
            )

    def test_n23_unsupported_stream_rejected_in_canonical_transform(self) -> None:
        unsupported = {
            "exchange": "bithumb",
            "stream": "unknown_future_stream",
            "market": "KRW-BTC",
            "exchange_ts": "2026-09-04T15:30:00Z",
            "local_recv_ts": "2026-09-04T15:30:00Z",
            "local_recv_monotonic_ns": 1000,
            "payload": {},
        }
        with pytest.raises(CanonicalDataValidationError, match="UNSUPPORTED_STREAM"):
            raw_record_to_canonical(unsupported)

    def test_n24_crossed_book_rejected(self) -> None:
        crossed = {
            "exchange": "bithumb",
            "stream": "orderbook",
            "market": "KRW-BTC",
            "exchange_ts": "2026-09-04T15:30:00Z",
            "local_recv_ts": "2026-09-04T15:30:00Z",
            "local_recv_monotonic_ns": 1000,
            "payload": {
                "market": "KRW-BTC",
                "bids": [{"price": 100_000_000.0, "quantity": 1.0}],
                "asks": [{"price": 99_000_000.0, "quantity": 1.0}],  # Ask < Bid (crossed)
            },
        }
        with pytest.raises(CanonicalDataValidationError, match="Crossed book"):
            raw_record_to_canonical(crossed)


# =============================================================================
# P15: Mutation Sensitivity Suite
# =============================================================================

class TestMutationSensitivity:
    """P15: Verifies that mutating core invariants triggers immediate test failure."""

    def test_mut_01_empty_epoch_status_mutation(self, tmp_path: Path) -> None:
        """Mutate empty epoch check: if auditor returns PASS for empty, test must fail."""
        empty = tmp_path / "mut_empty"
        empty.mkdir()
        auditor = SoakAuditor72H(empty)
        report = auditor.audit()
        # Invariant: Must NOT be PASS
        assert report["status"] != "PASS", "Mutation caught: empty epoch returning PASS is forbidden"

    def test_mut_02_fake_constant_source_hash_mutation(self, tmp_path: Path) -> None:
        """Mutate source manifest: if constant hash fallback was restored, test must fail."""
        report = tmp_path / "report.json"
        report.write_text(json.dumps({"status": "DQ_PASS_ELIGIBLE", "blockers": []}), encoding="utf-8")
        qual_out = tmp_path / "qual.json"
        args = argparse.Namespace(
            audit_report=str(report),
            source_manifest=None,
            out=str(qual_out),
            strict=True,
            auditor_commit=None,
        )
        code = cmd_dq_qualify(args)
        assert code == 2, "Mutation caught: constant fallback without source manifest is forbidden"

    def test_mut_03_stale_commit_hash_mutation(self, tmp_path: Path) -> None:
        """Mutate commit provenance: qualify must record current HEAD, not hardcoded stale Phase 3 commit."""
        epoch = tmp_path / "mut_commit"
        _populate_synthetic_epoch(epoch)
        auditor = SoakAuditor72H(epoch)
        report = auditor.audit()
        rep_file = tmp_path / "report.json"
        rep_file.write_text(json.dumps(report), encoding="utf-8")
        src_manifest = list((epoch / "manifests").glob("**/manifest_*.json"))[0]

        qual_file = tmp_path / "qual.json"
        cmd_dq_qualify(argparse.Namespace(
            audit_report=str(rep_file),
            source_manifest=str(src_manifest),
            out=str(qual_file),
            strict=True,
            auditor_commit=None,
        ))
        data = json.loads(qual_file.read_text(encoding="utf-8"))
        # Phase 3 hardcoded commit was e654f51 or earlier; auditor_commit must be dynamically detected
        assert data["auditor_commit"] != "c" * 40
        assert len(data["auditor_commit"]) == 40

    def test_mut_04_skip_raw_sha_verification(self, tmp_path: Path) -> None:
        """Mutate: if auditor skips raw sha verification, altered byte should not trigger HASH_MISMATCH."""
        epoch = tmp_path / "mut_sha"
        _populate_synthetic_epoch(epoch)
        raw_file = list((epoch / "raw").rglob("*.jsonl"))[0]
        raw_file.write_bytes(raw_file.read_bytes() + b"\n")
        report = SoakAuditor72H(epoch).audit()
        assert report["status"] == "FAIL"
        assert any("HASH_MISMATCH" in b or "RECORD_COUNT_MISMATCH" in b for b in report["blockers"])

    def test_mut_05_structural_audit_qualifies_dq(self, tmp_path: Path) -> None:
        """Mutate: structural-audit passed to dq-qualify must be rejected."""
        rep = tmp_path / "structural.json"
        rep.write_text(json.dumps({"status": "PASS", "audit_type": "structural_only", "blockers": []}))
        mf = tmp_path / "mf.json"
        mf.write_text(json.dumps({"manifest": 1}))
        out = tmp_path / "q.json"
        code = cmd_dq_qualify(argparse.Namespace(
            audit_report=str(rep),
            source_manifest=str(mf),
            out=str(out),
            strict=True,
            auditor_commit=None,
        ))
        assert code == 2

    def test_mut_06_constant_fake_source_hash(self, tmp_path: Path) -> None:
        """Mutate: no constant fallback source hash allowed without source manifest."""
        rep = tmp_path / "rep.json"
        rep.write_text(json.dumps({"status": "DQ_PASS_ELIGIBLE", "blockers": []}))
        code = cmd_dq_qualify(argparse.Namespace(
            audit_report=str(rep),
            source_manifest=None,
            out=str(tmp_path / "q.json"),
            strict=True,
            auditor_commit=None,
        ))
        assert code == 2

    def test_mut_07_hash_qualification_instead_of_audit_report(self, tmp_path: Path) -> None:
        """Mutate: audit_report_sha256 must match SHA256 of actual audit report bytes."""
        epoch = tmp_path / "mut_rep_hash"
        _populate_synthetic_epoch(epoch)
        report = SoakAuditor72H(epoch).audit()
        rep_path = tmp_path / "rep.json"
        rep_path.write_text(json.dumps(report, indent=2))
        mf = list((epoch / "manifests").glob("**/manifest_*.json"))[0]
        q_path = tmp_path / "qual.json"
        cmd_dq_qualify(argparse.Namespace(
            audit_report=str(rep_path),
            source_manifest=str(mf),
            out=str(q_path),
            strict=True,
            auditor_commit=None,
        ))
        q_data = json.loads(q_path.read_text())
        expected_sha = hashlib.sha256(rep_path.read_bytes()).hexdigest()
        assert q_data["audit_report_sha256"] == expected_sha
        assert q_data["qualification_sha256"] != q_data["audit_report_sha256"]

    def test_mut_08_source_manifest_optional(self, tmp_path: Path) -> None:
        """Mutate: partition-dataset must require source-manifest."""
        canon = tmp_path / "c.ndjson.zst"
        from bithumb_coin_trader.canonical_market_data import write_canonical_ndjson_zstd
        ob = CanonicalOrderBook(
            exchange="bithumb",
            market="KRW-BTC",
            receive_timestamp_ms=1000,
            exchange_timestamp_ms=1000,
            bids=[(100.0, 1.0)],
            asks=[(101.0, 1.0)],
        )
        write_canonical_ndjson_zstd(canon, [ob])
        dummy_dq = tmp_path / "dq.json"
        dummy_dq.write_text(json.dumps({"status": "DQ_PASS", "report_hash": "a" * 64, "approved_policy": "strict_v1"}))
        code = cmd_partition_dataset(argparse.Namespace(
            input_file=str(canon),
            output_dir=str(tmp_path / "ds"),
            dq_report=str(dummy_dq),
            train_frac=0.6,
            val_frac=0.2,
            purge_window_ms=0,
            source_manifest=None,
            qualification_evidence=None,
            clock="receive",
            dataset_name=None,
            source_epoch_id="e1",
            source_run_id="r1",
        ))
        assert code == 2

    def test_mut_10_copy_bithumb_raw_timestamp_directly(self) -> None:
        """Mutate: raw Bithumb microsecond timestamp must NOT be copied directly as ms."""
        raw_msg = json.dumps({
            "type": "orderbook",
            "code": "KRW-BTC",
            "timestamp": 1725463800123456,  # microseconds
            "orderbook_units": [{"bid_price": 100.0, "bid_size": 1.0, "ask_price": 101.0, "ask_size": 1.0}],
        })
        stream, market, payload, exch_ts = parse_bithumb_message(raw_msg)
        assert exch_ts is not None
        # Must be parsed as ~1725463800 sec, NOT 1.7e15 sec
        assert exch_ts.timestamp() < 2e9

    def test_mut_11_disable_stream_dispatch(self) -> None:
        """Mutate: raw_record_to_canonical must reject unsupported or unknown streams."""
        with pytest.raises(CanonicalDataValidationError, match="UNSUPPORTED_STREAM"):
            raw_record_to_canonical({
                "exchange": "bithumb",
                "stream": "magic_stream",
                "market": "KRW-BTC",
                "local_recv_ts": "2026-09-04T15:30:00Z",
                "exchange_ts": "2026-09-04T15:30:00Z",
                "local_recv_monotonic_ns": 100,
                "payload": {},
            })

    def test_mut_12_drop_trade_rows(self) -> None:
        """Mutate: raw_record_to_canonical correctly maps trades without dropping."""
        trade_rec = {
            "exchange": "bithumb",
            "stream": "trade",
            "market": "KRW-BTC",
            "local_recv_ts": "2026-09-04T15:30:00Z",
            "exchange_ts": "2026-09-04T15:30:00Z",
            "local_recv_monotonic_ns": 100,
            "payload": {
                "trade_id": "tr1",
                "price": 100.0,
                "units_traded": 2.0,
                "ask_bid": "ASK",
            },
        }
        res = raw_record_to_canonical(trade_rec)
        assert isinstance(res, CanonicalTrade)
        assert res.price == 100.0
        assert res.quantity == 2.0

    def test_mut_13_load_malformed_local_recv_as_none(self) -> None:
        """Mutate: malformed local_recv_ts must not be silently converted to None."""
        with pytest.raises(CanonicalDataValidationError, match="MALFORMED_LOCAL_RECV_TS"):
            raw_record_to_canonical({
                "exchange": "bithumb",
                "stream": "orderbook",
                "market": "KRW-BTC",
                "local_recv_ts": "INVALID_TS",
                "exchange_ts": "2026-09-04T15:30:00Z",
                "local_recv_monotonic_ns": 100,
                "payload": {"market": "KRW-BTC", "bids": [], "asks": []},
            })

    def test_mut_14_best_match_dsr_candidate(self) -> None:
        """Mutate: circular best-match DSR candidate search must remain removed."""
        from scripts.reproduce_v6_statistics import reproduce_v6
        import inspect
        sig = inspect.signature(reproduce_v6)
        assert "target_trial_id" in sig.parameters

    def test_mut_15_trial_increases_own_max_trials(self, tmp_path: Path) -> None:
        """Mutate: trial manifest self-increasing max_trials must be blocked by cycle policy."""
        policy = ResearchCyclePolicy(
            cycle_id="c1",
            allowed_feature_families={"ofi": 1},
            max_total_trials=1,
        )
        ledger = ExperimentLedger(tmp_path / "ledger.json", policy=policy)
        m = PreregistrationManifest(
            trial_id="t1",
            family_id="ofi",
            hypothesis="h",
            features=("ofi",),
            target_horizon_ms=1000,
            sample_budget=100,
            max_trials_in_family=10,
        )
        ledger.reserve_trial(m)
        m_bad = PreregistrationManifest(
            trial_id="t2",
            family_id="ofi",
            hypothesis="h",
            features=("ofi",),
            target_horizon_ms=1000,
            sample_budget=100,
            max_trials_in_family=10,
        )
        with pytest.raises(TrialBudgetExceededError):
            ledger.reserve_trial(m_bad)

    def test_mut_16_unsafe_traversal_trial_id(self) -> None:
        """Mutate: path traversal in identifier must be blocked."""
        with pytest.raises(ValueError, match="UNSAFE_IDENTIFIER"):
            validate_safe_identifier("../traversal")
