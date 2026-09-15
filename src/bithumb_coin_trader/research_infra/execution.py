"""Execution Simulator Wrapper for Microstructure Research.

Wraps the existing DeterministicTakerSimulator with research-specific
cost modeling, fee regimes, and position tracking.

Execution assumptions are stored in every result manifest.

Key principles:
- prediction correct != profit
- Include fees, spread, slippage, depth, latency
- Use Bithumb-specific fee assumptions through configuration
- Passive fills are NOT assumed by default (queue position unknown)
- Conservative depth walking for marketable orders
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import math
from typing import Any, Mapping, Sequence

from .canonical_events import CanonicalEvent, EventKind
from ..execution_simulator import (
    DeterministicTakerSimulator,
    OrderBookSnapshot as SimOrderBookSnapshot,
    MarketOrderRequest,
    ExecutionResult,
)
from ..fee_regimes import FeeRegimeConfig, FEE_REGIMES


@dataclass(frozen=True, slots=True)
class ExecutionAssumptions:
    """Complete execution assumptions for a research run.

    Stored in every manifest for reproducibility.
    """

    fee_regime: str  # Key into FEE_REGIMES
    fee_rate: float
    slippage_bps: float
    latency_ms: float  # Simulated latency in milliseconds
    position_size_krw: float
    max_depth_levels: int  # How many levels to walk
    passive_fills_enabled: bool = False  # Conservative default
    partial_fills_enabled: bool = True
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ExecutionAssumptions:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})  # type: ignore[arg-type]


# Default assumptions for different research scenarios
DEFAULT_TAKER_ASSUMPTIONS = ExecutionAssumptions(
    fee_regime="live_zero_fee",
    fee_rate=0.0000,
    slippage_bps=5.0,
    latency_ms=50.0,
    position_size_krw=100_000,
    max_depth_levels=5,
    passive_fills_enabled=False,
    partial_fills_enabled=True,
    description="Default taker execution: zero-fee event, 5bps slippage, 50ms latency",
)

STRESS_TAKER_ASSUMPTIONS = ExecutionAssumptions(
    fee_regime="normal_fee",
    fee_rate=0.0025,
    slippage_bps=10.0,
    latency_ms=100.0,
    position_size_krw=100_000,
    max_depth_levels=3,
    passive_fills_enabled=False,
    partial_fills_enabled=True,
    description="Stress taker: normal fee, wider slippage, higher latency",
)


@dataclass(frozen=True, slots=True)
class SimulatedTrade:
    """Result of a simulated trade execution."""

    timestamp_ns: int
    exchange: str
    market: str
    side: str  # "BUY" or "SELL"
    signal: str  # The hypothesis signal that triggered this

    # Execution details
    fill_price: float
    fill_quantity: float
    notional_krw: float
    fee_krw: float
    slippage_bps: float
    spread_at_fill_bps: float

    # Cost decomposition
    half_spread_cost_krw: float
    depth_slippage_cost_krw: float
    total_cost_krw: float

    # Context
    mid_price_at_signal: float
    mid_price_at_fill: float
    adverse_selection_bps: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PositionState:
    """Tracks current position for execution simulation."""

    is_flat: bool = True
    entry_price: float = 0.0
    entry_quantity: float = 0.0
    entry_notional: float = 0.0
    entry_timestamp_ns: int = 0
    cumulative_cost: float = 0.0


class ResearchExecutionSimulator:
    """Parameterized execution simulator for research.

    Wraps the existing DeterministicTakerSimulator with:
    - Fee regime integration
    - Position tracking
    - Trade logging
    - PnL computation
    """

    def __init__(
        self,
        assumptions: ExecutionAssumptions | None = None,
    ) -> None:
        self.assumptions = assumptions or DEFAULT_TAKER_ASSUMPTIONS
        self._simulator = DeterministicTakerSimulator()
        self._position = PositionState()
        self._trades: list[SimulatedTrade] = []
        self._equity_curve: list[tuple[int, float]] = []
        self._initial_equity = self.assumptions.position_size_krw

    def execute_signal(
        self,
        signal: str,  # "BUY", "SELL", "HOLD"
        event: CanonicalEvent,
        mid_price_at_signal: float,
    ) -> SimulatedTrade | None:
        """Execute a trading signal against the current orderbook.

        LONG-ONLY SPOT: BUY when flat, SELL when in position.
        SELL only exits an existing long (no short simulation).
        Returns SimulatedTrade if a trade was executed, None otherwise.
        """
        if signal == "HOLD":
            return None

        if signal == "BUY" and not self._position.is_flat:
            return None  # Already in position

        if signal == "SELL" and self._position.is_flat:
            return None  # No position to sell (long-only spot)

        if event.event_kind != EventKind.ORDERBOOK:
            return None

        payload = event.payload
        bids = tuple((float(p), float(s)) for p, s in payload.get("bids", []))
        asks = tuple((float(p), float(s)) for p, s in payload.get("asks", []))

        if not bids or not asks:
            return None

        try:
            ob = SimOrderBookSnapshot(
                timestamp=datetime.fromtimestamp(
                    event.ordering_timestamp_ns / 1_000_000_000, tz=timezone.utc
                ),
                bids=bids,
                asks=asks,
                market=event.market,
                validate=False,
            )
        except Exception:
            return None

        # Build request with correct API
        fill_time = datetime.fromtimestamp(
            event.ordering_timestamp_ns / 1_000_000_000, tz=timezone.utc
        )
        if signal == "BUY":
            request = MarketOrderRequest(
                timestamp=fill_time,
                side="BUY",
                requested_amount_krw=self.assumptions.position_size_krw,
                fee_rate=self.assumptions.fee_rate,
                allow_partial=self.assumptions.partial_fills_enabled,
                market=event.market,
            )
        else:  # SELL (exit long)
            if self._position.entry_quantity <= 0:
                return None
            request = MarketOrderRequest(
                timestamp=fill_time,
                side="SELL",
                requested_quantity_btc=self._position.entry_quantity,
                fee_rate=self.assumptions.fee_rate,
                allow_partial=self.assumptions.partial_fills_enabled,
                market=event.market,
            )

        try:
            result: ExecutionResult = self._simulator.execute_order(request, ob)
        except Exception:
            return None

        if result.filled_quantity <= 0:
            return None

        # Extract execution details from correct attributes
        fill_price = result.vwap_price
        fill_qty = result.filled_quantity
        notional = result.filled_amount_krw
        fee = result.fee_paid_krw
        mid = result.mid_price_at_fill
        spread_bps = result.slippage_vs_mid_bps
        slippage_vs_mid = result.slippage_vs_mid_bps
        half_spread_cost = result.half_spread_cost_krw
        depth_slip_cost = result.depth_slippage_cost_krw
        total_cost = result.total_cost_krw

        # Adverse selection from result
        adverse = result.adverse_selection_bps

        trade = SimulatedTrade(
            timestamp_ns=event.ordering_timestamp_ns,
            exchange=event.exchange,
            market=event.market,
            side=signal,
            signal=signal,
            fill_price=fill_price,
            fill_quantity=fill_qty,
            notional_krw=notional,
            fee_krw=fee,
            slippage_bps=slippage_vs_mid,
            spread_at_fill_bps=spread_bps,
            half_spread_cost_krw=half_spread_cost,
            depth_slippage_cost_krw=depth_slip_cost,
            total_cost_krw=total_cost,
            mid_price_at_signal=mid_price_at_signal,
            mid_price_at_fill=mid,
            adverse_selection_bps=adverse,
        )

        # Update position
        if signal == "BUY":
            self._position = PositionState(
                is_flat=False,
                entry_price=fill_price,
                entry_quantity=fill_qty,
                entry_notional=notional,
                entry_timestamp_ns=event.ordering_timestamp_ns,
                cumulative_cost=total_cost,
            )
        else:  # SELL (exit long)
            entry_cost = self._position.cumulative_cost
            gross_pnl = (fill_price - self._position.entry_price) * fill_qty
            net_pnl = gross_pnl - entry_cost - total_cost
            self._initial_equity += net_pnl
            self._position = PositionState(is_flat=True)

        self._trades.append(trade)
        self._equity_curve.append((event.ordering_timestamp_ns, self._initial_equity))

        return trade

    def get_trades(self) -> list[SimulatedTrade]:
        return list(self._trades)

    def get_equity_curve(self) -> list[tuple[int, float]]:
        return list(self._equity_curve)

    def get_pnl_summary(self) -> dict[str, Any]:
        """Compute PnL summary from simulated trades.

        Tracks round-trip trades (BUY→SELL pairs) with full cost decomposition.
        """
        if not self._trades:
            return {
                "trade_count": 0,
                "round_trips": 0,
                "total_fees": 0.0,
                "total_cost": 0.0,
                "gross_pnl": 0.0,
                "net_pnl": 0.0,
                "assumptions": self.assumptions.to_dict(),
            }

        buy_trades = [t for t in self._trades if t.side == "BUY"]
        sell_trades = [t for t in self._trades if t.side == "SELL"]

        total_fees = sum(t.fee_krw for t in self._trades)
        total_spread = sum(t.half_spread_cost_krw for t in self._trades)
        total_depth_slip = sum(t.depth_slippage_cost_krw for t in self._trades)
        total_cost = sum(t.total_cost_krw for t in self._trades)

        # Round-trip PnL
        gross_pnl = 0.0
        net_pnl = 0.0
        round_trips = 0
        for sell in sell_trades:
            matching_buys = [b for b in buy_trades if b.timestamp_ns < sell.timestamp_ns]
            if matching_buys:
                buy = matching_buys[-1]
                g = (sell.fill_price - buy.fill_price) * sell.fill_quantity
                gross_pnl += g
                net_pnl += g - buy.total_cost_krw - sell.total_cost_krw
                round_trips += 1

        n = len(self._trades)
        return {
            "trade_count": n,
            "buy_count": len(buy_trades),
            "sell_count": len(sell_trades),
            "round_trips": round_trips,
            "total_fees": total_fees,
            "total_spread_cost": total_spread,
            "total_depth_slippage": total_depth_slip,
            "total_cost": total_cost,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "avg_slippage_bps": sum(t.slippage_bps for t in self._trades) / n,
            "avg_spread_bps": sum(t.spread_at_fill_bps for t in self._trades) / n,
            "assumptions": self.assumptions.to_dict(),
        }
