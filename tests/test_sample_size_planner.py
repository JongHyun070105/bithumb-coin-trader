import pytest
from bithumb_coin_trader.sample_size_planner import (
    compute_required_sample_size,
    compute_minimum_detectable_sharpe,
    evaluate_72h_statistical_power,
)


def test_compute_required_sample_size():
    # Target Sharpe = 0.05 per observation, alpha = 0.01, power = 0.80
    n = compute_required_sample_size(0.05, alpha=0.01, power=0.80, autocorrelation_rho=0.0)
    assert n > 4000

    # With positive autocorrelation, required N increases
    n_autocorr = compute_required_sample_size(0.05, alpha=0.01, power=0.80, autocorrelation_rho=0.25)
    assert n_autocorr > n
    assert pytest.approx(n_autocorr / n, rel=0.05) == (1.25 / 0.75)


def test_72h_statistical_power():
    res = evaluate_72h_statistical_power(interval_seconds=1.0, total_hours=72.0, rho=0.20)
    assert res["total_observations"] == 72 * 3600
    assert res["effective_sample_size"] < res["total_observations"]
    # With ~259k observations, MDSR should be tiny (e.g. < 0.02)
    assert res["mdsr_alpha_0_01_power_80"] < 0.02
