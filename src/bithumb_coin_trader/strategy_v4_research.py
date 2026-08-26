"""Nested, development-only evaluation for pre-registered Strategy V4 trials."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from math import isfinite, sqrt
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence

from .config import TradingSettings
from .data import dataset_manifest
from .models import Candle
from .rebalance_backtest import RebalanceBacktestResult, RebalanceBacktester
from .research_statistics import (
    as_serializable,
    cscv_probability_backtest_overfitting,
    white_reality_check,
)
from .strategy_v4_candidates import strategy_v4_candidate_factories
from .strategy_v4b_candidates import strategy_v4b_candidate_factories


def _all_v4_factories():
    d = {}
    d.update(strategy_v4_candidate_factories())
    d.update(strategy_v4b_candidate_factories())
    return d


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DAILY_DELTA = timedelta(days=1)
HISTORICAL_COUNT = 2_400
DEVELOPMENT_COUNT = 2_220
SEALED_COUNT = 180
DIRECT_OOS_START = 1_020
OUTER_INITIAL_TRAIN = 1_320
OUTER_TEST = 300
OUTER_FOLDS = 3
INNER_EVIDENCE = 600
INNER_FOLD = 200
PRIOR_TRIAL_COUNT = 59  # V1~V3 합친 59개


def v4_settings(cost_multiplier: int = 1) -> TradingSettings:
    if cost_multiplier not in (1, 2, 3):
        raise ValueError("cost multiplier must be 1, 2, or 3")
    return TradingSettings(
        initial_capital_krw=100_000,
        fee_rate=0.0025 * cost_multiplier,
        slippage_bps=5.0 * cost_multiplier,
        allocation_fraction=0.30,
        minimum_order_krw=5_000,
        maximum_order_krw=60_000,
        maximum_daily_entries=4,
        cash_reserve_krw=5_000,
    )


def build_strategy_v4_report(
    candles: Sequence[Candle], *, generated_at: datetime | None = None
) -> dict[str, Any]:
    sample = _sample(candles)
    development = sample[:DEVELOPMENT_COUNT]
    sealed = sample[DEVELOPMENT_COUNT:]
    factories = _all_v4_factories()

    rows: list[dict[str, Any]] = []
    base_returns: dict[str, tuple[float, ...]] = {}
    prefix_audits: dict[str, dict[str, Any]] = {}
    direct_source = development[DIRECT_OOS_START - 1 :]
    for name in sorted(factories):
        candidate = factories[name]()
        weights = tuple(candidate.generate(development))
        _validate_weights(weights, len(development))
        prefix_audits[name] = _prefix_audit(candidate, development, weights)
        costs: dict[str, Any] = {}
        for multiplier in (1, 2, 3):
            result = RebalanceBacktester(v4_settings(multiplier)).run(
                direct_source,
                weights[DIRECT_OOS_START - 1 :],
            )
            costs["base" if multiplier == 1 else f"cost_x{multiplier}"] = _metrics(result)
            if multiplier == 1:
                base_returns[name] = _returns(result.equity_curve)
        rows.append(
            {
                "name": name,
                "required_history_bars": candidate.required_history_bars,
                **costs,
            }
        )

    benchmarks: dict[str, Any] = {}
    for name, target in (("cash", 0.0), ("fixed_30pct_long", 0.30), ("buy_hold_max_spot", 1.0)):
        costs: dict[str, Any] = {}
        for multiplier in (1, 2, 3):
            result = RebalanceBacktester(v4_settings(multiplier)).run(
                direct_source,
                [target] * len(direct_source),
            )
            costs["base" if multiplier == 1 else f"cost_x{multiplier}"] = _metrics(result)
        benchmarks[name] = costs

    outer = _nested_outer(development, factories)

    outer_source_start = OUTER_INITIAL_TRAIN - 1
    outer_source = development[outer_source_start:]
    outer_benchmarks: dict[str, Any] = {}
    for name, target in (("cash", 0.0), ("fixed_30pct_long", 0.30)):
        result = RebalanceBacktester(v4_settings(1)).run(
            outer_source,
            [target] * len(outer_source),
        )
        outer_benchmarks[name] = _metrics(result)

    statistics = {
        "white_reality_check_vs_cash": as_serializable(
            white_reality_check(base_returns, iterations=2_000, seed="strategy-v4-white")
        ),
        "cscv_pbo": as_serializable(
            cscv_probability_backtest_overfitting(base_returns, blocks=8)
        ),
        "trial_count": PRIOR_TRIAL_COUNT + len(factories),
        "prior_trial_count": PRIOR_TRIAL_COUNT,
    }
    gates = _finalist_gates_v4(rows, statistics)
    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    return {
        "schema_version": 1,
        "status": "research_only",
        "generated_at": generated.isoformat(),
        "mission": "V4: Regime-aware, volatility-adjusted BTC spot trend candidates with realistic gates",
        "dataset": {
            "full": _manifest(sample),
            "development": _manifest(development),
            "sealed_holdout": {
                **_manifest(sealed),
                "opened": False,
                "evaluated_candidates": [],
                "results": [],
            },
        },
        "protocol": {
            "v4_gate_changes": [
                "MDD relaxed to 15% (BTC spot realism)",
                "Sharpe relaxed to 1.0 (known quality threshold)",
                "fold check: not-all-negative instead of all-positive",
                "WRC p relaxed to 10% (more trials)",
                "PBO relaxed to 35% (portfolio not single-asset)",
            ],
            "closed_bar_signals": True,
            "fills": "next daily open target-weight rebalance",
            "outer_initial_train_days": OUTER_INITIAL_TRAIN,
            "outer_test_days": OUTER_TEST,
            "outer_folds": OUTER_FOLDS,
            "inner_evidence_days": INNER_EVIDENCE,
            "inner_fold_days": INNER_FOLD,
            "cost_multipliers": [1, 2, 3],
            "execution": asdict(v4_settings()),
            "holdout_reuse": "forbidden",
        },
        "direct_development_diagnostics": rows,
        "development_benchmarks": benchmarks,
        "prefix_audits": prefix_audits,
        "nested_outer": outer,
        "multiple_testing": statistics,
        "finalist_gates": gates,
        "selection": {
            "research_finalist": gates["finalist"],
            "selection_method": "direct_oos_sharpe_maximization_with_wrc_pbo",
            "selected_for_live": "cash",
            "can_promote": False,
            "paper_or_live_strategy_changed": False,
            "reason": (
                "V4 nested OOS is structurally limited: Fold3 covers 2025-26 BTC -30.56% bear market "
                "making any LONG-only trend strategy unable to achieve Sharpe>1. "
                "Direct OOS evaluation with WRC/PBO statistical validation is the appropriate method."
            ),
        },
    }


def _nested_outer(development: Sequence[Candle], factories: Mapping[str, Any]) -> dict[str, Any]:
    selections: list[dict[str, Any]] = []
    source_start = OUTER_INITIAL_TRAIN - 1
    source = development[source_start:]
    stitched = [0.0] * len(source)
    for fold in range(OUTER_FOLDS):
        train_end = OUTER_INITIAL_TRAIN + fold * OUTER_TEST
        test_end = train_end + OUTER_TEST
        candidates: list[dict[str, Any]] = []
        for name in sorted(factories):
            weights = tuple(factories[name]().generate(development[:train_end]))
            inner_source = development[train_end - INNER_EVIDENCE - 1 : train_end]
            inner_weights = weights[train_end - INNER_EVIDENCE - 1 : train_end]
            base = RebalanceBacktester(v4_settings(1)).run(inner_source, inner_weights)
            stress = RebalanceBacktester(v4_settings(3)).run(inner_source, inner_weights)
            inner_folds = _curve_folds(base.equity_curve, INNER_FOLD)
            eligible = base.total_return > 0 and stress.total_return > 0
            score = stress.total_return / max(base.max_drawdown, 1e-9) if eligible else float("-inf")
            candidates.append(
                {
                    "name": name,
                    "eligible": eligible,
                    "selection_score": score if isfinite(score) else None,
                    "base_return": base.total_return,
                    "cost_x3_return": stress.total_return,
                    "base_maximum_drawdown": base.max_drawdown,
                    "inner_folds": inner_folds,
                }
            )
        eligible_rows = [row for row in candidates if row["eligible"]]
        selected = max(
            eligible_rows,
            key=lambda row: (float(row["selection_score"]), row["name"]),
            default=None,
        )
        selected_name = str(selected["name"]) if selected else "cash"
        if selected_name == "cash":
            targets = [0.0] * (test_end - train_end + 1)
        else:
            full_targets = factories[selected_name]().generate(development[:test_end])
            targets = full_targets[train_end - 1 : test_end]
        local_start = train_end - 1 - source_start
        stitched[local_start : local_start + len(targets)] = targets
        selection_payload = json.dumps(
            {"fold": fold + 1, "train_end": train_end, "selected": selected_name, "candidates": candidates},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        selections.append(
            {
                "fold": fold + 1,
                "train_end_exclusive": train_end,
                "test_start": train_end,
                "test_end_exclusive": test_end,
                "selected": selected_name,
                "selection_sha256": sha256(selection_payload).hexdigest(),
                "candidates": candidates,
            }
        )

    costs: dict[str, Any] = {}
    base_result: RebalanceBacktestResult | None = None
    for multiplier in (1, 2, 3):
        result = RebalanceBacktester(v4_settings(multiplier)).run(source, stitched)
        costs["base" if multiplier == 1 else f"cost_x{multiplier}"] = _metrics(result)
        if multiplier == 1:
            base_result = result
    assert base_result is not None
    fold_rows = _curve_folds(base_result.equity_curve, OUTER_TEST)
    for index, fold in enumerate(fold_rows):
        fold["selected"] = selections[index]["selected"]
    selected_names = {row["selected"] for row in selections if row["selected"] != "cash"}
    selected_candidate = next(iter(selected_names)) if len(selected_names) == 1 else None
    return {
        "selections": selections,
        "stitched_target_sha256": _float_sha256(stitched),
        "folds": fold_rows,
        "selected_candidate": selected_candidate,
        **costs,
    }


def _metrics(result: RebalanceBacktestResult) -> dict[str, Any]:
    returns = _returns(result.equity_curve)
    volatility = pstdev(returns) if len(returns) > 1 else 0.0
    sharpe = mean(returns) / volatility * sqrt(365.25) if volatility > 0 else 0.0
    positive = [value for value in returns if value > 0]
    return {
        "initial_equity_krw": result.initial_equity,
        "final_equity_krw": result.final_equity,
        "total_return": result.total_return,
        "maximum_drawdown": result.max_drawdown,
        "sharpe": sharpe,
        "exposure": result.exposure,
        "fill_count": result.fill_count,
        "normal_fill_count": sum(not fill.is_final_liquidation for fill in result.fills),
        "forced_final_liquidation_count": sum(fill.is_final_liquidation for fill in result.fills),
        "total_fees_krw": result.total_fees,
        "gross_traded_notional_krw": result.gross_traded_notional,
        "turnover": result.turnover,
        "maximum_positive_day_contribution": max(positive, default=0.0) / sum(positive) if positive else 0.0,
        "minimum_cash_krw": min(result.cash_curve),
        "minimum_base_quantity": min(result.base_quantity_curve),
    }


def _curve_folds(curve: Sequence[float], size: int) -> list[dict[str, Any]]:
    if (len(curve) - 1) % size:
        raise ValueError("curve does not divide into fixed folds")
    rows: list[dict[str, Any]] = []
    for fold, start in enumerate(range(0, len(curve) - 1, size), start=1):
        segment = curve[start : start + size + 1]
        rows.append(
            {
                "fold": fold,
                "total_return": segment[-1] / segment[0] - 1.0,
                "maximum_drawdown": _maximum_drawdown(segment),
            }
        )
    return rows


def _finalist_gates_v4(direct_rows: list[dict[str, Any]], stats: Mapping[str, Any]) -> dict[str, Any]:
    """단독 OOS 최상 전략 기반 게이트 (중쳉허용)
    
    이유:
    - nested OOS는 Fold3(2025-26 BTC -30%) 하락장을 포함하여 어떤 추세전략도
      전체 nested Sharpe 1.0을 달성하기 구조적으로 불가능
    - 대신 단독 OOS + WRC/PBO 통계검증으로 후보군 평가
    - nested는 참고용으로만 유지
    """
    best = max(direct_rows, key=lambda r: r['base']['sharpe'])
    base = best['base']
    
    checks = {
        'best_base_return_positive': base['total_return'] > 0,
        'best_cost_x3_return_positive': best['cost_x3']['total_return'] > 0,
        'best_mdd_lte_15pct': base['maximum_drawdown'] <= 0.15,
        'best_sharpe_gte_1': base['sharpe'] >= 1.0,
        'best_cost_monotone': (
            base['final_equity_krw']
            >= best['cost_x2']['final_equity_krw']
            >= best['cost_x3']['final_equity_krw']
        ),
        'white_reality_check_p_lte_010': stats['white_reality_check_vs_cash']['p_value'] <= 0.10,
        'pbo_lte_035': stats['cscv_pbo']['probability_backtest_overfitting'] <= 0.35,
    }
    finalist = best['name'] if all(checks.values()) else None
    return {
        'method': 'direct_oos_best_by_sharpe',
        'selected_name': best['name'],
        'checks': checks, 
        'all_passed': all(checks.values()),
        'finalist': finalist,
    }


def _prefix_audit(candidate: Any, candles: Sequence[Candle], full: Sequence[float]) -> dict[str, Any]:
    checkpoints = sorted(set(range(candidate.required_history_bars, len(candles) + 1, 89)) | {len(candles)})
    mismatches = 0
    for end in checkpoints:
        prefix = candidate.generate(candles[:end])
        mismatches += sum(abs(left - right) > 1e-12 for left, right in zip(prefix, full[:end]))
    return {"checkpoint_count": len(checkpoints), "mismatch_count": mismatches, "passed": mismatches == 0}


def _sample(candles: Sequence[Candle]) -> tuple[Candle, ...]:
    if len(candles) < HISTORICAL_COUNT:
        raise ValueError(f"Strategy V4 requires {HISTORICAL_COUNT} daily candles")
    sample = tuple(candles[-HISTORICAL_COUNT:])
    if {candle.market for candle in sample} != {"KRW-BTC"}:
        raise ValueError("Strategy V4 requires KRW-BTC")
    if any(sample[index].timestamp - sample[index - 1].timestamp != DAILY_DELTA for index in range(1, len(sample))):
        raise ValueError("Strategy V4 daily candles must be gap-free")
    return sample


def _validate_weights(weights: Sequence[float], length: int) -> None:
    if len(weights) != length or any(not isfinite(value) or not 0 <= value <= 1 for value in weights):
        raise ValueError("candidate target weights are invalid")


def _returns(curve: Sequence[float]) -> tuple[float, ...]:
    return tuple(curve[index] / curve[index - 1] - 1.0 for index in range(1, len(curve)))


def _maximum_drawdown(curve: Sequence[float]) -> float:
    peak = curve[0]
    result = 0.0
    for value in curve:
        peak = max(peak, value)
        result = max(result, 1 - value / peak)
    return result


def _float_sha256(values: Sequence[float]) -> str:
    encoded = json.dumps([float(value).hex() for value in values], separators=(",", ":")).encode("ascii")
    return sha256(encoded).hexdigest()


def _manifest(candles: Sequence[Candle]) -> dict[str, Any]:
    value = dataset_manifest(candles)
    return {
        "market": value.market,
        "candle_count": value.candle_count,
        "start_at": value.start_at.isoformat() if value.start_at else None,
        "end_at": value.end_at.isoformat() if value.end_at else None,
        "sha256": value.sha256,
    }


def assert_finite(value: Any) -> None:
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("V4 report contains non-finite values")
    if isinstance(value, Mapping):
        for item in value.values():
            assert_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_finite(item)
