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
