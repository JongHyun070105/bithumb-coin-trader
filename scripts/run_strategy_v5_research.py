"""Execute Strategy V5 research pipeline and output comprehensive diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from bithumb_coin_trader.data import load_candles_csv
from bithumb_coin_trader.strategy_v5_research import build_strategy_v5_report
from bithumb_coin_trader.trial_ledger import TrialRecord, append_trial_record

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"


def main() -> None:
    print("=" * 70)
    print("  Strategy V5 Research Lane: Pre-registered Evaluation")
    print("=" * 70)

    btc_csv = DATA_DIR / "krw-btc-1d-2026-08-24-2400.csv"
    eth_csv = DATA_DIR / "krw-eth-1d-2026-08-24-2400.csv"
    xrp_csv = DATA_DIR / "krw-xrp-1d-2026-08-24-2400.csv"

    print("\n[1/4] Loading multi-asset daily candle datasets...")
    btc_candles = load_candles_csv(btc_csv)
    eth_candles = load_candles_csv(eth_csv) if eth_csv.exists() else None
    xrp_candles = load_candles_csv(xrp_csv) if xrp_csv.exists() else None
    print(f"Loaded BTC: {len(btc_candles)} bars")
    if eth_candles:
        print(f"Loaded ETH: {len(eth_candles)} bars")
    if xrp_candles:
        print(f"Loaded XRP: {len(xrp_candles)} bars")

    print("\n[2/4] Running V5 research engine (Nested CV + DSR + Multiple Testing)...")
    report = build_strategy_v5_report(
        btc_candles,
        eth_candles=eth_candles,
        xrp_candles=xrp_candles,
    )

    # Append V5 trials to permanent ledger
    for row in report["direct_development_diagnostics"]:
        append_trial_record(
            TrialRecord(
                trial_id=f"TRIAL-V5-{row['name']}",
                lane="V5_PreRegistered",
                strategy_name=row["name"],
                parameters={"required_history_bars": row["required_history_bars"]},
                dataset_manifest_sha256=report["dataset"]["development"]["sha256"],
                code_hash="v5-canonical-hash",
                created_at=report["generated_at"],
                total_return=row["base"]["total_return"],
                maximum_drawdown=row["base"]["maximum_drawdown"],
                observed_sharpe=row["base"]["sharpe"],
                exposure=row["base"]["exposure"],
                description=f"V5 pre-registered candidate {row['name']}",
            )
        )

    print("\n[3/4] Strategy V5 Direct OOS Performance Summary:")
    print("-" * 70)
    print(f"{'Strategy Name':<35} {'Return':<10} {'MDD':<10} {'Sharpe':<10} {'Cost x3':<10}")
    print("-" * 70)
    for row in report["direct_development_diagnostics"]:
        b = row["base"]
        x3 = row["cost_x3"]
        print(
            f"{row['name']:<35} "
            f"{b['total_return']:>8.2%} "
            f"{b['maximum_drawdown']:>8.2%} "
            f"{b['sharpe']:>8.3f} "
            f"{x3['total_return']:>8.2%}"
        )
    print("-" * 70)

    print("\n[4/4] Multi-Asset Portfolio Simulation (Challenger B):")
    if report.get("multi_asset_diagnostics"):
        m = report["multi_asset_diagnostics"]
        print(f"  Composite Portfolio Return: {m['total_return']:.2%}")
        print(f"  Composite MDD:             {m['maximum_drawdown']:.2%}")
        print(f"  Composite Sharpe:          {m['sharpe']:.3f}")

    print("\n[Nested Outer Folds Evaluation (Bear-Aware)]:")
    for fold in report["nested_outer"]["folds"]:
        bear_tag = " [BEAR FOLD]" if fold["is_bear_fold"] else ""
        pass_tag = "✅ PASSED" if fold["fold_passed"] else "❌ FAILED"
        print(
            f"  Fold {fold['fold']} ({fold['period_start']} ~ {fold['period_end']}){bear_tag}: "
            f"Selected={fold['selected_strategy']}, "
            f"Return={fold['total_return']:.2%}, "
            f"MDD={fold['maximum_drawdown']:.2%}, "
            f"BTC Buy&Hold={fold['btc_buy_hold_return']:.2%} -> {pass_tag}"
        )
    print(f"  All Folds Passed: {report['nested_outer']['all_folds_passed']}")
    print(f"  Stitched Base Return: {report['nested_outer']['base']['total_return']:.2%}")
    print(f"  Stitched Cost x3 Return: {report['nested_outer']['cost_x3']['total_return']:.2%}")

    print("\n[Multiple Testing & Deflated Sharpe Ratio]:")
    stats = report["multiple_testing"]
    print(f"  Total Cumulative Trials in Ledger: {stats['cumulative_trial_count']}")
    print(f"  White Reality Check p-value:       {stats['white_reality_check_vs_cash']['p_value']:.5f}")
    print(f"  CSCV PBO:                          {stats['cscv_pbo']['probability_backtest_overfitting']:.5f}")
    dsr = stats["deflated_sharpe_ratio"]
    print(f"  DSR Observed Sharpe:               {dsr['observed_sharpe']:.3f}")
    print(f"  DSR Expected Max Sharpe:           {dsr['expected_maximum_sharpe']:.3f}")
    print(f"  DSR Probability (Honest Sharpe):   {dsr['probability']:.4f}")

    print("\n[Finalist Gates & Decision]:")
    gates = report["finalist_gates"]
    for k, v in gates["checks"].items():
        print(f"  {k:<38}: {'✅ True' if v else '❌ False'}")
    print(f"\n  All Gates Passed: {gates['all_passed']}")
    print(f"  Final Decision:   {report['selection']['research_finalist']}")
    print(f"  Rationale:        {report['selection']['decision_rationale']}")

    # Save report
    out_json = REPORTS_DIR / "krw-btc-strategy-v5-research-2026-08-25.json"
    with out_json.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nReport saved to {out_json.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
