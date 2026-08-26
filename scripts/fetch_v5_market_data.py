"""Fetch 2,400 daily candles for KRW-ETH and KRW-XRP for V5 multi-asset research."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

from bithumb_coin_trader.data import fetch_daily_candles, load_candles_csv, save_candles_csv

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
BTC_CSV = DATA_DIR / "krw-btc-1d-2026-08-24-2400.csv"


def main() -> None:
    btc_candles = load_candles_csv(BTC_CSV)
    count = len(btc_candles)
    start_time = btc_candles[0].timestamp
    end_time = btc_candles[-1].timestamp
    print(f"BTC Reference: {count} candles, from {start_time.isoformat()} to {end_time.isoformat()}")

    # End timestamp + 1 day to ensure we include end_time candle
    to_cursor = end_time.replace(tzinfo=timezone.utc)

    for market in ("KRW-ETH", "KRW-XRP"):
        target_csv = DATA_DIR / f"{market.lower()}-1d-2026-08-24-2400.csv"
        print(f"\nFetching {count} daily candles for {market}...")
        try:
            # We fetch using to=end_time + 1 day to align with BTC dataset
            candles = fetch_daily_candles(market, count=count, to=to_cursor + btc_candles[1].timestamp - btc_candles[0].timestamp)
            print(f"Fetched {len(candles)} candles. Range: {candles[0].timestamp.isoformat()} ~ {candles[-1].timestamp.isoformat()}")
            save_candles_csv(target_csv, candles)
            print(f"Saved to {target_csv.relative_to(DATA_DIR.parent)}")
        except Exception as exc:
            print(f"Error fetching {market}: {exc}", file=sys.stderr)
            # Try without `to` cursor to get the most recent candles
            try:
                print("Retrying with default cursor...")
                candles = fetch_daily_candles(market, count=count)
                save_candles_csv(target_csv, candles)
                print(f"Saved {len(candles)} candles to {target_csv.relative_to(DATA_DIR.parent)}")
            except Exception as retry_exc:
                print(f"Fatal error fetching {market}: {retry_exc}", file=sys.stderr)
                sys.exit(1)


if __name__ == "__main__":
    main()
