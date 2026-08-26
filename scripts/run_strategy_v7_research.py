"""Execute Strategy V7 Multi-Asset Intraday Alpha Research."""

from __future__ import annotations

import json
from pathlib import Path

from bithumb_coin_trader.data import load_candles_csv
from bithumb_coin_trader.strategy_v7_research import run_strategy_v7_multiverse_backtest

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"

UNIVERSE_FILES = {
    "KRW-BTC": DATA_DIR / "krw-btc-1h-6000.csv",
    "KRW-ETH": DATA_DIR / "krw-eth-1h-6000.csv",
    "KRW-XRP": DATA_DIR / "krw-xrp-1h-6000.csv",
    "KRW-SOL": DATA_DIR / "krw-sol-1h-6000.csv",
    "KRW-DOGE": DATA_DIR / "krw-doge-1h-6000.csv",
}


def main() -> None:
    print("=" * 80)
    print("  Strategy V7 Research Lane: Multi-Asset Intraday Alpha Discovery")
    print("=" * 80)

    universe_candles = {}
    for market, path in UNIVERSE_FILES.items():
        candles = load_candles_csv(path)
        universe_candles[market] = candles
        print(f"Loaded {market}: {len(candles)} 1H bars (Embargo 4,320 bars = 180 days)")

    report = run_strategy_v7_multiverse_backtest(universe_candles)

    print(f"\n[Development Period: {report['dev_period_days']:.1f} days ({report['dev_period_weeks']:.1f} weeks)]")
    print(f"Holdout Protection: {report['holdout_embargo_status']}")

    print("\n" + "=" * 80)
    print("  V7 4-Family Multi-Asset Evaluation Across 5 Top Bithumb Markets")
    print("=" * 80)

    for fam_name, res in report["family_results"].items():
        print(f"\n▶ Strategy Family: {fam_name}")
        print(f"  * Total Universe Trades: {res['total_universe_trades']} trades")
        print(f"  * Trades / Week:        {res['trades_per_week']:.2f} trades/week (Target: 7~20 trades/wk)")
        print(f"  * Overall Profit Factor: {res['overall_profit_factor']:.2f} (Target: >= 1.20)")

        if "market_breakdown" in res:
            print("  * Market-by-Market Breakdown:")
            print(f"    {'Market':<10} {'Trades':<8} {'WinRate':<10} {'Live 0% Ret':<14} {'Normal Ret':<14} {'MDD':<10}")
            print("    " + "-" * 66)
            for m, m_stats in res["market_breakdown"].items():
                print(
                    f"    {m:<10} "
                    f"{m_stats['trades']:>6d}  "
                    f"{m_stats['win_rate']:>8.1%}  "
                    f"{m_stats['zero_fee_return']:>12.2%}  "
                    f"{m_stats['normal_fee_return']:>12.2%}  "
                    f"{m_stats['max_drawdown']:>8.2%}"
                )

    out_file = REPORTS_DIR / "krw-multiverse-strategy-v7-research-2026-08-25.json"
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nSaved V7 Research Report -> {out_file.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
