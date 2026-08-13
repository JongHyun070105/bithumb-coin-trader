#!/usr/bin/env python3
"""Fail-closed validator for the frozen Wave 3 BTC research artifact.

The validator intentionally knows the experiment manifest.  A report is useful
as evidence only when its dataset, candidate set, fold geometry, nested
selection rule, accounting, and non-promotion status all remain frozen.
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


EXPECTED_DATASET_COUNT = 40_048
EXPECTED_DATASET_SHA256 = (
    "b8f7217eb30c9b2b55e5b0462e40d826c8c83a057e2e548fd928951156e03e07"
)
EXPECTED_HISTORY_COUNT = 40_000
EXPECTED_HISTORY_SHA256 = (
    "dc3537c862bc54efebfd215807e2ab57da66396ebfbfcf3d5a243327b9817248"
)
EXPECTED_FORWARD_SHA256 = (
    "1650b99b82302c2cb27480da29f7bc8cc1b5277e597de750c3bcf0ce9af5f9db"
)
EXPECTED_FORWARD_PERIOD = (
    "2026-08-12T11:30:00+00:00",
    "2026-08-13T11:00:00+00:00",
)
EXPECTED_CANDIDATE_MANIFEST_SHA256 = (
    "41afcddf791ced95f6e92751e45d8f71dacd94083d1ea5c516001407d179674a"
)
EXPECTED_CANDIDATES = {
    "trading_range_daily_50_band_1pct",
    "trading_range_daily_50_no_band",
    "trend_daily_sma50_200_adx14_25",
    "trend_daily_macd12_26_9_pvo12_26",
    "ensemble_daily_3_of_5",
}
EXPECTED_PREVIOUS_BEST = "mean_reversion_1h_bb20_rsi30_reentry_4h_sma50_uptrend"
EXPECTED_TIMEFRAME = "30m_execution_with_completed_higher_timeframe_signals"

OUTER_TRAIN = 19_200
OUTER_TEST = 2_400
OUTER_FOLDS = 8
INNER_TRAIN = 12_000
INNER_TEST = 1_200
INNER_FOLDS = 6


def _is_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _as_mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _first_mapping(container: Mapping[str, Any], *keys: str) -> Mapping[str, Any] | None:
    for key in keys:
        value = container.get(key)
        if isinstance(value, Mapping):
            return value
    return None


def _first_list(container: Mapping[str, Any], *keys: str) -> list[Any] | None:
    for key in keys:
        value = container.get(key)
        if isinstance(value, list):
            return value
    return None


def _candidate_names(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    names: list[str] = []
    for item in value:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, Mapping):
            name = item.get("name", item.get("candidate_name"))
            if not isinstance(name, str):
                return None
            names.append(name)
        else:
            return None
    return names


def _contains_untouched_holdout_claim(value: object) -> bool:
    """Reject both schema keys and prose that combine the two claim words."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if "untouched" in lowered and "holdout" in lowered:
                return True
            if _contains_untouched_holdout_claim(child):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_untouched_holdout_claim(child) for child in value)
    if isinstance(value, str):
        lowered = value.lower()
        return "untouched" in lowered and "holdout" in lowered
    return False


def _expected_outer_boundaries() -> tuple[tuple[tuple[int, int], tuple[int, int]], ...]:
    return tuple(
        (
            (0, fold * OUTER_TEST + OUTER_TRAIN),
            (fold * OUTER_TEST + OUTER_TRAIN, fold * OUTER_TEST + OUTER_TRAIN + OUTER_TEST),
        )
        for fold in range(OUTER_FOLDS)
    )


EXPECTED_OUTER_BOUNDARIES = _expected_outer_boundaries()


