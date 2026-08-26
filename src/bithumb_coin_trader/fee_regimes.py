"""Multi-cost fee regimes for Bithumb spot trading research.

Supports:
- live_zero_fee: 0.00% fee + 5 bps slippage (current Bithumb zero-fee event)
- live_zero_fee_high_slip: 0.00% fee + 15 bps slippage (spread/slippage stress under zero fee)
- normal_fee: 0.25% fee + 5 bps slippage (post-event baseline survival)
- stress_2x: 0.50% fee + 10 bps slippage (2x cost stress)
- stress_3x: 0.75% fee + 15 bps slippage (3x extreme cost stress)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .config import TradingSettings


@dataclass(frozen=True, slots=True)
class FeeRegimeConfig:
    name: str
    fee_rate: float
    slippage_bps: float
    description: str


FEE_REGIMES: Mapping[str, FeeRegimeConfig] = {
    "live_zero_fee": FeeRegimeConfig(
        name="live_zero_fee",
        fee_rate=0.0000,
        slippage_bps=5.0,
        description="Current Bithumb live promotion: 0% maker/taker API fee with 5bps baseline slippage",
    ),
    "live_zero_fee_high_slip": FeeRegimeConfig(
        name="live_zero_fee_high_slip",
        fee_rate=0.0000,
        slippage_bps=15.0,
        description="0% fee environment with elevated spread/market impact (15bps slippage)",
    ),
    "normal_fee": FeeRegimeConfig(
        name="normal_fee",
        fee_rate=0.0025,
        slippage_bps=5.0,
        description="Standard post-promotion baseline: 0.25% fee + 5bps slippage (0.60% round-trip)",
    ),
    "stress_2x": FeeRegimeConfig(
        name="stress_2x",
        fee_rate=0.0050,
        slippage_bps=10.0,
        description="2x cost stress: 0.50% fee + 10bps slippage (1.20% round-trip)",
    ),
    "stress_3x": FeeRegimeConfig(
        name="stress_3x",
        fee_rate=0.0075,
        slippage_bps=15.0,
        description="3x extreme stress: 0.75% fee + 15bps slippage (1.80% round-trip)",
    ),
}


def get_fee_regime_settings(
    regime_name: str,
    *,
    initial_capital_krw: float = 100_000.0,
    allocation_fraction: float = 1.0,
) -> TradingSettings:
    """Return TradingSettings configured for the specified fee regime."""
    if regime_name not in FEE_REGIMES:
        raise ValueError(f"Unknown fee regime: {regime_name}. Supported: {list(FEE_REGIMES)}")
    config = FEE_REGIMES[regime_name]
    return TradingSettings(
        initial_capital_krw=initial_capital_krw,
        fee_rate=config.fee_rate,
        slippage_bps=config.slippage_bps,
        allocation_fraction=allocation_fraction,
        minimum_order_krw=5_000,
        maximum_order_krw=100_000,
        maximum_daily_entries=10,
        cash_reserve_krw=5_000,
    )
