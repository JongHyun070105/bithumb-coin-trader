"""Deterministic Taker Execution Simulator for Microstructure Research.

This module provides high-precision, deterministic simulation of market/taker
order execution against Level 2 order book depth with latency delays,
multi-level depth consumption, fees, spread crossing, and slippage calculations.

All operations are offline and have zero live network/AWS dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from decimal import Decimal, ROUND_DOWN
from typing import Any, Sequence

from .canonical_market_data import CanonicalOrderBook


class ExecutionSimulatorError(Exception):
    """Base exception for execution simulation errors."""


class AmbiguousOrderModeError(ExecutionSimulatorError, ValueError):
    """Raised when both quote notional and base quantity are specified or neither is."""


class InsufficientFutureDataError(ExecutionSimulatorError):
    """Raised when future orderbook snapshots are missing during latency simulation."""


class StaleOrderBookError(ExecutionSimulatorError):
    """Raised when orderbook snapshot exceeds maximum allowable staleness."""


class InvalidOrderBookError(ExecutionSimulatorError):
    """Raised when order book violates sorting or price invariants."""


class CrossedBookError(InvalidOrderBookError):
    """Raised when the order book is locked or crossed (best_bid >= best_ask)."""


class EmptyBookError(InvalidOrderBookError):
    """Raised when the order book has no bids or no asks."""


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    """Represents an immutable snapshot of an L2 order book."""

    timestamp: datetime | float | int
    bids: tuple[tuple[float, float], ...]  # [(price, size), ...] sorted desc by price
    asks: tuple[tuple[float, float], ...]  # [(price, size), ...] sorted asc by price
    market: str = "KRW-BTC"
    validate: bool = True

    def __post_init__(self) -> None:
        if not self.validate:
            return

        if not self.bids or not self.asks:
            raise EmptyBookError("Orderbook must have at least one bid and one ask.")

        # Validate bids: non-empty, strictly positive, sorted descending
        prev_bid = float("inf")
        for p, s in self.bids:
            if p <= 0 or s <= 0:
                raise InvalidOrderBookError(f"Bid price and size must be positive: price={p}, size={s}")
            if p > prev_bid:
                raise InvalidOrderBookError(f"Bids must be sorted descending by price: {p} > {prev_bid}")
            prev_bid = p

        # Validate asks: non-empty, strictly positive, sorted ascending
        prev_ask = float("-inf")
        for p, s in self.asks:
            if p <= 0 or s <= 0:
                raise InvalidOrderBookError(f"Ask price and size must be positive: price={p}, size={s}")
            if p < prev_ask:
                raise InvalidOrderBookError(f"Asks must be sorted ascending by price: {p} < {prev_ask}")
            prev_ask = p

        # Check crossed book
        best_bid = self.bids[0][0]
        best_ask = self.asks[0][0]
        if best_bid >= best_ask:
            raise CrossedBookError(
                f"Crossed or locked order book detected: best_bid={best_bid} >= best_ask={best_ask}"
            )

    @property
    def best_bid(self) -> float:
        return self.bids[0][0]

    @property
    def best_ask(self) -> float:
        return self.asks[0][0]

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def spread_bps(self) -> float:
        mid = self.mid_price
        return (self.spread / mid) * 10_000.0 if mid > 0 else 0.0

    @property
    def total_bid_depth_krw(self) -> float:
        return sum(p * s for p, s in self.bids)

    @property
    def total_ask_depth_krw(self) -> float:
        return sum(p * s for p, s in self.asks)

    @property
    def total_bid_size(self) -> float:
        return sum(s for _, s in self.bids)

    @property
    def total_ask_size(self) -> float:
        return sum(s for _, s in self.asks)


@dataclass(frozen=True, slots=True)
class MarketOrderRequest:
    """Request for deterministic market (taker) order execution."""

    timestamp: datetime | float | int
    side: str  # "BUY" or "SELL"
    requested_amount_krw: float | None = None
    requested_quantity_btc: float | None = None
    fee_rate: float = 0.0004  # 4 bps default
    latency_delay_ms: float = 0.0
    allow_partial: bool = True
    market: str = "KRW-BTC"

    def __post_init__(self) -> None:
        side_norm = self.side.upper()
        if side_norm not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side: {self.side}. Must be 'BUY' or 'SELL'.")
        if self.requested_amount_krw is None and self.requested_quantity_btc is None:
            raise AmbiguousOrderModeError("Must specify either requested_amount_krw or requested_quantity_btc.")
        if self.requested_amount_krw is not None and self.requested_quantity_btc is not None:
            raise AmbiguousOrderModeError("Cannot specify both requested_amount_krw and requested_quantity_btc.")
        if self.requested_amount_krw is not None and self.requested_amount_krw <= 0:
            raise ValueError("requested_amount_krw must be strictly positive.")
        if self.requested_quantity_btc is not None and self.requested_quantity_btc <= 0:
            raise ValueError("requested_quantity_btc must be strictly positive.")
        if self.fee_rate < 0:
            raise ValueError("fee_rate must be non-negative.")
        if self.latency_delay_ms < 0:
            raise ValueError("latency_delay_ms must be non-negative.")


@dataclass(frozen=True, slots=True)
class ExecutionFill:
    """A fill slice at a specific order book price level."""

    level_index: int
    price: float
    size: float
    notional_krw: float


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Deterministic result of a taker execution."""

    request: MarketOrderRequest
    order_timestamp: datetime | float | int
    fill_timestamp: datetime | float | int
    status: str  # "FILLED", "PARTIALLY_FILLED", "REJECTED"
    side: str
    filled_quantity: float
    filled_amount_krw: float
    unfilled_quantity: float
    unfilled_amount_krw: float
    vwap_price: float
    mid_price_at_order: float
    top_of_book_at_order: float
    mid_price_at_fill: float
    top_of_book_at_fill: float
    fee_rate: float
    fee_paid_krw: float
    slippage_vs_mid_bps: float
    slippage_vs_top_bps: float
    adverse_selection_bps: float
    latency_delay_ms: float
    fills: tuple[ExecutionFill, ...] = ()
    rejection_reason: str = ""
    half_spread_cost_krw: float = 0.0
    depth_slippage_cost_krw: float = 0.0
    latency_slippage_cost_krw: float = 0.0
    total_cost_krw: float = 0.0

    @property
    def is_filled(self) -> bool:
        return self.status == "FILLED"

    @property
    def is_partial(self) -> bool:
        return self.status == "PARTIALLY_FILLED"

    @property
    def is_rejected(self) -> bool:
        return self.status == "REJECTED"


