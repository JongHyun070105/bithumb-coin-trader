from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from bithumb_coin_trader.evidence_hashing import canonical_sha256
from bithumb_coin_trader.feed_hour_coverage import (
    DataArtifactBinding,
    FeedHourCoverage,
    FeedHourCoverageTracker,
    FrozenFeedHourObservation,
    load_feed_hour_coverage,
    materialize_feed_hour_coverage,
    save_feed_hour_coverage,
)
from bithumb_coin_trader.session_evidence import (
    FeedIdentity,
    HeartbeatPolicy,
    SessionEvidenceTracker,
    SessionSegment,
    WriterHealthSnapshot,
)


def _make_feed(
    exchange: str = "bithumb",
    stream: str = "orderbook",
    market: str = "KRW-BTC",
) -> FeedIdentity:
    return FeedIdentity(exchange=exchange, stream=stream, market=market)


def _make_policy(max_gap: int = 30) -> HeartbeatPolicy:
    return HeartbeatPolicy(
        heartbeat_probe_interval_seconds=10,
        heartbeat_timeout_seconds=10,
        max_allowed_heartbeat_gap_seconds={"bithumb": max_gap, "binance": max_gap, "upbit": max_gap},
    )


def _make_binding(
    record_count: int = 10,
    manifest_count: int | None = None,
    receipt_count: int | None = None,
) -> DataArtifactBinding:
    return DataArtifactBinding(
        raw_relative_path="raw/2026-09-14_12/bithumb/orderbook/KRW-BTC.raw.jsonl.zst",
        raw_size=1024,
        raw_sha256="a" * 64,
        manifest_relative_path="manifests/2026-09-14_12/bithumb/orderbook/KRW-BTC.manifest.json",
        manifest_file_sha256="b" * 64,
        manifest_record_count=manifest_count if manifest_count is not None else record_count,
        receipt_relative_path="receipts/2026-09-14_12/bithumb/orderbook/KRW-BTC.receipt.json",
        receipt_file_sha256="c" * 64,
        receipt_source_record_count=receipt_count if receipt_count is not None else record_count,
    )


