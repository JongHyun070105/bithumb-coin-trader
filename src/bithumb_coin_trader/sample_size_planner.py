"""Sample Size and Statistical Power Planner for Microstructure Research (P17).

Provides rigorous statistical power planning:
- Minimum sample size required for target Sharpe ratio given alpha and power.
- Effective sample size adjustment for autocorrelated returns (AR(1) adjustment).
- Minimum Detectable Effect Size / Sharpe Ratio (MDSR) for 72-hour dataset.
"""

from __future__ import annotations

import math
from typing import Mapping


# Standard normal quantiles (Z-scores)
Z_ALPHA = {
    0.05: 1.95996,   # Two-sided 5%
    0.01: 2.57583,   # Two-sided 1%
    0.001: 3.29053,  # Two-sided 0.1%
}

Z_POWER = {
    0.80: 0.84162,   # 80% power (beta = 0.20)
    0.90: 1.28155,   # 90% power (beta = 0.10)
    0.95: 1.64485,   # 95% power (beta = 0.05)
}


def compute_required_sample_size(
    target_sharpe_per_period: float,
    alpha: float = 0.01,
    power: float = 0.80,
    autocorrelation_rho: float = 0.0,
) -> int:
    """Calculates required number of observations to achieve desired power."""
    if target_sharpe_per_period <= 0:
        raise ValueError("target_sharpe_per_period must be strictly positive")
    if alpha not in Z_ALPHA:
        raise ValueError(f"Unsupported alpha {alpha}. Supported: {list(Z_ALPHA.keys())}")
    if power not in Z_POWER:
        raise ValueError(f"Unsupported power {power}. Supported: {list(Z_POWER.keys())}")
    if not (-0.99 < autocorrelation_rho < 0.99):
        raise ValueError("autocorrelation_rho must be in (-0.99, 0.99)")

    z_a = Z_ALPHA[alpha]
    z_b = Z_POWER[power]

    # i.i.d. required sample size
    t_iid = ((z_a + z_b) / target_sharpe_per_period) ** 2

    # Autocorrelation inflation factor: (1 + rho) / (1 - rho)
    inflation = (1.0 + autocorrelation_rho) / (1.0 - autocorrelation_rho)
    t_required = t_iid * inflation
    return int(math.ceil(t_required))


def compute_minimum_detectable_sharpe(
    sample_size: int,
    alpha: float = 0.01,
    power: float = 0.80,
    autocorrelation_rho: float = 0.0,
) -> float:
    """Calculates the minimum Sharpe ratio detectable with given sample size."""
    if sample_size <= 1:
        raise ValueError("sample_size must be > 1")

    z_a = Z_ALPHA.get(alpha, 2.57583)
    z_b = Z_POWER.get(power, 0.84162)

    # Effective sample size: N_eff = N * (1 - rho) / (1 + rho)
    inflation = (1.0 + autocorrelation_rho) / (1.0 - autocorrelation_rho)
    n_eff = sample_size / inflation

    mdsr = (z_a + z_b) / math.sqrt(n_eff)
    return float(mdsr)


def evaluate_72h_statistical_power(
    interval_seconds: float = 1.0,
    total_hours: float = 72.0,
    rho: float = 0.20,
) -> dict[str, float]:
    """Evaluates statistical detection power for the 72-hour soak window."""
    total_seconds = total_hours * 3600.0
    n_obs = int(total_seconds / interval_seconds)
    mdsr_01_80 = compute_minimum_detectable_sharpe(n_obs, alpha=0.01, power=0.80, autocorrelation_rho=rho)
    mdsr_05_80 = compute_minimum_detectable_sharpe(n_obs, alpha=0.05, power=0.80, autocorrelation_rho=rho)

    return {
        "total_observations": float(n_obs),
        "effective_sample_size": n_obs * (1.0 - rho) / (1.0 + rho),
        "mdsr_alpha_0_01_power_80": mdsr_01_80,
        "mdsr_alpha_0_05_power_80": mdsr_05_80,
    }
