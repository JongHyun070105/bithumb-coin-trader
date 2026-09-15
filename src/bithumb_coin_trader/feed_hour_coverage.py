from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from typing import Any

from bithumb_coin_trader.evidence_hashing import canonical_sha256
from bithumb_coin_trader.session_evidence import (
    FeedIdentity,
    HeartbeatPolicy,
    SessionEvidenceTracker,
    SessionSegment,
    WriterHealthSnapshot,
)

__all__ = [
    "DataArtifactBinding",
    "FeedHourCoverage",
    "FeedHourCoverageTracker",
    "FrozenFeedHourObservation",
    "load_feed_hour_coverage",
    "load_frozen_journal",
    "materialize_feed_hour_coverage",
    "save_feed_hour_coverage",
    "save_frozen_journal",
]


@dataclass(frozen=True)
class DataArtifactBinding:
    raw_relative_path: str
    raw_size: int
    raw_sha256: str
    manifest_relative_path: str
    manifest_file_sha256: str
    manifest_record_count: int
    receipt_relative_path: str
    receipt_file_sha256: str
    receipt_source_record_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FrozenFeedHourObservation:
    feed: FeedIdentity
    cohort_utc: str
    interval_start_utc: str
    interval_end_utc: str
    cohort_qualification: str  # "QUALIFYING_FULL_HOUR" or "TOUCHED_PARTIAL"
    observation_start_utc: str
    observation_end_utc: str
    event_count: int
    first_event_timestamp: str | None
    last_event_timestamp: str | None
    session_segments: tuple[SessionSegment, ...]
    disconnect_count: int
    reconnect_count: int
    health: WriterHealthSnapshot
    progress_entry_id: str | None = None


