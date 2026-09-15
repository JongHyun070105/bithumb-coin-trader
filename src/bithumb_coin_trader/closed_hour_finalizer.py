"""Ordered closed-hour finalizer enforcing V3 coverage chains and generic archive integration.

Enforces strict order per design spec §7.3:
Positive event slot:
  hour append-closed
  -> observation journal frozen
  -> common slot gate evaluated
  -> RAW manifest generated or source-bound reuse validated
  -> RAW_DATA archived through generic pipeline
  -> RAW terminal receipt and restore verification complete
  -> event_count == manifest.record_count == receipt.source_record_count
  -> immutable DATA_PRESENT coverage object materialized with RAW bindings
  -> COVERAGE_EVIDENCE archived through same generic pipeline
  -> coverage terminal receipt and restore verification complete

Zero event slot:
  hour append-closed
  -> observation journal frozen
  -> common slot gate evaluated
  -> immutable VERIFIED_ZERO_EVENT coverage object materialized
  -> COVERAGE_EVIDENCE archived through generic pipeline
  -> coverage terminal receipt and restore verification complete
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

from bithumb_coin_trader.evidence_hashing import (
    canonical_sha256,
    file_sha256,
)
from bithumb_coin_trader.feed_hour_coverage import (
    DataArtifactBinding,
    FeedHourCoverage,
    FrozenFeedHourObservation,
    load_frozen_journal,
    materialize_feed_hour_coverage,
    save_feed_hour_coverage,
)
from bithumb_coin_trader.incremental_finalizer import (
    ArtifactBinding,
    FinalizationIdentity,
    FinalizationProgressStore,
)
from bithumb_coin_trader.microstructure_storage import (
    PartitionManifest,
    RawMicrostructureStorage,
)
from bithumb_coin_trader.pre_soak_archive import (
    ArchivePipeline,
    ArchiveReceiptV3,
    ArtifactKind,
    ImmutableArtifact,
)
from bithumb_coin_trader.session_evidence import (
    FeedIdentity,
    HeartbeatPolicy,
)


EXPECTED_BITHUMB_20: tuple[str, ...] = (
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE",
    "KRW-ADA", "KRW-XLM", "KRW-LINK", "KRW-AVAX", "KRW-BCH",
    "KRW-ETC", "KRW-NEAR", "KRW-SUI", "KRW-APT", "KRW-TRX",
    "KRW-SHIB", "KRW-SAND", "KRW-MANA", "KRW-AXS", "KRW-DOT",
)
EXPECTED_BINANCE_4: tuple[str, ...] = ("btcusdt", "ethusdt", "solusdt", "xrpusdt")
EXPECTED_UPBIT_4: tuple[str, ...] = ("KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP")


def _build_sealed_feed_universe() -> tuple[FeedIdentity, ...]:
    feeds: list[FeedIdentity] = []
    for m in EXPECTED_BITHUMB_20:
        for s in ("orderbook", "trade", "ticker"):
            feeds.append(FeedIdentity(exchange="bithumb", stream=s, market=m))
    for m in EXPECTED_BINANCE_4:
        for s in ("orderbook", "trade"):
            feeds.append(FeedIdentity(exchange="binance", stream=s, market=m))
    for m in EXPECTED_UPBIT_4:
        for s in ("orderbook", "trade"):
            feeds.append(FeedIdentity(exchange="upbit", stream=s, market=m))
    return tuple(sorted(feeds))


SEALED_FEED_UNIVERSE: tuple[FeedIdentity, ...] = _build_sealed_feed_universe()
SEALED_FEED_CANONICAL_SET: frozenset[str] = frozenset(f.canonical for f in SEALED_FEED_UNIVERSE)


@dataclass(frozen=True)
class ClosedSlotResult:
    coverage: FeedHourCoverage
    coverage_receipt: ArchiveReceiptV3 | None
    raw_receipt: ArchiveReceiptV3 | None
    failure_reason_codes: Sequence[str]

    @property
    def status(self) -> str:
        return self.coverage.coverage_state

    @property
    def slot(self) -> str:
        return self.coverage.feed_identity

    @property
    def reason(self) -> Sequence[str]:
        return self.failure_reason_codes


def evaluate_common_gate(
    observation: FrozenFeedHourObservation,
    heartbeat_policy: HeartbeatPolicy,
) -> list[str]:
    """Evaluate common completeness gates required before either qualifying state."""
    failure_reasons: list[str] = []

    # 0. Cohort qualification check
    if observation.cohort_qualification != "QUALIFYING_FULL_HOUR":
        failure_reasons.append("NOT_QUALIFYING_FULL_HOUR")

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
            if seg.confirmed_feeds and observation.feed.canonical not in seg.confirmed_feeds:
                if "FEED_NOT_CONFIRMED_ON_SESSION" not in failure_reasons:
                    failure_reasons.append("FEED_NOT_CONFIRMED_ON_SESSION")

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
                    all_hb.append(datetime.fromisoformat(h.replace("Z", "+00:00")))
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

    if observation.feed.canonical not in SEALED_FEED_CANONICAL_SET:
        if "FOREIGN_FEED" not in failure_reasons:
            failure_reasons.append("FOREIGN_FEED")

    return failure_reasons


class ClosedHourFinalizer:
    def __init__(
        self,
        raw_archive: ArchivePipeline,
        coverage_archive: ArchivePipeline,
        heartbeat_policy: HeartbeatPolicy,
        progress_store: FinalizationProgressStore,
        journals_dir: Path,
        coverage_dir: Path,
        environment_id: str,
        runtime_commit: str = "HEAD",
        runtime_config_fingerprint: str = "unknown",
        stability_wait_seconds: float = 0.0,
        tracer: list[str] | None = None,
    ) -> None:
        self.raw_archive = raw_archive
        self.coverage_archive = coverage_archive
        self.heartbeat_policy = heartbeat_policy
        self.progress_store = progress_store
        self.journals_dir = Path(journals_dir)
        self.coverage_dir = Path(coverage_dir)
        self.environment_id = environment_id
        self.runtime_commit = runtime_commit
        self.runtime_config_fingerprint = runtime_config_fingerprint
        self.stability_wait_seconds = stability_wait_seconds
        self.tracer = tracer

    def _relative_coverage_path(self, cov_path: Path) -> str:
        try:
            return str(cov_path.relative_to(self.coverage_dir))
        except ValueError:
            return cov_path.name

    def _find_raw_path(self, observation: FrozenFeedHourObservation) -> tuple[Path | None, str | None]:
        entry_id = observation.progress_entry_id
        if entry_id:
            try:
                entry = self.progress_store.get_entry(entry_id)
                p = self.raw_archive.raw_root / entry.identity.raw_relative_path
                if p.exists():
                    return p, entry_id
            except Exception:
                pass

        for entry in self.progress_store.pending_entries():
            if (
                entry.identity.cohort == observation.cohort_utc
                and entry.identity.exchange.lower() == observation.feed.exchange.lower()
                and entry.identity.stream.lower() == observation.feed.stream.lower()
                and (
                    entry.identity.market == observation.feed.market
                    or entry.identity.feed_identity == observation.feed.canonical
                )
            ):
                p = self.raw_archive.raw_root / entry.identity.raw_relative_path
                if p.exists():
                    return p, entry.entry_id

        dt_str, hour_str = observation.cohort_utc.split("_")
        clean_market = observation.feed.market.replace("/", "-").replace(":", "-").lower()
        cand = (
            self.raw_archive.raw_root
            / dt_str
            / observation.feed.exchange.lower()
            / observation.feed.stream.lower()
            / f"{observation.feed.exchange.lower()}_{observation.feed.stream.lower()}_{clean_market}_{dt_str}_{hour_str}.jsonl"
        )
        if cand.exists():
            return cand, entry_id

        exch_lower = observation.feed.exchange.lower()
        stream_lower = observation.feed.stream.lower()
        for p in self.raw_archive.raw_root.glob("**/*.jsonl"):
            parts_lower = [part.lower() for part in p.parts]
            if exch_lower not in parts_lower and not p.name.lower().startswith(f"{exch_lower}_"):
                continue
            if stream_lower not in parts_lower and f"_{stream_lower}_" not in p.name.lower():
                continue
            if observation.feed.market in p.name and observation.cohort_utc in p.name:
                return p, entry_id
            if f"{dt_str}_{hour_str}" in p.name and (
                clean_market in p.name.lower()
                or observation.feed.market.lower().replace("-", "_") in p.name.lower()
            ):
                return p, entry_id

        return None, entry_id

    def _ensure_manifest(
        self, raw_path: Path, observation: FrozenFeedHourObservation
    ) -> tuple[Any | None, Path | None]:
        m_file = self.raw_archive.manifest_root / f"manifest_{raw_path.stem}.json"
        if not m_file.exists():
            try:
                rel_parent = raw_path.relative_to(self.raw_archive.raw_root).parent
                cand = self.raw_archive.manifest_root / rel_parent / f"manifest_{raw_path.stem}.json"
                if cand.exists():
                    m_file = cand
            except ValueError:
                pass

        if not m_file.exists():
            storage = RawMicrostructureStorage(
                self.raw_archive.raw_root,
                manifest_dir=self.raw_archive.manifest_root,
                git_commit=self.runtime_commit,
            )
            manifest = storage.generate_partition_manifest(raw_path)
            return manifest, m_file

        try:
            m_data = json.loads(m_file.read_text(encoding="utf-8"))
            manifest = PartitionManifest(
                partition_path=m_data.get("partition_path", str(raw_path)),
                exchange=m_data.get("exchange", observation.feed.exchange),
                stream=m_data.get("stream", observation.feed.stream),
                market=m_data.get("market", observation.feed.market),
                record_count=int(m_data["record_count"]),
                first_exchange_ts=m_data.get("first_exchange_ts"),
                last_exchange_ts=m_data.get("last_exchange_ts"),
                first_local_ts=m_data.get("first_local_ts"),
                last_local_ts=m_data.get("last_local_ts"),
                sha256=m_data.get("sha256", file_sha256(raw_path)),
                bytes=int(m_data.get("bytes", raw_path.stat().st_size)),
                latency_p50_ms=float(m_data.get("latency_p50_ms", 0.0)),
                latency_p95_ms=float(m_data.get("latency_p95_ms", 0.0)),
                latency_p99_ms=float(m_data.get("latency_p99_ms", 0.0)),
                latency_max_ms=float(m_data.get("latency_max_ms", 0.0)),
                clock_skew_or_offset_p50_ms=float(m_data.get("clock_skew_or_offset_p50_ms", 0.0)),
                negative_latency_count=int(m_data.get("negative_latency_count", 0)),
                trade_sequence_gaps=m_data.get("trade_sequence_gaps"),
                trade_duplicate_count=int(m_data.get("trade_duplicate_count", 0)),
                trade_sequence_completeness=m_data.get("trade_sequence_completeness", "COMPLETE"),
                malformed_quarantined_count=int(m_data.get("malformed_quarantined_count", 0)),
                schema_mismatch_count=int(m_data.get("schema_mismatch_count", 0)),
                missing_required_field_count=int(m_data.get("missing_required_field_count", 0)),
                non_finite_numeric_count=int(m_data.get("non_finite_numeric_count", 0)),
                malformed_timestamp_count=int(m_data.get("malformed_timestamp_count", 0)),
                local_timestamp_reversal_count=int(m_data.get("local_timestamp_reversal_count", 0)),
                unknown_market_count=int(m_data.get("unknown_market_count", 0)),
                monotonic_missing_count=int(m_data.get("monotonic_missing_count", 0)),
                monotonic_invalid_count=int(m_data.get("monotonic_invalid_count", 0)),
                monotonic_reversal_count=int(m_data.get("monotonic_reversal_count", 0)),
                latency_observation_count=int(m_data.get("latency_observation_count", 0)),
                latency_parseable_observation_count=int(m_data.get("latency_parseable_observation_count", 0)),
                latency_out_of_range_count=int(m_data.get("latency_out_of_range_count", 0)),
                exchange_timestamp_present_count=int(m_data.get("exchange_timestamp_present_count", 0)),
                latency_sample_count=int(m_data.get("latency_sample_count", 0)),
                latency_metric_semantics=m_data.get("latency_metric_semantics", "strict"),
            )
            return manifest, m_file
        except Exception:
            return None, None

    def _persist_failed(
        self,
        observation: FrozenFeedHourObservation,
        reasons: Sequence[str],
        raw_receipt: ArchiveReceiptV3 | None = None,
        manifest: Any | None = None,
        manifest_path: Path | None = None,
    ) -> ClosedSlotResult:
        all_reasons = list(reasons)
        if "RECORD_COUNT_MISMATCH" in all_reasons and "EVENT_COUNT_MISMATCH" not in all_reasons:
            all_reasons.append("EVENT_COUNT_MISMATCH")
        if "EVENT_COUNT_MISMATCH" in all_reasons and "RECORD_COUNT_MISMATCH" not in all_reasons:
            all_reasons.append("RECORD_COUNT_MISMATCH")

        data_binding: DataArtifactBinding | None = None
        if manifest is not None and manifest_path is not None and raw_receipt is not None:
            raw_path = Path(raw_receipt.source_path)
            try:
                raw_rel = str(raw_path.relative_to(self.raw_archive.raw_root))
            except ValueError:
                raw_rel = str(raw_path)
            try:
                m_rel = str(manifest_path.relative_to(self.raw_archive.manifest_root))
            except ValueError:
                m_rel = str(manifest_path)
            receipt_path = self.raw_archive.artifact_receipt_path(
                ImmutableArtifact(
                    kind=ArtifactKind.RAW_DATA,
                    source_path=raw_path,
                    relative_path=raw_rel,
                    environment_id=self.environment_id,
                    collector_epoch=self.raw_archive.collector_epoch,
                    collector_run_id=self.raw_archive.run_id,
                    cohort=observation.cohort_utc,
                    exchange=observation.feed.exchange,
                    stream=observation.feed.stream,
                    market=observation.feed.market,
                    source_sha256=raw_receipt.source_sha256,
                    source_size=raw_receipt.source_size,
                    source_record_count=raw_receipt.source_record_count,
                    manifest_path=manifest_path,
                    manifest_sha256=file_sha256(manifest_path),
                )
            )
            try:
                rec_rel = str(receipt_path.relative_to(self.raw_archive.receipt_root))
            except ValueError:
                rec_rel = str(receipt_path)
            data_binding = DataArtifactBinding(
                raw_relative_path=raw_rel,
                raw_size=raw_receipt.source_size,
                raw_sha256=raw_receipt.source_sha256,
                manifest_relative_path=m_rel,
                manifest_file_sha256=file_sha256(manifest_path),
                manifest_record_count=manifest.record_count,
                receipt_relative_path=rec_rel,
                receipt_file_sha256=file_sha256(receipt_path) if receipt_path.exists() else "0" * 64,
                receipt_source_record_count=raw_receipt.source_record_count or 0,
            )

        temp_coverage = materialize_feed_hour_coverage(
            observation,
            self.heartbeat_policy,
            data_binding,
            runtime_commit=self.runtime_commit,
            runtime_config_fingerprint=self.runtime_config_fingerprint,
            environment_id=self.environment_id,
        )
        combined_reasons = tuple(sorted(set(temp_coverage.failure_reason_codes).union(all_reasons)))
        cov_dict = temp_coverage.to_dict()
        cov_dict["coverage_state"] = "FAILED"
        cov_dict["failure_reason_codes"] = combined_reasons
        cov_dict["evidence_sha256"] = ""
        computed_sha = canonical_sha256(cov_dict, excluded=("evidence_sha256",))
        cov_dict["evidence_sha256"] = computed_sha

        coverage = replace(
            temp_coverage,
            coverage_state="FAILED",
            data_artifact_binding=data_binding,
            failure_reason_codes=combined_reasons,
            evidence_sha256=computed_sha,
        )

        cov_path = save_feed_hour_coverage(coverage, self.coverage_dir)
        if self.tracer is not None:
            self.tracer.append("archive_coverage")
        cov_rel = self._relative_coverage_path(cov_path)
        cov_artifact = ImmutableArtifact(
            kind=ArtifactKind.COVERAGE_EVIDENCE,
            source_path=cov_path,
            relative_path=cov_rel,
            environment_id=self.environment_id,
            collector_epoch=coverage.collector_epoch,
            collector_run_id=coverage.collector_run_id,
            cohort=coverage.cohort_utc,
            exchange=coverage.exchange,
            stream=coverage.stream,
            market=coverage.market,
            source_sha256=file_sha256(cov_path),
            source_size=cov_path.stat().st_size,
        )
        cov_receipt = self.coverage_archive.finalize_artifact(cov_artifact)
        return ClosedSlotResult(
            coverage=coverage,
            coverage_receipt=cov_receipt,
            raw_receipt=raw_receipt,
            failure_reason_codes=coverage.failure_reason_codes,
        )

    def finalize_slot(self, observation: FrozenFeedHourObservation) -> ClosedSlotResult:
        """Enforce strict V3 ordering for a single feed slot."""
        gate_failures = evaluate_common_gate(observation, self.heartbeat_policy)
        if gate_failures:
            return self._persist_failed(observation, gate_failures)

        if observation.event_count == 0:
            if self.tracer is not None:
                self.tracer.append("materialize_verified_zero_event")
            coverage = materialize_feed_hour_coverage(
                observation,
                self.heartbeat_policy,
                None,
                runtime_commit=self.runtime_commit,
                runtime_config_fingerprint=self.runtime_config_fingerprint,
                environment_id=self.environment_id,
            )
            if coverage.coverage_state != "VERIFIED_ZERO_EVENT":
                return self._persist_failed(observation, coverage.failure_reason_codes)

            if self.tracer is not None:
                self.tracer.append("archive_coverage")
            cov_path = save_feed_hour_coverage(coverage, self.coverage_dir)
            cov_rel = self._relative_coverage_path(cov_path)
            cov_artifact = ImmutableArtifact(
                kind=ArtifactKind.COVERAGE_EVIDENCE,
                source_path=cov_path,
                relative_path=cov_rel,
                environment_id=self.environment_id,
                collector_epoch=coverage.collector_epoch,
                collector_run_id=coverage.collector_run_id,
                cohort=coverage.cohort_utc,
                exchange=coverage.exchange,
                stream=coverage.stream,
                market=coverage.market,
                source_sha256=file_sha256(cov_path),
                source_size=cov_path.stat().st_size,
            )
            cov_receipt = self.coverage_archive.finalize_artifact(cov_artifact)

            if self.tracer is not None:
                self.tracer.append("verify_coverage_restore")
            if cov_receipt.restore_verified_at is None:
                return self._persist_failed(observation, ("COVERAGE_RESTORE_VERIFICATION_FAILED",))

            return ClosedSlotResult(
                coverage=coverage,
                coverage_receipt=cov_receipt,
                raw_receipt=None,
                failure_reason_codes=coverage.failure_reason_codes,
            )

        # event_count > 0: RAW stage must precede coverage
        if self.tracer is not None:
            self.tracer.append("manifest_raw")

        raw_path, entry_id = self._find_raw_path(observation)
        if raw_path is None or not raw_path.exists():
            return self._persist_failed(observation, ("RAW_PARTITION_MISSING",))

        manifest, manifest_path = self._ensure_manifest(raw_path, observation)
        if manifest is None or manifest_path is None:
            return self._persist_failed(observation, ("RAW_MANIFEST_MISSING",))

        # Ensure registered in progress store if not yet registered
        raw_rel = str(raw_path.relative_to(self.raw_archive.raw_root))
        if entry_id is None:
            ident = FinalizationIdentity(
                environment_id=self.environment_id,
                collector_epoch=observation.session_segments[0].collector_epoch if observation.session_segments else self.raw_archive.collector_epoch,
                collector_run_id=observation.session_segments[0].collector_run_id if observation.session_segments else self.raw_archive.run_id,
                cohort=observation.cohort_utc,
                exchange=observation.feed.exchange,
                stream=observation.feed.stream,
                market=observation.feed.market,
                feed_identity=observation.feed.canonical,
                raw_relative_path=raw_rel,
            )
            entry = self.progress_store.register_pending(ident)
            entry_id = entry.entry_id

        # Pre-archive check: observation count must match manifest record count
        if observation.event_count != manifest.record_count:
            return self._persist_failed(
                observation,
                ("RECORD_COUNT_MISMATCH", "EVENT_COUNT_MISMATCH"),
                manifest=manifest,
                manifest_path=manifest_path,
            )

        if self.tracer is not None:
            self.tracer.append("archive_raw")

        try:
            m_rel = str(manifest_path.relative_to(self.raw_archive.manifest_root))
        except ValueError:
            m_rel = str(manifest_path)

        raw_artifact = ImmutableArtifact(
            kind=ArtifactKind.RAW_DATA,
            source_path=raw_path,
            relative_path=raw_rel,
            environment_id=self.environment_id,
            collector_epoch=observation.session_segments[0].collector_epoch if observation.session_segments else self.raw_archive.collector_epoch,
            collector_run_id=observation.session_segments[0].collector_run_id if observation.session_segments else self.raw_archive.run_id,
            cohort=observation.cohort_utc,
            exchange=observation.feed.exchange,
            stream=observation.feed.stream,
            market=observation.feed.market,
            source_sha256=manifest.sha256,
            source_size=manifest.bytes,
            source_record_count=manifest.record_count,
            manifest_path=manifest_path,
            manifest_sha256=file_sha256(manifest_path),
        )

        try:
            raw_receipt = self.raw_archive.finalize_artifact(
                raw_artifact,
                grace_period=timedelta(seconds=0),
                stability_wait_seconds=self.stability_wait_seconds,
            )
        except Exception as exc:
            reasons = [f"RAW_ARCHIVE_ERROR: {exc}"]
            if "does not match its manifest" in str(exc) or "record" in str(exc).lower():
                reasons.extend(["RECORD_COUNT_MISMATCH", "EVENT_COUNT_MISMATCH"])
            return self._persist_failed(
                observation,
                reasons,
                manifest=manifest,
                manifest_path=manifest_path,
            )

        if self.tracer is not None:
            self.tracer.append("verify_raw_restore")

        if raw_receipt.restore_verified_at is None:
            return self._persist_failed(
                observation,
                ("RAW_RESTORE_VERIFICATION_FAILED",),
                raw_receipt=raw_receipt,
                manifest=manifest,
                manifest_path=manifest_path,
            )

        # Equal counts check
        if not (observation.event_count == manifest.record_count == raw_receipt.source_record_count):
            return self._persist_failed(
                observation,
                ("RECORD_COUNT_MISMATCH", "EVENT_COUNT_MISMATCH"),
                raw_receipt=raw_receipt,
                manifest=manifest,
                manifest_path=manifest_path,
            )

        receipt_path = self.raw_archive.artifact_receipt_path(raw_artifact)
        try:
            rec_rel = str(receipt_path.relative_to(self.raw_archive.receipt_root))
        except ValueError:
            rec_rel = str(receipt_path)

        # Update progress store
        artifact_binding = ArtifactBinding(
            source_size=raw_receipt.source_size,
            source_sha256=raw_receipt.source_sha256,
            source_record_count=raw_receipt.source_record_count or 0,
            manifest_relative_path=m_rel,
            manifest_file_sha256=file_sha256(manifest_path),
            receipt_relative_path=rec_rel,
            receipt_file_sha256=file_sha256(receipt_path),
            receipt_state=raw_receipt.state,
            artifact_kind="RAW_DATA",
        )
        self.progress_store.mark_reused(entry_id, artifact_binding)

        if self.tracer is not None:
            self.tracer.append("materialize_data_present")

        data_binding = DataArtifactBinding(
            raw_relative_path=raw_rel,
            raw_size=raw_receipt.source_size,
            raw_sha256=raw_receipt.source_sha256,
            manifest_relative_path=m_rel,
            manifest_file_sha256=file_sha256(manifest_path),
            manifest_record_count=manifest.record_count,
            receipt_relative_path=rec_rel,
            receipt_file_sha256=file_sha256(receipt_path),
            receipt_source_record_count=raw_receipt.source_record_count or 0,
        )

        coverage = materialize_feed_hour_coverage(
            observation,
            self.heartbeat_policy,
            data_binding,
            runtime_commit=self.runtime_commit,
            runtime_config_fingerprint=self.runtime_config_fingerprint,
            environment_id=self.environment_id,
        )

        if coverage.coverage_state != "DATA_PRESENT":
            return self._persist_failed(
                observation,
                coverage.failure_reason_codes,
                raw_receipt=raw_receipt,
                manifest=manifest,
                manifest_path=manifest_path,
            )

        if self.tracer is not None:
            self.tracer.append("archive_coverage")

        cov_path = save_feed_hour_coverage(coverage, self.coverage_dir)
        cov_rel = self._relative_coverage_path(cov_path)
        cov_artifact = ImmutableArtifact(
            kind=ArtifactKind.COVERAGE_EVIDENCE,
            source_path=cov_path,
            relative_path=cov_rel,
            environment_id=self.environment_id,
            collector_epoch=coverage.collector_epoch,
            collector_run_id=coverage.collector_run_id,
            cohort=coverage.cohort_utc,
            exchange=coverage.exchange,
            stream=coverage.stream,
            market=coverage.market,
            source_sha256=file_sha256(cov_path),
            source_size=cov_path.stat().st_size,
        )
        cov_receipt = self.coverage_archive.finalize_artifact(cov_artifact)

        if self.tracer is not None:
            self.tracer.append("verify_coverage_restore")

        if cov_receipt.restore_verified_at is None:
            return self._persist_failed(
                observation,
                ("COVERAGE_RESTORE_VERIFICATION_FAILED",),
                raw_receipt=raw_receipt,
                manifest=manifest,
                manifest_path=manifest_path,
            )

        return ClosedSlotResult(
            coverage=coverage,
            coverage_receipt=cov_receipt,
            raw_receipt=raw_receipt,
            failure_reason_codes=coverage.failure_reason_codes,
        )

    def finalize_cohort(self, cohort_key: str) -> Sequence[ClosedSlotResult]:
        """Validate frozen journal feeds and finalize all 76 slots in order."""
        journal_path = self.journals_dir / f"journal_{cohort_key}.json"
        if not journal_path.exists():
            raise FileNotFoundError(f"Frozen journal not found: {journal_path}")

        observations = load_frozen_journal(journal_path)

        obs_by_feed: dict[str, list[FrozenFeedHourObservation]] = {}
        for obs in observations:
            obs_by_feed.setdefault(obs.feed.canonical, []).append(obs)

        missing = SEALED_FEED_CANONICAL_SET - set(obs_by_feed.keys())
        duplicates = [feed for feed, items in obs_by_feed.items() if len(items) > 1]
        foreign = set(obs_by_feed.keys()) - SEALED_FEED_CANONICAL_SET

        if len(observations) != 76 or missing or duplicates or foreign:
            err_parts = []
            if missing:
                err_parts.append(f"MISSING_FEEDS: {sorted(missing)}")
            if duplicates:
                err_parts.append(f"DUPLICATE_FEEDS: {sorted(duplicates)}")
            if foreign:
                err_parts.append(f"FOREIGN_FEEDS: {sorted(foreign)}")
            if len(observations) != 76 and not (missing or duplicates or foreign):
                err_parts.append(f"INVALID_COUNT: expected 76, got {len(observations)}")
            raise ValueError(f"Cohort journal validation failed: {'; '.join(err_parts)}")

        results: list[ClosedSlotResult] = []
        for feed in SEALED_FEED_UNIVERSE:
            obs = obs_by_feed[feed.canonical][0]
            slot_res = self.finalize_slot(obs)
            results.append(slot_res)

        return tuple(results)
