import pytest
import time
from bithumb_coin_trader.canonical_market_data import (
    CanonicalOrderBook,
    CanonicalTrade,
    CanonicalTicker,
)
from bithumb_coin_trader.replay import (
    ReplayClock,
    ClockViolationError,
    ReplayEvent,
    MultiStreamReplay,
    InProcessEventBus,
    canonical_to_replay_event,
)


def test_replay_clock_advancement():
    clock = ReplayClock()
    with pytest.raises(RuntimeError, match="not been initialized"):
        _ = clock.current_time_ms

    assert clock.advance(1000) == 1000
    assert clock.current_time_ms == 1000
    assert clock.advance(1000) == 1000  # Non-decreasing allowed
    assert clock.advance(1050) == 1050

    with pytest.raises(ClockViolationError, match="Clock regression"):
        clock.advance(1020)


def test_multi_stream_deterministic_merge():
    bithumb_books = [
        CanonicalOrderBook("bithumb", "KRW-BTC", 100, 105, ((100.0, 1.0),), ((101.0, 1.0),)),
        CanonicalOrderBook("bithumb", "KRW-BTC", 200, 205, ((100.5, 1.0),), ((101.5, 1.0),)),
        CanonicalOrderBook("bithumb", "KRW-BTC", 300, 305, ((100.2, 1.0),), ((101.2, 1.0),)),
    ]
    binance_trades = [
        CanonicalTrade("binance", "BTCUSDT", "tr_1", 90, 102, 60000.0, 0.1, "BUY"),
        CanonicalTrade("binance", "BTCUSDT", "tr_2", 190, 202, 60010.0, 0.2, "SELL"),
        CanonicalTrade("binance", "BTCUSDT", "tr_3", 290, 302, 60005.0, 0.3, "BUY"),
    ]
    upbit_tickers = [
        CanonicalTicker("upbit", "KRW-BTC", 101, 104, 100000000.0),
        CanonicalTicker("upbit", "KRW-BTC", 201, 204, 100005000.0),
        CanonicalTicker("upbit", "KRW-BTC", 301, 304, 100002000.0),
    ]

    clock = ReplayClock()
    replay = MultiStreamReplay(
        streams=[iter(bithumb_books), iter(binance_trades), iter(upbit_tickers)],
        clock=clock,
    )

    events = list(replay)
    assert len(events) == 9

    # Verify timestamps are strictly non-decreasing
    ts_list = [ev.timestamp_ms for ev in events]
    assert ts_list == sorted(ts_list)
    assert ts_list == [102, 104, 105, 202, 204, 205, 302, 304, 305]

    # Verify stream types match the chronological order
    types = [ev.stream_type for ev in events]
    assert types == [
        "trade", "ticker", "orderbook",
        "trade", "ticker", "orderbook",
        "trade", "ticker", "orderbook",
    ]

    cp = replay.checkpoint()
    assert cp["events_processed"] == 9
    assert cp["current_time_ms"] == 305


def test_tie_breaking_determinism():
    # Identical timestamps across streams
    ev1 = CanonicalTrade("binance", "BTCUSDT", "t1", 100, 150, 60000.0, 1.0, "BUY")
    ev2 = CanonicalOrderBook("bithumb", "KRW-BTC", 100, 150, ((100.0, 1.0),), ((101.0, 1.0),))

    replay1 = MultiStreamReplay([iter([ev1]), iter([ev2])])
    order1 = [e.item_id for e in replay1]

    replay2 = MultiStreamReplay([iter([ev2]), iter([ev1])])
    order2 = [e.item_id for e in replay2]

    assert order1 == order2  # Deterministic regardless of input stream order


def test_in_process_event_bus():
    bus = InProcessEventBus()
    received_books = []
    received_all = []

    bus.subscribe("orderbook", lambda ev: received_books.append(ev))
    bus.subscribe("*", lambda ev: received_all.append(ev))

    ev = canonical_to_replay_event(
        CanonicalOrderBook("bithumb", "KRW-BTC", 100, 105, ((100.0, 1.0),), ((101.0, 1.0),))
    )
    bus.publish(ev)

    assert len(received_books) == 1
    assert len(received_all) == 1
    assert received_books[0] == ev


def test_zero_wall_clock_leakage(monkeypatch):
    def forbidden_wall_clock(*args, **kwargs):
        raise AssertionError("Wall clock was called during deterministic replay!")

    monkeypatch.setattr(time, "time", forbidden_wall_clock)
    monkeypatch.setattr(time, "sleep", forbidden_wall_clock)

    books = [CanonicalOrderBook("bithumb", "KRW-BTC", 100, 105, ((100.0, 1.0),), ((101.0, 1.0),))]
    replay = MultiStreamReplay([iter(books)])
    events = list(replay)
    assert len(events) == 1
