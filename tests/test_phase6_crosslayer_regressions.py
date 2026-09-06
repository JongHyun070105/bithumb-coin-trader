"""Phase 6 Cross-Layer Forensics & Regression Reproduction Suite.

Before implementing fixes, every cross-layer contradiction between Phase 5 claims
and actual code execution must be reproduced here as an explicit test.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zstandard

import pytest

from bithumb_coin_trader.canonical_market_data import (
    CanonicalDataValidationError,
    CanonicalOrderBook,
    CanonicalTicker,
    CanonicalTrade,
    TimestampSemantics,
    raw_record_to_canonical,
    validate_canonical_orderbook,
)
from bithumb_coin_trader.prospective_dataset import (
    DqQualificationEvidence,
    DqQualificationStatus,
    build_and_export_dataset,
)
from bithumb_coin_trader.research_cli import (
    cmd_audit_quality,
    cmd_dq_qualify,
    cmd_partition_dataset,
    cmd_transform_canonical,
    main as cli_main,
)
from scripts.audit_72h_soak import EXPECTED_BITHUMB_20, EXPECTED_BINANCE_4, EXPECTED_UPBIT_4, SoakAuditor72H


# =============================================================================
# P0.1: Missing required feeds currently do NOT fail DQ
# =============================================================================

def test_p0_1_missing_required_feed_must_fail_dq(tmp_path: Path) -> None:
    """P0.1: When 75 out of 76 required feeds exist, audit status MUST be FAIL with blocker MISSING_REQUIRED_FEED."""
    epoch_dir = tmp_path / "epoch_75_feeds"
    raw_dir = epoch_dir / "raw"
    raw_dir.mkdir(parents=True)
    manifests_dir = epoch_dir / "manifests"
    manifests_dir.mkdir(parents=True)

    # Generate 75 feeds (skip the 76th feed: bithumb/KRW-DOT/ticker)
    feed_universe = SoakAuditor72H.get_expected_feed_universe()
    assert len(feed_universe) == 76, f"Universe must have 76 feeds, got {len(feed_universe)}"

    cctx = zstandard.ZstdCompressor(level=3)
    base_ts = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
    base_ms = int(base_ts.timestamp() * 1000)

    # Write 75 feeds
    for exch, strm, mkt in feed_universe[:-1]:
        part_dir = raw_dir / f"exchange={exch}" / f"stream={strm}" / f"market={mkt}"
        part_dir.mkdir(parents=True, exist_ok=True)
        rec = {
            "exchange": exch,
            "stream": strm,
            "market": mkt,
            "exchange_ts": base_ms,
            "local_recv_ts": base_ms + 10,
            "local_recv_monotonic_ns": 1_000_000_000,
            "collector_run_id": "run-p0-1",
            "local_write_ts": base_ms + 15,
            "payload": {"price": "1000", "bids": [["1000", "1.0"]], "asks": [["1001", "1.0"]]},
        }
        compressed = cctx.compress(json.dumps(rec).encode("utf-8") + b"\n")
        (part_dir / "part-00000.zst").write_bytes(compressed)

    # Dummy manifests and receipts
    manifest_data = {
        "collector_epoch": "epoch-p0-1",
        "collector_run_id": "run-p0-1",
        "runtime_commit": "abcdef123456",
        "raw_schema_version": "2.0.0",
        "start_time_utc": base_ts.isoformat(),
        "files": [],
    }
    (manifests_dir / "epoch_manifest.json").write_text(json.dumps(manifest_data))
    receipts_dir = epoch_dir / "archive-receipts"
    receipts_dir.mkdir(parents=True)
    (receipts_dir / "20260901-00.archive-receipt.json").write_text(json.dumps({
        "hour_cohort": "20260901-00",
        "file_count": 75,
        "restore_verified": True,
        "manifest_sha256": "dummy",
    }))

    auditor = SoakAuditor72H(epoch_dir)
    report = auditor.audit()

    # Phase 6 Requirement: MUST FAIL with MISSING_REQUIRED_FEED
    # Phase 5 bug: warnings.append(MISSING_FEED) -> blockers was empty -> DQ_PASS_ELIGIBLE
    blockers = " ".join(report.get("blockers", []))
    assert report["status"] == "FAIL", f"Expected FAIL for 75/76 feeds, got {report['status']} (warnings: {report.get('warnings')})"
    assert "MISSING_REQUIRED_FEED" in blockers or "MISSING_FEED" in blockers


# =============================================================================
# P3: Fix deep-dq CLI exit code mismatch
# =============================================================================

def test_p3_deep_dq_audit_cli_exit_code_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P3: When SoakAuditor72H produces status == 'DQ_PASS_ELIGIBLE', research_cli deep-dq-audit must exit 0, NOT 2."""
    epoch_dir = tmp_path / "epoch_p3"
    epoch_dir.mkdir()
    report_out = tmp_path / "deep_dq_report.json"

    # Monkeypatch SoakAuditor72H.audit to return DQ_PASS_ELIGIBLE
    def mock_audit(self: SoakAuditor72H) -> dict:
        return {
            "status": "DQ_PASS_ELIGIBLE",
            "audit_type": "authoritative_deep_dq",
            "errors": [],
            "blockers": [],
        }

    monkeypatch.setattr(SoakAuditor72H, "audit", mock_audit)

    # In Phase 5: return 0 if report['status'] == 'PASS' else 2
    # Because status was 'DQ_PASS_ELIGIBLE', it returned 2!
    rc = cli_main(["deep-dq-audit", "--epoch-dir", str(epoch_dir), "--report-out", str(report_out)])
    assert rc == 0, f"Expected deep-dq-audit exit code 0 for DQ_PASS_ELIGIBLE, got {rc}"


