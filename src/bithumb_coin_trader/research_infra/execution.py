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
- VWAP-to-VWAP PnL is authoritative: only subtract FEES from VWAP gross.
  Spread/depth slippage are already embedded in fill VWAPs.
"""

from __future__ import annotations

from bisect import bisect_left
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

    Note: ``additional_impact_bps`` models extra market impact beyond what the
    depth-walking VWAP already captures.  It is NOT a duplicate of spread or
    depth slippage.
    """

    fee_regime: str  # Key into FEE_REGIMES
    fee_rate: float
    additional_impact_bps: float  # Extra impact beyond depth-walking VWAP
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
        # Accept legacy key name for backward compat
        if "slippage_bps" in d and "additional_impact_bps" not in d:
            d = {**d, "additional_impact_bps": d["slippage_bps"]}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})  # type: ignore[arg-type]


# Default assumptions for different research scenarios
DEFAULT_TAKER_ASSUMPTIONS = ExecutionAssumptions(
    fee_regime="live_zero_fee",
    fee_rate=0.0000,
    additional_impact_bps=5.0,
    latency_ms=0.0,
    position_size_krw=100_000,
    max_depth_levels=5,
    passive_fills_enabled=False,
    partial_fills_enabled=True,
    description="Default taker execution: zero-fee event, 5bps additional impact, 0ms theoretical latency",
)

STRESS_TAKER_ASSUMPTIONS = ExecutionAssumptions(
    fee_regime="normal_fee",
    fee_rate=0.0025,
    additional_impact_bps=10.0,
    latency_ms=100.0,
    position_size_krw=100_000,
    max_depth_levels=3,
    passive_fills_enabled=False,
    partial_fills_enabled=True,
    description="Stress taker: normal fee, wider impact, higher latency",
)


@dataclass(frozen=True, slots=True)
class SimulatedTrade:
    """Result of a simulated trade execution."""

    timestamp_ns: int
    exchange: str
    market: str
    side: str  # "BUY" or "SELL"
    signal: str  # The hypothesis signal that triggered this
    latency_ms: float  # Actual latency used for this execution

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
    cumulative_fee: float = 0.0


