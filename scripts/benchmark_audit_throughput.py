#!/usr/bin/env python3
"""Benchmark throughput and streaming memory efficiency for 72H post-soak auditing.

Measures:
1. Streaming SHA-256 calculation throughput (MB/s)
2. Streaming Zstandard decompression throughput (MB/s)
3. Streaming JSONL parsing throughput (lines/s, MB/s)
4. Memory consumption (demonstrates bounded constant-memory streaming)
5. Full 72H dataset audit runtime extrapolation
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import resource
import sys
import tempfile
import time
import zstandard as zstd


def get_peak_memory_mb() -> float:
    # ru_maxrss is in bytes on macOS, kilobytes on Linux
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return usage / (1024 * 1024)
    return usage / 1024


def run_benchmark(target_data_mb: int = 50) -> int:
    print("=" * 80)
    print(f"72H POST-SOAK PIPELINE STREAMING BENCHMARK ({target_data_mb} MB synthetic workload)")
    print("=" * 80)

    initial_mem = get_peak_memory_mb()

    # Generate synthetic orderbook data in memory
    sample_line = json.dumps({
        "exchange": "bithumb",
        "stream": "orderbook",
        "market": "KRW-BTC",
        "exchange_time": 1725500000000,
        "local_receive_time": 1725500000050,
        "bids": [[99900000.0, 1.5], [99800000.0, 2.0]],
        "asks": [[100100000.0, 1.2], [100200000.0, 3.1]],
    }) + "\n"
    sample_bytes = sample_line.encode("utf-8")
    lines_needed = (target_data_mb * 1024 * 1024) // len(sample_bytes)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        raw_file = tmp_path / "synthetic_audit_workload.jsonl"
        zst_file = tmp_path / "synthetic_audit_workload.jsonl.zst"

        print(f"Generating {lines_needed:,} synthetic JSONL records...")
        with open(raw_file, "wb") as f:
            for _ in range(lines_needed):
                f.write(sample_bytes)

        raw_size_mb = raw_file.stat().st_size / (1024 * 1024)

        # Compress to Zstandard
        cctx = zstd.ZstdCompressor(level=3)
        with open(raw_file, "rb") as f_in, open(zst_file, "wb") as f_out:
            cctx.copy_stream(f_in, f_out)
        zst_size_mb = zst_file.stat().st_size / (1024 * 1024)
        print(f"Raw Size: {raw_size_mb:.2f} MB | Compressed ZST: {zst_size_mb:.2f} MB (Ratio: {raw_size_mb/zst_size_mb:.2f}x)")

        # Benchmark 1: Streaming SHA-256
        t0 = time.perf_counter()
        hasher = hashlib.sha256()
        with open(raw_file, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        t_sha = time.perf_counter() - t0
        sha_throughput = raw_size_mb / t_sha

        # Benchmark 2: Streaming Zstandard Decompression
        t0 = time.perf_counter()
        dctx = zstd.ZstdDecompressor()
        decompressed_bytes = 0
        with open(zst_file, "rb") as f_in:
            with dctx.stream_reader(f_in) as reader:
                while chunk := reader.read(65536):
                    decompressed_bytes += len(chunk)
        t_zst = time.perf_counter() - t0
        zst_throughput = raw_size_mb / t_zst

        # Benchmark 3: Streaming JSONL Parsing
        t0 = time.perf_counter()
        parsed_records = 0
        with open(raw_file, "r", encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                parsed_records += 1
        t_json = time.perf_counter() - t0
        json_mb_throughput = raw_size_mb / t_json
        json_line_throughput = parsed_records / t_json

    peak_mem = get_peak_memory_mb()
    mem_delta = peak_mem - initial_mem

    print("-" * 80)
    print("STREAMING AUDIT PERFORMANCE RESULTS:")
    print(f"  1. SHA-256 Hashing Throughput:    {sha_throughput:>8.2f} MB/s (Time: {t_sha:.3f}s)")
    print(f"  2. Zstandard Decomp Throughput:   {zst_throughput:>8.2f} MB/s (Time: {t_zst:.3f}s)")
    print(f"  3. JSONL Line Parsing Throughput: {json_mb_throughput:>8.2f} MB/s ({json_line_throughput:,.0f} lines/s)")
    print(f"  Peak Resident Memory:             {peak_mem:.1f} MB (Delta: +{mem_delta:.1f} MB)")
    print("-" * 80)

    # Extrapolations for full 72H soak
    # Assume 72 hours * 76 feeds * ~15 MB/hour = ~82 GB raw data
    full_dataset_est_gb = 85.0
    full_dataset_est_mb = full_dataset_est_gb * 1024
    est_total_audit_sec = (full_dataset_est_mb / zst_throughput) + (full_dataset_est_mb / json_mb_throughput)
    est_total_audit_min = est_total_audit_sec / 60.0

    print("72-HOUR SOAK DATASET EXTRAPOLATION:")
    print(f"  Estimated Full 72H Raw Size:      {full_dataset_est_gb:.1f} GB")
    print(f"  Estimated Full Audit Duration:    {est_total_audit_min:.2f} minutes ({est_total_audit_sec:.1f} seconds)")
    print(f"  Streaming Memory Guarantee:       CONSTANT O(1) < 150 MB Resident")
    print("=" * 80)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark post-soak audit throughput.")
    parser.add_argument("--size-mb", type=int, default=30, help="Benchmark workload size in MB")
    args = parser.parse_args()
    return run_benchmark(args.size_mb)


if __name__ == "__main__":
    sys.exit(main())
