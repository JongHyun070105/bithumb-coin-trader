#!/usr/bin/env python3
"""Read-only terminal audit for the sealed Fresh 30H-v3 evidence export.

The auditor consumes an offline evidence bundle. It never connects to AWS or an
exchange and never writes below the evidence directory. Missing evidence is
reported as NOT_VERIFIABLE; an asserted supervisor PASS cannot override a
failed cohort, missing receipt, or inconsistent hash.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from math import isfinite
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


PASS = "PASS"
FAIL = "FAIL"
NOT_VERIFIABLE = "NOT_VERIFIABLE"
EXPECTED_RUNTIME_COMMIT = "22e06b9527798567e185fb0dd41dca3a448f444e"
EXPECTED_RUNTIME_TREE = "a44c9591045f3634cda1066d582927f277b79e2d"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AuditCheck:
    name: str
    status: str
    summary: str
    details: Mapping[str, Any]


class Bundle:
    """Confined read-only access to paths declared by audit-bundle.json."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.manifest_path = self.root / "audit-bundle.json"
        self.manifest: dict[str, Any] | None = None
        self.manifest_error: str | None = None
        if not self.root.is_dir():
            self.manifest_error = "evidence bundle directory is missing"
            return
        try:
            payload = _read_json(self.manifest_path)
            if not isinstance(payload, dict) or payload.get("schema_version") != 1:
                raise ValueError("audit-bundle.json must use schema_version 1")
            if not isinstance(payload.get("paths"), dict):
                raise ValueError("audit-bundle.json paths must be an object")
            if not isinstance(payload.get("directories"), dict):
                raise ValueError("audit-bundle.json directories must be an object")
            self.manifest = payload
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.manifest_error = str(exc)

    def resolve(self, section: str, key: str) -> Path | None:
        if self.manifest is None:
            return None
        table = self.manifest.get(section)
        rel = table.get(key) if isinstance(table, dict) else None
        if rel is None:
            return None
        if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
            raise ValueError(f"{section}.{key} must be a non-empty relative path")
        candidate = (self.root / rel).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError(f"{section}.{key} escapes the evidence bundle")
        return candidate

    def json(self, key: str) -> dict[str, Any] | None:
        path = self.resolve("paths", key)
        if path is None or not path.is_file():
            return None
        payload = _read_json(path)
        return payload if isinstance(payload, dict) else None

    def directory(self, key: str) -> Path | None:
        path = self.resolve("directories", key)
        return path if path is not None and path.is_dir() else None


