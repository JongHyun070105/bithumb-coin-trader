from __future__ import annotations

from bithumb_coin_trader.session_evidence import (
    FeedIdentity,
    SessionEvidenceTracker,
)


def test_reconnect_requires_new_owner_confirmation() -> None:
    tracker = SessionEvidenceTracker(epoch="epoch-1", run_id="run-1")
    requested = ["upbit/ticker/KRW-BTC"]
    first = tracker.open_session("upbit", requested, "2026-09-14T11:50:00Z")
    tracker.confirm(first, requested, "LIST_SUBSCRIPTIONS", "2026-09-14T11:51:00Z", {"result": []})
    assert tracker.is_confirmed(first)

    tracker.close_session(first, "2026-09-14T12:20:00Z", "socket_closed")
    second = tracker.open_session("upbit", requested, "2026-09-14T12:21:00Z")
    assert not tracker.is_confirmed(second)


def test_sorted_subscriptions_hash_determinism() -> None:
    tracker1 = SessionEvidenceTracker(epoch="epoch", run_id="run")
    tracker2 = SessionEvidenceTracker(epoch="epoch", run_id="run")

    s1 = tracker1.open_session(
        "upbit",
        ["upbit/trade/KRW-BTC", "upbit/ticker/KRW-BTC", "upbit/trade/KRW-BTC"],
        "2026-09-14T10:00:00Z",
    )
    s2 = tracker2.open_session(
        "upbit",
        ["upbit/ticker/KRW-BTC", "upbit/trade/KRW-BTC"],
        "2026-09-14T10:00:00Z",
    )

    feed = FeedIdentity("upbit", "ticker", "KRW-BTC")
    seg1 = tracker1.segments_for(feed, "2026-09-14T10:00:00Z", "2026-09-14T11:00:00Z")[0]
    seg2 = tracker2.segments_for(feed, "2026-09-14T10:00:00Z", "2026-09-14T11:00:00Z")[0]

    assert seg1.requested_feeds == ("upbit/ticker/KRW-BTC", "upbit/trade/KRW-BTC")
    assert seg2.requested_feeds == ("upbit/ticker/KRW-BTC", "upbit/trade/KRW-BTC")
    assert seg1.requested_subscription_sha256 == seg2.requested_subscription_sha256


def test_session_chronology_and_sorting() -> None:
    tracker = SessionEvidenceTracker(epoch="epoch", run_id="run")
    feed = FeedIdentity("bithumb", "orderbook", "KRW-BTC")

    tracker.open_session("bithumb", [feed.canonical], "2026-09-14T12:30:00Z")
    tracker.open_session("bithumb", [feed.canonical], "2026-09-14T11:00:00Z")
    tracker.open_session("bithumb", [feed.canonical], "2026-09-14T11:30:00Z")

    segments = tracker.segments_for(
        feed,
        "2026-09-14T10:00:00Z",
        "2026-09-14T13:00:00Z",
    )
    timestamps = [seg.connected_at_utc for seg in segments]
    assert timestamps == ["2026-09-14T11:00:00Z", "2026-09-14T11:30:00Z", "2026-09-14T12:30:00Z"]


def test_heartbeat_observation_recording() -> None:
    tracker = SessionEvidenceTracker(epoch="epoch", run_id="run")
    feed = FeedIdentity("binance", "trade", "btcusdt")
    sid = tracker.open_session("binance", [feed.canonical], "2026-09-14T12:00:00Z")
    tracker.confirm(sid, [feed.canonical], "LIST_SUBSCRIPTIONS", "2026-09-14T12:00:01Z", None)

    tracker.record_heartbeat(sid, "2026-09-14T12:00:10Z")
    tracker.record_heartbeat(sid, "2026-09-14T12:00:20Z")
    tracker.record_heartbeat(sid, "2026-09-14T12:00:30Z")

    segments = tracker.segments_for(feed, "2026-09-14T12:00:00Z", "2026-09-14T12:00:40Z")
    assert len(segments) == 1
    assert segments[0].heartbeat_observations_utc == (
        "2026-09-14T12:00:10Z",
        "2026-09-14T12:00:20Z",
        "2026-09-14T12:00:30Z",
    )
    assert segments[0].maximum_heartbeat_gap_seconds == 10.0


