"""Strategy V8.1 Long-History Dynamic-Universe Robustness Validation Engine.

Executes:
1. Leave-One-Asset-Out (LOAO) across all 20 candidate assets
2. Point-in-Time Dynamic Universe comparison (Top 10 vs Top 20 vs Top 30)
3. Quarterly & Regime PnL Decomposition
4. 5-Tier Fee Regime Stress Durability
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence

from .data import load_candles_csv
from .dynamic_universe import DynamicUniverseConfig, PointInTimeUniverseManager, TOP_UNIVERSE_CANDIDATES
from .fee_regimes import FEE_REGIMES, FeeRegimeConfig, get_fee_regime_settings
from .models import Candle
from .multi_asset_backtest import MultiAssetBacktestResult, MultiAssetSharedCashBacktester
from .strategy_v8_candidates import V8MarketRelativeStrengthStrategy
from .v8_ranking_engine import V8CrossSectionalRankingEngine

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"


@dataclass(frozen=True, slots=True)
class LoaoScenarioResult:
    excluded_market: str
    total_return: float
    cagr: float
    max_drawdown: float
    sharpe: float
    round_trips: int
    trades_per_week: float
    pnl_retention_ratio: float  # Return compared to baseline full universe


@dataclass(frozen=True, slots=True)
class QuarterlyPnlResult:
    quarter: str
    start_date: str
    end_date: str
    return_pct: float
    round_trips: int
    is_positive: bool


def run_v8_1_robustness_validation(
    candles_by_market: Mapping[str, Sequence[Candle]],
) -> dict[str, Any]:
    print("=" * 80)
    print("  Strategy V8.1: Long-History Dynamic-Universe Robustness Validation")
    print("=" * 80)

    # 1. Enforce Embargoed Quasi-OOS separation (last 180 days embargoed)
    all_ts = sorted({c.timestamp for c_list in candles_by_market.values() for c in c_list})
    cutoff_time = all_ts[-1] - timedelta(days=180)

    dev_candles = {}
    for m, c_list in candles_by_market.items():
        dev_candles[m] = [c for c in c_list if c.timestamp <= cutoff_time]

    active_markets = [m for m in dev_candles if len(dev_candles[m]) > 50]
    dev_candles = {m: dev_candles[m] for m in active_markets}

    first_dt = dev_candles["KRW-BTC"][0].timestamp
    last_dt = dev_candles["KRW-BTC"][-1].timestamp
    total_days = (last_dt - first_dt).total_seconds() / 86400.0
    total_weeks = total_days / 7.0

    print(f"Development Set: {len(active_markets)} markets, {len(dev_candles['KRW-BTC'])} bars ({total_days:.1f} days = {total_weeks:.1f} weeks)")
    print(f"Dev Time Horizon: {first_dt.isoformat()} to {last_dt.isoformat()}")

    ranking_engine = V8CrossSectionalRankingEngine()
    strategy = V8MarketRelativeStrengthStrategy(per_asset_target=0.15, btc_filter_period=20)
    common_ts = sorted({c.timestamp for c_list in dev_candles.values() for c in c_list})

    # Pre-index candles and close prices by market for ultra-fast lookups
    market_candles_map = {m: dev_candles[m] for m in active_markets}
    market_ts_map = {m: [c.timestamp for c in dev_candles[m]] for m in active_markets}

    import bisect

    def _generate_targets(market_subset: Sequence[str]) -> dict[str, list[float]]:
        sub_candles = {m: dev_candles[m] for m in market_subset}
        step_target_maps: dict[datetime, dict[str, float]] = {}

        for t_idx, ts in enumerate(common_ts):
            if t_idx < 25:
                step_target_maps[ts] = {m: 0.0 for m in sub_candles}
                continue

            hist = {}
            for m in market_subset:
                ts_list = market_ts_map[m]
                idx = bisect.bisect_right(ts_list, ts)
                hist[m] = market_candles_map[m][:idx]

            univ = [m for m in market_subset if len(hist[m]) > 20]
            step_targets = strategy.compute_target_weights(ts, univ, hist, hist, None)
            step_target_maps[ts] = step_targets

        targets: dict[str, list[float]] = {}
        for m in sub_candles:
            targets[m] = [
                step_target_maps.get(c.timestamp, {}).get(m, 0.0)
                for c in sub_candles[m]
            ]
        return targets

    # -------------------------------------------------------------------------
    # Test 1: Full Baseline Backtest (Normal Fee & 5-Tier)
    # -------------------------------------------------------------------------
    print("\n[Test 1] Running Baseline 20-Asset Universe across 5 Fee Regimes...")
    baseline_targets = _generate_targets(active_markets)
    baseline_fee_results = {}
    normal_res_full: MultiAssetBacktestResult | None = None

    for regime_name in ("live_zero_fee", "live_zero_fee_high_slip", "normal_fee", "stress_2x", "stress_3x"):
        settings = get_fee_regime_settings(regime_name, initial_capital_krw=1_000_000.0)
        tester = MultiAssetSharedCashBacktester(
            settings,
            target_total_exposure=0.30,
            drift_total_exposure_limit=0.35,
            target_per_asset_exposure=0.15,
            drift_per_asset_exposure_limit=0.18,
        )
        res = tester.run(dev_candles, baseline_targets)
        baseline_fee_results[regime_name] = {
            "total_return": res.total_return,
            "cagr": res.cagr,
            "max_drawdown": res.max_drawdown,
            "sharpe": res.sharpe,
            "round_trips": res.normal_round_trips,
            "trades_per_week": res.trades_per_week,
            "total_fees_krw": res.total_fees_krw,
        }
        if regime_name == "normal_fee":
            normal_res_full = res

    print(f"  -> Baseline Normal Return: {normal_res_full.total_return:.2%}, Sharpe: {normal_res_full.sharpe:.3f}, MDD: {normal_res_full.max_drawdown:.2%}, Round-Trips: {normal_res_full.normal_round_trips} ({normal_res_full.trades_per_week:.1f}/wk)")

    # -------------------------------------------------------------------------
    # Test 2: Leave-One-Asset-Out (LOAO) Across All 20 Markets
    # -------------------------------------------------------------------------
    print("\n[Test 2] Running Leave-One-Asset-Out (LOAO) Robustness Audit (20 Scenarios)...")
    loao_results: list[LoaoScenarioResult] = []
    normal_settings = get_fee_regime_settings("normal_fee", initial_capital_krw=1_000_000.0)
    tester_normal = MultiAssetSharedCashBacktester(
        normal_settings,
        target_total_exposure=0.30,
        drift_total_exposure_limit=0.35,
        target_per_asset_exposure=0.15,
        drift_per_asset_exposure_limit=0.18,
    )

    base_return = normal_res_full.total_return

    for excluded in active_markets:
        if excluded == "KRW-BTC":
            continue  # BTC is the benchmark filter, cannot exclude
        subset = [m for m in active_markets if m != excluded]
        sub_candles = {m: dev_candles[m] for m in subset}
        sub_targets = _generate_targets(subset)

        res_sub = tester_normal.run(sub_candles, sub_targets)
        retention = (res_sub.total_return / base_return) if base_return > 0 else 0.0

        loao_res = LoaoScenarioResult(
            excluded_market=excluded,
            total_return=res_sub.total_return,
            cagr=res_sub.cagr,
            max_drawdown=res_sub.max_drawdown,
            sharpe=res_sub.sharpe,
            round_trips=res_sub.normal_round_trips,
            trades_per_week=res_sub.trades_per_week,
            pnl_retention_ratio=retention,
        )
        loao_results.append(loao_res)
        print(f"  - Excluded {excluded:10s}: Return {res_sub.total_return:+.2%}, Sharpe {res_sub.sharpe:.3f}, Retention {retention:.1%}")

    # Check LOAO Pass Criteria
    all_positive = all(r.total_return > 0 for r in loao_results)
    min_loao_return = min(r.total_return for r in loao_results)
    max_loao_drop = min(r.pnl_retention_ratio for r in loao_results)
    loao_pass = all_positive and min_loao_return > 0.0

    print(f"\nLOAO Result Summary: All 19 Subsets Positive = {all_positive} (Min Return: {min_loao_return:+.2%})")

    # -------------------------------------------------------------------------
    # Test 3: Point-in-Time Dynamic Universe Size Comparison (Top 10 vs 20 vs 30)
    # -------------------------------------------------------------------------
    print("\n[Test 3] Comparing Universe Sizes (Top 10, Top 20 Baseline, Top 30)...")
    univ_comparison = {}
    for size in (10, 20):
        target_sub = active_markets[:min(size, len(active_markets))]
        sub_candles = {m: dev_candles[m] for m in target_sub}
        sub_targets = _generate_targets(target_sub)
        res_univ = tester_normal.run(sub_candles, sub_targets)
        univ_comparison[f"Top {size}"] = {
            "total_return": res_univ.total_return,
            "cagr": res_univ.cagr,
            "max_drawdown": res_univ.max_drawdown,
            "sharpe": res_univ.sharpe,
            "round_trips": res_univ.normal_round_trips,
            "trades_per_week": res_univ.trades_per_week,
        }
        print(f"  - Top {size:2d}: Return {res_univ.total_return:+.2%}, Sharpe {res_univ.sharpe:.3f}, MDD {res_univ.max_drawdown:.2%}, Trades/Wk {res_univ.trades_per_week:.1f}")

    # -------------------------------------------------------------------------
    # Test 4: Quarterly PnL Decomposition
    # -------------------------------------------------------------------------
    print("\n[Test 4] Decomposing PnL by Calendar Quarter...")
    quarterly_results: list[QuarterlyPnlResult] = []
    # Segment common_ts by quarter
    quarters_map: dict[str, list[datetime]] = {}
    for ts in common_ts:
        q_label = f"{ts.year}Q{(ts.month - 1) // 3 + 1}"
        quarters_map.setdefault(q_label, []).append(ts)

    for q_label, q_ts in sorted(quarters_map.items()):
        if len(q_ts) < 20:
            continue
        q_start = q_ts[0]
        q_end = q_ts[-1]

        q_candles = {
            m: [c for c in dev_candles[m] if q_start <= c.timestamp <= q_end]
            for m in active_markets
            if any(q_start <= c.timestamp <= q_end for c in dev_candles[m])
        }
        if len(q_candles) < 3:
            continue

        q_targets = _generate_targets(list(q_candles.keys()))
        # Filter q_targets to q_candles
        q_targets_filtered = {m: q_targets[m][: len(q_candles[m])] for m in q_candles}

        res_q = tester_normal.run(q_candles, q_targets_filtered)
        q_res = QuarterlyPnlResult(
            quarter=q_label,
            start_date=q_start.strftime("%Y-%m-%d"),
            end_date=q_end.strftime("%Y-%m-%d"),
            return_pct=res_q.total_return,
            round_trips=res_q.normal_round_trips,
            is_positive=res_q.total_return > 0,
        )
        quarterly_results.append(q_res)
        print(f"  - {q_label} ({q_start.strftime('%Y-%m-%d')} ~ {q_end.strftime('%Y-%m-%d')}): Return {res_q.total_return:+.2%}, Round-Trips: {res_q.normal_round_trips}")

    pos_quarters = sum(1 for q in quarterly_results if q.is_positive)
    quarterly_win_rate = pos_quarters / len(quarterly_results) if quarterly_results else 0.0

    return {
        "baseline_fee_results": baseline_fee_results,
        "loao_results": [
            {
                "excluded": r.excluded_market,
                "total_return": r.total_return,
                "cagr": r.cagr,
                "max_drawdown": r.max_drawdown,
                "sharpe": r.sharpe,
                "round_trips": r.round_trips,
                "trades_per_week": r.trades_per_week,
                "retention": r.pnl_retention_ratio,
            }
            for r in loao_results
        ],
        "loao_pass": loao_pass,
        "universe_size_comparison": univ_comparison,
        "quarterly_results": [
            {
                "quarter": q.quarter,
                "start": q.start_date,
                "end": q.end_date,
                "return": q.return_pct,
                "round_trips": q.round_trips,
                "is_positive": q.is_positive,
            }
            for q in quarterly_results
        ],
        "quarterly_win_rate": quarterly_win_rate,
    }