class Fresh30HTerminalAuditor:
    def __init__(
        self,
        evidence_dir: Path,
        *,
        expected_runtime_commit: str = EXPECTED_RUNTIME_COMMIT,
        expected_runtime_tree: str = EXPECTED_RUNTIME_TREE,
    ) -> None:
        self.bundle = Bundle(evidence_dir)
        self.expected_runtime_commit = expected_runtime_commit
        self.expected_runtime_tree = expected_runtime_tree
        self.checks: list[AuditCheck] = []

    def add(self, name: str, status: str, summary: str, **details: Any) -> None:
        self.checks.append(AuditCheck(name, status, summary, details))

    def audit(self) -> dict[str, Any]:
        if self.bundle.manifest is None:
            self.add(
                "evidence_bundle",
                NOT_VERIFIABLE,
                "A valid offline audit-bundle.json is required.",
                error=self.bundle.manifest_error,
            )
            return self._report()

        identity = self._load("identity")
        runtime = self._load("runtime")
        result = self._load("result")
        start = self._load("actual_start")
        witness = self._load("terminal_witness")

        self._audit_runtime_identity(identity, runtime, result, witness)
        self._audit_duration(runtime, result, start)
        self._audit_exits(result)
        self._audit_systemd(self._load("systemd"))
        self._audit_witness(identity, runtime, result, witness)
        self._audit_collector_lifecycle(self._load("collector_lifecycle"))
        self._audit_collector_metrics(self._load("collector_metrics"))
        self._audit_redundancy(
            runtime,
            self._load("collector_metrics"),
            self._load("redundancy_metrics"),
        )
        self._audit_cohorts(identity, runtime)
        self._audit_slots(identity, runtime)
        self._audit_receipt_hashes()
        self._audit_receipt_immutability(identity, runtime)
        self._audit_evidence_index()
        self._audit_finalization_trace()
        return self._report()

    def _load(self, key: str) -> dict[str, Any] | None:
        try:
            return self.bundle.json(key)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.add(key, FAIL, "Evidence path or JSON is invalid.", error=str(exc))
            return None

    def _audit_runtime_identity(
        self,
        identity: dict[str, Any] | None,
        runtime: dict[str, Any] | None,
        result: dict[str, Any] | None,
        witness: dict[str, Any] | None,
    ) -> None:
        if identity is None or runtime is None:
            self.add(
                "runtime_identity",
                NOT_VERIFIABLE,
                "Sealed identity and runtime configuration are both required.",
            )
            return
        commit = _first_text(
            identity, "software_commit_sha", "runtime_commit", "runtime_software_commit"
        )
        tree = _first_text(identity, "software_tree_sha", "runtime_tree", "runtime_tree_sha")
        config_commit = _first_text(
            runtime, "runtime_software_commit", "software_commit_sha", "runtime_commit"
        )
        config_tree = _first_text(runtime, "software_tree_sha", "runtime_tree_sha")
        details = {
            "expected_commit": self.expected_runtime_commit,
            "identity_commit": commit,
            "expected_tree": self.expected_runtime_tree,
            "identity_tree": tree,
            "runtime_config_commit": config_commit,
            "runtime_config_tree": config_tree,
        }
        if not commit or not tree or not config_commit:
            self.add(
                "runtime_identity",
                NOT_VERIFIABLE,
                "Runtime commit/tree fields are incomplete.",
                **details,
            )
            return
        mismatch = (
            commit != self.expected_runtime_commit
            or tree != self.expected_runtime_tree
            or config_commit != commit
            or (config_tree is not None and config_tree != tree)
        )
        run_id = _first_text(identity, "run_id", "collector_run_id")
        epoch = _first_text(identity, "epoch", "collector_epoch")
        reported_run_ids = [
            _first_text(item, "run_id", "collector_run_id")
            for item in (result, witness)
            if item is not None
        ]
        reported_epochs = [
            _first_text(item, "epoch", "collector_epoch")
            for item in (result, witness)
            if item is not None
        ]
        if any(value is not None and value != run_id for value in reported_run_ids):
            mismatch = True
        if any(value is not None and value != epoch for value in reported_epochs):
            mismatch = True
        details.update({"run_id": run_id, "epoch": epoch})
        self.add(
            "runtime_identity",
            FAIL if mismatch else PASS,
            "Runtime identity matches the Fresh 30H-v3 seal."
            if not mismatch
            else "Runtime commit, tree, epoch, or run ID differs from the sealed identity.",
            **details,
        )

    def _audit_duration(
        self,
        runtime: dict[str, Any] | None,
        result: dict[str, Any] | None,
        start: dict[str, Any] | None,
    ) -> None:
        if runtime is None or result is None or start is None:
            self.add(
                "actual_start_end_and_duration",
                NOT_VERIFIABLE,
                "Runtime, supervisor result, and actual-start evidence are required.",
            )
            return
        expected = _first_number(
            runtime,
            "duration_seconds",
            "collection_duration_seconds",
            nested=("execution", "collection_duration_seconds"),
        )
        actual_start = _first_text(start, "actual_start_time_utc", "actual_start_utc")
        actual_end = _first_text(result, "ended_at", "actual_end_time_utc")
        start_dt = _parse_aware_datetime(actual_start)
        end_dt = _parse_aware_datetime(actual_end)
        elapsed = (
            (end_dt - start_dt).total_seconds()
            if start_dt is not None and end_dt is not None
            else None
        )
        full_duration = result.get("full_duration_satisfied")
        supervisor_elapsed = _first_number(result, "elapsed_seconds")
        details = {
            "actual_start_utc": actual_start,
            "actual_end_utc": actual_end,
            "observed_wall_duration_seconds": elapsed,
            "supervisor_elapsed_seconds": supervisor_elapsed,
            "required_duration_seconds": expected,
            "full_duration_satisfied": full_duration,
        }
        if (
            expected is None
            or elapsed is None
            or supervisor_elapsed is None
            or not isinstance(full_duration, bool)
            or not isfinite(expected)
            or not isfinite(supervisor_elapsed)
            or expected <= 0
        ):
            status = NOT_VERIFIABLE
        elif full_duration is False or elapsed < 0 or elapsed < expected or supervisor_elapsed < expected:
            status = FAIL
        else:
            status = PASS
        self.add(
            "actual_start_end_and_duration",
            status,
            "Actual timestamps and supervisor duration satisfy the sealed runtime duration."
            if status == PASS
            else "Actual duration is short or the required timestamps/duration evidence is missing.",
            **details,
        )

    def _audit_exits(self, result: dict[str, Any] | None) -> None:
        if result is None:
            self.add("component_exit_codes", NOT_VERIFIABLE, "Supervisor result is missing.")
            return
        collector = _integer_including_negative(result, "collector_exit_code")
        scheduler = _integer_including_negative(result, "archive_scheduler_exit_code", "scheduler_exit_code")
        publisher = _integer_including_negative(result, "publisher_exit_code")
        started = (
            result.get("archive_scheduler_started") is True
            and result.get("publisher_started") is True
        )
        values = {
            "collector_exit_code": collector,
            "scheduler_exit_code": scheduler,
            "publisher_exit_code": publisher,
            "required_components_started": started,
        }
        if collector is None or scheduler is None or publisher is None:
            status = NOT_VERIFIABLE
        else:
            status = PASS if started and collector == scheduler == publisher == 0 else FAIL
        self.add(
            "component_exit_codes",
            status,
            "Collector, archive scheduler, and publisher all started and exited cleanly."
            if status == PASS
            else "One or more component exits or startup records are failed or missing.",
            **values,
        )

    def _audit_systemd(self, systemd: dict[str, Any] | None) -> None:
        if systemd is None:
            self.add("systemd_result_and_restarts", NOT_VERIFIABLE, "Systemd terminal properties are missing.")
            return
        result = _first_text(systemd, "Result", "result")
        restarts = _first_integer(systemd, "NRestarts", "n_restarts")
        exit_status = _integer_including_negative(systemd, "ExecMainStatus", "exec_main_status")
        if result is None or restarts is None or exit_status is None:
            status = NOT_VERIFIABLE
        else:
            status = PASS if result.lower() == "success" and restarts == 0 and exit_status == 0 else FAIL
        self.add(
            "systemd_result_and_restarts",
            status,
            "Systemd reports success, zero restarts, and zero main-process exit status."
            if status == PASS
            else "Systemd evidence is incomplete or reports a restart/failure.",
            result=result,
            n_restarts=restarts,
            exec_main_status=exit_status,
        )

    def _audit_witness(
        self,
        identity: dict[str, Any] | None,
        runtime: dict[str, Any] | None,
        result: dict[str, Any] | None,
        witness: dict[str, Any] | None,
    ) -> None:
        if identity is None or result is None or witness is None:
            self.add("terminal_witness", NOT_VERIFIABLE, "Identity, supervisor result, and terminal witness are required.")
            return
        expected_run_id = _first_text(identity, "run_id", "collector_run_id")
        expected_epoch = _first_text(identity, "epoch", "collector_epoch")
        witness_run_id = _first_text(witness, "run_id", "collector_run_id")
        witness_epoch = _first_text(witness, "epoch", "collector_epoch")
        classification = _first_text(witness, "terminal_classification")
        service_result = _first_text(witness, "service_result")
        exit_status = str(witness.get("exit_status", ""))
        s3_uploaded = witness.get("s3_uploaded")
        expected_bucket = _first_text(identity, "s3_bucket")
        expected_prefix = _first_text(identity, "s3_prefix", "archive_prefix")
        expected_region = _first_text(identity, "s3_region", "region")
        runtime_region = _first_text(runtime or {}, "region")
        expected_region = expected_region or runtime_region
        if not expected_prefix and runtime:
            archive = _mapping(runtime.get("archive"))
            prefix_template = _first_text(archive, "temporary_prefix_template")
            if prefix_template and expected_epoch:
                expected_prefix = prefix_template.replace("{collector_epoch}", expected_epoch)
        expected_prefix = expected_prefix.rstrip("/") if expected_prefix else None
        expected_key = f"{expected_prefix}/terminal/terminal-receipt.json" if expected_prefix else None
        actual_s3_key = witness.get("s3_key")
        actual_bucket = _first_text(witness, "s3_bucket")
        actual_prefix = _first_text(witness, "s3_prefix")
        actual_region = _first_text(witness, "s3_region")
        s3_target_matches = (
            expected_key is not None
            and actual_s3_key == expected_key
            and expected_bucket is not None
            and actual_bucket == expected_bucket
            and (expected_region is None or actual_region == expected_region)
            and (actual_prefix is None or actual_prefix.rstrip("/") == expected_prefix)
        )
        details = {
            "run_id_matches": witness_run_id == expected_run_id if expected_run_id else None,
            "epoch_matches": witness_epoch == expected_epoch if expected_epoch else None,
            "classification": classification,
            "service_result": service_result,
            "exit_status": exit_status,
            "witness_s3_uploaded": s3_uploaded,
            "expected_s3_key": expected_key,
            "observed_s3_key": actual_s3_key,
            "s3_bucket_matches": actual_bucket == expected_bucket if expected_bucket else None,
            "s3_region_matches": actual_region == expected_region if expected_region else None,
            "s3_target_matches": s3_target_matches,
        }
        complete = all(value is not None for value in (
            expected_run_id, expected_epoch, witness_run_id, witness_epoch,
            classification, service_result,
        )) and "exit_status" in witness and isinstance(s3_uploaded, bool) and "s3_key" in witness
        clean = (
            complete
            and witness_run_id == expected_run_id
            and witness_epoch == expected_epoch
            and classification == "CLEAN_SUCCESS"
            and service_result is not None
            and service_result.lower() in {"success", "none"}
            and exit_status == "0"
            and s3_uploaded is True
            and s3_target_matches
        )
        status = PASS if clean else (FAIL if complete else NOT_VERIFIABLE)
        self.add(
            "terminal_witness",
            status,
            "Terminal witness is clean and bound to the sealed run."
            if status == PASS
            else "Terminal witness is failed, mismatched, or incomplete.",
            **details,
        )

    def _audit_collector_lifecycle(self, lifecycle: dict[str, Any] | None) -> None:
        if lifecycle is None:
            self.add("collector_lifecycle", NOT_VERIFIABLE, "Collector lifecycle receipt is missing.")
            return
        phase = _first_text(lifecycle, "phase", "lifecycle_state")
        flushed = lifecycle.get("final_manifest_flush_observed")
        if phase is None or not isinstance(flushed, bool):
            status = NOT_VERIFIABLE
        else:
            status = PASS if phase.upper() in {"COMPLETE", "COMPLETED"} and flushed else FAIL
        self.add(
            "collector_lifecycle",
            status,
            "Collector reached COMPLETE and flushed final manifests."
            if status == PASS
            else "Collector lifecycle is failed or lacks terminal flush evidence.",
            phase=phase,
            final_manifest_flush_observed=flushed,
        )

    def _audit_collector_metrics(self, metrics: dict[str, Any] | None) -> None:
        if metrics is None:
            self.add("queue_persistence_and_writer", NOT_VERIFIABLE, "Final collector metrics are missing.")
            return
        exchanges = metrics.get("exchanges")
        if not isinstance(exchanges, dict):
            exchanges = {}
        queue_depth = _first_integer(metrics, "queue_size", "final_queue_depth")
        if queue_depth is None and isinstance(metrics.get("writer"), dict):
            queue_depth = _first_integer(metrics["writer"], "queue_depth", "final_queue_depth")
        unpersisted = _first_integer(metrics, "unpersisted_event_count")
        if unpersisted is None and isinstance(metrics.get("writer"), dict):
            unpersisted = _first_integer(metrics["writer"], "unpersisted_count")
        writer_errors = _sum_metric(exchanges, "writer_errors")
        if writer_errors is None:
            writer_errors = _first_integer(metrics, "writer_error_count")
        dropped = _sum_metric(exchanges, "queue_dropped_events")
        if dropped is None:
            dropped = _first_integer(metrics, "queue_dropped_event_count", "queue_dropped_events")
        backpressure = _sum_metric(exchanges, "queue_backpressure_events")
        if backpressure is None:
            backpressure = _first_integer(metrics, "queue_backpressure_event_count")
        values = {
            "final_queue_depth": queue_depth,
            "unpersisted_records": unpersisted,
            "writer_errors": writer_errors,
            "dropped_records": dropped,
            "backpressure_events": backpressure,
        }
        required = (queue_depth, unpersisted, writer_errors, dropped, backpressure)
        if any(value is None for value in required):
            status = NOT_VERIFIABLE
        else:
            status = PASS if all(value == 0 for value in (queue_depth, unpersisted, writer_errors, dropped)) else FAIL
        summary = (
            "Final queue, unpersisted records, writer errors, and drops are zero; backpressure is reported."
            if status == PASS
            else "Required queue/persistence metrics are missing or at least one failure counter is non-zero."
        )
        self.add("queue_persistence_and_writer", status, summary, **values)

    def _audit_redundancy(
        self,
        runtime: dict[str, Any] | None,
        metrics: dict[str, Any] | None,
        redundancy: dict[str, Any] | None,
    ) -> None:
        source = redundancy or metrics
        if source is None or runtime is None:
            self.add("active_active_dedup_and_conflicts", NOT_VERIFIABLE, "Redundancy evidence is missing.")
            return
        bithumb = _mapping(source.get("bithumb"))
        exchanges = _mapping(source.get("exchanges"))
        if not bithumb:
            bithumb = _mapping(exchanges.get("bithumb"))
        enabled = _first_bool(
            source,
            "active_active_enabled",
            "bithumb_redundancy_enabled",
            nested=("redundancy", "enabled"),
        )
        connections = _first_integer(source, "bithumb_connection_count", "active_bithumb_connections")
        conflicts = _first_integer(
            bithumb,
            "conflicting_duplicate_frames",
            "conflicting_duplicates",
            "conflict_count",
        )
        duplicates = _first_integer(bithumb, "deduplicated_frames", "trade_duplicates", "duplicate_count")
        configured_enabled = _first_bool(runtime, "bithumb_redundancy_enabled", nested=("redundancy", "enabled"))
        configured_connections = _first_integer(runtime, "bithumb_connection_count", "active_bithumb_connections")
        if (
            enabled is None
            or connections is None
            or conflicts is None
            or duplicates is None
            or configured_enabled is None
            or configured_connections is None
        ):
            status = NOT_VERIFIABLE
        else:
            status = PASS if (
                enabled
                and connections >= 2
                and conflicts == 0
                and configured_enabled
                and configured_connections >= 2
                and enabled == configured_enabled
                and connections == configured_connections
            ) else FAIL
        self.add(
            "active_active_dedup_and_conflicts",
            status,
            "Two Bithumb sockets were active, deduplication was enabled, and no conflicting duplicate was observed."
            if status == PASS
            else "Active-active dedup evidence is incomplete, disabled, or recorded a conflict.",
            active_active_enabled=enabled,
            bithumb_connection_count=connections,
            configured_active_active_enabled=configured_enabled,
            configured_bithumb_connection_count=configured_connections,
            deduplicated_frames=duplicates,
            conflicting_duplicates=conflicts,
        )

    def _cohorts_and_feeds(
        self,
        identity: dict[str, Any] | None,
        runtime: dict[str, Any] | None,
    ) -> tuple[list[str] | None, list[str] | None, int | None]:
        schedule = _mapping(runtime.get("schedule")) if runtime else {}
        cohorts = _first_string_list(
            identity or {}, "qualifying_cohorts", "expected_qualifying_cohorts"
        ) or _first_string_list(schedule, "qualifying_cohorts", "expected_qualifying_cohorts")
        raw_feeds = None
        if self.bundle.manifest:
            raw_feeds = self.bundle.manifest.get("expected_feed_universe")
        if raw_feeds is None and identity:
            raw_feeds = identity.get("feed_universe")
        if raw_feeds is None and runtime:
            raw_feeds = runtime.get("feed_universe")
            if raw_feeds is None:
                raw_feeds = _mapping(runtime.get("feeds")).get("feed_universe")
        feeds = _normalize_feeds(raw_feeds)
        feed_count = _first_integer(identity or {}, "feed_count", "expected_feed_count")
        if feed_count is None and runtime:
            feed_count = _first_integer(runtime, "feed_count", "expected_feed_count")
        if feed_count is None and feeds is not None:
            feed_count = len(feeds)
        return cohorts, feeds, feed_count

    def _audit_cohorts(
        self,
        identity: dict[str, Any] | None,
        runtime: dict[str, Any] | None,
    ) -> None:
        cohorts, _, feed_count = self._cohorts_and_feeds(identity, runtime)
        if not cohorts or feed_count is None:
            self.add(
                "qualifying_cohorts_and_feed_counts",
                NOT_VERIFIABLE,
                "The sealed qualifying-cohort list and feed count are required.",
            )
            return
        receipts_root = self.bundle.directory("local_receipts")
        if receipts_root is None:
            self.add(
                "qualifying_cohorts_and_feed_counts",
                NOT_VERIFIABLE,
                "Local archive receipt directory is missing.",
                expected_cohorts=len(cohorts),
            )
            return
        candidates = list(receipts_root.rglob("cohort_*_finalized.json"))
        by_cohort: dict[str, list[Path]] = {}
        for path in candidates:
            data = _safe_read_json(path)
            if data is None:
                continue
            cohort = _first_text(data, "cohort", "cohort_utc")
            if cohort:
                by_cohort.setdefault(cohort, []).append(path)
        missing: list[str] = []
        duplicate: list[str] = []
        failed: list[str] = []
        for cohort in cohorts:
            paths = by_cohort.get(cohort, [])
            if not paths:
                missing.append(cohort)
                continue
            if len(paths) != 1:
                duplicate.append(cohort)
                continue
            data = _safe_read_json(paths[0]) or {}
            slot_count = _first_integer(data, "total_slots", "expected_slot_count")
            failed_count = _first_integer(data, "failed_count", "failed_slots")
            state = _first_text(data, "status", "state")
            if (
                state is None
                or state.upper() != "PASS"
                or slot_count != feed_count
                or failed_count != 0
            ):
                failed.append(cohort)
        status = FAIL if missing or duplicate or failed else PASS
        self.add(
            "qualifying_cohorts_and_feed_counts",
            status,
            "Every sealed qualifying cohort has exactly one passing receipt with the expected slot count."
            if status == PASS
            else "A qualifying cohort receipt is missing, duplicated, failed, or has a slot-count mismatch.",
            expected_cohorts=len(cohorts),
            expected_slots_per_cohort=feed_count,
            missing=missing,
            duplicate=duplicate,
            failed_or_mismatched=failed,
        )

    def _audit_slots(
        self,
        identity: dict[str, Any] | None,
        runtime: dict[str, Any] | None,
    ) -> None:
        cohorts, feeds, _ = self._cohorts_and_feeds(identity, runtime)
        receipts_root = self.bundle.directory("local_receipts")
        if not cohorts or feeds is None or receipts_root is None:
            self.add(
                "all_feed_slots_and_receipts",
                NOT_VERIFIABLE,
                "Exact sealed feed universe, qualifying cohorts, and local receipts are required.",
            )
            return
        expected = {(cohort, feed) for cohort in cohorts for feed in feeds}
        observed: dict[tuple[str, str], list[Path]] = {}
        invalid: list[str] = []
        for path in receipts_root.rglob("*coverage*archive-receipt*.json"):
            data = _safe_read_json(path)
            if data is None:
                invalid.append(str(path.relative_to(receipts_root)))
                continue
            identity_pair = _receipt_slot(path, receipts_root, data, cohorts)
            if identity_pair is None:
                invalid.append(str(path.relative_to(receipts_root)))
                continue
            observed.setdefault(identity_pair, []).append(path)
            state = _first_text(data, "state", "status", "terminal_state")
            if state is None or state.upper() not in {
                "PASS", "RESTORE_VERIFIED", "CLEANUP_ELIGIBLE", "CLEANED"
            }:
                invalid.append(str(path.relative_to(receipts_root)))
        duplicates = sorted(f"{cohort}/{feed}" for (cohort, feed), paths in observed.items() if len(paths) != 1)
        actual = set(observed)
        missing = sorted(f"{cohort}/{feed}" for cohort, feed in expected - actual)
        foreign = sorted(f"{cohort}/{feed}" for cohort, feed in actual - expected)
        status = FAIL if missing or duplicates or foreign or invalid else PASS
        self.add(
            "all_feed_slots_and_receipts",
            status,
            "Every sealed feed slot has exactly one valid local coverage receipt."
            if status == PASS
            else "Feed-slot receipts are missing, duplicated, foreign, unreadable, or non-terminal.",
            expected_slots=len(expected),
            observed_slots=len(actual),
            missing=missing[:100],
            duplicate=duplicates[:100],
            foreign=foreign[:100],
            invalid=invalid[:100],
        )

    def _audit_receipt_hashes(self) -> None:
        local = self.bundle.directory("local_receipts")
        remote = self.bundle.directory("s3_receipts")
        if local is None or remote is None:
            self.add(
                "local_s3_receipt_hash_equality",
                NOT_VERIFIABLE,
                "Offline local and S3 receipt mirrors are both required.",
            )
            return
        local_files = _receipt_files(local)
        remote_files = _receipt_files(remote)
        if not local_files:
            self.add(
                "local_s3_receipt_hash_equality",
                NOT_VERIFIABLE,
                "No local receipt files were found.",
            )
            return
        missing: list[str] = []
        different: list[str] = []
        for rel, local_path in local_files.items():
            remote_path = remote_files.get(rel)
            if remote_path is None:
                missing.append(rel)
            elif _file_sha256(local_path) != _file_sha256(remote_path):
                different.append(rel)
        extra = sorted(set(remote_files) - set(local_files))
        status = FAIL if missing or different or extra else PASS
        self.add(
            "local_s3_receipt_hash_equality",
            status,
            "Local and S3 receipt mirrors match byte-for-byte."
            if status == PASS
            else "Local/S3 receipt paths or file hashes differ.",
            compared=len(local_files),
            missing_from_s3=missing[:100],
            hash_mismatch=different[:100],
            extra_in_s3=extra[:100],
        )

    def _audit_receipt_immutability(
        self,
        identity: dict[str, Any] | None,
        runtime: dict[str, Any] | None,
    ) -> None:
        observations = self._load("receipt_observations")
        receipts_root = self.bundle.directory("local_receipts")
        rows = observations.get("qualifying_receipts") if observations else None
        if not isinstance(rows, list) or receipts_root is None:
            self.add(
                "receipt_immutability",
                NOT_VERIFIABLE,
                "Local receipts and observations at two independent times are required.",
            )
            return
        by_path = {
            _normalize_receipt_path(_first_text(row, "receipt_path", "path")): row
            for row in rows
            if isinstance(row, dict)
            and _normalize_receipt_path(_first_text(row, "receipt_path", "path")) is not None
        }
        receipt_files = _receipt_files(receipts_root)
        if not receipt_files:
            self.add(
                "receipt_immutability",
                NOT_VERIFIABLE,
                "No local receipt files were found.",
            )
            return
        missing: list[str] = []
        changed: list[str] = []
        for rel in receipt_files:
            row = by_path.get(rel)
            if row is None:
                missing.append(rel)
                continue
            first = _first_text(row, "receipt_sha256_t1", "sha256_t1")
            second = _first_text(row, "receipt_sha256_t2", "sha256_t2")
            close_times = row.get("closed_at_utc_values")
            if not first or not second or not SHA256_RE.fullmatch(first) or not SHA256_RE.fullmatch(second):
                missing.append(rel)
            elif first != second:
                changed.append(rel)
            elif second != _file_sha256(receipt_files[rel]):
                changed.append(rel)
            if isinstance(close_times, list) and len(set(close_times)) > 1 and rel not in changed:
                changed.append(rel)
        status = FAIL if changed else (NOT_VERIFIABLE if missing else PASS)
        self.add(
            "receipt_immutability",
            status,
            "Every local receipt retained the same hash across observations."
            if status == PASS
            else "Receipt hashes changed or immutable observations are missing.",
            expected_receipts=len(receipt_files),
            missing_observation=missing[:100],
            changed=changed[:100],
        )

    def _audit_evidence_index(self) -> None:
        index = self._load("evidence_hash_index")
        files = index.get("files") if index else None
        if not isinstance(files, list) or not files:
            self.add("evidence_hash_index", NOT_VERIFIABLE, "Evidence SHA-256 index is missing or empty.")
            return
        invalid: list[str] = []
        checked = 0
        for entry in files:
            if not isinstance(entry, dict):
                invalid.append("non-object entry")
                continue
            rel = entry.get("path")
            expected = entry.get("sha256")
            if not isinstance(rel, str) or Path(rel).is_absolute() or not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
                invalid.append(str(rel))
                continue
            path = (self.bundle.root / rel).resolve()
            if not path.is_relative_to(self.bundle.root) or not path.is_file():
                invalid.append(rel)
                continue
            checked += 1
            if _file_sha256(path) != expected:
                invalid.append(rel)
        status = FAIL if invalid else PASS
        self.add(
            "evidence_hash_index",
            status,
            "Every indexed evidence file matches its SHA-256."
            if status == PASS
            else "The evidence index contains an invalid, missing, or changed file.",
            indexed=len(files),
            checked=checked,
            invalid=invalid[:100],
        )

    def _audit_finalization_trace(self) -> None:
        trace = self._load("finalization_trace")
        if trace is None or trace.get("complete") is not True or not isinstance(trace.get("events"), list):
            for name in (
                "scheduler_retries",
                "finalizer_retries",
                "recovery_invocations",
                "duplicate_finalization",
                "closed_at_utc_stability",
                "finalization_evidence_hash_stability",
                "restart_idempotency_path",
            ):
                self.add(
                    name,
                    NOT_VERIFIABLE,
                    "A complete scheduler/finalizer/recovery event trace is required.",
                )
            return
        events = [event for event in trace["events"] if isinstance(event, dict)]
        scheduler_retries = [
            event for event in events
            if str(event.get("component", "")).lower() == "scheduler"
            and str(event.get("event", "")).lower() in {"retry", "retry_attempt"}
        ]
        finalizer_retry_events = [
            event for event in events
            if str(event.get("component", "")).lower() == "finalizer"
            and str(event.get("event", "")).lower() in {"retry", "retry_attempt"}
        ]
        recovery = [
            event for event in events
            if str(event.get("event", "")).lower() in {"recovery", "recovery_invoked"}
            or event.get("recovery_invoked") is True
        ]
        finalizations: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for event in events:
            component = str(event.get("component", "")).lower()
            name = str(event.get("event", "")).lower()
            if component != "finalizer" or name not in {"finalize", "finalization_complete", "completed"}:
                continue
            cohort = _first_text(event, "cohort", "cohort_utc")
            slot = _first_text(event, "slot", "feed_identity")
            if cohort and slot:
                finalizations.setdefault((cohort, slot), []).append(event)
        repeated = {
            key: rows for key, rows in finalizations.items() if len(rows) > 1
        }
        changed_closed: list[str] = []
        changed_hash: list[str] = []
        for (cohort, slot), rows in repeated.items():
            closed = {row.get("closed_at_utc") for row in rows if row.get("closed_at_utc") is not None}
            hashes = {
                row.get("evidence_sha256")
                for row in rows
                if row.get("evidence_sha256") is not None
            }
            label = f"{cohort}/{slot}"
            if len(closed) > 1:
                changed_closed.append(label)
            if len(hashes) > 1:
                changed_hash.append(label)
        repeat_labels = [f"{cohort}/{slot}" for cohort, slot in sorted(repeated)]
        self.add(
            "scheduler_retries",
            PASS if not scheduler_retries else FAIL,
            "No scheduler retries were observed."
            if not scheduler_retries
            else "Scheduler retries occurred and require run-specific review.",
            count=len(scheduler_retries),
        )
        self.add(
            "finalizer_retries",
            PASS if not finalizer_retry_events else FAIL,
            "No finalizer retry events were observed."
            if not finalizer_retry_events
            else "Finalizer retry events occurred on the runtime with known retry-sensitive evidence.",
            count=len(finalizer_retry_events),
        )
        self.add(
            "recovery_invocations",
            PASS if not recovery else FAIL,
            "No recovery invocation was observed."
            if not recovery
            else "A recovery path was invoked during the sealed run.",
            count=len(recovery),
        )
        self.add(
            "duplicate_finalization",
            PASS if not repeated else FAIL,
            "No feed slot was finalized more than once."
            if not repeated
            else "One or more feed slots were finalized more than once.",
            repeated_slots=repeat_labels[:100],
        )
        self.add(
            "closed_at_utc_stability",
            PASS if not changed_closed else FAIL,
            "Repeated finalizations preserve closed_at_utc."
            if not changed_closed
            else "Repeated finalizations contain different closed_at_utc values.",
            differences=changed_closed[:100],
        )
        self.add(
            "finalization_evidence_hash_stability",
            PASS if not changed_hash else FAIL,
            "Repeated finalizations preserve evidence hashes."
            if not changed_hash
            else "Repeated finalizations contain different evidence hashes.",
            differences=changed_hash[:100],
        )
        # Runtime 22e06 predates commit 48cfa0a, which binds closed_at_utc to
        # the frozen observation end. Any retry/recovery/repeated finalization
        # therefore exercises the known unstable-evidence path.
        exposed = bool(scheduler_retries or finalizer_retry_events or recovery or repeated)
        self.add(
            "restart_idempotency_path",
            FAIL if exposed else PASS,
            "The known restart-sensitive finalizer path was not exercised."
            if not exposed
            else "The runtime entered a retry/recovery/re-finalization path affected by the known idempotency defect.",
            runtime_commit=self.expected_runtime_commit,
            applied_fix_commit="48cfa0aa21327daa420638d275a3ca5314f6ad49",
            fix_in_runtime=False,
            exposed=exposed,
        )

    def _report(self) -> dict[str, Any]:
        statuses = {check.status for check in self.checks}
        overall = FAIL if FAIL in statuses else (NOT_VERIFIABLE if NOT_VERIFIABLE in statuses else PASS)
        identity = None
        try:
            identity = self.bundle.json("identity")
        except Exception:
            pass
        return {
            "schema_version": 1,
            "overall_status": overall,
            "epoch": _first_text(identity or {}, "epoch", "collector_epoch"),
            "run_id": _first_text(identity or {}, "run_id", "collector_run_id"),
            "runtime_commit_expected": self.expected_runtime_commit,
            "runtime_tree_expected": self.expected_runtime_tree,
            "checks": [asdict(check) for check in self.checks],
        }