def _validate_metrics(
    report: object,
    *,
    label: str,
    issues: list[str],
) -> tuple[tuple[tuple[int, int], tuple[int, int]], ...] | None:
    """Validate an eight-fold outer-OOS aggregate and its accounting."""

    if not isinstance(report, Mapping):
        issues.append(f"{label} report is missing")
        return None
    folds = report.get("folds")
    if not isinstance(folds, list) or len(folds) != OUTER_FOLDS:
        issues.append(f"{label} must contain exactly {OUTER_FOLDS} folds")
        return None
    if report.get("fold_count") != OUTER_FOLDS:
        issues.append(f"{label} fold_count must equal {OUTER_FOLDS}")

    curve = report.get("oos_equity_curve_krw")
    valid_curve = (
        isinstance(curve, list)
        and len(curve) == OUTER_TEST * OUTER_FOLDS + 1
        and all(_is_number(value) and value >= 0 for value in curve)
        and curve[0] > 0
    )
    if not valid_curve:
        issues.append(
            f"{label} must contain a finite continuous {OUTER_TEST * OUTER_FOLDS + 1}-point equity curve"
        )

    boundaries: list[tuple[tuple[int, int], tuple[int, int]]] = []
    returns: list[float] = []
    sharpes: list[float] = []
    trade_count = 0
    profitable_folds = 0
    weighted_wins = 0.0
    equity_pairs: list[tuple[float, float, float]] = []
    for index, fold in enumerate(folds):
        if not isinstance(fold, Mapping):
            issues.append(f"{label} fold {index + 1} must be an object")
            continue
        if fold.get("fold") not in (index, index + 1):
            issues.append(f"{label} folds are not numbered in order")
        train = fold.get("train")
        test = fold.get("test")
        valid_boundary = (
            isinstance(train, list)
            and isinstance(test, list)
            and len(train) == len(test) == 2
            and all(_is_nonnegative_int(item) for item in (*train, *test))
        )
        if not valid_boundary:
            issues.append(f"{label} fold {index + 1} has an invalid boundary")
            continue
        boundary = ((train[0], train[1]), (test[0], test[1]))
        boundaries.append(boundary)
        if boundary != EXPECTED_OUTER_BOUNDARIES[index]:
            issues.append(f"{label} fold {index + 1} differs from the frozen outer boundary")

        total_return = fold.get("total_return")
        initial_equity = fold.get("initial_equity_krw")
        final_equity = fold.get("final_equity_krw")
        drawdown = fold.get("max_drawdown")
        sharpe = fold.get("sharpe")
        trades = fold.get("trade_count")
        win_rate = fold.get("win_rate")
        exposure = fold.get("exposure")
        if not _is_number(total_return) or total_return < -1:
            issues.append(f"{label} fold {index + 1} return is invalid")
        else:
            returns.append(float(total_return))
            profitable_folds += total_return > 0
        if (
            not _is_number(initial_equity)
            or initial_equity <= 0
            or not _is_number(final_equity)
            or final_equity < 0
        ):
            issues.append(f"{label} fold {index + 1} equity is invalid")
        elif _is_number(total_return):
            derived_return = final_equity / initial_equity - 1.0
            if not math.isclose(total_return, derived_return, abs_tol=2e-5):
                issues.append(f"{label} fold {index + 1} return does not match equity")
            if _is_number(drawdown) and 0 <= drawdown <= 1:
                equity_pairs.append(
                    (float(initial_equity), float(final_equity), float(drawdown))
                )
        if not _is_number(drawdown) or not 0 <= drawdown <= 1:
            issues.append(f"{label} fold {index + 1} drawdown is invalid")
        if not _is_number(sharpe):
            issues.append(f"{label} fold {index + 1} Sharpe is not finite")
        else:
            sharpes.append(float(sharpe))
        if not _is_nonnegative_int(trades):
            issues.append(f"{label} fold {index + 1} trade count is invalid")
        elif not _is_number(win_rate) or not 0 <= win_rate <= 1:
            issues.append(f"{label} fold {index + 1} win rate is invalid")
        else:
            trade_count += trades
            weighted_wins += trades * float(win_rate)
        if not _is_number(exposure) or not 0 <= exposure <= 1:
            issues.append(f"{label} fold {index + 1} exposure is invalid")

    compounded = report.get("compounded_return")
    maximum_drawdown = report.get("maximum_drawdown")
    mean_sharpe = report.get("mean_sharpe")
    weighted_win_rate = report.get("weighted_win_rate")
    if not _is_number(compounded) or compounded < -1:
        issues.append(f"{label} compounded return is invalid")
    elif len(returns) == OUTER_FOLDS:
        derived = math.prod(1.0 + value for value in returns) - 1.0
        if not math.isclose(compounded, derived, abs_tol=5e-7):
            issues.append(f"{label} compounded return does not match folds")
    if len(equity_pairs) == OUTER_FOLDS:
        for index in range(1, OUTER_FOLDS):
            prior_final = equity_pairs[index - 1][1]
            current_initial = equity_pairs[index][0]
            if not math.isclose(current_initial, prior_final, abs_tol=0.011):
                issues.append(
                    f"{label} fold {index + 1} equity is discontinuous from the prior fold"
                )
        continuous_return = equity_pairs[-1][1] / equity_pairs[0][0] - 1.0
        if _is_number(compounded) and not math.isclose(
            compounded, continuous_return, abs_tol=5e-7
        ):
            issues.append(f"{label} compounded return does not match continuous equity")
    if valid_curve:
        assert isinstance(curve, list)
        curve_return = curve[-1] / curve[0] - 1.0
        if _is_number(compounded) and not math.isclose(
            compounded, curve_return, abs_tol=5e-7
        ):
            issues.append(f"{label} compounded return does not match equity curve")
        for index, fold in enumerate(folds):
            if not isinstance(fold, Mapping):
                continue
            start = index * OUTER_TEST
            end = (index + 1) * OUTER_TEST
            if _is_number(fold.get("initial_equity_krw")) and not math.isclose(
                fold["initial_equity_krw"], curve[start], abs_tol=0.011
            ):
                issues.append(f"{label} fold {index + 1} start differs from equity curve")
            if _is_number(fold.get("final_equity_krw")) and not math.isclose(
                fold["final_equity_krw"], curve[end], abs_tol=0.011
            ):
                issues.append(f"{label} fold {index + 1} end differs from equity curve")
            fold_curve = curve[start : end + 1]
            fold_return = fold_curve[-1] / fold_curve[0] - 1.0
            if _is_number(fold.get("total_return")) and not math.isclose(
                fold["total_return"], fold_return, abs_tol=5e-7
            ):
                issues.append(f"{label} fold {index + 1} return differs from equity curve")
            fold_peak = float(fold_curve[0])
            fold_drawdown = 0.0
            for value in fold_curve:
                fold_peak = max(fold_peak, float(value))
                if fold_peak > 0:
                    fold_drawdown = max(
                        fold_drawdown, 1.0 - float(value) / fold_peak
                    )
            if _is_number(fold.get("max_drawdown")) and not math.isclose(
                fold["max_drawdown"], fold_drawdown, abs_tol=5e-7
            ):
                issues.append(
                    f"{label} fold {index + 1} drawdown differs from equity curve"
                )
    if not _is_number(maximum_drawdown) or not 0 <= maximum_drawdown <= 1:
        issues.append(f"{label} maximum drawdown is invalid")
    elif len(equity_pairs) == OUTER_FOLDS:
        # Fold endpoints and each fold's internally measured drawdown do not
        # reveal the exact full curve, but they do establish a strict lower
        # bound.  Reject optimistic aggregate MDD claims below that bound.
        running_peak = equity_pairs[0][0]
        evidenced_drawdown = 0.0
        for initial, final, fold_drawdown in equity_pairs:
            running_peak = max(running_peak, initial)
            evidenced_drawdown = max(
                evidenced_drawdown,
                fold_drawdown,
                1.0 - initial / running_peak,
                1.0 - final / running_peak,
            )
            running_peak = max(running_peak, final)
        if maximum_drawdown + 5e-7 < evidenced_drawdown:
            issues.append(
                f"{label} maximum drawdown understates continuous equity evidence"
            )
    if valid_curve:
        assert isinstance(curve, list)
        peak = float(curve[0])
        curve_drawdown = 0.0
        for value in curve:
            peak = max(peak, float(value))
            if peak > 0:
                curve_drawdown = max(curve_drawdown, 1.0 - float(value) / peak)
        if _is_number(maximum_drawdown) and not math.isclose(
            maximum_drawdown, curve_drawdown, abs_tol=5e-7
        ):
            issues.append(f"{label} maximum drawdown does not match equity curve")
    if not _is_number(mean_sharpe):
        issues.append(f"{label} mean Sharpe is not finite")
    elif len(sharpes) == OUTER_FOLDS and not math.isclose(
        mean_sharpe, sum(sharpes) / OUTER_FOLDS, abs_tol=1e-5
    ):
        issues.append(f"{label} mean Sharpe does not match folds")
    if report.get("trade_count") != trade_count:
        issues.append(f"{label} trade_count does not match folds")
    if report.get("profitable_folds") != profitable_folds:
        issues.append(f"{label} profitable_folds does not match folds")
    derived_win_rate = weighted_wins / trade_count if trade_count else 0.0
    if not _is_number(weighted_win_rate) or not math.isclose(
        weighted_win_rate, derived_win_rate, abs_tol=5e-7
    ):
        issues.append(f"{label} weighted_win_rate does not match folds")
    return tuple(boundaries) if len(boundaries) == OUTER_FOLDS else None


