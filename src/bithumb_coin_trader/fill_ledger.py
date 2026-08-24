"""Append-only, exact-decimal ledger for exchange-reported fills.

The ledger deliberately ignores order-level ``executed_volume``.  That field is
cumulative in Bithumb order snapshots and treating it as a new fill causes
position volume to be counted more than once.  Only entries from the exchange's
``trades`` collection (or an explicitly supplied equivalent collection) are
accepted as accounting events.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence


LEDGER_SCHEMA_VERSION = 1
_MARKET_PATTERN = re.compile(r"^[A-Z0-9]{2,12}-[A-Z0-9]{1,20}$")
_SIDES = {"bid", "ask"}
_RECORD_FIELDS = {
    "schema_version",
    "record_type",
    "trade_id",
    "order_id",
    "client_order_id",
    "market",
    "side",
    "price",
    "volume",
    "funds",
    "paid_fee",
    "executed_at",
    "post_position_volume",
    "post_cost_basis",
    "post_average_cost",
    "realized_pnl",
}


class FillLedgerError(ValueError):
    """Raised when exchange fill data or persisted ledger data is unsafe."""


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """Current weighted-average-cost position for one market."""

    market: str
    volume: Decimal = Decimal("0")
    cost_basis: Decimal = Decimal("0")
    average_cost: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    paid_fees: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class IngestResult:
    appended_trade_ids: tuple[str, ...]
    duplicate_trade_ids: tuple[str, ...]
    positions: Mapping[str, PositionSnapshot]


@dataclass(frozen=True, slots=True)
class _Fill:
    trade_id: str
    order_id: str
    client_order_id: str | None
    market: str
    side: str
    price: Decimal
    volume: Decimal
    funds: Decimal
    paid_fee: Decimal
    executed_at: str


@dataclass(frozen=True, slots=True)
class _Position:
    volume: Decimal = Decimal("0")
    cost_basis: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    paid_fees: Decimal = Decimal("0")


class FillLedger:
    """Persist and replay actual Bithumb fills without floating-point math."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append_order(
        self,
        order: Mapping[str, Any],
        fills: Sequence[Mapping[str, Any]] | None = None,
    ) -> IngestResult:
        """Append new fills from an exchange order snapshot.

        ``fills`` defaults to ``order["trades"]``.  Every fill must contain an
        exchange trade identifier, price, volume, funds, and timestamp.  Fee may
        be supplied per fill; otherwise the order's cumulative ``paid_fee`` is
        allocated only across previously unseen fills.  Replaying the same
        snapshot is idempotent.
        """

        if not isinstance(order, Mapping):
            raise FillLedgerError("order must be a mapping")
        raw_fills = order.get("trades") if fills is None else fills
        if not isinstance(raw_fills, Sequence) or isinstance(raw_fills, (str, bytes)):
            raise FillLedgerError("fills must be a sequence of exchange trade mappings")
        if not raw_fills:
            raise FillLedgerError("at least one actual fill is required")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            _, positions, known = self._load()
            normalized, duplicate_ids = _normalize_order(order, raw_fills, known)

            new_fills: list[_Fill] = []
            for fill in normalized:
                existing = known.get(fill.trade_id)
                if existing is None:
                    new_fills.append(fill)
                    continue
                if _fill_identity(existing) != _fill_identity_from_fill(fill):
                    raise FillLedgerError(
                        f"trade_id {fill.trade_id!r} conflicts with persisted fill"
                    )

            new_records: list[dict[str, Any]] = []
            for fill in new_fills:
                record, updated = _apply_fill(fill, positions.get(fill.market, _Position()))
                positions[fill.market] = updated
                new_records.append(record)

            if new_records:
                payload = b"".join(
                    (
                        json.dumps(record, ensure_ascii=False, sort_keys=True)
                        + "\n"
                    ).encode("utf-8")
                    for record in new_records
                )
                descriptor = os.open(
                    self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600
                )
                try:
                    os.fchmod(descriptor, 0o600)
                    written = os.write(descriptor, payload)
                    if written != len(payload):
                        raise OSError("short write while appending fill ledger")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)

            return IngestResult(
                appended_trade_ids=tuple(fill.trade_id for fill in new_fills),
                duplicate_trade_ids=tuple(duplicate_ids),
                positions=_snapshots(positions),
            )
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def positions(self) -> Mapping[str, PositionSnapshot]:
        """Replay and strictly validate the complete ledger."""

        _, positions, _ = self._load()
        return _snapshots(positions)

    def position(self, market: str) -> PositionSnapshot:
        if not isinstance(market, str) or not _MARKET_PATTERN.fullmatch(market):
            raise FillLedgerError("market has an invalid format")
        return self.positions().get(market, PositionSnapshot(market=market))

    def _load(
        self,
    ) -> tuple[list[dict[str, Any]], dict[str, _Position], dict[str, dict[str, Any]]]:
        if not self.path.exists():
            return [], {}, {}
        records: list[dict[str, Any]] = []
        positions: dict[str, _Position] = {}
        known: dict[str, dict[str, Any]] = {}
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise FillLedgerError(
                        f"invalid ledger JSON at line {line_number}"
                    ) from exc
                _validate_record(record, line_number)
                trade_id = record["trade_id"]
                if trade_id in known:
                    raise FillLedgerError(f"duplicate trade_id in ledger: {trade_id!r}")
                fill = _fill_from_record(record)
                expected, updated = _apply_fill(
                    fill, positions.get(fill.market, _Position())
                )
                if record != expected:
                    raise FillLedgerError(
                        f"derived accounting fields disagree at line {line_number}"
                    )
                positions[fill.market] = updated
                records.append(record)
                known[trade_id] = record
        return records, positions, known


