"""Deterministic semantic replay of the immutable Fresh 6H-v4r1 failures."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bithumb_coin_trader.bithumb_redundancy import BithumbRedundancyFilter
from bithumb_coin_trader.closed_hour_finalizer import evaluate_common_gate
from bithumb_coin_trader.feed_hour_coverage import (
    FeedHourCoverageTracker,
    FrozenFeedHourObservation,
)
from bithumb_coin_trader.session_evidence import (
    FeedIdentity,
    HeartbeatPolicy,
    SessionEvidenceTracker,
    SessionSegment,
    WriterHealthSnapshot,
)


def _trade(sequential_id: int, timestamp: int) -> dict[str, object]:
    return {
        "type": "trade",
        "code": "KRW-BTC",
        "sequential_id": sequential_id,
        "timestamp": timestamp,
        "trade_timestamp": 1_790_000_000_000 + sequential_id,
        "trade_price": 100_000_000,
        "trade_volume": "0.001",
        "ask_bid": "BID",
    }


def test_replay_04_07_08_timestamp_only_trade_copies_are_duplicates() -> None:
    for cohort_index, cohort in enumerate(("_04", "_07", "_08")):
        cache = BithumbRedundancyFilter()
        for offset in range(64):
            trade = _trade(cohort_index * 1_000 + offset, 10_000 + offset)
            assert cache.observe("trade", "KRW-BTC", trade, "primary", offset).disposition == "canonical"
            jittered = dict(trade, timestamp=10_003 + offset)
            decision = cache.observe(
                "trade", "KRW-BTC", jittered, "secondary", offset + 0.001
            )
            assert decision.disposition == "duplicate", cohort
            assert decision.equivalence == "trade_timestamp_ignored"


def test_replay_05_ticker_timestamp_collision_retains_both_states() -> None:
    cache = BithumbRedundancyFilter()
    first = {
        "type": "ticker",
        "code": "KRW-XRP",
        "timestamp": 1_790_055_134_410,
        "trade_timestamp": 1_790_055_134_246,
        "trade_volume": 42.6,
        "acc_trade_volume_24h": 12_345.6,
    }
    second = dict(first, trade_timestamp=1_790_055_134_156, trade_volume=51.5)

    assert cache.observe("ticker", "KRW-XRP", first, "primary", 0).disposition == "canonical"
    assert cache.observe("ticker", "KRW-XRP", second, "secondary", 1).disposition == "canonical"
    assert cache.observe("ticker", "KRW-XRP", first, "secondary", 2).disposition == "duplicate"


def _segment(
    feed: FeedIdentity,
    session_id: str,
    connected: str,
    disconnected: str | None,
    heartbeat_start: int,
    heartbeat_end: int,
    *,
    confirmed: str | None = None,
) -> SessionSegment:
    base = datetime(2026, 9, 22, 6, 0, tzinfo=timezone.utc)
    heartbeats = tuple(
        (base + timedelta(seconds=second)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for second in range(heartbeat_start, heartbeat_end + 1, 10)
    )
    return SessionSegment(
        exchange="bithumb",
        session_id=session_id,
        connected_at_utc=connected,
        disconnected_at_utc=disconnected,
        requested_feeds=(feed.canonical,),
        requested_subscription_sha256=f"requested-{session_id}",
        confirmation_method="STREAM_SNAPSHOT",
        confirmed_at_utc=confirmed or connected,
        confirmed_feeds=(feed.canonical,),
        confirmed_subscription_sha256=f"confirmed-{session_id}",
        response_evidence_sha256=f"response-{session_id}",
        heartbeat_observations_utc=heartbeats,
        maximum_heartbeat_gap_seconds=10.0,
        disconnect_reason="CONNECTION_RESET" if disconnected else None,
        reconnect_successor_id="primary-2" if session_id == "primary-1" else None,
        collector_epoch="v4r1-replay",
        collector_run_id="v4r1-replay",
    )


def test_replay_06_primary_reconnect_is_visible_but_union_is_complete() -> None:
    feed = FeedIdentity("bithumb", "trade", "KRW-BTC")
    segments = (
        _segment(
            feed, "primary-1", "2026-09-22T05:50:00Z", "2026-09-22T06:54:34Z", 0, 3270
        ),
        _segment(
            feed, "primary-2", "2026-09-22T06:54:34Z", None, 3280, 3600,
            confirmed="2026-09-22T06:54:36Z",
        ),
        _segment(feed, "secondary", "2026-09-22T05:50:00Z", None, 0, 3600),
    )
    observation = FrozenFeedHourObservation(
        feed=feed,
        cohort_utc="2026-09-22_06",
        interval_start_utc="2026-09-22T06:00:00Z",
        interval_end_utc="2026-09-22T07:00:00Z",
        cohort_qualification="QUALIFYING_FULL_HOUR",
        observation_start_utc="2026-09-22T06:00:00Z",
        observation_end_utc="2026-09-22T07:00:00Z",
        event_count=1,
        first_event_timestamp="2026-09-22T06:00:00Z",
        last_event_timestamp="2026-09-22T07:00:00Z",
        session_segments=segments,
        disconnect_count=1,
        reconnect_count=1,
        health=WriterHealthSnapshot(),
        logical_redundancy_enabled=True,
    )

    assert segments[0].disconnect_reason == "CONNECTION_RESET"
    assert segments[0].reconnect_successor_id == "primary-2"
    assert evaluate_common_gate(
        observation,
        HeartbeatPolicy(max_allowed_heartbeat_gap_seconds={"bithumb": 30}),
    ) == []


def test_replay_conflict_scope_does_not_poison_other_feeds_or_cohorts() -> None:
    affected = FeedIdentity("bithumb", "ticker", "KRW-XRP")
    other_bithumb = FeedIdentity("bithumb", "trade", "KRW-BTC")
    binance = FeedIdentity("binance", "trade", "btcusdt")
    upbit = FeedIdentity("upbit", "trade", "KRW-BTC")
    feeds = (affected, other_bithumb, binance, upbit)
    tracker = FeedHourCoverageTracker(
        feeds,
        actual_start_utc=datetime(2026, 9, 22, 3, 50, tzinfo=timezone.utc),
        bithumb_redundancy_enabled=True,
    )
    tracker.record_conflicting_duplicate(
        affected, datetime(2026, 9, 22, 5, 32, 14, tzinfo=timezone.utc)
    )
    sessions = SessionEvidenceTracker("v4r1-replay", "v4r1-replay")
    observations = tracker.freeze_completed(
        datetime(2026, 9, 22, 6, 0, tzinfo=timezone.utc),
        sessions,
        WriterHealthSnapshot(conflicting_duplicate_frames=6_983),
    )
    by_feed = {observation.feed: observation for observation in observations}

    assert by_feed[affected].health.conflicting_duplicate_frames == 1
    assert by_feed[other_bithumb].health.conflicting_duplicate_frames == 0
    assert by_feed[binance].health.conflicting_duplicate_frames == 0
    assert by_feed[upbit].health.conflicting_duplicate_frames == 0
