"""Execute Strategy V6 Fee-Regime & Core+Satellite Portfolio Research."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from bithumb_coin_trader.data import load_candles_csv
from bithumb_coin_trader.strategy_v6_research import build_strategy_v6_report

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"


def main() -> None:
    print("=" * 80)
    print("  Strategy V6 Research Lane: Fee-Regimes & Core+Satellite Architecture")
    print("=" * 80)

    btc_csv = DATA_DIR / "krw-btc-1d-2026-08-24-2400.csv"
    eth_csv = DATA_DIR / "krw-eth-1d-2026-08-24-2400.csv"
    xrp_csv = DATA_DIR / "krw-xrp-1d-2026-08-24-2400.csv"

    btc_candles = load_candles_csv(btc_csv)
    eth_candles = load_candles_csv(eth_csv) if eth_csv.exists() else None
    xrp_candles = load_candles_csv(xrp_csv) if xrp_csv.exists() else None

    print(f"Loaded BTC candles: {len(btc_candles)} bars (Dev: 2,220 bars, Sealed: 180 bars untouched)")

    report = build_strategy_v6_report(
        btc_candles,
        eth_candles=eth_candles,
        xrp_candles=xrp_candles,
    )

    # 1. Standalone Core across Fee Regimes
    print("\n[1/4] Core Standalone (V4 Adaptive Donchian) across Fee Regimes:")
    print("-" * 80)
    print(f"{'Fee Regime':<26} {'Return':<10} {'CAGR':<10} {'MDD':<10} {'Sharpe':<10} {'Trades/Yr':<10}")
    print("-" * 80)
    for r_name, metrics in report["core_standalone"].items():
        print(
            f"{r_name:<26} "
            f"{metrics['total_return']:>8.2%} "
            f"{metrics['cagr']:>8.2%} "
            f"{metrics['max_drawdown']:>8.2%} "
            f"{metrics['sharpe']:>8.3f} "
            f"{metrics['trades_per_year']:>8.2f}"
        )

    # 2. Standalone Satellites across Fee Regimes
    print("\n[2/4] Standalone Satellite Candidates across Fee Regimes:")
    print("-" * 80)
    for sat_name, regimes in report["satellite_standalone"].items():
        print(f"\n  * Candidate: {sat_name}")
        print(f"    {'Fee Regime':<24} {'Return':<10} {'CAGR':<10} {'MDD':<10} {'Sharpe':<10} {'Trades/Yr':<10}")
        print("    " + "-" * 74)
        for r_name, metrics in regimes.items():
            print(
                f"    {r_name:<24} "
                f"{metrics['total_return']:>8.2%} "
                f"{metrics['cagr']:>8.2%} "
                f"{metrics['max_drawdown']:>8.2%} "
                f"{metrics['sharpe']:>8.3f} "
                f"{metrics['trades_per_year']:>8.2f}"
            )

    # 3. Composite Core(70%) + Satellite(30%) Portfolios
    print("\n[3/4] Composite Core(70%) + Satellite(30%) Portfolio Performance:")
    print("=" * 80)
    for port_name, regimes in report["composite_portfolios"].items():
        print(f"\n  ▶ Portfolio: {port_name}")
        print(f"    {'Fee Regime':<24} {'Return':<10} {'CAGR':<10} {'MDD':<10} {'Sharpe':<10} {'Trades/Yr':<10}")
        print("    " + "-" * 74)
        for r_name, metrics in regimes.items():
            print(
                f"    {r_name:<24} "
                f"{metrics['total_return']:>8.2%} "
                f"{metrics['cagr']:>8.2%} "
                f"{metrics['max_drawdown']:>8.2%} "
                f"{metrics['sharpe']:>8.3f} "
                f"{metrics['trades_per_year']:>8.2f}"
            )

    # 4. Top Portfolio Allocation Sensitivity
    top_sat = report["top_satellite_selected"]
    print(f"\n[4/4] Allocation Ratio Sensitivity for Top Portfolio (Core vs {top_sat}):")
    print("-" * 80)
    for ratio_name, regimes in report["allocation_sensitivity"].items():
        print(f"\n  * Allocation: {ratio_name}")
        for r_name, metrics in regimes.items():
            print(
                f"    {r_name:<20}: "
                f"Return = {metrics['total_return']:>7.2%}, "
                f"CAGR = {metrics['cagr']:>7.2%}, "
                f"MDD = {metrics['max_drawdown']:>7.2%}, "
                f"Sharpe = {metrics['sharpe']:>6.3f}, "
                f"Trades/Yr = {metrics['trades_per_year']:>5.2f}"
            )

    # 5. Pre-registered Gate Verdict
    print("\n" + "=" * 80)
    print("  Pre-registered Strategy V6 Gate Verdict")
    print("=" * 80)
    gates = report["finalist_gates"]
    print(f"Selected Portfolio: {gates['selected_portfolio']}")
    for k, v in gates["checks"].items():
        print(f"  {k:<38}: {'✅ True' if v else '❌ False'}")
    print(f"\nAll Gates Passed: {gates['all_passed']}")
    print(f"Recommended Decision: {report['decision']['recommended_portfolio']}")
    print(f"Holdout Protection Status: {report['dataset']['holdout_status']}")

    out_file = REPORTS_DIR / "krw-btc-strategy-v6-research-2026-08-25.json"
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nReport saved to {out_file.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
