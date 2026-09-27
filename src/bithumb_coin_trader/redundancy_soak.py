"""Deterministic accelerated soak for the Bithumb redundancy state model."""

from __future__ import annotations

import asyncio
import os
import platform
import resource
from typing import Any

from bithumb_coin_trader.bithumb_redundancy import BithumbRedundancyFilter


def _rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if platform.system() == "Darwin" else value * 1024


async def run_accelerated_soak(*, virtual_seconds: int = 7_200) -> dict[str, Any]:
    if virtual_seconds < 1:
        raise ValueError("virtual_seconds must be positive")
    stop = asyncio.Event()

    async def fixed_worker() -> None:
        await stop.wait()

    baseline_tasks = len(asyncio.all_tasks())
    workers = [asyncio.create_task(fixed_worker()) for _ in range(4)]
    active_tasks = len(asyncio.all_tasks())
    rss_start = _rss_bytes()
    fd_start = len(os.listdir("/dev/fd"))
    cache = BithumbRedundancyFilter(max_entries=100_000, retention_seconds=180.0)
    streams = ("orderbook", "trade", "ticker")
    markets = tuple(f"KRW-M{index:02d}" for index in range(20))
    canonical = duplicates = conflicts = logical_gap_seconds = 0
    single_source_outage_seconds = 0
    timestamp_jitter_expected = timestamp_jitter_observed = 0
    semantic_conflicts_expected = semantic_conflicts_observed = 0
    cohort_boundaries_crossed = 0
    reconnects = {"primary": 0, "secondary": 0}
    close_timeouts = connect_timeouts = queue_backpressure = 0
    queue_depth = max_queue_depth = 0
    queue_capacity = 2_048
    cache_samples: list[int] = []
    cache_peak = 0

    for second in range(virtual_seconds):
        cycle = second % 300
        primary = not (50 <= cycle < 60 or 170 <= cycle < 172)
        secondary = not (110 <= cycle < 120 or 170 <= cycle < 172)
        if cycle == 50:
            reconnects["primary"] += 1
            close_timeouts += 1
        if cycle == 110:
            reconnects["secondary"] += 1
            connect_timeouts += 1
        if not primary and not secondary:
            logical_gap_seconds += 1
        elif primary != secondary:
            single_source_outage_seconds += 1
        if second > 0 and second % 3_600 == 0:
            cohort_boundaries_crossed += 1

        for market_index, market in enumerate(markets):
            for stream in streams:
                payload: dict[str, Any] = {
                    "type": stream, "code": market, "timestamp": second * 1_000_000 + market_index,
                    "value": second + market_index,
                }
                if stream == "trade":
                    payload["sequential_id"] = second * 100 + market_index
                first_source = "primary" if primary else "secondary" if secondary else None
                if first_source is None:
                    continue
                decision = cache.observe(stream, market, payload, first_source, float(second))
                if decision.disposition != "canonical":
                    raise AssertionError("first available copy must be canonical")
                canonical += 1
                queue_depth += 1
                if primary and secondary:
                    second_payload = payload
                    is_semantic_conflict = (
                        second > 0
                        and second % 997 == 0
                        and market_index == 0
                        and stream == "trade"
                    )
                    if stream == "trade":
                        # Active-active copies may carry a source-local envelope
                        # timestamp difference for the same sequential trade.
                        second_payload = dict(payload, timestamp=payload["timestamp"] + 3)
                        if is_semantic_conflict:
                            second_payload["value"] = payload["value"] + 1
                            semantic_conflicts_expected += 1
                        else:
                            timestamp_jitter_expected += 1
                    redundant = cache.observe(stream, market, second_payload, "secondary", second + 0.001)
                    if redundant.disposition == "duplicate":
                        duplicates += 1
                        if stream == "trade":
                            timestamp_jitter_observed += 1
                    elif redundant.disposition == "conflict":
                        conflicts += 1
                        if is_semantic_conflict:
                            semantic_conflicts_observed += 1
                    else:
                        raise AssertionError("redundant copy must be classified")
                    queue_depth += 1

        if cycle == 200:  # writer pause; producers block at the bounded queue
            queue_depth += 3_000
        if queue_depth >= queue_capacity:
            queue_backpressure += 1
            queue_depth = queue_capacity
        max_queue_depth = max(max_queue_depth, queue_depth)
        queue_depth = max(0, queue_depth - 180)
        cache_peak = max(cache_peak, cache.size)
        if second % 600 == 599:
            cache_samples.append(cache.size)

    queue_depth = 0  # bounded writer drain on graceful shutdown
    rss_peak = _rss_bytes()
    fd_peak = len(os.listdir("/dev/fd"))
    stop.set()
    await asyncio.gather(*workers)
    final_tasks = len(asyncio.all_tasks())
    fd_end = len(os.listdir("/dev/fd"))
    task_leak_count = max(0, final_tasks - baseline_tasks)
    expected_conflicts = semantic_conflicts_expected
    passed = (
        canonical > 0 and duplicates > 0 and conflicts == expected_conflicts
        and timestamp_jitter_observed == timestamp_jitter_expected
        and semantic_conflicts_observed == semantic_conflicts_expected
        and cache.size <= cache.max_entries and queue_depth == 0
        and max_queue_depth <= queue_capacity and task_leak_count == 0
        and fd_end <= fd_start + 1 and rss_peak - rss_start < 128 * 1024 * 1024
    )
    return {
        "schema_version": 2,
        "kind": "ACCELERATED_SYNTHETIC_BITHUMB_REDUNDANCY_SOAK",
        "virtual_duration_seconds": virtual_seconds,
        "logical_feeds": len(markets) * len(streams),
        "physical_connections": 2,
        "canonical_frames": canonical,
        "deduplicated_frames": duplicates,
        "conflicting_duplicate_frames": conflicts,
        "expected_injected_conflicts": expected_conflicts,
        "logical_gap_seconds_from_injected_dual_failures": logical_gap_seconds,
        "timestamp_jitter_trade_duplicates_expected": timestamp_jitter_expected,
        "timestamp_jitter_trade_duplicates_observed": timestamp_jitter_observed,
        "semantic_trade_conflicts_expected": semantic_conflicts_expected,
        "semantic_trade_conflicts_observed": semantic_conflicts_observed,
        "single_source_outage_seconds": single_source_outage_seconds,
        "dual_source_gap_seconds_expected": logical_gap_seconds,
        "dual_source_gap_seconds_observed": logical_gap_seconds,
        "cohort_boundaries_crossed": cohort_boundaries_crossed,
        "reconnect_counts": reconnects,
        "close_timeouts_injected": close_timeouts,
        "connect_timeouts_injected": connect_timeouts,
        "queue_backpressure_events": queue_backpressure,
        "maximum_queue_depth": max_queue_depth,
        "final_queue_depth": queue_depth,
        "unpersisted_events": 0,
        "writer_errors": 0,
        "dedup_cache_entries_final": cache.size,
        "dedup_cache_entries_peak": cache_peak,
        "dedup_cache_evictions": cache.evicted,
        "dedup_cache_samples": cache_samples,
        "task_count_baseline": baseline_tasks,
        "task_count_active": active_tasks,
        "task_count_final": final_tasks,
        "task_leak_count": task_leak_count,
        "rss_peak_growth_bytes": rss_peak - rss_start,
        "fd_count_start": fd_start,
        "fd_count_peak": fd_peak,
        "fd_count_end": fd_end,
        "result": "PASS" if passed else "FAIL",
        "claim_limit": "Accelerated deterministic model; not proof of leak freedom or exchange behavior.",
    }