def _base_and_stress(row: Mapping[str, Any]) -> tuple[object, object]:
    base = row.get("walk_forward", row.get("base"))
    stress = row.get("double_cost_stress", row.get("stress"))
    return base, stress


def _validate_fixed_results(
    payload: Mapping[str, Any], issues: list[str]
) -> tuple[tuple[tuple[int, int], tuple[int, int]], ...] | None:
    candidates = _first_list(
        payload,
        "fixed_candidates",
        "candidates",
        "candidates_ranked_by_oos_return",
    )
    if candidates is None:
        issues.append("fixed candidate results are missing")
        return None
    names = _candidate_names(candidates)
    if (
        names is None
        or len(names) != len(EXPECTED_CANDIDATES)
        or len(set(names)) != len(names)
        or set(names) != EXPECTED_CANDIDATES
    ):
        issues.append("fixed candidate results differ from the frozen candidate set")
        return None

    common: tuple[tuple[tuple[int, int], tuple[int, int]], ...] | None = None
    for row in candidates:
        assert isinstance(row, Mapping)
        base, stress = _base_and_stress(row)
        base_boundaries = _validate_metrics(
            base, label=f"fixed candidate {row['name']} base", issues=issues
        )
        stress_boundaries = _validate_metrics(
            stress, label=f"fixed candidate {row['name']} stress", issues=issues
        )
        for boundaries in (base_boundaries, stress_boundaries):
            if boundaries is not None and common is None:
                common = boundaries
            elif boundaries is not None and boundaries != common:
                issues.append("fixed candidate base/stress fold boundaries differ")
    return common


def _control_rows(controls: object) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    if isinstance(controls, Mapping):
        previous = _first_mapping(
            controls, "previous_best", "previous_best_control", "historical_previous_best"
        )
        buy_hold = _first_mapping(controls, "buy_hold", "buy_and_hold", "benchmark")
        return previous, buy_hold
    if isinstance(controls, list):
        mappings = [row for row in controls if isinstance(row, Mapping)]
        previous = next((row for row in mappings if row.get("name") == EXPECTED_PREVIOUS_BEST), None)
        buy_hold = next((row for row in mappings if row.get("name") == "buy_and_hold"), None)
        return previous, buy_hold
    return None, None


def _validate_controls(
    payload: Mapping[str, Any],
    expected_boundaries: tuple[tuple[tuple[int, int], tuple[int, int]], ...] | None,
    issues: list[str],
) -> None:
    previous, buy_hold = _control_rows(payload.get("controls"))
    for label, row, expected_name in (
        ("previous-best control", previous, EXPECTED_PREVIOUS_BEST),
        ("buy-and-hold control", buy_hold, "buy_and_hold"),
    ):
        if row is None or row.get("name") != expected_name:
            issues.append(f"{label} is missing")
            continue
        base, stress = _base_and_stress(row)
        for cost_label, report in (("base", base), ("stress", stress)):
            boundaries = _validate_metrics(
                report, label=f"{label} {cost_label}", issues=issues
            )
            if expected_boundaries is not None and boundaries != expected_boundaries:
                issues.append(f"{label} {cost_label} boundaries differ from fixed candidates")


def _inner_values(row: Mapping[str, Any]) -> tuple[object, object, object, object, object, object]:
    base = _as_mapping(row.get("base", row.get("walk_forward"))) or {}
    stress = _as_mapping(row.get("stress", row.get("double_cost_stress"))) or {}
    base_return = base.get("compounded_return", row.get("base_compounded_return"))
    stress_return = stress.get("compounded_return", row.get("stress_compounded_return"))
    stress_drawdown = stress.get("maximum_drawdown", row.get("stress_maximum_drawdown"))
    stress_returns = row.get("stress_fold_returns")
    base_returns = row.get("base_fold_returns")
    stress_profitable = stress.get(
        "profitable_folds",
        row.get(
            "stress_profitable_folds",
            row.get("profitable_stress_fold_count"),
        ),
    )
    base_fold_count = base.get(
        "fold_count",
        row.get(
            "base_fold_count",
            len(base_returns) if isinstance(base_returns, list) else None,
        ),
    )
    stress_fold_count = stress.get(
        "fold_count",
        row.get(
            "stress_fold_count",
            len(stress_returns) if isinstance(stress_returns, list) else None,
        ),
    )
    return (
        base_return,
        stress_return,
        stress_drawdown,
        stress_profitable,
        base_fold_count,
        stress_fold_count,
    )


