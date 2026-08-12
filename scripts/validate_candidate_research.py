#!/usr/bin/env python3
"""Fail-closed validator for the fixed BTC candidate research artifact."""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


EXPECTED_CANDIDATES = {
    "dc_30m_bb20_rsi14_armed_reentry_5pct_exit",
    "mean_reversion_1h_bb20_rsi30_reentry_24bar_exit",
    "mean_reversion_1h_bb20_rsi30_reentry_ema200_uptrend",
    "mean_reversion_1h_bb20_rsi30_reentry_4h_sma50_uptrend",
    "bb_squeeze_bottom20_breakout_120_exit_midline",
    "trend_daily_close_above_sma140",
    "trend_daily_close_above_sma200",
    "trend_daily_sma50_above_sma200",
    "donchian_4h_55_20_breakout",
    "donchian_4h_20_10_breakout",
    "trend_daily_tsmom_365",
    "trend_monthly_close_above_sma10",
    "donchian_daily_55_20_breakout",
    "donchian_daily_20_10_breakout",
    "dc_30m_bb20_rsi14_with_4h_sma50_uptrend",
    "dc_30m_bb20_rsi14_with_daily_sma140_uptrend",
}


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _parse_aware_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _validate_metrics(
    report: object,
    *,
    label: str,
    issues: list[str],
) -> tuple[tuple[tuple[int, int], tuple[int, int]], ...] | None:
    if not isinstance(report, Mapping):
        issues.append(f"{label} report is missing")
        return None
    folds = report.get("folds")
    if not isinstance(folds, list) or not folds or not all(isinstance(fold, Mapping) for fold in folds):
        issues.append(f"{label} folds are missing")
        return None
    if report.get("fold_count") != len(folds):
        issues.append(f"{label} fold_count does not match its folds")

    boundaries: list[tuple[tuple[int, int], tuple[int, int]]] = []
    fold_returns: list[float] = []
    trade_count = 0
    profitable_folds = 0
    weighted_wins = 0.0
    fold_sharpes: list[float] = []
    for fold in folds:
        train = fold.get("train")
        test = fold.get("test")
        if not (
            isinstance(train, list)
            and isinstance(test, list)
            and len(train) == len(test) == 2
            and all(_is_nonnegative_int(value) for value in (*train, *test))
            and train[0] < train[1] == test[0] < test[1]
        ):
            issues.append(f"{label} has an invalid fold boundary")
            return None
        boundaries.append(((train[0], train[1]), (test[0], test[1])))
        total_return = fold.get("total_return")
        initial_equity = fold.get("initial_equity_krw")
        final_equity = fold.get("final_equity_krw")
        maximum_drawdown = fold.get("max_drawdown")
        fold_sharpe = fold.get("sharpe")
        fold_trades = fold.get("trade_count")
        win_rate = fold.get("win_rate")
        exposure = fold.get("exposure")
        if not _is_finite_number(total_return) or total_return < -1:
            issues.append(f"{label} has an impossible fold return")
            continue
        if (
            not _is_finite_number(initial_equity)
            or initial_equity <= 0
            or not _is_finite_number(final_equity)
            or final_equity < 0
        ):
            issues.append(f"{label} has invalid fold equity")
        else:
            derived_return = final_equity / initial_equity - 1.0
            if not math.isclose(total_return, derived_return, abs_tol=2e-5):
                issues.append(f"{label} fold return does not match its equity")
        if not _is_finite_number(maximum_drawdown) or not 0 <= maximum_drawdown <= 1:
            issues.append(f"{label} has an impossible fold drawdown")
        if not _is_finite_number(fold_sharpe):
            issues.append(f"{label} fold Sharpe must be finite")
        else:
            fold_sharpes.append(float(fold_sharpe))
        if not _is_nonnegative_int(fold_trades):
            issues.append(f"{label} has an invalid fold trade count")
            continue
        if not _is_finite_number(win_rate) or not 0 <= win_rate <= 1:
            issues.append(f"{label} has an invalid fold win rate")
            continue
        if not _is_finite_number(exposure) or not 0 <= exposure <= 1:
            issues.append(f"{label} has an invalid fold exposure")
        fold_returns.append(float(total_return))
        trade_count += fold_trades
        weighted_wins += float(win_rate) * fold_trades
        profitable_folds += total_return > 0

    compounded = report.get("compounded_return")
    maximum_drawdown = report.get("maximum_drawdown")
    mean_sharpe = report.get("mean_sharpe")
    weighted_win_rate = report.get("weighted_win_rate")
    if not _is_finite_number(compounded) or compounded < -1:
        issues.append(f"{label} compounded return is impossible")
    elif len(fold_returns) == len(folds):
        derived = math.prod(1.0 + value for value in fold_returns) - 1.0
        if not math.isclose(compounded, derived, abs_tol=5e-7):
            issues.append(f"{label} compounded return does not match its folds")
    if not _is_finite_number(maximum_drawdown) or not 0 <= maximum_drawdown <= 1:
        issues.append(f"{label} maximum drawdown is impossible")
    if not _is_finite_number(mean_sharpe):
        issues.append(f"{label} mean Sharpe must be finite")
    elif len(fold_sharpes) == len(folds):
        derived_mean_sharpe = sum(fold_sharpes) / len(fold_sharpes)
        if not math.isclose(mean_sharpe, derived_mean_sharpe, abs_tol=1e-5):
            issues.append(f"{label} mean Sharpe does not match its folds")
    if report.get("trade_count") != trade_count:
        issues.append(f"{label} trade_count does not match its folds")
    if report.get("profitable_folds") != profitable_folds:
        issues.append(f"{label} profitable_folds does not match its folds")
    derived_win_rate = weighted_wins / trade_count if trade_count else 0.0
    if not _is_finite_number(weighted_win_rate) or not math.isclose(
        weighted_win_rate, derived_win_rate, abs_tol=5e-7
    ):
        issues.append(f"{label} weighted win rate does not match its folds")
    return tuple(boundaries)


