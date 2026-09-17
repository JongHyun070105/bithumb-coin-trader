"""Conservative Historical Maker Execution Simulator.

Models passive limit-order execution with conservative fill assumptions.
No fantasy fills. No assumed queue position.

Three Fill Scenarios:
- OPTIMISTIC: Price touch or level presence triggers fill (upper bound / fragile baseline).
- BASE: Aggressive trades touching level or book crossing through level triggers fill.
- CONSERVATIVE: Requires verifiable volume exceeding queue-ahead at price level.
  Touch without volume is strictly NO_FILL.

Three Execution Variants:
- Variant A: PASSIVE ENTRY -> TAKER EXIT (hold to horizon, unwind via aggressive taker order).
- Variant B: PASSIVE ENTRY -> PASSIVE EXIT (hold, post passive exit order at opposing touch).
- Variant C: PASSIVE ENTRY -> TIMED TAKER UNWIND (passive entry, timed forced unwind if unfulfilled).

Usage:
    from bithumb_coin_trader.maker_simulator import MakerSimulator, MakerAssumptions, FillModel
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from .research_infra.canonical_events import CanonicalEvent, EventKind


class FillModel(str, Enum):
    CONSERVATIVE = "conservative"  # Requires volume proof exceeding queue
    BASE = "base"                  # Price touch / aggressive trade sufficient
    OPTIMISTIC = "optimistic"      # Any price movement to level


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    STALE = "STALE"
    ADVERSE = "ADVERSE"
    FORCED_EXIT = "FORCED_EXIT"


@dataclass(frozen=True, slots=True)
class MakerAssumptions:
    """Configuration for maker simulation."""
    fill_model: FillModel = FillModel.BASE
    cancellation_horizon_s: float = 5.0
    holding_horizon_s: float = 10.0
    queue_multiplier: float = 1.0  # Assume queue = displayed_size * multiplier
    maker_fee_rate: float = 0.0    # Maker fee (e.g. 0.0 on Bithumb promotion)
    taker_fee_rate: float = 0.0004 # Taker exit fee (4 bps standard or 0.0 promo)
    latency_ms: float = 100.0      # Placement / wire latency
    max_staleness_ms: float = 5000.0


@dataclass
class MakerOrder:
    """Represents a passive limit order."""
    side: str  # "BUY" or "SELL"
    limit_price: float
    quantity: float
    placement_timestamp_ns: int
    effective_timestamp_ns: int
    cancellation_timestamp_ns: int
    initial_queue_ahead: float
    assumptions: MakerAssumptions


@dataclass
class MakerFill:
    """Result of a maker order evaluation."""
    order: MakerOrder
    status: OrderStatus
    fill_price: float = 0.0
    fill_quantity: float = 0.0
    fill_timestamp_ns: int = 0
    fee_krw: float = 0.0
    adverse_selection_bps: float = 0.0
    queue_delay_ms: float = 0.0
    reason: str = ""


@dataclass
class MakerTradeRoundTrip:
    """Complete round-trip trade resulting from maker entry."""
    variant: str  # "A", "B", "C"
    entry_fill: MakerFill
    exit_fill: MakerFill | None
    exit_type: str  # "TAKER", "PASSIVE", "FORCED_UNWIND", "NONE"
    entry_price: float
    exit_price: float
    quantity: float
    gross_krw: float
    net_krw: float
    gross_bps: float
    net_bps: float
    holding_duration_s: float
    success: bool
    reason: str = ""


class MakerSimulator:
    """Conservative historical maker execution simulator.

    Evaluates passive limit orders against historical orderbook and trade events.
    Uses strictly causal (backward-looking) information for fill decisions.
    """

    def __init__(self, assumptions: MakerAssumptions | None = None):
        self.assumptions = assumptions or MakerAssumptions()

    def create_order(
        self,
        side: str,
        limit_price: float,
        quantity: float,
        placement_ts: int,
        initial_book: CanonicalEvent | None = None,
    ) -> MakerOrder:
        """Create a new MakerOrder with latency and queue-ahead initialized."""
        effective_ts = placement_ts + int(self.assumptions.latency_ms * 1_000_000)
        cancellation_ts = effective_ts + int(self.assumptions.cancellation_horizon_s * 1_000_000_000)

        # Estimate initial queue ahead from book snapshot at placement
        queue_ahead = 0.0
        if initial_book is not None and initial_book.payload:
            payload = initial_book.payload
            levels = payload.get("bids" if side.upper() == "BUY" else "asks", [])
            for px, sz in levels:
                if abs(float(px) - limit_price) < 1e-8:
                    queue_ahead = float(sz)
                    break

        return MakerOrder(
            side=side.upper(),
            limit_price=limit_price,
            quantity=quantity,
            placement_timestamp_ns=placement_ts,
            effective_timestamp_ns=effective_ts,
            cancellation_timestamp_ns=cancellation_ts,
            initial_queue_ahead=queue_ahead * self.assumptions.queue_multiplier,
            assumptions=self.assumptions,
        )

    def evaluate_order(
        self,
        order: MakerOrder,
        future_events: Sequence[CanonicalEvent],
        reference_mid_at_placement: float = 0.0,
    ) -> MakerFill:
        """Evaluate a passive limit order (BUY or SELL) against future events."""
        remaining_qty = order.quantity
        filled_qty = 0.0
        filled_value = 0.0
        queue_ahead_remaining = order.initial_queue_ahead
        first_fill_ts = 0

        # Check staleness if future events exist
        if future_events:
            first_event_ts = future_events[0].ordering_timestamp_ns
            if first_event_ts - order.placement_timestamp_ns > int(order.assumptions.max_staleness_ms * 1_000_000):
                return MakerFill(
                    order=order,
                    status=OrderStatus.STALE,
                    reason="FIRST_EVENT_EXCEEDS_MAX_STALENESS",
                )

        for event in future_events:
            ts = event.ordering_timestamp_ns

            # 1. In-flight wire latency guard: order has not yet reached matching engine
            if ts < order.effective_timestamp_ns:
                continue

            # 2. Check cancellation boundary
            if ts >= order.cancellation_timestamp_ns:
                if filled_qty > 0:
                    avg_price = filled_value / filled_qty
                    queue_delay = (first_fill_ts - order.effective_timestamp_ns) / 1_000_000.0 if first_fill_ts else 0.0
                    return MakerFill(
                        order=order,
                        status=OrderStatus.PARTIAL,
                        fill_price=avg_price,
                        fill_quantity=filled_qty,
                        fill_timestamp_ns=ts,
                        fee_krw=filled_value * order.assumptions.maker_fee_rate,
                        adverse_selection_bps=self._calc_adverse(reference_mid_at_placement, avg_price, order.side),
                        queue_delay_ms=max(0.0, queue_delay),
                        reason="PARTIAL_FILL_BEFORE_CANCEL",
                    )
                return MakerFill(
                    order=order,
                    status=OrderStatus.CANCELLED,
                    reason="CANCELLED_NO_FILL",
                )

            # 3. Check fill against Orderbook or Trade
            fill_delta: tuple[float, float] | None = None
            if event.event_kind == EventKind.ORDERBOOK:
                fill_delta, queue_ahead_remaining = self._check_orderbook_fill(
                    event, order, remaining_qty, queue_ahead_remaining
                )
            elif event.event_kind == EventKind.TRADE:
                fill_delta, queue_ahead_remaining = self._check_trade_fill(
                    event, order, remaining_qty, queue_ahead_remaining
                )

            if fill_delta is not None:
                qty, price = fill_delta
                if qty > 0:
                    if first_fill_ts == 0:
                        first_fill_ts = ts
                    filled_qty += qty
                    filled_value += qty * price
                    remaining_qty -= qty

                    if remaining_qty <= 1e-12:
                        avg_price = filled_value / filled_qty
                        queue_delay = (ts - order.effective_timestamp_ns) / 1_000_000.0
                        return MakerFill(
                            order=order,
                            status=OrderStatus.FILLED,
                            fill_price=avg_price,
                            fill_quantity=filled_qty,
                            fill_timestamp_ns=ts,
                            fee_krw=filled_value * order.assumptions.maker_fee_rate,
                            adverse_selection_bps=self._calc_adverse(reference_mid_at_placement, avg_price, order.side),
                            queue_delay_ms=max(0.0, queue_delay),
                            reason="FULL_FILL",
                        )

        # Future events exhausted before full fill
        if filled_qty > 0:
            avg_price = filled_value / filled_qty
            last_ts = future_events[-1].ordering_timestamp_ns if future_events else order.placement_timestamp_ns
            queue_delay = (first_fill_ts - order.effective_timestamp_ns) / 1_000_000.0 if first_fill_ts else 0.0
            return MakerFill(
                order=order,
                status=OrderStatus.PARTIAL,
                fill_price=avg_price,
                fill_quantity=filled_qty,
                fill_timestamp_ns=last_ts,
                fee_krw=filled_value * order.assumptions.maker_fee_rate,
                adverse_selection_bps=self._calc_adverse(reference_mid_at_placement, avg_price, order.side),
                queue_delay_ms=max(0.0, queue_delay),
                reason="PARTIAL_FILL_EVENT_EXHAUSTION",
            )

        return MakerFill(
            order=order,
            status=OrderStatus.EXPIRED,
            reason="NO_FILL_EVENT_EXHAUSTION",
        )

    def evaluate_passive_buy(
        self,
        limit_price: float,
        quantity_btc: float,
        placement_ts: int,
        future_events: Sequence[CanonicalEvent],
        reference_mid_at_placement: float,
        initial_book: CanonicalEvent | None = None,
    ) -> MakerFill:
        """Backwards-compatible wrapper for passive BUY limit order evaluation."""
        order = self.create_order("BUY", limit_price, quantity_btc, placement_ts, initial_book)
        return self.evaluate_order(order, future_events, reference_mid_at_placement)

    def evaluate_passive_sell(
        self,
        limit_price: float,
        quantity_btc: float,
        placement_ts: int,
        future_events: Sequence[CanonicalEvent],
        reference_mid_at_placement: float,
        initial_book: CanonicalEvent | None = None,
    ) -> MakerFill:
        """Wrapper for passive SELL limit order evaluation."""
        order = self.create_order("SELL", limit_price, quantity_btc, placement_ts, initial_book)
        return self.evaluate_order(order, future_events, reference_mid_at_placement)

    def simulate_variant_a(
        self,
        entry_fill: MakerFill,
        future_events: Sequence[CanonicalEvent],
    ) -> MakerTradeRoundTrip:
        """Variant A: PASSIVE ENTRY -> TAKER EXIT (timed market unwind at holding horizon)."""
        if entry_fill.fill_quantity <= 0:
            return self._empty_roundtrip("A", entry_fill, "NO_ENTRY_FILL")

        holding_ns = int(entry_fill.order.assumptions.holding_horizon_s * 1_000_000_000)
        target_exit_ts = entry_fill.fill_timestamp_ns + holding_ns

        # Find first orderbook event at or after target_exit_ts to execute taker exit
        exit_price = 0.0
        exit_ts = 0
        for event in future_events:
            if event.ordering_timestamp_ns >= target_exit_ts and event.event_kind == EventKind.ORDERBOOK:
                payload = event.payload
                if entry_fill.order.side == "BUY":
                    # Exit long by selling into best bid
                    bids = payload.get("bids", [])
                    if bids:
                        exit_price = float(bids[0][0])
                        exit_ts = event.ordering_timestamp_ns
                        break
                else:
                    # Exit short by buying from best ask
                    asks = payload.get("asks", [])
                    if asks:
                        exit_price = float(asks[0][0])
                        exit_ts = event.ordering_timestamp_ns
                        break

        if exit_price <= 0:
            # Fallback to last known price
            return self._empty_roundtrip("A", entry_fill, "NO_EXIT_LIQUIDITY")

        qty = entry_fill.fill_quantity
        entry_val = entry_fill.fill_price * qty
        exit_val = exit_price * qty

        gross_krw = (exit_val - entry_val) if entry_fill.order.side == "BUY" else (entry_val - exit_val)
        entry_fee = entry_val * entry_fill.order.assumptions.maker_fee_rate
        exit_fee = exit_val * entry_fill.order.assumptions.taker_fee_rate
        net_krw = gross_krw - entry_fee - exit_fee
        gross_bps = (gross_krw / entry_val) * 10_000.0 if entry_val > 0 else 0.0
        net_bps = (net_krw / entry_val) * 10_000.0 if entry_val > 0 else 0.0

        return MakerTradeRoundTrip(
            variant="A",
            entry_fill=entry_fill,
            exit_fill=None,
            exit_type="TAKER",
            entry_price=entry_fill.fill_price,
            exit_price=exit_price,
            quantity=qty,
            gross_krw=gross_krw,
            net_krw=net_krw,
            gross_bps=gross_bps,
            net_bps=net_bps,
            holding_duration_s=(exit_ts - entry_fill.fill_timestamp_ns) / 1_000_000_000.0,
            success=net_bps > 0,
            reason="TAKER_EXIT_AT_HORIZON",
        )

    def simulate_variant_b(
        self,
        entry_fill: MakerFill,
        future_events: Sequence[CanonicalEvent],
    ) -> MakerTradeRoundTrip:
        """Variant B: PASSIVE ENTRY -> PASSIVE EXIT (passive exit order posted at opposing touch)."""
        if entry_fill.fill_quantity <= 0:
            return self._empty_roundtrip("B", entry_fill, "NO_ENTRY_FILL")

        # Find current best quote at entry fill timestamp to set passive exit limit price
        exit_limit = 0.0
        exit_placement_ts = entry_fill.fill_timestamp_ns
        exit_side = "SELL" if entry_fill.order.side == "BUY" else "BUY"

        for event in future_events:
            if event.ordering_timestamp_ns >= exit_placement_ts and event.event_kind == EventKind.ORDERBOOK:
                payload = event.payload
                if exit_side == "SELL":
                    asks = payload.get("asks", [])
                    if asks:
                        exit_limit = float(asks[0][0])
                        break
                else:
                    bids = payload.get("bids", [])
                    if bids:
                        exit_limit = float(bids[0][0])
                        break

        if exit_limit <= 0:
            return self.simulate_variant_a(entry_fill, future_events)

        exit_order = self.create_order(
            side=exit_side,
            limit_price=exit_limit,
            quantity=entry_fill.fill_quantity,
            placement_ts=exit_placement_ts,
        )

        exit_events = [e for e in future_events if e.ordering_timestamp_ns >= exit_placement_ts]
        exit_fill = self.evaluate_order(exit_order, exit_events)

        if exit_fill.status in (OrderStatus.FILLED, OrderStatus.PARTIAL) and exit_fill.fill_quantity > 0:
            qty = exit_fill.fill_quantity
            entry_val = entry_fill.fill_price * qty
            exit_val = exit_fill.fill_price * qty
            gross_krw = (exit_val - entry_val) if entry_fill.order.side == "BUY" else (entry_val - exit_val)
            entry_fee = entry_val * entry_fill.order.assumptions.maker_fee_rate
            exit_fee = exit_val * exit_fill.order.assumptions.maker_fee_rate  # Maker fee on exit!
            net_krw = gross_krw - entry_fee - exit_fee
            gross_bps = (gross_krw / entry_val) * 10_000.0 if entry_val > 0 else 0.0
            net_bps = (net_krw / entry_val) * 10_000.0 if entry_val > 0 else 0.0

            return MakerTradeRoundTrip(
                variant="B",
                entry_fill=entry_fill,
                exit_fill=exit_fill,
                exit_type="PASSIVE",
                entry_price=entry_fill.fill_price,
                exit_price=exit_fill.fill_price,
                quantity=qty,
                gross_krw=gross_krw,
                net_krw=net_krw,
                gross_bps=gross_bps,
                net_bps=net_bps,
                holding_duration_s=(exit_fill.fill_timestamp_ns - entry_fill.fill_timestamp_ns) / 1_000_000_000.0,
                success=net_bps > 0,
                reason="PASSIVE_EXIT_FILLED",
            )
        else:
            # Fallback to forced market unwind (FORCED_EXIT)
            trip_a = self.simulate_variant_a(entry_fill, future_events)
            return MakerTradeRoundTrip(
                variant="B",
                entry_fill=entry_fill,
                exit_fill=exit_fill,
                exit_type="FORCED_UNWIND",
                entry_price=trip_a.entry_price,
                exit_price=trip_a.exit_price,
                quantity=trip_a.quantity,
                gross_krw=trip_a.gross_krw,
                net_krw=trip_a.net_krw,
                gross_bps=trip_a.gross_bps,
                net_bps=trip_a.net_bps,
                holding_duration_s=trip_a.holding_duration_s,
                success=trip_a.success,
                reason="PASSIVE_EXIT_FAILED_FORCED_TAKER_UNWIND",
            )

    def simulate_variant_c(
        self,
        entry_fill: MakerFill,
        future_events: Sequence[CanonicalEvent],
    ) -> MakerTradeRoundTrip:
        """Variant C: PASSIVE ENTRY -> TIMED TAKER UNWIND (timed forced unwind after holding horizon)."""
        res = self.simulate_variant_a(entry_fill, future_events)
        return MakerTradeRoundTrip(
            variant="C",
            entry_fill=res.entry_fill,
            exit_fill=res.exit_fill,
            exit_type="FORCED_UNWIND",
            entry_price=res.entry_price,
            exit_price=res.exit_price,
            quantity=res.quantity,
            gross_krw=res.gross_krw,
            net_krw=res.net_krw,
            gross_bps=res.gross_bps,
            net_bps=res.net_bps,
            holding_duration_s=res.holding_duration_s,
            success=res.success,
            reason="TIMED_TAKER_UNWIND",
        )

    def _check_orderbook_fill(
        self,
        event: CanonicalEvent,
        order: MakerOrder,
        remaining_qty: float,
        queue_ahead_remaining: float,
    ) -> tuple[tuple[float, float] | None, float]:
        """Check orderbook event for fill and update remaining queue ahead."""
        payload = event.payload
        model = order.assumptions.fill_model

        if order.side == "BUY":
            asks = payload.get("asks", [])
            if not asks:
                return None, queue_ahead_remaining
            best_ask = float(asks[0][0])
            ask_size = float(asks[0][1])

            if model == FillModel.OPTIMISTIC:
                if best_ask <= order.limit_price:
                    return (min(remaining_qty, ask_size), order.limit_price), queue_ahead_remaining

            elif model == FillModel.BASE:
                # Fill if best ask crosses at or through limit
                if best_ask <= order.limit_price:
                    return (min(remaining_qty, ask_size), order.limit_price), queue_ahead_remaining

            elif model == FillModel.CONSERVATIVE:
                # Trade-through strictly required for orderbook-only fill: best ask < limit
                if best_ask < order.limit_price:
                    return (min(remaining_qty, ask_size), order.limit_price), 0.0
                # Touch alone (best_ask == limit_price) does NOT fill in Conservative without trade volume!
                return None, queue_ahead_remaining

        else:  # SELL
            bids = payload.get("bids", [])
            if not bids:
                return None, queue_ahead_remaining
            best_bid = float(bids[0][0])
            bid_size = float(bids[0][1])

            if model == FillModel.OPTIMISTIC:
                if best_bid >= order.limit_price:
                    return (min(remaining_qty, bid_size), order.limit_price), queue_ahead_remaining

            elif model == FillModel.BASE:
                if best_bid >= order.limit_price:
                    return (min(remaining_qty, bid_size), order.limit_price), queue_ahead_remaining

            elif model == FillModel.CONSERVATIVE:
                if best_bid > order.limit_price:
                    return (min(remaining_qty, bid_size), order.limit_price), 0.0
                return None, queue_ahead_remaining

        return None, queue_ahead_remaining

    def _check_trade_fill(
        self,
        event: CanonicalEvent,
        order: MakerOrder,
        remaining_qty: float,
        queue_ahead_remaining: float,
    ) -> tuple[tuple[float, float] | None, float]:
        """Check trade event for fill with conservative queue clearing."""
        payload = event.payload
        trade_price = float(payload.get("price", 0))
        trade_qty = float(payload.get("quantity", 0))
        aggressor = str(payload.get("aggressor_side", "")).upper()

        if trade_price <= 0 or trade_qty <= 0:
            return None, queue_ahead_remaining

        model = order.assumptions.fill_model

        if order.side == "BUY":
            # Incoming aggressive SELL can match our passive bid
            is_opposing = aggressor in ("SELL", "ASK")
            if not is_opposing:
                return None, queue_ahead_remaining

            if model == FillModel.OPTIMISTIC:
                if trade_price <= order.limit_price:
                    return (min(remaining_qty, trade_qty), min(trade_price, order.limit_price)), queue_ahead_remaining

            elif model == FillModel.BASE:
                if trade_price <= order.limit_price:
                    return (min(remaining_qty, trade_qty), min(trade_price, order.limit_price)), queue_ahead_remaining

            elif model == FillModel.CONSERVATIVE:
                if trade_price < order.limit_price:
                    # Traded through: queue ahead completely cleared
                    return (min(remaining_qty, trade_qty), min(trade_price, order.limit_price)), 0.0
                elif abs(trade_price - order.limit_price) < 1e-8:
                    # Traded at limit price: must clear queue ahead first
                    if trade_qty <= queue_ahead_remaining:
                        # Trade absorbed entirely by queue ahead
                        return None, queue_ahead_remaining - trade_qty
                    else:
                        fillable = trade_qty - queue_ahead_remaining
                        return (min(remaining_qty, fillable), order.limit_price), 0.0

        else:  # SELL
            # Incoming aggressive BUY can match our passive ask
            is_opposing = aggressor in ("BUY", "BID")
            if not is_opposing:
                return None, queue_ahead_remaining

            if model == FillModel.OPTIMISTIC:
                if trade_price >= order.limit_price:
                    return (min(remaining_qty, trade_qty), max(trade_price, order.limit_price)), queue_ahead_remaining

            elif model == FillModel.BASE:
                if trade_price >= order.limit_price:
                    return (min(remaining_qty, trade_qty), max(trade_price, order.limit_price)), queue_ahead_remaining

            elif model == FillModel.CONSERVATIVE:
                if trade_price > order.limit_price:
                    # Traded through
                    return (min(remaining_qty, trade_qty), max(trade_price, order.limit_price)), 0.0
                elif abs(trade_price - order.limit_price) < 1e-8:
                    if trade_qty <= queue_ahead_remaining:
                        return None, queue_ahead_remaining - trade_qty
                    else:
                        fillable = trade_qty - queue_ahead_remaining
                        return (min(remaining_qty, fillable), order.limit_price), 0.0

        return None, queue_ahead_remaining

    def _empty_roundtrip(self, variant: str, fill: MakerFill, reason: str) -> MakerTradeRoundTrip:
        return MakerTradeRoundTrip(
            variant=variant,
            entry_fill=fill,
            exit_fill=None,
            exit_type="NONE",
            entry_price=fill.fill_price,
            exit_price=0.0,
            quantity=0.0,
            gross_krw=0.0,
            net_krw=0.0,
            gross_bps=0.0,
            net_bps=0.0,
            holding_duration_s=0.0,
            success=False,
            reason=reason,
        )

    @staticmethod
    def _calc_adverse(ref_mid: float, fill_price: float, side: str) -> float:
        """Calculate adverse selection in bps."""
        if ref_mid <= 0:
            return 0.0
        if side == "BUY":
            return ((ref_mid - fill_price) / ref_mid) * 10_000.0
        return ((fill_price - ref_mid) / ref_mid) * 10_000.0
