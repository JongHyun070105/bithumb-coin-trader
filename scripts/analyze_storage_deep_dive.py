"""Deep-dive storage throughput, record size, stream breakdown, and disk exhaustion analysis."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import time

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "microstructure"
RAW_DIR = DATA_DIR / "raw"


def main() -> None:
    print("=" * 80)
    print("  STRATEGY V9: DEEP-DIVE REAL-TIME STORAGE & ROOT CAUSE ANALYSIS")
    print("=" * 80)

    now = time.time()
    raw_files = list(RAW_DIR.glob("**/*.jsonl"))
    if not raw_files:
        print("No raw files found.")
        return

    # Total bytes & file timestamps
    total_bytes = 0
    file_mtimes = []
    file_ctimes = []

    exchange_stats: dict[str, dict[str, Any]] = {}
    stream_stats: dict[str, dict[str, Any]] = {}
    pair_stats: dict[str, dict[str, Any]] = {}

    for f in raw_files:
        st = f.stat()
        sz = st.st_size
        total_bytes += sz
        file_mtimes.append(st.st_mtime)
        file_ctimes.append(st.st_ctime)

        parts = f.relative_to(RAW_DIR).parts
        if len(parts) >= 3:
            exch = parts[1]
            stream = parts[2]
            key = f"{exch}:{stream}"

            exchange_stats.setdefault(exch, {"bytes": 0, "files": 0})
            exchange_stats[exch]["bytes"] += sz
            exchange_stats[exch]["files"] += 1

            stream_stats.setdefault(stream, {"bytes": 0, "files": 0})
            stream_stats[stream]["bytes"] += sz
            stream_stats[stream]["files"] += 1

            pair_stats.setdefault(key, {"bytes": 0, "files": 0})
            pair_stats[key]["bytes"] += sz
            pair_stats[key]["files"] += 1

    first_file_time = min(file_ctimes) if file_ctimes else now
    latest_file_time = max(file_mtimes) if file_mtimes else now
    elapsed_seconds = max(1.0, latest_file_time - first_file_time)
    elapsed_hours = elapsed_seconds / 3600.0

    print(f"\n[1. Time & Filesystem Scope]")
    print(f"  - First Raw File Created : {datetime.fromtimestamp(first_file_time, tz=timezone.utc).isoformat()}")
    print(f"  - Latest Raw File Mod    : {datetime.fromtimestamp(latest_file_time, tz=timezone.utc).isoformat()}")
    print(f"  - Elapsed Collection Time: {elapsed_hours:.2f} hours ({elapsed_seconds/60:.1f} minutes, {elapsed_seconds:.0f} seconds)")
    print(f"  - Total Raw Files Count  : {len(raw_files)} files")
    print(f"  - Total Raw Disk Usage   : {total_bytes / (1024 * 1024):,.2f} MB ({total_bytes / (1024 * 1024 * 1024):.3f} GB)")

    # Storage rates
    mb_per_hour = (total_bytes / (1024 * 1024)) / elapsed_hours
    gb_per_day = mb_per_hour * 24.0 / 1024.0
    proj_72h_gb = gb_per_day * 3.0
    proj_7d_gb = gb_per_day * 7.0
    proj_30d_gb = gb_per_day * 30.0

    print(f"\n[2. Observed Storage Throughput vs Projections]")
    print(f"  - Observed Ingestion Rate: {mb_per_hour:,.2f} MB/hour ({mb_per_hour/60:.2f} MB/min)")
    print(f"  - Observed Daily Rate    : {gb_per_day:,.2f} GB/day")
    print(f"  - Projected 72-Hour Total: {proj_72h_gb:,.2f} GB")
    print(f"  - Projected 7-Day Total  : {proj_7d_gb:,.2f} GB")
    print(f"  - Projected 30-Day Total : {proj_30d_gb:,.2f} GB")

    print(f"\n[3. Breakdown by Exchange]")
    for exch, d in sorted(exchange_stats.items(), key=lambda x: x[1]["bytes"], reverse=True):
        b = d["bytes"]
        pct = (b / total_bytes * 100.0) if total_bytes > 0 else 0
        mb_h = (b / (1024 * 1024)) / elapsed_hours
        print(f"  - {exch:10s}: {b/(1024*1024):10,.2f} MB ({pct:5.1f}%) | {d['files']:4d} files | {mb_h:6.2f} MB/h")

    print(f"\n[4. Breakdown by Stream]")
    for st, d in sorted(stream_stats.items(), key=lambda x: x[1]["bytes"], reverse=True):
        b = d["bytes"]
        pct = (b / total_bytes * 100.0) if total_bytes > 0 else 0
        mb_h = (b / (1024 * 1024)) / elapsed_hours
        print(f"  - {st:10s}: {b/(1024*1024):10,.2f} MB ({pct:5.1f}%) | {d['files']:4d} files | {mb_h:6.2f} MB/h")

    print(f"\n[5. Breakdown by Exchange × Stream]")
    for key, d in sorted(pair_stats.items(), key=lambda x: x[1]["bytes"], reverse=True):
        b = d["bytes"]
        pct = (b / total_bytes * 100.0) if total_bytes > 0 else 0
        mb_h = (b / (1024 * 1024)) / elapsed_hours
        print(f"  - {key:22s}: {b/(1024*1024):10,.2f} MB ({pct:5.1f}%) | {d['files']:4d} files | {mb_h:6.2f} MB/h")

    # Sample latest 10 files to get accurate record counts and bytes/record
    print(f"\n[6. Detailed Record Sampling on Latest Hour Partitions]")
    sampled_records = 0
    sampled_bytes = 0
    stream_sample: dict[str, dict[str, int]] = {}

    # Sample top 30 most recent files
    recent_files = sorted(raw_files, key=lambda f: f.stat().st_mtime, reverse=True)[:30]
    for rf in recent_files:
        st = rf.parent.name
        stream_sample.setdefault(st, {"records": 0, "bytes": 0})
        with rf.open("r", encoding="utf-8") as h:
            for line in h:
                lb = len(line.encode("utf-8"))
                sampled_records += 1
                sampled_bytes += lb
                stream_sample[st]["records"] += 1
                stream_sample[st]["bytes"] += lb

    overall_avg_b = (sampled_bytes / sampled_records) if sampled_records > 0 else 0
    print(f"  - Sampled {sampled_records:,} records ({sampled_bytes/(1024*1024):.2f} MB)")
    print(f"  - Overall Average Record Size: {overall_avg_b:.1f} bytes / record")
    for st, d in stream_sample.items():
        c = d["records"]
        b = d["bytes"]
        avg = (b / c) if c > 0 else 0
        print(f"    * {st:10s}: {c:6d} records | {avg:.1f} bytes/record")

    # Observed events per second across all streams
    total_estimated_events = total_bytes / overall_avg_b if overall_avg_b > 0 else 0
    observed_eps = total_estimated_events / elapsed_seconds
    print(f"\n[7. Observed Event Throughput]")
    print(f"  - Estimated Total Events : {total_estimated_events:,.0f} events")
    print(f"  - Observed Events/Sec    : {observed_eps:,.1f} events/sec across all exchanges")

    # Disk Space and Time-to-full
    disk = shutil.disk_usage(ROOT)
    total_disk_gb = disk.total / (1024**3)
    used_disk_gb = disk.used / (1024**3)
    free_disk_gb = disk.free / (1024**3)
    free_disk_pct = (disk.free / disk.total) * 100.0
    used_disk_pct = (disk.used / disk.total) * 100.0

    gb_per_hour = mb_per_hour / 1024.0
    hours_to_full = (free_disk_gb / gb_per_hour) if gb_per_hour > 0 else 999999.0
    days_to_full = hours_to_full / 24.0

    print(f"\n[8. Machine Disk Safety & Exhaustion Projection]")
    print(f"  - Total Disk Space   : {total_disk_gb:,.1f} GB")
    print(f"  - Used Disk Space    : {used_disk_gb:,.1f} GB ({used_disk_pct:.1f}%)")
    print(f"  - Free Disk Space    : {free_disk_gb:,.1f} GB ({free_disk_pct:.1f}%)")
    print(f"  - Time-To-Full (0% free): {hours_to_full:,.1f} hours ({days_to_full:,.1f} days)")
    print(f"  - 72-Hour Soak Headroom : {free_disk_gb - proj_72h_gb:,.1f} GB remaining after 72h")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
