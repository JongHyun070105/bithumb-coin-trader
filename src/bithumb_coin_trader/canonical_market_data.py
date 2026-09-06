"""Canonical Market Data Model, Validator, and Schema Contracts (P2 - P2.4).

Provides an exchange-agnostic, versioned, strictly-validated representation of:
- CanonicalOrderBook
- CanonicalTrade
- CanonicalTicker
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
    receive_timestamp_ms: int | None
    bids: tuple[tuple[float, float], ...]  # ((price, size), ...) sorted high to low
    asks: tuple[tuple[float, float], ...]  # ((price, size), ...) sorted low to high
    schema_version: str = "2.0.0"
    exchange_timestamp_semantics: TimestampSemantics = TimestampSemantics.EXCHANGE_EVENT
    receive_monotonic_ns: int | None = None
    sequence_id: int | None = None
    is_snapshot: bool = True
    timestamp_semantics: TimestampSemantics = TimestampSemantics.EXCHANGE_EVENT

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
        sem = self.exchange_timestamp_semantics or self.timestamp_semantics
        sem_val = sem.value if hasattr(sem, "value") else str(sem)
        return {
            "exchange": self.exchange,
            "market": self.market,
            "exchange_timestamp_ms": self.exchange_timestamp_ms,
            "receive_timestamp_ms": self.receive_timestamp_ms,
            "bids": [list(item) for item in self.bids],
            "asks": [list(item) for item in self.asks],
            "schema_version": self.schema_version,
            "exchange_timestamp_semantics": sem_val,
            "timestamp_semantics": sem_val,
            "receive_monotonic_ns": self.receive_monotonic_ns,
            "sequence_id": self.sequence_id,
            "is_snapshot": self.is_snapshot,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], validate: bool = True) -> CanonicalOrderBook:
        bids = tuple((float(p), float(s)) for p, s in d["bids"])
        asks = tuple((float(p), float(s)) for p, s in d["asks"])
        sem_val = d.get("exchange_timestamp_semantics", d.get("timestamp_semantics", TimestampSemantics.EXCHANGE_EVENT.value))
        semantics = TimestampSemantics(sem_val)
        ob = cls(
            exchange=str(d["exchange"]),
            market=str(d["market"]),
            exchange_timestamp_ms=int(d["exchange_timestamp_ms"]),
            receive_timestamp_ms=int(d["receive_timestamp_ms"]) if d.get("receive_timestamp_ms") is not None else None,
            bids=bids,
            asks=asks,
            schema_version=str(d.get("schema_version", "2.0.0")),
            exchange_timestamp_semantics=semantics,
            timestamp_semantics=semantics,
            receive_monotonic_ns=int(d["receive_monotonic_ns"]) if d.get("receive_monotonic_ns") is not None else None,
            sequence_id=int(d["sequence_id"]) if d.get("sequence_id") is not None else None,
            is_snapshot=bool(d.get("is_snapshot", True)),
        )
        if validate:
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
    exchange_timestamp_semantics: TimestampSemantics = TimestampSemantics.EXCHANGE_TRADE_EXECUTION
    receive_monotonic_ns: int | None = None
    timestamp_semantics: TimestampSemantics = TimestampSemantics.EXCHANGE_TRADE_EXECUTION

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
        sem = self.exchange_timestamp_semantics or self.timestamp_semantics
        sem_val = sem.value if hasattr(sem, "value") else str(sem)
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
            "exchange_timestamp_semantics": sem_val,
            "timestamp_semantics": sem_val,
            "receive_monotonic_ns": self.receive_monotonic_ns,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> CanonicalTrade:
        sem_val = d.get("exchange_timestamp_semantics", d.get("timestamp_semantics", TimestampSemantics.EXCHANGE_TRADE_EXECUTION.value))
        semantics = TimestampSemantics(sem_val)
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
            exchange_timestamp_semantics=semantics,
            timestamp_semantics=semantics,
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
    exchange_timestamp_semantics: TimestampSemantics = TimestampSemantics.EXCHANGE_PUBLICATION
    timestamp_semantics: TimestampSemantics = TimestampSemantics.EXCHANGE_PUBLICATION
    receive_monotonic_ns: int | None = None

    def to_dict(self) -> dict[str, Any]:
        sem = self.timestamp_semantics
        sem_val = sem.value if hasattr(sem, "value") else str(sem)
        exch_sem = self.exchange_timestamp_semantics
        exch_sem_val = exch_sem.value if hasattr(exch_sem, "value") else str(exch_sem)
        return {
            "exchange": self.exchange,
            "market": self.market,
            "exchange_timestamp_ms": self.exchange_timestamp_ms,
            "receive_timestamp_ms": self.receive_timestamp_ms,
            "last_price": self.last_price,
            "volume_24h": self.volume_24h,
            "schema_version": self.schema_version,
            "exchange_timestamp_semantics": exch_sem_val,
            "timestamp_semantics": sem_val,
            "receive_monotonic_ns": self.receive_monotonic_ns,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> CanonicalTicker:
        exch_sem_val = d.get("exchange_timestamp_semantics") or d.get("timestamp_semantics", TimestampSemantics.EXCHANGE_PUBLICATION.value)
        ticker = cls(
            exchange=str(d["exchange"]),
            market=str(d["market"]),
            exchange_timestamp_ms=int(d["exchange_timestamp_ms"]),
            receive_timestamp_ms=int(d["receive_timestamp_ms"]),
            last_price=float(d["last_price"]),
            volume_24h=float(d["volume_24h"]) if d.get("volume_24h") is not None else None,
            schema_version=str(d.get("schema_version", "2.0.0")),
            exchange_timestamp_semantics=TimestampSemantics(exch_sem_val),
            timestamp_semantics=TimestampSemantics(d.get("timestamp_semantics", TimestampSemantics.EXCHANGE_PUBLICATION.value)),
            receive_monotonic_ns=int(d["receive_monotonic_ns"]) if d.get("receive_monotonic_ns") is not None else None,
        )
        validate_canonical_ticker(ticker)
        return ticker


def validate_canonical_orderbook(ob: CanonicalOrderBook) -> None:
    """Strict validation for CanonicalOrderBook."""
    if not ob.exchange or not ob.market:
        raise CanonicalDataValidationError("exchange and market must be non-empty strings")
    if ob.exchange_timestamp_ms <= 0:
        raise CanonicalDataValidationError("exchange timestamp must be strictly positive")
    if ob.receive_timestamp_ms is not None and ob.receive_timestamp_ms <= 0:
        raise CanonicalDataValidationError("receive timestamp must be strictly positive if provided")

    # Future receive check: exchange event should not be ridiculously far in the future
    if ob.receive_timestamp_ms is not None and ob.exchange_timestamp_ms > ob.receive_timestamp_ms + 10_000:
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


def raw_record_to_canonical(
    record: Mapping[str, Any],
    exchange_override: str | None = None,
) -> Union[CanonicalOrderBook, CanonicalTrade, CanonicalTicker]:
    """Converts a serialized raw microstructure record into canonical model.

    Enforces:
    - Real raw envelope keys: exchange, stream, market, exchange_ts, local_recv_ts, local_recv_monotonic_ns, payload
    - Normalized exchange_ts ISO 8601 parsing (no erroneous payload timestamp reinterpretation)
    - Strict local_recv_ts validation (malformed string -> rejects record)
    - Automatic stream dispatch: orderbook, trade, ticker
    """
    stream = str(record.get("stream", "")).lower()
    payload = record.get("payload", record)
    exch = str(exchange_override or record.get("exchange", "")).lower()
    market = str(record.get("market", "")).replace("_", "-").upper()

    if not stream or stream in ("unknown", ""):
        if "bids" in payload or "asks" in payload or "orderbook_units" in payload or "bids" in record or "asks" in record:
            stream = "orderbook"
        elif "trade_id" in payload or "trade_price" in payload or "trade_volume" in payload or "units_traded" in payload or "trade_id" in record:
            stream = "trade"
        elif "last_price" in payload or "last_price" in record:
            stream = "ticker"
        else:
            stream = str(payload.get("type", "")).lower()

    if not market or market in ("UNKNOWN", ""):
        market = str(payload.get("code", payload.get("market", payload.get("s", "")))).replace("_", "-").upper()

    # 1. Parse local_recv_ts (strict validation: required in raw envelope contract)
    local_recv_ms = None
    if "local_recv_ts" in record and record["local_recv_ts"] is not None:
        raw_val = record["local_recv_ts"]
        try:
            if isinstance(raw_val, (int, float)):
                local_recv_ms = int(raw_val)
            else:
                try:
                    local_recv_ms = int(raw_val)
                except ValueError:
                    dt = datetime.fromisoformat(str(raw_val))
                    local_recv_ms = int(dt.timestamp() * 1000)
        except Exception as err:
            raise CanonicalDataValidationError(f"MALFORMED_LOCAL_RECV_TS: {raw_val}") from err
    elif "receive_timestamp_ms" in record and record["receive_timestamp_ms"] is not None:
        local_recv_ms = int(record["receive_timestamp_ms"])
    elif "local_receive_ms" in record and record["local_receive_ms"] is not None:
        local_recv_ms = int(record["local_receive_ms"])

    if local_recv_ms is None:
        raise CanonicalDataValidationError("MISSING_LOCAL_RECEIVE_TIMESTAMP: local_recv_ts is required")

    monotonic_ns = record.get("local_recv_monotonic_ns") or record.get("receive_monotonic_ns")
    if monotonic_ns is not None:
        try:
            monotonic_ns = int(monotonic_ns)
        except (ValueError, TypeError):
            monotonic_ns = None

    # 2. Parse exchange_ts (prefer top-level normalized ISO string or numeric ms)
    exch_ts_ms = None
    if "exchange_ts" in record and record["exchange_ts"] is not None:
        raw_val = record["exchange_ts"]
        try:
            if isinstance(raw_val, (int, float)):
                exch_ts_ms = int(raw_val)
            else:
                try:
                    exch_ts_ms = int(raw_val)
                except ValueError:
                    dt = datetime.fromisoformat(str(raw_val))
                    exch_ts_ms = int(dt.timestamp() * 1000)
        except Exception as err:
            raise CanonicalDataValidationError(f"MALFORMED_EXCHANGE_TS: {raw_val}") from err

    # Fallback to payload timestamps only if exchange_ts was not provided
    if exch_ts_ms is None:
        if exch == "bithumb":
            raw_ts = payload.get("trade_timestamp") or payload.get("timestamp", 0)
            if raw_ts:
                raw_f = float(raw_ts)
                if raw_f > 1e14:  # microseconds
                    exch_ts_ms = int(raw_f / 1000.0)
                elif raw_f > 1e11:  # milliseconds
                    exch_ts_ms = int(raw_f)
                elif raw_f > 1e9:  # seconds
                    exch_ts_ms = int(raw_f * 1000.0)
                else:  # small integer synthetic ms
                    exch_ts_ms = int(raw_f)
        elif exch == "binance":
            data = payload.get("data", payload)
            raw_ts = data.get("E", payload.get("E", 0))
            if raw_ts:
                exch_ts_ms = int(raw_ts)
        elif exch == "upbit":
            raw_ts = payload.get("trade_timestamp") or payload.get("timestamp", 0)
            if raw_ts:
                raw_f = float(raw_ts)
                if raw_f > 1e14:
                    exch_ts_ms = int(raw_f / 1000.0)
                elif raw_f > 1e11:
                    exch_ts_ms = int(raw_f)
                elif raw_f > 1e9:
                    exch_ts_ms = int(raw_f * 1000.0)
                else:
                    exch_ts_ms = int(raw_f)

    if exch_ts_ms is None or exch_ts_ms <= 0:
        exch_ts_ms = local_recv_ms or 0

    if exch_ts_ms <= 0:
        raise CanonicalDataValidationError("Missing or non-positive exchange timestamp")

    # 3. Stream Dispatch
    if stream == "orderbook":
        raw_bids: list[tuple[float, float]] = []
        raw_asks: list[tuple[float, float]] = []

        if exch == "bithumb":
            units = payload.get("orderbook_units", [])
            if units:
                for u in units:
                    raw_bids.append((float(u["bid_price"]), float(u["bid_size"])))
                    raw_asks.append((float(u["ask_price"]), float(u["ask_size"])))
            elif "bids" in payload and "asks" in payload:
                for b in payload["bids"]:
                    p_val = b[0] if isinstance(b, (list, tuple)) else b["price"]
                    q_val = b[1] if isinstance(b, (list, tuple)) else b["quantity"]
                    raw_bids.append((float(p_val), float(q_val)))
                for a in payload["asks"]:
                    p_val = a[0] if isinstance(a, (list, tuple)) else a["price"]
                    q_val = a[1] if isinstance(a, (list, tuple)) else a["quantity"]
                    raw_asks.append((float(p_val), float(q_val)))
        elif exch == "binance":
            data = payload.get("data", payload)
            for b in data.get("b", []):
                raw_bids.append((float(b[0]), float(b[1])))
            for a in data.get("a", []):
                raw_asks.append((float(a[0]), float(a[1])))
        elif exch == "upbit":
            units = payload.get("orderbook_units", [])
            for u in units:
                raw_bids.append((float(u["bid_price"]), float(u["bid_size"])))
                raw_asks.append((float(u["ask_price"]), float(u["ask_size"])))

        if not raw_bids or not raw_asks:
            raise CanonicalDataValidationError(f"Empty bids or asks for orderbook: bids={len(raw_bids)}, asks={len(raw_asks)}")

        # P12: Raw data invariant validation (no silent repair)
        seen_bids: set[float] = set()
        for i, (p, s) in enumerate(raw_bids):
            if p in seen_bids:
                raise CanonicalDataValidationError(f"ORDERBOOK_INVARIANT_VIOLATION: duplicate bid price level {p}")
            seen_bids.add(p)
            if i > 0 and p >= raw_bids[i - 1][0]:
                raise CanonicalDataValidationError(f"UNSORTED_BIDS: bids must be sorted descending, got {p} >= {raw_bids[i-1][0]}")

        seen_asks: set[float] = set()
        for i, (p, s) in enumerate(raw_asks):
            if p in seen_asks:
                raise CanonicalDataValidationError(f"ORDERBOOK_INVARIANT_VIOLATION: duplicate ask price level {p}")
            seen_asks.add(p)
            if i > 0 and p <= raw_asks[i - 1][0]:
                raise CanonicalDataValidationError(f"UNSORTED_ASKS: asks must be sorted ascending, got {p} <= {raw_asks[i-1][0]}")

        if raw_bids[0][0] >= raw_asks[0][0]:
            raise CanonicalDataValidationError(f"ORDERBOOK_INVARIANT_VIOLATION: crossed book best_bid {raw_bids[0][0]} >= best_ask {raw_asks[0][0]}")

        sorted_bids = tuple(raw_bids)
        sorted_asks = tuple(raw_asks)

        rec_schema_ver = str(record.get("schema_version", "2.0.0"))
        ob = CanonicalOrderBook(
            exchange=exch,
            market=market,
            exchange_timestamp_ms=exch_ts_ms,
            receive_timestamp_ms=local_recv_ms,
            bids=sorted_bids,
            asks=sorted_asks,
            schema_version=rec_schema_ver,
            exchange_timestamp_semantics=TimestampSemantics.EXCHANGE_EVENT,
            timestamp_semantics=TimestampSemantics.EXCHANGE_EVENT,
            receive_monotonic_ns=monotonic_ns,
            is_snapshot=True,
        )
        validate_canonical_orderbook(ob)
        return ob

    elif stream == "trade":
        trade_id = None
        price = 0.0
        quantity = 0.0
        side = "BUY"

        if exch == "bithumb":
            t_val = payload.get("trade_id") or payload.get("sequential_id") or payload.get("cont_no")
            trade_id = str(t_val) if t_val is not None and str(t_val).strip() else None
            price = float(payload.get("trade_price") or payload.get("price", 0.0))
            quantity = float(payload.get("trade_volume") or payload.get("volume") or payload.get("units_traded", 0.0))
            ask_bid = str(payload.get("ask_bid", "")).upper()
            side = "SELL" if ask_bid in ("ASK", "SELL") else "BUY"
        elif exch == "binance":
            data = payload.get("data", payload)
            t_val = data.get("t") or data.get("trade_id")
            trade_id = str(t_val) if t_val is not None and str(t_val).strip() else None
            price = float(data.get("p") or data.get("price", 0.0))
            quantity = float(data.get("q") or data.get("quantity", 0.0))
            is_buyer_maker = bool(data.get("m", False))
            side = "SELL" if is_buyer_maker else "BUY"
        elif exch == "upbit":
            t_val = payload.get("sequential_id") or payload.get("trade_id")
            trade_id = str(t_val) if t_val is not None and str(t_val).strip() else None
            price = float(payload.get("trade_price") or payload.get("price", 0.0))
            quantity = float(payload.get("trade_volume") or payload.get("volume", 0.0))
            ask_bid = str(payload.get("ask_bid", "")).upper()
            side = "SELL" if ask_bid in ("ASK", "SELL") else "BUY"
        else:
            t_val = payload.get("trade_id")
            trade_id = str(t_val) if t_val is not None and str(t_val).strip() else None
            price = float(payload.get("price", 0.0))
            quantity = float(payload.get("quantity", 0.0))
            side = str(payload.get("aggressor_side", "BUY")).upper()

        if not trade_id:
            raise CanonicalDataValidationError("MISSING_TRADE_ID: trade payload lacks valid exchange trade identifier")

        rec_schema_ver = str(record.get("schema_version", "2.0.0"))
        trade = CanonicalTrade(
            exchange=exch,
            market=market,
            trade_id=trade_id,
            exchange_timestamp_ms=exch_ts_ms,
            receive_timestamp_ms=local_recv_ms,
            price=price,
            quantity=quantity,
            aggressor_side=side,
            schema_version=rec_schema_ver,
            exchange_timestamp_semantics=TimestampSemantics.EXCHANGE_TRADE_EXECUTION,
            timestamp_semantics=TimestampSemantics.EXCHANGE_TRADE_EXECUTION,
            receive_monotonic_ns=monotonic_ns,
        )
        validate_canonical_trade(trade)
        return trade

    elif stream == "ticker":
        last_price = float(payload.get("trade_price", payload.get("last_price", payload.get("c", payload.get("closing_price", 0.0)))))
        rec_schema_ver = str(record.get("schema_version", "2.0.0"))
        ticker = CanonicalTicker(
            exchange=exch,
            market=market,
            exchange_timestamp_ms=exch_ts_ms,
            receive_timestamp_ms=local_recv_ms,
            last_price=last_price,
            volume_24h=float(payload.get("acc_trade_volume_24h", 0.0)) if "acc_trade_volume_24h" in payload else None,
            schema_version=rec_schema_ver,
            exchange_timestamp_semantics=TimestampSemantics.EXCHANGE_PUBLICATION,
            timestamp_semantics=TimestampSemantics.EXCHANGE_PUBLICATION,
            receive_monotonic_ns=monotonic_ns,
        )
        validate_canonical_ticker(ticker)
        return ticker

    else:
        raise CanonicalDataValidationError(f"UNSUPPORTED_STREAM: {stream}")
