"""Run Strategy V8.1 Long-History Dynamic-Universe Robustness Validation."""

from __future__ import annotations

import json
from pathlib import Path

from bithumb_coin_trader.data import load_candles_csv
from bithumb_coin_trader.dynamic_universe import TOP_UNIVERSE_CANDIDATES
from bithumb_coin_trader.strategy_v8_1_robustness import run_v8_1_robustness_validation

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def main() -> None:
    print("=" * 80)
    print("  Loading 20-Asset Deep History Candles for Strategy V8.1")
    print("=" * 80)

    candles_by_market = {}
    for market in TOP_UNIVERSE_CANDIDATES:
        sym = market.lower()
        p_4h = DATA_DIR / f"{sym}-4h-v71.csv"
        if p_4h.exists():
            candles_by_market[market] = load_candles_csv(p_4h)

    print(f"Loaded {len(candles_by_market)} markets (4H candles).")
    results = run_v8_1_robustness_validation(candles_by_market)

    out_file = ROOT / "reports" / "krw-multiverse-strategy-v8-1-robustness-2026-08-25.json"
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print("\n" + "=" * 80)
    print(f"  V8.1 ROBUSTNESS VALIDATION COMPLETE. Results saved to {out_file.relative_to(ROOT)}")
    print(f"  LOAO Pass: {results['loao_pass']} | Quarterly Win Rate: {results['quarterly_win_rate']:.1%}")
    print("=" * 80)


if __name__ == "__main__":
    main()
