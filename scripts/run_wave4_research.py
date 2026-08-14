#!/usr/bin/env python3
"""Generate the frozen 2026-08-14 two-persona KRW-BTC Wave 4 artifact."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.data import dataset_manifest, load_candles_csv
from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.research import CandidateComparisonReport, ProjectResearchReport
from bithumb_coin_trader.wave3 import (
    PREVIOUS_BEST_CANDIDATE,
    deterministic_daily_moving_block_bootstrap,
    fixed_control_comparison,
)
from bithumb_coin_trader.wave4 import (
    Wave4NestedConfig,
    compare_wave4_candidates,
    run_wave4_nested_research,
    wave4_candidate_manifest,
    wave4_candidate_manifest_hash,
)


FIXED_START = datetime(2024, 4, 28, 13, 30, tzinfo=UTC)
HISTORY_END = datetime(2026, 8, 12, 11, 0, tzinfo=UTC)
WAVE3_FORWARD_START = datetime(2026, 8, 13, 11, 30, tzinfo=UTC)
WAVE4_FREEZE_AT = datetime(2026, 8, 14, 11, 18, tzinfo=UTC)
OBSERVED_AT = datetime(2026, 8, 14, 11, 0, tzinfo=UTC)
EXPECTED_HISTORY_SHA256 = (
    "dc3537c862bc54efebfd215807e2ab57da66396ebfbfcf3d5a243327b9817248"
)
PREVIOUS_BEST_RETURN = 0.01019286


def _parse_aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone offset")
    return parsed.astimezone(UTC)


def _manifest(candles: Sequence[Candle]) -> dict[str, Any]:
    value = dataset_manifest(candles)
    return {
        "schema_version": value.schema_version,
        "market": value.market,
        "candle_count": value.candle_count,
        "start_at": value.start_at.isoformat() if value.start_at else None,
        "end_at": value.end_at.isoformat() if value.end_at else None,
        "sha256": value.sha256,
    }


def _trade_evidence(report: ProjectResearchReport) -> dict[str, Any]:
    trades = [trade for fold in report.folds for trade in fold.result.trades]
    positive = [trade.net_pnl for trade in trades if trade.net_pnl > 0]
    positive_total = sum(positive)
    concentration = max(positive) / positive_total if positive_total else 0.0
    return {
        "non_final_trade_count": sum(not trade.is_final_liquidation for trade in trades),
        "final_liquidation_count": sum(trade.is_final_liquidation for trade in trades),
        "max_positive_trade_contribution": concentration,
        "trade_net_pnl_krw": [trade.net_pnl for trade in trades],
        "trade_is_final_liquidation": [trade.is_final_liquidation for trade in trades],
    }


def _report(report: ProjectResearchReport) -> dict[str, Any]:
    trade_evidence = _trade_evidence(report)
    return {
        "fold_count": len(report.folds),
        "compounded_return": report.compounded_return,
        "maximum_drawdown": report.maximum_drawdown,
        "mean_sharpe": report.mean_sharpe,
        "trade_count": report.trade_count,
        "weighted_win_rate": report.weighted_win_rate,
        "profitable_folds": sum(fold.result.total_return > 0 for fold in report.folds),
        "oos_equity_curve_krw": list(report.oos_equity_curve),
        "non_final_trade_count": trade_evidence["non_final_trade_count"],
        "max_positive_trade_contribution": trade_evidence[
            "max_positive_trade_contribution"
        ],
        "trade_evidence": trade_evidence,
        "folds": [
            {
                "fold": fold.fold + 1,
                "train": [fold.train_start, fold.train_end],
                "test": [fold.test_start, fold.test_end],
                "initial_equity_krw": fold.result.initial_equity,
                "final_equity_krw": fold.result.final_equity,
                "total_return": fold.result.total_return,
                "max_drawdown": fold.result.max_drawdown,
                "sharpe": fold.result.sharpe,
                "trade_count": fold.result.trade_count,
                "win_rate": fold.result.win_rate,
                "exposure": fold.result.exposure,
            }
            for fold in report.folds
        ],
    }


def _comparison_rows(
    base: CandidateComparisonReport, stress: CandidateComparisonReport
) -> list[dict[str, Any]]:
    stress_by_name = {item.candidate_name: item for item in stress.candidates}
    rows = [
        {
            "name": item.candidate_name,
            "walk_forward": _report(item),
            "double_cost_stress": _report(stress_by_name[item.candidate_name]),
        }
        for item in base.candidates
    ]
    return rows


def _find_report(
    comparison: CandidateComparisonReport, name: str
) -> ProjectResearchReport:
    return next(item for item in comparison.candidates if item.candidate_name == name)


def _decision_payload(decision: Any) -> dict[str, Any]:
    return {
        "fold": decision.fold + 1,
        "train": [decision.train_start, decision.train_end],
        "test": [decision.test_start, decision.test_end],
        "selected_candidate": decision.selected_candidate or "cash",
        "inner_candidates": [
            {
                "name": score.candidate_name,
                "base_return": score.base_compounded_return,
                "base_maximum_drawdown": score.base_maximum_drawdown,
                "stress_return": score.stress_compounded_return,
                "stress_maximum_drawdown": score.stress_maximum_drawdown,
                "base_fold_returns": list(score.base_fold_returns),
                "stress_fold_returns": list(score.stress_fold_returns),
                "profitable_stress_folds": score.profitable_stress_fold_count,
                "eligible": score.qualifies,
            }
            for score in decision.candidate_scores
        ],
    }


def _quarter_returns(curve: Sequence[float]) -> list[float]:
    periods = len(curve) - 1
    if periods <= 0 or periods % 4:
        raise ValueError("continuous OOS curve must split into four equal quarters")
    width = periods // 4
    return [
        curve[(index + 1) * width] / curve[index * width] - 1.0
        for index in range(4)
    ]


def _data_quality(candles: Sequence[Candle]) -> dict[str, Any]:
    interval = timedelta(minutes=30)
    gaps = [
        candles[index].timestamp - candles[index - 1].timestamp
        for index in range(1, len(candles))
        if candles[index].timestamp - candles[index - 1].timestamp != interval
    ]
    return {
        "observed_at": OBSERVED_AT.isoformat(),
        "expected_interval_minutes": 30,
        "gap_event_count": len(gaps),
        "missing_candle_count": sum(int(gap / interval) - 1 for gap in gaps),
        "maximum_gap_minutes": max(
            [30, *(int(gap.total_seconds() // 60) for gap in gaps)]
        ),
        "gap_policy": "reset FLAT; never forward-fill; require next complete KST day",
    }


def _check(passed: bool, actual: Any, requirement: str) -> dict[str, Any]:
    return {"passed": bool(passed), "actual": actual, "requirement": requirement}


def build_report(
    candles: Sequence[Candle], *, generated_at: datetime
) -> dict[str, Any]:
    selected = tuple(
        candle
        for candle in candles
        if FIXED_START <= candle.timestamp and candle.timestamp + timedelta(minutes=30) <= OBSERVED_AT
    )
    if len(selected) < 40_000 or any(candle.market != "KRW-BTC" for candle in selected):
        raise ValueError("Wave 4 input must contain the fixed KRW-BTC history")
    if any(
        selected[index].timestamp <= selected[index - 1].timestamp
        for index in range(1, len(selected))
    ):
        raise ValueError("Wave 4 candles must be strictly chronological")
    history = tuple(candle for candle in selected if candle.timestamp <= HISTORY_END)
    if len(history) != 40_000 or _manifest(history)["sha256"] != EXPECTED_HISTORY_SHA256:
        raise ValueError("Wave 4 historical prefix differs from the frozen reused input")

    base_settings = TradingSettings()
    stress_settings = TradingSettings(fee_rate=0.005, slippage_bps=10)
    config = Wave4NestedConfig()
    manifest = wave4_candidate_manifest()
    manifest_hash = wave4_candidate_manifest_hash(manifest)

    fixed_base = compare_wave4_candidates(
        history, settings=base_settings, config=config
    )
    fixed_stress = compare_wave4_candidates(
        history, settings=stress_settings, config=config
    )
    nested = run_wave4_nested_research(
        history,
        base_settings=base_settings,
        stress_settings=stress_settings,
        config=config,
    )
    control_base = fixed_control_comparison(history, settings=base_settings)
    control_stress = fixed_control_comparison(history, settings=stress_settings)
    previous_base = _find_report(control_base, PREVIOUS_BEST_CANDIDATE)
    previous_stress = _find_report(control_stress, PREVIOUS_BEST_CANDIDATE)
    buy_hold_base = _find_report(control_base, "buy_and_hold_long")
    buy_hold_stress = _find_report(control_stress, "buy_and_hold_long")

    execution_candles = history[19_199:38_400]
    bootstrap = deterministic_daily_moving_block_bootstrap(
        nested.base.oos_equity_curve,
        previous_base.oos_equity_curve,
        execution_candles,
        block_days=7,
        iterations=5_000,
        seed=20_260_814,
    )
    stress_quarters = _quarter_returns(nested.stress.oos_equity_curve)
    nested_trade = _trade_evidence(nested.base)
    checks = {
        "base_exceeds_previous_best_1_019286pct": _check(
            nested.base.compounded_return > PREVIOUS_BEST_RETURN,
            nested.base.compounded_return,
            "> 0.01019286",
        ),
        "double_cost_return_positive": _check(
            nested.stress.compounded_return > 0,
            nested.stress.compounded_return,
            "> 0",
        ),
        "maximum_drawdown_at_most_10pct": _check(
            nested.base.maximum_drawdown <= 0.10,
            nested.base.maximum_drawdown,
            "<= 0.10",
        ),
        "at_least_five_of_eight_positive_outer_folds": _check(
            sum(fold.result.total_return > 0 for fold in nested.base.folds) >= 5,
            sum(fold.result.total_return > 0 for fold in nested.base.folds),
            ">= 5",
        ),
        "at_least_three_of_four_positive_stress_quarters": _check(
            sum(value > 0 for value in stress_quarters) >= 3,
            sum(value > 0 for value in stress_quarters),
            ">= 3",
        ),
        "bootstrap_excess_lower_95_positive": _check(
            bootstrap.lower_95 > 0,
            bootstrap.lower_95,
            "> 0",
        ),
        "at_least_twelve_non_final_closed_trades": _check(
            nested_trade["non_final_trade_count"] >= 12,
            nested_trade["non_final_trade_count"],
            ">= 12",
        ),
        "single_positive_trade_contribution_at_most_50pct": _check(
            nested_trade["max_positive_trade_contribution"] <= 0.50,
            nested_trade["max_positive_trade_contribution"],
            "<= 0.50",
        ),
    }
    all_gates_pass = all(item["passed"] for item in checks.values())
    tail = tuple(candle for candle in selected if candle.timestamp > HISTORY_END)
    wave3_forward = tuple(
        candle for candle in selected if candle.timestamp >= WAVE3_FORWARD_START
    )

    return {
        "generated_at": generated_at.isoformat(),
        "market": "KRW-BTC",
        "mode": "two_persona_adversarial_spot_research",
        "historical_data_reused": True,
        "dataset": {
            **_manifest(selected),
            "wave4_forward_sample_count": 0,
            "wave4_forward_sample_status": "none",
        },
        "historical_prefix": {**_manifest(history), "historical_data_reused": True},
        "data_quality": _data_quality(selected),
        "candidate_manifest": {
            **manifest,
            "candidate_count": len(manifest["candidates"]),
            "sha256": manifest_hash,
        },
        "validation": {
            "outer": {
                "method": "expanding continuous OOS",
                "initial_train_candles_30m": 19_200,
                "test_candles_30m": 2_400,
                "fold_count": 8,
                "expanding": True,
            },
            "inner": {
                "method": "expanding train-only fit anchored at outer train end",
                "initial_train_candles_30m": 12_000,
                "test_candles_30m": 1_200,
                "fold_count": 6,
                "expanding": True,
            },
            "allow_short": False,
            "signal_fill_contract": "completed close signal, next 30m open fill",
            "oos_tuning": False,
        },
        "costs": {
            "base_fee_rate_per_fill": base_settings.fee_rate,
            "base_slippage_bps_per_fill": base_settings.slippage_bps,
            "stress_fee_rate_per_fill": stress_settings.fee_rate,
            "stress_slippage_bps_per_fill": stress_settings.slippage_bps,
        },
        "candidates": _comparison_rows(fixed_base, fixed_stress),
        "controls": {
            "previous_best": {
                "name": PREVIOUS_BEST_CANDIDATE,
                "walk_forward": _report(previous_base),
                "double_cost_stress": _report(previous_stress),
            },
            "buy_hold": {
                "name": "buy_and_hold_long",
                "walk_forward": _report(buy_hold_base),
                "double_cost_stress": _report(buy_hold_stress),
            },
        },
        "nested_selection": {
            "rule": "inner base/stress positive and at least 4/6 stress folds positive; rank stress return, stress drawdown, name; else cash",
            "decisions": [_decision_payload(value) for value in nested.decisions],
            "walk_forward": _report(nested.base),
            "double_cost_stress": _report(nested.stress),
            "stress_quarter_returns": stress_quarters,
            "cash_fold_count": sum(value.selected_candidate is None for value in nested.decisions),
        },
        "bootstrap": {
            "comparison": "nested selector minus previous best KST daily log returns",
            "method": "kst_daily_moving_block",
            "block_days": bootstrap.block_days,
            "iterations": bootstrap.iterations,
            "seed": bootstrap.seed,
            "point_estimate": bootstrap.point_estimate,
            "lower_95": bootstrap.lower_95,
            "median": bootstrap.median,
            "upper_95": bootstrap.upper_95,
            "probability_excess_gt_zero": bootstrap.probability_positive,
        },
        "gate_evaluation": {
            "checks": checks,
            "overall_pass": all_gates_pass,
            "family_independence_count": 2,
            "preferred_non_final_trade_count": 20,
        },
        "wave4_forward_sample": {
            "freeze_at": WAVE4_FREEZE_AT.isoformat(),
            "observed_at": OBSERVED_AT.isoformat(),
            "candle_count_30m": 0,
            "prospective": True,
            "sample_sufficient": False,
            "reason": "the deterministic input cutoff precedes candidate freeze",
        },
        "prior_wave3_prospective_update": {
            "period": [
                wave3_forward[0].timestamp.isoformat() if wave3_forward else None,
                wave3_forward[-1].timestamp.isoformat() if wave3_forward else None,
            ],
            "candle_count_30m": len(wave3_forward),
            "sha256": _manifest(wave3_forward)["sha256"] if wave3_forward else None,
            "frozen_policy_action": "cash",
            "policy_return": 0.0,
            "sample_sufficient": False,
        },
        "adaptive_tail": {
            "candle_count_30m": len(tail),
            "sha256": _manifest(tail)["sha256"] if tail else None,
            "prospective_for_wave4": False,
        },
        "selection": {
            "status": "RESEARCH_ONLY",
            "selected_candidate": None,
            "paper_or_live_strategy_changed": False,
            "can_promote": False,
            "reason": (
                "all historical gates passed but the repeatedly observed history cannot promote a strategy"
                if all_gates_pass
                else "the adversarial historical gates rejected the Wave 4 policy"
            ),
        },
        "limitations": [
            "The 40000-candle historical prefix has been repeatedly observed and is adaptive evidence.",
            "The Wave 4 input cutoff precedes candidate freeze, so there is no Wave 4 forward sample.",
            "Candidates 1 and 2 are one time-series-momentum family, not independent confirmations.",
            "OHLCV cannot reconstruct order-book impact, partial fills, or intrabar ordering.",
        ],
        "warning": "Backtests are research evidence, not a profit guarantee or live-trading approval.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generated-at", type=_parse_aware, required=True)
    args = parser.parse_args()
    payload = build_report(load_candles_csv(args.input), generated_at=args.generated_at)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
