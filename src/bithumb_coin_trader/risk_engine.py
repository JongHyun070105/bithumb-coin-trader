"""Fail-Closed Risk and Execution-Preflight Engine (P6 - P6.5).

FORENSIC HARDENING (Phase 2.5):
- BUG-2 FIXED: max_slippage_bps is now actually enforced in evaluate_preflight().
  Prior to this fix, max_slippage_bps was defined in RiskEngineConfig but
  never read, making it a dead safety config field.
- BUG-3 FIXED: SELL orders now correctly DECREASE exposure. Prior to this fix,
  exposure was computed as (position + notional) / equity regardless of side,
  causing SELL orders to incorrectly show increasing exposure.
- ADDED: strict side validation — any value other than 'BUY' or 'SELL' triggers HALT.
- ADDED: invalid config rejection (non-positive limits trigger ValueError at init).
- ADDED: audit context_hash now includes side, notional, equity, position so that
  the same order_id with different parameters produces a different hash.

Features:
- Ternary risk verdicts: ALLOW, REJECT, HALT.
- Comprehensive pre-flight checks: max notional, gross exposure, spread filter,
  max_slippage_bps (NOW ENFORCED), data staleness, crossed/locked book.
- Dual circuit breakers: daily drawdown and consecutive execution failures.
- Software kill-switch (in-memory and file-based).
- Fail-closed default: any missing, NaN, Inf, or invalid input results in REJECT or HALT.
- Tamper-evident audit trail (in-memory list; not immutable — see LIMITATION).

LIMITATION: audit_log is an in-memory list. It is mutable and not persisted.
Claims of 'immutable audit trail' in earlier documentation are OVERCLAIMS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence


from .canonical_market_data import CanonicalOrderBook
from .execution_simulator import OrderBookSnapshot

@dataclass(frozen=True, slots=True)
class ExecutionCostEstimate:
    spread_crossing_bps: float
    depth_slippage_bps: float
    fee_bps: float
    total_execution_cost_bps: float
    fill_ratio: float
    expected_vwap: float
    visible_depth_krw: float

def simulate_taker_execution(
    side: str,
    requested_notional_krw: float,
    levels: tuple[tuple[float, float], ...],
    best_reference_price: float,
    taker_fee_bps: float = 40.0,
) -> ExecutionCostEstimate:
    if not levels or best_reference_price <= 0:
        return ExecutionCostEstimate(99999.0, 99999.0, taker_fee_bps, 99999.0, 0.0, 0.0, 0.0)

    visible_depth_krw = sum(px * sz for px, sz in levels)
    
    filled_notional = 0.0
    filled_size = 0.0
    for px, sz in levels:
        remaining = requested_notional_krw - filled_notional
        if remaining <= 0:
            break
        level_notional = px * sz
        fill_notional = min(remaining, level_notional)
        fill_size = fill_notional / px
        filled_notional += fill_notional
        filled_size += fill_size
        
    fill_ratio = filled_notional / requested_notional_krw if requested_notional_krw > 0 else 0.0
    expected_vwap = filled_notional / filled_size if filled_size > 0 else levels[0][0]
    
    best_px = levels[0][0]
    
    if side == "BUY":
        spread_crossing_bps = (best_px - best_reference_price) / best_reference_price * 10000.0
        depth_slippage_bps = (expected_vwap - best_px) / best_reference_price * 10000.0
    else:  # SELL
        spread_crossing_bps = (best_reference_price - best_px) / best_reference_price * 10000.0
        depth_slippage_bps = (best_px - expected_vwap) / best_reference_price * 10000.0
        
    spread_crossing_bps = max(0.0, spread_crossing_bps)
    depth_slippage_bps = max(0.0, depth_slippage_bps)
    
    total = spread_crossing_bps + depth_slippage_bps + taker_fee_bps
    
    return ExecutionCostEstimate(
        spread_crossing_bps=spread_crossing_bps,
        depth_slippage_bps=depth_slippage_bps,
        fee_bps=taker_fee_bps,
        total_execution_cost_bps=total,
        fill_ratio=fill_ratio,
        expected_vwap=expected_vwap,
        visible_depth_krw=visible_depth_krw,
    )



class RiskVerdict(str, Enum):
    ALLOW = "ALLOW"
    REJECT = "REJECT"
    HALT = "HALT"


@dataclass(frozen=True, slots=True)
class RiskAuditRecord:
    timestamp_ms: int
    order_id: str
    verdict: RiskVerdict
    reasons: tuple[str, ...]
    context_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_ms": self.timestamp_ms,
            "order_id": self.order_id,
            "verdict": self.verdict.value,
            "reasons": list(self.reasons),
            "context_hash": self.context_hash,
        }


@dataclass
class RiskEngineConfig:
    max_order_notional_krw: float = 10_000_000.0
    max_portfolio_exposure_fraction: float = 0.95
    max_spread_bps: float = 50.0
    max_slippage_bps: float = 30.0  # BUG-2 FIX: now actually enforced
    taker_fee_bps: float = 40.0
    max_total_execution_cost_bps: float = 80.0
    max_data_age_ms: float = 5000.0
    max_daily_loss_fraction: float = 0.05
    consecutive_rejection_limit: int = 3
    kill_switch_file: Path | str | None = None

    def __post_init__(self) -> None:
        """BUG-ADD: Reject obviously invalid config at construction time."""
        if self.max_order_notional_krw <= 0:
            raise ValueError(f"max_order_notional_krw must be > 0, got {self.max_order_notional_krw}")
        if not (0 < self.max_portfolio_exposure_fraction <= 1.0):
            raise ValueError(
                f"max_portfolio_exposure_fraction must be in (0, 1], got {self.max_portfolio_exposure_fraction}"
            )
        if self.max_spread_bps <= 0:
            raise ValueError(f"max_spread_bps must be > 0, got {self.max_spread_bps}")
        if self.max_slippage_bps <= 0:
            raise ValueError(f"max_slippage_bps must be > 0, got {self.max_slippage_bps}")
        if self.max_data_age_ms <= 0:
            raise ValueError(f"max_data_age_ms must be > 0, got {self.max_data_age_ms}")
        if not (0 < self.max_daily_loss_fraction <= 1.0):
            raise ValueError(
                f"max_daily_loss_fraction must be in (0, 1], got {self.max_daily_loss_fraction}"
            )
        if self.consecutive_rejection_limit <= 0:
            raise ValueError(
                f"consecutive_rejection_limit must be > 0, got {self.consecutive_rejection_limit}"
            )


_VALID_SIDES = frozenset({"BUY", "SELL"})


class RiskEngine:
    """Fail-closed risk and execution-preflight evaluation engine."""

    def __init__(self, config: RiskEngineConfig | None = None, audit_sink_path: Path | str | None = None) -> None:
        self.config = config or RiskEngineConfig()
        self.halted: bool = False
        self.halt_reason: str = ""
        self.kill_switch_active: bool = False
        self.consecutive_rejections: int = 0
        self.audit_log: list[RiskAuditRecord] = []  # In-memory; not immutable (see LIMITATION)
        self._audit_sink_path = audit_sink_path

    def set_kill_switch(self, active: bool) -> None:
        self.kill_switch_active = active

    def reset_circuit_breaker(self) -> None:
        self.halted = False
        self.halt_reason = ""
        self.consecutive_rejections = 0

    def record_execution_outcome(self, is_success: bool) -> None:
        """Tracks execution outcomes to trigger circuit breaker on persistent failures."""
        if is_success:
            self.consecutive_rejections = 0
        else:
            self.consecutive_rejections += 1
            if self.consecutive_rejections >= self.config.consecutive_rejection_limit:
                self.halted = True
                self.halt_reason = (
                    f"Consecutive execution rejections ({self.consecutive_rejections}) "
                    f"exceeded limit ({self.config.consecutive_rejection_limit})"
                )

    def _estimate_slippage_bps(
        self,
        side: str,
        requested_notional_krw: float,
        best_bid: float,
        best_ask: float,
    ) -> float:
        """BUG-2 FIX: Estimate expected slippage in bps from best price.

        Simplified model: slippage = |execution_price - reference_price| / reference_price * 10000.
        For BUY: execution at ask vs reference mid_price.
        For SELL: execution at bid vs reference mid_price.

        LIMITATION: This is a simplified point-estimate using only best bid/ask.
        Real slippage depends on order size vs available depth at each price level.
        For production, walk-the-book simulation is required.
        """
        if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
            return 99999.0  # Invalid book -> treat as infinite slippage
        mid_price = (best_bid + best_ask) / 2.0
        if mid_price <= 0:
            return 99999.0
        if side == "BUY":
            slippage_bps = (best_ask - mid_price) / mid_price * 10_000.0
        else:  # SELL
            slippage_bps = (mid_price - best_bid) / mid_price * 10_000.0
        return max(0.0, slippage_bps)

    def evaluate_preflight(
        self,
        order_id: str,
        side: str,
        requested_notional_krw: float,
        current_equity_krw: float,
        current_position_notional_krw: float,
        daily_loss_fraction: float,
        orderbook: OrderBookSnapshot | CanonicalOrderBook | None,
        current_time_ms: int,
    ) -> tuple[RiskVerdict, tuple[str, ...], RiskAuditRecord]:
        """Evaluates order preflight with fail-closed semantics."""
        reasons: list[str] = []

        # 0. BUG-ADD: strict side validation — before any other check
        if side not in _VALID_SIDES:
            reasons.append(f"Invalid order side '{side}': must be BUY or SELL")
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
            )

        # 1. Fail-closed validation of numeric inputs
        for name, val in [
            ("requested_notional_krw", requested_notional_krw),
            ("current_equity_krw", current_equity_krw),
            ("current_position_notional_krw", current_position_notional_krw),
            ("daily_loss_fraction", daily_loss_fraction),
            ("current_time_ms", current_time_ms),
        ]:
            if val is None or not math.isfinite(val):
                reasons.append(f"Input {name} is None or non-finite: {val}")
                return self._finalize_decision(
                    order_id, side, requested_notional_krw, current_equity_krw,
                    current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
                )

                # BUG-ADD: reject semantically invalid inputs
        if requested_notional_krw <= 0:
            reasons.append(f"requested_notional_krw must be > 0, got {requested_notional_krw}")
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
            )
        if current_equity_krw <= 0:
            reasons.append(f"current_equity_krw must be > 0, got {current_equity_krw}")
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
            )
        if current_position_notional_krw < 0:
            reasons.append(
                f"current_position_notional_krw < 0 is invalid: {current_position_notional_krw}"
            )
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
            )
        if side == 'SELL' and requested_notional_krw > current_position_notional_krw:
            reasons.append(f'INSUFFICIENT_POSITION: requested {requested_notional_krw} > position {current_position_notional_krw}')
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.REJECT, reasons, current_time_ms
            )
        if current_equity_krw <= 0:
            reasons.append(f"current_equity_krw must be > 0, got {current_equity_krw}")
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
            )
        if current_position_notional_krw < 0 and side == "BUY":
            # Negative position in spot BUY context is semantically invalid
            reasons.append(
                f"current_position_notional_krw < 0 for BUY order: {current_position_notional_krw}"
            )
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
            )

        # 2. Check system halted state
        if self.halted:
            reasons.append(f"System is HALTED: {self.halt_reason}")
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
            )

        # 3. Check software kill switch (in-memory or file)
        if self.kill_switch_active:
            reasons.append("Kill switch is ACTIVE in memory")
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
            )

        if self.config.kill_switch_file:
            ks_path = Path(self.config.kill_switch_file)
            if ks_path.exists():
                reasons.append(f"Kill switch file exists: {ks_path}")
                return self._finalize_decision(
                    order_id, side, requested_notional_krw, current_equity_krw,
                    current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
                )

                # 4. Daily drawdown circuit breaker
        if daily_loss_fraction < 0 or daily_loss_fraction > 1.0:
            reasons.append(f"Invalid daily_loss_fraction: {daily_loss_fraction}")
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
            )
        if daily_loss_fraction >= self.config.max_daily_loss_fraction:
            self.halted = True
            self.halt_reason = (
                f"Daily loss fraction {daily_loss_fraction:.4f} >= "
                f"limit {self.config.max_daily_loss_fraction:.4f}"
            )
            reasons.append(self.halt_reason)
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
            )

        # 5. Market data checks
        if orderbook is None:
            reasons.append("Orderbook snapshot is missing (None)")
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.REJECT, reasons, current_time_ms
            )

        # Handle OrderBookSnapshot vs CanonicalOrderBook
        if isinstance(orderbook, CanonicalOrderBook):
            ob_ts_ms = orderbook.receive_timestamp_ms
            best_bid = orderbook.best_bid
            best_ask = orderbook.best_ask
            spread_bps = (orderbook.spread / orderbook.mid_price * 10_000.0) if orderbook.mid_price > 0 else 99999.0
        else:
            ob_ts_sec = float(orderbook.timestamp.timestamp()) if hasattr(orderbook.timestamp, "timestamp") else float(orderbook.timestamp)
            ob_ts_ms = int(ob_ts_sec * 1000)
            best_bid = orderbook.best_bid
            best_ask = orderbook.best_ask
            spread_bps = orderbook.spread_bps

                # Stale data check
        age_ms = current_time_ms - ob_ts_ms
        if age_ms < -50:
            reasons.append(f"CLOCK_INVERSION: Market data is in the future: age {age_ms}ms")
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
            )
        if age_ms > self.config.max_data_age_ms:
            reasons.append(f"Market data is stale: age {age_ms}ms > limit {self.config.max_data_age_ms}ms")

        # Crossed/locked book check
        if best_bid >= best_ask or best_bid <= 0 or best_ask <= 0:
            reasons.append(f"Crossed, locked, or empty book detected: bid={best_bid}, ask={best_ask}")
            self.halted = True
            self.halt_reason = "Crossed, locked, or zero depth book detected"
            return self._finalize_decision(
                order_id, side, requested_notional_krw, current_equity_krw,
                current_position_notional_krw, RiskVerdict.HALT, reasons, current_time_ms
            )

        # Spread filter
        if spread_bps > self.config.max_spread_bps:
            reasons.append(f"Spread {spread_bps:.2f} bps exceeds limit {self.config.max_spread_bps:.2f} bps")

                # BUG-2 FIX: max_slippage_bps is now actually enforced
        estimated_slippage_bps = self._estimate_slippage_bps(
            side, requested_notional_krw, best_bid, best_ask
        )
        if estimated_slippage_bps > self.config.max_slippage_bps:
            reasons.append(
                f"Estimated slippage {estimated_slippage_bps:.2f} bps exceeds "
                f"limit {self.config.max_slippage_bps:.2f} bps"
            )

        mid_price = (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask > 0 else 0.0
        if mid_price > 0 and isinstance(orderbook, CanonicalOrderBook):
            levels = orderbook.asks if side == 'BUY' else orderbook.bids
            if levels:
                cost_estimate = simulate_taker_execution(
                    side, requested_notional_krw, levels, mid_price, self.config.taker_fee_bps
                )
                if cost_estimate.total_execution_cost_bps > self.config.max_total_execution_cost_bps:
                    reasons.append(
                        f"Execution cost {cost_estimate.total_execution_cost_bps:.2f} bps exceeds limit {self.config.max_total_execution_cost_bps:.2f} bps"
                    )

        # 6. Sizing and exposure checks
        if requested_notional_krw > self.config.max_order_notional_krw:
            reasons.append(
                f"Order notional {requested_notional_krw} KRW exceeds max {self.config.max_order_notional_krw} KRW"
            )

        # BUG-3 FIX: SELL orders DECREASE exposure; BUY orders INCREASE exposure
        if current_equity_krw > 0:
            if side == "BUY":
                future_position = current_position_notional_krw + requested_notional_krw
            else:  # SELL
                # Selling reduces position; floor at 0 for spot (cannot go short)
                future_position = max(0.0, current_position_notional_krw - requested_notional_krw)
            future_exposure = future_position / current_equity_krw
            if future_exposure > self.config.max_portfolio_exposure_fraction:
                reasons.append(
                    f"Future gross exposure {future_exposure:.4f} exceeds "
                    f"limit {self.config.max_portfolio_exposure_fraction:.4f}"
                )

        verdict = RiskVerdict.ALLOW if not reasons else RiskVerdict.REJECT
        return self._finalize_decision(
            order_id, side, requested_notional_krw, current_equity_krw,
            current_position_notional_krw, verdict, reasons, current_time_ms
        )

    def _finalize_decision(
        self,
        order_id: str,
        side: str,
        requested_notional_krw: float,
        current_equity_krw: float,
        current_position_notional_krw: float,
        verdict: RiskVerdict,
        reasons: Sequence[str],
        timestamp_ms: int,
    ) -> tuple[RiskVerdict, tuple[str, ...], RiskAuditRecord]:
        """BUG-ADD: context_hash now includes side, notional, equity, position.

        Prior to this fix, two calls with different parameters but same order_id
        and verdict could produce the same context_hash, reducing audit fidelity.
        """
        ctx = {
            "order_id": order_id,
            "side": side,
            "requested_notional_krw": requested_notional_krw,
            "current_equity_krw": current_equity_krw,
            "current_position_notional_krw": current_position_notional_krw,
            "verdict": verdict.value,
            "reasons": list(reasons),
            "timestamp_ms": timestamp_ms,
        }
        ctx_hash = hashlib.sha256(json.dumps(ctx, sort_keys=True).encode("utf-8")).hexdigest()
        audit = RiskAuditRecord(
            timestamp_ms=timestamp_ms,
            order_id=order_id,
            verdict=verdict,
            reasons=tuple(reasons),
            context_hash=ctx_hash,
        )
        self.audit_log.append(audit)
        
        if self._audit_sink_path:
            with open(self._audit_sink_path, 'a') as f:
                f.write(json.dumps(audit.to_dict()) + "\n")
                f.flush()
                
        return verdict, tuple(reasons), audit
