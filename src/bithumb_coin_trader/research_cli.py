"""Command Line Interface for Microstructure Research and Paper Simulation (P24).

Provides commands:
- verify-ledger: cryptographically verifies the research experiment hash-chain ledger.
- run-synthetic-sim: runs an end-to-end replay simulation on synthetic microstructure data.
- power-plan: computes required sample size and detectable effect size for given horizon.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from .experiment_runner import GovernedExperimentRunner
from .sample_size_planner import compute_required_sample_size, compute_minimum_detectable_sharpe
from .synthetic_market import SignalMarketGenerator
from .replay import MultiStreamReplay
from .risk_engine import RiskEngine
from .paper_engine import PaperPortfolio


def cmd_verify_ledger(args: argparse.Namespace) -> int:
    ledger_path = Path(args.ledger)
    if not ledger_path.exists():
        print(f"Ledger file not found: {ledger_path}")
        return 1
    try:
        runner = GovernedExperimentRunner(ledger_path)
        runner.verify_ledger_chain()
        print(f"SUCCESS: Ledger chain verified ({len(runner._entries)} entries). No tampering detected.")
        return 0
    except Exception as exc:
        print(f"FAILED: Ledger verification failed: {exc}")
        return 2


def cmd_power_plan(args: argparse.Namespace) -> int:
    n = compute_required_sample_size(
        target_sharpe_per_period=args.sharpe,
        alpha=args.alpha,
        power=args.power,
        autocorrelation_rho=args.rho,
    )
    print(f"Required observations: {n:,} (alpha={args.alpha}, power={args.power}, rho={args.rho})")
    return 0


def cmd_run_synthetic_sim(args: argparse.Namespace) -> int:
    gen = SignalMarketGenerator(initial_price=100_000_000.0, seed=42)
    books, signals = gen.generate_signal_orderbooks(count=args.count)
    replay = MultiStreamReplay([iter(books)])
    events = list(replay)
    print(f"Generated and replayed {len(events)} synthetic microstructure events successfully.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Microstructure Research & Paper CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # verify-ledger
    p_vl = sub.add_parser("verify-ledger", help="Verify cryptographic hash-chain of experiment ledger")
    p_vl.add_argument("--ledger", default="evidence/research/governed_experiment_ledger.json")

    # power-plan
    p_pp = sub.add_parser("power-plan", help="Compute sample size requirements")
    p_pp.add_argument("--sharpe", type=float, default=0.05, help="Target Sharpe per observation")
    p_pp.add_argument("--alpha", type=float, default=0.01, help="Significance level")
    p_pp.add_argument("--power", type=float, default=0.80, help="Statistical power (1-beta)")
    p_pp.add_argument("--rho", type=float, default=0.20, help="Autocorrelation coefficient")

    # run-synthetic-sim
    p_sim = sub.add_parser("run-synthetic-sim", help="Run deterministic synthetic market simulation")
    p_sim.add_argument("--count", type=int, default=100, help="Number of orderbook events")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "verify-ledger":
        return cmd_verify_ledger(args)
    elif args.command == "power-plan":
        return cmd_power_plan(args)
    elif args.command == "run-synthetic-sim":
        return cmd_run_synthetic_sim(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
