"""Measure exact record sizes, throughput, and projected storage from live raw files."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "microstructure" / "raw"


def main() -> None:
    print("=" * 80)
    print("  EXACT STORAGE & THROUGHPUT MEASUREMENT FROM LIVE RAW DATA")
    print("=" * 80)

    raw_files = list(RAW_DIR.glob("**/*.jsonl"))
    if not raw_files:
        print("No raw files found.")
        return

    total_bytes = 0
    total_records = 0
    stream_stats: dict[str, dict[str, int]] = {}

    for f in raw_files:
        f_size = f.stat().st_size
        total_bytes += f_size
        stream = f.parent.name  # orderbook, trade, ticker

        stream_stats.setdefault(stream, {"count": 0, "bytes": 0})
        with f.open("r", encoding="utf-8") as handle:
            for line in handle:
                line_bytes = len(line.encode("utf-8"))
                total_records += 1
                stream_stats[stream]["count"] += 1
                stream_stats[stream]["bytes"] += line_bytes

    print(f"Total Raw Files Analyzed: {len(raw_files)}")
    print(f"Total Records Collected : {total_records:,}")
    print(f"Total Bytes Collected   : {total_bytes:,} bytes ({total_bytes / (1024 * 1024):.2f} MB)")

    avg_bytes_per_rec = (total_bytes / total_records) if total_records > 0 else 0
    print(f"\n[Overall Average Record Size]: {avg_bytes_per_rec:.1f} bytes / record")

    print("\n[Per-Stream Breakdown]")
    for s, data in stream_stats.items():
        c = data["count"]
        b = data["bytes"]
        avg = (b / c) if c > 0 else 0
        print(f"  - {s:10s}: {c:6d} records | {b / (1024 * 1024):.2f} MB | {avg:.1f} bytes/record")

    print("\n[Rigorous Storage Projections at Various Event Rates]")
    for rate in (15, 25, 35, 50):
        bytes_per_sec = rate * avg_bytes_per_rec
        mb_per_hour = (bytes_per_sec * 3600) / (1024 * 1024)
        gb_per_day = (bytes_per_sec * 86400) / (1024 * 1024 * 1024)
        gb_per_month = gb_per_day * 30
        print(f"  - At {rate:2d} events/sec: {mb_per_hour:6.2f} MB/hour | {gb_per_day:5.2f} GB/day | {gb_per_month:6.2f} GB/month")

    print("=" * 80)


if __name__ == "__main__":
    main()
