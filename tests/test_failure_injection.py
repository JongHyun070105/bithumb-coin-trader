import pytest
from decimal import Decimal
from bithumb_coin_trader.canonical_market_data import CanonicalOrderBook
from bithumb_coin_trader.risk_engine import RiskEngine, RiskVerdict
from bithumb_coin_trader.execution_simulator import DeterministicTakerSimulator, MarketOrderRequest
from bithumb_coin_trader.paper_engine import PaperPortfolio


def test_crossed_book_failure_injection():
    engine = RiskEngine()
    crossed_book = CanonicalOrderBook(
        exchange="bithumb",
        market="KRW-BTC",
        exchange_timestamp_ms=1000,
        receive_timestamp_ms=1010,
        bids=((101_000_000.0, 1.0),),
        asks=((100_000_000.0, 1.0),),
        is_snapshot=True,
    )
    verdict, reasons, _ = engine.evaluate_preflight(
        order_id="ord_fail_1",
        side="BUY",
        requested_notional_krw=1_000_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=0.0,
        daily_loss_fraction=0.0,
        orderbook=crossed_book,
        current_time_ms=1050,
    )
    assert verdict == RiskVerdict.HALT
    assert engine.halted is True
    assert any("Crossed" in r for r in reasons)


def test_crash_drawdown_circuit_breaker():
    engine = RiskEngine()
    clean_book = CanonicalOrderBook(
        exchange="bithumb",
        market="KRW-BTC",
        exchange_timestamp_ms=1000,
        receive_timestamp_ms=1010,
        bids=((50_000_000.0, 1.0),),
        asks=((50_050_000.0, 1.0),),
    )
    # 30% sudden loss
    verdict, reasons, _ = engine.evaluate_preflight(
        order_id="ord_fail_2",
        side="BUY",
        requested_notional_krw=1_000_000.0,
        current_equity_krw=14_000_000.0,
        current_position_notional_krw=0.0,
        daily_loss_fraction=0.30,
        orderbook=clean_book,
        current_time_ms=1050,
    )
    assert verdict == RiskVerdict.HALT
    assert engine.halted is True