def _validate_nested_selection(
    payload: Mapping[str, Any],
    expected_boundaries: tuple[tuple[tuple[int, int], tuple[int, int]], ...] | None,
    issues: list[str],
) -> None:
    nested = _first_mapping(payload, "nested_selection", "nested")
    if nested is None:
        issues.append("nested selection evidence is missing")
        return
    decisions = _first_list(nested, "decisions", "selection_decisions")
    if decisions is None or len(decisions) != OUTER_FOLDS:
        issues.append("nested selection must contain exactly eight outer decisions")
    else:
        for index, decision in enumerate(decisions):
            if not isinstance(decision, Mapping):
                issues.append(f"nested decision {index + 1} must be an object")
                continue
            if decision.get("fold") not in (index, index + 1):
                issues.append("nested decisions are not numbered in order")
            train = decision.get("train")
            test = decision.get("test")
            if train is None and all(
                key in decision for key in ("train_start", "train_end")
            ):
                train = [decision.get("train_start"), decision.get("train_end")]
            if test is None and all(
                key in decision for key in ("test_start", "test_end")
            ):
                test = [decision.get("test_start"), decision.get("test_end")]
            expected_train, expected_test = EXPECTED_OUTER_BOUNDARIES[index]
            if train != list(expected_train) or test != list(expected_test):
                issues.append(f"nested decision {index + 1} has the wrong outer boundary")
            inner_rows = _first_list(
                decision,
                "inner_candidates",
                "inner_candidate_summaries",
                "candidate_scores",
                "candidates",
            )
            names = _candidate_names(inner_rows)
            if (
                inner_rows is None
                or names is None
                or len(names) != len(EXPECTED_CANDIDATES)
                or len(set(names)) != len(names)
                or set(names) != EXPECTED_CANDIDATES
            ):
                issues.append(f"nested decision {index + 1} has an invalid inner candidate set")
                continue

            eligible: list[tuple[str, float, float]] = []
            for candidate in inner_rows:
                assert isinstance(candidate, Mapping)
                (
                    base_return,
                    stress_return,
                    stress_drawdown,
                    stress_profitable,
                    base_fold_count,
                    stress_fold_count,
                ) = _inner_values(candidate)
                summary_valid = (
                    _is_number(base_return)
                    and _is_number(stress_return)
                    and _is_number(stress_drawdown)
                    and 0 <= stress_drawdown <= 1
                    and _is_nonnegative_int(stress_profitable)
                    and base_fold_count == INNER_FOLDS
                    and stress_fold_count == INNER_FOLDS
                )
                if not summary_valid:
                    issues.append(
                        f"nested decision {index + 1} candidate "
                        f"{candidate.get('name', candidate.get('candidate_name'))} "
                        "has an invalid inner summary"
                    )
                    continue
                base_map = _as_mapping(
                    candidate.get("base", candidate.get("walk_forward"))
                ) or {}
                stress_map = _as_mapping(
                    candidate.get("stress", candidate.get("double_cost_stress"))
                ) or {}
                base_returns = base_map.get(
                    "fold_returns", candidate.get("base_fold_returns")
                )
                stress_returns = stress_map.get(
                    "fold_returns", candidate.get("stress_fold_returns")
                )
                if not (
                    isinstance(base_returns, list)
                    and isinstance(stress_returns, list)
                    and len(base_returns) == len(stress_returns) == INNER_FOLDS
                    and all(
                        _is_number(value) and value >= -1
                        for value in (*base_returns, *stress_returns)
                    )
                ):
                    issues.append(
                        f"nested decision {index + 1} candidate "
                        f"{candidate.get('name', candidate.get('candidate_name'))} "
                        "inner fold returns are missing or invalid"
                    )
                    continue
                derived_base = math.prod(1.0 + value for value in base_returns) - 1.0
                derived_stress = (
                    math.prod(1.0 + value for value in stress_returns) - 1.0
                )
                derived_profitable_stress = sum(value > 0 for value in stress_returns)
                if not (
                    math.isclose(base_return, derived_base, abs_tol=5e-7)
                    and math.isclose(stress_return, derived_stress, abs_tol=5e-7)
                    and stress_profitable == derived_profitable_stress
                ):
                    issues.append(
                        f"nested decision {index + 1} candidate "
                        f"{candidate.get('name', candidate.get('candidate_name'))} "
                        "inner summary does not match its folds"
                    )
                    continue
                expected_eligible = (
                    base_return > 0 and stress_return > 0 and stress_profitable >= 4
                )
                stored_eligible_flag = candidate.get(
                    "eligible", candidate.get("qualifies")
                )
                if stored_eligible_flag is not expected_eligible:
                    issues.append(
                        f"nested decision {index + 1} candidate "
                        f"{candidate.get('name', candidate.get('candidate_name'))} "
                        "eligibility contradicts its inner metrics"
                    )
                if expected_eligible:
                    candidate_name = candidate.get(
                        "name", candidate.get("candidate_name")
                    )
                    eligible.append(
                        (str(candidate_name), float(stress_return), float(stress_drawdown))
                    )

            expected_selected = (
                sorted(eligible, key=lambda item: (-item[1], item[2], item[0]))[0][0]
                if eligible
                else "cash"
            )
            stored_eligible = decision.get("eligible_candidates")
            expected_eligible_names = {item[0] for item in eligible}
            if stored_eligible is not None and (
                not isinstance(stored_eligible, list)
                or len(stored_eligible) != len(set(stored_eligible))
                or set(stored_eligible) != expected_eligible_names
            ):
                issues.append(f"nested decision {index + 1} eligible candidate list is wrong")
            expected_stored_selection = None if expected_selected == "cash" else expected_selected
            if decision.get("selected_candidate") not in {
                expected_selected,
                expected_stored_selection,
            }:
                issues.append(
                    f"nested decision {index + 1} selection contradicts the frozen ranking rule"
                )

    base, stress = _base_and_stress(nested)
    for label, report in (("nested base", base), ("nested stress", stress)):
        boundaries = _validate_metrics(report, label=label, issues=issues)
        if expected_boundaries is not None and boundaries != expected_boundaries:
            issues.append(f"{label} boundaries differ from fixed candidates")


