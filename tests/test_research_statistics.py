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