class ResearchExecutionSimulator:
    """Parameterized execution simulator for research.

    Wraps the existing DeterministicTakerSimulator with:
    - Fee regime integration
    - Position tracking
    - Trade logging
    - PnL computation
    - Latency-aware execution (0ms/100ms/250ms/500ms)

    PnL accounting uses authoritative cash-flow reconciliation:
    - executable_gross_pnl = (exit_vwap - entry_vwap) * Q_closed
    - net_pnl = executable_gross_pnl - entry_fee - exit_fee
    Only fees are subtracted from VWAP-to-VWAP gross, because VWAP already
    embeds spread crossing and depth walking by definition of historical
    visible execution.
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
        """Execute a trading signal against the current orderbook (0ms theoretical latency).

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

        trade = self._build_trade(event, signal, result)

        # Update position
        if signal == "BUY":
            self._position = PositionState(
                is_flat=False,
                entry_price=trade.fill_price,
                entry_quantity=trade.fill_quantity,
                entry_notional=trade.notional_krw,
                entry_timestamp_ns=event.ordering_timestamp_ns,
                cumulative_fee=trade.fee_krw,
            )
        else:  # SELL (exit long)
            self._close_position(trade)

        self._trades.append(trade)
        return trade

    def execute_signal_with_latency(
        self,
        signal: str,
        signal_event: CanonicalEvent,
        events: Sequence[CanonicalEvent],
    ) -> SimulatedTrade | None:
        """Execute a signal with latency-aware book selection.

        Uses the first orderbook snapshot whose availability timestamp
        (ordering_timestamp_ns) >= signal_time + latency_ms.

        This models realistic execution: the decision is made at signal_event
        time, but the fill happens against a later book.

        Parameters
        ----------
        signal : str
            "BUY", "SELL", or "HOLD"
        signal_event : CanonicalEvent
            The event that triggered the signal.
        events : Sequence[CanonicalEvent]
            Stream of canonical events (at least the signal event and subsequent
            orderbook events).  Must include events AFTER signal time + latency.
        """
        if signal == "HOLD":
            return None

        if signal == "BUY" and not self._position.is_flat:
            return None
        if signal == "SELL" and self._position.is_flat:
            return None
        if signal == "SELL" and self._position.entry_quantity <= 0:
            return None

        latency_ms = self.assumptions.latency_ms
        signal_ts = signal_event.ordering_timestamp_ns
        target_fill_ts = signal_ts + int(latency_ms * 1_000_000)

        # Build sorted (timestamp, event) index of orderbook events
        ob_events: list[tuple[int, CanonicalEvent]] = []
        for ev in events:
            if ev.event_kind == EventKind.ORDERBOOK and ev.payload.get("bids") and ev.payload.get("asks"):
                ob_events.append((ev.ordering_timestamp_ns, ev))

        if not ob_events:
            return None

        ob_timestamps = [ts for ts, _ in ob_events]

        # Signal-time book (for adverse selection reference)
        idx_signal = bisect_left(ob_timestamps, signal_ts)
        if idx_signal >= len(ob_timestamps):
            return None
        order_time_event = ob_events[idx_signal][1]

        # Fill book: first at or after target_fill_ts
        idx_fill = bisect_left(ob_timestamps, target_fill_ts)
        if idx_fill >= len(ob_timestamps):
            return None
        fill_event = ob_events[idx_fill][1]

        # Validate: fill book must not be before signal
        if fill_event.ordering_timestamp_ns < signal_ts:
            return None

        # Compute mid-price at signal for reference
        try:
            signal_payload = order_time_event.payload
            signal_bids = signal_payload.get("bids", [])
            signal_asks = signal_payload.get("asks", [])
            if signal_bids and signal_asks:
                mid_price_at_signal = (float(signal_bids[0][0]) + float(signal_asks[0][0])) / 2.0
            else:
                mid_price_at_signal = 0.0
        except Exception:
            mid_price_at_signal = 0.0

        # Build orderbook snapshots for the core simulator
        try:
            order_time_ob = SimOrderBookSnapshot(
                timestamp=datetime.fromtimestamp(
                    order_time_event.ordering_timestamp_ns / 1_000_000_000, tz=timezone.utc
                ),
                bids=tuple((float(p), float(s)) for p, s in order_time_event.payload.get("bids", [])),
                asks=tuple((float(p), float(s)) for p, s in order_time_event.payload.get("asks", [])),
                market=order_time_event.market,
                validate=False,
            )
            fill_ob = SimOrderBookSnapshot(
                timestamp=datetime.fromtimestamp(
                    fill_event.ordering_timestamp_ns / 1_000_000_000, tz=timezone.utc
                ),
                bids=tuple((float(p), float(s)) for p, s in fill_event.payload.get("bids", [])),
                asks=tuple((float(p), float(s)) for p, s in fill_event.payload.get("asks", [])),
                market=fill_event.market,
                validate=False,
            )
        except Exception:
            return None

        # Build request
        request_ts = datetime.fromtimestamp(
            signal_ts / 1_000_000_000, tz=timezone.utc
        )
        if signal == "BUY":
            request = MarketOrderRequest(
                timestamp=request_ts,
                side="BUY",
                requested_amount_krw=self.assumptions.position_size_krw,
                fee_rate=self.assumptions.fee_rate,
                latency_delay_ms=latency_ms,
                allow_partial=self.assumptions.partial_fills_enabled,
                market=signal_event.market,
            )
        else:
            request = MarketOrderRequest(
                timestamp=request_ts,
                side="SELL",
                requested_quantity_btc=self._position.entry_quantity,
                fee_rate=self.assumptions.fee_rate,
                latency_delay_ms=latency_ms,
                allow_partial=self.assumptions.partial_fills_enabled,
                market=signal_event.market,
            )

        try:
            result = self._simulator.execute_order(request, fill_ob, order_time_ob)
        except Exception:
            return None

        if result.filled_quantity <= 0:
            return None

        trade = self._build_trade(fill_event, signal, result,
                                  mid_price_at_signal=mid_price_at_signal)

        # Update position
        if signal == "BUY":
            self._position = PositionState(
                is_flat=False,
                entry_price=trade.fill_price,
                entry_quantity=trade.fill_quantity,
                entry_notional=trade.notional_krw,
                entry_timestamp_ns=fill_event.ordering_timestamp_ns,
                cumulative_fee=trade.fee_krw,
            )
        else:
            self._close_position(trade)

        self._trades.append(trade)
        return trade

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_trade(
        self,
        event: CanonicalEvent,
        signal: str,
        result: ExecutionResult,
        *,
        mid_price_at_signal: float = 0.0,
    ) -> SimulatedTrade:
        """Construct a SimulatedTrade from an ExecutionResult."""
        if mid_price_at_signal == 0.0:
            mid_price_at_signal = result.mid_price_at_order

        return SimulatedTrade(
            timestamp_ns=event.ordering_timestamp_ns,
            exchange=event.exchange,
            market=event.market,
            side=signal,
            signal=signal,
            latency_ms=self.assumptions.latency_ms,
            fill_price=result.vwap_price,
            fill_quantity=result.filled_quantity,
            notional_krw=result.filled_amount_krw,
            fee_krw=result.fee_paid_krw,
            slippage_bps=result.slippage_vs_mid_bps,
            spread_at_fill_bps=result.slippage_vs_mid_bps,
            half_spread_cost_krw=result.half_spread_cost_krw,
            depth_slippage_cost_krw=result.depth_slippage_cost_krw,
            total_cost_krw=result.total_cost_krw,
            mid_price_at_signal=mid_price_at_signal,
            mid_price_at_fill=result.mid_price_at_fill,
            adverse_selection_bps=result.adverse_selection_bps,
        )

    def _close_position(self, sell_trade: SimulatedTrade) -> None:
        """Close the current long position and update equity curve.

        Authoritative PnL accounting:
          executable_gross = (exit_vwap - entry_vwap) * Q_closed
          net = executable_gross - entry_fee - exit_fee
        """
        entry_fee = self._position.cumulative_fee
        exit_fee = sell_trade.fee_krw
        gross_pnl = ((sell_trade.fill_price - self._position.entry_price)
                      * sell_trade.fill_quantity)
        net_pnl = gross_pnl - entry_fee - exit_fee
        self._initial_equity += net_pnl
        self._position = PositionState(is_flat=True)
        self._equity_curve.append((sell_trade.timestamp_ns, self._initial_equity))

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_trades(self) -> list[SimulatedTrade]:
        return list(self._trades)

    def get_equity_curve(self) -> list[tuple[int, float]]:
        return list(self._equity_curve)

    def get_unrealized_pnl(self, current_mid: float) -> float | None:
        """Get unrealized PnL for an open position."""
        if self._position.is_flat:
            return None
        return ((current_mid - self._position.entry_price)
                * self._position.entry_quantity
                - self._position.cumulative_fee)

    def get_pnl_summary(self) -> dict[str, Any]:
        """Compute PnL summary from simulated trades.

        Tracks round-trip trades (BUY→SELL pairs) with authoritative
        cash-flow reconciliation:
          executable_gross_pnl = (exit_vwap - entry_vwap) * Q_closed
          net_pnl = executable_gross_pnl - entry_fee - exit_fee
        """
        if not self._trades:
            return {
                "trade_count": 0,
                "round_trips": 0,
                "total_fees": 0.0,
                "total_spread_cost": 0.0,
                "total_depth_slippage": 0.0,
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

        gross_pnl = 0.0
        net_pnl = 0.0
        round_trips = 0
        for sell in sell_trades:
            matching_buys = [b for b in buy_trades if b.timestamp_ns < sell.timestamp_ns]
            if matching_buys:
                buy = matching_buys[-1]
                g = (sell.fill_price - buy.fill_price) * sell.fill_quantity
                gross_pnl += g
                # Only subtract FEES from VWAP-to-VWAP gross.
                # Spread/depth are already in the VWAPs.
                net_pnl += g - buy.fee_krw - sell.fee_krw
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
