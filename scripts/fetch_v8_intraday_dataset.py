"""Fetch 15-minute and 1-hour candles for V8 multi-asset intraday research."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time

from bithumb_coin_trader.data import (
    fetch_minute_candles,
    load_candles_csv,
    save_candles_csv,
)
from bithumb_coin_trader.dynamic_universe import TOP_UNIVERSE_CANDIDATES

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def main() -> None:
    print("=" * 80)
    print("  Fetching 15m Intraday Candle Datasets for V8 Research")
    print("=" * 80)

    # Top 10 focus for intraday execution speed and deep liquidity
    target_markets = TOP_UNIVERSE_CANDIDATES[:10]
    print(f"Target Markets ({len(target_markets)}): {target_markets}")

    for market in target_markets:
        sym = market.lower()
        csv_15m_path = DATA_DIR / f"{sym}-15m-v8.csv"
        csv_1h_path = DATA_DIR / f"{sym}-1h-v8.csv"

        # 1. Fetch 15m (10,000 candles = ~104 days)
        if not csv_15m_path.exists():
            print(f"Fetching 15m candles for {market}...")
            try:
                c_15m = fetch_minute_candles(market, unit=15, count=10_000)
                if c_15m:
                    save_candles_csv(csv_15m_path, c_15m)
                    print(f"  -> Saved {len(c_15m)} 15m candles ({c_15m[0].timestamp.isoformat()} to {c_15m[-1].timestamp.isoformat()})")
                time.sleep(0.5)
            except Exception as e:
                print(f"  -> Failed to fetch 15m for {market}: {e}")
        else:
            print(f"15m candles for {market} already exist ({csv_15m_path.name})")

        # 2. Fetch 1h (6,000 candles = 250 days)
        if not csv_1h_path.exists():
            print(f"Fetching 1h candles for {market}...")
            try:
                c_1h = fetch_minute_candles(market, unit=60, count=6_000)
                if c_1h:
                    save_candles_csv(csv_1h_path, c_1h)
                    print(f"  -> Saved {len(c_1h)} 1h candles ({c_1h[0].timestamp.isoformat()} to {c_1h[-1].timestamp.isoformat()})")
                time.sleep(0.5)
            except Exception as e:
                print(f"  -> Failed to fetch 1h for {market}: {e}")
        else:
            print(f"1h candles for {market} already exist ({csv_1h_path.name})")

    print("\nDataset preparation completed.")


if __name__ == "__main__":
    main()