# =============================================================================
# P5: Structural audit must never qualify
# =============================================================================

def test_p5_structural_audit_must_never_qualify(tmp_path: Path) -> None:
    """P5: structural-audit output must have audit_type='structural_only' and dq-qualify MUST reject it with exit 2."""
    input_dir = tmp_path / "raw_input"
    input_dir.mkdir()
    # Create valid manifest and ndjson file
    (input_dir / "manifest.json").write_text(json.dumps({"files": ["test.ndjson"]}))
    (input_dir / "test.ndjson").write_text('{"a": 1}\n')

    struct_report_path = tmp_path / "structural_report.json"
    qual_path = tmp_path / "qualification.json"

    rc_audit = cli_main(["structural-audit", "--input-dir", str(input_dir), "--report-out", str(struct_report_path)])
    assert rc_audit == 0, f"structural-audit should pass, got {rc_audit}"

    struct_data = json.loads(struct_report_path.read_text())
    # Phase 6 Requirement: structural report must have audit_type == 'structural_only'
    assert struct_data.get("audit_type") == "structural_only", f"structural report missing audit_type='structural_only', got {struct_data.get('audit_type')}"

    # Now pass it to dq-qualify
    rc_qual = cli_main([
        "dq-qualify",
        "--audit-report", str(struct_report_path),
        "--source-manifest", str(input_dir / "manifest.json"),
        "--out", str(qual_path),
    ])
    # Phase 6 Requirement: MUST FAIL with exit 2 (STRUCTURAL_ONLY_NOT_QUALIFIABLE)
    # Phase 5 bug: accepted STRUCTURAL_AUDIT_PASS and missing audit_type
    assert rc_qual == 2, f"dq-qualify must reject structural audit with exit 2, got {rc_qual}"


# =============================================================================
# P6: Remove all fake source hash fallbacks
# =============================================================================

def test_p6_strict_phase4_constant_source_hash_removed(tmp_path: Path) -> None:
    """P6: --policy strict_phase4 fallback to fake source hash 'canonical_source_manifest' must be removed."""
    report_path = tmp_path / "deep_dq_pass.json"
    report_path.write_text(json.dumps({
        "status": "DQ_PASS_ELIGIBLE",
        "audit_type": "authoritative_deep_dq",
        "errors": [],
        "blockers": [],
    }))
    qual_path = tmp_path / "qualification.json"

    # Attempt dq-qualify without --source-manifest but with --policy strict_phase4
    rc = cli_main([
        "dq-qualify",
        "--audit-report", str(report_path),
        "--policy", "strict_phase4",
        "--out", str(qual_path),
    ])
    # Phase 6: MUST reject because source manifest is missing; no fake fallback allowed!
    assert rc == 2, f"dq-qualify must reject missing source manifest even with strict_phase4, got {rc}"


# =============================================================================
# P10: Fix CanonicalTicker constructor schema mismatch
# =============================================================================

