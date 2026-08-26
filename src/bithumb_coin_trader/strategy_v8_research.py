"""Strategy V8 Market-Wide Intraday Research Engine.

Evaluates 4 strategy families across 5-Tier fee regimes using the V7.2.2 Multi-Asset Shared-Cash Backtester.
Enforces:
1. Strict 180-day Embargoed Quasi-OOS separation
2. V8 Family Trial Ledger recording
3. White's Reality Check (WRC), CSCV PBO, Deflated Sharpe Ratio (DSR)
4. Per-asset PnL attribution analysis
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from math import erf, sqrt
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence

from .data import load_candles_csv
from .dynamic_universe import PointInTimeUniverseManager, TOP_UNIVERSE_CANDIDATES
from .fee_regimes import FEE_REGIMES, FeeRegimeConfig, get_fee_regime_settings
from .models import Candle
from .multi_asset_backtest import MultiAssetBacktestResult, MultiAssetSharedCashBacktester
from .strategy_v8_candidates import V8StrategyBase, v8_strategy_factories
from .v8_ranking_engine import V8CrossSectionalRankingEngine

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
V8_LEDGER_FILE = ROOT / "reports" / "research_trial_ledger_v8.jsonl"


@dataclass(frozen=True, slots=True)
class V8StrategyEvaluation:
    name: str
    family: str
    fee_results: dict[str, dict[str, Any]]
    normal_return: float
    normal_cagr: float
    normal_mdd: float
    normal_sharpe: float
    normal_round_trips: int
    trades_per_week: float
    asset_pnl_attribution: dict[str, float]
    max_single_asset_pnl_share: float


def compute_v8_cscv_pbo(equity_curves_by_strategy: dict[str, Sequence[float]], num_blocks: int = 16) -> float:
    """Compute CSCV Probability of Backtest Overfitting for V8 candidate family."""
    names = list(equity_curves_by_strategy.keys())
    if len(names) < 2:
        return 0.0

    min_len = min(len(c) for c in equity_curves_by_strategy.values())
    if min_len < num_blocks * 2:
        return 0.5

    block_size = min_len // num_blocks
    returns_by_strategy = {}
    for name, eq in equity_curves_by_strategy.items():
        rets = [eq[i] / eq[i - 1] - 1.0 for i in range(1, min_len)]
        returns_by_strategy[name] = rets

    from itertools import combinations
    ranks_in_oos = []
    comb_indices = list(combinations(range(num_blocks), num_blocks // 2))[:500]

    for train_blocks in comb_indices:
        test_blocks = [b for b in range(num_blocks) if b not in train_blocks]

        train_sharpes = {}
        for name, rets in returns_by_strategy.items():
            train_r = []
            for b in train_blocks:
                start = b * block_size
                end = (b + 1) * block_size
                train_r.extend(rets[start:end])
            s = (mean(train_r) / pstdev(train_r)) if pstdev(train_r) > 1e-8 else 0.0
            train_sharpes[name] = s

        best_train = max(train_sharpes, key=train_sharpes.get)

        test_sharpes = {}
        for name, rets in returns_by_strategy.items():
            test_r = []
            for b in test_blocks:
                start = b * block_size
                end = (b + 1) * block_size
                test_r.extend(rets[start:end])
            s = (mean(test_r) / pstdev(test_r)) if pstdev(test_r) > 1e-8 else 0.0
            test_sharpes[name] = s

        sorted_test = sorted(test_sharpes.keys(), key=lambda k: test_sharpes[k], reverse=True)
        rank = sorted_test.index(best_train)
        ranks_in_oos.append(rank >= (len(sorted_test) / 2.0))

    return sum(ranks_in_oos) / len(ranks_in_oos) if ranks_in_oos else 0.0


def compute_v8_wrc_p_value(candidate_returns: Sequence[float], benchmark_returns: Sequence[float], b_samples: int = 500) -> float:
    """White's Reality Check p-value against cash benchmark."""
    import random
    diffs = [c - b for c, b in zip(candidate_returns, benchmark_returns)]
    if not diffs:
        return 1.0
    obs_mean = mean(diffs)
    if obs_mean <= 0:
        return 1.0

    n = len(diffs)
    centered = [d - obs_mean for d in diffs]
    boot_means = []
    for _ in range(b_samples):
        sample = [random.choice(centered) for _ in range(n)]
        boot_means.append(mean(sample))

    count = sum(1 for m in boot_means if m >= obs_mean)
    return count / b_samples


