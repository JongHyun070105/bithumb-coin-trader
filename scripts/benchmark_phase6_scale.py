#!/usr/bin/env python3
"""Phase 6 Scale Benchmark: Proving Bounded-Memory O(1) Dataset Partitioning.

Evaluates dataset partitioning performance and memory scaling across multiple scales:
- 100,000 records
- 300,000 records
- 600,000 records (and optionally 1,000,000 records)

Measures:
- Input records and bytes
- Partitioning duration and throughput (records/sec)
- Peak RSS memory and RSS delta per step
- Memory slope (d(Peak_RSS) / d(Record_Count)) to prove bounded memory
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import tempfile
import time
import zstandard

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bithumb_coin_trader.research_cli import (
    cmd_dq_qualify,
    cmd_partition_dataset,
)


def get_peak_rss_mb() -> float:
    """Returns peak RSS memory in MB."""
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return usage / (1024 * 1024)
    return usage / 1024


def generate_synthetic_canonical_zst(
    output_file: Path,
    record_count: int,
    base_timestamp_ms: int = 1725148800000,
) -> int:
    """Generates synthetic canonical orderbook ndjson.zst directly for high-speed benchmark."""
    cctx = zstandard.ZstdCompressor(level=3)
    chunk_size = 10_000
    with open(output_file, "wb") as f:
        with cctx.stream_writer(f) as writer:
            for chunk_start in range(0, record_count, chunk_size):
                chunk_end = min(chunk_start + chunk_size, record_count)
                lines = []
                for i in range(chunk_start, chunk_end):
                    t = base_timestamp_ms + i * 10
                    rec = {
                        "exchange": "bithumb",
                        "market": "KRW-BTC",
                        "stream": "orderbook",
                        "receive_timestamp_ms": t,
                        "exchange_timestamp_ms": t,
                        "bids": [[100_000_000.0 - (i % 1000), 1.0]],
                        "asks": [[100_010_000.0 + (i % 1000), 1.0]],
                    }
                    lines.append(json.dumps(rec))
                raw_chunk = ("\n".join(lines) + "\n").encode("utf-8")
                writer.write(raw_chunk)

    return output_file.stat().st_size


def run_benchmark_scale(
    record_counts: list[int] | None = None,
) -> list[dict[str, float]]:
    if record_counts is None:
        record_counts = [100_000, 300_000, 600_000]

    print("============================================================")
    print("PHASE 6 BOUNDED-MEMORY DATASET PARTITIONING SCALE BENCHMARK")
    print(f"Target record scales: {[f'{c:,}' for c in record_counts]}")
    print("============================================================\n")

    results: list[dict[str, float]] = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Setup common qualification and source manifest
        deep_report = tmp_path / "deep_report.json"
        deep_report.write_text(json.dumps({
            "status": "DQ_PASS_ELIGIBLE",
            "audit_type": "authoritative_deep_dq",
            "errors": [],
            "blockers": [],
        }))
        src_manifest = tmp_path / "source_manifest.json"
        src_manifest.write_text(json.dumps({"files": ["synthetic_input"]}))

        qual_file = tmp_path / "qual.json"
        cmd_dq_qualify(argparse.Namespace(
            audit_report=str(deep_report),
            source_manifest=str(src_manifest),
            out=str(qual_file),
            strict=True,
            auditor_commit="a" * 40,
        ))

        for scale_idx, n_records in enumerate(record_counts):
            print(f"--- Scale [{scale_idx + 1}/{len(record_counts)}]: {n_records:,} records ---")
            canon_file = tmp_path / f"canonical_{n_records}.ndjson.zst"

            gen_start = time.perf_counter()
            file_bytes = generate_synthetic_canonical_zst(canon_file, n_records)
            gen_dur = time.perf_counter() - gen_start
            print(f"Generated {file_bytes / (1024 * 1024):.2f} MB in {gen_dur:.2f}s")

            dataset_out = tmp_path / f"dataset_{n_records}"
            start_rss = get_peak_rss_mb()

            part_start = time.perf_counter()
            rc = cmd_partition_dataset(argparse.Namespace(
                input_file=str(canon_file),
                output_dir=str(dataset_out),
                dq_report=str(qual_file),
                source_manifest=str(src_manifest),
                train_frac=0.60,
                val_frac=0.20,
                purge_window_ms=5_000,
                clock="receive_wall_clock",
                source_epoch_id=f"epoch_scale_{n_records}",
                source_run_id=f"run_scale_{n_records}",
                dataset_name=f"scale_{n_records}",
            ))
            part_dur = time.perf_counter() - part_start
            assert rc == 0, f"Partition failed for scale {n_records}"

            end_rss = get_peak_rss_mb()
            part_rate = n_records / max(part_dur, 0.001)

            entry = {
                "records": n_records,
                "input_bytes": file_bytes,
                "partition_duration_sec": round(part_dur, 3),
                "records_per_sec": round(part_rate, 1),
                "peak_rss_mb": round(end_rss, 2),
                "rss_delta_mb": round(end_rss - start_rss, 2),
            }
            results.append(entry)
            print(f"Partitioned in {part_dur:.2f}s ({part_rate:,.0f} recs/sec) | Peak RSS: {end_rss:.2f} MB\n")

            canon_file.unlink(missing_ok=True)
            import shutil
            shutil.rmtree(dataset_out, ignore_errors=True)

    print("============================================================")
    print("SCALE BENCHMARK RESULTS & MEMORY SCALING ANALYSIS")
    print("============================================================")
    print("| Records | Input (MB) | Duration (s) | Throughput (rec/s) | Peak RSS (MB) |")
    print("| :--- | :--- | :--- | :--- | :--- |")
    for r in results:
        print(f"| {r['records']:,} | {r['input_bytes'] / (1024 * 1024):.2f} MB | {r['partition_duration_sec']:.2f}s | {r['records_per_sec']:,.0f} | {r['peak_rss_mb']:.2f} MB |")

    first_r, last_r = results[0], results[-1]
    delta_records = last_r["records"] - first_r["records"]
    delta_memory_mb = last_r["peak_rss_mb"] - first_r["peak_rss_mb"]
    memory_slope = (delta_memory_mb / delta_records) * 100_000 if delta_records > 0 else 0.0

    print(f"\nMemory Scaling Slope: {memory_slope:.4f} MB per 100,000 records")
    if abs(memory_slope) < 5.0:
        print("Verdict: BOUNDED MEMORY O(1) STREAMING VERIFIED (Slope < 5.0 MB/100k records)")
    else:
        print("Verdict: MEMORY GROWTH DETECTED")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Phase 6 scale benchmark")
    parser.add_argument("--scales", nargs="+", type=int, default=[100_000, 300_000, 600_000])
    args = parser.parse_args()
    run_benchmark_scale(args.scales)
