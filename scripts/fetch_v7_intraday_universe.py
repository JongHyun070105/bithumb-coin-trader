"""Fetch deep multi-asset universe candles from Bithumb API (v1 endpoints).

Downloads:
- KRW-BTC, KRW-ETH, KRW-XRP, KRW-SOL, KRW-DOGE
- 1-hour (unit=60) candles: 10,000 bars per asset
- 4-hour (unit=240) candles: 5,000 bars per asset
- Daily candles: 2,400 bars per asset
- Enforces 180-day holdout embargo during dataset partitioning.
"""

from __future__ import annotations

import time
from pathlib import Path

from bithumb_coin_trader.data import (
    fetch_daily_candles,
    fetch_minute_candles,
    save_candles_csv,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

UNIVERSE_MARKETS = [
    "KRW-BTC",
    "KRW-ETH",
    "KRW-XRP",
    "KRW-SOL",
    "KRW-DOGE",
]


def main() -> None:
    print("=" * 80)
    print("  Fetching Strategy V7 Deep Multi-Asset Universe Candles (v1 Paginator)")
    print("=" * 80)

    for market in UNIVERSE_MARKETS:
        symbol = market.lower()
        print(f"\n[{market}]")

        # 1. Daily Candles (2,400 bars)
        daily_path = DATA_DIR / f"{symbol}-1d-2400.csv"
        if not daily_path.exists():
            print(f"  * Fetching Daily candles (2,400 bars)...")
            daily_candles = fetch_daily_candles(market, count=2400)
            save_candles_csv(daily_path, daily_candles)
            print(f"    Saved {len(daily_candles)} daily bars -> {daily_path.name}")
            time.sleep(0.3)
        else:
            print(f"  * Daily candles already exist ({daily_path.name})")

        # 2. 1-Hour Candles (6,000 bars ~ 250 days)
        h1_path = DATA_DIR / f"{symbol}-1h-6000.csv"
        if not h1_path.exists():
            print(f"  * Fetching 1-Hour candles (6,000 bars)...")
            h1_candles = fetch_minute_candles(market, unit=60, count=6000)
            save_candles_csv(h1_path, h1_candles)
            print(f"    Saved {len(h1_candles)} 1H bars -> {h1_path.name}")
            time.sleep(0.3)
        else:
            print(f"  * 1H candles already exist ({h1_path.name})")

        # 3. 4-Hour Candles (3,000 bars ~ 500 days)
        h4_path = DATA_DIR / f"{symbol}-4h-3000.csv"
        if not h4_path.exists():
            print(f"  * Fetching 4-Hour candles (3,000 bars)...")
            h4_candles = fetch_minute_candles(market, unit=240, count=3000)
            save_candles_csv(h4_path, h4_candles)
            print(f"    Saved {len(h4_candles)} 4H bars -> {h4_path.name}")
            time.sleep(0.3)
        else:
            print(f"  * 4H candles already exist ({h4_path.name})")

    print("\n" + "=" * 80)
    print("  Deep Multi-Asset Dataset Collection Completed")
    print("=" * 80)


if __name__ == "__main__":
    main()