def validate_report(payload: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    dataset = payload.get("dataset")
    if not isinstance(dataset, Mapping) or dataset.get("market") != "KRW-BTC":
        issues.append("dataset must identify KRW-BTC")
        dataset_count = None
    else:
        dataset_count = dataset.get("candle_count")
        if not _is_nonnegative_int(dataset_count) or dataset_count == 0:
            issues.append("dataset candle_count must be positive")
        digest = dataset.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            issues.append("dataset must include a hexadecimal SHA-256 manifest")
    if payload.get("market") != "KRW-BTC":
        issues.append("top-level market must identify KRW-BTC")
    if payload.get("mode") != "bithumb_spot_long_flat_research":
        issues.append("report mode must remain spot long/flat research")
    if (
        payload.get("timeframe")
        != "30m_execution_with_completed_higher_timeframe_signals"
    ):
        issues.append(
            "report timeframe must identify 30-minute execution and completed "
            "higher-timeframe signals"
        )

    data_quality = payload.get("data_quality")
    observed_at = _parse_aware_datetime(
        data_quality.get("observed_at") if isinstance(data_quality, Mapping) else None
    )
    if not (
        isinstance(data_quality, Mapping)
        and observed_at is not None
        and data_quality.get("expected_interval_minutes") == 30
        and _is_nonnegative_int(data_quality.get("gap_event_count"))
        and _is_nonnegative_int(data_quality.get("missing_candle_count"))
        and _is_nonnegative_int(data_quality.get("maximum_gap_minutes"))
        and data_quality["maximum_gap_minutes"] >= 30
        and data_quality.get("gap_policy") == "never forward-fill; omit incomplete aggregate buckets"
    ):
        issues.append("30-minute data-quality evidence is missing or invalid")
    elif isinstance(dataset, Mapping):
        dataset_start = _parse_aware_datetime(dataset.get("start_at"))
        dataset_end = _parse_aware_datetime(dataset.get("end_at"))
        if dataset_end is None or dataset_end.timestamp() + 30 * 60 > observed_at.timestamp():
            issues.append("dataset final candle is not complete at the recorded cutoff")
        if (
            dataset_start is None
            or dataset_end is None
            or not _is_nonnegative_int(dataset_count)
        ):
            issues.append("dataset timestamps cannot support cadence verification")
        else:
            elapsed = dataset_end.timestamp() - dataset_start.timestamp()
            expected_slots = int(elapsed // (30 * 60)) + 1
            derived_missing = expected_slots - dataset_count
            if derived_missing != data_quality.get("missing_candle_count"):
                issues.append("missing-candle count does not match the dataset span")
            if data_quality.get("maximum_gap_minutes", 0) > 49 * 30:
                issues.append("maximum candle gap exceeds the research contract")
            if (data_quality.get("gap_event_count") == 0) != (derived_missing == 0):
                issues.append("gap-event evidence contradicts missing candles")

    validation = payload.get("validation")
    if not isinstance(validation, Mapping):
        issues.append("validation block is missing")
        return issues
    if validation.get("candidate_count") != len(EXPECTED_CANDIDATES):
        issues.append("candidate count does not match the pre-registered set")
    if validation.get("oos_tuning") is not False:
        issues.append("OOS tuning must be explicitly false")
    calendar_folds = validation.get("calendar_folds")
    if not isinstance(calendar_folds, list) or len(calendar_folds) < 6:
        issues.append("at least six calendar folds are required")
    train_size = validation.get("train_candles_30m")
    test_size = validation.get("test_candles_30m")
    valid_sizes = (
        _is_nonnegative_int(train_size)
        and train_size > 0
        and _is_nonnegative_int(test_size)
        and test_size > 0
    )
    if not valid_sizes:
        issues.append("train and test sizes must be positive integers")

    costs = payload.get("costs")
    if not isinstance(costs, Mapping):
        issues.append("cost assumptions are missing")
    elif not (
        _is_finite_number(costs.get("base_fee_rate_per_fill"))
        and _is_finite_number(costs.get("stress_fee_rate_per_fill"))
        and costs["stress_fee_rate_per_fill"] > costs["base_fee_rate_per_fill"]
        and _is_finite_number(costs.get("base_slippage_bps_per_fill"))
        and _is_finite_number(costs.get("stress_slippage_bps_per_fill"))
        and costs["stress_slippage_bps_per_fill"] > costs["base_slippage_bps_per_fill"]
    ):
        issues.append("stress costs must be finite and greater than base costs")

    candidates = payload.get("candidates_ranked_by_oos_return")
    if not isinstance(candidates, list):
        issues.append("candidate results are missing")
        return issues
    names = {row.get("name") for row in candidates if isinstance(row, Mapping)}
    if names != EXPECTED_CANDIDATES or len(candidates) != len(EXPECTED_CANDIDATES):
        issues.append("candidate results differ from the pre-registered set")

    expected_boundaries: tuple[tuple[tuple[int, int], tuple[int, int]], ...] | None = None
    promoted_names: list[str] = []
    base_returns: list[float] = []
    for row in candidates:
        if not isinstance(row, Mapping):
            issues.append("candidate row must be an object")
            continue
        base = row.get("walk_forward")
        stress = row.get("double_cost_stress")
        promotion = row.get("promotion")
        base_boundaries = _validate_metrics(base, label=f"{row.get('name')} base", issues=issues)
        stress_boundaries = _validate_metrics(stress, label=f"{row.get('name')} stress", issues=issues)
        for boundaries in (base_boundaries, stress_boundaries):
            if boundaries is not None and expected_boundaries is None:
                expected_boundaries = boundaries
            elif boundaries is not None and boundaries != expected_boundaries:
                issues.append("candidate or stress fold boundaries differ")
        if not isinstance(promotion, Mapping) or not isinstance(promotion.get("checks"), Mapping):
            issues.append(f"{row.get('name')} promotion checks are missing")
        elif isinstance(base, Mapping) and isinstance(stress, Mapping):
            folds = base.get("folds")
            expected_checks = {
                "at_least_six_folds": isinstance(folds, list) and len(folds) >= 6,
                "at_least_thirty_oos_trades": _is_nonnegative_int(base.get("trade_count"))
                and base["trade_count"] >= 30,
                "majority_profitable_folds": isinstance(folds, list)
                and sum(
                    isinstance(fold, Mapping)
                    and _is_finite_number(fold.get("total_return"))
                    and fold["total_return"] > 0
                    for fold in folds
                )
                > len(folds) / 2,
                "positive_oos_return_after_costs": _is_finite_number(base.get("compounded_return"))
                and base["compounded_return"] > 0,
                "positive_double_cost_stress": _is_finite_number(stress.get("compounded_return"))
                and stress["compounded_return"] > 0,
            }
            if dict(promotion["checks"]) != expected_checks:
                issues.append(f"{row.get('name')} promotion checks contradict its metrics")
            should_promote = all(expected_checks.values())
            expected_status = "PAPER_CANDIDATE" if should_promote else "RESEARCH_ONLY"
            if promotion.get("status") != expected_status:
                issues.append(f"{row.get('name')} promotion status contradicts its checks")
            if should_promote:
                promoted_names.append(str(row.get("name")))
        if isinstance(base, Mapping) and _is_finite_number(base.get("compounded_return")):
            base_returns.append(float(base["compounded_return"]))

    if len(base_returns) == len(candidates) and base_returns != sorted(base_returns, reverse=True):
        issues.append("candidate rows are not ranked by OOS compounded return")
    if expected_boundaries and valid_sizes:
        for train, test in expected_boundaries:
            if train_size != train[1] - train[0] or test_size != test[1] - test[0]:
                issues.append("validation train/test sizes differ from candidate folds")
                break
        if len(expected_boundaries) != len(calendar_folds or []):
            issues.append("calendar folds do not match candidate fold count")
        elif isinstance(calendar_folds, list):
            previous_test_end: datetime | None = None
            for index, calendar_fold in enumerate(calendar_folds):
                if not isinstance(calendar_fold, Mapping) or calendar_fold.get("fold") != index + 1:
                    issues.append("calendar folds are not numbered in order")
                    break
                train = calendar_fold.get("train")
                test = calendar_fold.get("test")
                if not (
                    isinstance(train, list)
                    and isinstance(test, list)
                    and len(train) == len(test) == 2
                ):
                    issues.append("calendar fold spans are invalid")
                    break
                parsed = [_parse_aware_datetime(value) for value in (*train, *test)]
                if any(value is None for value in parsed):
                    issues.append("calendar fold timestamps are invalid")
                    break
                train_start, train_end, test_start, test_end = parsed
                assert train_start and train_end and test_start and test_end
                if not train_start <= train_end < test_start <= test_end:
                    issues.append("calendar fold timestamps are not chronological")
                    break
                expected_train_span = (train_size - 1) * 30 * 60
                expected_test_span = (test_size - 1) * 30 * 60
                maximum_extra = (
                    data_quality.get("missing_candle_count", 0) * 30 * 60
                    if isinstance(data_quality, Mapping)
                    else 0
                )
                train_span = train_end.timestamp() - train_start.timestamp()
                test_span = test_end.timestamp() - test_start.timestamp()
                if not (
                    expected_train_span <= train_span <= expected_train_span + maximum_extra
                    and expected_test_span <= test_span <= expected_test_span + maximum_extra
                ):
                    issues.append("calendar fold spans do not match indexed candle counts")
                    break
                if previous_test_end is not None and test_start <= previous_test_end:
                    issues.append("calendar test spans overlap or are out of order")
                    break
                previous_test_end = test_end
            if calendar_folds and isinstance(dataset, Mapping):
                dataset_start = _parse_aware_datetime(dataset.get("start_at"))
                first_train = calendar_folds[0].get("train") if isinstance(calendar_folds[0], Mapping) else None
                first_train_start = (
                    _parse_aware_datetime(first_train[0])
                    if isinstance(first_train, list) and first_train
                    else None
                )
                if dataset_start is None or dataset_start != first_train_start:
                    issues.append("calendar folds are not bound to the dataset start")

    benchmark = payload.get("benchmark")
    if not isinstance(benchmark, Mapping) or benchmark.get("name") != "buy_and_hold":
        issues.append("buy-and-hold benchmark is missing")
    else:
        benchmark_boundaries = _validate_metrics(
            benchmark.get("walk_forward"), label="buy-and-hold benchmark", issues=issues
        )
        if expected_boundaries is not None and benchmark_boundaries != expected_boundaries:
            issues.append("benchmark fold boundaries differ from candidate folds")

    selection = payload.get("selection")
    holdout = payload.get("final_untouched_holdout")
    holdout_passed = False
    holdout_candidate: object = None
    holdout_count: object = None
    if not isinstance(holdout, Mapping) or holdout.get("candidate") not in EXPECTED_CANDIDATES:
        issues.append("untouched holdout evidence is missing")
    else:
        holdout_candidate = holdout.get("candidate")
        holdout_count = holdout.get("candle_count_30m")
        if not _is_nonnegative_int(holdout_count) or holdout_count < 2:
            issues.append("holdout candle count is invalid")
        holdout_base = holdout.get("walk_forward")
        holdout_stress = holdout.get("double_cost_stress")
        _validate_metrics(holdout_base, label="holdout base", issues=issues)
        _validate_metrics(holdout_stress, label="holdout stress", issues=issues)
        if isinstance(holdout_base, Mapping) and isinstance(holdout_stress, Mapping):
            holdout_passed = (
                _is_nonnegative_int(holdout_base.get("trade_count"))
                and holdout_base["trade_count"] > 0
                and _is_finite_number(holdout_base.get("compounded_return"))
                and holdout_base["compounded_return"] > 0
                and _is_finite_number(holdout_stress.get("compounded_return"))
                and holdout_stress["compounded_return"] > 0
            )
        if holdout.get("passed") is not holdout_passed:
            issues.append("holdout passed flag contradicts holdout metrics")

    provisional = candidates[0].get("name") if candidates and isinstance(candidates[0], Mapping) else None
    expected_holdout_candidate = promoted_names[0] if promoted_names else provisional
    if holdout_candidate != expected_holdout_candidate:
        issues.append("holdout candidate does not match the pre-holdout selection rule")
    if _is_nonnegative_int(dataset_count) and expected_boundaries and _is_nonnegative_int(holdout_count):
        if dataset_count != expected_boundaries[-1][1][1] + holdout_count:
            issues.append("dataset count does not equal OOS folds plus holdout")

    if not isinstance(selection, Mapping):
        issues.append("selection block is missing")
    else:
        if selection.get("paper_or_live_strategy_changed") is not False:
            issues.append("research must not change the paper or live strategy")
        if selection.get("adaptive_search_requires_forward_validation") is not True:
            issues.append("adaptive second-wave research must require frozen forward validation")
        if selection.get("provisional_best_before_holdout") != provisional:
            issues.append("provisional selection does not match the top-ranked candidate")
        expected_selected = None
        if selection.get("selected_candidate") != expected_selected:
            issues.append("adaptive research cannot select a candidate before forward validation")
        expected_status = "RESEARCH_ONLY"
        if selection.get("status") != expected_status:
            issues.append("selection status contradicts the selected candidate")
    return issues


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        result = {"status": "failed", "passed": False, "summary": str(exc)}
    else:
        issues = validate_report(payload) if isinstance(payload, Mapping) else ["report root must be an object"]
        result = {
            "status": "passed" if not issues else "failed",
            "passed": not issues,
            "summary": "candidate research artifact satisfies the fixed mission" if not issues else "; ".join(issues),
            "output_artifact_path": str(args.report),
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.result:
        args.result.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
