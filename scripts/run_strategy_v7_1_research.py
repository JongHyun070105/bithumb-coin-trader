"""Execute Strategy V7.1 Point-in-Time Dynamic Universe Research."""

from __future__ import annotations

import json
from pathlib import Path

from bithumb_coin_trader.data import load_candles_csv
from bithumb_coin_trader.dynamic_universe import TOP_UNIVERSE_CANDIDATES
from bithumb_coin_trader.strategy_v7_1_research import run_strategy_v7_1_dynamic_universe_research

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"


def main() -> None:
    print("=" * 80)
    print("  Strategy V7.1 Point-in-Time Dynamic Universe Research (Top 10 / 20 / 30)")
    print("=" * 80)

    universe_candles = {}
    for market in TOP_UNIVERSE_CANDIDATES:
        symbol = market.lower()
        h4_path = DATA_DIR / f"{symbol}-4h-v71.csv"
        if h4_path.exists():
            candles = load_candles_csv(h4_path)
            universe_candles[market] = candles

    print(f"Loaded {len(universe_candles)} eligible assets with 4H historical data.")

    report = run_strategy_v7_1_dynamic_universe_research(
        universe_candles,
        universe_sizes=(10, 20, 30),
    )

    print(f"\n[Development Period: {report['dataset']['dev_period_days']:.1f} days ({report['dataset']['dev_period_weeks']:.1f} weeks)]")
    print(f"Holdout Protection: {report['dataset']['holdout_status']} (180 days embargo)")

    print("\n" + "=" * 80)
    print("  Robustness Comparison: Dynamic Top 10 vs Top 20 (Baseline) vs Top 30")
    print("=" * 80)

    for u_size, regimes in report["universe_size_results"].items():
        print(f"\n▶ Dynamic Universe Size: {u_size}")
        print(f"  {'Fee Regime':<24} {'Mean Return':<14} {'Round Trips':<14} {'Trades / Week':<14}")
        print("  " + "-" * 66)
        for r_name, metrics in regimes.items():
            print(
                f"  {r_name:<24} "
                f"{metrics['mean_return']:>12.2%}  "
                f"{metrics['round_trips']:>12d}  "
                f"{metrics['trades_per_week']:>12.2f}"
            )

    out_file = REPORTS_DIR / "krw-multiverse-strategy-v7-1-research-2026-08-25.json"
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nSaved V7.1 Research Report -> {out_file.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
