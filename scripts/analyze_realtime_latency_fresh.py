"""Compute fresh latency & clock offset distributions from the latest hour raw partitions."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "microstructure" / "raw"


def main() -> None:
    print("=" * 80)
    print("  STRATEGY V9: FRESH REAL-TIME LATENCY & CLOCK OFFSET AUDIT")
    print("=" * 80)

    raw_files = list(RAW_DIR.glob("**/*.jsonl"))
    if not raw_files:
        print("No raw files found.")
        return

    # Filter files modified within the last 2 hours
    now = time.time()
    recent_files = [f for f in raw_files if (now - f.stat().st_mtime) < 7200]
    print(f"Found {len(recent_files)} active partition files in the last 2 hours.")

    exchange_latencies: dict[str, list[float]] = {"bithumb": [], "binance": [], "upbit": []}
    exchange_write_delays: dict[str, list[float]] = {"bithumb": [], "binance": [], "upbit": []}
    negative_counts: dict[str, int] = {"bithumb": 0, "binance": 0, "upbit": 0}

    for f in recent_files:
        parts = f.relative_to(RAW_DIR).parts
        if len(parts) < 3:
            continue
        exch = parts[1].lower()
        if exch not in exchange_latencies:
            continue

        with f.open("r", encoding="utf-8") as handle:
            # Read last 500 lines per file
            lines = handle.readlines()
            for line in lines[-500:]:
                try:
                    rec = json.loads(line)
                    e_ts = rec.get("exchange_ts")
                    l_ts = rec.get("local_recv_ts")
                    w_ts = rec.get("local_write_ts")

                    if e_ts and l_ts:
                        dt_e = datetime.fromisoformat(e_ts)
                        dt_l = datetime.fromisoformat(l_ts)
                        diff_ms = (dt_l - dt_e).total_seconds() * 1000.0
                        if -60_000.0 < diff_ms < 60_000.0:
                            exchange_latencies[exch].append(diff_ms)
                            if diff_ms < 0:
                                negative_counts[exch] += 1

                    if l_ts and w_ts:
                        dt_l = datetime.fromisoformat(l_ts)
                        dt_w = datetime.fromisoformat(w_ts)
                        w_delay_ms = (dt_w - dt_l).total_seconds() * 1000.0
                        if 0 <= w_delay_ms < 10_000.0:
                            exchange_write_delays[exch].append(w_delay_ms)
                except Exception:
                    pass

    print("\n[Fresh Latency & Clock Offset by Exchange (Last 2h Active Data)]")
    for exch in ("bithumb", "binance", "upbit"):
        lats = sorted(exchange_latencies[exch])
        writes = sorted(exchange_write_delays[exch])
        n_neg = negative_counts[exch]
        cnt = len(lats)

        if not lats:
            print(f"\n  [{exch.upper()}] No recent samples")
            continue

        p50 = lats[int(cnt * 0.50)]
        p90 = lats[int(cnt * 0.90)]
        p95 = lats[int(cnt * 0.95)]
        p99 = lats[int(cnt * 0.99)]
        lat_max = lats[-1]
        lat_min = lats[0]

        w_p50 = writes[int(len(writes) * 0.50)] if writes else 0.0
        w_p95 = writes[int(len(writes) * 0.95)] if writes else 0.0

        print(f"\n  [{exch.upper()}] Sample Count: {cnt:,}")
        print(f"    - Clock Offset (T_recv - T_exch) Min: {lat_min:8.2f} ms | Max: {lat_max:8.2f} ms")
        print(f"    - Offset Percentiles : p50={p50:7.2f} ms | p90={p90:7.2f} ms | p95={p95:7.2f} ms | p99={p99:7.2f} ms")
        print(f"    - Negative Offsets   : {n_neg:,} ({n_neg/cnt*100.0:.2f}%) [Exchange Clock Ahead]")
        print(f"    - Local Queue Write  : p50={w_p50:5.2f} ms | p95={w_p95:5.2f} ms [Internal OS Buffer Delay]")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
