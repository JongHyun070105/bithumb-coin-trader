"""Tests for offline FillLedger -> TradingSnapshot v1 builder."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from bithumb_coin_trader.dashboard_snapshot import (
    SnapshotBuilderError,
    build_trading_snapshot,
)
from bithumb_coin_trader.fill_ledger import FillLedger


@pytest.fixture
def temp_env(tmp_path: Path):
    ledger_file = tmp_path / "fills.jsonl"
    account_file = tmp_path / "account_state.json"
    marks_file = tmp_path / "mark_prices.json"
    history_file = tmp_path / "equity_history.json"

    # Seed FillLedger
    ledger = FillLedger(ledger_file)
    # Buy 0.1 BTC @ 100M KRW, fee 5,000 KRW
    ledger.append_order({
        "order_id": "ord-1",
        "market": "KRW-BTC",
        "side": "bid",
        "paid_fee": "5000",
        "trades": [{
            "trade_id": "trd-1",
            "price": "100000000",
            "volume": "0.1",
            "funds": "10000000",
            "paid_fee": "5000",
            "created_at": "2026-09-08T00:30:00Z",
        }],
    })
    # Buy 2.0 ETH @ 4M KRW, fee 4,000 KRW
    ledger.append_order({
        "order_id": "ord-2",
        "market": "KRW-ETH",
        "side": "bid",
        "paid_fee": "4000",
        "trades": [{
            "trade_id": "trd-2",
            "price": "4000000",
            "volume": "2.0",
            "funds": "8000000",
            "paid_fee": "4000",
            "created_at": "2026-09-08T01:00:00Z",
        }],
    })
    # Partial sell 0.05 BTC @ 110M KRW, fee 2,750 KRW
    ledger.append_order({
        "order_id": "ord-3",
        "market": "KRW-BTC",
        "side": "ask",
        "paid_fee": "2750",
        "trades": [{
            "trade_id": "trd-3",
            "price": "110000000",
            "volume": "0.05",
            "funds": "5500000",
            "paid_fee": "2750",
            "created_at": "2026-09-08T02:00:00Z",
        }],
    })

    account_data = {
        "schema_version": 1,
        "timestamp": "2026-09-08T08:00:00Z",
        "mode": "OFF",
        "cash_krw": 5000000,
        "starting_equity_krw": 20000000,
        "strategy": "Offline-Strategy-A",
        "daily_baseline": {
            "equity": 18000000,
            "netCashFlow": 0,
            "tradingDay": "2026-09-08",
            "timeZone": "Asia/Seoul",
        },
        "botStatus": {
            "mode": "OFF",
            "marketData": "READY",
            "orderExecution": "DISABLED",
            "riskGuard": "ACTIVE",
            "uptimeSeconds": 3600,
            "todayTrades": 0,
            "errors": 0,
        },
    }
    with account_file.open("w", encoding="utf-8") as f:
        json.dump(account_data, f)

    marks_data = {
        "schema_version": 1,
        "timestamp": "2026-09-08T08:00:00Z",
        "markets": {
            "KRW-BTC": 105000000,
            "KRW-ETH": 4200000,
        },
    }
    with marks_file.open("w", encoding="utf-8") as f:
        json.dump(marks_data, f)

    return {
        "ledger": ledger_file,
        "account": account_file,
        "marks": marks_file,
        "history": history_file,
    }


def test_build_trading_snapshot_success(temp_env):
    snapshot = build_trading_snapshot(
        ledger_path=temp_env["ledger"],
        account_state_path=temp_env["account"],
        mark_prices_path=temp_env["marks"],
    )

    # Top-level invariants
    assert snapshot["schemaVersion"] == 1
    assert snapshot["mode"] == "OFF"
    assert snapshot["source"]["kind"] == "local_snapshot"
    assert snapshot["source"]["label"] == "오프라인 FillLedger 스냅샷"
    assert snapshot["recentTrades"] == []

    # Positions:
    # Remaining BTC: 0.05 BTC. Mark = 105M. Exposure = 5,250,000 KRW
    # Remaining ETH: 2.0 ETH. Mark = 4.2M. Exposure = 8,400,000 KRW
    # Total exposure = 13,650,000 KRW
    # Cash = 5,000,000 KRW
    # Total equity = 18,650,000 KRW
    positions = {p["pair"]: p for p in snapshot["positions"]}
    assert len(positions) == 2
    assert positions["KRW-BTC"]["quantity"] == 0.05
    assert positions["KRW-BTC"]["exposure"] == 5250000.0
    assert positions["KRW-ETH"]["quantity"] == 2.0
    assert positions["KRW-ETH"]["exposure"] == 8400000.0

    portfolio = snapshot["portfolio"]
    assert portfolio["cash"] == 5000000.0
    assert portfolio["exposure"] == 13650000.0
    assert portfolio["equity"] == 18650000.0
    assert portfolio["equity"] == portfolio["cash"] + portfolio["exposure"]

    # Realized PnL from partial sell of BTC:
    # Bought 0.1 BTC for 10M + 5k fee = 10,005,000 cost basis.
    # Sold 0.05 BTC (half) for 5.5M - 2750 fee = 5,497,250 net funds.
    # Allocated cost = 10,005,000 * 0.5 = 5,002,500.
    # Realized = 5,497,250 - 5,002,500 = 494,750 KRW.
    assert portfolio["realizedPnl"] == 494750.0

    # Unrealized PnL:
    # BTC: exposure 5,250,000 - remaining cost basis 5,002,500 = 247,500 KRW.
    # ETH: exposure 8,400,000 - cost basis 8,004,000 = 396,000 KRW.
    # Total unrealized = 247,500 + 396,000 = 643,500 KRW.
    assert portfolio["unrealizedPnl"] == 643500.0
    assert positions["KRW-BTC"]["pnl"] == 247500.0
    assert positions["KRW-ETH"]["pnl"] == 396000.0

    # Total fees: 5,000 + 4,000 + 2,750 = 11,750 KRW
    assert portfolio["fees"] == 11750.0

    # Today metrics from daily baseline:
    # equity 18,650,000 - baseline 18,000,000 - cashflow 0 = 650,000 KRW.
    assert portfolio["todayPnl"] == 650000.0
    assert portfolio["todayReturnPct"] == pytest.approx(650000.0 / 18000000.0 * 100)


def test_zero_position_exclusion(temp_env):
    # Fully close ETH position
    ledger = FillLedger(temp_env["ledger"])
    ledger.append_order({
        "order_id": "ord-4",
        "market": "KRW-ETH",
        "side": "ask",
        "paid_fee": "4000",
        "trades": [{
            "trade_id": "trd-4",
            "price": "4000000",
            "volume": "2.0",
            "funds": "8000000",
            "paid_fee": "4000",
            "created_at": "2026-09-08T03:00:00Z",
        }],
    })
    snapshot = build_trading_snapshot(
        ledger_path=temp_env["ledger"],
        account_state_path=temp_env["account"],
        mark_prices_path=temp_env["marks"],
    )
    # Only BTC position remains
    pairs = [p["pair"] for p in snapshot["positions"]]
    assert "KRW-ETH" not in pairs
    assert pairs == ["KRW-BTC"]


def test_missing_mark_price_raises(temp_env):
    # Remove KRW-BTC from mark prices
    with temp_env["marks"].open("w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "timestamp": "2026-09-08T08:00:00Z", "markets": {"KRW-ETH": 4200000}}, f)

    with pytest.raises(SnapshotBuilderError, match="missing mark price for open position"):
        build_trading_snapshot(
            ledger_path=temp_env["ledger"],
            account_state_path=temp_env["account"],
            mark_prices_path=temp_env["marks"],
        )


def test_missing_account_state_raises(temp_env):
    with pytest.raises(SnapshotBuilderError, match="account state file not found"):
        build_trading_snapshot(
            ledger_path=temp_env["ledger"],
            account_state_path=temp_env["account"].with_name("nonexistent.json"),
            mark_prices_path=temp_env["marks"],
        )


def test_invalid_json_raises(temp_env):
    with temp_env["account"].open("w", encoding="utf-8") as f:
        f.write("{ invalid json")

    with pytest.raises(SnapshotBuilderError, match="invalid JSON"):
        build_trading_snapshot(
            ledger_path=temp_env["ledger"],
            account_state_path=temp_env["account"],
            mark_prices_path=temp_env["marks"],
        )


def test_unsupported_schema_version_raises(temp_env):
    with temp_env["account"].open("w", encoding="utf-8") as f:
        json.dump({"schema_version": 999, "timestamp": "2026-09-08T08:00:00Z"}, f)

    with pytest.raises(SnapshotBuilderError, match="unsupported account state schema version"):
        build_trading_snapshot(
            ledger_path=temp_env["ledger"],
            account_state_path=temp_env["account"],
            mark_prices_path=temp_env["marks"],
        )


def test_missing_daily_baseline_yields_null_today_metrics(temp_env):
    # Remove daily_baseline
    with temp_env["account"].open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["daily_baseline"] = None
    with temp_env["account"].open("w", encoding="utf-8") as f:
        json.dump(data, f)

    snapshot = build_trading_snapshot(
        ledger_path=temp_env["ledger"],
        account_state_path=temp_env["account"],
        mark_prices_path=temp_env["marks"],
    )
    assert snapshot["portfolio"]["todayPnl"] is None
    assert snapshot["portfolio"]["todayReturnPct"] is None
    assert snapshot["dailyBaseline"] is None


def test_missing_equity_history_yields_null_performance_metrics(temp_env):
    snapshot = build_trading_snapshot(
        ledger_path=temp_env["ledger"],
        account_state_path=temp_env["account"],
        mark_prices_path=temp_env["marks"],
        equity_history_path=None,
    )
    perf = snapshot["performance"]
    assert perf["return7d"] is None
    assert perf["return30d"] is None
    assert perf["maxDrawdown"] is None
    assert perf["winRate"] is None
    assert perf["profitFactor"] is None
    assert perf["averageTrade"] is None
    assert snapshot["equityCurve"] == []
    assert snapshot["dailyPerformance"] == []