def _normalize_order(
    order: Mapping[str, Any],
    raw_fills: Sequence[Mapping[str, Any]],
    known: Mapping[str, Mapping[str, Any]],
) -> tuple[list[_Fill], list[str]]:
    order_id = _text(order, ("uuid", "order_id"), "order id")
    client_order_id = _optional_text(order.get("client_order_id"), "client_order_id")
    market = _text(order, ("market",), "market")
    if not _MARKET_PATTERN.fullmatch(market):
        raise FillLedgerError("market has an invalid format")
    side = _text(order, ("side",), "side")
    if side not in _SIDES:
        raise FillLedgerError("side must be bid or ask")

    parsed: list[dict[str, Any]] = []
    seen_in_request: set[str] = set()
    for index, raw in enumerate(raw_fills):
        if not isinstance(raw, Mapping):
            raise FillLedgerError(f"fill {index} must be a mapping")
        trade_id = _text(raw, ("uuid", "trade_id"), f"fill {index} trade id")
        if trade_id in seen_in_request:
            raise FillLedgerError(f"duplicate trade_id in order payload: {trade_id!r}")
        seen_in_request.add(trade_id)
        fill_market = raw.get("market", market)
        fill_side = raw.get("side", side)
        if fill_market != market or fill_side != side:
            raise FillLedgerError("fill market and side must match the parent order")
        price = _decimal(raw.get("price"), f"fill {index} price", positive=True)
        volume = _decimal(raw.get("volume"), f"fill {index} volume", positive=True)
        funds = _decimal(raw.get("funds"), f"fill {index} funds", positive=True)
        executed_at = _text(raw, ("created_at", "executed_at"), "executed_at")
        raw_fee = raw.get("paid_fee", raw.get("fee"))
        parsed.append(
            {
                "trade_id": trade_id,
                "price": price,
                "volume": volume,
                "funds": funds,
                "executed_at": executed_at,
                "fee": None
                if raw_fee is None
                else _decimal(raw_fee, f"fill {index} paid_fee", positive=False),
            }
        )

    existing_order_fees = sum(
        (
            _decimal(record["paid_fee"], "persisted paid_fee", positive=False)
            for record in known.values()
            if record["order_id"] == order_id
        ),
        Decimal("0"),
    )
    unseen = [item for item in parsed if item["trade_id"] not in known]
    duplicates = [item["trade_id"] for item in parsed if item["trade_id"] in known]
    explicit = [item["fee"] for item in parsed]
    if any(fee is not None for fee in explicit):
        if not all(fee is not None for fee in explicit):
            raise FillLedgerError("either every fill or no fill must provide paid_fee")
    else:
        order_fee = _decimal(order.get("paid_fee"), "order paid_fee", positive=False)
        remaining_fee = order_fee - existing_order_fees
        if remaining_fee < 0:
            raise FillLedgerError("order paid_fee is below fees already in the ledger")
        for item in parsed:
            if item["trade_id"] in known:
                item["fee"] = _decimal(
                    known[item["trade_id"]]["paid_fee"],
                    "persisted paid_fee",
                    positive=False,
                )
        if not unseen:
            if remaining_fee != 0:
                raise FillLedgerError("fee changed without a new immutable fill event")
        else:
            total_new_funds = sum(
                (item["funds"] for item in unseen), Decimal("0")
            )
            allocated = Decimal("0")
            for item in unseen[:-1]:
                item["fee"] = remaining_fee * item["funds"] / total_new_funds
                allocated += item["fee"]
            unseen[-1]["fee"] = remaining_fee - allocated

    normalized = [
        _Fill(
            trade_id=item["trade_id"],
            order_id=order_id,
            client_order_id=client_order_id,
            market=market,
            side=side,
            price=item["price"],
            volume=item["volume"],
            funds=item["funds"],
            paid_fee=item["fee"],
            executed_at=item["executed_at"],
        )
        for item in parsed
    ]
    return normalized, duplicates