def _validate_experiment_geometry(payload: Mapping[str, Any], issues: list[str]) -> None:
    validation = _as_mapping(payload.get("validation"))
    if validation is None:
        issues.append("validation geometry is missing")
        return
    outer = _first_mapping(validation, "outer", "outer_walk_forward") or validation
    inner = _first_mapping(validation, "inner", "inner_walk_forward", "inner_selection")
    if not (
        outer.get(
            "initial_train_candles_30m",
            outer.get("train_candles_30m", outer.get("train_size")),
        ) == OUTER_TRAIN
        and outer.get("test_candles_30m", outer.get("test_size")) == OUTER_TEST
        and outer.get("fold_count") == OUTER_FOLDS
        and (
            outer.get("expanding") is True
            or "expanding" in str(outer.get("method", "")).lower()
        )
    ):
        issues.append("outer validation must be frozen at 19200/2400/eight folds")
    if inner is None or not (
        inner.get("train_candles_30m", inner.get("train_size")) == INNER_TRAIN
        and inner.get("test_candles_30m", inner.get("test_size")) == INNER_TEST
        and inner.get("fold_count") == INNER_FOLDS
        and (
            inner.get("expanding") is True
            or "expanding" in str(inner.get("method", "")).lower()
        )
    ):
        issues.append("inner validation must be expanding 12000/1200/six folds")
    fill_contract = validation.get(
        "signal_fill_contract", payload.get("signal_fill_contract")
    )
    normalized = re.sub(r"[^a-z0-9]+", " ", str(fill_contract).lower())
    if not all(word in normalized.split() for word in ("completed", "next", "30m", "open")):
        issues.append("signal/fill contract must state completed signal and next 30m open fill")


def _validate_costs(payload: Mapping[str, Any], issues: list[str]) -> None:
    costs = _as_mapping(payload.get("costs"))
    if costs is None:
        issues.append("base/stress costs are missing")
        return
    base_fee = costs.get("base_fee_rate_per_fill", costs.get("base_fee_rate"))
    stress_fee = costs.get("stress_fee_rate_per_fill", costs.get("stress_fee_rate"))
    base_slippage = costs.get(
        "base_slippage_bps_per_fill", costs.get("base_slippage_bps")
    )
    stress_slippage = costs.get(
        "stress_slippage_bps_per_fill", costs.get("stress_slippage_bps")
    )
    if not (
        _is_number(base_fee)
        and math.isclose(base_fee, 0.0025, abs_tol=1e-15)
        and _is_number(stress_fee)
        and math.isclose(stress_fee, 0.005, abs_tol=1e-15)
        and _is_number(base_slippage)
        and math.isclose(base_slippage, 5.0, abs_tol=1e-15)
        and _is_number(stress_slippage)
        and math.isclose(stress_slippage, 10.0, abs_tol=1e-15)
    ):
        issues.append("costs must equal the frozen 0.25%/5bps base and 0.50%/10bps stress contract")


def _bootstrap_quantiles(bootstrap: Mapping[str, Any]) -> tuple[object, object, object] | None:
    quantiles = bootstrap.get("quantiles", bootstrap.get("excess_return_quantiles"))
    if isinstance(quantiles, list) and len(quantiles) == 3:
        return quantiles[0], quantiles[1], quantiles[2]
    if isinstance(quantiles, Mapping):
        for keys in (
            ("p2_5", "p50", "p97_5"),
            ("2.5%", "50%", "97.5%"),
            ("0.025", "0.5", "0.975"),
            ("lower_95", "median", "upper_95"),
        ):
            if all(key in quantiles for key in keys):
                return tuple(quantiles[key] for key in keys)  # type: ignore[return-value]
    if all(key in bootstrap for key in ("lower_95", "median", "upper_95")):
        return bootstrap["lower_95"], bootstrap["median"], bootstrap["upper_95"]
    interval = bootstrap.get("confidence_interval_95")
    if isinstance(interval, list) and len(interval) == 2 and "median" in bootstrap:
        return interval[0], bootstrap["median"], interval[1]
    return None