def test_p10_canonical_ticker_constructor_has_exchange_timestamp_semantics() -> None:
    """P10: raw_record_to_canonical for ticker passes exchange_timestamp_semantics.
    
    CanonicalTicker MUST declare exchange_timestamp_semantics or handle it without TypeError.
    """
    raw_ticker_record = {
        "exchange": "bithumb",
        "stream": "ticker",
        "market": "KRW-BTC",
        "exchange_ts": "2026-09-01T00:00:00.000000+00:00",
        "local_recv_ts": "2026-09-01T00:00:00.010000+00:00",
        "local_recv_monotonic_ns": 1_000_000_000,
        "collector_run_id": "run-p10",
        "local_write_ts": "2026-09-01T00:00:00.020000+00:00",
        "payload": {
            "closing_price": "95000000",
            "acc_trade_volume_24h": "123.45",
        },
    }

    # Phase 5 bug: raises TypeError: CanonicalTicker.__init__() got an unexpected keyword argument 'exchange_timestamp_semantics'
    try:
        ticker = raw_record_to_canonical(raw_ticker_record)
        assert isinstance(ticker, CanonicalTicker)
        assert ticker.last_price == 95000000.0
        assert hasattr(ticker, "exchange_timestamp_semantics")
        assert ticker.exchange_timestamp_semantics == TimestampSemantics.EXCHANGE_PUBLICATION
    except TypeError as e:
        pytest.fail(f"Phase 5 bug reproduced: {e}")


# =============================================================================
# P11: Never fabricate receive time
# =============================================================================

def test_p11_missing_local_recv_ts_must_reject_trade_and_ticker() -> None:
    """P11: Missing local_recv_ts in trade and ticker must raise CanonicalDataValidationError, not copy exchange_ts."""
    raw_trade = {
        "exchange": "bithumb",
        "stream": "trade",
        "market": "KRW-BTC",
        "exchange_ts": "2026-09-01T00:00:00.000000+00:00",
        "local_recv_ts": None,  # Missing!
        "local_recv_monotonic_ns": 1_000_000_000,
        "collector_run_id": "run-p11",
        "local_write_ts": "2026-09-01T00:00:00.020000+00:00",
        "payload": {
            "trade_id": "T123",
            "price": "95000000",
            "units_traded": "0.5",
            "ask_bid": "BID",
        },
    }

    # Phase 5 bug: if local_recv_ms is None: local_recv_ms = exch_ts_ms (silently copied!)
    with pytest.raises(CanonicalDataValidationError, match="MISSING_LOCAL_RECEIVE_TIMESTAMP"):
        raw_record_to_canonical(raw_trade)

    raw_ticker = {
        "exchange": "bithumb",
        "stream": "ticker",
        "market": "KRW-BTC",
        "exchange_ts": "2026-09-01T00:00:00.000000+00:00",
        "local_recv_ts": None,  # Missing!
        "local_recv_monotonic_ns": 1_000_000_000,
        "collector_run_id": "run-p11",
        "local_write_ts": "2026-09-01T00:00:00.020000+00:00",
        "payload": {
            "closing_price": "95000000",
        },
    }

    with pytest.raises(CanonicalDataValidationError, match="MISSING_LOCAL_RECEIVE_TIMESTAMP"):
        raw_record_to_canonical(raw_ticker)


# =============================================================================
# P12: No silent orderbook repair
# =============================================================================

def test_p12_unsorted_or_duplicate_orderbook_must_reject_or_record_action() -> None:
    """P12: Unsorted bids (ascending instead of descending) or duplicate price levels must NOT be silently repaired."""
    unsorted_bids_record = {
        "exchange": "bithumb",
        "stream": "orderbook",
        "market": "KRW-BTC",
        "exchange_ts": "2026-09-01T00:00:00.000000+00:00",
        "local_recv_ts": "2026-09-01T00:00:00.010000+00:00",
        "local_recv_monotonic_ns": 1_000_000_000,
        "collector_run_id": "run-p12",
        "local_write_ts": "2026-09-01T00:00:00.020000+00:00",
        "payload": {
            "orderbook_units": [
                {"bid_price": "94000000", "bid_size": "1.0", "ask_price": "96000000", "ask_size": "1.0"},
                {"bid_price": "95000000", "bid_size": "1.0", "ask_price": "97000000", "ask_size": "1.0"},
            ]
        },
    }

    # Phase 5 bug: silently sorted bids descending without validation
    with pytest.raises(CanonicalDataValidationError, match="UNSORTED_BIDS|ORDERBOOK_INVARIANT_VIOLATION"):
        raw_record_to_canonical(unsorted_bids_record)


