"""Unit tests for Python TradingSnapshot v1 structural contract validator."""

import pytest

from bithumb_coin_trader.dashboard_contract import validate_trading_snapshot


@pytest.fixture
def minimal_valid_snapshot():
    return {
        "schemaVersion": 1,
        "timestamp": "2026-09-08T09:00:00Z",
        "mode": "OFF",
        "source": {
            "kind": "local_snapshot",
            "label": "테스트 출처",
        },
        "portfolio": {
            "equity": 1000.0,
            "cash": 1000.0,
            "exposure": 0.0,
            "todayPnl": None,
            "todayReturnPct": None,
            "totalPnl": None,
            "totalReturnPct": None,
            "realizedPnl": 0.0,
            "unrealizedPnl": 0.0,
            "fees": 0.0,
        },
        "positions": [],
        "recentTrades": [],
        "equityCurve": [],
        "dailyPerformance": [],
        "botStatus": {
            "mode": "OFF",
            "strategy": None,
            "marketData": "PENDING",
            "orderExecution": "DISABLED",
            "riskGuard": "LOCKED",
            "lastActivity": None,
            "uptimeSeconds": None,
            "todayTrades": None,
            "errors": None,
        },
        "performance": {
            "return7d": None,
            "return30d": None,
            "totalReturn": None,
            "maxDrawdown": None,
            "winRate": None,
            "profitFactor": None,
            "averageTrade": None,
        },
        "today": {
            "realizedPnl": None,
            "unrealizedPnl": None,
            "fees": None,
            "trades": None,
            "wins": None,
            "losses": None,
            "exposurePct": 0.0,
        },
        "dailyBaseline": None,
    }


def test_validator_passes_minimal_valid_snapshot(minimal_valid_snapshot):
    errors = validate_trading_snapshot(minimal_valid_snapshot)
    assert errors == []


def test_validator_rejects_non_dict():
    errors = validate_trading_snapshot("not a dict")
    assert any("올바른 JSON 객체가 아닙니다" in e for e in errors)


def test_validator_rejects_invalid_schema_version(minimal_valid_snapshot):
    minimal_valid_snapshot["schemaVersion"] = 2
    errors = validate_trading_snapshot(minimal_valid_snapshot)
    assert any("지원하지 않는 스키마 버전" in e for e in errors)


def test_validator_rejects_missing_schema_version(minimal_valid_snapshot):
    del minimal_valid_snapshot["schemaVersion"]
    errors = validate_trading_snapshot(minimal_valid_snapshot)
    assert any("schemaVersion: 필드가 누락되었습니다" in e for e in errors)


def test_validator_rejects_naive_timestamp(minimal_valid_snapshot):
    minimal_valid_snapshot["timestamp"] = "2026-09-08T09:00:00"  # No timezone
    errors = validate_trading_snapshot(minimal_valid_snapshot)
    assert any("timestamp: 유효하지 않거나 누락된" in e for e in errors)


def test_validator_rejects_invalid_mode(minimal_valid_snapshot):
    minimal_valid_snapshot["mode"] = "BACKTEST"
    errors = validate_trading_snapshot(minimal_valid_snapshot)
    assert any("mode: 'OFF', 'PAPER', 'LIVE' 중 하나여야 합니다" in e for e in errors)


def test_validator_rejects_invalid_source(minimal_valid_snapshot):
    minimal_valid_snapshot["source"]["kind"] = "untrusted_network"
    errors = validate_trading_snapshot(minimal_valid_snapshot)
    assert any("source.kind:" in e for e in errors)


def test_validator_rejects_non_finite_portfolio_values(minimal_valid_snapshot):
    minimal_valid_snapshot["portfolio"]["equity"] = float("inf")
    errors = validate_trading_snapshot(minimal_valid_snapshot)
    assert any("portfolio.equity:" in e for e in errors)


