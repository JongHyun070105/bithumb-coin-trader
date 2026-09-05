from __future__ import annotations

import unittest

from bithumb_coin_trader.research_statistics import (
    cscv_probability_backtest_overfitting,
    deflated_sharpe_ratio,
    stationary_bootstrap_indices,
    white_reality_check,
)


class ResearchStatisticsTests(unittest.TestCase):
    def test_stationary_bootstrap_is_deterministic_and_aligned(self) -> None:
        first = stationary_bootstrap_indices(
            12, mean_block_length=3, iterations=5, seed="fixed"
        )
        second = stationary_bootstrap_indices(
            12, mean_block_length=3, iterations=5, seed="fixed"
        )
        self.assertEqual(first, second)
        self.assertTrue(all(len(sample) == 12 for sample in first))

    def test_reality_check_uses_all_candidates_and_returns_probability(self) -> None:
        result = white_reality_check(
            {
                "weak": [0.00, 0.01, -0.01, 0.00] * 10,
                "strong": [0.01, 0.02, -0.005, 0.01] * 10,
            },
            iterations=200,
            seed="fixed",
        )
        self.assertGreater(result.observed_best_mean, 0)
        self.assertGreater(result.p_value, 0)
        self.assertLessEqual(result.p_value, 1)

    def test_deflated_sharpe_penalizes_more_trials(self) -> None:
        returns = [0.01, 0.02, -0.01, 0.015, 0.005] * 20
        few = deflated_sharpe_ratio(
            returns, trial_sharpes=[0.1, 0.2, 0.3], trial_count=3
        )
        many = deflated_sharpe_ratio(
            returns, trial_sharpes=[0.1, 0.2, 0.3], trial_count=100
        )
        self.assertGreater(few.probability, many.probability)
        self.assertLess(few.expected_maximum_sharpe, many.expected_maximum_sharpe)

    def test_deflated_sharpe_monotonicity_across_n_spectrum(self) -> None:
        """Verify mathematical monotonicity of DSR terms: E[max(SR)] increases, DSR prob decreases."""
        returns = [0.005, 0.01, -0.003, 0.008, -0.002] * 40
        trial_sharpes = [0.1 * i for i in range(1, 20)]
        n_spectrum = [1, 2, 3, 5, 10, 20, 40, 77, 100, 250, 500]

        prev_benchmark = -float("inf")
        prev_prob = float("inf")

        for n in n_spectrum:
            res = deflated_sharpe_ratio(returns, trial_sharpes=trial_sharpes, trial_count=n)
            # 1. Expected maximum Sharpe must be non-decreasing with N
            self.assertGreaterEqual(
                res.expected_maximum_sharpe,
                prev_benchmark - 1e-12,
                f"Benchmark decreased at N={n}",
            )
            # 2. DSR probability must be non-increasing with N
            self.assertLessEqual(
                res.probability,
                prev_prob + 1e-12,
                f"DSR probability increased at N={n}",
            )
            prev_benchmark = res.expected_maximum_sharpe
            prev_prob = res.probability

    def test_cscv_reports_exact_split_count(self) -> None:
        result = cscv_probability_backtest_overfitting(
            {
                "a": [0.01, 0.02, -0.01, 0.00] * 8,
                "b": [-0.01, 0.00, 0.02, 0.01] * 8,
            },
            blocks=8,
        )
        self.assertEqual(result.split_count, 70)
        self.assertGreaterEqual(result.probability_backtest_overfitting, 0)
        self.assertLessEqual(result.probability_backtest_overfitting, 1)

    def test_non_finite_returns_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            white_reality_check({"bad": [0.0, float("nan")]})


if __name__ == "__main__":
    unittest.main()