@dataclass(frozen=True)
class FeedHourCoverage:
    schema_version: int
    artifact_kind: str
    environment_id: str
    collector_epoch: str
    collector_run_id: str
    runtime_commit: str
    runtime_config_fingerprint: str
    cohort_utc: str
    interval_start_utc: str
    interval_end_utc: str
    cohort_qualification: str
    observation_start_utc: str
    observation_end_utc: str
    exchange: str
    stream: str
    market: str
    feed_identity: str
    configured: bool
    coverage_state: str
    event_count: int
    first_event_timestamp: str | None
    last_event_timestamp: str | None
    session_segments: tuple[SessionSegment, ...]
    disconnect_count: int
    reconnect_count: int
    writer_error_count: int
    queue_dropped_events: int
    unpersisted_event_count: int
    fatal_writer_error_type: str | None
    data_artifact_binding: DataArtifactBinding | None
    failure_reason_codes: tuple[str, ...]
    closed_at_utc: str
    evidence_sha256: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def materialize_feed_hour_coverage(
    observation: FrozenFeedHourObservation,
    heartbeat_policy: HeartbeatPolicy,
    data_binding: DataArtifactBinding | None,
    runtime_commit: str = "unknown",
    runtime_config_fingerprint: str = "unknown",
    environment_id: str = "unknown",
    closed_at_utc: str | None = None,
) -> FeedHourCoverage:
    failure_reasons: list[str] = []

    # 1. Health check
    health = observation.health
    if (
        health.writer_error_count > 0
        or health.queue_dropped_events > 0
        or health.unpersisted_event_count > 0
        or health.fatal_writer_error_type is not None
    ):
        failure_reasons.append("WRITER_HEALTH_DEGRADED")

    # 2. Session segments check
    if not observation.session_segments:
        failure_reasons.append("NO_SESSION_SEGMENTS")
    else:
        # Check confirmation
        for seg in observation.session_segments:
            if seg.confirmed_at_utc is None:
                if "SESSION_NOT_CONFIRMED" not in failure_reasons:
                    failure_reasons.append("SESSION_NOT_CONFIRMED")
            elif seg.confirmed_at_utc > observation.interval_start_utc:
                if "LATE_CONFIRMATION" not in failure_reasons:
                    failure_reasons.append("LATE_CONFIRMATION")

        # Heartbeat gap check
        threshold = heartbeat_policy.max_allowed_heartbeat_gap_seconds.get(observation.feed.exchange)
        if threshold is None:
            failure_reasons.append("MISSING_HEARTBEAT_POLICY")
        else:
            gap_exceeded = False
            for seg in observation.session_segments:
                if seg.maximum_heartbeat_gap_seconds is not None and seg.maximum_heartbeat_gap_seconds > threshold:
                    gap_exceeded = True
                    break

            start_dt = datetime.fromisoformat(observation.interval_start_utc.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(observation.interval_end_utc.replace("Z", "+00:00"))

            all_hb: list[datetime] = []
            for seg in observation.session_segments:
                for h in seg.heartbeat_observations_utc:
                    dt = datetime.fromisoformat(h.replace("Z", "+00:00"))
                    all_hb.append(dt)
            all_hb.sort()

            if not all_hb:
                gap_exceeded = True
            else:
                if (all_hb[0] - start_dt).total_seconds() > threshold:
                    gap_exceeded = True
                for i in range(len(all_hb) - 1):
                    if (all_hb[i + 1] - all_hb[i]).total_seconds() > threshold:
                        gap_exceeded = True
                        break
                if (end_dt - all_hb[-1]).total_seconds() > threshold:
                    gap_exceeded = True

            if gap_exceeded and "HEARTBEAT_GAP_EXCEEDED" not in failure_reasons:
                failure_reasons.append("HEARTBEAT_GAP_EXCEEDED")

        # Reconnect gap check
        reconnect_gap = False
        if observation.disconnect_count > 0 or observation.reconnect_count > 0:
            reconnect_gap = True

        segments = sorted(observation.session_segments, key=lambda s: s.connected_at_utc)
        if segments:
            if segments[0].connected_at_utc > observation.interval_start_utc:
                reconnect_gap = True
            for i in range(len(segments) - 1):
                s_curr = segments[i]
                s_next = segments[i + 1]
                if s_curr.disconnected_at_utc is not None:
                    if s_next.connected_at_utc > s_curr.disconnected_at_utc:
                        reconnect_gap = True
                        break
            if segments[-1].disconnected_at_utc is not None and segments[-1].disconnected_at_utc < observation.interval_end_utc:
                reconnect_gap = True

        if reconnect_gap and "COLLECTION_GAP" not in failure_reasons:
            failure_reasons.append("COLLECTION_GAP")

    # 3. State selection
    if failure_reasons:
        coverage_state = "FAILED"
    elif observation.event_count > 0:
        if data_binding is None:
            coverage_state = "FAILED"
            failure_reasons.append("MISSING_DATA_BINDING")
        elif not (
            observation.event_count == data_binding.manifest_record_count == data_binding.receipt_source_record_count
        ):
            coverage_state = "FAILED"
            failure_reasons.append("EVENT_COUNT_MISMATCH")
        else:
            coverage_state = "DATA_PRESENT"
    else:
        if data_binding is not None:
            coverage_state = "FAILED"
            failure_reasons.append("UNEXPECTED_DATA_BINDING_FOR_ZERO_EVENT")
        elif observation.first_event_timestamp is not None or observation.last_event_timestamp is not None:
            coverage_state = "FAILED"
            failure_reasons.append("INVALID_ZERO_EVENT_TIMESTAMPS")
        elif observation.cohort_qualification == "TOUCHED_PARTIAL":
            coverage_state = "FAILED"
            failure_reasons.append("ZERO_EVENT_FORBIDDEN_FOR_PARTIAL_COHORT")
        else:
            coverage_state = "VERIFIED_ZERO_EVENT"

    epoch = observation.session_segments[0].collector_epoch if observation.session_segments else "unknown"
    run_id = observation.session_segments[0].collector_run_id if observation.session_segments else "unknown"

    if closed_at_utc is None:
        closed_at_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Construct coverage object with empty sha, then compute canonical sha256
    temp_coverage = FeedHourCoverage(
        schema_version=1,
        artifact_kind="COVERAGE_EVIDENCE",
        environment_id=environment_id,
        collector_epoch=epoch,
        collector_run_id=run_id,
        runtime_commit=runtime_commit,
        runtime_config_fingerprint=runtime_config_fingerprint,
        cohort_utc=observation.cohort_utc,
        interval_start_utc=observation.interval_start_utc,
        interval_end_utc=observation.interval_end_utc,
        cohort_qualification=observation.cohort_qualification,
        observation_start_utc=observation.observation_start_utc,
        observation_end_utc=observation.observation_end_utc,
        exchange=observation.feed.exchange,
        stream=observation.feed.stream,
        market=observation.feed.market,
        feed_identity=observation.feed.canonical,
        configured=True,
        coverage_state=coverage_state,
        event_count=observation.event_count,
        first_event_timestamp=observation.first_event_timestamp,
        last_event_timestamp=observation.last_event_timestamp,
        session_segments=tuple(observation.session_segments),
        disconnect_count=observation.disconnect_count,
        reconnect_count=observation.reconnect_count,
        writer_error_count=observation.health.writer_error_count,
        queue_dropped_events=observation.health.queue_dropped_events,
        unpersisted_event_count=observation.health.unpersisted_event_count,
        fatal_writer_error_type=observation.health.fatal_writer_error_type,
        data_artifact_binding=data_binding,
        failure_reason_codes=tuple(failure_reasons),
        closed_at_utc=closed_at_utc,
        evidence_sha256="",
    )
    computed_sha = canonical_sha256(temp_coverage.to_dict(), excluded=("evidence_sha256",))
    return replace(temp_coverage, evidence_sha256=computed_sha)


def _fsync_dir(dir_path: Path) -> None:
    try:
        dfd = os.open(str(dir_path), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def save_feed_hour_coverage(coverage: FeedHourCoverage, base_dir: Path) -> Path:
    if base_dir.name != "coverage" and not str(base_dir).endswith("/coverage"):
        target_dir = base_dir / "coverage" / coverage.cohort_utc / coverage.exchange / coverage.stream
    else:
        target_dir = base_dir / coverage.cohort_utc / coverage.exchange / coverage.stream
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{coverage.market}.coverage.json"
    tmp_path = target_dir / f".{target_path.name}.{os.getpid()}.{time.time_ns()}.tmp"

    data_bytes = json.dumps(coverage.to_dict(), indent=2, ensure_ascii=False).encode("utf-8")
    try:
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp_path), str(target_path))
        _fsync_dir(target_dir)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise
    return target_path


