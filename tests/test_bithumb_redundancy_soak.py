from __future__ import annotations

import asyncio

from bithumb_coin_trader.redundancy_soak import run_accelerated_soak


def test_accelerated_soak_model_is_bounded() -> None:
    report = asyncio.run(run_accelerated_soak(virtual_seconds=1_200))
    assert report["result"] == "PASS"
    assert report["logical_feeds"] == 60
    assert report["physical_connections"] == 2
    assert report["maximum_queue_depth"] == 2_048
    assert report["final_queue_depth"] == 0
    assert report["task_leak_count"] == 0
    assert report["writer_errors"] == 0
    assert report["unpersisted_events"] == 0
    assert report["timestamp_jitter_trade_duplicates_observed"] == report[
        "timestamp_jitter_trade_duplicates_expected"
    ]
    assert report["semantic_trade_conflicts_observed"] == report[
        "semantic_trade_conflicts_expected"
    ]
    assert report["single_source_outage_seconds"] > 0
    assert report["dual_source_gap_seconds_observed"] > 0
    assert report["dual_source_gap_seconds_observed"] == report[
        "dual_source_gap_seconds_expected"
    ]
    assert report["dedup_cache_entries_peak"] >= report["dedup_cache_entries_final"]
    assert report["cohort_boundaries_crossed"] == 0
