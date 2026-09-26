"""Tests for data/artifact binding verification in the hour-close canary.

Verifies:
1. Checksum match → binding_verified=True, PASS
2. Checksum mismatch → raw_terminal=False, FAIL/DEGRADED
3. DATA_PRESENT without binding → warning recorded
4. VERIFIED_ZERO with raw binding → warning
5. Receipt binding mismatch → receipts_terminal=False
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from bithumb_coin_trader.evidence_hashing import canonical_sha256
from bithumb_coin_trader.hour_close_canary import HourCloseCanary
from bithumb_coin_trader.session_evidence import FeedIdentity


COHORT = "2026-09-14_12"
NOW_ELIGIBLE = datetime(2026, 9, 14, 13, 15, 0, tzinfo=timezone.utc)
TEST_FEED = FeedIdentity(exchange="BITHUMB", stream="trade", market="KRW-BTC")
RAW_CONTENT = b'{"event": 1}\n{"event": 2}\n'
RAW_SHA256 = hashlib.sha256(RAW_CONTENT).hexdigest()
RAW_SIZE = len(RAW_CONTENT)


def _cov_json(
    feed: FeedIdentity,
    coverage_state: str = "DATA_PRESENT",
    event_count: int = 10,
    binding: dict | None = None,
) -> dict:
    cov: dict = {
        "schema_version": 1,
        "artifact_kind": "COVERAGE_EVIDENCE",
        "environment_id": "test-env",
        "collector_epoch": "epoch-1",
        "collector_run_id": "run-1",
        "runtime_commit": "test",
        "runtime_config_fingerprint": "fp-test",
        "cohort_utc": COHORT,
        "interval_start_utc": "2026-09-14T12:00:00Z",
        "interval_end_utc": "2026-09-14T13:00:00Z",
        "cohort_qualification": "QUALIFYING_FULL_HOUR",
        "observation_start_utc": "2026-09-14T12:00:00Z",
        "observation_end_utc": "2026-09-14T13:00:00Z",
        "exchange": feed.exchange,
        "stream": feed.stream,
        "market": feed.market,
        "feed_identity": feed.canonical,
        "configured": True,
        "coverage_state": coverage_state,
        "event_count": event_count if coverage_state == "DATA_PRESENT" else 0,
        "first_event_timestamp": "2026-09-14T12:05:00Z" if coverage_state == "DATA_PRESENT" else None,
        "last_event_timestamp": "2026-09-14T12:55:00Z" if coverage_state == "DATA_PRESENT" else None,
        "session_segments": [],
        "disconnect_count": 0,
        "reconnect_count": 0,
        "writer_error_count": 0,
        "queue_dropped_events": 0,
        "unpersisted_event_count": 0,
        "fatal_writer_error_type": None,
        "data_artifact_binding": binding,
        "failure_reason_codes": [],
        "closed_at_utc": "2026-09-14T13:05:00Z",
        "evidence_sha256": "",
    }
    cov["evidence_sha256"] = canonical_sha256(cov, excluded=("evidence_sha256",))
    return cov


def _raw_path(base: Path, feed: FeedIdentity) -> Path:
    dt_str, hour_str = COHORT.split("_")
    clean_market = feed.market.replace("/", "-").replace(":", "-").lower()
    return base / dt_str / feed.exchange.lower() / feed.stream.lower() / f"{feed.exchange.lower()}_{feed.stream.lower()}_{clean_market}_{dt_str}_{hour_str}.jsonl"


def _receipt_path(base: Path, feed: FeedIdentity) -> Path:
    dt_str, hour_str = COHORT.split("_")
    clean_market = feed.market.replace("/", "-").replace(":", "-").lower()
    return base / dt_str / feed.exchange.lower() / feed.stream.lower() / f"{feed.exchange.lower()}_{feed.stream.lower()}_{clean_market}_{dt_str}_{hour_str}.jsonl.archive-receipt.json"


def _cov_path(base: Path, feed: FeedIdentity) -> Path:
    return base / COHORT / feed.exchange.lower() / feed.stream.lower() / f"{feed.market}.coverage.json"


def _minimal_binding(raw_sha256: str = RAW_SHA256, raw_size: int = RAW_SIZE) -> dict:
    return {
        "raw_relative_path": "some/raw.jsonl",
        "raw_size": raw_size,
        "raw_sha256": raw_sha256,
        "manifest_relative_path": "some/manifest.json",
        "manifest_file_sha256": "a" * 64,
        "manifest_record_count": 10,
        "receipt_relative_path": "some/receipt.json",
        "receipt_file_sha256": "b" * 64,
        "receipt_source_record_count": 10,
    }


def _receipt_json(source_sha256: str = RAW_SHA256) -> dict:
    return {
        "schema_version": 3,
        "artifact_kind": "RAW_DATA",
        "state": "RESTORE_VERIFIED",
        "environment_id": "test-env",
        "run_id": "run-1",
        "collector_epoch": "epoch-1",
        "cohort": COHORT,
        "exchange": TEST_FEED.exchange,
        "stream": TEST_FEED.stream,
        "market": TEST_FEED.market,
        "source_path": "some/path",
        "source_size": RAW_SIZE,
        "source_sha256": source_sha256,
        "source_record_count": 10,
        "restore_verified_at": "2026-09-14T13:08:00Z",
    }


def _write_cov(base: Path, feed: FeedIdentity, cov_json: dict) -> Path:
    p = _cov_path(base, feed)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cov_json, indent=2), encoding="utf-8")
    return p


def _write_raw(base: Path, feed: FeedIdentity, content: bytes = RAW_CONTENT) -> Path:
    p = _raw_path(base, feed)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def _comp_path(base: Path, feed: FeedIdentity) -> Path:
    dt_str, hour_str = COHORT.split("_")
    clean_market = feed.market.replace("/", "-").replace(":", "-").lower()
    return base / dt_str / feed.exchange.lower() / feed.stream.lower() / f"{feed.exchange.lower()}_{feed.stream.lower()}_{clean_market}_{dt_str}_{hour_str}.jsonl.zst"


def _write_compressed(base: Path, feed: FeedIdentity) -> Path:
    p = _comp_path(base, feed)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x28\xb5\x2f\xfd\x00\x00\x01\x00\x00dummy")
    return p


def _cov_receipt_path(base: Path, feed: FeedIdentity) -> Path:
    return base / "coverage" / COHORT / feed.exchange.lower() / feed.stream.lower() / f"{feed.market}.coverage.json.archive-receipt.json"


def _write_coverage_receipt(base: Path, feed: FeedIdentity, receipt_json: dict) -> Path:
    p = _cov_receipt_path(base, feed)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(receipt_json, indent=2), encoding="utf-8")
    return p


def _write_receipt(base: Path, feed: FeedIdentity, receipt_json: dict) -> Path:
    p = _receipt_path(base, feed)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(receipt_json, indent=2), encoding="utf-8")
    return p


def _run_canary(tmp_path: Path, emit: bool = False):
    artifact_dir = tmp_path / "canary"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return HourCloseCanary(
        raw_root=tmp_path / "raw",
        coverage_root=tmp_path / "cov",
        compressed_root=tmp_path / "comp",
        receipt_root=tmp_path / "rec",
        artifact_dir=artifact_dir,
        feeds=[TEST_FEED],
    )


# ---------- TEST 1: Checksum match → binding_verified=True ----------

def test_binding_checksum_match(tmp_path: Path):
    """Checksum match → binding_verified=True, full PASS."""
    binding = _minimal_binding()
    cov = _cov_json(TEST_FEED, binding=binding)
    _write_cov(tmp_path / "cov", TEST_FEED, cov)
    _write_raw(tmp_path / "raw", TEST_FEED)
    _write_compressed(tmp_path / "comp", TEST_FEED)
    _write_receipt(tmp_path / "rec", TEST_FEED, _receipt_json(source_sha256=RAW_SHA256))

    canary = _run_canary(tmp_path)
    report = canary.inspect_cohort(COHORT, now=NOW_ELIGIBLE, emit_local=False, upload_s3=False)

    assert report.status == "PASS"
    fs = report.feed_statuses[0]
    assert fs.binding_verified is True
    assert fs.raw_terminal is True
    assert fs.receipts_terminal is True
    assert fs.coverage_terminal is True
    assert fs.details.get("raw_sha256_verified") is True
    assert fs.details.get("data_artifact_binding_present") is True


# ---------- TEST 2: Checksum mismatch → raw_terminal=False ----------

def test_binding_checksum_mismatch(tmp_path: Path):
    """Checksum mismatch → raw_terminal=False, report FAIL."""
    binding = _minimal_binding(raw_sha256="f" * 64)
    cov = _cov_json(TEST_FEED, binding=binding)
    _write_cov(tmp_path / "cov", TEST_FEED, cov)
    _write_raw(tmp_path / "raw", TEST_FEED)
    _write_receipt(tmp_path / "rec", TEST_FEED, _receipt_json(source_sha256=RAW_SHA256))

    canary = _run_canary(tmp_path)
    report = canary.inspect_cohort(COHORT, now=NOW_ELIGIBLE, emit_local=False, upload_s3=False)

    assert report.status == "FAIL"
    fs = report.feed_statuses[0]
    assert fs.binding_verified is False
    assert fs.raw_terminal is False
    assert fs.details.get("raw_state") == "CHECKSUM_MISMATCH"
    assert fs.details.get("raw_sha256_actual") == RAW_SHA256
    assert fs.details.get("raw_sha256_expected") == "f" * 64


# ---------- TEST 3: DATA_PRESENT without binding → warning ----------

def test_data_present_without_binding_warning(tmp_path: Path):
    """DATA_PRESENT coverage with no binding records a warning."""
    cov = _cov_json(TEST_FEED, binding=None)
    _write_cov(tmp_path / "cov", TEST_FEED, cov)
    _write_raw(tmp_path / "raw", TEST_FEED)
    _write_compressed(tmp_path / "comp", TEST_FEED)
    _write_receipt(tmp_path / "rec", TEST_FEED, _receipt_json())

    canary = _run_canary(tmp_path)
    report = canary.inspect_cohort(COHORT, now=NOW_ELIGIBLE, emit_local=False, upload_s3=False)

    assert report.status == "PASS"
    fs = report.feed_statuses[0]
    assert fs.binding_verified is False
    assert fs.details.get("data_artifact_binding_present") is False
    assert fs.details.get("binding_warning") == "DATA_PRESENT_WITHOUT_BINDING"


# ---------- TEST 4: VERIFIED_ZERO with raw binding → warning ----------

def test_verified_zero_with_raw_binding_warning(tmp_path: Path):
    """VERIFIED_ZERO coverage that claims raw data exists records a warning."""
    binding = _minimal_binding(raw_size=1024)
    cov = _cov_json(TEST_FEED, coverage_state="VERIFIED_ZERO_EVENT", event_count=0, binding=binding)
    _write_cov(tmp_path / "cov", TEST_FEED, cov)
    # Coverage receipt needed so receipts_terminal is True (verdict = PASS)
    cov_receipt = _receipt_json()
    cov_receipt["artifact_kind"] = "COVERAGE_EVIDENCE"
    _write_coverage_receipt(tmp_path / "rec", TEST_FEED, cov_receipt)

    canary = _run_canary(tmp_path)
    report = canary.inspect_cohort(COHORT, now=NOW_ELIGIBLE, emit_local=False, upload_s3=False)

    assert report.status == "PASS"
    fs = report.feed_statuses[0]
    assert fs.is_verified_zero is True
    assert fs.details.get("binding_warning") == "VERIFIED_ZERO_WITH_RAW_BINDING"
    # All terminals true despite warning; binding warning is advisory
    assert fs.raw_terminal is True
    assert fs.coverage_terminal is True
    assert fs.receipts_terminal is True


# ---------- TEST 5: Receipt binding mismatch → receipts_terminal=False ----------

def test_receipt_binding_mismatch(tmp_path: Path):
    """Receipt source_sha256 differs from coverage binding → receipts_terminal=False."""
    binding = _minimal_binding(raw_sha256=RAW_SHA256)
    cov = _cov_json(TEST_FEED, binding=binding)
    _write_cov(tmp_path / "cov", TEST_FEED, cov)
    _write_raw(tmp_path / "raw", TEST_FEED)
    _write_compressed(tmp_path / "comp", TEST_FEED)
    _write_receipt(tmp_path / "rec", TEST_FEED, _receipt_json(source_sha256="a" * 64))

    canary = _run_canary(tmp_path)
    report = canary.inspect_cohort(COHORT, now=NOW_ELIGIBLE, emit_local=False, upload_s3=False)

    assert report.status == "DEGRADED"
    fs = report.feed_statuses[0]
    assert fs.binding_verified is True  # raw checksum was verified
    assert fs.raw_terminal is True
    assert fs.receipts_terminal is False
    assert fs.details.get("receipt_binding_mismatch") is True
