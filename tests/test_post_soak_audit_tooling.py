"""Comprehensive Unit Tests for Post-Soak Data Quality and Integrity Tooling."""

import hashlib
import json
from pathlib import Path
import pytest
import zstandard as zstd

from scripts.audit_72h_soak import SoakAuditor72H, TimestampStats, parse_partition_path
from scripts.verify_soak_reproducibility import (
    decompress_and_hash_zstd,
    hash_stream,
    verify_cohort,
)


def test_parse_partition_path():
    p = "raw/bithumb/orderbook/KRW-BTC/20260905_12.jsonl"
    parsed = parse_partition_path(p)
    assert parsed == ("bithumb", "orderbook", "KRW-BTC", "20260905_12")

    p_binance = "raw/binance/depth/BTCUSDT/20260905_12.jsonl"
    parsed_b = parse_partition_path(p_binance)
    assert parsed_b == ("binance", "depth", "BTCUSDT", "20260905_12")

    assert parse_partition_path("invalid/path.jsonl") is None


def test_timestamp_stats_monotonicity_and_offset():
    stats = TimestampStats()
    stats.total_records = 3
    stats.exchange_ts_count = 3
    stats.wall_ts_count = 3
    stats.monotonic_ts_count = 3
    stats.monotonic_reversals = 0
    stats.offsets_ms = [5.0, 10.0, 15.0]

    s = stats.summary()
    assert s["total_records"] == 3
    assert s["monotonic_reversals"] == 0
    assert s["offset_p50_ms"] == 10.0
    assert s["offset_max_ms"] == 15.0


def test_audit_empty_epoch_handles_cleanly(tmp_path: Path):
    epoch_dir = tmp_path / "test_epoch"
    epoch_dir.mkdir()
    auditor = SoakAuditor72H(epoch_dir)
    res = auditor.audit()

    assert res["status"] == "PASS"
    assert res["summary"]["raw_files_count"] == 0
    assert res["summary"]["manifests_count"] == 0


def test_reproducibility_hash_stream_and_zstd(tmp_path: Path):
    content = b'{"t": 1000, "price": 100000}\n{"t": 1001, "price": 100001}\n'
    raw_file = tmp_path / "sample.jsonl"
    raw_file.write_bytes(content)

    sha_raw, bytes_raw, lines_raw = hash_stream(raw_file)
    assert sha_raw == hashlib.sha256(content).hexdigest()
    assert bytes_raw == len(content)
    assert lines_raw == 2

    # Compress with Zstandard
    zst_file = tmp_path / "sample.jsonl.zst"
    cctx = zstd.ZstdCompressor()
    zst_file.write_bytes(cctx.compress(content))

    sha_zst, bytes_zst, lines_zst = decompress_and_hash_zstd(zst_file)
    assert sha_zst == sha_raw
    assert bytes_zst == bytes_raw
    assert lines_zst == lines_raw


def test_cohort_verification_pass_and_mismatch(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    comp_dir = tmp_path / "compressed"
    raw_dir.mkdir()
    comp_dir.mkdir()

    raw_file = raw_dir / "feed.jsonl"
    content = b'{"msg": "data"}\n'
    raw_file.write_bytes(content)

    zst_file = comp_dir / "feed.jsonl.zst"
    cctx = zstd.ZstdCompressor()
    zst_file.write_bytes(cctx.compress(content))

    # Matching case
    report = verify_cohort(raw_dir, comp_dir)
    assert report["all_match"] is True
    assert report["verified_files"] == 1
    assert report["details"][0]["status"] == "PASS"

    # Corrupt zst case
    zst_file.write_bytes(cctx.compress(b'{"msg": "tampered"}\n'))
    report_tampered = verify_cohort(raw_dir, comp_dir)
    assert report_tampered["all_match"] is False
    assert report_tampered["details"][0]["status"] == "MISMATCH"
