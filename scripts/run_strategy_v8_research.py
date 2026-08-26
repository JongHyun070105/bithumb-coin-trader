"""Run Strategy V8 Market-Wide Intraday Research."""

from __future__ import annotations

import json
from pathlib import Path

from bithumb_coin_trader.data import load_candles_csv
from bithumb_coin_trader.dynamic_universe import TOP_UNIVERSE_CANDIDATES
from bithumb_coin_trader.strategy_v8_research import run_v8_family_research

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def main() -> None:
    print("=" * 80)
    print("  Loading Multi-Asset Intraday Data for Strategy V8 Research")
    print("=" * 80)

    # Use 1H as Entry TF and 4H as Context TF for deep 403-day development dataset
    target_markets = TOP_UNIVERSE_CANDIDATES[:10]
    entry_candles = {}
    context_candles = {}

    for market in target_markets:
        sym = market.lower()
        # 1H candles (6,000 bars = 250 days) or 4H candles (3,500 bars = 583 days)
        p_1h = DATA_DIR / f"{sym}-1h-v8.csv"
        p_4h = DATA_DIR / f"{sym}-4h-v71.csv"

        if p_1h.exists() and p_4h.exists():
            entry_candles[market] = load_candles_csv(p_1h)
            context_candles[market] = load_candles_csv(p_4h)
        elif p_4h.exists():
            # If 1h not found, fallback to 4h for both
            context_candles[market] = load_candles_csv(p_4h)
            entry_candles[market] = load_candles_csv(p_4h)

    print(f"Loaded {len(entry_candles)} markets for V8 research.")
    results = run_v8_family_research(entry_candles, context_candles)

    out_file = ROOT / "reports" / "krw-multiverse-strategy-v8-research-2026-08-25.json"
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print("\n" + "=" * 80)
    print(f"  V8 RESEARCH COMPLETE. Results saved to {out_file.relative_to(ROOT)}")
    print(f"  Best Selected Strategy: {results['best_strategy']}")
    print("=" * 80)


if __name__ == "__main__":
    main()