# =============================================================================
# P13: No synthetic trade ID fallback
# =============================================================================

def test_p13_missing_trade_id_must_reject_not_synthesize_timestamp_id() -> None:
    """P13: If trade payload lacks a trade ID, it must NOT synthesize f'bithumb_{exch_ts_ms}'."""
    no_id_trade = {
        "exchange": "bithumb",
        "stream": "trade",
        "market": "KRW-BTC",
        "exchange_ts": "2026-09-01T00:00:00.000000+00:00",
        "local_recv_ts": "2026-09-01T00:00:00.010000+00:00",
        "local_recv_monotonic_ns": 1_000_000_000,
        "collector_run_id": "run-p13",
        "local_write_ts": "2026-09-01T00:00:00.020000+00:00",
        "payload": {
            # No trade_id, sequential_id, or cont_no!
            "price": "95000000",
            "volume": "0.1",
            "ask_bid": "ASK",
        },
    }

    # Phase 5 bug: synthesized trade_id = f"bithumb_{exch_ts_ms}"
    with pytest.raises(CanonicalDataValidationError, match="MISSING_TRADE_ID"):
        raw_record_to_canonical(no_id_trade)


# =============================================================================
# P1.3: Missing expected hour cohort must fail
# =============================================================================

def test_p1_3_missing_expected_hour_cohort_must_fail(tmp_path: Path) -> None:
    """P1.3: If the run contract specifies 2 hours (00 and 01) but hour 01 has zero files, audit must FAIL with MISSING_EXPECTED_HOUR."""
    epoch_dir = tmp_path / "epoch_missing_hour"
    raw_dir = epoch_dir / "raw"
    raw_dir.mkdir(parents=True)
    manifests_dir = epoch_dir / "manifests"
    manifests_dir.mkdir(parents=True)

    # Write contract specifying 2 hours: 2026-09-01T00:00:00 to 2026-09-01T02:00:00
    contract_data = {
        "collector_epoch": "epoch-p1-3",
        "collector_run_id": "run-p1-3",
        "start_time_utc": "2026-09-01T00:00:00+00:00",
        "expected_end_time_utc": "2026-09-01T02:00:00+00:00",
        "duration_seconds": 7200,
        "runtime_software_commit": "abcdef123456",
        "runtime_fingerprint": "fp-123",
        "raw_schema_version": "2.0.0",
        "feed_universe": ["bithumb/KRW-BTC/orderbook"],
    }
    (epoch_dir / "epoch_contract.json").write_text(json.dumps(contract_data))

    # Only write data for hour 00
    cctx = zstandard.ZstdCompressor(level=3)
    part_dir = raw_dir / "exchange=bithumb" / "stream=orderbook" / "market=KRW-BTC"
    part_dir.mkdir(parents=True)
    rec = {
        "exchange": "bithumb",
        "stream": "orderbook",
        "market": "KRW-BTC",
        "exchange_ts": "2026-09-01T00:10:00.000000+00:00",
        "local_recv_ts": "2026-09-01T00:10:00.010000+00:00",
        "local_recv_monotonic_ns": 1_000_000_000,
        "collector_run_id": "run-p1-3",
        "local_write_ts": "2026-09-01T00:10:00.020000+00:00",
        "payload": {
            "orderbook_units": [{"bid_price": "95000000", "bid_size": "1.0", "ask_price": "96000000", "ask_size": "1.0"}]
        },
    }
    (part_dir / "part-20260901-00.zst").write_bytes(cctx.compress(json.dumps(rec).encode("utf-8") + b"\n"))

    receipts_dir = epoch_dir / "archive-receipts"
    receipts_dir.mkdir(parents=True)
    (receipts_dir / "20260901-00.archive-receipt.json").write_text(json.dumps({
        "hour_cohort": "20260901-00",
        "file_count": 1,
        "restore_verified": True,
    }))

    auditor = SoakAuditor72H(epoch_dir)
    report = auditor.audit()

    # Phase 5 bug: observed_hours only saw {"20260901-00"}, hour 01 was completely invisible!
    # Phase 6: MUST FAIL with MISSING_EXPECTED_HOUR
    blockers = " ".join(report.get("blockers", []))
    assert report["status"] == "FAIL", f"Expected FAIL for missing hour cohort, got {report['status']}"
    assert "MISSING_EXPECTED_HOUR" in blockers


# =============================================================================
# P1.4: Missing archive receipt must fail when required
# =============================================================================

