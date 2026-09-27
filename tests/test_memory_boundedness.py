"""Regression tests for memory boundedness, heartbeat deduplication/throttling, and pruning."""

from __future__ import annotations

import gc
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bithumb_coin_trader.feed_hour_coverage import FeedIdentity
from bithumb_coin_trader.session_evidence import SessionEvidenceTracker


def test_heartbeat_throttling_subsecond_frames() -> None:
    """Verify that 100,000 sub-second frames within the same second record exactly 1 heartbeat."""
    tracker = SessionEvidenceTracker(epoch="test_epoch", run_id="test_run")
    feed = FeedIdentity("binance", "trade", "btcusdt")
    sid = tracker.open_session("binance", [feed.canonical], "2026-09-17T08:00:00Z")

    timestamp = "2026-09-17T08:00:01Z"
    for _ in range(100_000):
        tracker.record_heartbeat(sid, timestamp, kind="FRAME")

    session = tracker._sessions[sid]
    # Exactly 1 heartbeat must be stored, preventing memory explosion
    assert len(session.heartbeat_observations_utc) == 1
    assert session.heartbeat_observations_utc[0] == timestamp


def test_heartbeat_prune_older_than() -> None:
    """Verify pruning removes heartbeats older than threshold while keeping 1 boundary point for gap calculations."""
    tracker = SessionEvidenceTracker(epoch="test_epoch", run_id="test_run")
    feed = FeedIdentity("bithumb", "ticker", "KRW-BTC")
    sid = tracker.open_session("bithumb", [feed.canonical], "2026-09-17T06:00:00Z")

    # Record heartbeats every 10 seconds from 06:50 to 07:10
    base_dt = datetime(2026, 9, 17, 6, 50, 0, tzinfo=timezone.utc)
    for i in range(120):  # 120 * 10s = 1200s (20 mins) -> 06:50:00 to 07:09:50
        cur_ts = (base_dt + timedelta(seconds=i * 10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        tracker.record_heartbeat(sid, cur_ts)

    session = tracker._sessions[sid]
    assert len(session.heartbeat_observations_utc) == 120

    # Prune at 07:00:00 boundary
    threshold = "2026-09-17T07:00:00Z"
    pruned = tracker.prune_older_than(threshold)
    assert pruned > 0

    # All retained elements must be >= 07:00:00, except at most 1 element immediately before threshold
    retained = session.heartbeat_observations_utc
    assert len(retained) == 120 - pruned
    # Verify the first element is the boundary point (06:59:50)
    assert retained[0] == "2026-09-17T06:59:50Z"
    assert retained[1] == "2026-09-17T07:00:00Z"
    for item in retained[1:]:
        assert item >= threshold


def test_high_frequency_tick_memory_boundedness() -> None:
    """Simulate 360,000 incoming ticks over 1 hour (100 Hz), asserting max list size is 3600."""
    tracker = SessionEvidenceTracker(epoch="test_epoch", run_id="test_run")
    feed = FeedIdentity("upbit", "trade", "KRW-BTC")
    sid = tracker.open_session("upbit", [feed.canonical], "2026-09-17T07:00:00Z")

    base_dt = datetime(2026, 9, 17, 7, 0, 0, tzinfo=timezone.utc)
    # 3600 seconds, 100 ticks per second
    for sec in range(3600):
        sec_ts = (base_dt + timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for _ in range(100):  # 100 sub-second messages
            tracker.record_heartbeat(sid, sec_ts, kind="FRAME")

    session = tracker._sessions[sid]
    # Must be exactly 3600, not 360,000!
    assert len(session.heartbeat_observations_utc) == 3600
