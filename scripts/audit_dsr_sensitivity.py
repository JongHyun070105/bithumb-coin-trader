#!/usr/bin/env python3
"""Audit DSR sensitivity across effective trial counts (N).

Calculates Expected Maximum Sharpe Ratio and Deflated Sharpe Ratio (DSR) probability
across a spectrum of trial counts N to perform sensitivity analysis under Bailey & Lopez de Prado (2014).

Mathematically demonstrates:
1. Expected Maximum Sharpe is non-decreasing in N.
2. DSR probability is non-increasing in N (more trials -> higher selection hurdle -> lower DSR probability).
"""

from __future__ import annotations

import argparse
import json
from math import isfinite
from pathlib import Path
import sys
from typing import Sequence

# Add src to path
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bithumb_coin_trader.research_statistics import deflated_sharpe_ratio, DeflatedSharpeResult


DEFAULT_N_SPECTRUM = (1, 2, 5, 10, 20, 40, 77, 100, 200)


def compute_dsr_sensitivity(
    returns: Sequence[float],
    trial_sharpes: Sequence[float],
    n_values: Sequence[int] = DEFAULT_N_SPECTRUM,
) -> list[dict[str, float | int]]:
    """Compute DSR metrics for a sequence of trial counts N."""
    results = []
    prev_benchmark = -float("inf")
    prev_prob = float("inf")

    for n in sorted(set(n_values)):
        if n < 1:
            continue
        dsr: DeflatedSharpeResult = deflated_sharpe_ratio(
            returns=returns,
            trial_sharpes=trial_sharpes,
            trial_count=n,
        )
        row = {
            "trial_count_N": n,
            "observed_sharpe": dsr.observed_sharpe,
            "expected_max_sharpe": dsr.expected_maximum_sharpe,
            "dsr_probability": dsr.probability,
        }
        results.append(row)

        # Monotonicity assertions
        assert dsr.expected_maximum_sharpe >= prev_benchmark - 1e-9, (
            f"Expected max Sharpe non-monotonic at N={n}: {dsr.expected_maximum_sharpe} < {prev_benchmark}"
        )
        assert dsr.probability <= prev_prob + 1e-9, (
            f"DSR probability non-monotonic at N={n}: {dsr.probability} > {prev_prob}"
        )
        prev_benchmark = dsr.expected_maximum_sharpe
        prev_prob = dsr.probability

    return results


def render_markdown_table(rows: Sequence[dict[str, float | int]]) -> str:
    lines = [
        "| Trial Count (N) | Observed Sharpe | Expected Max Sharpe E[max(SR)] | DSR Probability | Selection Penalty Hurdle |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    for r in rows:
        n = r["trial_count_N"]
        obs = r["observed_sharpe"]
        exp_max = r["expected_max_sharpe"]
        prob = r["dsr_probability"]
        hurdle_text = f"+{exp_max:.4f}" if exp_max > 0 else "0.0000 (No penalty)"
        lines.append(f"| {n:4d} | {obs:15.4f} | {exp_max:30.4f} | {prob:15.4%} | {hurdle_text:24s} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--returns-file", type=Path, help="JSON file containing array of candidate period returns")
    parser.add_argument("--ledger-file", type=Path, help="JSONL trial ledger file containing Sharpe ratios")
    parser.add_argument("--output-json", type=Path, help="Path to write JSON sensitivity report")
    parser.add_argument("--custom-n", type=int, nargs="+", help="Custom trial count N values to evaluate")
    args = parser.parse_args()

    # Default synthetic returns and sharpes if not supplied
    if args.returns_file and args.returns_file.exists():
        returns = json.loads(args.returns_file.read_text(encoding="utf-8"))
    else:
        # Canonical baseline synthetic: ~250 periods, mean ~0.0008, vol ~0.01 (annualized Sharpe ~2.0)
        returns = [0.0008 + (0.01 if i % 2 == 0 else -0.009) for i in range(250)]

    trial_sharpes = []
    if args.ledger_file and args.ledger_file.exists():
        for line in args.ledger_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            sh = item.get("sharpe") or item.get("sharpe_ratio")
            if sh is not None and isfinite(float(sh)):
                trial_sharpes.append(float(sh))

    if not trial_sharpes:
        # Representative trial distribution: 77 trials with mean 0.5, std 0.6
        trial_sharpes = [0.5 + 0.6 * ((i - 38) / 38.0) for i in range(77)]

    n_values = args.custom_n if args.custom_n else DEFAULT_N_SPECTRUM
    rows = compute_dsr_sensitivity(returns, trial_sharpes, n_values)

    md_table = render_markdown_table(rows)
    print("=== DEFLATED SHARPE RATIO (DSR) SENSITIVITY TABLE ===")
    print(md_table)
    print("\nMATHEMATICAL DIRECTION OF N:")
    print("- As N increases, Expected Maximum Sharpe monotonically INCREASES.")
    print("- As N increases, DSR Probability monotonically DECREASES (selection bias hurdle grows).")
    print("- Consequently, smaller effective N yields a WEAKER hurdle (higher DSR probability), NOT a larger penalty.")

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "observations": len(returns),
            "trial_sharpes_count": len(trial_sharpes),
            "sensitivity_rows": rows,
        }
        args.output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nSaved sensitivity report to {args.output_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
