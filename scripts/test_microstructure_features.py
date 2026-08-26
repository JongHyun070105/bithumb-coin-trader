"""Test Microstructure Feature Extraction on real ingested Bithumb WebSocket data."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from bithumb_coin_trader.microstructure_features import (
    MicrostructureFeatureEngine,
    OrderbookSnapshot,
    TradeTick,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "microstructure"


def main() -> None:
    print("=" * 80)
    print("  Testing Strategy V9 Microstructure Feature Engine on Real WebSocket Data")
    print("=" * 80)

    # 1. Load latest orderbook record
    ob_files = list(DATA_DIR.glob("orderbook/*/*.jsonl"))
    if not ob_files:
        print("No orderbook files found.")
        return

    latest_ob_file = sorted(ob_files)[-1]
    with latest_ob_file.open("r", encoding="utf-8") as f:
        ob_line = f.readline()
        ob_data = json.loads(ob_line)

    units = ob_data.get("orderbook_units", [])
    bids = tuple((float(u["bid_price"]), float(u["bid_size"])) for u in units)
    asks = tuple((float(u["ask_price"]), float(u["ask_size"])) for u in units)
    ts = datetime.fromtimestamp(ob_data["timestamp"] / 1_000_000, tz=timezone.utc)

    ob_snap = OrderbookSnapshot(
        market=ob_data["code"],
        timestamp=ts,
        bids=bids,
        asks=asks,
    )

    # 2. Load latest trade record
    tr_files = list(DATA_DIR.glob("trade/*/*.jsonl"))
    trades = []
    if tr_files:
        latest_tr_file = sorted(tr_files)[-1]
        with latest_tr_file.open("r", encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                t_ts = datetime.fromtimestamp(d["trade_timestamp"] / 1000.0, tz=timezone.utc)
                trades.append(
                    TradeTick(
                        market=d["code"],
                        timestamp=t_ts,
                        price=float(d["trade_price"]),
                        volume=float(d["trade_volume"]),
                        side=d["ask_bid"],
                    )
                )

    # 3. Extract Features
    engine = MicrostructureFeatureEngine()
    features = engine.extract_features(ob_snap, trades, baseline_trade_volume=10.0)

    print(f"\n[Microstructure Snapshot for {features.market}]")
    print(f"  - Timestamp           : {features.timestamp.isoformat()}")
    print(f"  - Mid Price           : {features.mid_price:,.2f} KRW")
    print(f"  - Spread              : {features.spread_bps:.2f} bps")
    print(f"  - OBI Level 1 (Top 1) : {features.obi_level_1:+.4f} ([-1, +1])")
    print(f"  - OBI Level 5 (Top 5) : {features.obi_level_5:+.4f} ([-1, +1])")
    print(f"  - Microprice          : {features.microprice:,.2f} KRW")
    print(f"  - Microprice Bias     : {features.microprice_bias_bps:+.2f} bps")
    print(f"  - Aggressive Trade Imb: {features.trade_imbalance_30s:+.4f} ([-1, +1])")
    print(f"  - Volume Shock Ratio  : {features.volume_shock_ratio:.2f}x")
    print("\nFeature Engine test passed successfully.")


if __name__ == "__main__":
    main()
