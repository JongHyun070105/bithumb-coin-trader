#!/usr/bin/env python3
"""Run the research-only live entry and enhanced-exit comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from bithumb_coin_trader.data import load_candles_csv
from bithumb_coin_trader.live_policy_research import (
    DEFENSIVE_BASELINE_POLICY,
    ENHANCED_EXIT_POLICY,
    FIXED_EXIT_POLICY,
    build_live_policy_report,
    report_digest,
)


DEFAULT_INPUT = Path("data/krw-btc-30m-2026-08-14-wave4.csv")
DEFAULT_OUTPUT = Path("reports/live-policy-research-2026-08-24.json")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    report = build_live_policy_report(load_candles_csv(args.input))
    report["sha256"] = report_digest(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Research artifact: {args.output}")
    for scenario in report["capital_scenarios"]:
        capital = scenario["initial_capital_krw"]
        base = scenario["cost_cases"]["base"]
        stress = scenario["cost_cases"]["double_cost_stress"]
        for name in (
            FIXED_EXIT_POLICY.name,
            DEFENSIVE_BASELINE_POLICY.name,
            ENHANCED_EXIT_POLICY.name,
        ):
            print(
                f"{capital:,} KRW {name}: "
                f"return={base[name]['compounded_return'] * 100:+.2f}% "
                f"MDD={base[name]['maximum_drawdown'] * 100:.2f}% "
                f"trades={base[name]['trade_count']} "
                f"double-cost={stress[name]['compounded_return'] * 100:+.2f}%"
            )
        print(
            "historical best after cost stress: "
            f"{scenario['conclusion']['historical_best_after_cost_stress']}"
        )
    print("automatic promotion: forbidden")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
