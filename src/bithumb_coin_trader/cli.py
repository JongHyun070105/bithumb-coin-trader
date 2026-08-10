from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .backtest import BacktestResult, Backtester
from .config import TradingSettings
from .data import dataset_manifest, fetch_daily_candles, load_candles_csv, save_candles_csv
from .models import Candle
from .research import ProjectResearchReport, run_chronological_research
from .strategy import TrendBreakoutStrategy


def _metric(result: BacktestResult) -> dict[str, Any]:
    return {
        "initial_equity_krw": round(result.initial_equity, 2),
        "final_equity_krw": round(result.final_equity, 2),
        "total_return": round(result.total_return, 8),
        "max_drawdown": round(result.max_drawdown, 8),
        "sharpe": round(result.sharpe, 6),
        "trade_count": result.trade_count,
        "win_rate": round(result.win_rate, 8),
        "exposure": round(result.exposure, 8),
    }


def _report(report: ProjectResearchReport) -> dict[str, Any]:
    return {
        "fold_count": len(report.folds),
        "compounded_return": round(report.compounded_return, 8),
        "maximum_drawdown": round(report.maximum_drawdown, 8),
        "mean_sharpe": round(report.mean_sharpe, 6),
        "trade_count": report.trade_count,
        "weighted_win_rate": round(report.weighted_win_rate, 8),
        "profitable_folds": sum(fold.result.total_return > 0 for fold in report.folds),
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


def _manifest(candles: Sequence[Candle]) -> dict[str, Any]:
    manifest = dataset_manifest(candles)
    return {
        "schema_version": manifest.schema_version,
        "market": manifest.market,
        "candle_count": manifest.candle_count,
        "start_at": manifest.start_at.isoformat() if manifest.start_at else None,
        "end_at": manifest.end_at.isoformat() if manifest.end_at else None,
        "sha256": manifest.sha256,
    }


def _load_or_fetch(args: argparse.Namespace) -> list[Candle]:
    if args.input:
        return load_candles_csv(args.input)
    return fetch_daily_candles(args.market, args.count, timeout=args.timeout)


def _promotion_status(base: ProjectResearchReport, stress: ProjectResearchReport) -> dict[str, Any]:
    checks = {
        "at_least_six_folds": len(base.folds) >= 6,
        "at_least_thirty_oos_trades": base.trade_count >= 30,
        "majority_profitable_folds": sum(f.result.total_return > 0 for f in base.folds) > len(base.folds) / 2,
        "positive_oos_return_after_costs": base.compounded_return > 0,
        "positive_double_cost_stress": stress.compounded_return > 0,
    }
    return {
        "status": "PAPER_CANDIDATE" if all(checks.values()) else "RESEARCH_ONLY",
        "checks": checks,
    }


def command_fetch(args: argparse.Namespace) -> int:
    candles = fetch_daily_candles(args.market, args.count, timeout=args.timeout)
    save_candles_csv(args.output, candles)
    print(
        json.dumps(
            {
                "market": args.market,
                "candles": len(candles),
                "output": str(args.output),
                "dataset": _manifest(candles),
            }
        )
    )
    return 0


def command_research(args: argparse.Namespace) -> int:
    candles = _load_or_fetch(args)
    base_settings = TradingSettings()
    stress_settings = TradingSettings(fee_rate=0.005, slippage_bps=10)
    base = run_chronological_research(
        candles,
        train_size=args.train_size,
        test_size=args.test_size,
        settings=base_settings,
        allow_short=False,
    )
    stress = run_chronological_research(
        candles,
        train_size=args.train_size,
        test_size=args.test_size,
        settings=stress_settings,
        allow_short=False,
    )
    strategy = TrendBreakoutStrategy()
    full = Backtester(base_settings, allow_short=False).run(candles, strategy.generate(candles))
    payload = {
        "market": candles[0].market,
        "period": [candles[0].timestamp.isoformat(), candles[-1].timestamp.isoformat()],
        "candles": len(candles),
        "dataset": _manifest(candles),
        "mode": "bithumb_spot_long_flat",
        "full_sample_context_only": _metric(full),
        "walk_forward": _report(base),
        "double_cost_stress": _report(stress),
        "promotion": _promotion_status(base, stress),
        "warning": "Backtests are research evidence, not a profit guarantee or live-trading approval.",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_signal(args: argparse.Namespace) -> int:
    candles = _load_or_fetch(args)
    strategy = TrendBreakoutStrategy()
    research_signal = strategy.generate(candles)[-1]
    payload = {
        "market": candles[-1].market,
        "candle_timestamp": candles[-1].timestamp.isoformat(),
        "research_signal": research_signal.name,
        "bithumb_spot_target": research_signal.name,
        "order_submitted": False,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Research-first Bithumb coin trader")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="fetch public daily candles to an explicit CSV path")
    fetch.add_argument("--market", default="KRW-BTC")
    fetch.add_argument("--count", type=int, default=1_000)
    fetch.add_argument("--timeout", type=float, default=20.0)
    fetch.add_argument("--output", type=Path, required=True)
    fetch.set_defaults(handler=command_fetch)

    research = subparsers.add_parser("research", help="run fixed-parameter walk-forward research")
    research.add_argument("--market", default="KRW-BTC")
    research.add_argument("--count", type=int, default=1_000)
    research.add_argument("--timeout", type=float, default=20.0)
    research.add_argument("--input", type=Path)
    research.add_argument("--train-size", type=int, default=400)
    research.add_argument("--test-size", type=int, default=100)
    research.set_defaults(handler=command_research)

    signal = subparsers.add_parser("signal", help="print the current research signal without ordering")
    signal.add_argument("--market", default="KRW-BTC")
    signal.add_argument("--count", type=int, default=200)
    signal.add_argument("--timeout", type=float, default=20.0)
    signal.add_argument("--input", type=Path)
    signal.set_defaults(handler=command_signal)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
