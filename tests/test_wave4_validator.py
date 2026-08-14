from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import validate_wave4_research as validator  # noqa: E402
from bithumb_coin_trader.wave4 import (  # noqa: E402
    wave4_candidate_manifest,
    wave4_candidate_manifest_hash,
)


def _manifest() -> dict[str, object]:
    value: dict[str, object] = wave4_candidate_manifest()
    value["candidate_count"] = 3
    value["sha256"] = wave4_candidate_manifest_hash()
    return value


def _metric(total_return: float = 0.08) -> dict[str, object]:
    initial = 20_000.0
    factor = (1.0 + total_return) ** (1.0 / (validator.OUTER_TEST * validator.OUTER_FOLDS))
    curve = [initial * factor**index for index in range(validator.EXPECTED_CURVE_LENGTH)]
    folds = []
    for index, (train, test) in enumerate(validator.EXPECTED_BOUNDARIES):
        start = index * validator.OUTER_TEST
        end = (index + 1) * validator.OUTER_TEST
        folds.append(
            {
                "fold": index + 1,
                "train": list(train),
                "test": list(test),
                "initial_equity_krw": curve[start],
                "final_equity_krw": curve[end],
                "total_return": curve[end] / curve[start] - 1.0,
                "maximum_drawdown": 0.0,
                "trade_count": 2,
            }
        )
    maximum_drawdown = validator._max_drawdown(curve)
    profitable_folds = sum(fold["total_return"] > 0 for fold in folds)
    return {
        "fold_count": 8,
        "compounded_return": curve[-1] / curve[0] - 1.0,
        "maximum_drawdown": maximum_drawdown,
        "trade_count": 16,
        "trade_evidence": {
            "non_final_trade_count": 16,
            "final_liquidation_count": 0,
            "max_positive_trade_contribution": 0.20,
        },
        "profitable_folds": profitable_folds,
        "folds": folds,
        "oos_equity_curve_krw": curve,
    }


def valid_report() -> dict[str, object]:
    base = _metric(0.08)
    stress = _metric(0.04)
    previous = _metric(0.01)
    manifest = _manifest()
    decisions = [
        {
            "fold": index + 1,
            "train": list(train),
            "test": list(test),
            "selected_candidate": "daily_tsmom_84",
        }
        for index, (train, test) in enumerate(validator.EXPECTED_BOUNDARIES)
    ]
    return {
        "generated_at": "2026-08-14T12:00:00+00:00",
        "dataset": {
            "market": "KRW-BTC",
            "candle_count": validator.EXPECTED_DATASET_COUNT,
            "end_at": validator.EXPECTED_DATASET_END,
            "sha256": validator.EXPECTED_DATASET_SHA256,
            "wave4_forward_sample_count": 0,
            "wave4_forward_sample_status": "none",
        },
        "historical_prefix": {
            "candle_count": 40_000,
            "sha256": validator.EXPECTED_HISTORY_SHA256,
            "historical_data_reused": True,
        },
        "candidate_manifest": manifest,
        "validation": {
            "outer": {
                "initial_train_candles_30m": 19_200,
                "test_candles_30m": 2_400,
                "fold_count": 8,
                "expanding": True,
            },
            "inner": {
                "initial_train_candles_30m": 12_000,
                "test_candles_30m": 1_200,
                "fold_count": 6,
                "expanding": True,
            },
            "allow_short": False,
            "signal_fill_contract": "completed close signal, next 30m open fill",
        },
        "costs": {
            "base_fee_rate_per_fill": 0.0025,
            "base_slippage_bps_per_fill": 5.0,
            "stress_fee_rate_per_fill": 0.005,
            "stress_slippage_bps_per_fill": 10.0,
        },
        "candidates": [
            {
                "name": name,
                "walk_forward": copy.deepcopy(base),
                "double_cost_stress": copy.deepcopy(stress),
            }
            for name in validator.EXPECTED_CANDIDATES
        ],
        "controls": {
            "previous_best": {
                "walk_forward": previous,
                "double_cost_stress": _metric(-0.01),
            }
        },
        "nested_selection": {
            "decisions": decisions,
            "walk_forward": base,
            "double_cost_stress": stress,
            "stress_quarter_returns": [0.01, 0.01, 0.01, 0.01],
        },
        "bootstrap": {
            "method": "kst_daily_moving_block",
            "block_days": 7,
            "iterations": 5_000,
            "seed": 20_260_814,
            "lower_95": 0.001,
            "median": 0.02,
            "upper_95": 0.04,
        },
        "gate_evaluation": {
            "checks": {
                "base_exceeds_previous_best_1_019286pct": {
                    "passed": True,
                    "actual": 0.08,
                    "requirement": "> 0.01019286",
                },
                "double_cost_return_positive": {
                    "passed": True,
                    "actual": 0.04,
                    "requirement": "> 0",
                },
                "maximum_drawdown_at_most_10pct": {
                    "passed": True,
                    "actual": 0.0,
                    "requirement": "<= 0.10",
                },
                "at_least_five_of_eight_positive_outer_folds": {
                    "passed": True,
                    "actual": 8,
                    "requirement": ">= 5",
                },
                "at_least_three_of_four_positive_stress_quarters": {
                    "passed": True,
                    "actual": 4,
                    "requirement": ">= 3",
                },
                "bootstrap_excess_lower_95_positive": {
                    "passed": True,
                    "actual": 0.001,
                    "requirement": "> 0",
                },
                "at_least_twelve_non_final_closed_trades": {
                    "passed": True,
                    "actual": 16,
                    "requirement": ">= 12",
                },
                "single_positive_trade_contribution_at_most_50pct": {
                    "passed": True,
                    "actual": 0.20,
                    "requirement": "<= 0.50",
                },
            },
            "overall_pass": True,
            "family_independence_count": 2,
        },
        "wave4_forward_sample": {
            "freeze_at": "2026-08-14T11:18:00+00:00",
            "observed_at": "2026-08-14T11:00:00+00:00",
            "candle_count_30m": 0,
            "prospective": True,
            "sample_sufficient": False,
        },
        "adaptive_tail": {
            "candle_count_30m": validator.EXPECTED_ADAPTIVE_TAIL_COUNT,
            "sha256": validator.EXPECTED_ADAPTIVE_TAIL_SHA256,
            "prospective_for_wave4": False,
        },
        "prior_wave3_prospective_update": {
            "period": validator.EXPECTED_WAVE3_FORWARD_PERIOD,
            "candle_count_30m": validator.EXPECTED_WAVE3_FORWARD_COUNT,
            "sha256": validator.EXPECTED_WAVE3_FORWARD_SHA256,
            "frozen_policy_action": "cash",
            "policy_return": 0.0,
            "sample_sufficient": False,
        },
        "selection": {
            "status": "RESEARCH_ONLY",
            "selected_candidate": None,
            "paper_or_live_strategy_changed": False,
            "can_promote": False,
        },
        "limitations": [
            "The 40000-candle historical prefix was reused and is adaptive evidence only."
        ],
    }


