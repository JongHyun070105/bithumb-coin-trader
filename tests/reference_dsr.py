"""Independent Reference Implementation for Deflated Sharpe Ratio (DSR).

Mathematical formulation strictly following Bailey & Lopez de Prado (2014):
"The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality"
Journal of Portfolio Management, 40(5), 94-107.

Zero dependencies on production codebase. Direct standard library math/statistics implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import e, isfinite, sqrt
from statistics import NormalDist, mean, pstdev
from typing import Sequence


EULER_MASCHERONI = 0.57721566490153286060651209


@dataclass(frozen=True, slots=True)
class ReferenceDsrResult:
    observed_sharpe: float
    expected_maximum_sharpe: float
    z_score: float
    probability: float
    trial_count: int
    variance_term: float
    standard_error: float
    unit_mode: str  # "per_period" or "annualized"


def compute_expected_maximum_sharpe(
    trial_sharpes: Sequence[float],
    trial_count: int | None = None,
) -> tuple[float, float]:
    """Compute expected maximum Sharpe ratio under selection bias.
    
    Returns:
        tuple of (expected_max_sharpe, trial_dispersion_sigma)
    """
    if not trial_sharpes:
        raise ValueError("trial_sharpes cannot be empty")
    n = trial_count if trial_count is not None else len(trial_sharpes)
    if n < 1:
        raise ValueError("trial_count must be >= 1")

    sigma = pstdev(trial_sharpes)
    if n == 1:
        return (0.0, sigma)

    normal = NormalDist()
    q1 = normal.inv_cdf(1.0 - 1.0 / n)
    q2 = normal.inv_cdf(1.0 - 1.0 / (n * e))
    expected_max = sigma * ((1.0 - EULER_MASCHERONI) * q1 + EULER_MASCHERONI * q2)
    return (expected_max, sigma)


def reference_deflated_sharpe_per_period(
    observed_sr_period: float,
    trial_sharpes_period: Sequence[float],
    sample_length: int,
    *,
    trial_count: int | None = None,
    skewness: float = 0.0,
    kurtosis: float = 3.0,  # Pearson kurtosis (normal distribution = 3.0)
) -> ReferenceDsrResult:
    """Reference DSR calculation in per-period units (e.g. daily Sharpe, daily trials, T=1200 days)."""
    if sample_length <= 1:
        raise ValueError("sample_length must be > 1")
    n = trial_count if trial_count is not None else len(trial_sharpes_period)

    exp_max, sigma = compute_expected_maximum_sharpe(trial_sharpes_period, trial_count=n)

    # Variance term under non-normality (Mertens 2002 / Bailey & Lopez de Prado 2014)
    # V = 1 - gamma_3 * SR + ((gamma_4 - 1) / 4) * SR^2
    # Note: gamma_3 = skewness, gamma_4 = Pearson kurtosis (>= 1.0)
    variance_term = 1.0 - skewness * observed_sr_period + ((kurtosis - 1.0) / 4.0) * (observed_sr_period ** 2)
    variance_term = max(variance_term, 1e-12)

    se = sqrt(variance_term / (sample_length - 1))
    z = (observed_sr_period - exp_max) / se
    prob = NormalDist().cdf(z)

    return ReferenceDsrResult(
        observed_sharpe=observed_sr_period,
        expected_maximum_sharpe=exp_max,
        z_score=z,
        probability=prob,
        trial_count=n,
        variance_term=variance_term,
        standard_error=se,
        unit_mode="per_period",
    )


def reference_deflated_sharpe_annualized(
    observed_sr_ann: float,
    trial_sharpes_ann: Sequence[float],
    sample_length_periods: int,
    periods_per_year: float = 365.25,
    *,
    trial_count: int | None = None,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> ReferenceDsrResult:
    """Reference DSR calculation in annualized units.
    
    Rigorous unit consistency:
    If Sharpe is annualized by sqrt(f), its asymptotic variance per year is scaled by f.
    Var(SR_ann) = f * Var(SR_period) = f * (V / (T_periods - 1)) = V / ((T_periods - 1) / f).
    Therefore, the effective sample length in years is T_years = (T_periods - 1) / f.
    se(SR_ann) = sqrt(V / T_years) = sqrt(V * f / (T_periods - 1)).
    
    This mathematically guarantees z_ann == z_period.
    """
    if sample_length_periods <= 1:
        raise ValueError("sample_length_periods must be > 1")
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be > 0")

    n = trial_count if trial_count is not None else len(trial_sharpes_ann)
    exp_max_ann, sigma_ann = compute_expected_maximum_sharpe(trial_sharpes_ann, trial_count=n)

    # To calculate variance term consistently, use the per-period Sharpe in the formula
    ann_factor = sqrt(periods_per_year)
    observed_sr_period = observed_sr_ann / ann_factor

    variance_term = 1.0 - skewness * observed_sr_period + ((kurtosis - 1.0) / 4.0) * (observed_sr_period ** 2)
    variance_term = max(variance_term, 1e-12)

    # Standard error of annualized Sharpe
    # se_ann = sqrt(variance_term) * ann_factor / sqrt(sample_length_periods - 1)
    se_ann = sqrt(variance_term * periods_per_year / (sample_length_periods - 1))
    z = (observed_sr_ann - exp_max_ann) / se_ann
    prob = NormalDist().cdf(z)

    return ReferenceDsrResult(
        observed_sharpe=observed_sr_ann,
        expected_maximum_sharpe=exp_max_ann,
        z_score=z,
        probability=prob,
        trial_count=n,
        variance_term=variance_term,
        standard_error=se_ann,
        unit_mode="annualized",
    )