def compute_v8_dsr(sharpe: float, num_trials: int, variance: float = 1.0, skew: float = 0.0, kurt: float = 3.0) -> float:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado)."""
    if num_trials <= 1:
        return 0.5
    from math import log
    euler_mascheroni = 0.5772156649
    exp_max_sharpe = (1.0 - euler_mascheroni) * (2.0 * log(num_trials)) ** -0.5 + (2.0 * log(num_trials)) ** 0.5
    se = sqrt((1.0 - skew * sharpe + ((kurt - 1.0) / 4.0) * (sharpe ** 2)) / max(1, 100))
    z = (sharpe - exp_max_sharpe) / se if se > 1e-8 else 0.0
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


def run_v8_family_research(
    candles_entry_by_market: Mapping[str, Sequence[Candle]],
    candles_context_by_market: Mapping[str, Sequence[Candle]],
) -> dict[str, Any]:
    print("=" * 80)
    print("  Strategy V8 Research: Evaluating 4 Market-Wide Intraday Families")
    print("=" * 80)

    # 1. Enforce Embargoed Quasi-OOS separation (last 180 days embargoed)
    all_ts = sorted({c.timestamp for c_list in candles_entry_by_market.values() for c in c_list})
    cutoff_time = all_ts[-1] - timedelta(days=180)

    dev_entry = {}
    dev_context = {}
    for m, c_list in candles_entry_by_market.items():
        dev_entry[m] = [c for c in c_list if c.timestamp <= cutoff_time]
    for m, c_list in candles_context_by_market.items():
        dev_context[m] = [c for c in c_list if c.timestamp <= cutoff_time]

    active_markets = [m for m in dev_entry if len(dev_entry[m]) > 50]
    dev_entry = {m: dev_entry[m] for m in active_markets}
    dev_context = {m: dev_context[m] for m in active_markets if m in dev_context}

    print(f"Development Set: {len(active_markets)} markets, {len(dev_entry['KRW-BTC'])} entry bars")
    print(f"Dev Horizon: {dev_entry['KRW-BTC'][0].timestamp.isoformat()} to {dev_entry['KRW-BTC'][-1].timestamp.isoformat()}")

    ranking_engine = V8CrossSectionalRankingEngine()
    factories = v8_strategy_factories()
    evaluations: list[V8StrategyEvaluation] = []
    equity_curves: dict[str, list[float]] = {}

    common_entry_ts = sorted({c.timestamp for c_list in dev_entry.values() for c in c_list})

    for name, factory in factories.items():
        strat = factory()
        print(f"\nEvaluating Family Candidate: {name} ({strat.family})...")

        # 1. Precompute step targets at all timestamps
        step_target_maps: dict[datetime, dict[str, float]] = {}
        for t_idx, ts in enumerate(common_entry_ts):
            if t_idx < 30:
                step_target_maps[ts] = {m: 0.0 for m in dev_entry}
                continue

            hist_entry = {m: [c for c in dev_entry[m] if c.timestamp <= ts] for m in dev_entry}
            hist_context = {m: [c for c in dev_context[m] if c.timestamp <= ts] for m in dev_context}
            univ = [m for m in active_markets if len(hist_entry[m]) > 20]

            step_targets = strat.compute_target_weights(ts, univ, hist_entry, hist_context, ranking_engine)
            step_target_maps[ts] = step_targets

        # Map to each market's exact candle sequence
        target_weights_by_market: dict[str, list[float]] = {}
        for m in dev_entry:
            target_weights_by_market[m] = [
                step_target_maps.get(c.timestamp, {}).get(m, 0.0)
                for c in dev_entry[m]
            ]

        # 2. Backtest across 5-Tier fee regimes
        fee_res = {}
        normal_res: MultiAssetBacktestResult | None = None

        for regime_name in ("live_zero_fee", "live_zero_fee_high_slip", "normal_fee", "stress_2x", "stress_3x"):
            settings = get_fee_regime_settings(regime_name, initial_capital_krw=1_000_000.0)
            tester = MultiAssetSharedCashBacktester(
                settings,
                target_total_exposure=0.30,
                drift_total_exposure_limit=0.35,
                target_per_asset_exposure=0.15,
                drift_per_asset_exposure_limit=0.18,
            )
            res = tester.run(dev_entry, target_weights_by_market)
            fee_res[regime_name] = {
                "total_return": res.total_return,
                "cagr": res.cagr,
                "max_drawdown": res.max_drawdown,
                "sharpe": res.sharpe,
                "fill_count": res.fill_count,
                "normal_round_trips": res.normal_round_trips,
                "trades_per_week": res.trades_per_week,
                "total_fees_krw": res.total_fees_krw,
            }
            if regime_name == "normal_fee":
                normal_res = res
                equity_curves[name] = list(res.equity_curve)

        # 3. Asset PnL Attribution
        asset_pnl: dict[str, float] = {m: 0.0 for m in dev_entry}
        for f in normal_res.fills:
            if f.side == "sell":
                asset_pnl[f.market] += f.notional - f.fee
            elif f.side == "buy":
                asset_pnl[f.market] -= f.notional + f.fee

        total_net_pnl = sum(asset_pnl.values())
        max_share = max((abs(v) / abs(total_net_pnl) for v in asset_pnl.values()), default=0.0) if abs(total_net_pnl) > 0 else 0.0

        evaluations.append(
            V8StrategyEvaluation(
                name=name,
                family=strat.family,
                fee_results=fee_res,
                normal_return=normal_res.total_return,
                normal_cagr=normal_res.cagr,
                normal_mdd=normal_res.max_drawdown,
                normal_sharpe=normal_res.sharpe,
                normal_round_trips=normal_res.normal_round_trips,
                trades_per_week=normal_res.trades_per_week,
                asset_pnl_attribution=asset_pnl,
                max_single_asset_pnl_share=max_share,
            )
        )

        print(f"  -> Normal Return: {normal_res.total_return:.2%}, Sharpe: {normal_res.sharpe:.3f}, Round-Trips: {normal_res.normal_round_trips} ({normal_res.trades_per_week:.1f}/wk)")

    # 4. Statistical Validation across V8 candidates
    pbo = compute_v8_cscv_pbo(equity_curves, num_blocks=16)
    best_eval = max(evaluations, key=lambda e: e.normal_sharpe)
    best_curve = equity_curves[best_eval.name]
    best_rets = [best_curve[i] / best_curve[i - 1] - 1.0 for i in range(1, len(best_curve))]
    cash_rets = [0.0] * len(best_rets)
    wrc_p = compute_v8_wrc_p_value(best_rets, cash_rets, b_samples=500)
    dsr_prob = compute_v8_dsr(best_eval.normal_sharpe, num_trials=len(factories))

    # Append to V8 Family Ledger
    ledger_entries = []
    for ev in evaluations:
        entry = {
            "strategy": ev.name,
            "family": ev.family,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "fee_results": ev.fee_results,
            "normal_metrics": {
                "total_return": ev.normal_return,
                "cagr": ev.normal_cagr,
                "mdd": ev.normal_mdd,
                "sharpe": ev.normal_sharpe,
                "round_trips": ev.normal_round_trips,
                "trades_per_week": ev.trades_per_week,
                "max_single_asset_pnl_share": ev.max_single_asset_pnl_share,
            },
        }
        ledger_entries.append(entry)

    with V8_LEDGER_FILE.open("a", encoding="utf-8") as f:
        for entry in ledger_entries:
            f.write(json.dumps(entry) + "\n")

    return {
        "evaluations": [
            {
                "name": e.name,
                "family": e.family,
                "normal_return": e.normal_return,
                "normal_cagr": e.normal_cagr,
                "normal_mdd": e.normal_mdd,
                "normal_sharpe": e.normal_sharpe,
                "normal_round_trips": e.normal_round_trips,
                "trades_per_week": e.trades_per_week,
                "fee_results": e.fee_results,
                "max_single_asset_pnl_share": e.max_single_asset_pnl_share,
            }
            for e in evaluations
        ],
        "statistics": {
            "cscv_pbo": pbo,
            "wrc_p_value": wrc_p,
            "dsr_prob": dsr_prob,
        },
        "best_strategy": best_eval.name,
    }
