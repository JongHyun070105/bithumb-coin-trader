from __future__ import annotations

import copy
import importlib.util
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from bithumb_coin_trader.wave3 import wave3_candidate_manifest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_wave3_research.py"
SPEC = importlib.util.spec_from_file_location("wave3_validator", VALIDATOR)
assert SPEC and SPEC.loader
wave3_validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wave3_validator)


CANDIDATES = sorted(wave3_validator.EXPECTED_CANDIDATES)


def _metric(*, fold_return: float = 0.01) -> dict[str, Any]:
    folds: list[dict[str, Any]] = []
    equity = 20_000.0
    curve = [equity]
    for index, (train, test) in enumerate(wave3_validator.EXPECTED_OUTER_BOUNDARIES):
        initial = equity
        equity = initial * (1.0 + fold_return)
        folds.append(
            {
                "fold": index + 1,
                "train": list(train),
                "test": list(test),
                "initial_equity_krw": initial,
                "final_equity_krw": equity,
                "total_return": fold_return,
                "max_drawdown": 0.0,
                "sharpe": 0.5,
                "trade_count": 1,
                "win_rate": 1.0 if fold_return > 0 else 0.0,
                "exposure": 0.4,
            }
        )
        step_return = (equity / initial) ** (1.0 / wave3_validator.OUTER_TEST)
        curve.extend(initial * step_return**step for step in range(1, wave3_validator.OUTER_TEST + 1))
    return {
        "fold_count": 8,
        "compounded_return": math.prod(1.0 + fold_return for _ in folds) - 1.0,
        "maximum_drawdown": 0.0,
        "mean_sharpe": 0.5,
        "trade_count": 8,
        "weighted_win_rate": 1.0 if fold_return > 0 else 0.0,
        "profitable_folds": 8 if fold_return > 0 else 0,
        "folds": folds,
        "oos_equity_curve_krw": curve,
    }


def _candidate_result(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "walk_forward": _metric(fold_return=0.01),
        "double_cost_stress": _metric(fold_return=0.005),
    }


def _zero_forward_metric() -> dict[str, Any]:
    return {
        "initial_equity_krw": 20_000.0,
        "final_equity_krw": 20_000.0,
        "total_return": 0.0,
        "max_drawdown": 0.0,
        "sharpe": 0.0,
        "trade_count": 0,
        "win_rate": 0.0,
        "exposure": 0.0,
        "equity_curve_krw": [20_000.0] * 49,
        "position_curve": [0] * 49,
        "trade_net_pnl_krw": [],
    }


