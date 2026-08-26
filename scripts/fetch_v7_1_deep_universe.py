"""Fetch deep multi-asset universe candles for 20 top Bithumb markets.

Downloads 4-Hour (unit=240) and Daily candles for 20 liquid assets.
"""

from __future__ import annotations

from pathlib import Path
import time

from bithumb_coin_trader.data import (
    fetch_daily_candles,
    fetch_minute_candles,
    save_candles_csv,
)
from bithumb_coin_trader.dynamic_universe import TOP_UNIVERSE_CANDIDATES

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def main() -> None:
    print("=" * 80)
    print("  Fetching Strategy V7.1 Deep Universe Data (20 Top Liquid Assets)")
    print("=" * 80)

    for market in TOP_UNIVERSE_CANDIDATES:
        symbol = market.lower()
        print(f"\n[{market}]")

        # 4-Hour Candles (3,500 bars ~ 580 days)
        h4_path = DATA_DIR / f"{symbol}-4h-v71.csv"
        if not h4_path.exists():
            print(f"  * Fetching 4H candles (3,500 bars)...")
            try:
                h4_candles = fetch_minute_candles(market, unit=240, count=3500)
                save_candles_csv(h4_path, h4_candles)
                print(f"    Saved {len(h4_candles)} 4H bars -> {h4_path.name}")
            except Exception as e:
                print(f"    Error fetching {market} 4H: {e}")
            time.sleep(0.25)
        else:
            print(f"  * 4H candles already exist ({h4_path.name})")

        # Daily Candles (2,400 bars)
        daily_path = DATA_DIR / f"{symbol}-1d-v71.csv"
        if not daily_path.exists():
            print(f"  * Fetching Daily candles (2,400 bars)...")
            try:
                daily_candles = fetch_daily_candles(market, count=2400)
                save_candles_csv(daily_path, daily_candles)
                print(f"    Saved {len(daily_candles)} daily bars -> {daily_path.name}")
            except Exception as e:
                print(f"    Error fetching {market} 1D: {e}")
            time.sleep(0.25)
        else:
            print(f"  * Daily candles already exist ({daily_path.name})")

    print("\n" + "=" * 80)
    print("  Deep Universe Data Collection Completed")
    print("=" * 80)


if __name__ == "__main__":
    main()