def _validate_credibility_and_bootstrap(
    payload: Mapping[str, Any], issues: list[str]
) -> None:
    bootstrap = _first_mapping(payload, "bootstrap", "moving_block_bootstrap")
    if bootstrap is None:
        issues.append("moving-block bootstrap evidence is missing")
        return
    iterations = bootstrap.get("iterations", bootstrap.get("replications"))
    block_days = bootstrap.get("block_days", bootstrap.get("block_length_days"))
    if iterations != 5_000 or bootstrap.get("seed") != 20_260_813 or block_days != 7:
        issues.append("bootstrap must use 5000 iterations, seed 20260813, and seven-day blocks")
    quantiles = _bootstrap_quantiles(bootstrap)
    if quantiles is None or not all(_is_number(value) for value in quantiles):
        issues.append("bootstrap must contain three finite excess-return quantiles")
    elif not quantiles[0] <= quantiles[1] <= quantiles[2]:
        issues.append("bootstrap quantiles are out of order")
    probability = bootstrap.get("probability_excess_gt_zero", bootstrap.get("probability_gt_zero"))
    if probability is not None and (not _is_number(probability) or not 0 <= probability <= 1):
        issues.append("bootstrap positive-excess probability is invalid")

    credibility = _first_mapping(payload, "credibility", "historical_credibility")
    if credibility is None:
        issues.append("historical credibility checks are missing")
        return
    checks = _as_mapping(credibility.get("checks"))
    if not checks or not all(isinstance(value, bool) for value in checks.values()):
        issues.append("credibility checks must be a non-empty boolean mapping")
        return

    nested = _first_mapping(payload, "nested_selection", "nested")
    nested_base, nested_stress = _base_and_stress(nested or {})
    nested_base_map = _as_mapping(nested_base)
    nested_stress_map = _as_mapping(nested_stress)
    previous, _ = _control_rows(payload.get("controls"))
    previous_base, _ = _base_and_stress(previous or {})
    previous_base_map = _as_mapping(previous_base)
    fixed = _first_list(
        payload,
        "fixed_candidates",
        "candidates",
        "candidates_ranked_by_oos_return",
    )
    subperiods = (
        nested.get("stress_subperiod_returns") if nested is not None else None
    )
    required_numbers = (
        nested_base_map.get("compounded_return") if nested_base_map else None,
        nested_stress_map.get("compounded_return") if nested_stress_map else None,
        nested_base_map.get("maximum_drawdown") if nested_base_map else None,
        previous_base_map.get("compounded_return") if previous_base_map else None,
    )
    folds = nested_base_map.get("folds") if nested_base_map else None
    if not (
        all(_is_number(value) for value in required_numbers)
        and isinstance(folds, list)
        and len(folds) == OUTER_FOLDS
        and all(isinstance(fold, Mapping) for fold in folds)
        and isinstance(subperiods, list)
        and len(subperiods) == 4
        and all(_is_number(value) for value in subperiods)
        and quantiles is not None
        and all(_is_number(value) for value in quantiles)
        and isinstance(fixed, list)
    ):
        issues.append("credibility checks cannot be recomputed from report evidence")
        return

    trade_count = nested_base_map.get("trade_count")
    profitable_folds = nested_base_map.get("profitable_folds")
    exposures = [fold.get("exposure") for fold in folds]
    if not (
        _is_nonnegative_int(trade_count)
        and _is_nonnegative_int(profitable_folds)
        and all(_is_number(value) and 0 <= value <= 1 for value in exposures)
    ):
        issues.append("credibility sample evidence is invalid")
        return
    exposure_days = sum(float(value) for value in exposures) / OUTER_FOLDS * 400.0

    range_stress_returns: list[float] = []
    range_names = {
        "trading_range_daily_50_band_1pct",
        "trading_range_daily_50_no_band",
    }
    for row in fixed:
        if not isinstance(row, Mapping) or row.get("name") not in range_names:
            continue
        _, row_stress = _base_and_stress(row)
        row_stress_map = _as_mapping(row_stress)
        value = row_stress_map.get("compounded_return") if row_stress_map else None
        if _is_number(value):
            range_stress_returns.append(float(value))
    if len(range_stress_returns) != 2:
        issues.append("credibility adjacent-family evidence is missing")
        return

    expected_checks = {
        "nested_base_exceeds_previous_best_same_window": (
            float(nested_base_map["compounded_return"])
            > float(previous_base_map["compounded_return"])
        ),
        "nested_double_cost_stress_positive": (
            float(nested_stress_map["compounded_return"]) > 0
        ),
        "at_least_five_profitable_outer_folds": profitable_folds >= 5,
        "maximum_drawdown_at_most_10pct": (
            float(nested_base_map["maximum_drawdown"]) <= 0.10
        ),
        "sample_sufficient": trade_count >= 30
        or (trade_count >= 12 and exposure_days >= 120),
        "three_of_four_stress_subperiods_positive": (
            sum(float(value) > 0 for value in subperiods) >= 3
        ),
        "bootstrap_excess_lower_95_positive": float(quantiles[0]) > 0,
        "adjacent_trading_range_variants_stress_positive": all(
            value > 0 for value in range_stress_returns
        ),
    }
    if dict(checks) != expected_checks:
        issues.append("credibility checks contradict report metrics")
    expected = all(expected_checks.values())
    actual = credibility.get(
        "credible_historical_improvement",
        credibility.get("passed"),
    )
    if actual is not expected:
        issues.append("credible_historical_improvement must equal derived checks")
    if credibility.get("can_promote") is not False:
        issues.append("historical research can never authorize promotion")


