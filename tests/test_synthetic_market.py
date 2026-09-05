import pytest
from bithumb_coin_trader.synthetic_market import (
    NullMarketGenerator,
    SignalMarketGenerator,
    ChaosInjector,
)
from bithumb_coin_trader.canonical_market_data import validate_canonical_orderbook


def test_null_market_generator():
    gen = NullMarketGenerator(initial_price=100_000_000.0, seed=42)
    books = gen.generate_orderbooks(count=50, interval_ms=100)
    assert len(books) == 50
    for b in books:
        validate_canonical_orderbook(b)
        assert b.spread == 1000.0 or b.spread > 0


def test_signal_market_generator():
    gen = SignalMarketGenerator(initial_price=100_000_000.0, seed=123)
    books, signals = gen.generate_signal_orderbooks(count=100)
    assert len(books) == 100
    assert len(signals) == 100
    for b in books:
        validate_canonical_orderbook(b)


def test_chaos_injector():
    gen = NullMarketGenerator(initial_price=100_000_000.0, seed=42)
    books = gen.generate_orderbooks(count=100)

    injector = ChaosInjector(seed=999)
    disrupted = injector.inject_disruptions(books, drop_rate=0.10, max_jitter_ms=150, spread_blowout_rate=0.05)

    # Some packets should be dropped
    assert len(disrupted) < len(books)
    # Jitter should introduce different receive timestamps
    assert any(d.receive_timestamp_ms != b.receive_timestamp_ms for d, b in zip(disrupted, books))