def load_feed_hour_coverage(path: Path) -> FeedHourCoverage:
    data = json.loads(path.read_text(encoding="utf-8"))

    if data.get("schema_version") != 1:
        raise ValueError("INVALID_SCHEMA_VERSION")
    if data.get("artifact_kind") != "COVERAGE_EVIDENCE":
        raise ValueError("INVALID_ARTIFACT_KIND")

    stored_hash = data.get("evidence_sha256")
    computed_hash = canonical_sha256(data, excluded=("evidence_sha256",))
    if stored_hash != computed_hash:
        raise ValueError("TAMPERED_COVERAGE_HASH")

    segments_raw = data.get("session_segments", [])
    segments: list[SessionSegment] = []
    for s in segments_raw:
        segments.append(
            SessionSegment(
                exchange=s["exchange"],
                session_id=s["session_id"],
                connected_at_utc=s["connected_at_utc"],
                disconnected_at_utc=s.get("disconnected_at_utc"),
                requested_feeds=tuple(s.get("requested_feeds", ())),
                requested_subscription_sha256=s["requested_subscription_sha256"],
                confirmation_method=s.get("confirmation_method"),
                confirmed_at_utc=s.get("confirmed_at_utc"),
                confirmed_feeds=tuple(s.get("confirmed_feeds", ())),
                confirmed_subscription_sha256=s.get("confirmed_subscription_sha256"),
                response_evidence_sha256=s.get("response_evidence_sha256"),
                heartbeat_observations_utc=tuple(s.get("heartbeat_observations_utc", ())),
                maximum_heartbeat_gap_seconds=s.get("maximum_heartbeat_gap_seconds"),
                disconnect_reason=s.get("disconnect_reason"),
                reconnect_successor_id=s.get("reconnect_successor_id"),
                collector_epoch=s["collector_epoch"],
                collector_run_id=s["collector_run_id"],
            )
        )

    binding_raw = data.get("data_artifact_binding")
    binding: DataArtifactBinding | None = None
    if binding_raw is not None:
        binding = DataArtifactBinding(
            raw_relative_path=binding_raw["raw_relative_path"],
            raw_size=binding_raw["raw_size"],
            raw_sha256=binding_raw["raw_sha256"],
            manifest_relative_path=binding_raw["manifest_relative_path"],
            manifest_file_sha256=binding_raw["manifest_file_sha256"],
            manifest_record_count=binding_raw["manifest_record_count"],
            receipt_relative_path=binding_raw["receipt_relative_path"],
            receipt_file_sha256=binding_raw["receipt_file_sha256"],
            receipt_source_record_count=binding_raw["receipt_source_record_count"],
        )

    return FeedHourCoverage(
        schema_version=data["schema_version"],
        artifact_kind=data["artifact_kind"],
        environment_id=data["environment_id"],
        collector_epoch=data["collector_epoch"],
        collector_run_id=data["collector_run_id"],
        runtime_commit=data["runtime_commit"],
        runtime_config_fingerprint=data["runtime_config_fingerprint"],
        cohort_utc=data["cohort_utc"],
        interval_start_utc=data["interval_start_utc"],
        interval_end_utc=data["interval_end_utc"],
        cohort_qualification=data["cohort_qualification"],
        observation_start_utc=data["observation_start_utc"],
        observation_end_utc=data["observation_end_utc"],
        exchange=data["exchange"],
        stream=data["stream"],
        market=data["market"],
        feed_identity=data["feed_identity"],
        configured=data.get("configured", True),
        coverage_state=data["coverage_state"],
        event_count=data["event_count"],
        first_event_timestamp=data.get("first_event_timestamp"),
        last_event_timestamp=data.get("last_event_timestamp"),
        session_segments=tuple(segments),
        disconnect_count=data.get("disconnect_count", 0),
        reconnect_count=data.get("reconnect_count", 0),
        writer_error_count=data.get("writer_error_count", 0),
        queue_dropped_events=data.get("queue_dropped_events", 0),
        unpersisted_event_count=data.get("unpersisted_event_count", 0),
        fatal_writer_error_type=data.get("fatal_writer_error_type"),
        data_artifact_binding=binding,
        failure_reason_codes=tuple(data.get("failure_reason_codes", ())),
        closed_at_utc=data["closed_at_utc"],
        evidence_sha256=data["evidence_sha256"],
    )


