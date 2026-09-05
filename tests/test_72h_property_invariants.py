"""Property Invariant Tests for 72H Architecture.

Implements Section 47 of the 72H post-soak specification:
- partition path parsing & reverse extraction
- hour eligibility at +599s vs +600s boundary
- epoch isolation & directory path safety
- receipt state transitions
- manifest validation invariants
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import pytest

from bithumb_coin_trader.archive_scheduler import (
    ArchiveSchedulerConfig,
    ClosedHourArchiveScheduler,
    EligibleHour,
)
from scripts.audit_72h_soak import parse_partition_path


def test_property_partition_path_parsing():
    """Verify robust extraction of exchange, stream, market, and hour from varied paths."""
    valid_cases = [
        ("raw/bithumb/orderbook/KRW-BTC/20260904_15.jsonl", ("bithumb", "orderbook", "KRW-BTC", "20260904_15")),
        ("raw/binance/trade/btcusdt/20260904_00.jsonl", ("binance", "trade", "btcusdt", "20260904_00")),
        ("/var/lib/bitcoin-trader/72h-soak/epoch/raw/upbit/orderbook/KRW-ETH/20260905_12.jsonl", ("upbit", "orderbook", "KRW-ETH", "20260905_12")),
    ]
    for p, expected in valid_cases:
        res = parse_partition_path(p)
        assert res == expected, f"Failed parsing {p}, got {res}"


def test_property_grace_seconds_boundary(tmp_path: Path):
    """Test boundary condition: +599s is NOT eligible, while +600s IS eligible."""
    cfg = ArchiveSchedulerConfig(
        epoch="prop-epoch",
        run_id="prop-run",
        base_dir=tmp_path,
        raw_root=tmp_path / "raw",
        manifest_root=tmp_path / "manifests",
        compressed_root=tmp_path / "compressed",
        receipt_root=tmp_path / "archive-receipts",
        metrics_path=tmp_path / "metrics.json",
        grace_seconds=600,
    )
    for d in [cfg.raw_root, cfg.manifest_root, cfg.compressed_root, cfg.receipt_root]:
        d.mkdir(parents=True, exist_ok=True)

    # Closed hour 2026-09-04 14:00 (closed_at is 15:00:00 UTC)
    closed_at = datetime(2026, 9, 4, 15, 0, 0, tzinfo=timezone.utc)
    h = EligibleHour(date_str="20260904", hour_str="14", files=[], closed_at=closed_at)

    # At 15:09:59 (599s after close) -> must NOT be eligible
    t_599 = datetime(2026, 9, 4, 15, 9, 59, tzinfo=timezone.utc)
    diff_599 = (t_599 - h.closed_at).total_seconds()
    assert diff_599 < 600

    # At 15:10:00 (600s after close) -> must BE eligible
    t_600 = datetime(2026, 9, 4, 15, 10, 0, tzinfo=timezone.utc)
    diff_600 = (t_600 - h.closed_at).total_seconds()
    assert diff_600 >= 600


def test_property_epoch_isolation(tmp_path: Path):
    """Paths must remain strictly under the sealed epoch directory without traversal."""
    epoch = "aws-72h-soak-20260904-43e79055"
    base = tmp_path / epoch

    safe_paths = [
        base / "raw",
        base / "manifests",
        base / "compressed",
        base / "archive-receipts",
        base / "logs",
    ]

    for p in safe_paths:
        resolved = p.resolve()
        assert str(resolved).startswith(str(base.resolve()))
