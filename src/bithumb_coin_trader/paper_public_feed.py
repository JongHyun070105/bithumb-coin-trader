"""Public-only Bithumb WebSocket adapter for the local PAPER runtime."""

from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, date, datetime
from decimal import Decimal
import hashlib
import json
from threading import Event
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .bithumb_websocket import (
    BithumbWebSocketObserver,
    ObservationEvent,
    ObservationValidationError,
    OrderbookObservation,
    TradeObservation,
    build_public_subscription,
)
from .execution_simulator import OrderBookSnapshot
from .models import Candle
from .paper_runtime import NormalizedPaperEvent, PaperRuntime, PaperRuntimeError

_KST = ZoneInfo("Asia/Seoul")
_MAX_DAILY_TRADE_IDS = 500_000
_RECENT_BOOK_IDS = 20_000


class PaperPublicFeedError(ValueError):
    """Raised when public feed order or completeness is unsafe for PAPER."""


class BithumbPaperEventAdapter:
    """Turn validated public trades/books into deterministic runtime events.

    The first observed daily trade bar is always discarded because collection
    may have begun mid-day. A disconnect, trade reordering, skipped day, or an
    excessive duplicate-tracking window invalidates that day's candle.
    """

    def __init__(self, market: str) -> None:
        if not market.startswith("KRW-"):
            raise PaperPublicFeedError("public PAPER adapter only supports KRW spot markets")
        self.market = market
        self._day: date | None = None
        self._ohlcv: list[Decimal] | None = None
        self._last_trade_ms: int | None = None
        self._trade_ids: dict[int, tuple[Decimal, Decimal, str, int]] = {}
        self._bar_invalid = False
        self._initial_partial_bar = True
        self._pending_candle: Candle | None = None
        self._pending_source_hashes: tuple[str, ...] = ()
        self._day_source_hashes: list[str] = []
        self._last_book_timestamp_us: int | None = None
        self._last_event_timestamp_ms: int | None = None
        self._recent_books: OrderedDict[str, None] = OrderedDict()

    def connection_changed(self, connected: bool) -> None:
        if not connected and self._day is not None:
            self._bar_invalid = True

    def accept(self, event: ObservationEvent) -> NormalizedPaperEvent | None:
        observation = event.observation
        if not _is_sha256(event.raw_payload_sha256):
            raise PaperPublicFeedError("Bithumb observation is missing its raw-frame SHA-256")
        if isinstance(observation, TradeObservation):
            self._accept_trade(observation, event.raw_payload_sha256)
            return None
        if isinstance(observation, OrderbookObservation):
            return self._accept_book(observation, event.raw_payload_sha256)
        return None

    def _accept_trade(self, trade: TradeObservation, raw_payload_sha256: str) -> None:
        if trade.code != self.market:
            return
        if trade.stream_type != "REALTIME":
            self._bar_invalid = True
            return
        trade_day = datetime.fromtimestamp(trade.trade_timestamp_ms / 1000.0, UTC).astimezone(_KST).date()
        if self._day is None:
            self._start_day(trade_day, trade, raw_payload_sha256)
            return
        if trade_day < self._day:
            self._bar_invalid = True
            raise PaperPublicFeedError("Bithumb trade time moved to an earlier KST day")
        if trade_day > self._day:
            if (trade_day - self._day).days != 1:
                self._bar_invalid = True
                raise PaperPublicFeedError("Bithumb trade stream skipped one or more KST days")
            elif not self._bar_invalid and not self._initial_partial_bar:
                self._pending_candle = self._finish_bar()
                self._pending_source_hashes = tuple(self._day_source_hashes)
            self._start_day(trade_day, trade, raw_payload_sha256)
            self._initial_partial_bar = False
            return
        previous_trade = self._trade_ids.get(trade.sequential_id)
        if previous_trade is not None:
            if previous_trade != _trade_identity(trade):
                self._bar_invalid = True
                raise PaperPublicFeedError("Bithumb reused a trade sequential_id with conflicting data")
            return
        if len(self._trade_ids) >= _MAX_DAILY_TRADE_IDS:
            self._bar_invalid = True
            raise PaperPublicFeedError("daily Bithumb trade deduplication limit exceeded; candle invalidated")
        self._trade_ids[trade.sequential_id] = _trade_identity(trade)
        if self._last_trade_ms is not None and trade.trade_timestamp_ms < self._last_trade_ms:
            self._bar_invalid = True
            return
        self._update_bar(trade, raw_payload_sha256)

    def _start_day(self, day: date, trade: TradeObservation, raw_payload_sha256: str) -> None:
        self._day = day
        self._trade_ids = {trade.sequential_id: _trade_identity(trade)}
        self._day_source_hashes = [raw_payload_sha256]
        self._last_trade_ms = trade.trade_timestamp_ms
        self._ohlcv = [trade.trade_price, trade.trade_price, trade.trade_price, trade.trade_price, trade.trade_volume]
        self._bar_invalid = False

    def _update_bar(self, trade: TradeObservation, raw_payload_sha256: str) -> None:
        assert self._ohlcv is not None
        self._ohlcv[1] = max(self._ohlcv[1], trade.trade_price)
        self._ohlcv[2] = min(self._ohlcv[2], trade.trade_price)
        self._ohlcv[3] = trade.trade_price
        self._ohlcv[4] += trade.trade_volume
        self._day_source_hashes.append(raw_payload_sha256)
        self._last_trade_ms = trade.trade_timestamp_ms

    def _finish_bar(self) -> Candle:
        assert self._day is not None and self._ohlcv is not None and self._last_trade_ms is not None
        timestamp = datetime.fromtimestamp(self._last_trade_ms / 1000.0, UTC)
        return Candle(
            timestamp=timestamp,
            open=float(self._ohlcv[0]),
            high=float(self._ohlcv[1]),
            low=float(self._ohlcv[2]),
            close=float(self._ohlcv[3]),
            volume=float(self._ohlcv[4]),
            market=self.market,
        )

    def _accept_book(
        self, book: OrderbookObservation, raw_payload_sha256: str
    ) -> NormalizedPaperEvent | None:
        if book.code != self.market:
            return None
        if book.stream_type != "REALTIME":
            raise PaperPublicFeedError("Bithumb order-book snapshots are unsupported for PAPER")
        fingerprint = _hash_json({
            "timestamp_us": book.timestamp_us,
            "levels": [
                [str(level.bid_price), str(level.bid_size), str(level.ask_price), str(level.ask_size)]
                for level in book.levels
            ],
        })
        if fingerprint in self._recent_books:
            return None
        if self._last_book_timestamp_us is not None and book.timestamp_us <= self._last_book_timestamp_us:
            raise PaperPublicFeedError("Bithumb order-book timestamps must be strictly increasing")
        self._last_book_timestamp_us = book.timestamp_us
        self._recent_books[fingerprint] = None
        if len(self._recent_books) > _RECENT_BOOK_IDS:
            self._recent_books.popitem(last=False)

        timestamp_ms = book.timestamp_us // 1000
        runtime_candle = None
        source_hashes = [raw_payload_sha256]
        if self._pending_candle is not None:
            candle_ms = int(self._pending_candle.timestamp.timestamp() * 1000)
            if timestamp_ms > candle_ms:
                runtime_candle = self._pending_candle
                self._pending_candle = None
                source_hashes = [*self._pending_source_hashes, raw_payload_sha256]
                self._pending_source_hashes = ()

        received_at_ms = max(timestamp_ms, (self._last_event_timestamp_ms or -1) + 1)
        if received_at_ms < 0:
            raise PaperPublicFeedError("Bithumb order-book timestamp is invalid")
        self._last_event_timestamp_ms = received_at_ms
        bids = tuple(sorted(
            ((float(level.bid_price), float(level.bid_size)) for level in book.levels),
            key=lambda item: item[0], reverse=True,
        ))
        asks = tuple(sorted(
            ((float(level.ask_price), float(level.ask_size)) for level in book.levels),
            key=lambda item: item[0],
        ))
        snapshot = OrderBookSnapshot(
            timestamp=book.timestamp_us / 1_000_000.0,
            bids=bids,
            asks=asks,
            market=self.market,
        )
        event_id = f"bithumb:{self.market}:book:{book.timestamp_us}:{fingerprint[:16]}"
        if runtime_candle is not None:
            event_id += f":candle:{runtime_candle.timestamp.isoformat()}"
        return NormalizedPaperEvent(
            event_id,
            received_at_ms,
            runtime_candle,
            (snapshot,),
            tuple(source_hashes),
        )