def test_validator_rejects_invalid_position_side_or_timestamps(minimal_valid_snapshot):
    minimal_valid_snapshot["positions"].append({
        "id": "pos-1",
        "asset": "BTC",
        "name": "비트코인",
        "pair": "KRW-BTC",
        "side": "SHORT",  # Invalid side
        "entry": 100000.0,
        "current": 105000.0,
        "quantity": 1.0,
        "exposure": 105000.0,
        "pnl": 5000.0,
        "pnlPct": 5.0,
        "entryFee": None,
        "openedAt": "not_an_iso_ts",
        "strategy": None,
    })
    errors = validate_trading_snapshot(minimal_valid_snapshot)
    assert any("positions[0].side:" in e for e in errors)
    assert any("positions[0].openedAt:" in e for e in errors)


def test_validator_rejects_trade_opened_after_closed(minimal_valid_snapshot):
    minimal_valid_snapshot["recentTrades"].append({
        "id": "trd-1",
        "asset": "BTC",
        "pair": "KRW-BTC",
        "side": "LONG",
        "entry": 100000.0,
        "exit": 105000.0,
        "quantity": 1.0,
        "pnl": 5000.0,
        "pnlPct": 5.0,
        "fee": 100.0,
        "openedAt": "2026-09-08T10:00:00Z",
        "closedAt": "2026-09-08T09:00:00Z",  # Closed before opened!
        "exitReason": None,
    })
    errors = validate_trading_snapshot(minimal_valid_snapshot)
    assert any("진입 시각(openedAt)이 청산 시각(closedAt)보다 미래일 수 없습니다" in e for e in errors)


def test_validator_rejects_invalid_baseline_timezone(minimal_valid_snapshot):
    minimal_valid_snapshot["dailyBaseline"] = {
        "equity": 1000.0,
        "netCashFlow": 0.0,
        "tradingDay": "2026-09-08",
        "timeZone": "UTC",  # Must be Asia/Seoul
    }
    errors = validate_trading_snapshot(minimal_valid_snapshot)
    assert any("dailyBaseline.timeZone: 반드시 'Asia/Seoul'이어야 합니다" in e for e in errors)


# FIX 1: Exact wire schema version key (reject snake_case schema_version alias)
def test_validator_rejects_snake_case_schema_version(minimal_valid_snapshot):
    payload = dict(minimal_valid_snapshot)
    del payload["schemaVersion"]
    payload["schema_version"] = 1
    errors = validate_trading_snapshot(payload)
    assert any("schemaVersion: 필드가 누락되었습니다" in e for e in errors)


# FIX 2: Timestamp parity (accept Z / +09:00; reject naive timestamps on all fields)
@pytest.mark.parametrize("tz_str", ["2026-09-08T07:30:00Z", "2026-09-08T16:30:00+09:00"])
def test_validator_accepts_timezone_aware_timestamps(minimal_valid_snapshot, tz_str):
    minimal_valid_snapshot["timestamp"] = tz_str
    minimal_valid_snapshot["botStatus"]["lastActivity"] = tz_str
    minimal_valid_snapshot["positions"].append({
        "id": "pos-1", "asset": "BTC", "name": "비트코인", "pair": "KRW-BTC", "side": "LONG",
        "entry": 100000.0, "current": 105000.0, "quantity": 1.0, "exposure": 105000.0,
        "pnl": 5000.0, "pnlPct": 5.0, "entryFee": None, "openedAt": tz_str, "strategy": None,
    })
    minimal_valid_snapshot["recentTrades"].append({
        "id": "trd-1", "asset": "BTC", "pair": "KRW-BTC", "side": "LONG",
        "entry": 100000.0, "exit": 105000.0, "quantity": 1.0, "pnl": 5000.0, "pnlPct": 5.0, "fee": 100.0,
        "openedAt": tz_str, "closedAt": tz_str, "exitReason": None,
    })
    minimal_valid_snapshot["equityCurve"].append({
        "timestamp": tz_str, "equity": 10000000.0, "returnPct": 0.0, "drawdownPct": 0.0,
    })
    errors = validate_trading_snapshot(minimal_valid_snapshot)
    assert errors == []


