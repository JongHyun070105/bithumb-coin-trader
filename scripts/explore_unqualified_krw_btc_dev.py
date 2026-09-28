#!/usr/bin/env python3
"""Run bounded, non-promotional diagnostics on the KRW-BTC development prefix.

The reader stops after the configured development rows. It never loads the
sealed tail from the historical 2,400-row file. Outputs are exploratory only.
"""

from __future__ import annotations

import argparse
import bisect
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import random
import statistics
import subprocess
import sys
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for _root in (ROOT, SRC):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from bithumb_coin_trader.composite_portfolio_backtest import (
    run_composite_portfolio_backtest,
)
from bithumb_coin_trader.fee_regimes import FEE_REGIMES, get_fee_regime_settings
from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.rebalance_backtest import RebalanceBacktester
from bithumb_coin_trader.research_infra.evaluation import create_chronological_folds
from bithumb_coin_trader.strategy import DailySmaTrendStrategy
from bithumb_coin_trader.strategy_v4_candidates import V4AdaptiveDonchianAtrStrategy
from bithumb_coin_trader.strategy_v6_candidates import (
    V6DailyEmaPullbackStrategy,
    V6FastDonchianSwingStrategy,
)


CSV_FIELDS = ("market", "timestamp", "open", "high", "low", "close", "volume")
DEFAULT_DEVELOPMENT_BARS = 2_220
DEFAULT_DIRECT_OOS_START = 1_020
SECONDS_PER_DAY = 86_400
STRATEGIES = {
    "Core70_V6DailyEmaPullback_Sat30": V6DailyEmaPullbackStrategy,
    "Core70_V6FastDonchian_Sat30": V6FastDonchianSwingStrategy,
}


def read_development_prefix(path: Path, bar_count: int) -> tuple[list[Candle], str, int]:
    """Read and hash only the CSV header and first ``bar_count`` data rows."""
    if bar_count < 2:
        raise ValueError("bar_count must be at least two")
    digest = hashlib.sha256()
    prefix_bytes = 0
    candles: list[Candle] = []
    with path.open("rb", buffering=0) as handle:
        header = handle.readline()
        if not header:
            raise ValueError("input CSV is empty")
        digest.update(header)
        prefix_bytes += len(header)
        fields = next(csv.reader([header.decode("utf-8-sig").rstrip("\r\n")]))
        if tuple(fields) != CSV_FIELDS:
            raise ValueError(f"CSV header must be {CSV_FIELDS!r}")

        for index in range(bar_count):
            raw_line = handle.readline()
            if not raw_line:
                raise ValueError(f"input ended after {index} of {bar_count} requested rows")
            digest.update(raw_line)
            prefix_bytes += len(raw_line)
            row = next(csv.reader([raw_line.decode("utf-8").rstrip("\r\n")]))
            if len(row) != len(CSV_FIELDS):
                raise ValueError(f"invalid CSV field count in development row {index + 1}")
            market, timestamp, open_, high, low, close, volume = row
            candles.append(
                Candle(
                    market=market,
                    timestamp=datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
                    open=float(open_),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    volume=float(volume),
                )
            )

    return candles, digest.hexdigest(), prefix_bytes


def _metrics(result: Any, bar_count: int) -> dict[str, float | int]:
    years = (bar_count - 1) / 365.25
    curve = result.equity_curve
    returns = [curve[i] / curve[i - 1] - 1.0 for i in range(1, len(curve))]
    volatility = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    sharpe = (
        statistics.mean(returns) / volatility * math.sqrt(365.25)
        if volatility > 0.0
        else 0.0
    )
    if hasattr(result, "round_trip_trades"):
        round_trips = result.round_trip_trades
    else:
        round_trips = 0
        in_position = False
        for fill in result.fills:
            if fill.side == "buy" and not in_position:
                in_position = True
            elif fill.side == "sell" and in_position:
                round_trips += 1
                in_position = False
    cagr = (
        (result.final_equity / result.initial_equity) ** (1.0 / years) - 1.0
        if years > 0.0 and result.final_equity > 0.0
        else 0.0
    )
    return {
        "total_return": result.total_return,
        "cagr": cagr,
        "max_drawdown": result.max_drawdown,
        "sharpe": sharpe,
        "fill_count": result.fill_count,
        "round_trip_trades": round_trips,
        "total_fees_krw": (
            result.total_fees
            if hasattr(result, "total_fees")
            else result.total_fees_krw
        ),
    }


def _run_weights(candles: Sequence[Candle], weights: Sequence[float], regime: str) -> dict[str, Any]:
    result = RebalanceBacktester(get_fee_regime_settings(regime)).run(candles, weights)
    return _metrics(result, len(candles))


