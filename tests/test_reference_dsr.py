"""Unit and Oracle tests for Independent Reference DSR Implementation.

Verifies:
1. Mathematical equivalence between per-period and annualized DSR formulations.
2. Exact monotonicity across trial count spectrum N in (1, 2, 5, 10, 20, 77, 200).
3. Exact reconciliation against production deflated_sharpe_ratio.
4. Formal mathematical isolation of the Strategy V6 DSR unit-mismatch discrepancy.
"""

from __future__ import annotations

import math
from statistics import mean, pstdev
import unittest

from bithumb_coin_trader.research_statistics import deflated_sharpe_ratio
from tests.reference_dsr import (
    compute_expected_maximum_sharpe,
    reference_deflated_sharpe_annualized,
    reference_deflated_sharpe_per_period,
)


class ReferenceDsrTests(unittest.TestCase):
    def test_annualized_and_per_period_formulations_are_strictly_identical(self) -> None:
        """Prove z-score and probability are invariant to annualization scaling."""
        f = 365.25
        ann_factor = math.sqrt(f)

        # Synthetic daily Sharpes
        trial_sharpes_daily = [0.05, 0.08, -0.02, 0.04, 0.10, 0.07, 0.03]
        obs_daily = 0.09
        sample_length = 1200

        trial_sharpes_ann = [s * ann_factor for s in trial_sharpes_daily]
        obs_ann = obs_daily * ann_factor

        skewness = -0.3
        kurtosis = 4.2

        res_period = reference_deflated_sharpe_per_period(
            observed_sr_period=obs_daily,
            trial_sharpes_period=trial_sharpes_daily,
            sample_length=sample_length,
            trial_count=77,
            skewness=skewness,
            kurtosis=kurtosis,
        )

        res_ann = reference_deflated_sharpe_annualized(
            observed_sr_ann=obs_ann,
            trial_sharpes_ann=trial_sharpes_ann,
            sample_length_periods=sample_length,
            periods_per_year=f,
            trial_count=77,
            skewness=skewness,
            kurtosis=kurtosis,
        )

        self.assertAlmostEqual(res_period.z_score, res_ann.z_score, places=10)
        self.assertAlmostEqual(res_period.probability, res_ann.probability, places=10)
        self.assertAlmostEqual(
            res_period.expected_maximum_sharpe * ann_factor,
            res_ann.expected_maximum_sharpe,
            places=10,
        )

    def test_dsr_monotonicity_across_n_spectrum(self) -> None:
        """Verify expected max Sharpe is non-decreasing in N, DSR is non-increasing in N."""
        trials = [0.01 * i for i in range(-5, 15)]
        obs = 0.08
        n_values = [1, 2, 5, 10, 20, 77, 200]

        prev_benchmark = -float("inf")
        prev_prob = float("inf")

        for n in n_values:
            res = reference_deflated_sharpe_per_period(
                observed_sr_period=obs,
                trial_sharpes_period=trials,
                sample_length=500,
                trial_count=n,
            )
            # Non-decreasing hurdle
            self.assertGreaterEqual(
                res.expected_maximum_sharpe,
                prev_benchmark - 1e-12,
                f"Expected max Sharpe decreased at N={n}",
            )
            # Non-increasing probability
            self.assertLessEqual(
                res.probability,
                prev_prob + 1e-12,
                f"DSR probability increased at N={n}",
            )
            prev_benchmark = res.expected_maximum_sharpe
            prev_prob = res.probability

    def test_reconciliation_with_production_implementation(self) -> None:
        """Verify reference implementation matches production deflated_sharpe_ratio."""
        # Generate synthetic returns
        returns = [0.001 + (0.015 if i % 2 == 0 else -0.013) for i in range(300)]
        mean_r = mean(returns)
        vol_r = pstdev(returns)
        obs_sr = mean_r / vol_r
        trials = [obs_sr * 0.8, obs_sr * 0.9, obs_sr * 1.1]

        # Calculate empirical skewness and kurtosis
        std_rets = [(r - mean_r) / vol_r for r in returns]
        skew = mean([r**3 for r in std_rets])
        kurt = mean([r**4 for r in std_rets])

        prod_res = deflated_sharpe_ratio(
            returns,
            trial_sharpes=trials,
            trial_count=20,
        )

        ref_res = reference_deflated_sharpe_per_period(
            observed_sr_period=obs_sr,
            trial_sharpes_period=trials,
            sample_length=len(returns),
            trial_count=20,
            skewness=skew,
            kurtosis=kurt,
        )

        self.assertAlmostEqual(prod_res.observed_sharpe, ref_res.observed_sharpe, places=7)
        self.assertAlmostEqual(prod_res.expected_maximum_sharpe, ref_res.expected_maximum_sharpe, places=7)
        self.assertAlmostEqual(prod_res.probability, ref_res.probability, places=7)

    def test_v6_discrepancy_demonstration(self) -> None:
        """Analytically reproduce both the 61.47% historical result and the 1.0000 bug."""
        f = 365.25
        ann_factor = math.sqrt(f)

        # Historical V6 Core70/Sat30 values:
        # Annualized observed Sharpe: 1.575
        # 77-Trial Annualized Expected Max Sharpe: 1.425
        # Sample length: 1200 days
        obs_ann = 1.575
        exp_max_ann = 1.425
        sample_length_days = 1200

        # In consistent daily units:
        obs_daily = obs_ann / ann_factor
        exp_max_daily = exp_max_ann / ann_factor
        se_daily = 1.0 / math.sqrt(sample_length_days - 1)  # standard normal return assumption

        z_consistent = (obs_daily - exp_max_daily) / se_daily
        prob_consistent = 0.5 * (1.0 + math.erf(z_consistent / math.sqrt(2.0)))

        # In buggy mixed units (Annualized difference multiplied by sqrt(T_daily)):
        z_buggy = (obs_ann - exp_max_ann) * math.sqrt(sample_length_days - 1)
        prob_buggy = 0.5 * (1.0 + math.erf(z_buggy / math.sqrt(2.0)))

        # Assert consistent calculation yields ~60.7% (without skew/kurtosis adjustment)
        self.assertAlmostEqual(z_consistent, 0.2718, places=3)
        self.assertAlmostEqual(prob_consistent, 0.6071, places=3)

        # Assert buggy calculation yields inflated z ~ 5.19 and prob ~ 1.0000
        self.assertAlmostEqual(z_buggy, 5.194, places=3)
        self.assertGreater(prob_buggy, 0.999999)

        # Ratio of z-scores is exactly the annualization factor sqrt(365.25)
        self.assertAlmostEqual(z_buggy / z_consistent, ann_factor, places=8)


if __name__ == "__main__":
    unittest.main()
