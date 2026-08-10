from __future__ import annotations

from dataclasses import dataclass

from .models import Signal


@dataclass(frozen=True, slots=True)
class RiskLimits:
    minimum_order_krw: int = 5_000
    maximum_order_krw: int = 10_000
    maximum_daily_loss_fraction: float = 0.02
    maximum_drawdown_fraction: float = 0.10
    maximum_daily_entries: int = 1
    short_execution_enabled: bool = False


@dataclass(frozen=True, slots=True)
class RiskContext:
    requested_side: Signal
    requested_notional_krw: float
    current_equity_krw: float
    start_of_day_equity_krw: float
    peak_equity_krw: float
    daily_entries: int
    data_is_fresh: bool
    has_untracked_order: bool = False
    reference_price_krw: float | None = None


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[str, ...]


def evaluate_pretrade(context: RiskContext, limits: RiskLimits | None = None) -> RiskDecision:
    limits = limits or RiskLimits()
    reasons: list[str] = []
    if not context.data_is_fresh:
        reasons.append("market data is stale")
    if context.has_untracked_order:
        reasons.append("an order is untracked")
    if context.requested_side is Signal.SHORT and not limits.short_execution_enabled:
        reasons.append("short execution adapter is disabled")
    if context.requested_side is not Signal.FLAT:
        if context.requested_notional_krw < limits.minimum_order_krw:
            reasons.append("order is below the exchange minimum")
        if context.requested_notional_krw > limits.maximum_order_krw:
            reasons.append("order exceeds the configured maximum")
    if context.daily_entries >= limits.maximum_daily_entries:
        reasons.append("daily entry limit reached")
    if context.start_of_day_equity_krw > 0:
        daily_loss = 1 - context.current_equity_krw / context.start_of_day_equity_krw
        if daily_loss >= limits.maximum_daily_loss_fraction:
            reasons.append("daily loss limit reached")
    if context.peak_equity_krw > 0:
        drawdown = 1 - context.current_equity_krw / context.peak_equity_krw
        if drawdown >= limits.maximum_drawdown_fraction:
            reasons.append("maximum drawdown reached")
    return RiskDecision(not reasons, tuple(reasons))
