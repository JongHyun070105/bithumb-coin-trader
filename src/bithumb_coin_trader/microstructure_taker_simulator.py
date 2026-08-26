"""Realistic High-Precision Taker Execution Simulator for Strategy V9.

Models:
1. Full L2 Orderbook VWAP Depth Sweep
2. Multi-Latency Scenarios (50ms, 100ms, 250ms, 500ms, 1000ms)
3. Spread + Slippage + Adverse Selection in 0% Fee Regime
4. Partial Liquidity & Insufficient Depth Rejection
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Sequence

from .microstructure_features import OrderbookSnapshot


@dataclass(frozen=True, slots=True)
class FillSlice:
    price: float
    size: float
    notional_krw: float


@dataclass(frozen=True, slots=True)
class TakerExecutionResult:
    market: str
    side: str  # "BUY" or "SELL"
    requested_notional_krw: float
    filled_notional_krw: float
    filled_size: float
    vwap_price: float
    best_price_at_order: float
    spread_bps_at_order: float
    slippage_bps: float
    effective_cost_krw: float
    latency_ms: float
    is_partial_fill: bool
    is_rejected: bool
    rejection_reason: str = ""
    fill_slices: tuple[FillSlice, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "side": self.side,
            "requested_krw": round(self.requested_notional_krw, 2),
            "filled_krw": round(self.filled_notional_krw, 2),
            "filled_size": round(self.filled_size, 8),
            "vwap_price": round(self.vwap_price, 2),
            "best_price": round(self.best_price_at_order, 2),
            "spread_bps": round(self.spread_bps_at_order, 2),
            "slippage_bps": round(self.slippage_bps, 2),
            "effective_cost_krw": round(self.effective_cost_krw, 2),
            "latency_ms": self.latency_ms,
            "is_partial": self.is_partial_fill,
            "is_rejected": self.is_rejected,
            "rejection_reason": self.rejection_reason,
            "num_depth_levels_swept": len(self.fill_slices),
        }


class RealisticTakerExecutionSimulator:
    """Simulates market order execution by sweeping L2 orderbook depth."""

    def __init__(
        self,
        default_latency_ms: float = 100.0,
        adverse_selection_bps_per_sec: float = 2.0,
    ) -> None:
        self.default_latency_ms = default_latency_ms
        self.adverse_bps_per_sec = adverse_selection_bps_per_sec

    def execute_market_order(
        self,
        orderbook: OrderbookSnapshot,
        side: str,
        target_notional_krw: float,
        latency_ms: float | None = None,
    ) -> TakerExecutionResult:
        if target_notional_krw <= 0:
            return TakerExecutionResult(
                market=orderbook.market,
                side=side,
                requested_notional_krw=target_notional_krw,
                filled_notional_krw=0.0,
                filled_size=0.0,
                vwap_price=0.0,
                best_price_at_order=0.0,
                spread_bps_at_order=0.0,
                slippage_bps=0.0,
                effective_cost_krw=0.0,
                latency_ms=0.0,
                is_partial_fill=False,
                is_rejected=True,
                rejection_reason="zero_or_negative_notional",
            )

        side_upper = side.upper()
        lat = latency_ms if latency_ms is not None else self.default_latency_ms
        levels = orderbook.asks if side_upper == "BUY" else orderbook.bids
        best_price = orderbook.best_ask if side_upper == "BUY" else orderbook.best_bid

        if not levels or best_price <= 0:
            return TakerExecutionResult(
                market=orderbook.market,
                side=side_upper,
                requested_notional_krw=target_notional_krw,
                filled_notional_krw=0.0,
                filled_size=0.0,
                vwap_price=0.0,
                best_price_at_order=0.0,
                spread_bps_at_order=orderbook.spread_bps,
                slippage_bps=0.0,
                effective_cost_krw=0.0,
                latency_ms=lat,
                is_partial_fill=False,
                is_rejected=True,
                rejection_reason="empty_orderbook",
            )

        # Adverse selection adjustment based on latency
        adverse_factor = 1.0 + (self.adverse_bps_per_sec * (lat / 1000.0) / 10_000.0)
        remaining_krw = target_notional_krw
        slices: list[FillSlice] = []
        total_size = 0.0

        for price, size in levels:
            adj_price = price * adverse_factor if side_upper == "BUY" else price / adverse_factor
            level_krw = adj_price * size

            if level_krw <= remaining_krw:
                slices.append(FillSlice(price=adj_price, size=size, notional_krw=level_krw))
                total_size += size
                remaining_krw -= level_krw
            else:
                # Partial fill of this depth level
                needed_size = remaining_krw / adj_price
                slices.append(FillSlice(price=adj_price, size=needed_size, notional_krw=remaining_krw))
                total_size += size
                remaining_krw = 0.0
                break

        filled_krw = target_notional_krw - remaining_krw
        if filled_krw <= 0 or total_size <= 0:
            return TakerExecutionResult(
                market=orderbook.market,
                side=side_upper,
                requested_notional_krw=target_notional_krw,
                filled_notional_krw=0.0,
                filled_size=0.0,
                vwap_price=0.0,
                best_price_at_order=best_price,
                spread_bps_at_order=orderbook.spread_bps,
                slippage_bps=0.0,
                effective_cost_krw=0.0,
                latency_ms=lat,
                is_partial_fill=False,
                is_rejected=True,
                rejection_reason="insufficient_liquidity",
            )

        vwap = sum(s.notional_krw for s in slices) / sum(s.size for s in slices)
        slippage_bps = abs(vwap - best_price) / best_price * 10_000.0 if best_price > 0 else 0.0
        effective_cost = filled_krw * (slippage_bps / 10_000.0)
        is_partial = remaining_krw > 0.0

        return TakerExecutionResult(
            market=orderbook.market,
            side=side_upper,
            requested_notional_krw=target_notional_krw,
            filled_notional_krw=filled_krw,
            filled_size=total_size,
            vwap_price=vwap,
            best_price_at_order=best_price,
            spread_bps_at_order=orderbook.spread_bps,
            slippage_bps=slippage_bps,
            effective_cost_krw=effective_cost,
            latency_ms=lat,
            is_partial_fill=is_partial,
            is_rejected=False,
            fill_slices=tuple(slices),
        )


@dataclass(frozen=True, slots=True)
class MakerSimulatorSpecification:
    """Specification of required Maker simulation components (Currently Not Validatable)."""

    status: str = "not_yet_validatable"
    reason: str = (
        "Maker order simulation requires exact L3 tick-level order queue tracking and order cancellation "
        "reconstruction. Applying zero-fee maker assumptions without true queue data causes massive backtest overestimation."
    )
    required_features: tuple[str, ...] = (
        "queue_position_ahead",
        "subsequent_opposite_trade_volume",
        "cancellation_rate_distribution",
        "adverse_selection_fill_probability",
    )