def run_bithumb_public_paper_feed(
    *,
    runtime: PaperRuntime,
    market: str,
    stop_event: Event,
    record_callback: Callable[[dict[str, Any], dict[str, Any]], None],
    receipt_callback: Callable[[dict[str, Any]], None] | None = None,
    subscription: list[dict[str, Any]] | None = None,
) -> None:
    """Run public-only feed; a disconnect or invalid frame durably halts PAPER."""
    adapter = BithumbPaperEventAdapter(market)
    terminal_reason = "REQUESTED_STOP"

    def on_connection(connected: bool) -> None:
        nonlocal terminal_reason
        if receipt_callback is not None:
            receipt_callback({
                "record_type": "CONNECTION",
                "connected": connected,
                "received_at_ms": _now_ms(),
            })
        adapter.connection_changed(connected)
        if not connected:
            terminal_reason = "PUBLIC_FEED_DISCONNECTED"
            runtime.halt("PUBLIC_FEED_DISCONNECTED")
            stop_event.set()

    def on_validation_error(_error: ObservationValidationError) -> None:
        nonlocal terminal_reason
        terminal_reason = "PUBLIC_FEED_VALIDATION_ERROR"
        if receipt_callback is not None:
            receipt_callback({
                "record_type": "VALIDATION_ERROR",
                "reason_code": "OBSERVATION_SCHEMA_INVALID",
                "received_at_ms": _now_ms(),
            })
        adapter.connection_changed(False)
        runtime.halt("PUBLIC_FEED_VALIDATION_ERROR")
        stop_event.set()

    def on_observation(event: ObservationEvent) -> None:
        nonlocal terminal_reason
        try:
            try:
                normalized = adapter.accept(event)
            except Exception as exc:
                terminal_reason = "PAPER_PUBLIC_FEED_ADAPTER_FAILURE"
                if receipt_callback is not None:
                    receipt_callback({
                        "record_type": "OBSERVATION",
                        "raw_payload_sha256": event.raw_payload_sha256,
                        "observation_type": type(event.observation).__name__,
                        "received_at_ms": int(event.received_at.timestamp() * 1000),
                        "validation_status": "ADAPTER_REJECTED",
                        "error_type": type(exc).__name__,
                    })
                raise
            if receipt_callback is not None:
                receipt_callback({
                    "record_type": "OBSERVATION",
                    "raw_payload_sha256": event.raw_payload_sha256,
                    "observation_type": type(event.observation).__name__,
                    "received_at_ms": int(event.received_at.timestamp() * 1000),
                    "validation_status": "ACCEPTED",
                    "normalized_event_id": normalized.event_id if normalized is not None else None,
                    "source_observation_sha256": list(normalized.source_observation_sha256)
                    if normalized is not None else [],
                })
            if normalized is None:
                return
            result = runtime.process_event(normalized)
            metrics = runtime.metrics(normalized.orderbooks[-1])
            record_callback(result, metrics)
            if runtime.is_halted:
                terminal_reason = runtime.halt_reason or "PAPER_RUNTIME_HALTED"
                stop_event.set()
        except Exception:
            if terminal_reason == "REQUESTED_STOP":
                terminal_reason = "PAPER_PUBLIC_FEED_FAILURE"
            if not runtime.is_halted:
                try:
                    runtime.halt("PAPER_PUBLIC_FEED_ADAPTER_FAILURE")
                except PaperRuntimeError:
                    pass
            stop_event.set()
            raise

    active_subscription = subscription or build_public_subscription(
        [market], ticker=False, trade=True, orderbook=True, realtime_only=True
    )
    observer = BithumbWebSocketObserver(
        active_subscription,
        private=False,
        callback=on_observation,
        connection_callback=on_connection,
        validation_callback=on_validation_error,
    )
    try:
        observer.run_forever(stop_event)
    finally:
        if receipt_callback is not None:
            receipt_callback({
                "record_type": "SESSION_TERMINAL",
                "status": "HALTED" if runtime.is_halted else "STOPPED",
                "reason_code": terminal_reason,
                "ended_at_ms": _now_ms(),
            })


def _hash_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _trade_identity(trade: TradeObservation) -> tuple[Decimal, Decimal, str, int]:
    return (
        trade.trade_price,
        trade.trade_volume,
        trade.ask_bid,
        trade.trade_timestamp_ms,
    )


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )
