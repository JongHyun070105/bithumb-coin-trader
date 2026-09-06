"""Deterministic Replay Engine and Monotonic Replay Clock (P3 - P3.5).

Provides:
- ReplayClock: strictly monotonic virtual clock driven by event receive timestamps.
- MultiStreamReplay: deterministic merging of orderbook, trade, and ticker streams.
- InProcessEventBus: synchronous deterministic dispatch of market events.
- Checkpointing and resume capability without wall-clock dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
import heapq
from typing import Any, Callable, Iterator, Mapping, Sequence, Union

from .canonical_market_data import (
    CanonicalOrderBook,
    CanonicalTrade,
    CanonicalTicker,
)


class ClockViolationError(ValueError):
    """Raised when a timestamp moves backwards relative to the replay clock."""


class ReplayClock:
    """Monotonically non-decreasing virtual clock driven exclusively by receive timestamps."""

    def __init__(self, initial_ms: int | None = None) -> None:
        self._current_time_ms: int | None = initial_ms

    @property
    def current_time_ms(self) -> int:
        if self._current_time_ms is None:
            raise RuntimeError("ReplayClock has not been initialized with any timestamp")
        return self._current_time_ms

    def advance(self, timestamp_ms: int) -> int:
        if self._current_time_ms is not None and timestamp_ms < self._current_time_ms:
            raise ClockViolationError(
                f"Clock regression: cannot advance to {timestamp_ms}ms from {self._current_time_ms}ms"
            )
        self._current_time_ms = timestamp_ms
        return self._current_time_ms

    def reset(self, initial_ms: int | None = None) -> None:
        self._current_time_ms = initial_ms


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    timestamp_ms: int
    exchange: str
    market: str
    stream_type: str
    item_id: str
    payload: Union[CanonicalOrderBook, CanonicalTrade, CanonicalTicker]

    @property
    def sort_key(self) -> tuple[int, str, str, str, str]:
        return (
            self.timestamp_ms,
            self.exchange,
            self.market,
            self.stream_type,
            self.item_id,
        )

    def __lt__(self, other: ReplayEvent) -> bool:
        return self.sort_key < other.sort_key

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ReplayEvent):
            return NotImplemented
        return self.sort_key == other.sort_key


def canonical_to_replay_event(
    record: Union[CanonicalOrderBook, CanonicalTrade, CanonicalTicker]
) -> ReplayEvent:
    if isinstance(record, CanonicalOrderBook):
        item_id = str(record.sequence_id or record.exchange_timestamp_ms)
        return ReplayEvent(
            timestamp_ms=record.receive_timestamp_ms,
            exchange=record.exchange,
            market=record.market,
            stream_type="orderbook",
            item_id=item_id,
            payload=record,
        )
    elif isinstance(record, CanonicalTrade):
        return ReplayEvent(
            timestamp_ms=record.receive_timestamp_ms,
            exchange=record.exchange,
            market=record.market,
            stream_type="trade",
            item_id=record.trade_id,
            payload=record,
        )
    elif isinstance(record, CanonicalTicker):
        return ReplayEvent(
            timestamp_ms=record.receive_timestamp_ms,
            exchange=record.exchange,
            market=record.market,
            stream_type="ticker",
            item_id=str(record.exchange_timestamp_ms),
            payload=record,
        )
    else:
        raise TypeError(f"Unsupported record type: {type(record)}")


class MultiStreamReplay:
    """Merges multiple streams deterministically and drives the ReplayClock."""

    def __init__(
        self,
        streams: Sequence[Iterator[Union[CanonicalOrderBook, CanonicalTrade, CanonicalTicker]]],
        clock: ReplayClock | None = None,
    ) -> None:
        self.streams = streams
        self.clock = clock if clock is not None else ReplayClock()
        self.events_processed = 0
        self._last_event_key: tuple[int, str, str, str, str] | None = None

    def __iter__(self) -> Iterator[ReplayEvent]:
        return self.iter_events()

    def iter_events(self) -> Iterator[ReplayEvent]:
        # Convert each raw stream to a stream of ReplayEvent
        event_streams = [
            (canonical_to_replay_event(rec) for rec in stream)
            for stream in self.streams
        ]
        # heapq.merge requires sorted inputs
        merged = heapq.merge(*event_streams, key=lambda ev: ev.sort_key)

        for ev in merged:
            self.clock.advance(ev.timestamp_ms)
            self._last_event_key = ev.sort_key
            self.events_processed += 1
            yield ev

    def checkpoint(self) -> dict[str, Any]:
        """Produces a serializable snapshot of replay progress."""
        return {
            "events_processed": self.events_processed,
            "current_time_ms": self.clock.current_time_ms if self.clock._current_time_ms is not None else None,
            "last_event_key": list(self._last_event_key) if self._last_event_key is not None else None,
        }


class InProcessEventBus:
    """Deterministic in-process event bus dispatching to registered callbacks."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[ReplayEvent], None]]] = {}

    def subscribe(self, stream_type: str, handler: Callable[[ReplayEvent], None]) -> None:
        """Subscribe to a stream_type ('orderbook', 'trade', 'ticker', or '*' for all)."""
        if stream_type not in self._subscribers:
            self._subscribers[stream_type] = []
        self._subscribers[stream_type].append(handler)

    def publish(self, event: ReplayEvent) -> None:
        """Synchronously dispatch event to matching subscribers."""
        # Specific subscribers
        for handler in self._subscribers.get(event.stream_type, []):
            handler(event)
        # Wildcard subscribers
        for handler in self._subscribers.get("*", []):
            handler(event)
