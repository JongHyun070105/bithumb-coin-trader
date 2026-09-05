"""Canonical Market Data Model, Validator, and Schema Contracts (P2 - P2.4).

Provides an exchange-agnostic, versioned, strictly-validated representation of:
- CanonicalOrderBook
- CanonicalTrade
- CanonicalTicker
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence, Type, Union
import zstandard as zstd


class TimestampSemantics(str, Enum):
    EXCHANGE_EVENT = "EXCHANGE_EVENT"
    EXCHANGE_PUBLICATION = "EXCHANGE_PUBLICATION"
    EXCHANGE_TRADE_EXECUTION = "EXCHANGE_TRADE_EXECUTION"
    LOCAL_RECEIVE = "LOCAL_RECEIVE"
    UNKNOWN = "UNKNOWN"


class CanonicalDataValidationError(ValueError):
    """Raised when canonical market data violates schema invariants."""


@dataclass(frozen=True, slots=True)
class CanonicalOrderBook:
    exchange: str
    market: str
    exchange_timestamp_ms: int
    receive_timestamp_ms: int
    bids: tuple[tuple[float, float], ...]  # ((price, size), ...) sorted high to low
    asks: tuple[tuple[float, float], ...]  # ((price, size), ...) sorted low to high
    schema_version: str = "2.0.0"
    timestamp_semantics: TimestampSemantics = TimestampSemantics.EXCHANGE_EVENT
    receive_monotonic_ns: int | None = None
    sequence_id: int | None = None
    is_snapshot: bool = True

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 0.0

    @property
    def best_bid_size(self) -> float:
        return self.bids[0][1] if self.bids else 0.0

    @property
    def best_ask_size(self) -> float:
        return self.asks[0][1] if self.asks else 0.0

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0 if (self.best_bid > 0 and self.best_ask > 0) else 0.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid if (self.best_bid > 0 and self.best_ask > 0) else 0.0

    @property
    def spread_bps(self) -> float:
        mid = self.mid_price
        return (self.spread / mid * 10_000.0) if mid > 0 else 0.0

    def compute_sha256(self) -> str:
        d = {
            "exchange": self.exchange,
            "market": self.market,
            "exchange_timestamp_ms": self.exchange_timestamp_ms,
            "receive_timestamp_ms": self.receive_timestamp_ms,
            "bids": self.bids,
            "asks": self.asks,
            "schema_version": self.schema_version,
        }
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange,
            "market": self.market,
            "exchange_timestamp_ms": self.exchange_timestamp_ms,
            "receive_timestamp_ms": self.receive_timestamp_ms,
            "bids": [list(item) for item in self.bids],
            "asks": [list(item) for item in self.asks],
            "schema_version": self.schema_version,
            "timestamp_semantics": self.timestamp_semantics.value,
            "receive_monotonic_ns": self.receive_monotonic_ns,
            "sequence_id": self.sequence_id,
            "is_snapshot": self.is_snapshot,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> CanonicalOrderBook:
        bids = tuple((float(p), float(s)) for p, s in d["bids"])
        asks = tuple((float(p), float(s)) for p, s in d["asks"])
        semantics = TimestampSemantics(d.get("timestamp_semantics", TimestampSemantics.EXCHANGE_EVENT.value))
        ob = cls(
            exchange=str(d["exchange"]),
            market=str(d["market"]),
            exchange_timestamp_ms=int(d["exchange_timestamp_ms"]),
            receive_timestamp_ms=int(d["receive_timestamp_ms"]),
            bids=bids,
            asks=asks,
            schema_version=str(d.get("schema_version", "2.0.0")),
            timestamp_semantics=semantics,
            receive_monotonic_ns=int(d["receive_monotonic_ns"]) if d.get("receive_monotonic_ns") is not None else None,
            sequence_id=int(d["sequence_id"]) if d.get("sequence_id") is not None else None,
            is_snapshot=bool(d.get("is_snapshot", True)),
        )
        validate_canonical_orderbook(ob)
        return ob


@dataclass(frozen=True, slots=True)
class CanonicalTrade:
    exchange: str
    market: str
    trade_id: str
    exchange_timestamp_ms: int
    receive_timestamp_ms: int
    price: float
    quantity: float
    aggressor_side: str  # "BUY" or "SELL"
    schema_version: str = "2.0.0"
    timestamp_semantics: TimestampSemantics = TimestampSemantics.EXCHANGE_TRADE_EXECUTION
    receive_monotonic_ns: int | None = None

    def compute_sha256(self) -> str:
        d = {
            "exchange": self.exchange,
            "market": self.market,
            "trade_id": self.trade_id,
            "price": self.price,
            "quantity": self.quantity,
            "aggressor_side": self.aggressor_side,
            "schema_version": self.schema_version,
        }
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange,
            "market": self.market,
            "trade_id": self.trade_id,
            "exchange_timestamp_ms": self.exchange_timestamp_ms,
            "receive_timestamp_ms": self.receive_timestamp_ms,
            "price": self.price,
            "quantity": self.quantity,
            "aggressor_side": self.aggressor_side,
            "schema_version": self.schema_version,
            "timestamp_semantics": self.timestamp_semantics.value,
            "receive_monotonic_ns": self.receive_monotonic_ns,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> CanonicalTrade:
        trade = cls(
            exchange=str(d["exchange"]),
            market=str(d["market"]),
            trade_id=str(d["trade_id"]),
            exchange_timestamp_ms=int(d["exchange_timestamp_ms"]),
            receive_timestamp_ms=int(d["receive_timestamp_ms"]),
            price=float(d["price"]),
            quantity=float(d["quantity"]),
            aggressor_side=str(d["aggressor_side"]).upper(),
            schema_version=str(d.get("schema_version", "2.0.0")),
            timestamp_semantics=TimestampSemantics(d.get("timestamp_semantics", TimestampSemantics.EXCHANGE_TRADE_EXECUTION.value)),
            receive_monotonic_ns=int(d["receive_monotonic_ns"]) if d.get("receive_monotonic_ns") is not None else None,
        )
        validate_canonical_trade(trade)
        return trade


@dataclass(frozen=True, slots=True)
class CanonicalTicker:
    exchange: str
    market: str
    exchange_timestamp_ms: int
    receive_timestamp_ms: int
    last_price: float
    volume_24h: float | None = None
    schema_version: str = "2.0.0"
    timestamp_semantics: TimestampSemantics = TimestampSemantics.EXCHANGE_PUBLICATION
    receive_monotonic_ns: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange,
            "market": self.market,
            "exchange_timestamp_ms": self.exchange_timestamp_ms,
            "receive_timestamp_ms": self.receive_timestamp_ms,
            "last_price": self.last_price,
            "volume_24h": self.volume_24h,
            "schema_version": self.schema_version,
            "timestamp_semantics": self.timestamp_semantics.value,
            "receive_monotonic_ns": self.receive_monotonic_ns,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> CanonicalTicker:
        ticker = cls(
            exchange=str(d["exchange"]),
            market=str(d["market"]),
            exchange_timestamp_ms=int(d["exchange_timestamp_ms"]),
            receive_timestamp_ms=int(d["receive_timestamp_ms"]),
            last_price=float(d["last_price"]),
            volume_24h=float(d["volume_24h"]) if d.get("volume_24h") is not None else None,
            schema_version=str(d.get("schema_version", "2.0.0")),
            timestamp_semantics=TimestampSemantics(d.get("timestamp_semantics", TimestampSemantics.EXCHANGE_PUBLICATION.value)),
            receive_monotonic_ns=int(d["receive_monotonic_ns"]) if d.get("receive_monotonic_ns") is not None else None,
        )
        validate_canonical_ticker(ticker)
        return ticker


def validate_canonical_orderbook(ob: CanonicalOrderBook) -> None:
    """Strict validation for CanonicalOrderBook."""
    if not ob.exchange or not ob.market:
        raise CanonicalDataValidationError("exchange and market must be non-empty strings")
    if ob.exchange_timestamp_ms <= 0 or ob.receive_timestamp_ms <= 0:
        raise CanonicalDataValidationError("timestamps must be strictly positive")

    # Future receive check: exchange event should not be ridiculously far in the future
    if ob.exchange_timestamp_ms > ob.receive_timestamp_ms + 10_000:
        raise CanonicalDataValidationError(
            f"Future exchange timestamp detected: {ob.exchange_timestamp_ms} > {ob.receive_timestamp_ms} + 10s"
        )

    if not ob.bids or not ob.asks:
        raise CanonicalDataValidationError("Orderbook must have at least one bid and one ask")

    # Validate bids: non-empty, strictly positive, finite, strictly descending
    prev_bid_price = float("inf")
    seen_bids = set()
    for p, s in ob.bids:
        if not math.isfinite(p) or not math.isfinite(s):
            raise CanonicalDataValidationError("Non-finite numeric in bids")
        if p <= 0 or s <= 0:
            raise CanonicalDataValidationError("Bid price and size must be positive")
        if p >= prev_bid_price:
            raise CanonicalDataValidationError(f"Bids not strictly descending: {p} >= {prev_bid_price}")
        if p in seen_bids:
            raise CanonicalDataValidationError(f"Duplicate bid price: {p}")
        seen_bids.add(p)
        prev_bid_price = p

    # Validate asks: non-empty, strictly positive, finite, strictly ascending
    prev_ask_price = float("-inf")
    seen_asks = set()
    for p, s in ob.asks:
        if not math.isfinite(p) or not math.isfinite(s):
            raise CanonicalDataValidationError("Non-finite numeric in asks")
        if p <= 0 or s <= 0:
            raise CanonicalDataValidationError("Ask price and size must be positive")
        if p <= prev_ask_price:
            raise CanonicalDataValidationError(f"Asks not strictly ascending: {p} <= {prev_ask_price}")
        if p in seen_asks:
            raise CanonicalDataValidationError(f"Duplicate ask price: {p}")
        seen_asks.add(p)
        prev_ask_price = p

    # Crossed/locked book check
    if ob.bids[0][0] >= ob.asks[0][0]:
        raise CanonicalDataValidationError(
            f"Crossed book: best_bid ({ob.bids[0][0]}) >= best_ask ({ob.asks[0][0]})"
        )


def validate_canonical_trade(trade: CanonicalTrade) -> None:
    """Strict validation for CanonicalTrade."""
    if not trade.exchange or not trade.market or not trade.trade_id:
        raise CanonicalDataValidationError("exchange, market, and trade_id must be non-empty")
    if trade.exchange_timestamp_ms <= 0 or trade.receive_timestamp_ms <= 0:
        raise CanonicalDataValidationError("timestamps must be positive")
    if not math.isfinite(trade.price) or not math.isfinite(trade.quantity):
        raise CanonicalDataValidationError("Non-finite numeric in trade")
    if trade.price <= 0 or trade.quantity <= 0:
        raise CanonicalDataValidationError("price and quantity must be strictly positive")
    if trade.aggressor_side not in ("BUY", "SELL"):
        raise CanonicalDataValidationError(f"Invalid aggressor side: {trade.aggressor_side}")


def validate_canonical_ticker(ticker: CanonicalTicker) -> None:
    """Strict validation for CanonicalTicker."""
    if not ticker.exchange or not ticker.market:
        raise CanonicalDataValidationError("exchange and market must be non-empty")
    if ticker.exchange_timestamp_ms <= 0 or ticker.receive_timestamp_ms <= 0:
        raise CanonicalDataValidationError("timestamps must be positive")
    if not math.isfinite(ticker.last_price) or ticker.last_price <= 0:
        raise CanonicalDataValidationError("last_price must be positive and finite")


def upgrade_v1_dict_to_canonical_orderbook(rec: Mapping[str, Any]) -> CanonicalOrderBook:
    """Upgrades historical v1 dict structure to CanonicalOrderBook v2."""
    bids = tuple((float(p), float(s)) for p, s in rec["bids"])
    asks = tuple((float(p), float(s)) for p, s in rec["asks"])
    return CanonicalOrderBook.from_dict({
        "exchange": rec["exchange"].lower(),
        "market": rec["symbol"].upper(),
        "exchange_timestamp_ms": int(rec["timestamp"]),
        "receive_timestamp_ms": int(rec.get("received_at", rec["timestamp"])),
        "bids": bids,
        "asks": asks,
        "schema_version": "2.0.0",
        "timestamp_semantics": TimestampSemantics.EXCHANGE_EVENT.value,
        "is_snapshot": True,
    })


def upgrade_v1_dict_to_canonical_trade(rec: Mapping[str, Any]) -> CanonicalTrade:
    """Upgrades historical v1 dict structure to CanonicalTrade v2."""
    side = str(rec.get("side", rec.get("aggressor_side", "BUY"))).upper()
    ts = rec["timestamp"]
    trade_id = str(rec.get("trade_id", rec.get("id", f"{ts}_0")))
    return CanonicalTrade.from_dict({
        "exchange": rec["exchange"].lower(),
        "market": rec["symbol"].upper(),
        "trade_id": trade_id,
        "exchange_timestamp_ms": int(ts),
        "receive_timestamp_ms": int(rec.get("received_at", ts)),
        "price": float(rec["price"]),
        "quantity": float(rec.get("quantity", rec.get("size", rec.get("units", 0.0)))),
        "aggressor_side": side,
        "schema_version": "2.0.0",
        "timestamp_semantics": TimestampSemantics.EXCHANGE_TRADE_EXECUTION.value,
    })


def write_canonical_ndjson_zstd(
    path: Path | str,
    records: Sequence[Union[CanonicalOrderBook, CanonicalTrade, CanonicalTicker]],
) -> int:
    """Writes canonical records to a zstandard-compressed ndjson file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    cctx = zstd.ZstdCompressor(level=3)
    count = 0
    with open(target, "wb") as fh:
        with cctx.stream_writer(fh) as compressor:
            for rec in records:
                line = json.dumps(rec.to_dict()) + "\n"
                compressor.write(line.encode("utf-8"))
                count += 1
    return count


def read_canonical_ndjson_zstd(
    path: Path | str,
    record_type: Type[Union[CanonicalOrderBook, CanonicalTrade, CanonicalTicker]],
) -> Iterator[Union[CanonicalOrderBook, CanonicalTrade, CanonicalTicker]]:
    """Streams canonical records from a zstandard-compressed ndjson file."""
    dctx = zstd.ZstdDecompressor()
    with open(path, "rb") as fh:
        with dctx.stream_reader(fh) as decompressor:
            import io
            text_stream = io.TextIOWrapper(decompressor, encoding="utf-8")
            for line in text_stream:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                yield record_type.from_dict(d)