def _fold_diagnostics(
    development: Sequence[Candle],
    core_weights: Sequence[float],
    satellite_weights: Sequence[float],
) -> list[dict[str, Any]]:
    timestamps_ns = [int(candle.timestamp.timestamp() * 1_000_000_000) for candle in development]
    folds = create_chronological_folds(
        timestamps_ns,
        n_folds=5,
        embargo_s=5 * SECONDS_PER_DAY,
        expanding=True,
    )
    combined = [
        min(1.0, 0.70 * core + 0.30 * satellite)
        for core, satellite in zip(core_weights, satellite_weights, strict=True)
    ]
    output = []
    for fold in folds:
        start = bisect.bisect_left(timestamps_ns, fold.test_start_ns)
        end = bisect.bisect_right(timestamps_ns, fold.test_end_ns)
        if start < 1 or end - start < 2:
            raise ValueError(f"fold {fold.fold_id} has insufficient test candles")
        test_candles = development[start - 1 : end]
        test_weights = combined[start - 1 : end]
        output.append(
            {
                "fold_id": fold.fold_id,
                "train_start": development[0].timestamp.isoformat(),
                "train_end": datetime.fromtimestamp(
                    fold.train_end_ns / 1_000_000_000, tz=timezone.utc
                ).isoformat(),
                "test_start": test_candles[1].timestamp.isoformat(),
                "test_end": test_candles[-1].timestamp.isoformat(),
                "embargo_seconds": fold.embargo_ns / 1_000_000_000,
                "test_bars": len(test_candles) - 1,
                "cost_regime": "normal_fee",
                "classification": "EXPLORATORY_ONLY",
                "metrics": _run_weights(test_candles, test_weights, "normal_fee"),
            }
        )
    return output


