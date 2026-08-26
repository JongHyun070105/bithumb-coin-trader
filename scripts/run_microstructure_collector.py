"""Run Bithumb Real-Time Market Microstructure Collector Daemon."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

from bithumb_coin_trader.dynamic_universe import TOP_UNIVERSE_CANDIDATES
from bithumb_coin_trader.microstructure_collector import run_standalone_collector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bithumb WebSocket Microstructure Collector")
    parser.add_argument("--markets-count", type=int, default=20, help="Number of top liquid markets to collect (10~30)")
    parser.add_argument("--duration", type=float, default=None, help="Duration in seconds (default: run indefinitely)")
    args = parser.parse_args()

    markets = list(TOP_UNIVERSE_CANDIDATES[: args.markets_count])
    print("=" * 80)
    print("  Starting Bithumb WebSocket Microstructure Collector (Strategy V9)")
    print(f"  Target Markets ({len(markets)}): {markets}")
    print(f"  Duration: {'Indefinite (Daemon)' if args.duration is None else f'{args.duration} seconds'}")
    print("=" * 80)

    try:
        run_standalone_collector(markets, duration=args.duration)
    except KeyboardInterrupt:
        print("\nCollector stopped by user.")


if __name__ == "__main__":
    main()
