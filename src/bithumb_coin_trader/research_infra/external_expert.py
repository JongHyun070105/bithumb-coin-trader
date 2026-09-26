"""Local, descriptive contracts for externally published execution/wallet files.

No source schema is assumed to exist until an imported file is profiled. These
helpers preserve every observed column and never infer account ownership or PnL.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decimal(value: str | None) -> Decimal | None:
    if value is None or value.strip() == "":
        return None
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError("Nonfinite numeric value")
    return result


def _first(row: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        if name in row and row[name] != "":
            return row[name]
    lower_row = {k.lower(): v for k, v in row.items()}
    for name in names:
        nl = name.lower()
        if nl in lower_row and lower_row[nl] != "":
            return lower_row[nl]
    return None


def _encoding_and_dialect(path: Path) -> tuple[str, Any]:
    with path.open("rb") as source:
        sample = source.read(64 * 1024)
    encoding = "latin-1"
    decoded = sample.decode(encoding)
    for candidate in ("utf-8-sig", "cp949"):
        try:
            decoded = sample.decode(candidate)
            encoding = candidate
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(decoded, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.get_dialect("excel")
    return encoding, dialect


def iter_csv_rows(path: Path) -> Iterator[dict[str, str]]:
    """Stream rows; keep all columns, including columns unknown to this module."""
    encoding, dialect = _encoding_and_dialect(path)
    with path.open("r", encoding=encoding, newline="") as source:
        reader = csv.DictReader(source, dialect=dialect)
        if not reader.fieldnames or any(not name for name in reader.fieldnames):
            raise ValueError("CSV header is missing or invalid")
        if len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("Duplicate CSV column names are ambiguous")
        for row in reader:
            if None in row:
                raise ValueError("CSV row has more fields than its header")
            yield dict(row)


def _primitive(value: str) -> str:
    if value.lower() in ("true", "false"):
        return "boolean"
    if _timestamp(value) is not None:
        return "timestamp"
    try:
        number = Decimal(value)
    except InvalidOperation:
        return "string"
    if not number.is_finite():
        return "string"
    return "integer" if number == number.to_integral_value() and "." not in value else "decimal"


def _merge_type(old: str | None, new: str) -> str:
    if old is None or old == new:
        return new
    if {old, new} == {"integer", "decimal"}:
        return "decimal"
    return "string"


def profile_csv(path: Path) -> dict[str, Any]:
    """One streaming pass plus a bounded initial sample; exact SHA over file bytes."""
    encoding, dialect = _encoding_and_dialect(path)
    with path.open("r", encoding=encoding, newline="") as source:
        reader = csv.DictReader(source, dialect=dialect)
        columns = reader.fieldnames or []
        if not columns or len(columns) != len(set(columns)) or any(not c for c in columns):
            raise ValueError("Invalid or duplicate CSV headers")
        null_counts = {name: 0 for name in columns}
        types: dict[str, str | None] = {name: None for name in columns}
        row_count = 0
        adjacent_duplicates = 0
        previous: tuple[str, ...] | None = None
        first_ts: datetime | None = None
        last_ts: datetime | None = None
        symbols: set[str] = set()
        symbols_truncated = False
        for row in reader:
            if None in row:
                raise ValueError("CSV row has more fields than its header")
            row_count += 1
            values = tuple(row[name] or "" for name in columns)
            if values == previous:
                adjacent_duplicates += 1
            previous = values
            for name, value in zip(columns, values):
                if not value:
                    null_counts[name] += 1
                else:
                    types[name] = _merge_type(types[name], _primitive(value))
            ts = _timestamp(_first(row, "timestamp", "transactTime", "execTime"))
            if ts is not None:
                first_ts = ts if first_ts is None else min(first_ts, ts)
                last_ts = ts if last_ts is None else max(last_ts, ts)
            symbol = _first(row, "symbol")
            if symbol and len(symbols) < 10_000:
                symbols.add(symbol)
            elif symbol and symbol not in symbols:
                symbols_truncated = True
    primitive_types = {name: types[name] or "null" for name in columns}
    fingerprint_source = json.dumps(
        {"columns": columns, "primitive_types": primitive_types},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return {
        "filename": path.name,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "encoding": encoding,
        "delimiter": dialect.delimiter,
        "columns": columns,
        "primitive_types": primitive_types,
        "row_count": row_count,
        "null_counts": null_counts,
        "first_timestamp": first_ts.isoformat() if first_ts else None,
        "last_timestamp": last_ts.isoformat() if last_ts else None,
        "unique_symbols": sorted(symbols),
        "unique_symbols_truncated": symbols_truncated,
        "adjacent_duplicate_row_count": adjacent_duplicates,
        "exact_duplicate_row_count": None,
        "schema_fingerprint": hashlib.sha256(fingerprint_source).hexdigest(),
    }


@dataclass(frozen=True)
class ExecutionRow:
    timestamp: datetime | None
    symbol: str | None
    side: str | None
    price: Decimal | None
    size: Decimal | None
    order_id: str | None
    trade_match_id: str | None
    execution_id: str | None
    execution_type: str | None
    order_type: str | None
    liquidity: str | None
    fee: Decimal | None
    fee_currency: str | None
    raw_fields: Mapping[str, str]


def normalize_execution(row: Mapping[str, str]) -> ExecutionRow:
    """Map only observed names; retain the complete source row verbatim."""
    return ExecutionRow(
        timestamp=_timestamp(_first(row, "timestamp", "execTime")),
        symbol=_first(row, "symbol"),
        side=_first(row, "side"),
        price=_decimal(_first(row, "lastPx", "price")),
        size=_decimal(_first(row, "lastQty", "size")),
        order_id=_first(row, "orderID"),
        trade_match_id=_first(row, "trdMatchID"),
        execution_id=_first(row, "execID"),
        execution_type=_first(row, "execType"),
        order_type=_first(row, "ordType"),
        liquidity=_first(row, "lastLiquidityInd"),
        fee=_decimal(_first(row, "execComm")),
        fee_currency=_first(row, "settlCurrency"),
        raw_fields=dict(row),
    )


@dataclass(frozen=True)
class WalletEvent:
    timestamp: datetime | None
    event_type: str
    amount: Decimal | None
    currency: str | None
    balance: Decimal | None
    raw_fields: Mapping[str, str]


_WALLET_TYPES = {
    "deposit": "DEPOSIT",
    "withdrawal": "WITHDRAWAL",
    "realisedpnl": "REALIZED_PNL",
    "realizedpnl": "REALIZED_PNL",
    "funding": "FUNDING",
    "fee": "FEE",
    "rebate": "REBATE",
    "transfer": "TRANSFER",
}


def _wallet_timestamp(date_str: str | None, time_str: str | None) -> datetime | None:
    if time_str:
        ts = _timestamp(time_str)
        if ts is not None and ts.year > 2000:
            return ts
    if not date_str:
        return _timestamp(time_str)
    date_clean = date_str.strip()
    if not time_str or time_str.strip() == "":
        try:
            return datetime.fromisoformat(date_clean).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    time_clean = time_str.strip()
    if ":" in time_clean:
        parts = time_clean.split(":")
        if len(parts) == 2:
            try:
                minute = int(parts[0])
                sec_part = parts[1]
                iso_candidate = f"{date_clean}T00:{minute:02d}:{sec_part}"
                return datetime.fromisoformat(iso_candidate).replace(tzinfo=timezone.utc)
            except Exception:
                pass
        elif len(parts) == 3:
            try:
                iso_candidate = f"{date_clean}T{time_clean}"
                return datetime.fromisoformat(iso_candidate).replace(tzinfo=timezone.utc)
            except Exception:
                pass
    try:
        return datetime.fromisoformat(date_clean).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def normalize_wallet(row: Mapping[str, str]) -> WalletEvent:
    observed_type = _first(row, "transactType", "type", "execType")
    event_type = _WALLET_TYPES.get((observed_type or "").lower(), "UNKNOWN")
    date_str = _first(row, "date")
    time_str = _first(row, "timestamp", "transactTime")
    ts = _wallet_timestamp(date_str, time_str)
    return WalletEvent(
        timestamp=ts,
        event_type=event_type,
        amount=_decimal(_first(row, "amount")),
        currency=_first(row, "currency"),
        balance=_decimal(_first(row, "walletBalance", "balance")),
        raw_fields=dict(row),
    )


def wallet_flow_totals(events: Iterable[WalletEvent]) -> dict[tuple[str, str], Decimal]:
    """Keep capital flows, PnL, funding and fees in disjoint currency buckets."""
    totals: dict[tuple[str, str], Decimal] = {}
    for event in events:
        if event.amount is None or event.currency is None:
            continue
        key = (event.currency, event.event_type)
        totals[key] = totals.get(key, Decimal(0)) + event.amount
    return totals


@dataclass(frozen=True)
class TapeMatch:
    classification: str
    mismatched_fields: tuple[str, ...]
    ownership_verified: bool = False


def verify_public_trade(
    execution: ExecutionRow,
    public_trade: Mapping[str, str] | None,
    *,
    history_complete: bool = False,
) -> TapeMatch:
    """Compare public trade evidence; a match never verifies account ownership."""
    if not execution.trade_match_id:
        return TapeMatch("SOURCE_FIELD_MISSING", ("trdMatchID",))
    if public_trade is None or public_trade.get("trdMatchID") != execution.trade_match_id:
        status = "PUBLIC_RECORD_NOT_FOUND" if history_complete else "INSUFFICIENT_PUBLIC_HISTORY"
        return TapeMatch(status, ())
    expected = {
        "timestamp": execution.timestamp,
        "symbol": execution.symbol,
        "side": execution.side,
        "price": execution.price,
        "size": execution.size,
    }
    missing = tuple(name for name, value in expected.items() if value is None or public_trade.get(name) in (None, ""))
    if missing:
        return TapeMatch("SOURCE_FIELD_MISSING", missing)
    actual = {
        "timestamp": _timestamp(public_trade["timestamp"]),
        "symbol": public_trade["symbol"],
        "side": public_trade["side"],
        "price": _decimal(public_trade["price"]),
        "size": _decimal(public_trade["size"]),
    }
    mismatches = tuple(name for name in expected if expected[name] != actual[name])
    return TapeMatch("PARTIAL_PUBLIC_MATCH" if mismatches else "EXACT_PUBLIC_MATCH", mismatches)


def verify_public_tape_sample(
    executions: Iterable[ExecutionRow],
    public_trades: Iterable[Mapping[str, str]],
    *,
    history_complete: bool = False,
) -> list[TapeMatch]:
    """Match a bounded caller-selected sample by public trade ID.

    ``history_complete`` is only valid when the caller has proven the queried
    time/symbol range is complete. An absent record otherwise stays unknown.
    """
    by_id = {row["trdMatchID"]: row for row in public_trades if row.get("trdMatchID")}
    return [verify_public_trade(row, by_id.get(row.trade_match_id or ""), history_complete=history_complete)
            for row in executions]


def dashboard_summary(source_manifest: Mapping[str, Any] | None) -> dict[str, Any]:
    """A row-free dashboard payload; caller supplies only source metadata."""
    files = source_manifest.get("source_files", []) if source_manifest else []
    return {
        "title": "External Expert Dataset",
        "dataset_id": "external-bitmex-trader-2018-2021",
        "source": "Publisher-attributed BitMEX public release",
        "claimed_coverage": "2018-03 through 2021-12",
        "source_files": [item["original_filename"] for item in files],
        "row_count": sum(item["row_count"] for item in files if item["row_count"] is not None) if files else None,
        "source_hashes": [item["sha256"] for item in files],
        "public_tape_verification": "NOT_RUN",
        "author_identity": "NOT_INDEPENDENTLY_VERIFIED",
        "research_role": "HYPOTHESIS_GENERATION_ONLY",
        "research_status": "NOT_STARTED",
        "raw_rows_exposed": False,
    }


@dataclass(frozen=True)
class ReconstructedOrder:
    order_id: str
    first_fill_timestamp: datetime
    last_fill_timestamp: datetime
    symbol: str
    side: str
    quantity: Decimal
    vwap: Decimal
    fill_count: int
    liquidity_counts: Mapping[str, int]
    fees_by_currency: Mapping[str, Decimal]


def group_fills_by_order(fills: Iterable[ExecutionRow]) -> list[ReconstructedOrder]:
    """Group identifiable fills; sorting is stable and no position is inferred."""
    groups: dict[str, list[ExecutionRow]] = {}
    for fill in fills:
        if fill.execution_type not in (None, "Trade"):
            continue
        if (not fill.order_id or fill.timestamp is None or not fill.symbol
                or fill.side not in ("Buy", "Sell") or fill.price is None
                or fill.size is None or fill.size <= 0):
            raise ValueError("Fill lacks orderID, timestamp, symbol, side, positive size, or price")
        groups.setdefault(fill.order_id, []).append(fill)
    orders = []
    for order_id, rows in groups.items():
        rows.sort(key=lambda row: row.timestamp or datetime.min.replace(tzinfo=timezone.utc))
        symbols = {row.symbol for row in rows}
        sides = {row.side for row in rows}
        if len(symbols) != 1 or len(sides) != 1:
            raise ValueError(f"Inconsistent symbol or side in order {order_id}")
        quantity = sum((row.size or Decimal(0) for row in rows), Decimal(0))
        liquidity: dict[str, int] = {}
        fees: dict[str, Decimal] = {}
        for row in rows:
            liquidity[row.liquidity or "UNKNOWN"] = liquidity.get(row.liquidity or "UNKNOWN", 0) + 1
            if row.fee is not None:
                currency = row.fee_currency or "UNSPECIFIED"
                fees[currency] = fees.get(currency, Decimal(0)) + row.fee
        orders.append(ReconstructedOrder(
            order_id=order_id,
            first_fill_timestamp=rows[0].timestamp,  # type: ignore[arg-type]
            last_fill_timestamp=rows[-1].timestamp,  # type: ignore[arg-type]
            symbol=rows[0].symbol or "",
            side=rows[0].side or "",
            quantity=quantity,
            vwap=sum(((row.price or Decimal(0)) * (row.size or Decimal(0)) for row in rows), Decimal(0)) / quantity,
            fill_count=len(rows),
            liquidity_counts=liquidity,
            fees_by_currency=fees,
        ))
    return sorted(orders, key=lambda order: (order.first_fill_timestamp, order.order_id))


@dataclass(frozen=True)
class PositionTransition:
    timestamp: datetime
    symbol: str
    order_id: str
    quantity_before: Decimal
    quantity_after: Decimal
    action: str
    realized_pnl: None = None  # Contract valuation and accounting are not established.


def reconstruct_position_transitions(
    orders: Iterable[ReconstructedOrder],
    *,
    initial_positions: Mapping[str, Decimal],
    quantity_semantics: str,
) -> list[PositionTransition]:
    """Track signed quantities only, with explicit initial state and units.

    Caller must establish instrument-specific quantity semantics. No equity,
    realized/unrealized PnL, or deposit-funded return is computed here.
    """
    if not quantity_semantics.strip():
        raise ValueError("Explicit instrument quantity semantics are required")
    positions = dict(initial_positions)
    transitions = []
    previous: tuple[datetime, str] | None = None
    for order in orders:
        current = (order.first_fill_timestamp, order.order_id)
        if previous is not None and current < previous:
            raise ValueError("Orders must be supplied in chronological order")
        previous = current
        if order.symbol not in positions:
            raise ValueError(f"Initial position is unknown for {order.symbol}")
        before = positions[order.symbol]
        delta = order.quantity if order.side == "Buy" else -order.quantity
        after = before + delta
        if before == 0:
            action = "OPEN_LONG" if after > 0 else "OPEN_SHORT"
        elif after == 0:
            action = "CLOSE"
        elif (before > 0) != (after > 0):
            action = "REVERSE"
        elif abs(after) > abs(before):
            action = "SCALE_IN"
        else:
            action = "REDUCE"
        positions[order.symbol] = after
        transitions.append(PositionTransition(
            timestamp=order.last_fill_timestamp,
            symbol=order.symbol,
            order_id=order.order_id,
            quantity_before=before,
            quantity_after=after,
            action=action,
        ))
    return transitions