def _derived_forward_selection(
    decision: object, issues: list[str]
) -> str | None:
    if not isinstance(decision, Mapping):
        issues.append("forward historical selection evidence is missing")
        return None
    if (
        decision.get("fold") != 9
        or decision.get("train") != [0, 40_000]
        or decision.get("test") != [40_000, 40_048]
    ):
        issues.append("forward historical selection has the wrong frozen boundary")
    rows = _first_list(
        decision,
        "inner_candidates",
        "inner_candidate_summaries",
        "candidate_scores",
        "candidates",
    )
    names = _candidate_names(rows)
    if (
        rows is None
        or names is None
        or len(names) != len(EXPECTED_CANDIDATES)
        or len(set(names)) != len(names)
        or set(names) != EXPECTED_CANDIDATES
    ):
        issues.append("forward historical selection has an invalid candidate set")
        return None

    eligible: list[tuple[str, float, float]] = []
    for candidate in rows:
        assert isinstance(candidate, Mapping)
        name = str(candidate.get("name", candidate.get("candidate_name")))
        (
            base_return,
            stress_return,
            stress_drawdown,
            stress_profitable,
            base_fold_count,
            stress_fold_count,
        ) = _inner_values(candidate)
        base_map = _as_mapping(candidate.get("base", candidate.get("walk_forward"))) or {}
        stress_map = _as_mapping(
            candidate.get("stress", candidate.get("double_cost_stress"))
        ) or {}
        base_returns = base_map.get("fold_returns", candidate.get("base_fold_returns"))
        stress_returns = stress_map.get(
            "fold_returns", candidate.get("stress_fold_returns")
        )
        if not (
            _is_number(base_return)
            and _is_number(stress_return)
            and _is_number(stress_drawdown)
            and 0 <= stress_drawdown <= 1
            and _is_nonnegative_int(stress_profitable)
            and base_fold_count == stress_fold_count == INNER_FOLDS
            and isinstance(base_returns, list)
            and isinstance(stress_returns, list)
            and len(base_returns) == len(stress_returns) == INNER_FOLDS
            and all(
                _is_number(value) and value >= -1
                for value in (*base_returns, *stress_returns)
            )
        ):
            issues.append(f"forward historical candidate {name} has invalid inner evidence")
            continue
        derived_base = math.prod(1.0 + value for value in base_returns) - 1.0
        derived_stress = math.prod(1.0 + value for value in stress_returns) - 1.0
        derived_profitable = sum(value > 0 for value in stress_returns)
        if not (
            math.isclose(base_return, derived_base, abs_tol=5e-7)
            and math.isclose(stress_return, derived_stress, abs_tol=5e-7)
            and stress_profitable == derived_profitable
        ):
            issues.append(f"forward historical candidate {name} summary does not match folds")
            continue
        qualifies = base_return > 0 and stress_return > 0 and stress_profitable >= 4
        stored_eligible = candidate.get("eligible", candidate.get("qualifies"))
        if stored_eligible is not qualifies:
            issues.append(f"forward historical candidate {name} eligibility is fabricated")
        if qualifies:
            eligible.append((name, float(stress_return), float(stress_drawdown)))

    expected_names = {item[0] for item in eligible}
    stored_names = decision.get("eligible_candidates")
    if not (
        isinstance(stored_names, list)
        and len(stored_names) == len(set(stored_names))
        and set(stored_names) == expected_names
    ):
        issues.append("forward historical eligible candidate list is wrong")
    expected = (
        sorted(eligible, key=lambda item: (-item[1], item[2], item[0]))[0][0]
        if eligible
        else "cash"
    )
    if decision.get("selected_candidate") not in {
        expected,
        None if expected == "cash" else expected,
    }:
        issues.append("forward historical selection contradicts the frozen ranking rule")
    return expected


def _validate_zero_forward_metric(value: object, *, label: str, issues: list[str]) -> None:
    metric = _as_mapping(value)
    expected = {
        "initial_equity_krw": 20_000.0,
        "final_equity_krw": 20_000.0,
        "total_return": 0.0,
        "max_drawdown": 0.0,
        "sharpe": 0.0,
        "trade_count": 0,
        "win_rate": 0.0,
        "exposure": 0.0,
    }
    if metric is None or any(
        metric.get(key) != expected_value for key, expected_value in expected.items()
    ):
        issues.append(f"{label} must equal the frozen zero-trade cash result")
        return
    curve = metric.get("equity_curve_krw")
    positions = metric.get("position_curve")
    trade_pnls = metric.get("trade_net_pnl_krw")
    if not (
        isinstance(curve, list)
        and len(curve) == 49
        and all(value == 20_000.0 for value in curve)
        and isinstance(positions, list)
        and len(positions) == 49
        and all(value == 0 for value in positions)
        and trade_pnls == []
    ):
        issues.append(f"{label} execution evidence contradicts the cash result")


def _validate_forward_and_selection(payload: Mapping[str, Any], issues: list[str]) -> None:
    if "forward_shadow" in payload or "forward_sample" in payload:
        issues.append("pre-freeze bars must not be labeled as forward evidence")
    forward = _as_mapping(payload.get("posthoc_shadow"))
    if forward is None:
        issues.append("post-hoc shadow diagnostic evidence is missing")
    else:
        if not (
            forward.get("evidence_class") == "posthoc_diagnostic"
            and forward.get("prospective") is False
            and forward.get("observed_before_manifest_freeze") is True
        ):
            issues.append("shadow block must be explicitly classified as post-hoc")
        count = forward.get("candle_count_30m", forward.get("candle_count"))
        if count != EXPECTED_DATASET_COUNT - EXPECTED_HISTORY_COUNT:
            issues.append("post-hoc shadow must contain exactly 48 candles")
        if forward.get("period") != list(EXPECTED_FORWARD_PERIOD):
            issues.append("post-hoc shadow timestamps differ from the frozen 48-bar period")
        forward_hash = forward.get("sha256", forward.get("dataset_sha256"))
        if forward_hash != EXPECTED_FORWARD_SHA256:
            issues.append("post-hoc shadow SHA-256 differs from the frozen 48-bar input")
        expected_action = _derived_forward_selection(
            forward.get("historical_selection"), issues
        )
        policy = _as_mapping(forward.get("frozen_policy"))
        if policy is None or policy.get("action") != expected_action:
            issues.append("forward frozen policy action contradicts historical selection")
        elif expected_action != "cash":
            issues.append("frozen forward result unexpectedly differs from cash")
        if policy is not None:
            _validate_zero_forward_metric(
                policy.get("base"), label="forward base policy", issues=issues
            )
            _validate_zero_forward_metric(
                policy.get("double_cost_stress"),
                label="forward stress policy",
                issues=issues,
            )
        diagnostics = forward.get("candidate_diagnostics")
        diagnostic_names = _candidate_names(diagnostics)
        if (
            not isinstance(diagnostics, list)
            or diagnostic_names is None
            or len(diagnostic_names) != len(EXPECTED_CANDIDATES)
            or len(set(diagnostic_names)) != len(diagnostic_names)
            or set(diagnostic_names) != EXPECTED_CANDIDATES
        ):
            issues.append("forward candidate diagnostics differ from the frozen set")
        else:
            for row in diagnostics:
                assert isinstance(row, Mapping)
                _validate_zero_forward_metric(
                    row.get("base"),
                    label=f"forward diagnostic {row['name']} base",
                    issues=issues,
                )
                _validate_zero_forward_metric(
                    row.get("double_cost_stress"),
                    label=f"forward diagnostic {row['name']} stress",
                    issues=issues,
                )
        sufficient_values = [
            forward.get(key)
            for key in (
                "sample_sufficient_for_promotion",
                "sufficient_for_promotion",
                "sample_sufficient",
            )
            if key in forward
        ]
        if not sufficient_values or any(value is not False for value in sufficient_values):
            issues.append("post-hoc shadow must be explicitly insufficient for promotion")
        if forward.get("selected_candidate") is not None:
            issues.append("post-hoc shadow cannot select a candidate")
        if forward.get("promoted") is True or forward.get("passed") is True:
            issues.append("post-hoc shadow cannot contain a positive promotion claim")
        status = forward.get("status", forward.get("promotion_status"))
        if status is not None and status not in {"RESEARCH_ONLY", "INSUFFICIENT_SAMPLE"}:
            issues.append("post-hoc shadow status cannot claim promotion")

    selection = _as_mapping(payload.get("selection"))
    if selection is None:
        issues.append("selection block is missing")
        return
    if selection.get("status") != "RESEARCH_ONLY":
        issues.append("selection status must remain RESEARCH_ONLY")
    if selection.get("selected_candidate") is not None:
        issues.append("Wave 3 research cannot select a candidate")
    if selection.get("paper_or_live_strategy_changed") is not False:
        issues.append("paper or live strategy must remain unchanged")


