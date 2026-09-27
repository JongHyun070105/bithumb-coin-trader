"""Explicit, reproducible cost assumptions for Bithumb spot research.

This model records configured assumptions; it does not fetch exchange schedules
or simulate order-book fills. Execution engines must bind the selected scenario
and separately model any behavior they do not support.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import itertools
import math
from typing import Any, Mapping, Sequence


class CostScenarioError(ValueError):
    """Raised when a spot cost scenario is incomplete or internally invalid."""


@dataclass(frozen=True, slots=True)
class SpotCostScenario:
    """Configured cost assumptions; every numeric value is explicit."""

    name: str
    maker_fee_bps: float
    taker_fee_bps: float
    slippage_bps: float
    latency_ms: float
    minimum_order_notional: float
    tick_size: float
    lot_size: float
    partial_fill_probability: float | None = None
    partial_fill_status: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise CostScenarioError("scenario name must be a non-empty string")
        for field_name in (
            "maker_fee_bps", "taker_fee_bps", "slippage_bps", "latency_ms",
            "minimum_order_notional", "tick_size", "lot_size",
        ):
            value = getattr(self, field_name)
            positive = field_name in {"minimum_order_notional", "tick_size", "lot_size"}
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or (value <= 0 if positive else value < 0)
            ):
                qualifier = "positive" if positive else "non-negative"
                raise CostScenarioError(f"{field_name} must be finite and {qualifier}")
        probability = self.partial_fill_probability
        if probability is None:
            if self.partial_fill_status != "UNSUPPORTED":
                raise CostScenarioError("partial fills require a probability or explicit UNSUPPORTED status")
        elif (
            isinstance(probability, bool)
            or not isinstance(probability, (int, float))
            or not math.isfinite(probability)
            or not 0.0 <= probability <= 1.0
            or self.partial_fill_status is not None
        ):
            raise CostScenarioError("partial_fill_probability must be in [0, 1] without an unsupported status")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SpotCostScenario:
        if not isinstance(value, Mapping):
            raise CostScenarioError("cost scenario must be an object")
        fields = set(cls.__dataclass_fields__)
        unknown = set(value) - fields
        if unknown:
            raise CostScenarioError(f"unknown cost scenario fields: {', '.join(sorted(unknown))}")
        missing = fields - value.keys()
        optional = {"partial_fill_probability", "partial_fill_status"}
        missing_required = missing - optional
        if missing_required:
            raise CostScenarioError(f"cost scenario fields missing: {', '.join(sorted(missing_required))}")
        try:
            return cls(**value)
        except TypeError as exc:
            raise CostScenarioError(f"invalid cost scenario fields: {exc}") from exc

    def fee_rate(self, order_type: str) -> float:
        normalized = order_type.upper()
        if normalized == "MAKER":
            return self.maker_fee_bps / 10_000.0
        if normalized == "TAKER":
            return self.taker_fee_bps / 10_000.0
        raise CostScenarioError("order_type must be MAKER or TAKER")

    def execution_price(self, reference_price: float, side: str) -> float:
        """Apply configured adverse slippage to a reference price."""
        if (
            isinstance(reference_price, bool)
            or not isinstance(reference_price, (int, float))
            or not math.isfinite(reference_price)
            or reference_price <= 0
        ):
            raise CostScenarioError("reference_price must be finite and positive")
        normalized = side.upper()
        direction = 1.0 if normalized == "BUY" else -1.0 if normalized == "SELL" else 0.0
        if direction == 0.0:
            raise CostScenarioError("side must be BUY or SELL")
        return reference_price * (1.0 + direction * self.slippage_bps / 10_000.0)

    def estimate_round_trip_cost(
        self,
        notional: float,
        *,
        entry_order_type: str = "TAKER",
        exit_order_type: str = "TAKER",
    ) -> dict[str, float]:
        """Estimate fee and configured slippage for equal-notional entry/exit."""
        if (
            isinstance(notional, bool)
            or not isinstance(notional, (int, float))
            or not math.isfinite(notional)
            or notional < self.minimum_order_notional
        ):
            raise CostScenarioError("notional must be finite and at least minimum_order_notional")
        entry_fee = notional * self.fee_rate(entry_order_type)
        exit_fee = notional * self.fee_rate(exit_order_type)
        one_side_slippage = notional * self.slippage_bps / 10_000.0
        fees = entry_fee + exit_fee
        slippage = one_side_slippage * 2.0
        return {
            "entry_fee_krw": entry_fee,
            "exit_fee_krw": exit_fee,
            "fees_paid_krw": fees,
            "slippage_cost_krw": slippage,
            "total_cost_krw": fees + slippage,
        }


def conservative_sensitivity_grid(
    baseline: SpotCostScenario,
    *,
    fee_additions_bps: Sequence[float],
    slippage_additions_bps: Sequence[float],
    latency_values_ms: Sequence[float],
) -> list[SpotCostScenario]:
    """Build a deterministic grid that never lowers baseline costs/latency."""
    if not fee_additions_bps or not slippage_additions_bps or not latency_values_ms:
        raise CostScenarioError("sensitivity dimensions must be non-empty")
    for name, values in (("fee_additions_bps", fee_additions_bps), ("slippage_additions_bps", slippage_additions_bps)):
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0.0
            for value in values
        ):
            raise CostScenarioError(f"{name} must contain finite non-negative additions")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        or value < baseline.latency_ms
        for value in latency_values_ms
    ):
        raise CostScenarioError("latency_values_ms must be finite and at least the baseline latency")

    scenarios: list[SpotCostScenario] = []
    combinations = itertools.product(fee_additions_bps, slippage_additions_bps, latency_values_ms)
    for index, (fee_addition, slip_addition, latency_ms) in enumerate(combinations, 1):
        scenarios.append(replace(
            baseline,
            name=f"{baseline.name}-sensitivity-{index:03d}",
            maker_fee_bps=baseline.maker_fee_bps + fee_addition,
            taker_fee_bps=baseline.taker_fee_bps + fee_addition,
            slippage_bps=baseline.slippage_bps + slip_addition,
            latency_ms=latency_ms,
        ))
    return scenarios
