#!/usr/bin/env python3
"""Fail-closed validator for the Wave 4 KRW-BTC research artifact.

Wave 4 is an adaptive historical experiment.  Even a report which passes all
statistical gates remains research-only and cannot select or promote a trading
strategy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


EXPECTED_HISTORY_COUNT = 40_000
EXPECTED_HISTORY_SHA256 = (
    "dc3537c862bc54efebfd215807e2ab57da66396ebfbfcf3d5a243327b9817248"
)
EXPECTED_DATASET_COUNT = 40_095
EXPECTED_DATASET_SHA256 = (
    "4a40c01ffd7974ad3893bdd34fcbe7b48a894ef21503bfd4ed901b689909f1b2"
)
EXPECTED_DATASET_END = "2026-08-14T10:30:00+00:00"
EXPECTED_ADAPTIVE_TAIL_COUNT = 95
EXPECTED_ADAPTIVE_TAIL_SHA256 = (
    "189e66c72f749f92e8d98bb635e9ebd76dfa45ffeb88184af78256737659e9fc"
)
EXPECTED_WAVE3_FORWARD_COUNT = 47
EXPECTED_WAVE3_FORWARD_SHA256 = (
    "86b17381c442db303bf40ca71d3f302873e7f56d5936df39168932d1984c865e"
)
EXPECTED_WAVE3_FORWARD_PERIOD = [
    "2026-08-13T11:30:00+00:00",
    "2026-08-14T10:30:00+00:00",
]
EXPECTED_CANDIDATES = (
    "daily_tsmom_84",
    "daily_tsmom_84_rv20_median_gate",
    "intraday_volume_clock_first_last_momentum",
)
EXPECTED_GATE_KEYS = {
    "base_exceeds_previous_best_1_019286pct",
    "double_cost_return_positive",
    "maximum_drawdown_at_most_10pct",
    "at_least_five_of_eight_positive_outer_folds",
    "at_least_three_of_four_positive_stress_quarters",
    "bootstrap_excess_lower_95_positive",
    "at_least_twelve_non_final_closed_trades",
    "single_positive_trade_contribution_at_most_50pct",
}
OUTER_TRAIN = 19_200
OUTER_TEST = 2_400
OUTER_FOLDS = 8
INNER_TRAIN = 12_000
INNER_TEST = 1_200
INNER_FOLDS = 6
EXPECTED_CURVE_LENGTH = OUTER_TEST * OUTER_FOLDS + 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _canonical_hash(value: Mapping[str, Any]) -> str:
    canonical = dict(value)
    canonical.pop("sha256", None)
    canonical.pop("candidate_count", None)
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _contains_untouched_claim(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            ("untouched" in str(key).lower() and "holdout" in str(key).lower())
            or _contains_untouched_claim(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_untouched_claim(child) for child in value)
    if isinstance(value, str):
        lowered = value.lower()
        return "untouched" in lowered and "holdout" in lowered
    return False


def _expected_boundaries() -> tuple[tuple[tuple[int, int], tuple[int, int]], ...]:
    return tuple(
        (
            (0, OUTER_TRAIN + fold * OUTER_TEST),
            (
                OUTER_TRAIN + fold * OUTER_TEST,
                OUTER_TRAIN + (fold + 1) * OUTER_TEST,
            ),
        )
        for fold in range(OUTER_FOLDS)
    )


EXPECTED_BOUNDARIES = _expected_boundaries()


def _max_drawdown(curve: Sequence[float]) -> float:
    peak = curve[0]
    result = 0.0
    for value in curve:
        peak = max(peak, value)
        result = max(result, (peak - value) / peak)
    return result


def _metric(
    value: object,
    *,
    label: str,
    issues: list[str],
) -> Mapping[str, Any] | None:
    report = _mapping(value)
    if report is None:
        issues.append(f"{label} metric is missing")
        return None
    folds = report.get("folds")
    curve = report.get("oos_equity_curve_krw")
    if not isinstance(folds, list) or len(folds) != OUTER_FOLDS:
        issues.append(f"{label} must have exactly eight folds")
        return report
    if report.get("fold_count") != OUTER_FOLDS:
        issues.append(f"{label} fold_count must equal eight")
    if not (
        isinstance(curve, list)
        and len(curve) == EXPECTED_CURVE_LENGTH
        and all(_number(item) and item > 0 for item in curve)
    ):
        issues.append(f"{label} must have a positive 19201-point OOS curve")
        return report

    boundaries: list[tuple[tuple[int, int], tuple[int, int]]] = []
    profitable = 0
    trade_count = 0
    prior_final: float | None = None
    for index, fold_value in enumerate(folds):
        fold = _mapping(fold_value)
        if fold is None:
            issues.append(f"{label} fold {index + 1} is invalid")
            continue
        train, test = fold.get("train"), fold.get("test")
        if not (
            isinstance(train, list)
            and isinstance(test, list)
            and len(train) == len(test) == 2
            and all(_nonnegative_int(item) for item in (*train, *test))
        ):
            issues.append(f"{label} fold {index + 1} boundary is invalid")
            continue
        boundary = ((train[0], train[1]), (test[0], test[1]))
        boundaries.append(boundary)
        if boundary != EXPECTED_BOUNDARIES[index]:
            issues.append(f"{label} fold {index + 1} boundary is not frozen")
        initial = fold.get("initial_equity_krw")
        final = fold.get("final_equity_krw")
        total_return = fold.get("total_return")
        drawdown = fold.get("maximum_drawdown", fold.get("max_drawdown"))
        trades = fold.get("trade_count")
        if not (
            _number(initial)
            and initial > 0
            and _number(final)
            and final > 0
            and _number(total_return)
            and math.isclose(final / initial - 1.0, total_return, abs_tol=1e-7)
        ):
            issues.append(f"{label} fold {index + 1} equity/return is inconsistent")
        else:
            profitable += total_return > 0
            if prior_final is not None and not math.isclose(
                initial, prior_final, abs_tol=0.011
            ):
                issues.append(f"{label} fold {index + 1} equity is discontinuous")
            prior_final = float(final)
        if not _number(drawdown) or not 0 <= drawdown <= 1:
            issues.append(f"{label} fold {index + 1} drawdown is invalid")
        if not _nonnegative_int(trades):
            issues.append(f"{label} fold {index + 1} trade count is invalid")
        else:
            trade_count += trades

    total_return = report.get("compounded_return")
    drawdown = report.get("maximum_drawdown")
    if not _number(total_return) or not math.isclose(
        curve[-1] / curve[0] - 1.0, total_return, abs_tol=1e-7
    ):
        issues.append(f"{label} compounded return does not match the curve")
    derived_drawdown = _max_drawdown([float(item) for item in curve])
    if not _number(drawdown) or not math.isclose(
        drawdown, derived_drawdown, abs_tol=1e-7
    ):
        issues.append(f"{label} drawdown does not match the curve")
    if report.get("trade_count") != trade_count:
        issues.append(f"{label} trade count does not match folds")
    if report.get("profitable_folds") != profitable:
        issues.append(f"{label} profitable fold count is inconsistent")

    evidence = _mapping(report.get("trade_evidence"))
    non_final = evidence.get("non_final_trade_count") if evidence else None
    concentration = (
        evidence.get("max_positive_trade_contribution") if evidence else None
    )
    if not _nonnegative_int(non_final) or (
        _nonnegative_int(report.get("trade_count"))
        and non_final > report["trade_count"]
    ):
        issues.append(f"{label} non-final trade evidence is invalid")
    if not _number(concentration) or not 0 <= concentration <= 1:
        issues.append(f"{label} positive-PnL concentration is invalid")
    return report


def _base_stress(container: Mapping[str, Any]) -> tuple[object, object]:
    return (
        container.get("walk_forward", container.get("base")),
        container.get("double_cost_stress", container.get("stress")),
    )


def _validate_manifest(payload: Mapping[str, Any], issues: list[str]) -> None:
    manifest = _mapping(payload.get("candidate_manifest"))
    if manifest is None:
        issues.append("candidate manifest is missing")
        return
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list):
        issues.append("candidate manifest candidates are missing")
        return
    names = [item.get("name") for item in candidates if isinstance(item, Mapping)]
    if (
        names != list(EXPECTED_CANDIDATES)
        or manifest.get("candidate_count", len(names)) != 3
        or len(names) != len(set(names))
    ):
        issues.append("candidate manifest differs from the exact Wave 4 set")
    declared_hash = manifest.get("sha256")
    if not isinstance(declared_hash, str) or declared_hash != _canonical_hash(manifest):
        issues.append("candidate manifest SHA-256 does not match canonical content")
    try:
        from bithumb_coin_trader.wave4 import (
            wave4_candidate_manifest,
            wave4_candidate_manifest_hash,
        )

        expected_manifest = wave4_candidate_manifest()
        canonical = dict(manifest)
        canonical.pop("sha256", None)
        canonical.pop("candidate_count", None)
        if canonical != expected_manifest:
            issues.append("candidate manifest differs from the Wave 4 runtime spec")
        if declared_hash != wave4_candidate_manifest_hash(expected_manifest):
            issues.append("candidate manifest hash differs from the Wave 4 runtime hash")
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        issues.append(f"could not verify Wave 4 runtime manifest: {exc}")
    execution = _mapping(manifest.get("execution"))
    if execution is None or execution.get("allow_short") is not False:
        issues.append("manifest must freeze spot LONG/FLAT execution")


def _validate_geometry_costs(payload: Mapping[str, Any], issues: list[str]) -> None:
    validation = _mapping(payload.get("validation"))
    outer = _mapping(validation.get("outer")) if validation else None
    inner = _mapping(validation.get("inner")) if validation else None
    if not outer or not (
        outer.get("initial_train_candles_30m") == OUTER_TRAIN
        and outer.get("test_candles_30m") == OUTER_TEST
        and outer.get("fold_count") == OUTER_FOLDS
        and outer.get("expanding") is True
    ):
        issues.append("outer validation geometry is not frozen")
    if not inner or not (
        inner.get("initial_train_candles_30m") == INNER_TRAIN
        and inner.get("test_candles_30m") == INNER_TEST
        and inner.get("fold_count") == INNER_FOLDS
        and inner.get("expanding") is True
    ):
        issues.append("inner validation geometry is not frozen")
    if not validation or validation.get("allow_short") is not False:
        issues.append("validation must remain spot LONG/FLAT")
    if not validation or validation.get("signal_fill_contract") != (
        "completed close signal, next 30m open fill"
    ):
        issues.append("next-open execution contract is missing")
    costs = _mapping(payload.get("costs"))
    expected = {
        "base_fee_rate_per_fill": 0.0025,
        "base_slippage_bps_per_fill": 5.0,
        "stress_fee_rate_per_fill": 0.005,
        "stress_slippage_bps_per_fill": 10.0,
    }
    if not costs or any(costs.get(key) != value for key, value in expected.items()):
        issues.append("base/stress costs differ from the frozen contract")


def _validate_dataset(payload: Mapping[str, Any], issues: list[str]) -> None:
    dataset = _mapping(payload.get("dataset"))
    history = _mapping(payload.get("historical_prefix"))
    if not dataset or not (
        dataset.get("market") == "KRW-BTC"
        and dataset.get("candle_count") == EXPECTED_DATASET_COUNT
        and dataset.get("sha256") == EXPECTED_DATASET_SHA256
        and dataset.get("end_at") == EXPECTED_DATASET_END
        and dataset.get("wave4_forward_sample_count") == 0
        and dataset.get("wave4_forward_sample_status") == "none"
    ):
        issues.append("source dataset differs from the frozen 40095-candle artifact")
    if not history or not (
        history.get("candle_count") == EXPECTED_HISTORY_COUNT
        and history.get("sha256") == EXPECTED_HISTORY_SHA256
        and history.get("historical_data_reused") is True
    ):
        issues.append("historical prefix must be the exact reused 40000-candle sample")
    forward = _mapping(payload.get("wave4_forward_sample"))
    if not forward or not (
        forward.get("candle_count_30m") == 0
        and forward.get("sample_sufficient") is False
        and forward.get("prospective") is True
    ):
        issues.append("Wave 4 must disclose that no sufficient forward sample exists")
    adaptive_tail = _mapping(payload.get("adaptive_tail"))
    prior_wave3 = _mapping(payload.get("prior_wave3_prospective_update"))
    if not adaptive_tail or not (
        adaptive_tail.get("candle_count_30m") == EXPECTED_ADAPTIVE_TAIL_COUNT
        and adaptive_tail.get("sha256") == EXPECTED_ADAPTIVE_TAIL_SHA256
        and adaptive_tail.get("prospective_for_wave4") is False
    ):
        issues.append("post-history source bars must be classified as adaptive for Wave 4")
    if not prior_wave3 or not (
        prior_wave3.get("period") == EXPECTED_WAVE3_FORWARD_PERIOD
        and prior_wave3.get("candle_count_30m") == EXPECTED_WAVE3_FORWARD_COUNT
        and prior_wave3.get("sha256") == EXPECTED_WAVE3_FORWARD_SHA256
        and prior_wave3.get("frozen_policy_action") == "cash"
        and prior_wave3.get("policy_return") == 0.0
        and prior_wave3.get("sample_sufficient") is False
    ):
        issues.append("post-history bars may update only the frozen Wave 3 cash policy")
    if dataset and adaptive_tail and (
        dataset.get("candle_count")
        != EXPECTED_HISTORY_COUNT + adaptive_tail.get("candle_count_30m", -1)
    ):
        issues.append("dataset count does not equal history plus adaptive tail")
    if forward and dataset:
        try:
            dataset_end = datetime.fromisoformat(
                str(dataset["end_at"]).replace("Z", "+00:00")
            )
            observed_at = datetime.fromisoformat(
                str(forward["observed_at"]).replace("Z", "+00:00")
            )
            freeze_at = datetime.fromisoformat(
                str(forward["freeze_at"]).replace("Z", "+00:00")
            )
            if not (
                dataset_end < observed_at < freeze_at
                and (observed_at - dataset_end).total_seconds() == 30 * 60
            ):
                issues.append("dataset cutoff, observation, and freeze times are inconsistent")
        except (KeyError, TypeError, ValueError):
            issues.append("dataset cutoff, observation, and freeze times are invalid")


def _validate_results(payload: Mapping[str, Any], issues: list[str]) -> None:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        issues.append("candidate results are missing")
    else:
        names: list[str] = []
        for row_value in candidates:
            row = _mapping(row_value)
            if row is None or not isinstance(row.get("name"), str):
                issues.append("candidate result row is invalid")
                continue
            names.append(row["name"])
            base, stress = _base_stress(row)
            _metric(base, label=f"candidate {row['name']} base", issues=issues)
            _metric(stress, label=f"candidate {row['name']} stress", issues=issues)
        if set(names) != set(EXPECTED_CANDIDATES) or len(names) != len(
            EXPECTED_CANDIDATES
        ):
            issues.append("candidate result order/set differs from manifest")

    controls = _mapping(payload.get("controls"))
    previous = _mapping(controls.get("previous_best")) if controls else None
    if previous is None:
        issues.append("previous-best control is missing")
    else:
        base, stress = _base_stress(previous)
        _metric(base, label="previous-best base", issues=issues)
        _metric(stress, label="previous-best stress", issues=issues)

    nested = _mapping(payload.get("nested_selection"))
    if nested is None:
        issues.append("nested selection is missing")
        return
    base_value, stress_value = _base_stress(nested)
    _metric(base_value, label="nested base", issues=issues)
    _metric(stress_value, label="nested stress", issues=issues)
    decisions = nested.get("decisions")
    if not isinstance(decisions, list) or len(decisions) != OUTER_FOLDS:
        issues.append("nested selection must contain eight decisions")
    else:
        for index, decision_value in enumerate(decisions):
            decision = _mapping(decision_value)
            if not decision or (
                decision.get("train") != list(EXPECTED_BOUNDARIES[index][0])
                or decision.get("test") != list(EXPECTED_BOUNDARIES[index][1])
                or decision.get("selected_candidate")
                not in {*EXPECTED_CANDIDATES, "cash", None}
            ):
                issues.append(f"nested decision {index + 1} is invalid")


def _validate_gates(payload: Mapping[str, Any], issues: list[str]) -> None:
    gate = _mapping(payload.get("gate_evaluation"))
    nested = _mapping(payload.get("nested_selection"))
    controls = _mapping(payload.get("controls"))
    bootstrap = _mapping(payload.get("bootstrap"))
    if not gate or not nested or not controls or not bootstrap:
        issues.append("gate evidence is incomplete")
        return
    checks = _mapping(gate.get("checks"))
    if checks is None or set(checks) != EXPECTED_GATE_KEYS or not all(
        isinstance(value, Mapping)
        and isinstance(value.get("passed"), bool)
        and "actual" in value
        and isinstance(value.get("requirement"), str)
        for value in checks.values()
    ):
        issues.append("gate checks differ from the exact eight-gate contract")
        return
    nested_base, nested_stress = _base_stress(nested)
    base = _mapping(nested_base) or {}
    stress = _mapping(nested_stress) or {}
    previous = _mapping(controls.get("previous_best")) or {}
    previous_base, _ = _base_stress(previous)
    previous_metric = _mapping(previous_base) or {}
    quarters = nested.get("stress_quarter_returns")
    lower = bootstrap.get("lower_95")
    if not (
        isinstance(quarters, list)
        and len(quarters) == 4
        and all(_number(value) for value in quarters)
        and _number(lower)
        and all(
            _number(value)
            for value in (
                base.get("compounded_return"),
                base.get("maximum_drawdown"),
                stress.get("compounded_return"),
                previous_metric.get("compounded_return"),
                (_mapping(base.get("trade_evidence")) or {}).get(
                    "max_positive_trade_contribution"
                ),
            )
        )
        and _nonnegative_int(base.get("profitable_folds"))
        and _nonnegative_int(
            (_mapping(base.get("trade_evidence")) or {}).get(
                "non_final_trade_count"
            )
        )
    ):
        issues.append("gate metrics cannot be recomputed")
        return
    base_evidence = _mapping(base.get("trade_evidence")) or {}
    expected = {
        "base_exceeds_previous_best_1_019286pct": base["compounded_return"]
        > previous_metric["compounded_return"],
        "double_cost_return_positive": stress["compounded_return"] > 0,
        "maximum_drawdown_at_most_10pct": base["maximum_drawdown"] <= 0.10,
        "at_least_five_of_eight_positive_outer_folds": base["profitable_folds"]
        >= 5,
        "at_least_three_of_four_positive_stress_quarters": sum(
            value > 0 for value in quarters
        )
        >= 3,
        "bootstrap_excess_lower_95_positive": lower > 0,
        "at_least_twelve_non_final_closed_trades": base_evidence[
            "non_final_trade_count"
        ]
        >= 12,
        "single_positive_trade_contribution_at_most_50pct": base_evidence[
            "max_positive_trade_contribution"
        ]
        <= 0.50,
    }
    if {key: value["passed"] for key, value in checks.items()} != expected:
        issues.append("gate pass flags contradict report metrics")
    expected_actuals = {
        "base_exceeds_previous_best_1_019286pct": base["compounded_return"],
        "double_cost_return_positive": stress["compounded_return"],
        "maximum_drawdown_at_most_10pct": base["maximum_drawdown"],
        "at_least_five_of_eight_positive_outer_folds": base["profitable_folds"],
        "at_least_three_of_four_positive_stress_quarters": sum(
            value > 0 for value in quarters
        ),
        "bootstrap_excess_lower_95_positive": lower,
        "at_least_twelve_non_final_closed_trades": base_evidence[
            "non_final_trade_count"
        ],
        "single_positive_trade_contribution_at_most_50pct": base_evidence[
            "max_positive_trade_contribution"
        ],
    }
    expected_requirements = {
        "base_exceeds_previous_best_1_019286pct": "> 0.01019286",
        "double_cost_return_positive": "> 0",
        "maximum_drawdown_at_most_10pct": "<= 0.10",
        "at_least_five_of_eight_positive_outer_folds": ">= 5",
        "at_least_three_of_four_positive_stress_quarters": ">= 3",
        "bootstrap_excess_lower_95_positive": "> 0",
        "at_least_twelve_non_final_closed_trades": ">= 12",
        "single_positive_trade_contribution_at_most_50pct": "<= 0.50",
    }
    for key, expected_actual in expected_actuals.items():
        actual = checks[key]["actual"]
        if isinstance(expected_actual, int) and not isinstance(expected_actual, bool):
            matches = (
                isinstance(actual, int)
                and not isinstance(actual, bool)
                and actual == expected_actual
            )
        else:
            matches = _number(actual) and math.isclose(
                float(actual), float(expected_actual), rel_tol=1e-12, abs_tol=1e-12
            )
        if not matches:
            issues.append(f"gate {key} actual value contradicts report metrics")
        if checks[key]["requirement"] != expected_requirements[key]:
            issues.append(f"gate {key} requirement differs from the frozen contract")
    if gate.get("overall_pass") is not all(expected.values()):
        issues.append("overall_pass must equal all eight recomputed gates")
    if gate.get("family_independence_count") != 2:
        issues.append("Wave 4 has exactly two independent strategy families")
    if not (
        bootstrap.get("method") == "kst_daily_moving_block"
        and bootstrap.get("block_days") == 7
        and bootstrap.get("iterations") == 5_000
        and isinstance(bootstrap.get("seed"), int)
    ):
        issues.append("bootstrap contract is not frozen")


def _validate_nonpromotion(payload: Mapping[str, Any], issues: list[str]) -> None:
    selection = _mapping(payload.get("selection"))
    if not selection or not (
        selection.get("status") == "RESEARCH_ONLY"
        and selection.get("selected_candidate") is None
        and selection.get("paper_or_live_strategy_changed") is False
        and selection.get("can_promote") is False
    ):
        issues.append("Wave 4 must remain research-only with no promotion")
    limitations = payload.get("limitations")
    if not isinstance(limitations, list) or not limitations or not all(
        isinstance(item, str) and item.strip() for item in limitations
    ):
        issues.append("research limitations must be explicit")


def validate_report(payload: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    if _contains_untouched_claim(payload):
        issues.append("adaptive Wave 4 research cannot claim an untouched holdout")
    generated_at = payload.get("generated_at")
    if not isinstance(generated_at, str):
        issues.append("generated_at is missing")
    else:
        try:
            datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            issues.append("generated_at is not an ISO-8601 timestamp")
    _validate_dataset(payload, issues)
    _validate_manifest(payload, issues)
    _validate_geometry_costs(payload, issues)
    _validate_results(payload, issues)
    _validate_gates(payload, issues)
    _validate_nonpromotion(payload, issues)
    return issues


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--input", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.report.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("report root must be an object")
        issues = validate_report(payload)
        replay_performed = False
        if args.input is not None:
            root = Path(__file__).resolve().parents[1]
            sys.path.insert(0, str(root / "src"))
            sys.path.insert(0, str(root / "scripts"))
            from bithumb_coin_trader.data import load_candles_csv
            from run_wave4_research import build_report

            replay = build_report(
                load_candles_csv(args.input),
                generated_at=datetime.fromisoformat(
                    str(payload["generated_at"]).replace("Z", "+00:00")
                ),
            )
            replay_performed = True
            if json.dumps(replay, sort_keys=True, separators=(",", ":")) != json.dumps(
                payload, sort_keys=True, separators=(",", ":")
            ):
                issues.append("report differs from deterministic raw-data replay")
    except (OSError, ValueError, TypeError, json.JSONDecodeError, ImportError) as exc:
        issues = [str(exc)]
        replay_performed = False
    result = {
        "status": "passed" if not issues else "failed",
        "passed": not issues,
        "summary": "Wave 4 artifact satisfies the frozen research contract"
        if not issues
        else "; ".join(issues),
        "issues": issues,
        "output_artifact_path": str(args.report),
        "replay_performed": replay_performed,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.result is not None:
        args.result.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
