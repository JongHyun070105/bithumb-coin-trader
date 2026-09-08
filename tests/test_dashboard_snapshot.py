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
            "lastActivity": "2026-09-08T08:00:00Z",
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

    positions = {p["pair"]: p for p in snapshot["positions"]}
    assert len(positions) == 2
    assert positions["KRW-BTC"]["quantity"] == 0.05
    assert positions["KRW-BTC"]["exposure"] == 5250000.0
    assert positions["KRW-ETH"]["quantity"] == 2.0
    assert positions["KRW-ETH"]["exposure"] == 8400000.0

    # P2: entryFee must be null (None) because FillLedger tracks cumulative market fees
    assert positions["KRW-BTC"]["entryFee"] is None
    assert positions["KRW-ETH"]["entryFee"] is None

    portfolio = snapshot["portfolio"]
    assert portfolio["cash"] == 5000000.0
    assert portfolio["exposure"] == 13650000.0
    assert portfolio["equity"] == 18650000.0
    assert portfolio["equity"] == portfolio["cash"] + portfolio["exposure"]
    assert portfolio["realizedPnl"] == 494750.0
    assert portfolio["unrealizedPnl"] == 643500.0
    assert portfolio["fees"] == 11750.0
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
    pairs = [p["pair"] for p in snapshot["positions"]]
    assert "KRW-ETH" not in pairs
    assert pairs == ["KRW-BTC"]


def test_missing_mark_price_raises(temp_env):
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


# P3: Fail-closed bot status defaults test
def test_fail_closed_bot_status_defaults(temp_env):
    with temp_env["account"].open("r", encoding="utf-8") as f:
        data = json.load(f)
    # Remove botStatus entirely
    del data["botStatus"]
    with temp_env["account"].open("w", encoding="utf-8") as f:
        json.dump(data, f)

    snapshot = build_trading_snapshot(
        ledger_path=temp_env["ledger"],
        account_state_path=temp_env["account"],
        mark_prices_path=temp_env["marks"],
    )
    bot = snapshot["botStatus"]
    assert bot["marketData"] == "PENDING"
    assert bot["orderExecution"] == "DISABLED"
    assert bot["riskGuard"] == "LOCKED"
    assert bot["lastActivity"] is None
    assert bot["uptimeSeconds"] is None
    assert bot["todayTrades"] is None
    assert bot["errors"] is None


# P4: Mark price timestamp freshness tests
def test_mark_price_timestamp_freshness_rule(temp_env):
    # Account timestamp = 08:00:00Z, Mark timestamp = 06:30:00Z (stale marks)
    with temp_env["marks"].open("r", encoding="utf-8") as f:
        marks_data = json.load(f)
    marks_data["timestamp"] = "2026-09-08T06:30:00Z"
    with temp_env["marks"].open("w", encoding="utf-8") as f:
        json.dump(marks_data, f)

    snapshot = build_trading_snapshot(
        ledger_path=temp_env["ledger"],
        account_state_path=temp_env["account"],
        mark_prices_path=temp_env["marks"],
    )
    # Snapshot timestamp must reflect earlier mark timestamp
    assert snapshot["timestamp"] == "2026-09-08T06:30:00Z"