def validate_report(payload: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    if payload.get("market") != "KRW-BTC":
        issues.append("top-level market must be KRW-BTC")
    if payload.get("mode") != "bithumb_spot_long_flat_research":
        issues.append("mode must remain Bithumb spot long/flat research")
    if payload.get("timeframe") != EXPECTED_TIMEFRAME:
        issues.append("timeframe must preserve completed higher-timeframe signals")
    if payload.get("historical_data_reused") is not True:
        issues.append("historical_data_reused must be explicitly true")
    if _contains_untouched_holdout_claim(payload):
        issues.append("report must not claim an untouched holdout")

    dataset = _as_mapping(payload.get("dataset"))
    if dataset is None or not (
        dataset.get("market") == "KRW-BTC"
        and dataset.get("candle_count") == EXPECTED_DATASET_COUNT
        and dataset.get("sha256") == EXPECTED_DATASET_SHA256
    ):
        issues.append("dataset manifest does not match the frozen 40048-candle input")
    history = _first_mapping(payload, "historical_prefix", "historical_dataset")
    if history is None and dataset is not None:
        history = _first_mapping(dataset, "historical_prefix", "historical_dataset")
    if history is None or not (
        history.get("candle_count") == EXPECTED_HISTORY_COUNT
        and history.get("sha256") == EXPECTED_HISTORY_SHA256
    ):
        issues.append("historical prefix does not match the reused 40000-candle input")

    manifest = _first_mapping(payload, "candidate_manifest", "pre_registered_candidates")
    if manifest is None:
        issues.append("candidate manifest is missing")
    else:
        names = _candidate_names(
            manifest.get("candidates", manifest.get("names"))
        )
        count = manifest.get("candidate_count", len(names) if names is not None else None)
        canonical_manifest = dict(manifest)
        declared_manifest_hash = canonical_manifest.pop("sha256", None)
        canonical_manifest.pop("candidate_count", None)
        computed_manifest_hash = hashlib.sha256(
            json.dumps(
                canonical_manifest,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        if (
            declared_manifest_hash != EXPECTED_CANDIDATE_MANIFEST_SHA256
            or computed_manifest_hash != EXPECTED_CANDIDATE_MANIFEST_SHA256
        ):
            issues.append("candidate manifest SHA-256 differs from the frozen manifest")
        if (
            names is None
            or count != len(EXPECTED_CANDIDATES)
            or count > 12
            or len(names) != len(set(names))
            or set(names) != EXPECTED_CANDIDATES
        ):
            issues.append("candidate manifest differs from the exact five-candidate set")

    _validate_experiment_geometry(payload, issues)
    _validate_costs(payload, issues)
    boundaries = _validate_fixed_results(payload, issues)
    _validate_controls(payload, boundaries, issues)
    _validate_nested_selection(payload, boundaries, issues)
    _validate_credibility_and_bootstrap(payload, issues)
    _validate_forward_and_selection(payload, issues)
    return issues


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--input", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        issues = [str(exc)]
    else:
        issues = (
            validate_report(payload)
            if isinstance(payload, Mapping)
            else ["report root must be an object"]
        )
        replay_performed = False
        if args.input is not None and isinstance(payload, Mapping):
            try:
                root = Path(__file__).resolve().parents[1]
                sys.path.insert(0, str(root / "src"))
                from bithumb_coin_trader.data import load_candles_csv
                from run_wave3_research import build_report

                generated_at = datetime.fromisoformat(
                    str(payload["generated_at"]).replace("Z", "+00:00")
                )
                replay = build_report(
                    load_candles_csv(args.input), generated_at=generated_at
                )
                replay_performed = True
                if json.dumps(
                    replay,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ) != json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ):
                    issues.append("report does not match deterministic replay from raw input")
            except (KeyError, OSError, ValueError, TypeError, ImportError) as exc:
                issues.append(f"could not replay report from raw input: {exc}")
    if 'replay_performed' not in locals():
        replay_performed = False
    result = {
        "status": "passed" if not issues else "failed",
        "passed": not issues,
        "summary": (
            "Wave 3 artifact satisfies the frozen research mission"
            if not issues
            else "; ".join(issues)
        ),
        "issues": issues,
        "output_artifact_path": str(args.report),
        "replay_performed": replay_performed,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.result is not None:
        try:
            args.result.write_text(rendered, encoding="utf-8")
        except OSError as exc:
            rendered = json.dumps(
                {
                    "status": "failed",
                    "passed": False,
                    "summary": f"could not write validator result: {exc}",
                    "issues": [str(exc)],
                    "output_artifact_path": str(args.report),
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n"
            print(rendered, end="")
            return 2
    print(rendered, end="")
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