def _valid_fixture() -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    selected = CANDIDATES[-1]
    for index, (train, test) in enumerate(wave3_validator.EXPECTED_OUTER_BOUNDARIES):
        summaries: list[dict[str, Any]] = []
        for candidate_index, name in enumerate(CANDIDATES):
            stress_return = 0.01 + candidate_index / 100.0
            base_fold_return = 1.1 ** (1.0 / 6.0) - 1.0
            stress_fold_return = (1.0 + stress_return) ** (1.0 / 6.0) - 1.0
            summaries.append(
                {
                    "name": name,
                    "base": {
                        "fold_count": 6,
                        "compounded_return": 0.1,
                        "fold_returns": [base_fold_return] * 6,
                    },
                    "stress": {
                        "fold_count": 6,
                        "compounded_return": stress_return,
                        "maximum_drawdown": 0.1,
                        "profitable_folds": 6,
                        "fold_returns": [stress_fold_return] * 6,
                    },
                    "eligible": True,
                }
            )
        decisions.append(
            {
                "fold": index + 1,
                "train": list(train),
                "test": list(test),
                "inner_candidates": summaries,
                "eligible_candidates": CANDIDATES,
                "selected_candidate": selected,
            }
        )

    manifest = wave3_candidate_manifest()
    forward_candidates = copy.deepcopy(decisions[-1]["inner_candidates"])
    for candidate in forward_candidates:
        candidate["base"]["compounded_return"] = -0.01
        candidate["base"]["fold_returns"] = [-0.01, 0.0, 0.0, 0.0, 0.0, 0.0]
        candidate["stress"]["compounded_return"] = -0.01
        candidate["stress"]["fold_returns"] = [-0.01, 0.0, 0.0, 0.0, 0.0, 0.0]
        candidate["stress"]["profitable_folds"] = 0
        candidate["eligible"] = False
    return {
        "market": "KRW-BTC",
        "mode": "bithumb_spot_long_flat_research",
        "timeframe": "30m_execution_with_completed_higher_timeframe_signals",
        "historical_data_reused": True,
        "dataset": {
            "market": "KRW-BTC",
            "candle_count": 40_048,
            "sha256": wave3_validator.EXPECTED_DATASET_SHA256,
        },
        "historical_prefix": {
            "candle_count": 40_000,
            "sha256": wave3_validator.EXPECTED_HISTORY_SHA256,
        },
        "candidate_manifest": {
            **manifest,
            "candidate_count": 5,
            "sha256": wave3_validator.EXPECTED_CANDIDATE_MANIFEST_SHA256,
        },
        "validation": {
            "outer": {
                "initial_train_candles_30m": 19_200,
                "test_candles_30m": 2_400,
                "fold_count": 8,
                "expanding": True,
            },
            "inner": {
                "method": "expanding walk-forward",
                "train_candles_30m": 12_000,
                "test_candles_30m": 1_200,
                "fold_count": 6,
            },
            "signal_fill_contract": "completed close signal, next 30m open fill",
        },
        "costs": {
            "base_fee_rate_per_fill": 0.0025,
            "stress_fee_rate_per_fill": 0.005,
            "base_slippage_bps_per_fill": 5.0,
            "stress_slippage_bps_per_fill": 10.0,
        },
        "fixed_candidates": [_candidate_result(name) for name in CANDIDATES],
        "controls": {
            "previous_best": _candidate_result(wave3_validator.EXPECTED_PREVIOUS_BEST),
            "buy_hold": _candidate_result("buy_and_hold"),
        },
        "nested_selection": {
            "decisions": decisions,
            "walk_forward": _metric(fold_return=0.012),
            "double_cost_stress": _metric(fold_return=0.006),
            "stress_subperiod_returns": [0.01, 0.01, 0.01, 0.01],
        },
        "credibility": {
            "checks": {
                "nested_base_exceeds_previous_best_same_window": True,
                "nested_double_cost_stress_positive": True,
                "at_least_five_profitable_outer_folds": True,
                "maximum_drawdown_at_most_10pct": True,
                "sample_sufficient": False,
                "three_of_four_stress_subperiods_positive": True,
                "bootstrap_excess_lower_95_positive": False,
                "adjacent_trading_range_variants_stress_positive": True,
            },
            "credible_historical_improvement": False,
            "can_promote": False,
        },
        "bootstrap": {
            "iterations": 5_000,
            "seed": 20_260_813,
            "block_days": 7,
            "quantiles": {"p2_5": -0.01, "p50": 0.005, "p97_5": 0.02},
            "probability_excess_gt_zero": 0.6,
        },
        "posthoc_shadow": {
            "evidence_class": "posthoc_diagnostic",
            "prospective": False,
            "observed_before_manifest_freeze": True,
            "period": list(wave3_validator.EXPECTED_FORWARD_PERIOD),
            "candle_count_30m": 48,
            "sha256": wave3_validator.EXPECTED_FORWARD_SHA256,
            "historical_selection": {
                "fold": 9,
        "train": [0, 40_000],
                "test": [40_000, 40_048],
                "inner_candidates": forward_candidates,
                "eligible_candidates": [],
                "selected_candidate": "cash",
            },
            "frozen_policy": {
                "action": "cash",
                "base": _zero_forward_metric(),
                "double_cost_stress": _zero_forward_metric(),
            },
            "candidate_diagnostics": [
                {
                    "name": name,
                    "base": _zero_forward_metric(),
                    "double_cost_stress": _zero_forward_metric(),
                }
                for name in CANDIDATES
            ],
            "sample_sufficient_for_promotion": False,
            "status": "INSUFFICIENT_SAMPLE",
        },
        "selection": {
            "status": "RESEARCH_ONLY",
            "selected_candidate": None,
            "paper_or_live_strategy_changed": False,
        },
    }