class Wave4ValidatorTests(unittest.TestCase):
    def test_valid_report_passes(self) -> None:
        self.assertEqual(validator.validate_report(valid_report()), [])

    def test_rejects_manifest_mutation(self) -> None:
        payload = valid_report()
        payload["candidate_manifest"]["candidates"][0]["parameters"]["lookback_kst_days"] = 85
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_cost_mutation(self) -> None:
        payload = valid_report()
        payload["costs"]["stress_fee_rate_per_fill"] = 0.004
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_curve_endpoint_mutation(self) -> None:
        payload = valid_report()
        payload["nested_selection"]["walk_forward"]["compounded_return"] = 0.9
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_drawdown_mutation(self) -> None:
        payload = valid_report()
        payload["nested_selection"]["walk_forward"]["maximum_drawdown"] = 0.01
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_fold_boundary_mutation(self) -> None:
        payload = valid_report()
        payload["nested_selection"]["walk_forward"]["folds"][0]["test"][0] += 1
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_false_gate_claim(self) -> None:
        payload = valid_report()
        payload["gate_evaluation"]["checks"]["double_cost_return_positive"][
            "passed"
        ] = False
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_gate_actual_mutation(self) -> None:
        payload = valid_report()
        payload["gate_evaluation"]["checks"][
            "maximum_drawdown_at_most_10pct"
        ]["actual"] = 0.99
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_gate_requirement_mutation(self) -> None:
        payload = valid_report()
        payload["gate_evaluation"]["checks"][
            "at_least_twelve_non_final_closed_trades"
        ]["requirement"] = ">= 1"
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_final_liquidation_substituted_for_sample(self) -> None:
        payload = valid_report()
        payload["nested_selection"]["walk_forward"]["trade_evidence"][
            "non_final_trade_count"
        ] = 11
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_single_trade_concentration(self) -> None:
        payload = valid_report()
        payload["nested_selection"]["walk_forward"]["trade_evidence"][
            "max_positive_trade_contribution"
        ] = 0.8
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_promotion_even_if_all_gates_pass(self) -> None:
        payload = valid_report()
        payload["selection"]["selected_candidate"] = "daily_tsmom_84"
        payload["selection"]["can_promote"] = True
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_forward_sample_claim(self) -> None:
        payload = valid_report()
        payload["wave4_forward_sample"]["candle_count_30m"] = 48
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_tail_count_mutation(self) -> None:
        payload = valid_report()
        payload["adaptive_tail"]["candle_count_30m"] = 999
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_forward_time_after_freeze(self) -> None:
        payload = valid_report()
        payload["wave4_forward_sample"]["observed_at"] = (
            "2026-08-14T12:00:00+00:00"
        )
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_dataset_hash_mutation(self) -> None:
        payload = valid_report()
        payload["dataset"]["sha256"] = "f" * 64
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_untouched_holdout_claim(self) -> None:
        payload = valid_report()
        payload["limitations"].append("This is an untouched holdout.")
        self.assertTrue(validator.validate_report(payload))


if __name__ == "__main__":
    unittest.main()
