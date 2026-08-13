#!/usr/bin/env python3
"""Generate the frozen 2026-08-13 KRW-BTC Wave 3 research artifact."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from bithumb_coin_trader.backtest import BacktestResult, Backtester
from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.data import dataset_manifest, load_candles_csv
from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.research import CandidateComparisonReport, ProjectResearchReport
from bithumb_coin_trader.wave3 import (
    PREVIOUS_BEST_CANDIDATE,
    NestedWalkForwardConfig,
    assert_wave3_cost_settings_match_manifest,
    deterministic_daily_moving_block_bootstrap,
    fixed_control_comparison,
    fixed_wave3_comparison,
    run_wave3_nested_research,
    select_nested_candidate,
    wave3_candidate_factories,
    wave3_candidate_manifest,
    wave3_candidate_manifest_hash,
)


EXPECTED_DATASET_SHA256 = (
    "b8f7217eb30c9b2b55e5b0462e40d826c8c83a057e2e548fd928951156e03e07"
)
EXPECTED_HISTORY_SHA256 = (
    "dc3537c862bc54efebfd215807e2ab57da66396ebfbfcf3d5a243327b9817248"
)
EXPECTED_MANIFEST_SHA256 = (
    "41afcddf791ced95f6e92751e45d8f71dacd94083d1ea5c516001407d179674a"
)
HISTORY_END = datetime(2026, 8, 12, 11, 0, tzinfo=UTC)
OBSERVED_AT = datetime(2026, 8, 13, 11, 30, tzinfo=UTC)


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


def _metric(
    result: BacktestResult, *, include_execution_evidence: bool = False
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "initial_equity_krw": round(result.initial_equity, 2),
        "final_equity_krw": round(result.final_equity, 2),
        "total_return": round(result.total_return, 8),
        "max_drawdown": round(result.max_drawdown, 8),
        "sharpe": round(result.sharpe, 6),
        "trade_count": result.trade_count,
        "win_rate": round(result.win_rate, 8),
        "exposure": round(result.exposure, 8),
    }
    if include_execution_evidence:
        payload.update(
            {
                "equity_curve_krw": list(result.equity_curve),
                "position_curve": [int(value) for value in result.position_curve],
                "trade_net_pnl_krw": [trade.net_pnl for trade in result.trades],
            }
        )
    return payload


def _report(report: ProjectResearchReport) -> dict[str, Any]:
    return {
        "fold_count": len(report.folds),
        "compounded_return": round(report.compounded_return, 8),
        "maximum_drawdown": round(report.maximum_drawdown, 8),
        "mean_sharpe": round(report.mean_sharpe, 6),
        "trade_count": report.trade_count,
        "weighted_win_rate": round(report.weighted_win_rate, 8),
        "profitable_folds": sum(
            fold.result.total_return > 0 for fold in report.folds
        ),
        "oos_equity_curve_krw": list(report.oos_equity_curve),
        "folds": [
            {
                "fold": fold.fold + 1,
                "train": [fold.train_start, fold.train_end],
                "test": [fold.test_start, fold.test_end],
                **_metric(fold.result),
            }
            for fold in report.folds
        ],
    }


def _comparison_rows(
    base: CandidateComparisonReport,
    stress: CandidateComparisonReport,
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
    rows.sort(key=lambda row: row["walk_forward"]["compounded_return"], reverse=True)
    return rows


def _find_report(
    comparison: CandidateComparisonReport, candidate_name: str
) -> ProjectResearchReport:
    return next(
        report
        for report in comparison.candidates
        if report.candidate_name == candidate_name
    )


def _decision_payload(decision: Any) -> dict[str, Any]:
    rows = []
    eligible = []
    for score in decision.candidate_scores:
        if score.qualifies:
            eligible.append(score.candidate_name)
        rows.append(
            {
                "name": score.candidate_name,
                "base": {
                    "compounded_return": round(score.base_compounded_return, 8),
                    "maximum_drawdown": round(score.base_maximum_drawdown, 8),
                    "profitable_folds": sum(value > 0 for value in score.base_fold_returns),
                    "fold_count": len(score.base_fold_returns),
                    "fold_returns": [round(value, 8) for value in score.base_fold_returns],
                },
                "stress": {
                    "compounded_return": round(score.stress_compounded_return, 8),
                    "maximum_drawdown": round(score.stress_maximum_drawdown, 8),
                    "profitable_folds": score.profitable_stress_fold_count,
                    "fold_count": len(score.stress_fold_returns),
                    "fold_returns": [round(value, 8) for value in score.stress_fold_returns],
                },
                "eligible": score.qualifies,
            }
        )
    return {
        "fold": decision.fold + 1,
        "train": [decision.train_start, decision.train_end],
        "test": [decision.test_start, decision.test_end],
        "inner_candidates": rows,
        "eligible_candidates": eligible,
        "selected_candidate": decision.selected_candidate or "cash",
    }


def _forward_metric(
    history: Sequence[Candle],
    shadow: Sequence[Candle],
    factory: Callable[[], Any] | None,
    settings: TradingSettings,
) -> dict[str, Any]:
    context = history
    execution_candles = [context[-1], *shadow]
    if factory is None:
        signals = [Signal.FLAT] * len(execution_candles)
    else:
        combined = [*context, *shadow]
        generated = factory().generate(combined)
        if len(generated) != len(combined):
            raise ValueError("forward strategy returned the wrong signal count")
        signals = [Signal(value) for value in generated[len(context) - 1 :]]
    return _metric(
        Backtester(settings, allow_short=False).run(execution_candles, signals),
        include_execution_evidence=True,
    )


def _subperiod_returns(curve: Sequence[float]) -> list[float]:
    period_count = 4
    periods = len(curve) - 1
    if periods <= 0 or periods % period_count:
        raise ValueError("OOS curve cannot be split into four equal subperiods")
    width = periods // period_count
    return [
        curve[(index + 1) * width] / curve[index * width] - 1.0
        for index in range(period_count)
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
        "gap_policy": "never forward-fill; omit incomplete aggregate buckets",
    }


def build_report(candles: Sequence[Candle], *, generated_at: datetime) -> dict[str, Any]:
    if len(candles) != 40_048:
        raise ValueError("Wave 3 input must contain exactly 40048 candles")
    if any(candle.market != "KRW-BTC" for candle in candles):
        raise ValueError("Wave 3 input must contain only KRW-BTC candles")
    if any(
        candles[index].timestamp <= candles[index - 1].timestamp
        for index in range(1, len(candles))
    ):
        raise ValueError("Wave 3 candles must be strictly chronological")
    if candles[-1].timestamp + timedelta(minutes=30) > OBSERVED_AT:
        raise ValueError("Wave 3 input contains an incomplete candle")

    history = tuple(candle for candle in candles if candle.timestamp <= HISTORY_END)
    shadow = tuple(candle for candle in candles if candle.timestamp > HISTORY_END)
    dataset = _manifest(candles)
    historical_prefix = _manifest(history)
    if dataset["sha256"] != EXPECTED_DATASET_SHA256:
        raise ValueError("combined dataset SHA-256 differs from the frozen input")
    if len(history) != 40_000 or historical_prefix["sha256"] != EXPECTED_HISTORY_SHA256:
        raise ValueError("historical prefix differs from the frozen 40000-candle input")
    if len(shadow) != 48:
        raise ValueError("post-hoc shadow must contain exactly 48 candles")

    manifest = wave3_candidate_manifest()
    manifest_hash = wave3_candidate_manifest_hash(manifest)
    if manifest_hash != EXPECTED_MANIFEST_SHA256:
        raise ValueError("candidate manifest differs from the frozen preregistration")

    base_settings = TradingSettings()
    stress_settings = TradingSettings(fee_rate=0.005, slippage_bps=10)
    assert_wave3_cost_settings_match_manifest(
        base_settings, stress_settings, manifest
    )
    fixed_base = fixed_wave3_comparison(history, settings=base_settings)
    fixed_stress = fixed_wave3_comparison(history, settings=stress_settings)
    control_base = fixed_control_comparison(history, settings=base_settings)
    control_stress = fixed_control_comparison(history, settings=stress_settings)
    nested = run_wave3_nested_research(
        history, base_settings=base_settings, stress_settings=stress_settings
    )

    previous_base = _find_report(control_base, PREVIOUS_BEST_CANDIDATE)
    previous_stress = _find_report(control_stress, PREVIOUS_BEST_CANDIDATE)
    buy_hold_base = _find_report(control_base, "buy_and_hold_long")
    buy_hold_stress = _find_report(control_stress, "buy_and_hold_long")
    execution_candles = history[19_199:38_400]
    bootstrap = deterministic_daily_moving_block_bootstrap(
        nested.base.oos_equity_curve,
        previous_base.oos_equity_curve,
        execution_candles,
    )
    subperiod_stress = _subperiod_returns(nested.stress.oos_equity_curve)

    final_decision = select_nested_candidate(
        history,
        candidate_factories=wave3_candidate_factories(),
        config=NestedWalkForwardConfig(),
        base_settings=base_settings,
        stress_settings=stress_settings,
        fold=8,
        train_start=0,
        train_end=40_000,
        test_start=40_000,
        test_end=40_048,
    )
    factories: Mapping[str, Callable[[], Any]] = wave3_candidate_factories()
    forward_rows = []
    for name, factory in factories.items():
        forward_rows.append(
            {
                "name": name,
                "base": _forward_metric(history, shadow, factory, base_settings),
                "double_cost_stress": _forward_metric(
                    history, shadow, factory, stress_settings
                ),
            }
        )
    selected_factory = (
        factories[final_decision.selected_candidate]
        if final_decision.selected_candidate is not None
        else None
    )
    forward_policy = {
        "action": final_decision.selected_candidate or "cash",
        "base": _forward_metric(history, shadow, selected_factory, base_settings),
        "double_cost_stress": _forward_metric(
            history, shadow, selected_factory, stress_settings
        ),
    }

    fixed_rows = _comparison_rows(fixed_base, fixed_stress)
    range_stress_positive = all(
        row["double_cost_stress"]["compounded_return"] > 0
        for row in fixed_rows
        if row["name"].startswith("trading_range_daily_50")
    )
    exposure_fraction = sum(
        fold.result.exposure * 2_400 for fold in nested.base.folds
    ) / 19_200
    exposure_days = exposure_fraction * 400.0
    sample_sufficient = nested.base.trade_count >= 30 or (
        nested.base.trade_count >= 12 and exposure_days >= 120
    )
    credibility_checks = {
        "nested_base_exceeds_previous_best_same_window": (
            nested.base.compounded_return > previous_base.compounded_return
        ),
        "nested_double_cost_stress_positive": nested.stress.compounded_return > 0,
        "at_least_five_profitable_outer_folds": sum(
            fold.result.total_return > 0 for fold in nested.base.folds
        )
        >= 5,
        "maximum_drawdown_at_most_10pct": nested.base.maximum_drawdown <= 0.10,
        "sample_sufficient": sample_sufficient,
        "three_of_four_stress_subperiods_positive": sum(
            value > 0 for value in subperiod_stress
        )
        >= 3,
        "bootstrap_excess_lower_95_positive": bootstrap.lower_95 > 0,
        "adjacent_trading_range_variants_stress_positive": range_stress_positive,
    }

    return {
        "generated_at": generated_at.isoformat(),
        "market": "KRW-BTC",
        "mode": "bithumb_spot_long_flat_research",
        "timeframe": "30m_execution_with_completed_higher_timeframe_signals",
        "historical_data_reused": True,
        "dataset": dataset,
        "historical_prefix": historical_prefix,
        "data_quality": _data_quality(candles),
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
                "method": "expanding continuous OOS candidate selection",
                "train_candles_30m": 12_000,
                "test_candles_30m": 1_200,
                "fold_count": 6,
                "expanding": True,
            },
            "signal_fill_contract": "completed close signal, next 30m open fill",
            "historical_remainder_candles": 1_600,
            "oos_tuning": False,
        },
        "costs": {
            "base_fee_rate_per_fill": base_settings.fee_rate,
            "base_slippage_bps_per_fill": base_settings.slippage_bps,
            "stress_fee_rate_per_fill": stress_settings.fee_rate,
            "stress_slippage_bps_per_fill": stress_settings.slippage_bps,
        },
        "fixed_candidates": fixed_rows,
        "controls": {
            "previous_best": {
                "name": PREVIOUS_BEST_CANDIDATE,
                "walk_forward": _report(previous_base),
                "double_cost_stress": _report(previous_stress),
            },
            "buy_hold": {
                "name": "buy_and_hold",
                "walk_forward": _report(buy_hold_base),
                "double_cost_stress": _report(buy_hold_stress),
            },
        },
        "nested_selection": {
            "rule": (
                "base and stress positive; at least four profitable stress folds; "
                "rank stress return, stress drawdown, name; otherwise cash"
            ),
            "decisions": [_decision_payload(value) for value in nested.decisions],
            "walk_forward": _report(nested.base),
            "double_cost_stress": _report(nested.stress),
            "cash_fold_count": sum(
                value.selected_candidate is None for value in nested.decisions
            ),
            "stress_subperiod_returns": [
                round(value, 8) for value in subperiod_stress
            ],
        },
        "bootstrap": {
            "comparison": "nested selector minus previous best KST daily log returns",
            "observation_count": bootstrap.observation_count,
            "iterations": bootstrap.iterations,
            "seed": bootstrap.seed,
            "block_days": bootstrap.block_days,
            "point_estimate": round(bootstrap.point_estimate, 8),
            "quantiles": {
                "lower_95": round(bootstrap.lower_95, 8),
                "median": round(bootstrap.median, 8),
                "upper_95": round(bootstrap.upper_95, 8),
            },
            "probability_excess_gt_zero": round(
                bootstrap.probability_positive, 8
            ),
        },
        "credibility": {
            "checks": credibility_checks,
            "credible_historical_improvement": all(credibility_checks.values()),
            "can_promote": False,
            "exposure_days_estimate": round(exposure_days, 4),
        },
        "posthoc_shadow": {
            "period": [shadow[0].timestamp.isoformat(), shadow[-1].timestamp.isoformat()],
            "candle_count_30m": len(shadow),
            "sha256": _manifest(shadow)["sha256"],
            "evidence_class": "posthoc_diagnostic",
            "prospective": False,
            "observed_before_manifest_freeze": True,
            "historical_selection": _decision_payload(final_decision),
            "frozen_policy": forward_policy,
            "candidate_diagnostics": forward_rows,
            "sample_sufficient_for_promotion": False,
            "selected_candidate": None,
            "promoted": False,
            "passed": False,
            "status": "INSUFFICIENT_SAMPLE",
        },
        "selection": {
            "status": "RESEARCH_ONLY",
            "selected_candidate": None,
            "paper_or_live_strategy_changed": False,
            "reason": (
                "the nested selector did not establish credible historical improvement, "
                "and the 48-bar post-hoc diagnostic is not prospective evidence"
            ),
        },
        "limitations": [
            "The 40000-candle historical prefix was already observed in earlier research.",
            "One market and one historical window cannot establish durable profitability.",
            "OHLCV bars cannot reproduce order-book impact or intrabar execution ordering.",
            "The 48-candle post-hoc shadow was observable before manifest freeze and is not prospective evidence.",
        ],
        "warning": "Backtests are research evidence, not a profit guarantee or live-trading approval.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generated-at", type=_parse_aware, required=True)
    args = parser.parse_args()
    payload = build_report(
        load_candles_csv(args.input), generated_at=args.generated_at
    )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
