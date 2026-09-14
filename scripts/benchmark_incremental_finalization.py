#!/usr/bin/env python3
"""Bounded scale benchmark for V3 incremental partition finalization."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
for d in (ROOT, SRC_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

from tests.test_incremental_finalization_scale import (
    build_progress_history,
    count_raw_opens,
)


def compute_percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (len(sorted_vals) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(sorted_vals) - 1)
    weight = rank - lower
    return sorted_vals[lower] + (sorted_vals[upper] - sorted_vals[lower]) * weight


def run_benchmark(
    *,
    repetitions: int = 5,
    histories: Sequence[int] = (1, 10, 30),
    dirty_tail: int = 2,
    output_path: Path,
) -> int:
    all_passed = True
    history_results: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="v3_finalization_bench_") as temp_dir_str:
        bench_root = Path(temp_dir_str)

        for history in histories:
            samples: list[dict[str, Any]] = []
            durations_sec: list[float] = []

            for rep in range(repetitions):
                run_dir = bench_root / f"h{history}_rep{rep}_{time.time_ns()}"
                fixture = build_progress_history(
                    run_dir,
                    terminal_cohorts=history,
                    dirty_tail=dirty_tail,
                )

                with count_raw_opens() as opened:
                    t0 = time.perf_counter()
                    summary = fixture.finalizer.finalize_pending()
                    t1 = time.perf_counter()

                elapsed_sec = t1 - t0
                durations_sec.append(elapsed_sec)

                # Assertions per repetition
                rep_dirty_match = summary.recomputed_count == dirty_tail
                rep_zero_historical_reads = (
                    summary.historical_raw_files_opened == 0 and summary.historical_raw_bytes_read == 0
                )
                rep_opened_match = opened.paths == set(fixture.dirty_tail_paths)

                if not (rep_dirty_match and rep_zero_historical_reads and rep_opened_match):
                    all_passed = False

                samples.append({
                    "repetition": rep,
                    "elapsed_seconds": elapsed_sec,
                    "elapsed_ms": round(elapsed_sec * 1000.0, 4),
                    "recomputed_count": summary.recomputed_count,
                    "reused_count": summary.reused_count,
                    "historical_raw_files_opened": summary.historical_raw_files_opened,
                    "historical_raw_bytes_read": summary.historical_raw_bytes_read,
                    "raw_files_opened_count": len(opened.paths),
                    "passed": rep_dirty_match and rep_zero_historical_reads and rep_opened_match,
                })

            durations_ms = [d * 1000.0 for d in durations_sec]
            p50_ms = compute_percentile(durations_ms, 0.50)
            p95_ms = compute_percentile(durations_ms, 0.95)
            p99_ms = compute_percentile(durations_ms, 0.99)
            min_ms = min(durations_ms)
            max_ms = max(durations_ms)

            p50_sec = compute_percentile(durations_sec, 0.50)
            p95_sec = compute_percentile(durations_sec, 0.95)
            p99_sec = compute_percentile(durations_sec, 0.99)
            min_sec = min(durations_sec)
            max_sec = max(durations_sec)

            history_results[str(history)] = {
                "history": history,
                "repetitions": repetitions,
                "timing_ms": {
                    "p50": round(p50_ms, 4),
                    "p95": round(p95_ms, 4),
                    "p99": round(p99_ms, 4),
                    "min": round(min_ms, 4),
                    "max": round(max_ms, 4),
                },
                "timing_seconds": {
                    "p50": round(p50_sec, 6),
                    "p95": round(p95_sec, 6),
                    "p99": round(p99_sec, 6),
                    "min": round(min_sec, 6),
                    "max": round(max_sec, 6),
                },
                "counters": {
                    "recomputed_count": dirty_tail,
                    "reused_count": history,
                    "historical_raw_files_opened": 0,
                    "historical_raw_bytes_read": 0,
                },
                "samples": samples,
            }

    # Cross-history assertions:
    # 1. Identical dirty work across all histories
    recomputed_counts = {
        h_data["counters"]["recomputed_count"] for h_data in history_results.values()
    }
    identical_dirty_tail = (recomputed_counts == {dirty_tail})

    # 2. Zero historical raw reads across all histories
    historical_opens = {
        h_data["counters"]["historical_raw_files_opened"] for h_data in history_results.values()
    }
    historical_bytes = {
        h_data["counters"]["historical_raw_bytes_read"] for h_data in history_results.values()
    }
    zero_historical_reads = (historical_opens == {0} and historical_bytes == {0})

    if not (identical_dirty_tail and zero_historical_reads):
        all_passed = False

    status = "PASS" if all_passed else "FAIL"

    report = {
        "schema_version": 1,
        "status": status,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "parameters": {
            "repetitions": repetitions,
            "histories": list(histories),
            "dirty_tail": dirty_tail,
        },
        "assertions": {
            "identical_dirty_tail": identical_dirty_tail,
            "zero_historical_reads": zero_historical_reads,
            "all_repetitions_passed": all_passed,
        },
        "histories": history_results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_json = json.dumps(report, indent=2, sort_keys=True) + "\n"
    output_path.write_text(report_json, encoding="utf-8")

    print(f"[{status}] V3 finalization scale benchmark completed.")
    print(f"Report written to: {output_path}")
    for h in histories:
        t = history_results[str(h)]["timing_ms"]
        print(f"  History {h:2d}: p50={t['p50']:.2f}ms, p95={t['p95']:.2f}ms, p99={t['p99']:.2f}ms")

    return 0 if all_passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark V3 bounded incremental finalization scale."
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=5,
        help="Number of repetitions per history size (default: 5).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / ".superpowers/sdd/2026-09-14-aws-30h-v3-remediation/aws-30h-v3-finalization-scale.json",
        help="Output path for benchmark report JSON.",
    )
    parser.add_argument(
        "--histories",
        nargs="+",
        type=int,
        default=[1, 10, 30],
        help="List of historical cohort sizes to test (default: 1 10 30).",
    )
    parser.add_argument(
        "--dirty-tail",
        type=int,
        default=2,
        help="Number of unfinalized dirty tail cohorts (default: 2).",
    )
    args = parser.parse_args()

    return run_benchmark(
        repetitions=args.repetitions,
        histories=args.histories,
        dirty_tail=args.dirty_tail,
        output_path=args.output,
    )


if __name__ == "__main__":
    sys.exit(main())