def _timestamp_to_seconds(ts: datetime | float | int) -> float:
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.timestamp()
    return float(ts)


def _to_snapshot(ob: OrderBookSnapshot | CanonicalOrderBook) -> OrderBookSnapshot:
    if isinstance(ob, OrderBookSnapshot):
        return ob
    if isinstance(ob, CanonicalOrderBook):
        return OrderBookSnapshot(
            timestamp=ob.receive_timestamp_ms / 1000.0,
            bids=ob.bids,
            asks=ob.asks,
            market=ob.market,
            validate=False,
        )
    raise TypeError(f"Expected OrderBookSnapshot or CanonicalOrderBook, got {type(ob)}")


class DeterministicTakerSimulator:
    """Engine for simulating market taker orders against order book snapshots."""

    @staticmethod
    def execute_order(
        request: MarketOrderRequest,
        orderbook: OrderBookSnapshot | CanonicalOrderBook,
        order_time_book: OrderBookSnapshot | CanonicalOrderBook | None = None,
    ) -> ExecutionResult:
        """Executes a MarketOrderRequest against a single OrderBookSnapshot or CanonicalOrderBook.

        If order_time_book is provided, slippage and adverse selection metrics are
        computed relative to the order placement book rather than the fill book.
        """
        ob = _to_snapshot(orderbook)
        ref_book = _to_snapshot(order_time_book) if order_time_book is not None else ob
        side_norm = request.side.upper()

        top_order = ref_book.best_ask if side_norm == "BUY" else ref_book.best_bid
        mid_order = ref_book.mid_price
        top_fill = ob.best_ask if side_norm == "BUY" else ob.best_bid
        mid_fill = ob.mid_price

        # Adverse selection: how much did top of book move adversely from order time to fill time
        if side_norm == "BUY":
            adv_bps = ((top_fill - top_order) / top_order) * 10_000.0 if top_order > 0 else 0.0
        else:
            adv_bps = ((top_order - top_fill) / top_order) * 10_000.0 if top_order > 0 else 0.0

        levels = ob.asks if side_norm == "BUY" else ob.bids
        if not levels:
            return ExecutionResult(
                request=request,
                order_timestamp=request.timestamp,
                fill_timestamp=ob.timestamp,
                status="REJECTED",
                side=side_norm,
                filled_quantity=0.0,
                filled_amount_krw=0.0,
                unfilled_quantity=request.requested_quantity_btc or 0.0,
                unfilled_amount_krw=request.requested_amount_krw or 0.0,
                vwap_price=0.0,
                mid_price_at_order=mid_order,
                top_of_book_at_order=top_order,
                mid_price_at_fill=mid_fill,
                top_of_book_at_fill=top_fill,
                fee_rate=request.fee_rate,
                fee_paid_krw=0.0,
                slippage_vs_mid_bps=0.0,
                slippage_vs_top_bps=0.0,
                adverse_selection_bps=adv_bps,
                latency_delay_ms=request.latency_delay_ms,
                rejection_reason="EMPTY_ORDERBOOK",
                half_spread_cost_krw=0.0,
                depth_slippage_cost_krw=0.0,
                latency_slippage_cost_krw=0.0,
                total_cost_krw=0.0,
            )

        fills: list[ExecutionFill] = []
        total_filled_qty = 0.0
        total_filled_krw = 0.0

        if request.requested_amount_krw is not None:
            remaining_krw = request.requested_amount_krw
            for idx, (level_price, level_size) in enumerate(levels):
                if remaining_krw <= 0:
                    break
                level_notional = level_price * level_size
                if remaining_krw >= level_notional:
                    fills.append(
                        ExecutionFill(
                            level_index=idx,
                            price=level_price,
                            size=level_size,
                            notional_krw=level_notional,
                        )
                    )
                    total_filled_qty += level_size
                    total_filled_krw += level_notional
                    remaining_krw -= level_notional
                else:
                    qty_slice = remaining_krw / level_price
                    fills.append(
                        ExecutionFill(
                            level_index=idx,
                            price=level_price,
                            size=qty_slice,
                            notional_krw=remaining_krw,
                        )
                    )
                    total_filled_qty += qty_slice
                    total_filled_krw += remaining_krw
                    remaining_krw = 0.0
                    break

            unfilled_krw = remaining_krw
            unfilled_qty = (remaining_krw / levels[-1][0]) if levels and remaining_krw > 0 else 0.0
            is_partial = unfilled_krw > 1e-6

        else:
            remaining_qty = request.requested_quantity_btc  # guaranteed non-None
            for idx, (level_price, level_size) in enumerate(levels):
                if remaining_qty <= 0:
                    break
                fill_size = min(remaining_qty, level_size)
                notional = fill_size * level_price
                fills.append(
                    ExecutionFill(
                        level_index=idx,
                        price=level_price,
                        size=fill_size,
                        notional_krw=notional,
                    )
                )
                total_filled_qty += fill_size
                total_filled_krw += notional
                remaining_qty -= fill_size

            unfilled_qty = remaining_qty
            unfilled_krw = remaining_qty * (levels[-1][0] if levels else 0.0)
            is_partial = unfilled_qty > 1e-8

        if not request.allow_partial and is_partial:
            return ExecutionResult(
                request=request,
                order_timestamp=request.timestamp,
                fill_timestamp=ob.timestamp,
                status="REJECTED",
                side=side_norm,
                filled_quantity=0.0,
                filled_amount_krw=0.0,
                unfilled_quantity=request.requested_quantity_btc or (unfilled_krw / top_order),
                unfilled_amount_krw=request.requested_amount_krw or (unfilled_qty * top_order),
                vwap_price=0.0,
                mid_price_at_order=mid_order,
                top_of_book_at_order=top_order,
                mid_price_at_fill=mid_fill,
                top_of_book_at_fill=top_fill,
                fee_rate=request.fee_rate,
                fee_paid_krw=0.0,
                slippage_vs_mid_bps=0.0,
                slippage_vs_top_bps=0.0,
                adverse_selection_bps=adv_bps,
                latency_delay_ms=request.latency_delay_ms,
                rejection_reason="INSUFFICIENT_DEPTH_PARTIAL_PROHIBITED",
                half_spread_cost_krw=0.0,
                depth_slippage_cost_krw=0.0,
                latency_slippage_cost_krw=0.0,
                total_cost_krw=0.0,
            )

        if total_filled_qty <= 0:
            return ExecutionResult(
                request=request,
                order_timestamp=request.timestamp,
                fill_timestamp=ob.timestamp,
                status="REJECTED",
                side=side_norm,
                filled_quantity=0.0,
                filled_amount_krw=0.0,
                unfilled_quantity=request.requested_quantity_btc or 0.0,
                unfilled_amount_krw=request.requested_amount_krw or 0.0,
                vwap_price=0.0,
                mid_price_at_order=mid_order,
                top_of_book_at_order=top_order,
                mid_price_at_fill=mid_fill,
                top_of_book_at_fill=top_fill,
                fee_rate=request.fee_rate,
                fee_paid_krw=0.0,
                slippage_vs_mid_bps=0.0,
                slippage_vs_top_bps=0.0,
                adverse_selection_bps=adv_bps,
                latency_delay_ms=request.latency_delay_ms,
                rejection_reason="ZERO_FILL",
                half_spread_cost_krw=0.0,
                depth_slippage_cost_krw=0.0,
                latency_slippage_cost_krw=0.0,
                total_cost_krw=0.0,
            )

        vwap = total_filled_krw / total_filled_qty
        fee_paid = total_filled_krw * request.fee_rate

        # Slippage vs mid price at order time:
        if side_norm == "BUY":
            slip_mid_bps = ((vwap - mid_order) / mid_order) * 10_000.0 if mid_order > 0 else 0.0
            slip_top_bps = ((vwap - top_order) / top_order) * 10_000.0 if top_order > 0 else 0.0
            half_spread_cost = max(0.0, top_order - mid_order) * total_filled_qty
            latency_cost = max(0.0, top_fill - top_order) * total_filled_qty
            depth_cost = max(0.0, vwap - top_fill) * total_filled_qty
        else:
            slip_mid_bps = ((mid_order - vwap) / mid_order) * 10_000.0 if mid_order > 0 else 0.0
            slip_top_bps = ((top_order - vwap) / top_order) * 10_000.0 if top_order > 0 else 0.0
            half_spread_cost = max(0.0, mid_order - top_order) * total_filled_qty
            latency_cost = max(0.0, top_order - top_fill) * total_filled_qty
            depth_cost = max(0.0, top_fill - vwap) * total_filled_qty

        total_cost = half_spread_cost + latency_cost + depth_cost + fee_paid

        status = "PARTIALLY_FILLED" if is_partial else "FILLED"

        return ExecutionResult(
            request=request,
            order_timestamp=request.timestamp,
            fill_timestamp=ob.timestamp,
            status=status,
            side=side_norm,
            filled_quantity=total_filled_qty,
            filled_amount_krw=total_filled_krw,
            unfilled_quantity=unfilled_qty,
            unfilled_amount_krw=unfilled_krw,
            vwap_price=vwap,
            mid_price_at_order=mid_order,
            top_of_book_at_order=top_order,
            mid_price_at_fill=mid_fill,
            top_of_book_at_fill=top_fill,
            fee_rate=request.fee_rate,
            fee_paid_krw=fee_paid,
            slippage_vs_mid_bps=slip_mid_bps,
            slippage_vs_top_bps=slip_top_bps,
            adverse_selection_bps=adv_bps,
            latency_delay_ms=request.latency_delay_ms,
            fills=tuple(fills),
            rejection_reason="",
            half_spread_cost_krw=half_spread_cost,
            depth_slippage_cost_krw=depth_cost,
            latency_slippage_cost_krw=latency_cost,
            total_cost_krw=total_cost,
        )

    @classmethod
    def execute_with_latency(
        cls,
        request: MarketOrderRequest,
        orderbook_stream: Sequence[OrderBookSnapshot | CanonicalOrderBook],
        max_book_age_ms: float = 5000.0,
        fail_closed: bool = True,
    ) -> ExecutionResult:
        """Simulates order execution against a sequential stream of snapshots.

        Finds the first snapshot at or after (request.timestamp + request.latency_delay_ms).
        Adverse selection and slippage are computed relative to the snapshot at request.timestamp.
        """
        if not orderbook_stream:
            raise ValueError("orderbook_stream cannot be empty.")

        snapshots = [_to_snapshot(ob) for ob in orderbook_stream]
        order_time_sec = _timestamp_to_seconds(request.timestamp)
        target_fill_sec = order_time_sec + (request.latency_delay_ms / 1000.0)

        # Find snapshot at or immediately preceding order_time
        order_time_book: OrderBookSnapshot | None = None
        for ob in snapshots:
            ob_sec = _timestamp_to_seconds(ob.timestamp)
            if ob_sec <= order_time_sec:
                order_time_book = ob
            else:
                break
        if order_time_book is None:
            order_time_book = snapshots[0]

        # Find first snapshot at or after target_fill_sec
        fill_book: OrderBookSnapshot | None = None
        for ob in snapshots:
            ob_sec = _timestamp_to_seconds(ob.timestamp)
            if ob_sec >= target_fill_sec:
                fill_book = ob
                break

        if fill_book is None:
            if fail_closed:
                top_order = order_time_book.best_ask if request.side.upper() == "BUY" else order_time_book.best_bid
                return ExecutionResult(
                    request=request,
                    order_timestamp=request.timestamp,
                    fill_timestamp=order_time_book.timestamp,
                    status="REJECTED",
                    side=request.side.upper(),
                    filled_quantity=0.0,
                    filled_amount_krw=0.0,
                    unfilled_quantity=request.requested_quantity_btc or 0.0,
                    unfilled_amount_krw=request.requested_amount_krw or 0.0,
                    vwap_price=0.0,
                    mid_price_at_order=order_time_book.mid_price,
                    top_of_book_at_order=top_order,
                    mid_price_at_fill=order_time_book.mid_price,
                    top_of_book_at_fill=top_order,
                    fee_rate=request.fee_rate,
                    fee_paid_krw=0.0,
                    slippage_vs_mid_bps=0.0,
                    slippage_vs_top_bps=0.0,
                    adverse_selection_bps=0.0,
                    latency_delay_ms=request.latency_delay_ms,
                    rejection_reason="INSUFFICIENT_FUTURE_DATA",
                )
            else:
                fill_book = snapshots[-1]

        # Check staleness
        fill_sec = _timestamp_to_seconds(fill_book.timestamp)
        age_ms = (fill_sec - target_fill_sec) * 1000.0
        if age_ms > max_book_age_ms and fail_closed:
            top_order = order_time_book.best_ask if request.side.upper() == "BUY" else order_time_book.best_bid
            return ExecutionResult(
                request=request,
                order_timestamp=request.timestamp,
                fill_timestamp=fill_book.timestamp,
                status="REJECTED",
                side=request.side.upper(),
                filled_quantity=0.0,
                filled_amount_krw=0.0,
                unfilled_quantity=request.requested_quantity_btc or 0.0,
                unfilled_amount_krw=request.requested_amount_krw or 0.0,
                vwap_price=0.0,
                mid_price_at_order=order_time_book.mid_price,
                top_of_book_at_order=top_order,
                mid_price_at_fill=fill_book.mid_price,
                top_of_book_at_fill=fill_book.best_ask if request.side.upper() == "BUY" else fill_book.best_bid,
                fee_rate=request.fee_rate,
                fee_paid_krw=0.0,
                slippage_vs_mid_bps=0.0,
                slippage_vs_top_bps=0.0,
                adverse_selection_bps=0.0,
                latency_delay_ms=request.latency_delay_ms,
                rejection_reason="STALE_BOOK",
            )

        return cls.execute_order(
            request=request,
            orderbook=fill_book,
            order_time_book=order_time_book,
        )

    @classmethod
    def verify_decimal_equivalence(
        cls,
        request: MarketOrderRequest,
        orderbook: OrderBookSnapshot | CanonicalOrderBook,
    ) -> dict[str, Any]:
        """Calculates fill using Decimal (28 digits precision) and cross-checks with float result."""
        float_res = cls.execute_order(request, orderbook)
        if not float_res.is_filled:
            return {"equivalent": True, "float_status": float_res.status}

        ob = _to_snapshot(orderbook)
        side_norm = request.side.upper()
        levels = ob.asks if side_norm == "BUY" else ob.bids

        dec_filled_qty = Decimal(0)
        dec_filled_krw = Decimal(0)

        if request.requested_amount_krw is not None:
            rem_krw = Decimal(str(request.requested_amount_krw))
            for lp, ls in levels:
                if rem_krw <= Decimal(0):
                    break
                p = Decimal(str(lp))
                s = Decimal(str(ls))
                lvl_notional = p * s
                if rem_krw >= lvl_notional:
                    dec_filled_qty += s
                    dec_filled_krw += lvl_notional
                    rem_krw -= lvl_notional
                else:
                    qty_slice = rem_krw / p
                    dec_filled_qty += qty_slice
                    dec_filled_krw += rem_krw
                    rem_krw = Decimal(0)
                    break
        else:
            rem_qty = Decimal(str(request.requested_quantity_btc))
            for lp, ls in levels:
                if rem_qty <= Decimal(0):
                    break
                p = Decimal(str(lp))
                s = Decimal(str(ls))
                fill_s = min(rem_qty, s)
                dec_filled_qty += fill_s
                dec_filled_krw += fill_s * p
                rem_qty -= fill_s

        diff_qty = abs(float(dec_filled_qty) - float_res.filled_quantity)
        diff_krw = abs(float(dec_filled_krw) - float_res.filled_amount_krw)

        # 1 satoshi = 1e-8 BTC; KRW precision = 1 KRW
        is_equivalent = diff_qty < 1e-8 and diff_krw < 1.0
        return {
            "equivalent": is_equivalent,
            "diff_qty": diff_qty,
            "diff_krw": diff_krw,
            "dec_filled_qty": float(dec_filled_qty),
            "dec_filled_krw": float(dec_filled_krw),
            "float_filled_qty": float_res.filled_quantity,
            "float_filled_krw": float_res.filled_amount_krw,
        }
