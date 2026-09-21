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