def _apply_fill(fill: _Fill, position: _Position) -> tuple[dict[str, Any], _Position]:
    if fill.side == "bid":
        volume = position.volume + fill.volume
        cost_basis = position.cost_basis + fill.funds + fill.paid_fee
        realized = Decimal("0")
        cumulative_realized = position.realized_pnl
    else:
        if fill.volume > position.volume:
            raise FillLedgerError(
                f"sell fill {fill.trade_id!r} exceeds tracked position volume"
            )
        allocated_cost = position.cost_basis * fill.volume / position.volume
        realized = fill.funds - fill.paid_fee - allocated_cost
        volume = position.volume - fill.volume
        cost_basis = position.cost_basis - allocated_cost
        if volume == 0:
            cost_basis = Decimal("0")
        cumulative_realized = position.realized_pnl + realized
    average = cost_basis / volume if volume else Decimal("0")
    updated = _Position(
        volume=volume,
        cost_basis=cost_basis,
        realized_pnl=cumulative_realized,
        paid_fees=position.paid_fees + fill.paid_fee,
    )
    record = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "record_type": "fill",
        "trade_id": fill.trade_id,
        "order_id": fill.order_id,
        "client_order_id": fill.client_order_id,
        "market": fill.market,
        "side": fill.side,
        "price": _format_decimal(fill.price),
        "volume": _format_decimal(fill.volume),
        "funds": _format_decimal(fill.funds),
        "paid_fee": _format_decimal(fill.paid_fee),
        "executed_at": fill.executed_at,
        "post_position_volume": _format_decimal(volume),
        "post_cost_basis": _format_decimal(cost_basis),
        "post_average_cost": _format_decimal(average),
        "realized_pnl": _format_decimal(realized),
    }
    return record, updated


def _validate_record(record: Any, line_number: int) -> None:
    if not isinstance(record, dict) or set(record) != _RECORD_FIELDS:
        raise FillLedgerError(f"ledger schema mismatch at line {line_number}")
    if record["schema_version"] != LEDGER_SCHEMA_VERSION:
        raise FillLedgerError(f"unsupported ledger version at line {line_number}")
    if record["record_type"] != "fill":
        raise FillLedgerError(f"unsupported ledger record type at line {line_number}")


def _fill_from_record(record: Mapping[str, Any]) -> _Fill:
    market = _required_string(record["market"], "market")
    side = _required_string(record["side"], "side")
    if not _MARKET_PATTERN.fullmatch(market) or side not in _SIDES:
        raise FillLedgerError("persisted market or side is invalid")
    return _Fill(
        trade_id=_required_string(record["trade_id"], "trade_id"),
        order_id=_required_string(record["order_id"], "order_id"),
        client_order_id=_optional_text(record["client_order_id"], "client_order_id"),
        market=market,
        side=side,
        price=_decimal(record["price"], "price", positive=True),
        volume=_decimal(record["volume"], "volume", positive=True),
        funds=_decimal(record["funds"], "funds", positive=True),
        paid_fee=_decimal(record["paid_fee"], "paid_fee", positive=False),
        executed_at=_required_string(record["executed_at"], "executed_at"),
    )


def _fill_identity(record: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        record[field]
        for field in (
            "trade_id",
            "order_id",
            "client_order_id",
            "market",
            "side",
            "price",
            "volume",
            "funds",
            "paid_fee",
            "executed_at",
        )
    )


def _fill_identity_from_fill(fill: _Fill) -> tuple[Any, ...]:
    return (
        fill.trade_id,
        fill.order_id,
        fill.client_order_id,
        fill.market,
        fill.side,
        _format_decimal(fill.price),
        _format_decimal(fill.volume),
        _format_decimal(fill.funds),
        _format_decimal(fill.paid_fee),
        fill.executed_at,
    )


def _snapshots(positions: Mapping[str, _Position]) -> Mapping[str, PositionSnapshot]:
    return {
        market: PositionSnapshot(
            market=market,
            volume=position.volume,
            cost_basis=position.cost_basis,
            average_cost=(
                position.cost_basis / position.volume
                if position.volume
                else Decimal("0")
            ),
            realized_pnl=position.realized_pnl,
            paid_fees=position.paid_fees,
        )
        for market, position in positions.items()
    }


def _text(source: Mapping[str, Any], keys: tuple[str, ...], field: str) -> str:
    for key in keys:
        if key in source:
            return _required_string(source[key], field)
    raise FillLedgerError(f"{field} is required")


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FillLedgerError(f"{field} must be a non-empty string")
    return value


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, field)


def _decimal(value: Any, field: str, *, positive: bool) -> Decimal:
    if isinstance(value, (bool, float)):
        raise FillLedgerError(f"{field} must be an exact decimal string or integer")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FillLedgerError(f"{field} must be a valid decimal") from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed == 0):
        qualifier = "positive" if positive else "non-negative"
        raise FillLedgerError(f"{field} must be finite and {qualifier}")
    return parsed


def _format_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value, "f")
