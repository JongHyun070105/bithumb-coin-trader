"""Strategy V7.1 Point-in-Time Dynamic Universe Research Engine."""

from __future__ import annotations

from datetime import UTC, datetime
from math import sqrt
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence

from .data import load_candles_csv
from .dynamic_universe import (
    DynamicUniverseConfig,
    PointInTimeUniverseManager,
    TOP_UNIVERSE_CANDIDATES,
)
from .fee_regimes import FEE_REGIMES, get_fee_regime_settings
from .models import Candle
from .rebalance_backtest import RebalanceBacktester

HOLDOUT_4H_BARS = 1080  # 180 days * 6 bars/day


def run_strategy_v7_1_dynamic_universe_research(
    candles_by_market: Mapping[str, Sequence[Candle]],
    *,
    universe_sizes: tuple[int, ...] = (10, 20, 30),
) -> dict[str, Any]:
    """Execute dynamic universe backtests across Top 10, Top 20 (Baseline), and Top 30 sizes."""
    # Partition development only (embargo 180-day holdout)
    dev_candles: dict[str, Sequence[Candle]] = {}
    for market, c_list in candles_by_market.items():
        if len(c_list) > HOLDOUT_4H_BARS:
            dev_candles[market] = c_list[:-HOLDOUT_4H_BARS]
        else:
            dev_candles[market] = c_list

    # Ensure BTC is present as the market regime anchor
    if "KRW-BTC" not in dev_candles:
        raise ValueError("KRW-BTC must be present in universe candles")

    btc_dev = dev_candles["KRW-BTC"]
    btc_closes = [c.close for c in btc_dev]
    min_length = len(btc_dev)
    total_days = min_length * 4.0 / 24.0
    total_weeks = total_days / 7.0

    universe_size_results: dict[str, Any] = {}

    for top_n in universe_sizes:
        cfg = DynamicUniverseConfig(top_n=top_n, min_listing_days=30, rolling_volume_days=30)
        manager = PointInTimeUniverseManager(dev_candles, cfg)

        # Generate dynamic weights
        # - Evaluate every 24h (every 6 4H bars)
        # - Top 2 assets allocated 15% each (Max 30% exposure)
        weights_by_market: dict[str, list[float]] = {m: [] for m in dev_candles}
        current_top_assets: list[str] = []

        for i in range(min_length):
            if i < 120:  # 20-day warmup (120 4H bars)
                for m in dev_candles:
                    weights_by_market[m].append(0.0)
                continue

            current_time = btc_dev[i].timestamp
            current_indices = {m: min(i, len(dev_candles[m]) - 1) for m in dev_candles}

            # Rebalance every 24 hours
            if i % 6 == 0:
                # 1. Macro BTC Regime Filter (BTC > 20-day SMA)
                btc_sma120 = sum(btc_closes[i - 120 + 1 : i + 1]) / 120.0
                btc_bull = btc_closes[i] > btc_sma120

                if btc_bull:
                    # 2. Get Point-in-Time Eligible Top-N Universe
                    eligible_top_n = manager.get_eligible_universe_at_index(
                        current_time, current_indices, bars_per_day=6
                    )

                    # 3. Score eligible assets by 7-day momentum (42 4H bars)
                    scores: dict[str, float] = {}
                    for m in eligible_top_n:
                        c_list = dev_candles[m]
                        idx = current_indices[m]
                        if idx >= 42:
                            c_now = c_list[idx].close
                            c_past = c_list[idx - 42].close
                            mom7d = (c_now / c_past) - 1.0
                            if mom7d > 0.0:
                                scores[m] = mom7d

                    # Select Top 2 assets
                    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                    current_top_assets = [m for m, s in sorted_scores[:2]]
                else:
                    current_top_assets = []

            for m in dev_candles:
                w = 0.15 if m in current_top_assets else 0.0
                weights_by_market[m].append(w)

        # Evaluate across 4 Fee Regimes
        regime_performances: dict[str, Any] = {}
        for regime_name in ("live_zero_fee", "normal_fee", "stress_2x", "stress_3x"):
            settings = get_fee_regime_settings(regime_name)
            market_returns = []
            total_fills = 0
            gross_pnl = 0.0

            for m in dev_candles:
                w_series = weights_by_market[m]
                c_series = dev_candles[m][: len(w_series)]
                res = RebalanceBacktester(settings).run(c_series, w_series)
                market_returns.append(res.total_return)
                total_fills += res.fill_count

            mean_return = mean(market_returns)
            trades_per_week = (total_fills / 2.0) / total_weeks if total_weeks > 0 else 0.0

            regime_performances[regime_name] = {
                "mean_return": mean_return,
                "total_fills": total_fills,
                "round_trips": int(total_fills / 2),
                "trades_per_week": trades_per_week,
            }

        universe_size_results[f"Top_{top_n}"] = regime_performances

    return {
        "schema_version": 1,
        "status": "research_only",
        "generated_at": datetime.now(UTC).isoformat(),
        "mission": "V7.1: Point-in-Time Dynamic Universe Expansion",
        "dataset": {
            "assets_evaluated": len(dev_candles),
            "dev_period_days": total_days,
            "dev_period_weeks": total_weeks,
            "holdout_embargo_bars": HOLDOUT_4H_BARS,
            "holdout_status": "SEALED_180_DAYS_UNTOUCHED",
        },
        "universe_size_results": universe_size_results,
    }
