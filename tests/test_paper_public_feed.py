from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from threading import Event
from typing import Any

import pytest

from bithumb_coin_trader.bithumb_websocket import ObservationEvent, parse_observation
from bithumb_coin_trader.paper_public_feed import (
    BithumbPaperEventAdapter,
    PaperPublicFeedError,
    run_bithumb_public_paper_feed,
)


def _trade(timestamp: str, sequential_id: int, price: int, volume: str = "1") -> ObservationEvent:
    timestamp_ms = int(datetime.fromisoformat(timestamp).timestamp() * 1000)
    return parse_observation(
        {
            "type": "trade",
            "code": "KRW-BTC",
            "trade_price": price,
            "trade_volume": volume,
            "ask_bid": "BID",
            "trade_timestamp": timestamp_ms,
            "sequential_id": sequential_id,
            "timestamp": timestamp_ms,
            "stream_type": "REALTIME",
        }
    )


def _book(
    timestamp: str, price: int = 100, *, stream_type: str = "REALTIME"
) -> ObservationEvent:
    timestamp_ms = int(datetime.fromisoformat(timestamp).timestamp() * 1000)
    return parse_observation(
        {
            "type": "orderbook",
            "code": "KRW-BTC",
            "timestamp": timestamp_ms * 1000,
            "stream_type": stream_type,
            "orderbook_units": [
                {"ask_price": price + 1, "bid_price": price, "ask_size": "2", "bid_size": "3"}
            ],
        }
    )


def test_adapter_discards_partial_startup_bar_and_emits_complete_daily_candle() -> None:
    adapter = BithumbPaperEventAdapter("KRW-BTC")
    assert adapter.accept(_trade("2024-01-01T01:00:00+09:00", 1, 100)) is None
    assert adapter.accept(_trade("2024-01-01T01:00:00+09:00", 1, 100)) is None
    assert adapter.accept(_trade("2024-01-02T00:00:01+09:00", 2, 105, "2")) is None
    assert adapter.accept(_trade("2024-01-02T12:00:00+09:00", 3, 110, "3")) is None
    assert adapter.accept(_trade("2024-01-03T00:00:01+09:00", 4, 108, "1")) is None

    event = adapter.accept(_book("2024-01-03T00:00:02+09:00", 108))

    assert event is not None
    assert event.candle is not None
    assert event.candle.timestamp == datetime(2024, 1, 2, 3, 0, tzinfo=UTC)
    assert event.candle.open == 105
    assert event.candle.high == 110
    assert event.candle.low == 105
    assert event.candle.close == 110
    assert event.candle.volume == 5
    assert event.orderbooks[0].market == "KRW-BTC"


def test_disconnect_invalidates_the_current_daily_candle() -> None:
    adapter = BithumbPaperEventAdapter("KRW-BTC")
    adapter.accept(_trade("2024-01-01T01:00:00+09:00", 1, 100))
    adapter.accept(_trade("2024-01-02T00:00:01+09:00", 2, 105))
    adapter.accept(_trade("2024-01-02T12:00:00+09:00", 3, 110))
    adapter.connection_changed(False)
    adapter.connection_changed(True)
    adapter.accept(_trade("2024-01-03T00:00:01+09:00", 4, 108))

    event = adapter.accept(_book("2024-01-03T00:00:02+09:00", 108))

    assert event is not None
    assert event.candle is None


def test_out_of_order_trade_invalidates_the_current_daily_candle() -> None:
    adapter = BithumbPaperEventAdapter("KRW-BTC")
    adapter.accept(_trade("2024-01-01T01:00:00+09:00", 1, 100))
    adapter.accept(_trade("2024-01-02T00:00:01+09:00", 2, 105))
    adapter.accept(_trade("2024-01-02T12:00:00+09:00", 3, 110))
    adapter.accept(_trade("2024-01-02T11:00:00+09:00", 4, 108))
    adapter.accept(_trade("2024-01-03T00:00:01+09:00", 5, 108))

    event = adapter.accept(_book("2024-01-03T00:00:02+09:00", 108))

    assert event is not None
    assert event.candle is None


def test_conflicting_duplicate_trade_identity_fails_closed() -> None:
    adapter = BithumbPaperEventAdapter("KRW-BTC")
    adapter.accept(_trade("2024-01-02T12:00:00+09:00", 7, 100))

    with pytest.raises(PaperPublicFeedError, match="conflicting data"):
        adapter.accept(_trade("2024-01-02T12:00:00+09:00", 7, 101))


def test_adapter_rejects_out_of_order_trade_day_and_book_timestamps() -> None:
    adapter = BithumbPaperEventAdapter("KRW-BTC")
    adapter.accept(_trade("2024-01-02T12:00:00+09:00", 1, 100))
    with pytest.raises(PaperPublicFeedError, match="earlier KST day"):
        adapter.accept(_trade("2024-01-01T12:00:00+09:00", 2, 101))

    books = BithumbPaperEventAdapter("KRW-BTC")
    books.accept(_book("2024-01-02T12:00:00+09:00"))
    with pytest.raises(PaperPublicFeedError, match="strictly increasing"):
        books.accept(_book("2024-01-02T11:59:59+09:00"))

    gap = BithumbPaperEventAdapter("KRW-BTC")
    gap.accept(_trade("2024-01-01T12:00:00+09:00", 1, 100))
    with pytest.raises(PaperPublicFeedError, match="skipped one or more KST days"):
        gap.accept(_trade("2024-01-03T00:00:01+09:00", 2, 101))


def test_duplicate_orderbook_is_idempotently_ignored() -> None:
    adapter = BithumbPaperEventAdapter("KRW-BTC")
    book = _book("2024-01-02T12:00:00+09:00")

    first = adapter.accept(book)
    second = adapter.accept(book)

    assert first is not None
    assert second is None


def test_orderbook_snapshot_is_not_used_as_a_paper_execution_book() -> None:
    adapter = BithumbPaperEventAdapter("KRW-BTC")

    with pytest.raises(PaperPublicFeedError, match="snapshots are unsupported"):
        adapter.accept(_book("2024-01-02T12:00:00+09:00", stream_type="SNAPSHOT"))


def test_public_feed_supervisor_never_authenticates_and_halts_on_disconnect(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, Any] = {}

    class RuntimeStub:
        is_halted = False

        def halt(self, reason: str) -> None:
            captured["halt_reason"] = reason
            self.is_halted = True

    class ObserverStub:
        def __init__(self, subscription, *, private, callback, connection_callback, validation_callback) -> None:
            captured["subscription"] = subscription
            captured["private"] = private
            self._connection_callback = connection_callback

        def run_forever(self, stop_event: Event) -> None:
            self._connection_callback(False)
            assert stop_event.is_set()

    monkeypatch.setattr("bithumb_coin_trader.paper_public_feed.BithumbWebSocketObserver", ObserverStub)
    stop_event = Event()
    run_bithumb_public_paper_feed(
        runtime=RuntimeStub(),  # type: ignore[arg-type]
        market="KRW-BTC",
        stop_event=stop_event,
        record_callback=lambda _result, _metrics: None,
    )

    assert captured["private"] is False
    assert [part.get("type") for part in captured["subscription"]] == [None, "trade", "orderbook", None]
    assert captured["halt_reason"] == "PUBLIC_FEED_DISCONNECTED"
