"""Toy Cases and Property Tests for White Reality Check (P0.3).

Evaluates the 5 explicit deterministic toy cases:
Case A: All candidates zero edge
Case B: One clearly positive deterministic candidate
Case C: Identical candidate duplication
Case D: One extreme outlier trial
Case E: Negatively performing candidate set
"""

from __future__ import annotations

import math
import unittest

from tests.reference_wrc import reference_white_reality_check


class WhiteRealityCheckToyCasesTests(unittest.TestCase):
    def test_toy_case_a_all_candidates_zero_edge(self) -> None:
        """When all candidates have exact zero expected edge, p-value is large (>0.3)."""
        # Zero-mean symmetric return series
        zero_series_1 = [0.01, -0.01, 0.02, -0.02] * 25  # mean = 0.0
        zero_series_2 = [0.015, -0.015, 0.005, -0.005] * 25  # mean = 0.0

        res = reference_white_reality_check(
            {"cand_1": zero_series_1, "cand_2": zero_series_2},
            iterations=500,
            seed=101,
        )
        self.assertAlmostEqual(res.observed_best_mean, 0.0, places=7)
        # Because observed best is 0.0 and bootstrap is centered at 0.0,
        # max of centered means >= 0 with high probability (typically >= 0.5)
        self.assertGreater(res.p_value, 0.40)

    def test_toy_case_b_one_clearly_positive_deterministic_candidate(self) -> None:
        """When one candidate has a massive, statistically overwhelming positive edge, p-value -> 0."""
        noise = [0.001, -0.001, 0.002, -0.002] * 25
        stellar = [0.05, 0.04, 0.06, 0.045] * 25  # mean ~ 4.8% per period

        res = reference_white_reality_check(
            {"noise": noise, "stellar": stellar},
            iterations=500,
            seed=102,
        )
        self.assertGreater(res.observed_best_mean, 0.04)
        # Under H0 (centered), bootstrap maxima can never reach 0.048
        self.assertLess(res.p_value, 0.01)

    def test_toy_case_c_identical_candidate_duplication(self) -> None:
        """Duplicating identical candidates does not change observed max and has minimal bootstrap impact."""
        c1 = [0.005, -0.002, 0.008, 0.001, -0.004] * 20
        c2 = [0.002, 0.001, -0.001, 0.003, -0.002] * 20

        res_orig = reference_white_reality_check(
            {"c1": c1, "c2": c2},
            iterations=300,
            seed=103,
        )
        res_dup = reference_white_reality_check(
            {"c1": c1, "c2": c2, "c1_dup1": c1, "c1_dup2": c1},
            iterations=300,
            seed=103,
        )
        self.assertAlmostEqual(res_orig.observed_best_mean, res_dup.observed_best_mean, places=10)
        # Duplication of identical candidates produces identical maxima in each bootstrap draw
        self.assertAlmostEqual(res_orig.p_value, res_dup.p_value, places=7)

    def test_toy_case_d_one_extreme_outlier_trial(self) -> None:
        """An extreme single outlier in one candidate does not cause crash or NaN."""
        normal_cand = [0.001, -0.001] * 50
        outlier_cand = [0.001, -0.001] * 49 + [1.0, -1.0]

        res = reference_white_reality_check(
            {"normal": normal_cand, "outlier": outlier_cand},
            iterations=200,
            seed=104,
        )
        self.assertFalse(any(math.isnan(m) for m in res.bootstrap_max_distribution))
        self.assertTrue(0.0 <= res.p_value <= 1.0)

    def test_toy_case_e_negatively_performing_candidate_set(self) -> None:
        """When all candidates have negative mean excess return, p-value >= 0.5."""
        neg1 = [-0.01, -0.02, -0.005, -0.015] * 25
        neg2 = [-0.02, -0.01, -0.03, -0.005] * 25

        res = reference_white_reality_check(
            {"neg1": neg1, "neg2": neg2},
            iterations=500,
            seed=105,
        )
        self.assertLess(res.observed_best_mean, 0.0)
        # Centered bootstrap mean has mean 0 > obs_best_mean, so bootstrap max >= obs_best_mean almost always
        self.assertGreaterEqual(res.p_value, 0.90)


if __name__ == "__main__":
    import math
    unittest.main()
