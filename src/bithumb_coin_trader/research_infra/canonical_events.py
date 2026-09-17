"""Canonical Event Model for Microstructure Research.

Provides a stable event envelope with event-specific normalized payloads.
Preserves full raw provenance while enabling consistent research processing.

The canonical ordering timestamp for research is LOCAL_WRITE_TIMESTAMP
(the time the event was persisted locally). This is chosen because:
1. It represents when the data was actually available for processing
2. It is monotonically non-decreasing within a single collector run
3. Cross-exchange join uses backward/as-of on this timestamp
4. Exchange timestamps are preserved but NOT used for ordering
   (exchange clocks are not provably synchronized)
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence, Union
import json


class EventKind(str, Enum):
    TRADE = "TRADE"
    ORDERBOOK = "ORDERBOOK"
    TICKER = "TICKER"


class TimestampRole(str, Enum):
    """Explicit documentation of timestamp semantics."""
    EXCHANGE_EVENT = "EXCHANGE_EVENT"
    LOCAL_RECEIVE = "LOCAL_RECEIVE"
    LOCAL_WRITE = "LOCAL_WRITE"
    FEATURE_AVAILABILITY = "FEATURE_AVAILABILITY"  # = LOCAL_WRITE
    NONE_AVAILABLE = "NONE_AVAILABLE"


@dataclass(frozen=True, slots=True)
class CanonicalEvent:
    """Immutable research event with full provenance.

    The ordering_timestamp_ns is the AUTHORITATIVE timestamp for:
    - event ordering
    - feature availability
    - label construction
    - cross-exchange as-of joins

    It is set to local_write_timestamp (when the event was persisted).
    availability_timestamp_ns tracks when the event was causally observed locally.
    """

    # Identity
    dataset_id: str
    source_run_id: str | None
    collector_epoch: str | None

    # Provenance
    source_file: str | None
    source_file_offset: int | None  # line number or byte offset

    # Exchange context
    exchange: str  # normalized lowercase
    market: str  # uppercase KRW-BTC format
    event_kind: EventKind

    # Timestamps
    exchange_timestamp_ms: int | None
    local_recv_timestamp_ms: int
    local_write_timestamp_ms: int
    ordering_timestamp_ns: int  # = local_write_timestamp_ms * 1_000_000

    # Timestamp documentation
    exchange_timestamp_role: TimestampRole
    availability_timestamp_ns: int | None = None
    ordering_timestamp_role: TimestampRole = TimestampRole.LOCAL_WRITE

    # Cohort
    cohort_utc: str | None = None  # "YYYY-MM-DD_HH"

    # Data quality
    dq_status: str = "DATA_PRESENT"
    dq_flags: int = 0  # bitmask from data_quality_flags.DataQualityFlag

    # Schema
    schema_version: str = "1.0.0"

    # Payload (event-specific, stored as dict for flexibility)
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def causal_availability_ns(self) -> int:
        if self.availability_timestamp_ns is not None and self.availability_timestamp_ns > 0:
            return self.availability_timestamp_ns
        return self.local_recv_timestamp_ms * 1_000_000

    def to_dict(self) -> dict[str, Any]:
        d = {
            "dataset_id": self.dataset_id,
            "source_run_id": self.source_run_id,
            "collector_epoch": self.collector_epoch,
            "source_file": self.source_file,
            "source_file_offset": self.source_file_offset,
            "exchange": self.exchange,
            "market": self.market,
            "event_kind": self.event_kind.value,
            "exchange_timestamp_ms": self.exchange_timestamp_ms,
            "local_recv_timestamp_ms": self.local_recv_timestamp_ms,
            "local_write_timestamp_ms": self.local_write_timestamp_ms,
            "ordering_timestamp_ns": self.ordering_timestamp_ns,
            "availability_timestamp_ns": self.causal_availability_ns,
            "exchange_timestamp_role": self.exchange_timestamp_role.value,
            "ordering_timestamp_role": self.ordering_timestamp_role.value,
            "cohort_utc": self.cohort_utc,
            "dq_status": self.dq_status,
            "dq_flags": self.dq_flags,
            "schema_version": self.schema_version,
            "payload": self.payload,
        }
        return d


@dataclass(frozen=True, slots=True)
class TradePayload:
    price: float
    quantity: float
    aggressor_side: str  # "BUY" or "SELL"
    trade_id: str | None = None


@dataclass(frozen=True, slots=True)
class OrderbookPayload:
    bids: tuple[tuple[float, float], ...]  # ((price, size), ...) desc
    asks: tuple[tuple[float, float], ...]  # ((price, size), ...) asc
    is_snapshot: bool = True

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 0.0

    @property
    def mid_price(self) -> float:
        bb, ba = self.best_bid, self.best_ask
        return (bb + ba) / 2.0 if bb > 0 and ba > 0 else 0.0

    @property
    def spread_bps(self) -> float:
        mid = self.mid_price
        if mid <= 0:
            return 0.0
        return ((self.best_ask - self.best_bid) / mid) * 10_000.0


@dataclass(frozen=True, slots=True)
class TickerPayload:
    last_price: float
    volume_24h: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    change_price: float | None = None
    change_rate: float | None = None


def parse_iso_to_ms(iso_str: str) -> int:
    """Parse ISO 8601 timestamp to millisecond epoch."""
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def ms_to_datetime(ms: int) -> datetime:
    """Convert millisecond epoch to timezone-aware datetime."""
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def datetime_to_ns(dt: datetime) -> int:
    """Convert datetime to nanosecond epoch."""
    return int(dt.timestamp() * 1_000_000_000)