def test_mark_price_missing_or_naive_timestamp_rejected(temp_env):
    # 1. Missing timestamp
    with temp_env["marks"].open("w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "markets": {"KRW-BTC": 105000000}}, f)
    with pytest.raises(SnapshotBuilderError, match="mark_prices must contain a timezone-aware timestamp"):
        build_trading_snapshot(temp_env["ledger"], temp_env["account"], temp_env["marks"])

    # 2. Naive timestamp (no timezone offset)
    with temp_env["marks"].open("w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "timestamp": "2026-09-08T08:00:00", "markets": {"KRW-BTC": 105000000}}, f)
    with pytest.raises(SnapshotBuilderError, match="must be timezone-aware"):
        build_trading_snapshot(temp_env["ledger"], temp_env["account"], temp_env["marks"])


# P5 / FIX 4: Equity history performance block is ignored, metrics derived from curve
def test_equity_history_cannot_override_accounting_truth(temp_env):
    with temp_env["history"].open("w", encoding="utf-8") as f:
        json.dump({
            "schema_version": 1,
            "performance": {
                "totalReturn": 99.9,
                "winRate": 0.85,
                "profitFactor": 2.5,
                "averageTrade": 50000,
                "return7d": 999.0,
                "return30d": 999.0,
                "maxDrawdown": -99.0,
            },
            "equityCurve": [{"timestamp": "2026-09-08T08:00:00Z", "equity": 18650000, "returnPct": 0.0, "drawdownPct": 0.0}],
            "dailyPerformance": [{"date": "2026-09-08", "pnl": 650000, "returnPct": 3.61}],
        }, f)

    snapshot = build_trading_snapshot(
        ledger_path=temp_env["ledger"],
        account_state_path=temp_env["account"],
        mark_prices_path=temp_env["marks"],
        equity_history_path=temp_env["history"],
    )
    perf = snapshot["performance"]
    # Total return must be the accounting truth from account state, NOT 99.9!
    assert perf["totalReturn"] == snapshot["portfolio"]["totalReturnPct"]
    # Trade-based metrics must remain null because there are no closed round-trip trades
    assert perf["winRate"] is None
    assert perf["profitFactor"] is None
    assert perf["averageTrade"] is None
    # Precomputed return7d, return30d, maxDrawdown are completely ignored
    # Since equityCurve only has 1 observation at 08:00Z, return7d is None and maxDrawdown is 0.0
    assert perf["return7d"] is None
    assert perf["return30d"] is None
    assert perf["maxDrawdown"] == 0.0


def test_performance_derived_deterministically_from_equity_curve(temp_env):
    # Curve spanning 8 days with a drawdown
    # Day 0 (8 days ago): 10,000,000
    # Day 4 (4 days ago): 12,000,000 (peak)
    # Day 6 (2 days ago): 9,600,000 (drawdown from 12M: -20%)
    # Day 8 (today): 18,650,000 (account equity from ledger+marks: 8.15M cash + 0.1 BTC @ 105M = 18.65M)
    with temp_env["history"].open("w", encoding="utf-8") as f:
        json.dump({
            "schema_version": 1,
            "performance": {
                "return7d": -999.9,  # Fake precomputed value
                "return30d": 999.9,  # Fake precomputed value
                "maxDrawdown": -99.9,  # Fake precomputed value
            },
            "equityCurve": [
                {"timestamp": "2026-08-31T08:00:00Z", "equity": 10000000, "returnPct": 0.0, "drawdownPct": 0.0},
                {"timestamp": "2026-09-04T08:00:00Z", "equity": 12000000, "returnPct": 20.0, "drawdownPct": 0.0},
                {"timestamp": "2026-09-06T08:00:00Z", "equity": 9600000, "returnPct": -4.0, "drawdownPct": -20.0},
                {"timestamp": "2026-09-08T08:00:00Z", "equity": 18650000, "returnPct": 86.5, "drawdownPct": 0.0},
            ],
            "dailyPerformance": [],
        }, f)

    snapshot = build_trading_snapshot(
        ledger_path=temp_env["ledger"],
        account_state_path=temp_env["account"],
        mark_prices_path=temp_env["marks"],
        equity_history_path=temp_env["history"],
    )
    perf = snapshot["performance"]
    # return7d: 8 days ago observation (10M) is at/before 7d horizon (2026-09-01T08:00:00Z)
    # Return = (18.65M - 10M) / 10M * 100 = 86.5%
    assert perf["return7d"] == pytest.approx(86.5)
    # return30d: No observation at or before 30d horizon -> None
    assert perf["return30d"] is None
    # maxDrawdown: Peak 12M -> Trough 9.6M = -20.0%
    assert perf["maxDrawdown"] == pytest.approx(-20.0)


# FIX 5: Opened position without reconstructable cycle openedAt must fail-closed
def test_open_position_without_reconstructable_cycle_opened_at_fails_closed(temp_env, monkeypatch):
    # Add KRW-SOL to mark prices so missing mark price error is not raised
    with temp_env["marks"].open("r", encoding="utf-8") as f:
        m_data = json.load(f)
    m_data["markets"]["KRW-SOL"] = 200000
    with temp_env["marks"].open("w", encoding="utf-8") as f:
        json.dump(m_data, f)

    # Simulate internal inconsistency: FillLedger reports an open position in KRW-SOL,
    # but the ledger record replay does not have a buy fill to establish openedAt for KRW-SOL.
    from bithumb_coin_trader.fill_ledger import PositionSnapshot
    real_positions = FillLedger.positions

    def fake_positions(self):
        pos = dict(real_positions(self))
        pos["KRW-SOL"] = PositionSnapshot(
            market="KRW-SOL",
            volume=Decimal("1.0"),
            cost_basis=Decimal("200000"),
            average_cost=Decimal("200000"),
        )
        return pos

    monkeypatch.setattr(FillLedger, "positions", fake_positions)
    with pytest.raises(SnapshotBuilderError, match="오픈 포지션 진입 시각 재구성 실패"):
        build_trading_snapshot(
            ledger_path=temp_env["ledger"],
            account_state_path=temp_env["account"],
            mark_prices_path=temp_env["marks"],
        )


# P6: Reopened position openedAt semantics test
def test_reopened_position_cycle_opened_at(tmp_path: Path):
    ledger_file = tmp_path / "fills_reopen.jsonl"
    account_file = tmp_path / "account_state.json"
    marks_file = tmp_path / "mark_prices.json"

    ledger = FillLedger(ledger_file)
    # 1. First cycle: Buy 1.0 BTC at T1 (01:00Z)
    ledger.append_order({
        "order_id": "ord-cycle1-buy",
        "market": "KRW-BTC",
        "side": "bid",
        "paid_fee": "5000",
        "trades": [{
            "trade_id": "trd-c1-buy",
            "price": "100000000",
            "volume": "1.0",
            "funds": "100000000",
            "paid_fee": "5000",
            "created_at": "2026-09-08T01:00:00Z",
        }],
    })
    # 2. Close cycle: Sell 1.0 BTC at T2 (02:00Z) -> volume = 0
    ledger.append_order({
        "order_id": "ord-cycle1-sell",
        "market": "KRW-BTC",
        "side": "ask",
        "paid_fee": "5000",
        "trades": [{
            "trade_id": "trd-c1-sell",
            "price": "105000000",
            "volume": "1.0",
            "funds": "105000000",
            "paid_fee": "5000",
            "created_at": "2026-09-08T02:00:00Z",
        }],
    })
    # 3. Second cycle (Reopen): Buy 0.5 BTC at T3 (04:00Z)
    ledger.append_order({
        "order_id": "ord-cycle2-buy",
        "market": "KRW-BTC",
        "side": "bid",
        "paid_fee": "2500",
        "trades": [{
            "trade_id": "trd-c2-buy",
            "price": "110000000",
            "volume": "0.5",
            "funds": "55000000",
            "paid_fee": "2500",
            "created_at": "2026-09-08T04:00:00Z",
        }],
    })

    with account_file.open("w", encoding="utf-8") as f:
        json.dump({
            "schema_version": 1,
            "timestamp": "2026-09-08T08:00:00Z",
            "mode": "OFF",
            "cash_krw": 50000000,
        }, f)

    with marks_file.open("w", encoding="utf-8") as f:
        json.dump({
            "schema_version": 1,
            "timestamp": "2026-09-08T08:00:00Z",
            "markets": {"KRW-BTC": 115000000},
        }, f)

    snapshot = build_trading_snapshot(ledger_file, account_file, marks_file)
    assert len(snapshot["positions"]) == 1
    btc_pos = snapshot["positions"][0]
    assert btc_pos["pair"] == "KRW-BTC"
    assert btc_pos["quantity"] == 0.5
    # Must be T3 (2026-09-08T04:00:00Z), NOT T1 (01:00:00Z) from the previous closed cycle!
    assert btc_pos["openedAt"] == "2026-09-08T04:00:00Z"
