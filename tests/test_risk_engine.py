import pytest
import math
from bithumb_coin_trader.canonical_market_data import CanonicalOrderBook
from bithumb_coin_trader.risk_engine import (
    RiskEngine,
    RiskEngineConfig,
    RiskVerdict,
)


@pytest.fixture
def clean_book():
    return CanonicalOrderBook(
        exchange="bithumb",
        market="KRW-BTC",
        exchange_timestamp_ms=1000,
        receive_timestamp_ms=1000,
        bids=((100_000_000.0, 1.0),),
        asks=((100_050_000.0, 1.0),),  # 5 bps spread
    )


def test_normal_order_allow(clean_book):
    engine = RiskEngine()
    verdict, reasons, audit = engine.evaluate_preflight(
        order_id="ord_1",
        side="BUY",
        requested_notional_krw=1_000_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=0.0,
        daily_loss_fraction=0.01,
        orderbook=clean_book,
        current_time_ms=1050,
    )
    assert verdict == RiskVerdict.ALLOW
    assert not reasons
    assert audit.verdict == RiskVerdict.ALLOW
    assert len(audit.context_hash) == 64


def test_stale_market_data_reject(clean_book):
    engine = RiskEngine(RiskEngineConfig(max_data_age_ms=1000.0))
    verdict, reasons, _ = engine.evaluate_preflight(
        order_id="ord_2",
        side="BUY",
        requested_notional_krw=1_000_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=0.0,
        daily_loss_fraction=0.01,
        orderbook=clean_book,
        current_time_ms=3000,  # 2000ms age > 1000ms limit
    )
    assert verdict == RiskVerdict.REJECT
    assert any("stale" in r for r in reasons)


def test_wide_spread_reject():
    wide_book = CanonicalOrderBook(
        exchange="bithumb",
        market="KRW-BTC",
        exchange_timestamp_ms=1000,
        receive_timestamp_ms=1000,
        bids=((100_000_000.0, 1.0),),
        asks=((101_000_000.0, 1.0),),  # 100 bps spread > 50 bps limit
    )
    engine = RiskEngine(RiskEngineConfig(max_spread_bps=50.0))
    verdict, reasons, _ = engine.evaluate_preflight(
        order_id="ord_3",
        side="BUY",
        requested_notional_krw=1_000_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=0.0,
        daily_loss_fraction=0.01,
        orderbook=wide_book,
        current_time_ms=1050,
    )
    assert verdict == RiskVerdict.REJECT
    assert any("Spread" in r for r in reasons)


def test_daily_drawdown_circuit_breaker_halt(clean_book):
    engine = RiskEngine(RiskEngineConfig(max_daily_loss_fraction=0.05))
    verdict, reasons, _ = engine.evaluate_preflight(
        order_id="ord_4",
        side="BUY",
        requested_notional_krw=1_000_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=0.0,
        daily_loss_fraction=0.06,  # 6% loss >= 5% limit
        orderbook=clean_book,
        current_time_ms=1050,
    )
    assert verdict == RiskVerdict.HALT
    assert engine.halted is True

    # Subsequent orders must immediately receive HALT
    verdict2, _, _ = engine.evaluate_preflight(
        order_id="ord_5",
        side="BUY",
        requested_notional_krw=100_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=0.0,
        daily_loss_fraction=0.0,
        orderbook=clean_book,
        current_time_ms=1050,
    )
    assert verdict2 == RiskVerdict.HALT


def test_consecutive_rejections_halt(clean_book):
    engine = RiskEngine(RiskEngineConfig(consecutive_rejection_limit=3))
    engine.record_execution_outcome(False)
    engine.record_execution_outcome(False)
    assert engine.halted is False
    engine.record_execution_outcome(False)
    assert engine.halted is True

    verdict, reasons, _ = engine.evaluate_preflight(
        order_id="ord_6",
        side="BUY",
        requested_notional_krw=1_000_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=0.0,
        daily_loss_fraction=0.0,
        orderbook=clean_book,
        current_time_ms=1050,
    )
    assert verdict == RiskVerdict.HALT


def test_kill_switch_activation(tmp_path, clean_book):
    ks_file = tmp_path / "KILL_SWITCH"
    engine = RiskEngine(RiskEngineConfig(kill_switch_file=ks_file))

    # In-memory kill switch
    engine.set_kill_switch(True)
    v1, _, _ = engine.evaluate_preflight("ord_k1", "BUY", 1000.0, 10000.0, 0.0, 0.0, clean_book, 1050)
    assert v1 == RiskVerdict.HALT
    engine.set_kill_switch(False)

    # File-based kill switch
    ks_file.write_text("HALT")
    v2, _, _ = engine.evaluate_preflight("ord_k2", "BUY", 1000.0, 10000.0, 0.0, 0.0, clean_book, 1050)
    assert v2 == RiskVerdict.HALT


def test_fail_closed_on_nan_or_inf(clean_book):
    engine = RiskEngine()
    v1, _, _ = engine.evaluate_preflight("ord_bad", "BUY", float("nan"), 10000.0, 0.0, 0.0, clean_book, 1050)
    assert v1 == RiskVerdict.HALT
    v2, _, _ = engine.evaluate_preflight("ord_bad2", "BUY", 1000.0, float("inf"), 0.0, 0.0, clean_book, 1050)
    assert v2 == RiskVerdict.HALT
