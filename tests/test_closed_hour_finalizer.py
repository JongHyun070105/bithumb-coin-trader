"""Unit tests for ClosedHourFinalizer enforcing ordered V3 coverage chains.

Follows TDD (RED -> GREEN -> REFACTOR).
Enforces:
1. Positive order: manifest_raw -> archive_raw -> verify_raw_restore -> materialize_data_present -> archive_coverage -> verify_coverage_restore
2. Zero event order: skips RAW entirely, materializes verified zero event, archives coverage, verifies restore
3. Count mismatch: event_count != manifest.record_count != receipt.source_record_count yields FAILED coverage state with RECORD_COUNT_MISMATCH
4. Common gate failures (WRITER_HEALTH_DEGRADED, SESSION_NOT_CONFIRMED, HEARTBEAT_GAP_EXCEEDED, COLLECTION_GAP) yield FAILED diagnostic coverage
5. Cohort finalization: validates exact 76 sealed feed identities (rejects missing, duplicate, foreign) and returns 76 ClosedSlotResults
6. Restart idempotency: existing receipts are verified and reused without recomputation errors
7. Progress store transition: marks registered pending entries as REUSED with valid data binding
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pwd
import pytest

from bithumb_coin_trader.closed_hour_finalizer import (
    SEALED_FEED_UNIVERSE,
    ClosedHourFinalizer,
    evaluate_common_gate,
)
from bithumb_coin_trader.feed_hour_coverage import (
    FrozenFeedHourObservation,
    save_frozen_journal,
)
from bithumb_coin_trader.incremental_finalizer import (
    FinalizationIdentity,
    FinalizationProgressStore,
    FinalizationState,
)
from bithumb_coin_trader.microstructure_storage import (
    RawMicrostructureStorage,
)
from bithumb_coin_trader.pre_soak_archive import (
    ArchivePipeline,
    FileArchiveStore,
)
from bithumb_coin_trader.session_evidence import (
    FeedIdentity,
    HeartbeatPolicy,
    SessionSegment,
    WriterHealthSnapshot,
)


def _current_user() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def _make_policy(max_gap: int = 30) -> HeartbeatPolicy:
    return HeartbeatPolicy(
        heartbeat_probe_interval_seconds=10,
        heartbeat_timeout_seconds=10,
        max_allowed_heartbeat_gap_seconds={"bithumb": max_gap, "binance": max_gap, "upbit": max_gap},
    )


def _make_segment(
    feed: FeedIdentity,
    interval_start_utc: str = "2026-09-14T12:00:00Z",
    interval_end_utc: str = "2026-09-14T13:00:00Z",
    confirmed: bool = True,
    max_gap: float = 10.0,
) -> SessionSegment:
    hb_list = []
    base = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
    for sec in range(0, 3601, 10):
        ts = datetime.fromtimestamp(base.timestamp() + sec, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        hb_list.append(ts)

    return SessionSegment(
        exchange=feed.exchange,
        session_id="sess-001",
        connected_at_utc="2026-09-14T11:50:00Z",
        disconnected_at_utc=None,
        requested_feeds=(feed.canonical,),
        requested_subscription_sha256="hash-req",
        confirmation_method="LIST_SUBSCRIPTIONS" if confirmed else None,
        confirmed_at_utc="2026-09-14T11:51:00Z" if confirmed else None,
        confirmed_feeds=(feed.canonical,) if confirmed else (),
        confirmed_subscription_sha256="hash-conf" if confirmed else None,
        response_evidence_sha256="hash-resp" if confirmed else None,
        heartbeat_observations_utc=tuple(hb_list),
        maximum_heartbeat_gap_seconds=max_gap,
        disconnect_reason=None,
        reconnect_successor_id=None,
        collector_epoch="epoch-1",
        collector_run_id="run-1",
    )


def _make_observation(
    feed: FeedIdentity,
    event_count: int = 10,
    cohort_qualification: str = "QUALIFYING_FULL_HOUR",
    health: WriterHealthSnapshot | None = None,
    progress_entry_id: str | None = None,
    confirmed: bool = True,
    cohort_utc: str = "2026-09-14_12",
    session_segments: tuple[SessionSegment, ...] | None = None,
) -> FrozenFeedHourObservation:
    interval_start = "2026-09-14T12:00:00Z"
    interval_end = "2026-09-14T13:00:00Z"
    segments = session_segments if session_segments is not None else (_make_segment(feed, interval_start, interval_end, confirmed=confirmed),)
    return FrozenFeedHourObservation(
        feed=feed,
        cohort_utc=cohort_utc,
        interval_start_utc=interval_start,
        interval_end_utc=interval_end,
        cohort_qualification=cohort_qualification,
        observation_start_utc=interval_start,
        observation_end_utc=interval_end,
        event_count=event_count,
        first_event_timestamp="2026-09-14T12:05:00Z" if event_count > 0 else None,
        last_event_timestamp="2026-09-14T12:55:00Z" if event_count > 0 else None,
        session_segments=segments,
        disconnect_count=0,
        reconnect_count=0,
        health=health or WriterHealthSnapshot(),
        progress_entry_id=progress_entry_id,
    )


class FixtureBundle:
    def __init__(self, tmp_path: Path, calls: list[str] | None = None) -> None:
        self.tmp_path = tmp_path
        self.raw_root = tmp_path / "raw"
        self.manifest_root = tmp_path / "manifests"
        self.compressed_root = tmp_path / "compressed"
        self.receipt_root = tmp_path / "archive-receipts"
        self.coverage_dir = tmp_path / "coverage"
        self.journals_dir = self.coverage_dir / "journals"
        self.progress_dir = tmp_path / "progress"

        for d in (
            self.raw_root,
            self.manifest_root,
            self.compressed_root,
            self.receipt_root,
            self.coverage_dir,
            self.journals_dir,
            self.progress_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

        self.calls = calls if calls is not None else []
        self.raw_store = FileArchiveStore(tmp_path / "remote-raw")
        self.cov_store = FileArchiveStore(tmp_path / "remote-cov")
        user = _current_user()

        self.raw_archive = ArchivePipeline(
            raw_root=self.raw_root,
            manifest_root=self.manifest_root,
            compressed_root=self.compressed_root,
            receipt_root=self.receipt_root,
            store=self.raw_store,
            environment_id="test-env",
            run_id="run-1",
            collector_epoch="epoch-1",
            remote_prefix="market-data/temporary/raw",
            expected_owner=user,
            disk_critical_percent=99.0,
        )

        self.coverage_archive = ArchivePipeline(
            raw_root=self.coverage_dir,
            manifest_root=self.manifest_root,
            compressed_root=self.compressed_root / "coverage",
            receipt_root=self.receipt_root / "coverage",
            store=self.cov_store,
            environment_id="test-env",
            run_id="run-1",
            collector_epoch="epoch-1",
            remote_prefix="market-data/temporary/coverage",
            expected_owner=user,
            disk_critical_percent=99.0,
        )

        self.policy = _make_policy()
        self.progress_store = FinalizationProgressStore(self.progress_dir)

        self.finalizer = ClosedHourFinalizer(
            raw_archive=self.raw_archive,
            coverage_archive=self.coverage_archive,
            heartbeat_policy=self.policy,
            progress_store=self.progress_store,
            journals_dir=self.journals_dir,
            coverage_dir=self.coverage_dir,
            environment_id="test-env",
            runtime_commit="test-commit",
            runtime_config_fingerprint="fp-test",
            stability_wait_seconds=0.0,
            tracer=self.calls,
        )

    def prepare_raw_feed(
        self,
        feed: FeedIdentity,
        cohort_utc: str = "2026-09-14_12",
        record_count: int = 10,
        manifest_count: int | None = None,
    ) -> tuple[FrozenFeedHourObservation, Path, Path]:
        dt_str, hour_str = cohort_utc.split("_")
        clean_market = feed.market.replace("/", "-").replace(":", "-").lower()
        part_dir = self.raw_root / dt_str / feed.exchange / feed.stream
        part_dir.mkdir(parents=True, exist_ok=True)
        raw_file = part_dir / f"{feed.exchange}_{feed.stream}_{clean_market}_{dt_str}_{hour_str}.jsonl"

        lines = []
        for i in range(record_count):
            rec = {
                "exchange": feed.exchange,
                "stream": feed.stream,
                "market": feed.market,
                "local_write_ts": f"{dt_str}T{hour_str}:{i:02d}:00Z",
                "payload": {"i": i},
            }
            lines.append(json.dumps(rec) + "\n")
        raw_file.write_text("".join(lines), encoding="utf-8")

        # Create manifest
        storage = RawMicrostructureStorage(self.raw_root, manifest_dir=self.manifest_root)
        storage.generate_partition_manifest(raw_file)
        manifest_file = self.manifest_root / f"manifest_{raw_file.stem}.json"

        if manifest_count is not None and manifest_count != record_count:
            # Deliberately tamper manifest record count for mismatch testing
            m_data = json.loads(manifest_file.read_text(encoding="utf-8"))
            m_data["record_count"] = manifest_count
            manifest_file.write_text(json.dumps(m_data), encoding="utf-8")

        # Register in progress store
        raw_rel = str(raw_file.relative_to(self.raw_root))
        identity = FinalizationIdentity(
            environment_id="test-env",
            collector_epoch="epoch-1",
            collector_run_id="run-1",
            cohort=cohort_utc,
            exchange=feed.exchange,
            stream=feed.stream,
            market=feed.market,
            feed_identity=feed.canonical,
            raw_relative_path=raw_rel,
        )
        entry = self.progress_store.register_pending(identity)

        obs = _make_observation(
            feed=feed,
            event_count=record_count,
            cohort_utc=cohort_utc,
            progress_entry_id=entry.entry_id,
        )
        return obs, raw_file, manifest_file


def test_positive_order(tmp_path: Path) -> None:
    calls: list[str] = []
    bundle = FixtureBundle(tmp_path, calls)
    feed = FeedIdentity("bithumb", "orderbook", "KRW-BTC")
    obs, _, _ = bundle.prepare_raw_feed(feed, record_count=5)

    result = bundle.finalizer.finalize_slot(obs)

    assert calls == [
        "manifest_raw",
        "archive_raw",
        "verify_raw_restore",
        "materialize_data_present",
        "archive_coverage",
        "verify_coverage_restore",
    ]
    assert result.coverage.coverage_state == "DATA_PRESENT"
    assert result.raw_receipt is not None
    assert result.raw_receipt.restore_verified_at is not None
    assert result.raw_receipt.source_record_count == 5
    assert result.coverage_receipt is not None
    assert result.coverage_receipt.restore_verified_at is not None
    assert len(result.failure_reason_codes) == 0


def test_zero_skips_raw(tmp_path: Path) -> None:
    calls: list[str] = []
    bundle = FixtureBundle(tmp_path, calls)
    feed = FeedIdentity("bithumb", "orderbook", "KRW-BTC")
    obs = _make_observation(feed, event_count=0)

    result = bundle.finalizer.finalize_slot(obs)

    assert calls == [
        "materialize_verified_zero_event",
        "archive_coverage",
        "verify_coverage_restore",
    ]
    assert result.coverage.coverage_state == "VERIFIED_ZERO_EVENT"
    assert result.raw_receipt is None
    assert result.coverage_receipt is not None
    assert result.coverage_receipt.restore_verified_at is not None
    assert len(result.failure_reason_codes) == 0


def test_count_mismatch_fails(tmp_path: Path) -> None:
    calls: list[str] = []
    bundle = FixtureBundle(tmp_path, calls)
    feed = FeedIdentity("bithumb", "orderbook", "KRW-BTC")
    obs, _, _ = bundle.prepare_raw_feed(feed, record_count=10, manifest_count=9)

    result = bundle.finalizer.finalize_slot(obs)

    assert result.coverage.coverage_state == "FAILED"
    assert "RECORD_COUNT_MISMATCH" in result.coverage.failure_reason_codes or "EVENT_COUNT_MISMATCH" in result.coverage.failure_reason_codes


def test_gate_failure_skips_raw_and_persists_diagnostic_coverage(tmp_path: Path) -> None:
    calls: list[str] = []
    bundle = FixtureBundle(tmp_path, calls)
    feed = FeedIdentity("bithumb", "orderbook", "KRW-BTC")
    obs = _make_observation(
        feed,
        event_count=10,
        health=WriterHealthSnapshot(writer_error_count=2),
    )

    result = bundle.finalizer.finalize_slot(obs)

    assert result.coverage.coverage_state == "FAILED"
    assert "WRITER_HEALTH_DEGRADED" in result.coverage.failure_reason_codes
    assert result.raw_receipt is None
    assert result.coverage_receipt is not None
    assert "archive_raw" not in calls
    assert "archive_coverage" in calls


def test_progress_store_transitions_to_reused(tmp_path: Path) -> None:
    bundle = FixtureBundle(tmp_path)
    feed = FeedIdentity("bithumb", "orderbook", "KRW-BTC")
    obs, _, _ = bundle.prepare_raw_feed(feed, record_count=5)

    assert obs.progress_entry_id is not None
    entry_before = bundle.progress_store.get_entry(obs.progress_entry_id)
    assert entry_before.state == FinalizationState.PENDING

    result = bundle.finalizer.finalize_slot(obs)

    assert result.coverage.coverage_state == "DATA_PRESENT"
    entry_after = bundle.progress_store.get_entry(obs.progress_entry_id)
    assert entry_after.state == FinalizationState.REUSED
    assert entry_after.source_record_count == 5
    assert entry_after.receipt_relative_path is not None


def test_restart_idempotency(tmp_path: Path) -> None:
    bundle = FixtureBundle(tmp_path)
    feed = FeedIdentity("bithumb", "orderbook", "KRW-BTC")
    obs, _, _ = bundle.prepare_raw_feed(feed, record_count=5)

    result1 = bundle.finalizer.finalize_slot(obs)
    result2 = bundle.finalizer.finalize_slot(obs)

    assert result1.coverage.evidence_sha256 == result2.coverage.evidence_sha256
    assert result1.coverage_receipt is not None and result2.coverage_receipt is not None
    assert result1.coverage_receipt.remote_checksum == result2.coverage_receipt.remote_checksum
    assert result1.raw_receipt is not None and result2.raw_receipt is not None
    assert result1.raw_receipt.remote_checksum == result2.raw_receipt.remote_checksum


def test_finalize_cohort_all_76_mixed_slots(tmp_path: Path) -> None:
    bundle = FixtureBundle(tmp_path)
    cohort_key = "2026-09-14_12"

    # Build 76 observations: first 74 positive, last 2 zero event
    observations: list[FrozenFeedHourObservation] = []
    assert len(SEALED_FEED_UNIVERSE) == 76

    for idx, feed in enumerate(SEALED_FEED_UNIVERSE):
        if idx < 74:
            obs, _, _ = bundle.prepare_raw_feed(feed, cohort_utc=cohort_key, record_count=3)
        else:
            obs = _make_observation(feed, event_count=0, cohort_utc=cohort_key)
        observations.append(obs)

    # Save frozen journal
    save_frozen_journal(observations, bundle.journals_dir)

    # Finalize cohort
    results = bundle.finalizer.finalize_cohort(cohort_key)

    assert len(results) == 76
    present_count = sum(1 for r in results if r.coverage.coverage_state == "DATA_PRESENT")
    zero_count = sum(1 for r in results if r.coverage.coverage_state == "VERIFIED_ZERO_EVENT")
    failed_count = sum(1 for r in results if r.coverage.coverage_state == "FAILED")

    assert present_count == 74
    assert zero_count == 2
    assert failed_count == 0

    # Receipts check
    for idx, r in enumerate(results):
        assert r.coverage_receipt is not None
        assert r.coverage_receipt.restore_verified_at is not None
        if idx < 74:
            assert r.raw_receipt is not None
            assert r.raw_receipt.restore_verified_at is not None
        else:
            assert r.raw_receipt is None


def test_finalize_cohort_missing_feed_rejected(tmp_path: Path) -> None:
    bundle = FixtureBundle(tmp_path)
    cohort_key = "2026-09-14_12"

    # Only 75 feeds (skip the last one)
    observations = [
        _make_observation(feed, event_count=0, cohort_utc=cohort_key)
        for feed in SEALED_FEED_UNIVERSE[:-1]
    ]
    save_frozen_journal(observations, bundle.journals_dir)

    with pytest.raises(ValueError) as exc:
        bundle.finalizer.finalize_cohort(cohort_key)
    assert "MISSING" in str(exc.value)


def test_finalize_cohort_duplicate_feed_rejected(tmp_path: Path) -> None:
    bundle = FixtureBundle(tmp_path)
    cohort_key = "2026-09-14_12"

    # 76 feeds but feed[0] duplicated in place of feed[1]
    obs_list = [_make_observation(SEALED_FEED_UNIVERSE[0], event_count=0, cohort_utc=cohort_key)]
    for feed in SEALED_FEED_UNIVERSE[1:]:
        obs_list.append(_make_observation(feed, event_count=0, cohort_utc=cohort_key))
    obs_list[1] = _make_observation(SEALED_FEED_UNIVERSE[0], event_count=0, cohort_utc=cohort_key)

    save_frozen_journal(obs_list, bundle.journals_dir)

    with pytest.raises(ValueError) as exc:
        bundle.finalizer.finalize_cohort(cohort_key)
    assert "DUPLICATE" in str(exc.value) or "MISSING" in str(exc.value)


def test_finalize_cohort_foreign_feed_rejected(tmp_path: Path) -> None:
    bundle = FixtureBundle(tmp_path)
    cohort_key = "2026-09-14_12"

    foreign_feed = FeedIdentity("kraken", "trade", "BTC-USD")
    obs_list = [
        _make_observation(feed, event_count=0, cohort_utc=cohort_key)
        for feed in SEALED_FEED_UNIVERSE[:-1]
    ]
    obs_list.append(_make_observation(foreign_feed, event_count=0, cohort_utc=cohort_key))

    save_frozen_journal(obs_list, bundle.journals_dir)

    with pytest.raises(ValueError) as exc:
        bundle.finalizer.finalize_cohort(cohort_key)
    assert "FOREIGN" in str(exc.value) or "MISSING" in str(exc.value)


def test_evaluate_common_gate_rejects_non_qualifying_full_hour() -> None:
    obs = _make_observation(
        SEALED_FEED_UNIVERSE[0],
        event_count=10,
        cohort_qualification="TOUCHED_PARTIAL",
    )
    policy = _make_policy()
    reasons = evaluate_common_gate(obs, policy)
    assert "NOT_QUALIFYING_FULL_HOUR" in reasons


def test_evaluate_common_gate_rejects_unconfirmed_feed_on_session() -> None:
    feed = SEALED_FEED_UNIVERSE[0]
    other_feed = SEALED_FEED_UNIVERSE[1]
    seg = _make_segment(other_feed)
    # The segment confirms other_feed, but observation is for feed
    obs = _make_observation(feed, event_count=0, session_segments=(seg,))
    policy = _make_policy()
    reasons = evaluate_common_gate(obs, policy)
    assert "FEED_NOT_CONFIRMED_ON_SESSION" in reasons


def test_closed_slot_result_convenience_properties(tmp_path: Path) -> None:
    bundle = FixtureBundle(tmp_path)
    cohort_key = "2026-09-14_12"
    obs_list = [
        _make_observation(feed, event_count=0, cohort_utc=cohort_key)
        for feed in SEALED_FEED_UNIVERSE
    ]
    save_frozen_journal(obs_list, bundle.journals_dir)
    results = bundle.finalizer.finalize_cohort(cohort_key)
    res = results[0]
    assert res.status == "VERIFIED_ZERO_EVENT"
    assert res.slot == SEALED_FEED_UNIVERSE[0].canonical
    assert res.reason == ()
