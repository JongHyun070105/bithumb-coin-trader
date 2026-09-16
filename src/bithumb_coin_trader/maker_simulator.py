"""Conservative Historical Maker Execution Simulator.

Models passive limit-order execution with conservative fill assumptions.
No fantasy fills. No assumed queue position.

Fill model:
- Conservative: only fills when aggressive trade volume exceeds displayed size at level
- Base: fills when aggressive trade touches the price level
- Optimistic: fills when price touches the level (regardless of volume)

Usage:
    from bithumb_coin_trader.maker_simulator import MakerSimulator, MakerAssumptions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .research_infra.canonical_events import CanonicalEvent, EventKind


class FillModel(str, Enum):
    CONSERVATIVE = "conservative"  # Requires volume proof
    BASE = "base"                  # Price touch sufficient
    OPTIMISTIC = "optimistic"      # Any price movement to level


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    ADVERSE = "ADVERSE"


@dataclass(frozen=True, slots=True)
class MakerAssumptions:
    """Configuration for maker simulation."""
    fill_model: FillModel = FillModel.BASE
    cancellation_horizon_s: float = 5.0
    queue_multiplier: float = 1.0  # Assume queue = displayed_size * multiplier
    fee_rate: float = 0.0  # Maker fee (often 0 or rebate)
    latency_ms: float = 100.0
    max_staleness_ms: float = 5000.0


@dataclass
class MakerOrder:
    """Represents a passive limit order."""
    side: str  # "BUY" or "SELL"
    limit_price: float
    quantity_btc: float
    placement_timestamp_ns: int
    cancellation_timestamp_ns: int
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
    reason: str = ""


class MakerSimulator:
    """Conservative historical maker execution simulator.

    Evaluates passive limit orders against historical orderbook events.
    Uses only causal (backward-looking) information for fill decisions.
    """

    def __init__(self, assumptions: MakerAssumptions | None = None):
        self.assumptions = assumptions or MakerAssumptions()

    def evaluate_passive_buy(
        self,
        limit_price: float,
        quantity_btc: float,
        placement_ts: int,
        future_events: list[CanonicalEvent],
        reference_mid_at_placement: float,
    ) -> MakerFill:
        """Evaluate a passive BUY order against future events.

        Args:
            limit_price: Limit price for the passive bid
            quantity_btc: Quantity in BTC
            placement_ts: When the order was placed (ns)
            future_events: Subsequent orderbook/trade events (chronological)
            reference_mid_at_placement: Mid price at order placement for adverse selection calc

        Returns:
            MakerFill with status and fill details
        """
        cancellation_ts = placement_ts + int(self.assumptions.cancellation_horizon_s * 1e9)
        order = MakerOrder(
            side="BUY",
            limit_price=limit_price,
            quantity_btc=quantity_btc,
            placement_timestamp_ns=placement_ts,
            cancellation_timestamp_ns=cancellation_ts,
            assumptions=self.assumptions,
        )

        remaining_qty = quantity_btc
        filled_qty = 0.0
        filled_value = 0.0

        for event in future_events:
            ts = event.ordering_timestamp_ns

            # Check cancellation
            if ts >= cancellation_ts:
                if filled_qty > 0:
                    avg_price = filled_value / filled_qty
                    return MakerFill(
                        order=order,
                        status=OrderStatus.PARTIAL,
                        fill_price=avg_price,
                        fill_quantity=filled_qty,
                        fill_timestamp_ns=ts,
                        fee_krw=filled_value * self.assumptions.fee_rate,
                        adverse_selection_bps=self._calc_adverse(
                            reference_mid_at_placement, avg_price, "BUY"
                        ),
                        reason="PARTIAL_FILL_BEFORE_CANCEL",
                    )
                return MakerFill(order=order, status=OrderStatus.CANCELLED,
                                  reason="CANCELLED_NO_FILL")

            if event.event_kind == EventKind.ORDERBOOK:
                fill_result = self._check_orderbook_fill_buy(
                    event, limit_price, remaining_qty, ts
                )
                if fill_result:
                    qty, price = fill_result
                    filled_qty += qty
                    filled_value += qty * price
                    remaining_qty -= qty
                    if remaining_qty <= 1e-12:
                        avg_price = filled_value / filled_qty
                        return MakerFill(
                            order=order,
                            status=OrderStatus.FILLED,
                            fill_price=avg_price,
                            fill_quantity=filled_qty,
                            fill_timestamp_ns=ts,
                            fee_krw=filled_value * self.assumptions.fee_rate,
                            adverse_selection_bps=self._calc_adverse(
                                reference_mid_at_placement, avg_price, "BUY"
                            ),
                            reason="FULL_FILL",
                        )

            elif event.event_kind == EventKind.TRADE:
                fill_result = self._check_trade_fill_buy(
                    event, limit_price, remaining_qty, ts
                )
                if fill_result:
                    qty, price = fill_result
                    filled_qty += qty
                    filled_value += qty * price
                    remaining_qty -= qty
                    if remaining_qty <= 1e-12:
                        avg_price = filled_value / filled_qty
                        return MakerFill(
                            order=order,
                            status=OrderStatus.FILLED,
                            fill_price=avg_price,
                            fill_quantity=filled_qty,
                            fill_timestamp_ns=ts,
                            fee_krw=filled_value * self.assumptions.fee_rate,
                            adverse_selection_bps=self._calc_adverse(
                                reference_mid_at_placement, avg_price, "BUY"
                            ),
                            reason="FULL_FILL",
                        )

        # Events exhausted before fill
        if filled_qty > 0:
            avg_price = filled_value / filled_qty
            return MakerFill(
                order=order,
                status=OrderStatus.PARTIAL,
                fill_price=avg_price,
                fill_quantity=filled_qty,
                fill_timestamp_ns=ts if future_events else placement_ts,
                fee_krw=filled_value * self.assumptions.fee_rate,
                adverse_selection_bps=self._calc_adverse(
                    reference_mid_at_placement, avg_price, "BUY"
                ),
                reason="PARTIAL_FILL_EVENT_EXHAUSTION",
            )
        return MakerFill(order=order, status=OrderStatus.EXPIRED,
                          reason="NO_FILL_EVENT_EXHAUSTION")

    def _check_orderbook_fill_buy(
        self, event: CanonicalEvent, limit_price: float, remaining_qty: float, ts: int
    ) -> tuple[float, float] | None:
        """Check if an orderbook event triggers a passive BUY fill."""
        payload = event.payload
        asks = payload.get("asks", [])
        if not asks:
            return None

        best_ask = float(asks[0][0])
        model = self.assumptions.fill_model

        if model == FillModel.OPTIMISTIC:
            # Fill if price touches limit
            if best_ask <= limit_price:
                return (min(remaining_qty, float(asks[0][1])), limit_price)

        elif model == FillModel.BASE:
            # Fill if best ask crosses through limit price
            if best_ask <= limit_price:
                return (min(remaining_qty, float(asks[0][1])), limit_price)

        elif model == FillModel.CONSERVATIVE:
            # Only fill if best ask is at or below limit AND volume evidence exists
            if best_ask <= limit_price:
                # Conservative: only partial fill based on queue position
                available = float(asks[0][1])
                queue_ahead = available * self.assumptions.queue_multiplier
                fillable = max(0, remaining_qty - queue_ahead)
                if fillable > 1e-12:
                    return (min(fillable, remaining_qty), limit_price)

        return None

    def _check_trade_fill_buy(
        self, event: CanonicalEvent, limit_price: float, remaining_qty: float, ts: int
    ) -> tuple[float, float] | None:
        """Check if a trade event triggers a passive BUY fill."""
        payload = event.payload
        trade_price = float(payload.get("price", 0))
        trade_qty = float(payload.get("quantity", 0))
        aggressor = payload.get("aggressor_side", "")

        if trade_price <= 0 or trade_qty <= 0:
            return None

        model = self.assumptions.fill_model

        if model == FillModel.OPTIMISTIC:
            # Any sell aggression at or below limit
            if trade_price <= limit_price and aggressor in ("SELL", "sell", "ASK", "ask"):
                return (min(remaining_qty, trade_qty), min(trade_price, limit_price))

        elif model == FillModel.BASE:
            # Sell aggression that trades through limit level
            if trade_price <= limit_price and aggressor in ("SELL", "sell", "ASK", "ask"):
                return (min(remaining_qty, trade_qty), min(trade_price, limit_price))

        elif model == FillModel.CONSERVATIVE:
            # Sell aggression that clearly exceeds our assumed queue
            if trade_price <= limit_price and aggressor in ("SELL", "sell", "ASK", "ask"):
                queue_ahead = trade_qty * self.assumptions.queue_multiplier
                if trade_qty > queue_ahead:
                    fillable = min(remaining_qty, trade_qty - queue_ahead)
                    if fillable > 1e-12:
                        return (fillable, min(trade_price, limit_price))

        return None

    @staticmethod
    def _calc_adverse(ref_mid: float, fill_price: float, side: str) -> float:
        """Calculate adverse selection in bps."""
        if ref_mid <= 0:
            return 0.0
        if side == "BUY":
            return (ref_mid - fill_price) / ref_mid * 10000
        return (fill_price - ref_mid) / ref_mid * 10000
