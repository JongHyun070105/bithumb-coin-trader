#!/usr/bin/env python3
"""Run Walk-Forward and Baseline Backtest on Tauric Multi-Agent and Institutional Strategies."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from bithumb_coin_trader.backtest import Backtester
from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.data import load_candles_csv
from bithumb_coin_trader.research import (
    compare_registered_candidates,
    run_chronological_research,
)
from bithumb_coin_trader.strategy import (
    InstitutionalDisplacementParameters,
    InstitutionalDisplacementStrategy,
    TradingAgentsMultiAgentParameters,
    TradingAgentsMultiAgentStrategy,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = PROJECT_ROOT / "data" / "krw-btc-30m-2026-08-14-wave4.csv"
OUTPUT_REPORT = PROJECT_ROOT / "reports" / "tauric-institutional-research-report.json"


def main() -> None:
    print(f"Loading data from {DATA_PATH}...")
    candles = load_candles_csv(DATA_PATH)
    print(f"Loaded {len(candles)} candles. Market: {candles[0].market}")

    settings = TradingSettings(
        initial_capital_krw=20_000,
        fee_rate=0.0025,
        slippage_bps=5.0,
        allocation_fraction=0.50,
        minimum_order_krw=5_000,
        cash_reserve_krw=5_000,
    )
    double_cost_settings = TradingSettings(
        initial_capital_krw=20_000,
        fee_rate=0.0050,
        slippage_bps=10.0,
        allocation_fraction=0.50,
        minimum_order_krw=5_000,
        cash_reserve_krw=5_000,
    )

    from bithumb_coin_trader.strategy import CompletedIntervalStrategy

    strategies = {
        "30m Institutional Displacement": InstitutionalDisplacementStrategy(),
        "30m TradingAgents MultiAgent": TradingAgentsMultiAgentStrategy(),
        "4h Completed Institutional Displacement": CompletedIntervalStrategy(
            InstitutionalDisplacementStrategy(
                InstitutionalDisplacementParameters(
                    vol_period=20,
                    vol_multiplier=2.0,
                    min_body_pct=50.0,
                    trend_ma_period=50,
                    stop_loss_pct=0.03,
                    take_profit_pct=0.08,
                    trailing_stop_pct=0.02,
                )
            ),
            source_minutes=30,
            target_minutes=240,
        ),
        "4h Completed TradingAgents MultiAgent": CompletedIntervalStrategy(
            TradingAgentsMultiAgentStrategy(
                TradingAgentsMultiAgentParameters(
                    fast_ma_period=20,
                    slow_ma_period=50,
                    rsi_period=14,
                    approval_threshold=70.0,
                    stop_loss_pct=0.03,
                    take_profit_pct=0.08,
                    trailing_stop_pct=0.02,
                )
            ),
            source_minutes=30,
            target_minutes=240,
        ),
    }

    full_results = {}
    print("\n" + "=" * 60)
    print(" 1. FULL PERIOD BASELINE EVALUATION (40,095 Bars)")
    print("=" * 60)

    backtester = Backtester(settings, allow_short=False)
    stress_backtester = Backtester(double_cost_settings, allow_short=False)

    for name, strat in strategies.items():
        signals = strat.generate(candles)
        res = backtester.run(candles, signals)
        stress_res = stress_backtester.run(candles, signals)

        full_results[name] = {
            "initial_equity": res.initial_equity,
            "final_equity": res.final_equity,
            "total_return_pct": round(res.total_return * 100, 2),
            "max_drawdown_pct": round(res.max_drawdown * 100, 2),
            "sharpe": round(res.sharpe, 4),
            "trade_count": res.trade_count,
            "win_rate_pct": round(res.win_rate * 100, 2),
            "double_cost_return_pct": round(stress_res.total_return * 100, 2),
        }
        print(f"[{name}]")
        print(f"  - Total Return: {res.total_return * 100:.2f}% (Double-cost Stress: {stress_res.total_return * 100:.2f}%)")
        print(f"  - Max Drawdown: {res.max_drawdown * 100:.2f}%")
        print(f"  - Sharpe Ratio: {res.sharpe:.4f}")
        print(f"  - Trades: {res.trade_count}, Win Rate: {res.win_rate * 100:.2f}%\n")

    print("=" * 60)
    print(" 2. OUT-OF-SAMPLE (OOS) WALK-FORWARD EVALUATION (8 Folds)")
    print("=" * 60)

    from bithumb_coin_trader.research import compare_candidate_factories

    factories = {
        "institutional_displacement": lambda: InstitutionalDisplacementStrategy(),
        "tauric_tradingagents_multiagent": lambda: TradingAgentsMultiAgentStrategy(),
    }
    comparison = compare_candidate_factories(
        candles,
        train_size=19_200,
        test_size=2_400,
        settings=settings,
        candidate_factories=factories,
    )

    oos_results = {}
    for report in comparison.candidates:
        profitable_folds = sum(f.result.total_return > 0 for f in report.folds)
        oos_results[report.candidate_name] = {
            "compounded_return_pct": round(report.compounded_return * 100, 2),
            "max_drawdown_pct": round(report.maximum_drawdown * 100, 2),
            "mean_sharpe": round(report.mean_sharpe, 4),
            "trade_count": report.trade_count,
            "weighted_win_rate_pct": round(report.weighted_win_rate * 100, 2),
            "profitable_folds": f"{profitable_folds} / {len(report.folds)}",
        }
        print(f"Candidate: {report.candidate_name}")
        print(f"  - OOS Compounded Return: {report.compounded_return * 100:.2f}%")
        print(f"  - OOS Max Drawdown: {report.maximum_drawdown * 100:.2f}%")
        print(f"  - Mean Sharpe: {report.mean_sharpe:.4f}")
        print(f"  - Total OOS Trades: {report.trade_count}, Win Rate: {report.weighted_win_rate * 100:.2f}%")
        print(f"  - Profitable Folds: {profitable_folds} / {len(report.folds)}\n")

    report_payload = {
        "dataset_bars": len(candles),
        "initial_capital_krw": settings.initial_capital_krw,
        "full_period_results": full_results,
        "walk_forward_oos_results": oos_results,
    }

    OUTPUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_REPORT.write_text(json.dumps(report_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Research report successfully saved to {OUTPUT_REPORT}")


if __name__ == "__main__":
    main()