class FeedHourCoverageTracker:
    def __init__(
        self,
        feeds: Sequence[FeedIdentity] | datetime = (),
        epoch: str = "",
        run_id: str = "",
        actual_start_utc: datetime | None = None,
    ) -> None:
        if isinstance(feeds, datetime):
            actual_start_utc = feeds
            feeds = ()
        self.configured_feeds: tuple[FeedIdentity, ...] = tuple(feeds)
        self.epoch = epoch
        self.run_id = run_id
        if actual_start_utc is None:
            self.actual_start_utc = datetime.now(timezone.utc)
        elif actual_start_utc.tzinfo is None:
            self.actual_start_utc = actual_start_utc.replace(tzinfo=timezone.utc)
        else:
            self.actual_start_utc = actual_start_utc.astimezone(timezone.utc)

        self._last_write_ts: datetime | None = None
        self._cohort_feed_stats: dict[tuple[str, FeedIdentity], dict[str, Any]] = {}
        self._active_cohorts: set[str] = set()
        self._frozen_cohorts: set[str] = set()
        self._all_seen_feeds: set[FeedIdentity] = set(self.configured_feeds)

    def record_persisted_event(
        self,
        feed: FeedIdentity,
        local_write_ts: datetime,
    ) -> None:
        if self._last_write_ts is not None and local_write_ts < self._last_write_ts:
            raise ValueError("WRITER_CLOCK_REGRESSION")

        hour_dt = local_write_ts.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        cohort_utc = hour_dt.strftime("%Y-%m-%d_%H")
        if cohort_utc in self._frozen_cohorts:
            raise ValueError(f"COHORT_ALREADY_FROZEN: {cohort_utc}")

        self._last_write_ts = local_write_ts
        ts_str = local_write_ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        self._active_cohorts.add(cohort_utc)
        self._all_seen_feeds.add(feed)

        key = (cohort_utc, feed)
        if key not in self._cohort_feed_stats:
            self._cohort_feed_stats[key] = {
                "event_count": 1,
                "first_event_timestamp": ts_str,
                "last_event_timestamp": ts_str,
            }
        else:
            stats = self._cohort_feed_stats[key]
            stats["event_count"] += 1
            if stats["first_event_timestamp"] is None:
                stats["first_event_timestamp"] = ts_str
            stats["last_event_timestamp"] = ts_str

    def freeze_completed(
        self,
        boundary_utc: datetime,
        session_tracker: SessionEvidenceTracker,
        health: WriterHealthSnapshot,
    ) -> Sequence[FrozenFeedHourObservation]:
        boundary_utc_clean = boundary_utc.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start_utc_clean = boundary_utc_clean - timedelta(hours=1)

        cohort_utc = start_utc_clean.strftime("%Y-%m-%d_%H")
        interval_start_utc = start_utc_clean.strftime("%Y-%m-%dT%H:%M:%SZ")
        interval_end_utc = boundary_utc_clean.strftime("%Y-%m-%dT%H:%M:%SZ")

        if cohort_utc in self._frozen_cohorts:
            return ()

        self._frozen_cohorts.add(cohort_utc)

        opening_start = self.actual_start_utc.replace(minute=0, second=0, microsecond=0)
        if start_utc_clean == opening_start:
            cohort_qualification = "TOUCHED_PARTIAL"
            observation_start_utc = self.actual_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            cohort_qualification = "QUALIFYING_FULL_HOUR"
            observation_start_utc = interval_start_utc

        feeds_to_freeze = list(self.configured_feeds) if self.configured_feeds else sorted(self._all_seen_feeds)
        observations: list[FrozenFeedHourObservation] = []

        for feed in feeds_to_freeze:
            segments = session_tracker.segments_for(feed, interval_start_utc, interval_end_utc)
            disc_count = sum(
                1 for s in segments
                if s.disconnected_at_utc is not None
                and interval_start_utc <= s.disconnected_at_utc < interval_end_utc
            )
            rec_count = sum(
                1 for s in segments
                if s.reconnect_successor_id is not None
                and s.disconnected_at_utc is not None
                and interval_start_utc <= s.disconnected_at_utc < interval_end_utc
            )

            stats = self._cohort_feed_stats.get((cohort_utc, feed))
            if stats is not None:
                ev_count = stats["event_count"]
                first_ts = stats["first_event_timestamp"]
                last_ts = stats["last_event_timestamp"]
            else:
                ev_count = 0
                first_ts = None
                last_ts = None

            observations.append(
                FrozenFeedHourObservation(
                    feed=feed,
                    cohort_utc=cohort_utc,
                    interval_start_utc=interval_start_utc,
                    interval_end_utc=interval_end_utc,
                    cohort_qualification=cohort_qualification,
                    observation_start_utc=observation_start_utc,
                    observation_end_utc=interval_end_utc,
                    event_count=ev_count,
                    first_event_timestamp=first_ts,
                    last_event_timestamp=last_ts,
                    session_segments=segments,
                    disconnect_count=disc_count,
                    reconnect_count=rec_count,
                    health=health,
                )
            )
        return tuple(observations)

    def freeze_shutdown(
        self,
        observation_end_utc: datetime,
        session_tracker: SessionEvidenceTracker,
        health: WriterHealthSnapshot,
    ) -> Sequence[FrozenFeedHourObservation]:
        end_clean = observation_end_utc.astimezone(timezone.utc)
        hour_start = end_clean.replace(minute=0, second=0, microsecond=0)
        cohort_utc = hour_start.strftime("%Y-%m-%d_%H")

        cohorts = sorted(self._active_cohorts - self._frozen_cohorts)
        if cohort_utc not in cohorts and cohort_utc not in self._frozen_cohorts:
            cohorts.append(cohort_utc)

        observations: list[FrozenFeedHourObservation] = []
        feeds_to_freeze = list(self.configured_feeds) if self.configured_feeds else sorted(self._all_seen_feeds)

        opening_start = self.actual_start_utc.replace(minute=0, second=0, microsecond=0)

        for c_utc in cohorts:
            self._frozen_cohorts.add(c_utc)
            c_dt = datetime.strptime(c_utc, "%Y-%m-%d_%H").replace(tzinfo=timezone.utc)
            c_end_dt = c_dt + timedelta(hours=1)
            interval_start_utc = c_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            interval_end_utc = c_end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            is_full = (end_clean >= c_end_dt)
            obs_end = interval_end_utc if is_full else end_clean.strftime("%Y-%m-%dT%H:%M:%SZ")

            if c_dt == opening_start:
                cohort_qualification = "TOUCHED_PARTIAL"
                obs_start = self.actual_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            elif is_full:
                cohort_qualification = "QUALIFYING_FULL_HOUR"
                obs_start = interval_start_utc
            else:
                cohort_qualification = "TOUCHED_PARTIAL"
                obs_start = interval_start_utc

            for feed in feeds_to_freeze:
                segments = session_tracker.segments_for(feed, interval_start_utc, interval_end_utc)
                disc_count = sum(
                    1 for s in segments
                    if s.disconnected_at_utc is not None
                    and interval_start_utc <= s.disconnected_at_utc < interval_end_utc
                )
                rec_count = sum(
                    1 for s in segments
                    if s.reconnect_successor_id is not None
                    and s.disconnected_at_utc is not None
                    and interval_start_utc <= s.disconnected_at_utc < interval_end_utc
                )

                stats = self._cohort_feed_stats.get((c_utc, feed))
                if stats is not None:
                    ev_count = stats["event_count"]
                    first_ts = stats["first_event_timestamp"]
                    last_ts = stats["last_event_timestamp"]
                else:
                    ev_count = 0
                    first_ts = None
                    last_ts = None

                observations.append(
                    FrozenFeedHourObservation(
                        feed=feed,
                        cohort_utc=c_utc,
                        interval_start_utc=interval_start_utc,
                        interval_end_utc=interval_end_utc,
                        cohort_qualification=cohort_qualification,
                        observation_start_utc=obs_start,
                        observation_end_utc=obs_end,
                        event_count=ev_count,
                        first_event_timestamp=first_ts,
                        last_event_timestamp=last_ts,
                        session_segments=segments,
                        disconnect_count=disc_count,
                        reconnect_count=rec_count,
                        health=health,
                    )
                )
        return tuple(observations)


