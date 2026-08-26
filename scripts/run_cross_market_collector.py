"""Run Enterprise Multi-Exchange Microstructure Collector Daemon."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import sys

from bithumb_coin_trader.cross_market_collector import MultiExchangeMicrostructureCollector
from bithumb_coin_trader.dynamic_universe import TOP_UNIVERSE_CANDIDATES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-Exchange Microstructure Collector Daemon")
    parser.add_argument("--bithumb-markets", type=int, default=20, help="Number of Bithumb KRW markets (default: 20)")
    parser.add_argument("--duration", type=float, default=None, help="Duration in seconds (default: run indefinitely)")
    args = parser.parse_args()

    bithumb_mkts = list(TOP_UNIVERSE_CANDIDATES[: args.bithumb_markets])
    binance_syms = ["btcusdt", "ethusdt", "solusdt", "xrpusdt"]
    upbit_mkts = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP"]

    print("=" * 80)
    print("  LAUNCHING MULTI-EXCHANGE MICROSTRUCTURE COLLECTOR DAEMON (V9)")
    print(f"  - Bithumb KRW Markets ({len(bithumb_mkts)}): {bithumb_mkts[:5]} ...")
    print(f"  - Binance Global Benchmark ({len(binance_syms)}): {binance_syms}")
    print(f"  - Upbit Domestic Benchmark ({len(upbit_mkts)}): {upbit_mkts}")
    print(f"  - Mode: {'INDEFINITE (DAEMON)' if args.duration is None else f'{args.duration} SECONDS'}")
    print("=" * 80)

    collector = MultiExchangeMicrostructureCollector(
        bithumb_markets=bithumb_mkts,
        binance_symbols=binance_syms,
        upbit_markets=upbit_mkts,
    )

    try:
        asyncio.run(collector.run_collector(max_duration_seconds=args.duration))
    except KeyboardInterrupt:
        print("\nCollector gracefully stopped.")
    finally:
        print("Flushing final manifests...")
        mfs = collector.generate_all_manifests()
        print(f"Generated {len(mfs)} partition manifests.")


if __name__ == "__main__":
    main()