def test_p1_4_missing_archive_receipt_must_fail(tmp_path: Path) -> None:
    """P1.4: If an expected cohort lacks an archive receipt, audit MUST fail with ARCHIVE_RECEIPT_MISSING."""
    epoch_dir = tmp_path / "epoch_no_receipt"
    raw_dir = epoch_dir / "raw"
    raw_dir.mkdir(parents=True)
    # No archive-receipts directory or missing receipt file
    part_dir = raw_dir / "exchange=bithumb" / "stream=orderbook" / "market=KRW-BTC"
    part_dir.mkdir(parents=True)
    cctx = zstandard.ZstdCompressor(level=3)
    rec = {
        "exchange": "bithumb",
        "stream": "orderbook",
        "market": "KRW-BTC",
        "exchange_ts": "2026-09-01T00:10:00.000000+00:00",
        "local_recv_ts": "2026-09-01T00:10:00.010000+00:00",
        "local_recv_monotonic_ns": 1_000_000_000,
        "collector_run_id": "run-p1-4",
        "local_write_ts": "2026-09-01T00:10:00.020000+00:00",
        "payload": {
            "orderbook_units": [{"bid_price": "95000000", "bid_size": "1.0", "ask_price": "96000000", "ask_size": "1.0"}]
        },
    }
    (part_dir / "part-20260901-00.zst").write_bytes(cctx.compress(json.dumps(rec).encode("utf-8") + b"\n"))

    auditor = SoakAuditor72H(epoch_dir)
    report = auditor.audit()

    # Phase 5 bug: validated receipts only IF receipts existed.
    # Phase 6: MUST FAIL with ARCHIVE_RECEIPT_MISSING
    blockers = " ".join(report.get("blockers", []))
    assert report["status"] == "FAIL"
    assert "ARCHIVE_RECEIPT_MISSING" in blockers


# =============================================================================
# P14: Transform count conservation accounting
# =============================================================================

def test_p14_transform_count_conservation_accounting(tmp_path: Path) -> None:
    """P14: Transform report must include skipped_exchange, skipped_stream, skipped_market, and satisfy conservation equations."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    out_dir = tmp_path / "canonical_out"
    out_dir.mkdir()

    raw_file = raw_dir / "mixed.jsonl"
    lines = [
        # Line 1: Target feed (bithumb KRW-BTC orderbook) -> canonicalized
        json.dumps({
            "exchange": "bithumb", "stream": "orderbook", "market": "KRW-BTC",
            "exchange_ts": "2026-09-01T00:00:00.000000+00:00", "local_recv_ts": "2026-09-01T00:00:00.010000+00:00",
            "payload": {"orderbook_units": [{"bid_price": "95000000", "bid_size": "1.0", "ask_price": "96000000", "ask_size": "1.0"}]}
        }),
        # Line 2: Wrong stream (trade) -> skipped_stream
        json.dumps({
            "exchange": "bithumb", "stream": "trade", "market": "KRW-BTC",
            "exchange_ts": "2026-09-01T00:00:00.000000+00:00", "local_recv_ts": "2026-09-01T00:00:00.010000+00:00",
            "payload": {"trade_id": "T1", "price": "95000000", "volume": "0.1", "ask_bid": "BID"}
        }),
        # Line 3: Wrong exchange (binance) -> skipped_exchange
        json.dumps({
            "exchange": "binance", "stream": "orderbook", "market": "BTCUSDT",
            "exchange_ts": "2026-09-01T00:00:00.000000+00:00", "local_recv_ts": "2026-09-01T00:00:00.010000+00:00",
            "payload": {"bids": [["95000", "1.0"]], "asks": [["96000", "1.0"]]}
        }),
        # Line 4: Blank line
        "",
    ]
    raw_file.write_text("\n".join(lines) + "\n")

    args = argparse.Namespace(
        input_dir=str(raw_dir),
        output_dir=str(out_dir),
        exchange="bithumb",
        stream="orderbook",
        schema_version="2.0.0",
    )
    rc = cmd_transform_canonical(args)
    assert rc == 0

    report = json.loads((out_dir / "transform_report.json").read_text())

    # Phase 6 Requirement: Conservation accounting
    assert "skipped_stream" in report, "Report missing skipped_stream counter"
    assert "skipped_exchange" in report, "Report missing skipped_exchange counter"
    assert report["skipped_stream"] == 1
    assert report["skipped_exchange"] == 1
    assert report["canonicalized_count"] == 1
