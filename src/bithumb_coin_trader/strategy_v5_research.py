"""Nested, development-only evaluation engine for Strategy V5 research lane.

Features:
- Bear-aware nested CV evaluation (Fold 3 bear market realistic defense check)
- Cumulative trial ledger integration with Deflated Sharpe Ratio (DSR)
- White's Reality Check (WRC) & CSCV PBO multiple-testing diagnostics
- Cross-asset multi-coin synchronized portfolio simulation (Challenger B)
- 180-bar sealed holdout protection (never opened during research)
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import hashlib
import json
from math import isfinite, sqrt
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence

from .config import TradingSettings
from .daily_strategy_candidates import KST
from .data import dataset_manifest, load_candles_csv
from .models import Candle
from .rebalance_backtest import RebalanceBacktestResult, RebalanceBacktester
from .research_statistics import (
    as_serializable,
    cscv_probability_backtest_overfitting,
    white_reality_check,
)
from .strategy_v5_candidates import (
    V5CrossAssetDualMomentumStrategy,
    strategy_v5_candidate_factories,
)
from .trial_ledger import (
    TrialRecord,
    append_trial_record,
    calculate_ledger_dsr,
    load_trial_ledger,
)

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


def v5_settings(cost_multiplier: int = 1) -> TradingSettings:
    """Standard Bithumb spot trading settings with scalable cost multiplier."""
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


def build_strategy_v5_report(
    btc_candles: Sequence[Candle],
    *,
    eth_candles: Sequence[Candle] | None = None,
    xrp_candles: Sequence[Candle] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    sample = _sample(btc_candles)
    development = sample[:DEVELOPMENT_COUNT]
    sealed = sample[DEVELOPMENT_COUNT:]
    factories = strategy_v5_candidate_factories()

    # Direct OOS evaluations
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
            result = RebalanceBacktester(v5_settings(multiplier)).run(
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

    # Multi-asset cross-coin simulation for Challenger B
    multi_asset_results: dict[str, Any] = {}
    if eth_candles is not None and xrp_candles is not None:
        eth_dev = eth_candles[:DEVELOPMENT_COUNT]
        xrp_dev = xrp_candles[:DEVELOPMENT_COUNT]
        dual_strat = V5CrossAssetDualMomentumStrategy()
        multi_weights = dual_strat.generate_multi_asset(
            {"KRW-BTC": development, "KRW-ETH": eth_dev, "KRW-XRP": xrp_dev}
        )
        # Simulate composite return
        multi_asset_results = _simulate_multi_asset_portfolio(
            {"KRW-BTC": direct_source, "KRW-ETH": eth_dev[DIRECT_OOS_START - 1 :], "KRW-XRP": xrp_dev[DIRECT_OOS_START - 1 :],},
            {k: v[DIRECT_OOS_START - 1 :] for k, v in multi_weights.items()},
        )

    # Benchmarks
    benchmarks: dict[str, Any] = {}
    for name, target in (("cash", 0.0), ("fixed_30pct_long", 0.30), ("buy_hold_max_spot", 1.0)):
        costs: dict[str, Any] = {}
        for multiplier in (1, 2, 3):
            result = RebalanceBacktester(v5_settings(multiplier)).run(
                direct_source,
                [target] * len(direct_source),
            )
            costs["base" if multiplier == 1 else f"cost_x{multiplier}"] = _metrics(result)
        benchmarks[name] = costs

    # Bear-Aware Nested CV
    outer = _nested_outer_v5(development, factories)

    # Statistical Diagnostics
    existing_ledger = load_trial_ledger()
    wrc_result = white_reality_check(base_returns, iterations=2_000, seed="strategy-v5-reality")
    pbo_result = cscv_probability_backtest_overfitting(base_returns, blocks=8)

    # Calculate DSR for each candidate against full ledger
    best_candidate_row = max(rows, key=lambda r: r["base"]["sharpe"])
    best_name = best_candidate_row["name"]
    best_returns = base_returns[best_name]

    dsr_result = calculate_ledger_dsr(
        best_returns,
        candidate_sharpe=best_candidate_row["base"]["sharpe"],
        ledger_records=existing_ledger,
    )

    statistics = {
        "white_reality_check_vs_cash": as_serializable(wrc_result),
        "cscv_pbo": as_serializable(pbo_result),
        "deflated_sharpe_ratio": as_serializable(dsr_result),
        "cumulative_trial_count": len(existing_ledger) + len(factories),
        "prior_trial_count": len(existing_ledger),
    }

    gates = _finalist_gates_v5(outer, best_candidate_row, statistics)
    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)

    return {
        "schema_version": 1,
        "status": "research_only",
        "generated_at": generated.isoformat(),
        "mission": "V5: Regime-Adaptive Donchian, Cross-Asset Dual Momentum & Trend Pullback Evaluation",
        "dataset": {
            "full": _manifest(sample),
            "development": _manifest(development),
            "sealed_holdout": {
                "candle_count": len(sealed),
                "sha256": dataset_manifest(sealed).sha256 if sealed else None,
                "status": "SEALED_UNREAD",
            },
        },
        "direct_development_diagnostics": rows,
        "multi_asset_diagnostics": multi_asset_results,
        "development_benchmarks": benchmarks,
        "prefix_audits": prefix_audits,
        "nested_outer": outer,
        "multiple_testing": statistics,
        "finalist_gates": gates,
        "selection": {
            "champion": "v4_adaptive_donchian_atr",
            "research_finalist": gates["finalist"],
            "selected_for_live": "cash",
            "can_promote": False,
            "paper_or_live_strategy_changed": False,
            "decision_rationale": gates["rationale"],
        },
    }


def _nested_outer_v5(
    development: Sequence[Candle],
    factories: Mapping[str, Any],
) -> dict[str, Any]:
    """Bear-aware Nested Out-of-Sample evaluation across 3 outer folds."""
    folds_detail: list[dict[str, Any]] = []
    stitched_weights: list[float] = []
    outer_source_start = OUTER_INITIAL_TRAIN
    outer_source = development[outer_source_start:]

    for fold_index in range(OUTER_FOLDS):
        train_end = OUTER_INITIAL_TRAIN + fold_index * OUTER_TEST
        test_start = train_end
        test_end = test_start + OUTER_TEST

        train_candles = development[:train_end]
        test_candles = development[test_start:test_end]

        # Benchmark performance during this fold test period
        btc_start_price = test_candles[0].close
        btc_end_price = test_candles[-1].close
        btc_fold_return = (btc_end_price / btc_start_price) - 1.0
        btc_prices = [c.close for c in test_candles]
        btc_peak = btc_prices[0]
        btc_mdd = 0.0
        for p in btc_prices:
            if p > btc_peak:
                btc_peak = p
            dd = (btc_peak - p) / btc_peak
            if dd > btc_mdd:
                btc_mdd = dd

        is_bear_fold = btc_fold_return <= -0.15

        # Inner CV Selection
        best_name = "cash"
        best_score = float("-inf")
        candidate_scores: list[dict[str, Any]] = []

        for name, factory in factories.items():
            candidate = factory()
            train_weights = tuple(candidate.generate(train_candles))
            inner_start = train_end - INNER_EVIDENCE
            inner_source = train_candles[inner_start:]
            inner_w = train_weights[inner_start:]

            res_base = RebalanceBacktester(v5_settings(1)).run(inner_source, inner_w)
            res_stress = RebalanceBacktester(v5_settings(3)).run(inner_source, inner_w)

            eligible = (res_base.total_return > 0 and res_stress.total_return > 0)
            calmar = res_stress.total_return / max(res_base.max_drawdown, 1e-9) if eligible else float("-inf")

            candidate_scores.append(
                {
                    "name": name,
                    "eligible": eligible,
                    "base_return": res_base.total_return,
                    "stress_return": res_stress.total_return,
                    "mdd": res_base.max_drawdown,
                    "score": calmar if eligible else None,
                }
            )
            if eligible and calmar > best_score:
                best_score = calmar
                best_name = name

        # Run test fold with selected candidate
        if best_name == "cash":
            fold_weights = [0.0] * len(test_candles)
        else:
            selected_cand = factories[best_name]()
            full_w = selected_cand.generate(development[:test_end])
            fold_weights = full_w[test_start:test_end]

        stitched_weights.extend(fold_weights)

        fold_res = RebalanceBacktester(v5_settings(1)).run(test_candles, fold_weights)
        fold_metrics = _metrics(fold_res)

        # Bear fold validation check
        if is_bear_fold:
            fold_passed = (fold_metrics["maximum_drawdown"] <= max(btc_mdd * 0.40, 0.05)) and (fold_metrics["total_return"] >= -0.05)
        else:
            fold_passed = fold_metrics["total_return"] > 0.0

        folds_detail.append(
            {
                "fold": fold_index + 1,
                "period_start": test_candles[0].timestamp.astimezone(KST).date().isoformat(),
                "period_end": test_candles[-1].timestamp.astimezone(KST).date().isoformat(),
                "btc_buy_hold_return": btc_fold_return,
                "btc_mdd": btc_mdd,
                "is_bear_fold": is_bear_fold,
                "selected_strategy": best_name,
                "fold_passed": fold_passed,
                "candidate_evaluations": candidate_scores,
                **fold_metrics,
            }
        )

    # Evaluate stitched curve across outer source
    stitched_costs: dict[str, Any] = {}
    for multiplier in (1, 2, 3):
        res = RebalanceBacktester(v5_settings(multiplier)).run(outer_source, stitched_weights)
        stitched_costs["base" if multiplier == 1 else f"cost_x{multiplier}"] = _metrics(res)

    return {
        "folds": folds_detail,
        "all_folds_passed": all(f["fold_passed"] for f in folds_detail),
        **stitched_costs,
    }


def _finalist_gates_v5(
    outer: Mapping[str, Any],
    best_candidate: Mapping[str, Any],
    stats: Mapping[str, Any],
) -> dict[str, Any]:
    """Pre-registered V5 gates combining Bear-aware Nested CV, Direct OOS & DSR."""
    nested_base = outer["base"]
    cand_base = best_candidate["base"]
    cand_x3 = best_candidate["cost_x3"]

    checks = {
        "nested_all_folds_bear_aware_passed": outer["all_folds_passed"],
        "nested_base_return_positive": nested_base["total_return"] > 0.0,
        "nested_cost_x3_return_positive": outer["cost_x3"]["total_return"] > 0.0,
        "best_cand_return_positive": cand_base["total_return"] > 0.0,
        "best_cand_cost_x3_positive": cand_x3["total_return"] > 0.0,
        "best_cand_mdd_lte_15pct": cand_base["maximum_drawdown"] <= 0.15,
        "best_cand_sharpe_gte_1": cand_base["sharpe"] >= 1.0,
        "cost_monotone": (
            cand_base["final_equity_krw"]
            >= best_candidate["cost_x2"]["final_equity_krw"]
            >= cand_x3["final_equity_krw"]
        ),
        "white_reality_check_p_lte_010": stats["white_reality_check_vs_cash"]["p_value"] <= 0.10,
        "pbo_lte_040": stats["cscv_pbo"]["probability_backtest_overfitting"] <= 0.40,
        "dsr_exceeds_expected_max_sharpe": stats["deflated_sharpe_ratio"]["probability"] >= 0.50,
    }

    all_passed = all(checks.values())
    finalist_name = best_candidate["name"] if all_passed else None

    rationale = (
        f"Selected {best_candidate['name']} as finalist (All gates passed)"
        if all_passed
        else f"No candidate passed all pre-registered V5 gates. Failed checks: {[k for k, v in checks.items() if not v]}"
    )

    return {
        "selected_candidate": best_candidate["name"],
        "checks": checks,
        "all_passed": all_passed,
        "finalist": finalist_name,
        "rationale": rationale,
    }


def _simulate_multi_asset_portfolio(
    universe_candles: Mapping[str, Sequence[Candle]],
    universe_weights: Mapping[str, Sequence[float]],
) -> dict[str, Any]:
    """Simulate a multi-asset portfolio with synchronized rebalancing and fees."""
    assets = sorted(universe_candles)
    length = len(next(iter(universe_candles.values())))
    settings = v5_settings(1)

    initial_capital = settings.initial_capital_krw
    cash = initial_capital
    holdings = {asset: 0.0 for asset in assets}
    equity_curve: list[float] = [initial_capital]

    for i in range(length):
        current_prices = {asset: universe_candles[asset][i].open for asset in assets}
        equity = cash + sum(holdings[asset] * current_prices[asset] for asset in assets)

        for asset in assets:
            target_w = universe_weights[asset][i]
            target_notional = equity * target_w
            current_notional = holdings[asset] * current_prices[asset]
            trade_notional = target_notional - current_notional

            if abs(trade_notional) >= settings.minimum_order_krw:
                fee = abs(trade_notional) * settings.fee_rate
                slippage = abs(trade_notional) * (settings.slippage_bps / 10000.0)
                cash -= (trade_notional + fee + slippage)
                holdings[asset] += trade_notional / current_prices[asset]

        close_prices = {asset: universe_candles[asset][i].close for asset in assets}
        end_equity = cash + sum(holdings[asset] * close_prices[asset] for asset in assets)
        equity_curve.append(end_equity)

    total_return = (equity_curve[-1] / initial_capital) - 1.0
    peak = equity_curve[0]
    mdd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak
        if dd > mdd:
            mdd = dd

    rets = [equity_curve[j] / equity_curve[j - 1] - 1.0 for j in range(1, len(equity_curve))]
    vol = pstdev(rets) if len(rets) > 1 else 0.0
    sharpe = (mean(rets) / vol) * sqrt(365.25) if vol > 0 else 0.0

    return {
        "initial_equity_krw": initial_capital,
        "final_equity_krw": equity_curve[-1],
        "total_return": total_return,
        "maximum_drawdown": mdd,
        "sharpe": sharpe,
    }


def _sample(candles: Sequence[Candle]) -> tuple[Candle, ...]:
    if len(candles) != HISTORICAL_COUNT:
        raise ValueError(f"dataset must contain exactly {HISTORICAL_COUNT} candles")
    return tuple(candles)


def _validate_weights(weights: Sequence[float], expected_length: int) -> None:
    if len(weights) != expected_length:
        raise ValueError("weights length mismatch")
    for w in weights:
        if not isfinite(w) or w < 0.0 or w > 1.0:
            raise ValueError(f"weight {w} out of bounds")


def _prefix_audit(candidate: Any, candles: Sequence[Candle], full_weights: Sequence[float]) -> dict[str, Any]:
    mismatches = 0
    checked = 0
    step = 89
    for end in range(candidate.required_history_bars, len(candles) + 1, step):
        prefix = candidate.generate(candles[:end])
        mismatches += sum(abs(a - b) > 1e-12 for a, b in zip(prefix, full_weights[:end]))
        checked += 1
    return {"checked_checkpoints": checked, "mismatches": mismatches, "passed": mismatches == 0}


def _returns(equity_curve: Sequence[float]) -> tuple[float, ...]:
    return tuple(equity_curve[i] / equity_curve[i - 1] - 1.0 for i in range(1, len(equity_curve)))


def _metrics(result: RebalanceBacktestResult) -> dict[str, Any]:
    rets = _returns(result.equity_curve)
    vol = pstdev(rets) if len(rets) > 1 else 0.0
    sharpe = mean(rets) / vol * sqrt(365.25) if vol > 0 else 0.0
    min_cash = min(result.cash_curve) if result.cash_curve else result.final_equity
    min_base = min(result.base_quantity_curve) if result.base_quantity_curve else 0.0
    return {
        "initial_equity_krw": result.initial_equity,
        "final_equity_krw": result.final_equity,
        "total_return": result.total_return,
        "maximum_drawdown": result.max_drawdown,
        "sharpe": sharpe,
        "exposure": result.exposure,
        "fill_count": result.fill_count,
        "total_fees_krw": result.total_fees,
        "turnover": result.turnover,
        "minimum_cash_krw": min_cash,
        "minimum_base_quantity": min_base,
    }


def _manifest(candles: Sequence[Candle]) -> dict[str, Any]:
    man = dataset_manifest(candles)
    return {
        "market": man.market,
        "candle_count": man.candle_count,
        "start_at": man.start_at.isoformat() if man.start_at else None,
        "end_at": man.end_at.isoformat() if man.end_at else None,
        "sha256": man.sha256,
    }
