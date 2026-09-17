from __future__ import annotations

import json
from pathlib import Path
import unittest

from bithumb_coin_trader.research_infra.evaluation import (
    compute_directional_diagnostics,
    compute_hit_rate,
    compute_spearman_rank_ic,
    rankdata_average,
)
from bithumb_coin_trader.project_state import resolve_project_state


REPO_ROOT = Path(__file__).resolve().parent.parent


class TestScientificCorrections(unittest.TestCase):
    """Exhaustive test suite verifying the scientific correction pass."""

    def test_maker_total_trials_aggregation_equals_70(self):
        """Verify Maker total trials sum to exactly 70 across Cycles 1, 2, and 3."""
        maker_report_path = REPO_ROOT / "research-artifacts/maker/reports/MAKER_RESULTS.json"
        self.assertTrue(maker_report_path.exists())
        with open(maker_report_path) as f:
            data = json.load(f)

        self.assertEqual(data["total_trials"], 70)

        # Also inspect cycle files directly
        c1_path = REPO_ROOT / "research-artifacts/maker/reports/MAKER_CYCLE1_BASELINE_RESULTS.json"
        c2_path = REPO_ROOT / "research-artifacts/maker/reports/MAKER_CYCLE2_REFINEMENT_RESULTS.json"
        c3_path = REPO_ROOT / "research-artifacts/maker/reports/MAKER_CYCLE3_DISCRIMINATING_RESULTS.json"

        with open(c1_path) as f:
            c1_data = json.load(f)
            c1_scenarios = len(c1_data["results"])
        with open(c2_path) as f:
            c2_data = json.load(f)
            c2_scenarios = len(c2_data)
        with open(c3_path) as f:
            c3_data = json.load(f)
            c3_scenarios = len(c3_data)

        self.assertEqual(c1_scenarios, 54)
        self.assertEqual(c2_scenarios, 4)
        self.assertEqual(c3_scenarios, 12)
        self.assertEqual(c1_scenarios + c2_scenarios + c3_scenarios, 70)

    def test_maker_best_candidate_exact_metrics(self):
        """Verify the raw XRP candidate in Cycle 3 has exact metrics and optimistic queue assumption noted."""
        c3_path = REPO_ROOT / "research-artifacts/maker/reports/MAKER_CYCLE3_DISCRIMINATING_RESULTS.json"
        with open(c3_path) as f:
            c3_data = json.load(f)

        target = next((item for item in c3_data if item["trial_id"] == "MAKER-C3-XRP-Q0.5-C20S-CONS"), None)
        self.assertIsNotNone(target)
        self.assertEqual(target["fills"], 54)
        self.assertAlmostEqual(target["fill_rate"], 54 / 5117, places=5)
        self.assertAlmostEqual(target["net_bps_per_fill"], 2.19785, places=4)
        self.assertIn("Q0.5", target["trial_id"])

        # Check Maker results summary
        maker_report_path = REPO_ROOT / "research-artifacts/maker/reports/MAKER_RESULTS.json"
        with open(maker_report_path) as f:
            summary = json.load(f)

        self.assertIn("RETROSPECTIVE_DEV_MARKET_SPECIFIC_LEAD", summary["classification"])

        best = summary["best_candidate"]
        self.assertEqual(best["trial_id"], "MAKER-C3-XRP-Q0.5-C20S-CONS")
        self.assertEqual(best["fills"], 54)
        self.assertAlmostEqual(best["net_bps"], 2.19785, places=4)
        self.assertIn("RETROSPECTIVE_DEV_MARKET_SPECIFIC_LEAD", best["scientific_status"])
        self.assertEqual(best["queue_multiplier"], 0.5)
        self.assertIn("OPTIMISTIC", best["queue_ahead_assumption"])

    def test_v4_terminal_semantics_distinction(self):
        """Verify V4 distinguishes finalized failure from unverified collector process state."""
        state = resolve_project_state()
        v4 = state.v4

        self.assertTrue(v4.validation_outcome_finalized)
        self.assertFalse(v4.collector_terminal_process_directly_verified)
        self.assertIn("NOT_VERIFIABLE", v4.collector_process)
        self.assertEqual(v4.exact_process_failure_mode, "UNKNOWN")
        self.assertIn("FAIL", v4.final_verdict)
        self.assertEqual(v4.dataset_research_usability, "NOT_RESEARCH_USABLE")

    def test_cross_exchange_execution_mode_label(self):
        """Verify cross-exchange report correctly labels execution mode as heuristic screen."""
        ce_path = REPO_ROOT / "research-artifacts/cross-exchange/reports/CROSS_EXCHANGE_RESULTS.json"
        with open(ce_path) as f:
            trials = json.load(f)

        self.assertEqual(len(trials), 124)
        for trial in trials:
            self.assertIn("HEURISTIC_ECONOMIC_SCREEN", trial["execution_mode"])
            self.assertIn("REAL_FUTURE_BOOK_EXECUTION = NOT RUN", trial["execution_mode"])

        state = resolve_project_state()
        self.assertEqual(state.cross_exchange.total_trials, 124)
        self.assertEqual(state.cross_exchange.taker_viable_count, 0)
        self.assertIn("HEURISTIC_ECONOMIC_SCREEN", state.cross_exchange.execution_mode)

    def test_cross_exchange_hypotheses_completeness(self):
        """Verify X1, X2, X5 were evaluated and X3, X4 were not run."""
        ce_path = REPO_ROOT / "research-artifacts/cross-exchange/reports/CROSS_EXCHANGE_RESULTS.json"
        with open(ce_path) as f:
            trials = json.load(f)

        trial_hypotheses = {t["hypothesis"] for t in trials}
        self.assertEqual(
            trial_hypotheses,
            {"X1_BINANCE_RETURN_LEAD", "X2_UPBIT_RETURN_LEAD", "X5_BASIS_DISLOCATION"},
        )
        self.assertFalse(any("X3" in h for h in trial_hypotheses))
        self.assertFalse(any("X4" in h for h in trial_hypotheses))

        state = resolve_project_state()
        self.assertEqual(state.cross_exchange.hypotheses_evaluated, "X1, X2, X5 (X3, X4 NOT RUN)")

    def test_rankdata_average_tie_handling(self):
        """Verify rankdata_average properly assigns fractional average ranks to ties."""
        # Pure ties
        ranks = rankdata_average([5.0, 5.0, 5.0])
        self.assertEqual(ranks, [2.0, 2.0, 2.0])

        # Partial ties
        ranks = rankdata_average([1.0, 2.0, 2.0, 4.0])
        self.assertEqual(ranks, [1.0, 2.5, 2.5, 4.0])

        # Strictly distinct
        ranks = rankdata_average([10.0, 30.0, 20.0])
        self.assertEqual(ranks, [1.0, 3.0, 2.0])

        # Constant vector rank correlation should be 0.0 when sample size >= 10
        ic, n = compute_spearman_rank_ic([0.0] * 10, list(range(10)))
        self.assertEqual(ic, 0.0)
        self.assertEqual(n, 10)

    def test_directional_diagnostics_nonzero_hit_rate(self):
        """Verify directional diagnostics separates nonzero hit rate from zero-return sample dilution."""
        # 10 samples: 8 zeros, 2 nonzero matching
        preds = [0.01, -0.02, 0.01, 0.01, -0.01, 0.01, -0.01, 0.01, 0.05, -0.05]
        targets = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.02, -0.03]

        diag = compute_directional_diagnostics(preds, targets)
        self.assertAlmostEqual(diag["zero_fraction_target"], 0.8)
        self.assertEqual(diag["nonzero_directional_hit_rate"], 1.0)  # 2 out of 2 matching

        # Raw hit rate counts zeros in denominator
        raw_hit, n = compute_hit_rate(preds, targets)
        self.assertEqual(n, 10)
        self.assertAlmostEqual(raw_hit, 0.2)  # 2 / 10 = 0.2

    def test_no_lookahead_invariant_preserved(self):
        """Verify scientific invariant that lookahead is strictly prevented."""
        current_state_path = REPO_ROOT / "project-state/CURRENT_STATE.json"
        with open(current_state_path) as f:
            cs = json.load(f)
        self.assertTrue(cs["cross_exchange_state"]["causal_availability_enforced"])
        self.assertTrue(cs["cross_exchange_state"]["timestamp_conflation_remediated"])

    def test_cross_exchange_confirmation_execution_artifact(self):
        """Verify real future book confirmation execution artifact exists and proves taker cost kill."""
        exec_artifact = REPO_ROOT / "research-artifacts/cross-exchange/reports/CROSS_EXCHANGE_CONFIRMATION_EXECUTION.json"
        self.assertTrue(exec_artifact.exists())
        with open(exec_artifact) as f:
            scenarios = json.load(f)

        self.assertEqual(len(scenarios), 36)
        # All scenarios have net_bps < 0 and verdict COST_KILLED_BY_SPREAD_AND_FEES
        for scenario in scenarios:
            self.assertLess(scenario["net_bps"], 0.0)
            self.assertIn("COST_KILLED_BY_SPREAD_AND_FEES", scenario["verdict"])


if __name__ == "__main__":
    unittest.main()