def _make_segment(
    feed: FeedIdentity,
    interval_start_utc: str = "2026-09-14T12:00:00Z",
    interval_end_utc: str = "2026-09-14T13:00:00Z",
    heartbeats: tuple[str, ...] | None = None,
    max_gap: float | None = None,
    confirmed: bool = True,
    connected_at_utc: str = "2026-09-14T11:50:00Z",
    disconnected_at_utc: str | None = None,
    disconnect_reason: str | None = None,
    reconnect_successor_id: str | None = None,
) -> SessionSegment:
    if heartbeats is None:
        # Generate heartbeats every 10 seconds across the hour
        hb_list = []
        base = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
        for sec in range(0, 3601, 10):
            ts = datetime.fromtimestamp(base.timestamp() + sec, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            hb_list.append(ts)
        heartbeats = tuple(hb_list)

    return SessionSegment(
        exchange=feed.exchange,
        session_id="sess-001",
        connected_at_utc=connected_at_utc,
        disconnected_at_utc=disconnected_at_utc,
        requested_feeds=(feed.canonical,),
        requested_subscription_sha256="hash-req",
        confirmation_method="LIST_SUBSCRIPTIONS" if confirmed else None,
        confirmed_at_utc="2026-09-14T11:51:00Z" if confirmed else None,
        confirmed_feeds=(feed.canonical,) if confirmed else (),
        confirmed_subscription_sha256="hash-conf" if confirmed else None,
        response_evidence_sha256="hash-resp" if confirmed else None,
        heartbeat_observations_utc=heartbeats,
        maximum_heartbeat_gap_seconds=max_gap if max_gap is not None else 10.0,
        disconnect_reason=disconnect_reason,
        reconnect_successor_id=reconnect_successor_id,
        collector_epoch="epoch-1",
        collector_run_id="run-1",
    )


def _make_observation(
    feed: FeedIdentity | None = None,
    event_count: int = 10,
    cohort_qualification: str = "QUALIFYING_FULL_HOUR",
    session_segments: tuple[SessionSegment, ...] | None = None,
    maximum_heartbeat_gap_seconds: float | None = None,
    disconnect_count: int = 0,
    reconnect_count: int = 0,
    health: WriterHealthSnapshot | None = None,
    interval_start_utc: str = "2026-09-14T12:00:00Z",
    interval_end_utc: str = "2026-09-14T13:00:00Z",
) -> FrozenFeedHourObservation:
    f = feed or _make_feed()
    if session_segments is None:
        if maximum_heartbeat_gap_seconds is not None:
            hb_list = [interval_start_utc]
            base = datetime.fromisoformat(interval_start_utc.replace("Z", "+00:00"))
            t1 = datetime.fromtimestamp(base.timestamp() + maximum_heartbeat_gap_seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            hb_list.append(t1)
            cur = base.timestamp() + maximum_heartbeat_gap_seconds
            end = datetime.fromisoformat(interval_end_utc.replace("Z", "+00:00")).timestamp()
            while cur + 10 < end:
                cur += 10
                hb_list.append(datetime.fromtimestamp(cur, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            hb_list.append(interval_end_utc)
            session_segments = (_make_segment(f, interval_start_utc, interval_end_utc, heartbeats=tuple(hb_list), max_gap=maximum_heartbeat_gap_seconds),)
        else:
            session_segments = (_make_segment(f, interval_start_utc, interval_end_utc),)

    return FrozenFeedHourObservation(
        feed=f,
        cohort_utc="2026-09-14_12",
        interval_start_utc=interval_start_utc,
        interval_end_utc=interval_end_utc,
        cohort_qualification=cohort_qualification,
        observation_start_utc=interval_start_utc,
        observation_end_utc=interval_end_utc,
        event_count=event_count,
        first_event_timestamp="2026-09-14T12:05:00Z" if event_count > 0 else None,
        last_event_timestamp="2026-09-14T12:55:00Z" if event_count > 0 else None,
        session_segments=session_segments,
        disconnect_count=disconnect_count,
        reconnect_count=reconnect_count,
        health=health or WriterHealthSnapshot(),
    )


def test_data_present_success_at_exact_30s_gap() -> None:
    obs = _make_observation(event_count=10, maximum_heartbeat_gap_seconds=30.0)
    res = materialize_feed_hour_coverage(obs, _make_policy(max_gap=30), _make_binding(record_count=10))
    assert res.coverage_state == "DATA_PRESENT"
    assert res.failure_reason_codes == ()


def test_data_present_fails_at_31_second_gap() -> None:
    obs = _make_observation(event_count=10, maximum_heartbeat_gap_seconds=31.0)
    res = materialize_feed_hour_coverage(obs, _make_policy(max_gap=30), _make_binding(record_count=10))
    assert res.coverage_state == "FAILED"
    assert "HEARTBEAT_GAP_EXCEEDED" in res.failure_reason_codes


def test_zero_event_has_null_raw_binding_and_stable_hash() -> None:
    obs = _make_observation(event_count=0)
    coverage = materialize_feed_hour_coverage(obs, _make_policy(), None)
    assert coverage.coverage_state == "VERIFIED_ZERO_EVENT"
    assert coverage.data_artifact_binding is None
    assert coverage.failure_reason_codes == ()
    expected_hash = canonical_sha256(coverage.to_dict(), excluded=("evidence_sha256",))
    assert coverage.evidence_sha256 == expected_hash


def test_zero_event_rejected_for_touched_partial() -> None:
    obs = _make_observation(event_count=0, cohort_qualification="TOUCHED_PARTIAL")
    res = materialize_feed_hour_coverage(obs, _make_policy(), None)
    assert res.coverage_state == "FAILED"
    assert "ZERO_EVENT_FORBIDDEN_FOR_PARTIAL_COHORT" in res.failure_reason_codes


def test_positive_count_requires_matching_data_binding() -> None:
    obs = _make_observation(event_count=10)
    # Missing binding
    res1 = materialize_feed_hour_coverage(obs, _make_policy(), None)
    assert res1.coverage_state == "FAILED"
    assert "MISSING_DATA_BINDING" in res1.failure_reason_codes

    # Manifest record count mismatch
    binding_mismatch = _make_binding(record_count=10, manifest_count=9, receipt_count=10)
    res2 = materialize_feed_hour_coverage(obs, _make_policy(), binding_mismatch)
    assert res2.coverage_state == "FAILED"
    assert "EVENT_COUNT_MISMATCH" in res2.failure_reason_codes

    # Receipt source record count mismatch
    binding_mismatch2 = _make_binding(record_count=10, manifest_count=10, receipt_count=8)
    res3 = materialize_feed_hour_coverage(obs, _make_policy(), binding_mismatch2)
    assert res3.coverage_state == "FAILED"
    assert "EVENT_COUNT_MISMATCH" in res3.failure_reason_codes


def test_writer_health_degraded_fails_coverage() -> None:
    for health_arg in [
        WriterHealthSnapshot(writer_error_count=1),
        WriterHealthSnapshot(queue_dropped_events=1),
        WriterHealthSnapshot(unpersisted_event_count=1),
        WriterHealthSnapshot(fatal_writer_error_type="DiskFull"),
    ]:
        obs = _make_observation(event_count=10, health=health_arg)
        res = materialize_feed_hour_coverage(obs, _make_policy(), _make_binding(10))
        assert res.coverage_state == "FAILED"
        assert "WRITER_HEALTH_DEGRADED" in res.failure_reason_codes


def test_reconnect_gap_fails_coverage() -> None:
    # 1. disconnect_count > 0
    obs1 = _make_observation(event_count=10, disconnect_count=1)
    res1 = materialize_feed_hour_coverage(obs1, _make_policy(), _make_binding(10))
    assert res1.coverage_state == "FAILED"
    assert "COLLECTION_GAP" in res1.failure_reason_codes

    # 2. segments with gap between them
    f = _make_feed()
    seg1 = _make_segment(f, connected_at_utc="2026-09-14T11:50:00Z", disconnected_at_utc="2026-09-14T12:20:00Z")
    seg2 = _make_segment(f, connected_at_utc="2026-09-14T12:25:00Z", disconnected_at_utc=None)
    obs2 = _make_observation(event_count=10, session_segments=(seg1, seg2))
    res2 = materialize_feed_hour_coverage(obs2, _make_policy(), _make_binding(10))
    assert res2.coverage_state == "FAILED"
    assert "COLLECTION_GAP" in res2.failure_reason_codes


def test_unconfirmed_session_fails_coverage() -> None:
    f = _make_feed()
    unconfirmed = _make_segment(f, confirmed=False)
    obs = _make_observation(event_count=10, session_segments=(unconfirmed,))
    res = materialize_feed_hour_coverage(obs, _make_policy(), _make_binding(10))
    assert res.coverage_state == "FAILED"
    assert "SESSION_NOT_CONFIRMED" in res.failure_reason_codes


def test_missing_heartbeat_policy_fails_coverage() -> None:
    obs = _make_observation(event_count=10)
    empty_policy = HeartbeatPolicy(
        heartbeat_probe_interval_seconds=10,
        heartbeat_timeout_seconds=10,
        max_allowed_heartbeat_gap_seconds={},  # no threshold for bithumb
    )
    res = materialize_feed_hour_coverage(obs, empty_policy, _make_binding(10))
    assert res.coverage_state == "FAILED"
    assert "MISSING_HEARTBEAT_POLICY" in res.failure_reason_codes


def test_edge_heartbeat_gap_fails_coverage() -> None:
    f = _make_feed()
    # First heartbeat at 12:00:35Z -> edge gap from 12:00:00Z is 35s > 30s
    hb_list = ["2026-09-14T12:00:35Z", "2026-09-14T12:00:45Z", "2026-09-14T13:00:00Z"]
    seg = _make_segment(f, heartbeats=tuple(hb_list))
    obs = _make_observation(event_count=10, session_segments=(seg,))
    res = materialize_feed_hour_coverage(obs, _make_policy(max_gap=30), _make_binding(10))
    assert res.coverage_state == "FAILED"
    assert "HEARTBEAT_GAP_EXCEEDED" in res.failure_reason_codes


def test_coverage_save_and_load_roundtrip(tmp_path: Path) -> None:
    obs = _make_observation(event_count=10)
    coverage = materialize_feed_hour_coverage(obs, _make_policy(), _make_binding(10))

    saved_path = save_feed_hour_coverage(coverage, tmp_path)
    assert saved_path.exists()
    assert saved_path == tmp_path / "coverage" / "2026-09-14_12" / "bithumb" / "orderbook" / "KRW-BTC.coverage.json"

    loaded = load_feed_hour_coverage(saved_path)
    assert loaded == coverage
    assert loaded.evidence_sha256 == coverage.evidence_sha256


def test_coverage_tampered_hash_fails_load(tmp_path: Path) -> None:
    obs = _make_observation(event_count=10)
    coverage = materialize_feed_hour_coverage(obs, _make_policy(), _make_binding(10))
    saved_path = save_feed_hour_coverage(coverage, tmp_path)

    # Tamper with file
    data = json.loads(saved_path.read_text(encoding="utf-8"))
    data["event_count"] = 999
    saved_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="TAMPERED_COVERAGE_HASH"):
        load_feed_hour_coverage(saved_path)


def test_writer_clock_regression_raises() -> None:
    feed = _make_feed()
    tracker = FeedHourCoverageTracker([feed])
    ts1 = datetime(2026, 9, 14, 12, 5, 0, tzinfo=timezone.utc)
    ts0 = datetime(2026, 9, 14, 12, 4, 0, tzinfo=timezone.utc)

    tracker.record_persisted_event(feed, ts1)
    with pytest.raises(ValueError, match="WRITER_CLOCK_REGRESSION"):
        tracker.record_persisted_event(feed, ts0)


def test_tracker_freeze_completed_and_shutdown() -> None:
    feed1 = _make_feed("bithumb", "orderbook", "KRW-BTC")
    feed2 = _make_feed("upbit", "ticker", "KRW-BTC")
    tracker = FeedHourCoverageTracker([feed1, feed2])

    sessions = SessionEvidenceTracker("epoch-1", "run-1")
    s1 = sessions.open_session("bithumb", [feed1.canonical], "2026-09-14T11:50:00Z")
    sessions.confirm(s1, [feed1.canonical], "frame", "2026-09-14T11:51:00Z", None)

    s2 = sessions.open_session("upbit", [feed2.canonical], "2026-09-14T11:50:00Z")
    sessions.confirm(s2, [feed2.canonical], "LIST_SUBSCRIPTIONS", "2026-09-14T11:51:00Z", None)

    # Record events in 12:00-13:00 hour
    tracker.record_persisted_event(feed1, datetime(2026, 9, 14, 12, 10, 0, tzinfo=timezone.utc))
    tracker.record_persisted_event(feed1, datetime(2026, 9, 14, 12, 20, 0, tzinfo=timezone.utc))
    # feed2 has 0 events in this hour

    boundary = datetime(2026, 9, 14, 13, 0, 0, tzinfo=timezone.utc)
    frozen = tracker.freeze_completed(boundary, sessions, WriterHealthSnapshot())
    assert len(frozen) == 2

    f1_obs = next(f for f in frozen if f.feed == feed1)
    assert f1_obs.event_count == 2
    assert f1_obs.cohort_qualification == "QUALIFYING_FULL_HOUR"
    assert f1_obs.first_event_timestamp == "2026-09-14T12:10:00Z"
    assert f1_obs.last_event_timestamp == "2026-09-14T12:20:00Z"

    f2_obs = next(f for f in frozen if f.feed == feed2)
    assert f2_obs.event_count == 0
    assert f2_obs.cohort_qualification == "QUALIFYING_FULL_HOUR"
    assert f2_obs.first_event_timestamp is None
    assert f2_obs.last_event_timestamp is None

    # Now shutdown in partial hour 13:15:00Z
    tracker.record_persisted_event(feed1, datetime(2026, 9, 14, 13, 5, 0, tzinfo=timezone.utc))
    shutdown_time = datetime(2026, 9, 14, 13, 15, 0, tzinfo=timezone.utc)
    partial_frozen = tracker.freeze_shutdown(shutdown_time, sessions, WriterHealthSnapshot())
    assert len(partial_frozen) == 2
    for p in partial_frozen:
        assert p.cohort_qualification == "TOUCHED_PARTIAL"
        assert p.observation_end_utc == "2026-09-14T13:15:00Z"


def test_ending_heartbeat_gap_exceeded_fails() -> None:
    f = _make_feed()
    # Heartbeats end at 12:59:25Z -> gap to 13:00:00Z is 35s > 30s
    hb_list = []
    base = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
    # Every 10s up to 3565s (12:59:25)
    for sec in range(0, 3566, 10):
        ts = datetime.fromtimestamp(base.timestamp() + sec, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        hb_list.append(ts)

    seg = _make_segment(f, heartbeats=tuple(hb_list))
    obs = _make_observation(event_count=10, session_segments=(seg,))
    res = materialize_feed_hour_coverage(obs, _make_policy(max_gap=30), _make_binding(10))
    assert res.coverage_state == "FAILED"
    assert "HEARTBEAT_GAP_EXCEEDED" in res.failure_reason_codes


def test_late_confirmation_fails() -> None:
    f = _make_feed()
    seg = _make_segment(f)
    # Session confirmed at 12:05:00Z (after interval_start_utc 12:00:00Z)
    seg_late = replace(seg, confirmed_at_utc="2026-09-14T12:05:00Z")
    obs = _make_observation(event_count=10, session_segments=(seg_late,))
    res = materialize_feed_hour_coverage(obs, _make_policy(), _make_binding(10))
    assert res.coverage_state == "FAILED"
    assert "LATE_CONFIRMATION" in res.failure_reason_codes


def test_verified_zero_event_rejects_non_null_timestamps() -> None:
    obs = _make_observation(event_count=0)
    # Corrupt zero-event observation with non-null timestamp
    corrupt_obs = replace(obs, first_event_timestamp="2026-09-14T12:05:00Z")
    res = materialize_feed_hour_coverage(corrupt_obs, _make_policy(), None)
    assert res.coverage_state == "FAILED"
    assert "INVALID_ZERO_EVENT_TIMESTAMPS" in res.failure_reason_codes

    corrupt_obs2 = replace(obs, last_event_timestamp="2026-09-14T12:55:00Z")
    res2 = materialize_feed_hour_coverage(corrupt_obs2, _make_policy(), None)
    assert res2.coverage_state == "FAILED"
    assert "INVALID_ZERO_EVENT_TIMESTAMPS" in res2.failure_reason_codes


def test_record_persisted_event_rejects_frozen_cohort() -> None:
    feed = _make_feed()
    tracker = FeedHourCoverageTracker([feed], actual_start_utc=datetime(2026, 9, 14, 11, 0, 0, tzinfo=timezone.utc))
    sessions = SessionEvidenceTracker("epoch-1", "run-1")
    tracker.record_persisted_event(feed, datetime(2026, 9, 14, 12, 10, 0, tzinfo=timezone.utc))

    boundary = datetime(2026, 9, 14, 13, 0, 0, tzinfo=timezone.utc)
    tracker.freeze_completed(boundary, sessions, WriterHealthSnapshot())

    # Attempt write to already frozen cohort 2026-09-14_12
    with pytest.raises(ValueError, match="COHORT_ALREADY_FROZEN: 2026-09-14_12"):
        tracker.record_persisted_event(feed, datetime(2026, 9, 14, 12, 30, 0, tzinfo=timezone.utc))


def test_disconnect_count_scoped_to_cohort_interval() -> None:
    feed = _make_feed()
    tracker = FeedHourCoverageTracker([feed], actual_start_utc=datetime(2026, 9, 14, 11, 0, 0, tzinfo=timezone.utc))
    sessions = SessionEvidenceTracker("epoch-1", "run-1")

    sid = sessions.open_session(feed.exchange, [feed.canonical], "2026-09-14T11:50:00Z")
    sessions.confirm(sid, [feed.canonical], "LIST_SUBSCRIPTIONS", "2026-09-14T11:51:00Z", None)
    sessions.record_heartbeat(sid, "2026-09-14T12:00:00Z")
    sessions.record_heartbeat(sid, "2026-09-14T13:00:00Z")
    # Disconnect occurs in hour 13, not hour 12
    sessions.close_session(sid, "2026-09-14T13:10:00Z", "conn_reset")

    # Freeze hour 12 (12:00:00Z to 13:00:00Z)
    obs_12 = tracker.freeze_completed(datetime(2026, 9, 14, 13, 0, 0, tzinfo=timezone.utc), sessions, WriterHealthSnapshot())
    assert obs_12[0].disconnect_count == 0
    assert obs_12[0].reconnect_count == 0

    # Freeze hour 13 (13:00:00Z to 14:00:00Z)
    obs_13 = tracker.freeze_completed(datetime(2026, 9, 14, 14, 0, 0, tzinfo=timezone.utc), sessions, WriterHealthSnapshot())
    assert obs_13[0].disconnect_count == 1
    assert obs_13[0].reconnect_count == 0


def test_opening_cohort_marked_touched_partial() -> None:
    feed = _make_feed()
    # Actual start is 12:00:00 (exact hour boundary)
    tracker = FeedHourCoverageTracker([feed], actual_start_utc=datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc))
    sessions = SessionEvidenceTracker("epoch-1", "run-1")
    sid = sessions.open_session(feed.exchange, [feed.canonical], "2026-09-14T12:00:00Z")
    sessions.confirm(sid, [feed.canonical], "LIST_SUBSCRIPTIONS", "2026-09-14T12:00:01Z", None)

    # Freeze completed hour 12 (12:00 to 13:00)
    obs_12 = tracker.freeze_completed(datetime(2026, 9, 14, 13, 0, 0, tzinfo=timezone.utc), sessions, WriterHealthSnapshot())
    assert obs_12[0].cohort_qualification == "TOUCHED_PARTIAL"
    assert obs_12[0].observation_start_utc == "2026-09-14T12:00:00Z"

    # Freeze completed hour 13 (13:00 to 14:00)
    obs_13 = tracker.freeze_completed(datetime(2026, 9, 14, 14, 0, 0, tzinfo=timezone.utc), sessions, WriterHealthSnapshot())
    assert obs_13[0].cohort_qualification == "QUALIFYING_FULL_HOUR"
    assert obs_13[0].observation_start_utc == "2026-09-14T13:00:00Z"


def test_coverage_save_already_coverage_dir(tmp_path: Path) -> None:
    obs = _make_observation(event_count=10)
    coverage = materialize_feed_hour_coverage(obs, _make_policy(), _make_binding(10))

    cov_dir = tmp_path / "coverage"
    saved_path = save_feed_hour_coverage(coverage, cov_dir)
    assert saved_path == cov_dir / "2026-09-14_12" / "bithumb" / "orderbook" / "KRW-BTC.coverage.json"
