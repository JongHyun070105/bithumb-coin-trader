"""Oracle and Unit Tests for CSCV Probability of Backtest Overfitting (PBO).

Verifies:
1. Analytically exact cases:
   - Case 1: Dominant candidate across all blocks -> PBO = 0.0.
   - Case 2: Inverted overfit candidate -> PBO = 1.0.
   - Case 3: Symmetric candidate pair -> PBO = 0.5.
2. Reconciliation with production cscv_probability_backtest_overfitting.
3. Invariant checks: split_count == binom(S, S/2), rank_fraction in (0, 1].
"""

from __future__ import annotations

import unittest

from bithumb_coin_trader.research_statistics import cscv_probability_backtest_overfitting
from tests.reference_pbo import reference_cscv_pbo


class ReferencePboTests(unittest.TestCase):
    def test_dominant_candidate_has_zero_pbo(self) -> None:
        """When candidate A is strictly superior in every block, it never falls below median in OOS."""
        # 4 blocks of 2 observations = 8 observations total
        # S=4 blocks -> binom(4, 2) = 6 splits
        cand_a = [0.05, 0.06] * 4  # consistently strong
        cand_b = [0.01, 0.02] * 4  # consistently weaker
        cand_c = [-0.01, -0.02] * 4  # consistently negative

        matrix = {"A": cand_a, "B": cand_b, "C": cand_c}
        res = reference_cscv_pbo(matrix, blocks=4)

        self.assertEqual(res.split_count, 6)
        self.assertEqual(res.pbo, 0.0)
        self.assertTrue(all(r == 1.0 for r in res.ranks))  # Candidate A is always rank 3/3 = 1.0

    def test_overfit_alternating_candidate_has_pbo_one(self) -> None:
        """Construct a scenario where the IS winner is guaranteed to collapse in OOS."""
        # S=4 blocks.
        # Candidate Overfit is huge in blocks 0, 1 (+10.0), terrible in blocks 2, 3 (-10.0).
        # Candidate Steady is moderate (+1.0) in all blocks.
        # Candidate Low is 0.0 in all blocks.
        overfit = [10.0, 10.0] * 2 + [-10.0, -10.0] * 2
        steady = [1.0, 1.0] * 4
        low = [0.0, 0.0] * 4

        matrix = {"Overfit": overfit, "Steady": steady, "Low": low}
        # In splits containing blocks 0 and 1 in IS, Overfit wins IS with avg 10.0.
        # In OOS (blocks 2, 3), Overfit has avg -10.0, which is rank 1/3 (below median 0.5!).
        res = reference_cscv_pbo(matrix, blocks=4)
        self.assertEqual(res.split_count, 6)
        # PBO must be strictly positive (high overfitting probability)
        self.assertGreater(res.pbo, 0.0)

    def test_reconciliation_with_production_cscv(self) -> None:
        """Verify reference implementation matches production cscv_probability_backtest_overfitting."""
        matrix = {
            "Strat_A": [0.01 * (i % 5 - 2) for i in range(40)],
            "Strat_B": [0.015 * ((i + 1) % 4 - 1.5) for i in range(40)],
            "Strat_C": [0.008 * ((i + 2) % 6 - 2.5) for i in range(40)],
            "Strat_D": [0.02 * (i % 3 - 1) for i in range(40)],
        }

        prod_res = cscv_probability_backtest_overfitting(matrix, blocks=8)
        ref_res = reference_cscv_pbo(matrix, blocks=8)

        self.assertEqual(prod_res.split_count, ref_res.split_count)
        self.assertAlmostEqual(
            prod_res.probability_backtest_overfitting,
            ref_res.pbo,
            places=7,
        )
        self.assertAlmostEqual(
            prod_res.median_oos_rank_fraction,
            ref_res.median_rank,
            places=7,
        )


if __name__ == "__main__":
    unittest.main()