def run_exploration(
    path: Path,
    *,
    development_bars: int = DEFAULT_DEVELOPMENT_BARS,
    oos_start: int = DEFAULT_DIRECT_OOS_START,
    placebo_seeds: int = 10,
    expected_prefix_sha256: str | None = None,
) -> dict[str, Any]:
    if not 2 <= oos_start < development_bars:
        raise ValueError("oos_start must identify at least two development bars")
    if placebo_seeds < 1:
        raise ValueError("placebo_seeds must be positive")
    candles, prefix_sha256, prefix_bytes = read_development_prefix(path, development_bars)
    if expected_prefix_sha256 and prefix_sha256 != expected_prefix_sha256:
        raise ValueError("development prefix SHA-256 did not match the pinned value")

    timestamps = [candle.timestamp for candle in candles]
    intervals_s = [
        int((timestamps[i] - timestamps[i - 1]).total_seconds())
        for i in range(1, len(timestamps))
    ]
    close_values = [candle.close for candle in candles]
    profile = {
        "record_count": len(candles),
        "symbols": sorted({candle.market for candle in candles}),
        "record_type": "daily_ohlcv_candle",
        "start_utc": timestamps[0].astimezone(timezone.utc).isoformat(),
        "end_utc": timestamps[-1].astimezone(timezone.utc).isoformat(),
        "duplicate_timestamps": len(timestamps) - len(set(timestamps)),
        "non_increasing_intervals": sum(delta <= 0 for delta in intervals_s),
        "intervals_not_24h": sum(delta != SECONDS_PER_DAY for delta in intervals_s),
        "minimum_interval_seconds": min(intervals_s),
        "maximum_interval_seconds": max(intervals_s),
        "non_positive_close_count": sum(value <= 0 for value in close_values),
        "negative_volume_count": sum(candle.volume < 0 for candle in candles),
        "file_size_bytes_metadata_only": path.stat().st_size,
        "bytes_read_through_development_prefix": prefix_bytes,
    }

    development = candles
    oos_candles = list(development[oos_start - 1 :])
    composites: dict[str, dict[str, Any]] = {}
    placebo_inputs: dict[str, list[float]] = {}
    for strategy_name, factory in STRATEGIES.items():
        core_weights = V4AdaptiveDonchianAtrStrategy().generate(development)
        satellite_weights = factory().generate(development)
        composite_weights = [
            min(1.0, 0.70 * core + 0.30 * satellite)
            for core, satellite in zip(core_weights, satellite_weights, strict=True)
        ][oos_start - 1 :]
        placebo_inputs[strategy_name] = composite_weights
        composites[strategy_name] = {}
        for regime in FEE_REGIMES:
            result = run_composite_portfolio_backtest(
                oos_candles,
                core_weights[oos_start - 1 :],
                satellite_weights[oos_start - 1 :],
                get_fee_regime_settings(regime),
                core_ratio=0.70,
                satellite_ratio=0.30,
                fee_regime_name=regime,
            )
            composites[strategy_name][regime] = {
                **_metrics(result, len(oos_candles)),
                "exposure": result.exposure,
                "mean_holding_days": result.mean_holding_days,
            }

    sma_signals = DailySmaTrendStrategy().generate(development)
    sma_weights = [1.0 if int(signal) > 0 else 0.0 for signal in sma_signals]
    baseline_weights = {
        "cash": [0.0] * len(oos_candles),
        "buy_and_hold": [1.0] * len(oos_candles),
        "daily_sma50_200": sma_weights[oos_start - 1 :],
    }
    baselines = {
        name: {regime: _run_weights(oos_candles, weights, regime) for regime in FEE_REGIMES}
        for name, weights in baseline_weights.items()
    }

    placebo_values: dict[str, dict[str, list[float]]] = {
        strategy_name: {regime: [] for regime in FEE_REGIMES}
        for strategy_name in STRATEGIES
    }
    for strategy_index, (strategy_name, weights) in enumerate(placebo_inputs.items()):
        for seed_offset in range(placebo_seeds):
            shuffled = list(weights)
            random.Random(20260928 + seed_offset).shuffle(shuffled)
            assert sorted(shuffled) == sorted(weights)
            for regime in FEE_REGIMES:
                placebo_values[strategy_name][regime].append(
                    _run_weights(oos_candles, shuffled, regime)["total_return"]
                )
    placebos = {
        strategy_name: {
            regime: {
                "seed_count": len(values),
                "median_total_return": statistics.median(values),
                "minimum_total_return": min(values),
                "maximum_total_return": max(values),
                "target_weight_histogram_preserved": True,
                "classification": "EXPLORATORY_ONLY",
            }
            for regime, values in by_regime.items()
        }
        for strategy_name, by_regime in placebo_values.items()
    }

    first_factory = next(iter(STRATEGIES.values()))
    fold_core = V4AdaptiveDonchianAtrStrategy().generate(development)
    fold_satellite = first_factory().generate(development)
    folds = _fold_diagnostics(development, fold_core, fold_satellite)
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    try:
        code_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        code_commit = "UNKNOWN"

    return {
        "schema_version": 1,
        "data_qualification": "UNQUALIFIED",
        "research_role": "EXPLORATORY_ONLY",
        "promotion_eligible": False,
        "prospective_holdout_consumed": False,
        "source": {
            "path": str(path.resolve()),
            "development_rows_read": development_bars,
            "sealed_tail_rows_read": 0,
            "development_prefix_sha256": prefix_sha256,
            "development_prefix_bytes": prefix_bytes,
            "full_file_size_bytes_metadata_only": path.stat().st_size,
        },
        "profile": profile,
        "evaluation": {
            "oos_start_development_index_1_based": oos_start,
            "oos_bars": len(oos_candles),
            "fee_scenarios": {
                name: {"fee_rate": config.fee_rate, "slippage_bps": config.slippage_bps}
                for name, config in FEE_REGIMES.items()
            },
            "composite_diagnostics": composites,
            "baselines": baselines,
            "randomized_placebos": placebos,
            "placebo_experiment_count": len(STRATEGIES) * len(FEE_REGIMES) * placebo_seeds,
            "walk_forward": {
                "strategy": next(iter(STRATEGIES)),
                "fold_count": len(folds),
                "folds": folds,
            },
        },
        "reproducibility": {
            "git_commit": code_commit,
            "script_sha256": script_hash,
            "random_seed_base": 20260928,
        },
        "limitations": [
            "One unqualified KRW-BTC daily OHLCV series; no trade IDs or order-book events.",
            "Placebos shuffle target weights and preserve only their histogram; they do not model market dependence.",
            "Fold outputs are pipeline diagnostics on exploratory data, not candidate-selection evidence.",
            "No maker queue, latency, order-book, or multi-asset execution model was exercised.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Local KRW-BTC daily CSV")
    parser.add_argument("--development-bars", type=int, default=DEFAULT_DEVELOPMENT_BARS)
    parser.add_argument("--oos-start", type=int, default=DEFAULT_DIRECT_OOS_START)
    parser.add_argument("--placebo-seeds", type=int, default=10)
    parser.add_argument("--expected-prefix-sha256")
    args = parser.parse_args()
    report = run_exploration(
        args.input,
        development_bars=args.development_bars,
        oos_start=args.oos_start,
        placebo_seeds=args.placebo_seeds,
        expected_prefix_sha256=args.expected_prefix_sha256,
    )
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