def test_heartbeat_scoping_and_interval_edge_gaps() -> None:
    tracker = SessionEvidenceTracker(epoch="epoch", run_id="run")
    feed = FeedIdentity("binance", "trade", "btcusdt")
    sid = tracker.open_session("binance", [feed.canonical], "2026-09-14T11:50:00Z")
    tracker.confirm(sid, [feed.canonical], "LIST_SUBSCRIPTIONS", "2026-09-14T11:51:00Z", None)

    # Record heartbeats before, during, and after interval
    tracker.record_heartbeat(sid, "2026-09-14T11:59:00Z")
    tracker.record_heartbeat(sid, "2026-09-14T12:00:10Z")
    tracker.record_heartbeat(sid, "2026-09-14T12:00:20Z")
    tracker.record_heartbeat(sid, "2026-09-14T12:00:30Z")
    tracker.record_heartbeat(sid, "2026-09-14T13:05:00Z")

    segments = tracker.segments_for(feed, "2026-09-14T12:00:00Z", "2026-09-14T12:00:40Z")
    assert len(segments) == 1
    # Only in-interval heartbeats should be retained
    assert segments[0].heartbeat_observations_utc == (
        "2026-09-14T12:00:10Z",
        "2026-09-14T12:00:20Z",
        "2026-09-14T12:00:30Z",
    )
    # Gaps: (12:00:10 - 12:00:00)=10, (12:00:20 - 12:00:10)=10, (12:00:30 - 12:00:20)=10, (12:00:40 - 12:00:30)=10
    assert segments[0].maximum_heartbeat_gap_seconds == 10.0


def test_session_segments_filtering_by_interval() -> None:
    tracker = SessionEvidenceTracker(epoch="epoch", run_id="run")
    feed = FeedIdentity("upbit", "ticker", "KRW-BTC")

    # Session 1: 09:00 - 10:00 (before 12:00 - 13:00)
    s1 = tracker.open_session("upbit", [feed.canonical], "2026-09-14T09:00:00Z")
    tracker.close_session(s1, "2026-09-14T10:00:00Z", "closed")

    # Session 2: 11:30 - 12:30 (overlaps 12:00 - 13:00)
    s2 = tracker.open_session("upbit", [feed.canonical], "2026-09-14T11:30:00Z")
    tracker.close_session(s2, "2026-09-14T12:30:00Z", "reconnect")

    # Session 3: 12:30 - ongoing (overlaps 12:00 - 13:00)
    s3 = tracker.open_session("upbit", [feed.canonical], "2026-09-14T12:30:00Z")

    # Session 4: 14:00 - 15:00 (after 12:00 - 13:00)
    s4 = tracker.open_session("upbit", [feed.canonical], "2026-09-14T14:00:00Z")
    tracker.close_session(s4, "2026-09-14T15:00:00Z", "closed")

    # Session 5: different feed / exchange
    tracker.open_session("bithumb", ["bithumb/orderbook/KRW-ETH"], "2026-09-14T12:00:00Z")

    segments = tracker.segments_for(feed, "2026-09-14T12:00:00Z", "2026-09-14T13:00:00Z")
    assert len(segments) == 2
    assert [seg.session_id for seg in segments] == [s2, s3]


def test_feed_identity_canonical_normalization() -> None:
    bithumb_feed = FeedIdentity("BITHUMB", "ORDERBOOK", "krw-btc")
    assert bithumb_feed.exchange == "bithumb"
    assert bithumb_feed.stream == "orderbook"
    assert bithumb_feed.market == "KRW-BTC"
    assert bithumb_feed.canonical == "bithumb/orderbook/KRW-BTC"

    binance_feed = FeedIdentity("Binance", "TRADE", "BTCUSDT")
    assert binance_feed.exchange == "binance"
    assert binance_feed.stream == "trade"
    assert binance_feed.market == "btcusdt"
    assert binance_feed.canonical == "binance/trade/btcusdt"
