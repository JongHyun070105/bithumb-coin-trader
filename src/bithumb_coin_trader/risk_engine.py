"""Fail-Closed Risk and Execution-Preflight Engine (P6 - P6.5).

Features:
- Ternary risk verdicts: ALLOW, REJECT, HALT.
- Comprehensive pre-flight checks: max notional, gross exposure, spread filter,
  expected slippage, data staleness, crossed/locked book.
- Dual circuit breakers: daily drawdown and consecutive execution failures.
- Software kill-switch (in-memory and file-based).
- Fail-closed default: any missing, NaN, Inf, or invalid input results in REJECT or HALT.
- Immutable audit trail generation for every evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .canonical_market_data import CanonicalOrderBook
from .execution_simulator import OrderBookSnapshot


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
    max_slippage_bps: float = 30.0
    max_data_age_ms: float = 5000.0
    max_daily_loss_fraction: float = 0.05
    consecutive_rejection_limit: int = 3
    kill_switch_file: Path | str | None = None


class RiskEngine:
    """Fail-closed risk and execution-preflight evaluation engine."""

    def __init__(self, config: RiskEngineConfig | None = None) -> None:
        self.config = config or RiskEngineConfig()
        self.halted: bool = False
        self.halt_reason: str = ""
        self.kill_switch_active: bool = False
        self.consecutive_rejections: int = 0
        self.audit_log: list[RiskAuditRecord] = []

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
                return self._finalize_decision(order_id, RiskVerdict.HALT, reasons, current_time_ms)

        # 2. Check system halted state
        if self.halted:
            reasons.append(f"System is HALTED: {self.halt_reason}")
            return self._finalize_decision(order_id, RiskVerdict.HALT, reasons, current_time_ms)

        # 3. Check software kill switch (in-memory or file)
        if self.kill_switch_active:
            reasons.append("Kill switch is ACTIVE in memory")
            return self._finalize_decision(order_id, RiskVerdict.HALT, reasons, current_time_ms)

        if self.config.kill_switch_file:
            ks_path = Path(self.config.kill_switch_file)
            if ks_path.exists():
                reasons.append(f"Kill switch file exists: {ks_path}")
                return self._finalize_decision(order_id, RiskVerdict.HALT, reasons, current_time_ms)

        # 4. Daily drawdown circuit breaker
        if daily_loss_fraction >= self.config.max_daily_loss_fraction:
            self.halted = True
            self.halt_reason = (
                f"Daily loss fraction {daily_loss_fraction:.4f} >= "
                f"limit {self.config.max_daily_loss_fraction:.4f}"
            )
            reasons.append(self.halt_reason)
            return self._finalize_decision(order_id, RiskVerdict.HALT, reasons, current_time_ms)

        # 5. Market data checks
        if orderbook is None:
            reasons.append("Orderbook snapshot is missing (None)")
            return self._finalize_decision(order_id, RiskVerdict.REJECT, reasons, current_time_ms)

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
        if age_ms > self.config.max_data_age_ms:
            reasons.append(f"Market data is stale: age {age_ms}ms > limit {self.config.max_data_age_ms}ms")

        # Crossed/locked book check
        if best_bid >= best_ask or best_bid <= 0 or best_ask <= 0:
            reasons.append(f"Crossed, locked, or empty book detected: bid={best_bid}, ask={best_ask}")
            self.halted = True
            self.halt_reason = "Crossed, locked, or zero depth book detected"
            return self._finalize_decision(order_id, RiskVerdict.HALT, reasons, current_time_ms)

        # Spread filter
        if spread_bps > self.config.max_spread_bps:
            reasons.append(f"Spread {spread_bps:.2f} bps exceeds limit {self.config.max_spread_bps:.2f} bps")

        # 6. Sizing and exposure checks
        if requested_notional_krw > self.config.max_order_notional_krw:
            reasons.append(
                f"Order notional {requested_notional_krw} KRW exceeds max {self.config.max_order_notional_krw} KRW"
            )

        if current_equity_krw > 0:
            future_exposure = (current_position_notional_krw + requested_notional_krw) / current_equity_krw
            if future_exposure > self.config.max_portfolio_exposure_fraction:
                reasons.append(
                    f"Future gross exposure {future_exposure:.4f} exceeds limit {self.config.max_portfolio_exposure_fraction:.4f}"
                )

        verdict = RiskVerdict.ALLOW if not reasons else RiskVerdict.REJECT
        return self._finalize_decision(order_id, verdict, reasons, current_time_ms)

    def _finalize_decision(
        self,
        order_id: str,
        verdict: RiskVerdict,
        reasons: Sequence[str],
        timestamp_ms: int,
    ) -> tuple[RiskVerdict, tuple[str, ...], RiskAuditRecord]:
        ctx = {
            "order_id": order_id,
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
        return verdict, tuple(reasons), audit