def save_frozen_journal(
    observations: Sequence[FrozenFeedHourObservation],
    journal_dir: Path,
) -> Path:
    """Atomically persist frozen observations for a cohort as JSON (mode 0o600)."""
    if not observations:
        raise ValueError("cannot save empty observations")
    cohort = observations[0].cohort_utc
    journal_dir = Path(journal_dir)
    journal_dir.mkdir(parents=True, exist_ok=True)
    out_path = journal_dir / f"journal_{cohort}.json"
    payload = {
        "schema_version": 1,
        "cohort_utc": cohort,
        "slot_count": len(observations),
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "observations": [asdict(obs) for obs in observations],
    }
    tmp_path = out_path.with_suffix(".json.tmp")
    data = json.dumps(payload, indent=2, sort_keys=True)
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp_path), str(out_path))
        dir_fd = os.open(str(journal_dir), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return out_path


def load_frozen_journal(path: Path) -> tuple[FrozenFeedHourObservation, ...]:
    """Load and reconstruct FrozenFeedHourObservation tuple from journal file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError(f"UNSUPPORTED_JOURNAL_SCHEMA: {data.get('schema_version')}")
    results: list[FrozenFeedHourObservation] = []
    for raw in data.get("observations", []):
        raw_feed = raw["feed"]
        if isinstance(raw_feed, dict):
            feed = FeedIdentity(
                exchange=raw_feed["exchange"],
                stream=raw_feed["stream"],
                market=raw_feed["market"],
            )
        elif isinstance(raw_feed, str):
            parts = raw_feed.split("/")
            feed = FeedIdentity(exchange=parts[0], stream=parts[1], market=parts[2])
        else:
            feed = raw_feed

        segments = tuple(
            SessionSegment(
                exchange=s["exchange"],
                session_id=s["session_id"],
                connected_at_utc=s["connected_at_utc"],
                disconnected_at_utc=s.get("disconnected_at_utc"),
                requested_feeds=tuple(s.get("requested_feeds", s.get("requested_subscriptions", ()))),
                requested_subscription_sha256=s.get("requested_subscription_sha256", s.get("requested_hash", "")),
                confirmation_method=s.get("confirmation_method"),
                confirmed_at_utc=s.get("confirmed_at_utc"),
                confirmed_feeds=tuple(s.get("confirmed_feeds", s.get("confirmed_subscriptions", ()))),
                confirmed_subscription_sha256=s.get("confirmed_subscription_sha256", s.get("confirmed_hash")),
                response_evidence_sha256=s.get("response_evidence_sha256"),
                heartbeat_observations_utc=tuple(s.get("heartbeat_observations_utc", s.get("heartbeat_observations", ()))),
                maximum_heartbeat_gap_seconds=s.get("maximum_heartbeat_gap_seconds", s.get("max_heartbeat_gap_seconds")),
                disconnect_reason=s.get("disconnect_reason"),
                reconnect_successor_id=s.get("reconnect_successor_id"),
                collector_epoch=s["collector_epoch"],
                collector_run_id=s["collector_run_id"],
            )
            for s in raw["session_segments"]
        )
        h = raw["health"]
        health = WriterHealthSnapshot(
            writer_error_count=h.get("writer_error_count", 0),
            queue_dropped_events=h.get("queue_dropped_events", 0),
            unpersisted_event_count=h.get("unpersisted_event_count", 0),
            fatal_writer_error_type=h.get("fatal_writer_error_type") or (h.get("fatal_writer_error") if isinstance(h.get("fatal_writer_error"), str) else None),
        )
        results.append(
            FrozenFeedHourObservation(
                feed=feed,
                cohort_utc=raw["cohort_utc"],
                interval_start_utc=raw["interval_start_utc"],
                interval_end_utc=raw["interval_end_utc"],
                cohort_qualification=raw["cohort_qualification"],
                observation_start_utc=raw["observation_start_utc"],
                observation_end_utc=raw["observation_end_utc"],
                event_count=raw["event_count"],
                first_event_timestamp=raw.get("first_event_timestamp"),
                last_event_timestamp=raw.get("last_event_timestamp"),
                session_segments=segments,
                disconnect_count=raw["disconnect_count"],
                reconnect_count=raw["reconnect_count"],
                health=health,
                progress_entry_id=raw.get("progress_entry_id"),
            )
        )
    return tuple(results)