def test_validator_rejects_naive_timestamp_across_all_fields(minimal_valid_snapshot):
    naive = "2026-09-08T16:30:00"

    # 1. snapshot.timestamp
    s = dict(minimal_valid_snapshot)
    s["timestamp"] = naive
    assert any("timestamp: 유효하지 않거나 누락된" in e for e in validate_trading_snapshot(s))

    # 2. botStatus.lastActivity
    s = dict(minimal_valid_snapshot)
    s["botStatus"] = dict(minimal_valid_snapshot["botStatus"], lastActivity=naive)
    assert any("botStatus.lastActivity:" in e for e in validate_trading_snapshot(s))

    # 3. positions[].openedAt
    s = dict(minimal_valid_snapshot)
    s["positions"] = [{
        "id": "pos-1", "asset": "BTC", "name": "비트코인", "pair": "KRW-BTC", "side": "LONG",
        "entry": 100000.0, "current": 105000.0, "quantity": 1.0, "exposure": 105000.0,
        "pnl": 5000.0, "pnlPct": 5.0, "entryFee": None, "openedAt": naive, "strategy": None,
    }]
    assert any("positions[0].openedAt:" in e for e in validate_trading_snapshot(s))

    # 4. trades[].openedAt & closedAt
    s = dict(minimal_valid_snapshot)
    s["recentTrades"] = [{
        "id": "trd-1", "asset": "BTC", "pair": "KRW-BTC", "side": "LONG",
        "entry": 100000.0, "exit": 105000.0, "quantity": 1.0, "pnl": 5000.0, "pnlPct": 5.0, "fee": 100.0,
        "openedAt": naive, "closedAt": "2026-09-08T17:00:00Z", "exitReason": None,
    }]
    assert any("recentTrades[0].openedAt:" in e for e in validate_trading_snapshot(s))

    s["recentTrades"][0]["openedAt"] = "2026-09-08T16:00:00Z"
    s["recentTrades"][0]["closedAt"] = naive
    assert any("recentTrades[0].closedAt:" in e for e in validate_trading_snapshot(s))

    # 5. equityCurve[].timestamp
    s = dict(minimal_valid_snapshot)
    s["equityCurve"] = [{"timestamp": naive, "equity": 1000.0, "returnPct": 0.0, "drawdownPct": 0.0}]
    assert any("equityCurve[0].timestamp:" in e for e in validate_trading_snapshot(s))


# FIX 3: Strict YYYY-MM-DD calendar validation
@pytest.mark.parametrize("invalid_date", ["2026-02-29", "2026-02-31", "2026-13-01", "2026-00-10"])
def test_validator_rejects_impossible_calendar_dates(minimal_valid_snapshot, invalid_date):
    # Test on dailyBaseline
    s1 = dict(minimal_valid_snapshot)
    s1["dailyBaseline"] = {
        "equity": 1000.0,
        "netCashFlow": 0.0,
        "tradingDay": invalid_date,
        "timeZone": "Asia/Seoul",
    }
    assert any("dailyBaseline.tradingDay: 유효한 YYYY-MM-DD" in e for e in validate_trading_snapshot(s1))

    # Test on dailyPerformance
    s2 = dict(minimal_valid_snapshot)
    s2["dailyPerformance"] = [{"date": invalid_date, "pnl": 100.0, "returnPct": 1.0}]
    assert any("dailyPerformance[0].date: 유효한 YYYY-MM-DD" in e for e in validate_trading_snapshot(s2))


def test_validator_accepts_valid_leap_year_date(minimal_valid_snapshot):
    s = dict(minimal_valid_snapshot)
    s["dailyBaseline"] = {
        "equity": 1000.0,
        "netCashFlow": 0.0,
        "tradingDay": "2024-02-29",
        "timeZone": "Asia/Seoul",
    }
    s["dailyPerformance"] = [{"date": "2024-02-29", "pnl": 100.0, "returnPct": 1.0}]
    errors = validate_trading_snapshot(s)
    assert errors == []
