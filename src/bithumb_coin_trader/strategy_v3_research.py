"""Nested, development-only evaluation for pre-registered Strategy V3 trials."""

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
from .strategy_v3_candidates import strategy_v3_candidate_factories


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
PRIOR_TRIAL_COUNT = 56
PREREGISTRATION = REPOSITORY_ROOT / "docs/STRATEGY_V3_PREREGISTRATION_2026-08-25.md"
TRIAL_PLAN = REPOSITORY_ROOT / ".omx/specs/autoresearch-strategy-v3/trial-plan.json"


def v3_settings(cost_multiplier: int = 1) -> TradingSettings:
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


def build_strategy_v3_report(
    candles: Sequence[Candle], *, generated_at: datetime | None = None
) -> dict[str, Any]:
    sample = _sample(candles)
    development = sample[:DEVELOPMENT_COUNT]
    sealed = sample[DEVELOPMENT_COUNT:]
    factories = strategy_v3_candidate_factories()
    plan = _load_plan()
    if sorted(plan["candidate_names"]) != sorted(factories):
        raise ValueError("trial plan candidate names differ from frozen registry")
    if plan["preregistration_sha256"] != sha256(PREREGISTRATION.read_bytes()).hexdigest():
        raise ValueError("pre-registration changed after the trial plan was recorded")
    trial_ids = {
        str(name): int(trial_id)
        for name, trial_id in zip(plan["candidate_names"], plan["trial_ids"], strict=True)
    }

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
            result = RebalanceBacktester(v3_settings(multiplier)).run(
                direct_source,
                weights[DIRECT_OOS_START - 1 :],
            )
            costs["base" if multiplier == 1 else f"cost_x{multiplier}"] = _metrics(result)
            if multiplier == 1:
                base_returns[name] = _returns(result.equity_curve)
        rows.append(
            {
                "name": name,
                "trial_id": trial_ids[name],
                "required_history_bars": candidate.required_history_bars,
                **costs,
            }
        )

    benchmarks: dict[str, Any] = {}
    for name, target in (("cash", 0.0), ("fixed_30pct_long", 0.30), ("buy_hold_max_spot", 1.0)):
        costs: dict[str, Any] = {}
        for multiplier in (1, 2, 3):
            result = RebalanceBacktester(v3_settings(multiplier)).run(
                direct_source,
                [target] * len(direct_source),
            )
            costs["base" if multiplier == 1 else f"cost_x{multiplier}"] = _metrics(result)
        benchmarks[name] = costs

    outer = _nested_outer(development, factories)
    statistics = {
        "white_reality_check_vs_cash": as_serializable(
            white_reality_check(base_returns, iterations=2_000, seed="strategy-v3-white")
        ),
        "cscv_pbo": as_serializable(
            cscv_probability_backtest_overfitting(base_returns, blocks=8)
        ),
        "trial_count": PRIOR_TRIAL_COUNT + len(factories),
        "prior_trial_count": PRIOR_TRIAL_COUNT,
        "deflated_sharpe": {
            "status": "unavailable",
            "reason": "The prior 56 trials do not have an immutable aligned return/Sharpe ledger.",
        },
    }
    gates = _finalist_gates(outer, statistics)
    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    return {
        "schema_version": 1,
        "status": "research_only",
        "generated_at": generated.isoformat(),
        "mission": "Test structurally different, pre-registered BTC trend/risk-allocation candidates with nested chronological selection.",
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
        "pre_registration": {
            "path": str(PREREGISTRATION.relative_to(REPOSITORY_ROOT)),
            "sha256": sha256(PREREGISTRATION.read_bytes()).hexdigest(),
            "trial_plan_path": str(TRIAL_PLAN.relative_to(REPOSITORY_ROOT)),
            "trial_plan_sha256": sha256(TRIAL_PLAN.read_bytes()).hexdigest(),
            "planned_at": plan["planned_at"],
            "trial_ids": plan["trial_ids"],
        },
        "protocol": {
            "closed_bar_signals": True,
            "fills": "next daily open target-weight rebalance",
            "outer_initial_train_days": OUTER_INITIAL_TRAIN,
            "outer_test_days": OUTER_TEST,
            "outer_folds": OUTER_FOLDS,
            "inner_evidence_days": INNER_EVIDENCE,
            "inner_fold_days": INNER_FOLD,
            "cost_multipliers": [1, 2, 3],
            "execution": asdict(v3_settings()),
            "holdout_reuse": "forbidden",
        },
        "source_manifest": _source_manifest(factories),
        "direct_development_diagnostics": rows,
        "development_benchmarks": benchmarks,
        "prefix_audits": prefix_audits,
        "nested_outer": outer,
        "multiple_testing": statistics,
        "finalist_gates": gates,
        "selection": {
            "research_finalist": outer["selected_candidate"] if gates["all_passed"] else None,
            "selected_for_live": "cash",
            "can_promote": False,
            "paper_or_live_strategy_changed": False,
            "reason": "V3 uses reused development data and the prior trial return ledger is incomplete; prospective evidence is required.",
        },
        "future_data_lanes": {
            "multi_asset": "collect point-in-time market/listing/warning snapshots before testing",
            "orderbook": "collect at least 30 days of raw orderbook/trade observations before calibrating a veto or fill model",
            "funding": "reference-only risk cap after a separately timestamped shadow study",
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
            base = RebalanceBacktester(v3_settings(1)).run(inner_source, inner_weights)
            stress = RebalanceBacktester(v3_settings(3)).run(inner_source, inner_weights)
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
                    "inner_folds": _curve_folds(base.equity_curve, INNER_FOLD),
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
        result = RebalanceBacktester(v3_settings(multiplier)).run(source, stitched)
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


def _finalist_gates(outer: Mapping[str, Any], stats: Mapping[str, Any]) -> dict[str, Any]:
    base = outer["base"]
    checks = {
        "base_return_positive": base["total_return"] > 0,
        "cost_x2_return_positive": outer["cost_x2"]["total_return"] > 0,
        "cost_x3_return_positive": outer["cost_x3"]["total_return"] > 0,
        "maximum_drawdown_lte_10pct": base["maximum_drawdown"] <= 0.10,
        "sharpe_gte_1_166608": base["sharpe"] >= 1.166608,
        "all_outer_folds_positive": all(fold["total_return"] > 0 for fold in outer["folds"]),
        "cost_monotone": base["final_equity_krw"] >= outer["cost_x2"]["final_equity_krw"] >= outer["cost_x3"]["final_equity_krw"],
        "cash_and_base_nonnegative": base["minimum_cash_krw"] >= 5_000 - 1e-6 and base["minimum_base_quantity"] >= 0,
        "white_reality_check_p_lte_005": stats["white_reality_check_vs_cash"]["p_value"] <= 0.05,
        "pbo_lte_025": stats["cscv_pbo"]["probability_backtest_overfitting"] <= 0.25,
        "dsr_available_and_passing": False,
    }
    return {"checks": checks, "all_passed": all(checks.values())}


def _prefix_audit(candidate: Any, candles: Sequence[Candle], full: Sequence[float]) -> dict[str, Any]:
    checkpoints = sorted(set(range(candidate.required_history_bars, len(candles) + 1, 89)) | {len(candles)})
    mismatches = 0
    for end in checkpoints:
        prefix = candidate.generate(candles[:end])
        mismatches += sum(abs(left - right) > 1e-12 for left, right in zip(prefix, full[:end]))
    return {"checkpoint_count": len(checkpoints), "mismatch_count": mismatches, "passed": mismatches == 0}


def _sample(candles: Sequence[Candle]) -> tuple[Candle, ...]:
    if len(candles) < HISTORICAL_COUNT:
        raise ValueError("Strategy V3 requires 2,400 daily candles")
    sample = tuple(candles[-HISTORICAL_COUNT:])
    if {candle.market for candle in sample} != {"KRW-BTC"}:
        raise ValueError("Strategy V3 requires KRW-BTC")
    if any(sample[index].timestamp - sample[index - 1].timestamp != DAILY_DELTA for index in range(1, len(sample))):
        raise ValueError("Strategy V3 daily candles must be gap-free")
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


def _load_plan() -> Mapping[str, Any]:
    value = json.loads(TRIAL_PLAN.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping) or value.get("state") != "planned":
        raise ValueError("Strategy V3 trial plan is invalid")
    return value


def _source_manifest(factories: Mapping[str, Any]) -> dict[str, Any]:
    paths = (
        Path(__file__),
        REPOSITORY_ROOT / "src/bithumb_coin_trader/rebalance_backtest.py",
        REPOSITORY_ROOT / "src/bithumb_coin_trader/strategy_v3_candidates.py",
        REPOSITORY_ROOT / "src/bithumb_coin_trader/research_statistics.py",
        REPOSITORY_ROOT / "src/bithumb_coin_trader/config.py",
        REPOSITORY_ROOT / "src/bithumb_coin_trader/data.py",
        REPOSITORY_ROOT / "src/bithumb_coin_trader/models.py",
        REPOSITORY_ROOT / "scripts/run_strategy_v3_research.py",
        REPOSITORY_ROOT / "scripts/validate_strategy_v3_research.py",
        PREREGISTRATION,
        TRIAL_PLAN,
    )
    files = [
        {"path": str(path.relative_to(REPOSITORY_ROOT)), "sha256": sha256(path.read_bytes()).hexdigest()}
        for path in paths
    ]
    payload = {"candidates": sorted(factories), "files": files, "settings": asdict(v3_settings())}
    return {**payload, "sha256": sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def assert_finite(value: Any) -> None:
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("V3 report contains non-finite values")
    if isinstance(value, Mapping):
        for item in value.values():
            assert_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_finite(item)