def _receipt_slot(
    path: Path,
    root: Path,
    data: Mapping[str, Any],
    cohorts: Sequence[str],
) -> tuple[str, str] | None:
    cohort = _first_text(data, "cohort", "cohort_utc", "hour")
    exchange = _first_text(data, "exchange")
    stream = _first_text(data, "stream")
    market = _first_text(data, "market")
    parts = path.relative_to(root).parts
    if "coverage" in parts:
        index = parts.index("coverage")
        if len(parts) >= index + 5:
            cohort = cohort or parts[index + 1]
            exchange = exchange or parts[index + 2]
            stream = stream or parts[index + 3]
            market = market or parts[index + 4].split(".coverage", 1)[0]
    if (
        not isinstance(cohort, str)
        or not cohort
        or cohort not in cohorts
        or not isinstance(exchange, str)
        or not exchange
        or not isinstance(stream, str)
        or not stream
        or not isinstance(market, str)
        or not market
    ):
        return None
    return cohort, f"{exchange}/{stream}/{market}"


def _normalize_feeds(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    feeds: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts = [part.strip() for part in item.split("/")]
        elif isinstance(item, Mapping):
            parts = [item.get("exchange"), item.get("stream"), item.get("market")]
        else:
            return None
        if len(parts) != 3:
            return None
        exchange, stream, market = parts
        if not all(isinstance(part, str) and part for part in (exchange, stream, market)):
            return None
        feeds.append(f"{exchange}/{stream}/{market}")
    return feeds if len(feeds) == len(set(feeds)) else None


def _receipt_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in root.rglob("*.json"):
        if "receipt" not in path.name.lower() and not path.name.startswith("cohort_"):
            continue
        if path.name.startswith("cohort_") and not path.name.endswith("_finalized.json"):
            continue
        files[path.relative_to(root).as_posix()] = path
    return files


def _normalize_receipt_path(value: str | None) -> str | None:
    if not value:
        return None
    normalized = Path(value).as_posix().lstrip("./")
    for prefix in ("terminal/archive-receipts/", "archive-receipts/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
    return normalized


def _sum_metric(mapping: Mapping[str, Any], key: str) -> int | None:
    values: list[int] = []
    for item in mapping.values():
        if not isinstance(item, dict):
            return None
        value = _first_integer(item, key)
        if value is None:
            return None
        values.append(value)
    return sum(values) if values else None


def _first_text(mapping: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _first_integer(mapping: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _integer_including_negative(mapping: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _first_number(
    mapping: Mapping[str, Any],
    *keys: str,
    nested: tuple[str, ...] | None = None,
) -> float | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    if nested:
        node: Any = mapping
        for key in nested:
            node = node.get(key) if isinstance(node, Mapping) else None
        if isinstance(node, (int, float)) and not isinstance(node, bool):
            return float(node)
    return None


def _first_bool(
    mapping: Mapping[str, Any],
    *keys: str,
    nested: tuple[str, ...] | None = None,
) -> bool | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, bool):
            return value
    if nested:
        node: Any = mapping
        for key in nested:
            node = node.get(key) if isinstance(node, Mapping) else None
        return node if isinstance(node, bool) else None
    return None


def _first_string_list(mapping: Mapping[str, Any], *keys: str) -> list[str] | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
            return value
    return None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_aware_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = _read_json(path)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Fresh 30H-v3 Terminal Audit",
        "",
        f"**Overall:** {report['overall_status']}",
        f"**Epoch:** {report.get('epoch') or 'NOT_VERIFIABLE'}",
        f"**Run ID:** {report.get('run_id') or 'NOT_VERIFIABLE'}",
        f"**Expected runtime commit:** {report['runtime_commit_expected']}",
        f"**Expected runtime tree:** {report['runtime_tree_expected']}",
        "",
        "| Check | Status | Result |",
        "|---|---|---|",
    ]
    for check in report["checks"]:
        summary = str(check["summary"]).replace("|", "\\|")
        lines.append(f"| {check['name']} | **{check['status']}** | {summary} |")
    lines.append("")
    lines.append("This report is derived from a local evidence export. The input evidence was not modified.")
    return "\n".join(lines) + "\n"


def _write_outputs(report: Mapping[str, Any], output_dir: Path, evidence_dir: Path) -> tuple[Path, Path]:
    output = output_dir.resolve()
    evidence = evidence_dir.resolve()
    if output == evidence or output.is_relative_to(evidence):
        raise ValueError("output directory must be outside the evidence directory")
    epoch = report.get("epoch") or "unknown-epoch"
    safe_epoch = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(epoch))
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    target = output / f"fresh-30h-v3-{safe_epoch}-{run_stamp}"
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "terminal-audit.json"
    md_path = target / "terminal-audit.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit a locally exported Fresh 30H-v3 evidence bundle without AWS or evidence writes."
    )
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/fresh-30h-v3-terminal-audits"))
    parser.add_argument("--expected-runtime-commit", default=EXPECTED_RUNTIME_COMMIT)
    parser.add_argument("--expected-runtime-tree", default=EXPECTED_RUNTIME_TREE)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        auditor = Fresh30HTerminalAuditor(
            args.evidence_dir,
            expected_runtime_commit=args.expected_runtime_commit,
            expected_runtime_tree=args.expected_runtime_tree,
        )
        report = auditor.audit()
        json_path, markdown_path = _write_outputs(report, args.output_dir, args.evidence_dir)
    except (OSError, ValueError) as exc:
        print(f"terminal audit could not run: {exc}", file=sys.stderr)
        return 2
    print(
        f"OVERALL_STATUS={report['overall_status']}\n"
        f"JSON={json_path}\n"
        f"REPORT={markdown_path}"
    )
    return {PASS: 0, FAIL: 1, NOT_VERIFIABLE: 2}[report["overall_status"]]


if __name__ == "__main__":
    raise SystemExit(main())