class Wave3ValidatorTests(unittest.TestCase):
    def assert_invalid(self, payload: dict[str, Any], expected: str) -> None:
        issues = wave3_validator.validate_report(payload)
        self.assertTrue(
            any(expected in issue for issue in issues),
            f"expected {expected!r} in {issues!r}",
        )

    def test_minimal_valid_fixture_passes(self) -> None:
        self.assertEqual(wave3_validator.validate_report(_valid_fixture()), [])

    def test_cli_writes_validator_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            result_path = Path(directory) / "result.json"
            report.write_text(json.dumps(_valid_fixture()), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(VALIDATOR), str(report), "--result", str(result_path)],
                check=False,
                capture_output=True,
                text=True,
            )
            written = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertTrue(written["passed"])
        self.assertTrue(json.loads(completed.stdout)["passed"])

    def test_manifest_mutations_fail_closed(self) -> None:
        payload = _valid_fixture()
        payload["dataset"]["sha256"] = "0" * 64
        payload["candidate_manifest"]["sha256"] = "1" * 64
        self.assert_invalid(payload, "dataset manifest")
        self.assert_invalid(payload, "candidate manifest SHA-256")

        payload = _valid_fixture()
        payload["candidate_manifest"]["candidates"][0]["parameters"]["period"] = 51
        self.assert_invalid(payload, "candidate manifest SHA-256")

    def test_candidate_set_mutation_fails_closed(self) -> None:
        payload = _valid_fixture()
        payload["fixed_candidates"].pop()
        self.assert_invalid(payload, "fixed candidate results differ")

    def test_cost_contract_mutation_fails_closed(self) -> None:
        payload = _valid_fixture()
        payload["costs"]["base_fee_rate_per_fill"] = 0.001
        self.assert_invalid(payload, "costs must equal the frozen")

    def test_metric_accounting_mutation_fails_closed(self) -> None:
        payload = _valid_fixture()
        report = payload["fixed_candidates"][0]["walk_forward"]
        report["folds"][0]["final_equity_krw"] *= 2
        report["compounded_return"] = float("nan")
        self.assert_invalid(payload, "return does not match equity")
        self.assert_invalid(payload, "compounded return is invalid")

    def test_equity_discontinuity_and_curve_mutations_fail_closed(self) -> None:
        payload = _valid_fixture()
        report = payload["fixed_candidates"][0]["walk_forward"]
        report["folds"][1]["initial_equity_krw"] += 100.0
        self.assert_invalid(payload, "equity is discontinuous")

        payload = _valid_fixture()
        report = payload["fixed_candidates"][0]["walk_forward"]
        report["oos_equity_curve_krw"][100] *= 0.5
        self.assert_invalid(payload, "maximum drawdown does not match equity curve")

        payload = _valid_fixture()
        report = payload["fixed_candidates"][0]["walk_forward"]
        report["oos_equity_curve_krw"][-1] *= 1.1
        self.assert_invalid(payload, "compounded return does not match equity curve")

    def test_forward_manifest_policy_and_evidence_mutations_fail_closed(self) -> None:
        payload = _valid_fixture()
        payload["posthoc_shadow"]["period"][0] = "2026-08-12T12:00:00+00:00"
        payload["posthoc_shadow"]["sha256"] = "0" * 64
        self.assert_invalid(payload, "timestamps differ")
        self.assert_invalid(payload, "SHA-256 differs")

        payload = _valid_fixture()
        payload["posthoc_shadow"]["historical_selection"]["selected_candidate"] = CANDIDATES[0]
        payload["posthoc_shadow"]["frozen_policy"]["action"] = CANDIDATES[0]
        self.assert_invalid(payload, "historical selection contradicts")
        self.assert_invalid(payload, "policy action contradicts")

        payload = _valid_fixture()
        payload["posthoc_shadow"]["frozen_policy"]["base"]["equity_curve_krw"][-1] = 21_000.0
        self.assert_invalid(payload, "execution evidence contradicts")

        payload = _valid_fixture()
        diagnostic = payload["posthoc_shadow"]["candidate_diagnostics"][0]["base"]
        diagnostic["total_return"] = 0.25
        self.assert_invalid(payload, "must equal the frozen zero-trade cash result")

    def test_credibility_claim_is_recomputed(self) -> None:
        payload = _valid_fixture()
        payload["credibility"]["checks"]["bootstrap_excess_lower_95_positive"] = True
        payload["credibility"]["credible_historical_improvement"] = True
        self.assert_invalid(payload, "credibility checks contradict")

    def test_nested_selection_mutation_fails_closed(self) -> None:
        payload = _valid_fixture()
        payload["nested_selection"]["decisions"][0]["selected_candidate"] = CANDIDATES[0]
        self.assert_invalid(payload, "selection contradicts the frozen ranking rule")

        payload = _valid_fixture()
        payload["nested_selection"]["decisions"][0]["inner_candidates"][0][
            "base"
        ]["compounded_return"] = 0.5
        self.assert_invalid(payload, "inner summary does not match its folds")

    def test_untouched_wording_and_promotion_fail_closed(self) -> None:
        payload = _valid_fixture()
        payload["notes"] = "This is an untouched final holdout."
        payload["selection"]["status"] = "PAPER_CANDIDATE"
        payload["selection"]["selected_candidate"] = CANDIDATES[0]
        self.assert_invalid(payload, "must not claim an untouched holdout")
        self.assert_invalid(payload, "selection status must remain RESEARCH_ONLY")
        self.assert_invalid(payload, "cannot select a candidate")

    def test_cash_fallback_is_recomputed(self) -> None:
        payload = _valid_fixture()
        decision = payload["nested_selection"]["decisions"][0]
        for summary in decision["inner_candidates"]:
            summary["base"]["compounded_return"] = -0.01
            summary["base"]["fold_returns"] = [-0.01, 0.0, 0.0, 0.0, 0.0, 0.0]
            summary["eligible"] = False
        decision["eligible_candidates"] = []
        decision["selected_candidate"] = "cash"
        self.assertEqual(wave3_validator.validate_report(payload), [])
        decision["selected_candidate"] = CANDIDATES[0]
        self.assert_invalid(payload, "selection contradicts the frozen ranking rule")

    def test_raw_engine_nested_decision_aliases_pass(self) -> None:
        payload = _valid_fixture()
        decision = payload["nested_selection"]["decisions"][0]
        raw_scores = []
        for summary in decision.pop("inner_candidates"):
            raw_scores.append(
                {
                    "candidate_name": summary["name"],
                    "base_compounded_return": summary["base"]["compounded_return"],
                    "stress_compounded_return": summary["stress"]["compounded_return"],
                    "stress_maximum_drawdown": summary["stress"]["maximum_drawdown"],
                    "base_fold_returns": summary["base"]["fold_returns"],
                    "stress_fold_returns": summary["stress"]["fold_returns"],
                    "profitable_stress_fold_count": 6,
                    "qualifies": True,
                }
            )
        decision["candidate_scores"] = raw_scores
        train = decision.pop("train")
        test = decision.pop("test")
        decision["train_start"], decision["train_end"] = train
        decision["test_start"], decision["test_end"] = test
        self.assertEqual(wave3_validator.validate_report(payload), [])

    def test_raw_engine_none_denotes_cash(self) -> None:
        payload = _valid_fixture()
        decision = payload["nested_selection"]["decisions"][0]
        for summary in decision["inner_candidates"]:
            summary["base"]["compounded_return"] = -0.01
            summary["base"]["fold_returns"] = [-0.01, 0.0, 0.0, 0.0, 0.0, 0.0]
            summary["eligible"] = False
        decision["eligible_candidates"] = []
        decision["selected_candidate"] = None
        self.assertEqual(wave3_validator.validate_report(payload), [])


if __name__ == "__main__":
    unittest.main()
