"""Tests for archiver health instrumentation bridge.

Validates:
1. Scheduler writes archiver health snapshot after each run_once() cycle.
2. Observer reads archiver health sidecar and merges into evaluated archiver fields.
3. PASS/FAIL/IDLE/ERROR/LOCKED/STOPPED status mapping to ComponentHealthState.
4. Stale archiver detection (>5 minute old sidecar, missing sidecar).
5. Atomic write guarantees (sidecar always valid JSON).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
SCRIPTS_DIR = ROOT / "scripts"
for p in (SRC_DIR, SCRIPTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from bithumb_coin_trader.archive_cohort import ArchiveCohortId
from bithumb_coin_trader.archive_scheduler import (
    ArchiveSchedulerConfig,
    ClosedHourArchiveScheduler,
)
from bithumb_coin_trader.collector_state_model import (
    ArchiverHealth,
    ComponentHealthState,
    RuntimeHealthSnapshot,
    read_health_snapshot,
    write_health_snapshot_atomic,
)
from bithumb_coin_trader.runtime_observer import (
    ObserverConfig,
    RuntimeObserver,
)


def current_user_name() -> str:
    import pwd
    return pwd.getpwuid(os.getuid()).pw_name


class ArchiverHealthWriteTests(unittest.TestCase):
    """Test that run_once() writes archiver health snapshot to sidecar JSON."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.tmp_dir.name)
        self.raw_root = self.base_dir / "raw"
        self.manifest_root = self.base_dir / "manifests"
        self.compressed_root = self.base_dir / "compressed"
        self.receipt_root = self.base_dir / "archive-receipts"
        self.metrics_path = self.base_dir / "collector_metrics.json"
        self.health_path = self.base_dir / "health" / "archiver_latest.json"

        for d in (self.raw_root, self.manifest_root, self.compressed_root, self.receipt_root):
            d.mkdir(parents=True, exist_ok=True)

        self.epoch = "test-archiver-health"
        self.run_id = "test-run-001"

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _config(self, **kwargs: Any) -> ArchiveSchedulerConfig:
        defaults: dict[str, Any] = dict(
            epoch=self.epoch,
            run_id=self.run_id,
            base_dir=self.base_dir,
            raw_root=self.raw_root,
            manifest_root=self.manifest_root,
            compressed_root=self.compressed_root,
            receipt_root=self.receipt_root,
            metrics_path=self.metrics_path,
            poll_interval_seconds=0.1,
            grace_seconds=600,
            expected_owner=current_user_name(),
            store_type="file",
            scan_runner_mode="none",
            run_full_scan=False,
            disk_critical_percent=99.0,
            health_path=self.health_path,
        )
        defaults.update(kwargs)
        return ArchiveSchedulerConfig(**defaults)

    def _create_raw_partition(self, market: str, date_str: str, hour_str: str) -> Path:
        partition_dir = self.raw_root / "bithumb" / "orderbook"
        partition_dir.mkdir(parents=True, exist_ok=True)
        file_path = partition_dir / f"{market}_{date_str}_{hour_str}.jsonl"
        lines = [json.dumps({"record": i, "market": market}) + "\n" for i in range(5)]
        file_path.write_text("".join(lines), encoding="utf-8")
        return file_path

    def _write_metrics(self, active_paths: list[str]) -> None:
        payload = {
            "schema_version": 1,
            "collector_run_id": self.run_id,
            "process_id": os.getpid(),
            "written_at": datetime.now(timezone.utc).isoformat(),
            "active_partition_files": active_paths,
        }
        self.metrics_path.write_text(json.dumps(payload), encoding="utf-8")

    def test_pass_writes_healthy_archiver_snapshot(self) -> None:
        """PASS status writes HEALTHY archiver health with last_closed_cohort populated."""
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._write_metrics([])

        test_now = datetime(2026, 9, 4, 6, 15, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: test_now)

        result = scheduler.run_once()
        self.assertEqual(result["status"], "PASS")

        # Verify sidecar written
        self.assertTrue(self.health_path.exists())
        snapshot = read_health_snapshot(self.health_path)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None

        self.assertEqual(snapshot.archiver.status, ComponentHealthState.HEALTHY.value)
        self.assertEqual(snapshot.archiver.last_closed_cohort, "2026-09-04_05")
        self.assertIsNotNone(snapshot.archiver.last_compression)
        self.assertIsNotNone(snapshot.archiver.last_receipt)
        self.assertEqual(snapshot.archiver.archive_errors, 0)
        self.assertEqual(snapshot.archiver.upload_failures, 0)
        self.assertEqual(snapshot.archiver.archive_queue_depth, 0)
        self.assertEqual(snapshot.epoch, self.epoch)
        self.assertEqual(snapshot.run_id, self.run_id)

    def test_idle_no_pending_writes_healthy(self) -> None:
        """IDLE with no pending cohorts writes HEALTHY."""
        self._write_metrics([])

        test_now = datetime(2026, 9, 4, 6, 15, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: test_now)

        result = scheduler.run_once()
        self.assertEqual(result["status"], "IDLE")

        snapshot = read_health_snapshot(self.health_path)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.archiver.status, ComponentHealthState.HEALTHY.value)
        self.assertEqual(snapshot.archiver.archive_queue_depth, 0)

    def test_fail_writes_degraded_with_errors(self) -> None:
        """FAIL status writes DEGRADED archiver health with archive_errors and upload_failures."""
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._write_metrics([])

        test_now = datetime(2026, 9, 4, 6, 15, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: test_now)

        with patch(
            "bithumb_coin_trader.archive_scheduler.orchestrate_closed_hour_archive",
            return_value={"archive_job_failures": 3},
        ):
            result = scheduler.run_once()

        self.assertEqual(result["status"], "FAIL")

        snapshot = read_health_snapshot(self.health_path)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.archiver.status, ComponentHealthState.DEGRADED.value)
        self.assertEqual(snapshot.archiver.archive_errors, 1)
        self.assertEqual(snapshot.archiver.upload_failures, 3)

    def test_error_writes_failed_status(self) -> None:
        """ERROR status writes FAILED archiver health with archive_errors=1."""
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._write_metrics([])

        test_now = datetime(2026, 9, 4, 6, 15, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: test_now)

        with patch(
            "bithumb_coin_trader.archive_scheduler.orchestrate_closed_hour_archive",
            side_effect=RuntimeError("catastrophic failure"),
        ):
            result = scheduler.run_once()

        self.assertEqual(result["status"], "ERROR")

        snapshot = read_health_snapshot(self.health_path)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.archiver.status, ComponentHealthState.FAILED.value)
        self.assertEqual(snapshot.archiver.archive_errors, 1)
        self.assertEqual(snapshot.archiver.archive_queue_depth, 1)  # pending_cohort in result

    def test_stopped_writes_degraded(self) -> None:
        """STOPPED status writes DEGRADED archiver health."""
        self._write_metrics([])
        scheduler = ClosedHourArchiveScheduler(self._config())
        scheduler.stop()

        result = scheduler.run_once()
        self.assertEqual(result["status"], "STOPPED")

        snapshot = read_health_snapshot(self.health_path)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.archiver.status, ComponentHealthState.DEGRADED.value)

    def test_locked_writes_degraded_with_queue_depth(self) -> None:
        """LOCKED status writes DEGRADED with queue depth from pending cohorts."""
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._create_raw_partition("BTC_KRW", "2026-09-04", "06")
        self._write_metrics([])

        from scripts.orchestrate_closed_hour_archive import (
            ARCHIVE_ORCHESTRATOR_LOCK_NAME,
            orchestrator_lock,
        )
        lock_path = self.receipt_root / ARCHIVE_ORCHESTRATOR_LOCK_NAME

        test_now = datetime(2026, 9, 4, 7, 15, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: test_now)

        with orchestrator_lock(lock_path, expected_owner=current_user_name()):
            result = scheduler.run_once()

        self.assertEqual(result["status"], "LOCKED")

        snapshot = read_health_snapshot(self.health_path)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.archiver.status, ComponentHealthState.DEGRADED.value)
        self.assertGreaterEqual(snapshot.archiver.archive_queue_depth, 1)

    def test_s3_store_records_last_s3_put(self) -> None:
        """When store_type='s3' with s3_bucket, PASS records last_s3_put."""
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._write_metrics([])

        test_now = datetime(2026, 9, 4, 6, 15, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(
            self._config(store_type="s3", s3_bucket="my-bucket"),
            now_fn=lambda: test_now,
        )

        with patch(
            "bithumb_coin_trader.archive_scheduler.orchestrate_closed_hour_archive",
            return_value={"archive_job_failures": 0},
        ):
            result = scheduler.run_once()

        self.assertEqual(result["status"], "PASS")

        snapshot = read_health_snapshot(self.health_path)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertIsNotNone(snapshot.archiver.last_s3_put)

    def test_file_store_no_s3_put_timestamp(self) -> None:
        """When store_type='file', PASS does NOT set last_s3_put."""
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._write_metrics([])

        test_now = datetime(2026, 9, 4, 6, 15, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(
            self._config(store_type="file"),
            now_fn=lambda: test_now,
        )

        result = scheduler.run_once()
        self.assertEqual(result["status"], "PASS")

        snapshot = read_health_snapshot(self.health_path)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertIsNone(snapshot.archiver.last_s3_put)

    def test_queue_depth_matches_pending_cohorts(self) -> None:
        """archive_queue_depth equals len(pending_cohorts) from run_once result."""
        self._create_raw_partition("BTC_KRW", "2026-09-04", "03")
        self._create_raw_partition("BTC_KRW", "2026-09-04", "04")
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._write_metrics([])

        test_now = datetime(2026, 9, 4, 6, 30, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: test_now)

        result = scheduler.run_once()
        self.assertEqual(result["status"], "PASS")
        # After processing 03, remaining are 04 and 05
        self.assertEqual(len(result["pending_cohorts"]), 2)

        snapshot = read_health_snapshot(self.health_path)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.archiver.archive_queue_depth, 2)

    def test_health_snapshot_is_always_valid_json(self) -> None:
        """Even after an error, the sidecar must be valid JSON."""
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._write_metrics([])

        test_now = datetime(2026, 9, 4, 6, 15, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: test_now)

        with patch(
            "bithumb_coin_trader.archive_scheduler.orchestrate_closed_hour_archive",
            side_effect=RuntimeError("boom"),
        ):
            scheduler.run_once()

        # Must be parseable as valid JSON
        raw_text = self.health_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
        self.assertIsInstance(data, dict)
        self.assertIn("archiver", data)


class ArchiverHealthObserverReadTests(unittest.TestCase):
    """Test that observer reads and evaluates archiver health from sidecar."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        (self.data_dir / "health").mkdir(parents=True, exist_ok=True)
        self.now = datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_archiver_sidecar(self, archiver: ArchiverHealth, observed_at: str | None = None) -> None:
        snapshot = RuntimeHealthSnapshot(
            epoch="obs_epoch",
            run_id="obs_run",
            observed_at=observed_at or self.now.isoformat(),
            archiver=archiver,
        )
        write_health_snapshot_atomic(
            self.data_dir / "health" / "archiver_latest.json",
            snapshot,
        )

    def test_observer_merges_healthy_archiver_from_sidecar(self) -> None:
        """Observer reads HEALTHY archiver sidecar and populates evaluated.archiver fields."""
        self._write_archiver_sidecar(ArchiverHealth(
            status=ComponentHealthState.HEALTHY.value,
            archive_queue_depth=0,
            last_closed_cohort="2026-09-04_05",
            last_compression=self.now.isoformat(),
            last_receipt=self.now.isoformat(),
        ))

        config = ObserverConfig(data_dir=self.data_dir, epoch="obs_epoch", run_id="obs_run")
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(evaluated.archiver.status, ComponentHealthState.HEALTHY.value)
        self.assertEqual(evaluated.archiver.last_closed_cohort, "2026-09-04_05")
        self.assertIsNotNone(evaluated.archiver.last_compression)
        self.assertIsNotNone(evaluated.archiver.last_receipt)
        self.assertEqual(evaluated.archiver.archive_queue_depth, 0)
        self.assertEqual(evaluated.archiver.archive_errors, 0)

    def test_observer_merges_degraded_archiver_from_sidecar(self) -> None:
        """Observer reads DEGRADED archiver sidecar with failures."""
        self._write_archiver_sidecar(ArchiverHealth(
            status=ComponentHealthState.DEGRADED.value,
            archive_errors=1,
            upload_failures=3,
            archive_queue_depth=5,
        ))

        config = ObserverConfig(data_dir=self.data_dir, epoch="obs_epoch", run_id="obs_run")
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(evaluated.archiver.status, ComponentHealthState.DEGRADED.value)
        self.assertEqual(evaluated.archiver.archive_errors, 1)
        self.assertEqual(evaluated.archiver.upload_failures, 3)
        self.assertEqual(evaluated.archiver.archive_queue_depth, 5)

    def test_observer_stale_when_sidecar_missing(self) -> None:
        """When archiver_latest.json is missing, observer marks archiver as STALE."""
        # Do NOT write archiver sidecar
        config = ObserverConfig(data_dir=self.data_dir, epoch="obs_epoch", run_id="obs_run")
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(evaluated.archiver.status, ComponentHealthState.STALE.value)

    def test_observer_stale_when_sidecar_older_than_5_minutes(self) -> None:
        """When archiver sidecar observed_at > 5 minutes ago, observer marks STALE."""
        stale_time = (self.now - timedelta(minutes=6)).isoformat()
        self._write_archiver_sidecar(
            ArchiverHealth(
                status=ComponentHealthState.HEALTHY.value,
                last_closed_cohort="2026-09-04_05",
                archive_queue_depth=2,
            ),
            observed_at=stale_time,
        )

        config = ObserverConfig(data_dir=self.data_dir, epoch="obs_epoch", run_id="obs_run")
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(evaluated.archiver.status, ComponentHealthState.STALE.value)
        # Queue depth is still preserved from stale sidecar
        self.assertEqual(evaluated.archiver.archive_queue_depth, 2)

    def test_observer_fresh_when_sidecar_within_5_minutes(self) -> None:
        """When archiver sidecar observed_at <= 5 minutes ago, observer uses sidecar values."""
        fresh_time = (self.now - timedelta(minutes=4, seconds=59)).isoformat()
        self._write_archiver_sidecar(
            ArchiverHealth(
                status=ComponentHealthState.HEALTHY.value,
                last_closed_cohort="2026-09-04_10",
                archive_queue_depth=1,
            ),
            observed_at=fresh_time,
        )

        config = ObserverConfig(data_dir=self.data_dir, epoch="obs_epoch", run_id="obs_run")
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(evaluated.archiver.status, ComponentHealthState.HEALTHY.value)
        self.assertEqual(evaluated.archiver.last_closed_cohort, "2026-09-04_10")
        self.assertEqual(evaluated.archiver.archive_queue_depth, 1)

    def test_observer_stale_at_exact_5_minute_boundary(self) -> None:
        """At exactly 300 seconds, the archiver is still considered fresh (< 300)."""
        boundary_time = (self.now - timedelta(seconds=300)).isoformat()
        self._write_archiver_sidecar(
            ArchiverHealth(
                status=ComponentHealthState.HEALTHY.value,
                archive_queue_depth=0,
            ),
            observed_at=boundary_time,
        )

        config = ObserverConfig(data_dir=self.data_dir, epoch="obs_epoch", run_id="obs_run")
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.now)

        # age_seconds == 300, not > 300, so should be FRESH (HEALTHY)
        self.assertEqual(evaluated.archiver.status, ComponentHealthState.HEALTHY.value)

    def test_observer_stale_at_just_over_5_minutes(self) -> None:
        """At 301 seconds, the archiver should be STALE."""
        boundary_time = (self.now - timedelta(seconds=301)).isoformat()
        self._write_archiver_sidecar(
            ArchiverHealth(
                status=ComponentHealthState.HEALTHY.value,
                archive_queue_depth=0,
            ),
            observed_at=boundary_time,
        )

        config = ObserverConfig(data_dir=self.data_dir, epoch="obs_epoch", run_id="obs_run")
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(evaluated.archiver.status, ComponentHealthState.STALE.value)

    def test_observer_handles_corrupt_archiver_sidecar_gracefully(self) -> None:
        """Corrupt archiver_latest.json should be treated as missing -> STALE."""
        corrupt_path = self.data_dir / "health" / "archiver_latest.json"
        corrupt_path.write_text("NOT_JSON{{{", encoding="utf-8")

        config = ObserverConfig(data_dir=self.data_dir, epoch="obs_epoch", run_id="obs_run")
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(evaluated.archiver.status, ComponentHealthState.STALE.value)


class ArchiverHealthEndToEndTests(unittest.TestCase):
    """End-to-end: scheduler writes -> observer reads -> correct evaluation."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.tmp_dir.name)
        self.raw_root = self.base_dir / "raw"
        self.manifest_root = self.base_dir / "manifests"
        self.compressed_root = self.base_dir / "compressed"
        self.receipt_root = self.base_dir / "archive-receipts"
        self.metrics_path = self.base_dir / "collector_metrics.json"
        self.health_path = self.base_dir / "health" / "archiver_latest.json"

        for d in (self.raw_root, self.manifest_root, self.compressed_root, self.receipt_root):
            d.mkdir(parents=True, exist_ok=True)

        self.epoch = "e2e-archiver-test"
        self.run_id = "e2e-run-001"
        self.test_now = datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _config(self, **kwargs: Any) -> ArchiveSchedulerConfig:
        defaults: dict[str, Any] = dict(
            epoch=self.epoch,
            run_id=self.run_id,
            base_dir=self.base_dir,
            raw_root=self.raw_root,
            manifest_root=self.manifest_root,
            compressed_root=self.compressed_root,
            receipt_root=self.receipt_root,
            metrics_path=self.metrics_path,
            poll_interval_seconds=0.1,
            grace_seconds=600,
            expected_owner=current_user_name(),
            store_type="file",
            scan_runner_mode="none",
            run_full_scan=False,
            disk_critical_percent=99.0,
            health_path=self.health_path,
        )
        defaults.update(kwargs)
        return ArchiveSchedulerConfig(**defaults)

    def _create_raw_partition(self, market: str, date_str: str, hour_str: str) -> Path:
        partition_dir = self.raw_root / "bithumb" / "orderbook"
        partition_dir.mkdir(parents=True, exist_ok=True)
        file_path = partition_dir / f"{market}_{date_str}_{hour_str}.jsonl"
        lines = [json.dumps({"record": i, "market": market}) + "\n" for i in range(5)]
        file_path.write_text("".join(lines), encoding="utf-8")
        return file_path

    def _write_metrics(self, active_paths: list[str]) -> None:
        payload = {
            "schema_version": 1,
            "collector_run_id": self.run_id,
            "process_id": os.getpid(),
            "written_at": self.test_now.isoformat(),
            "active_partition_files": active_paths,
        }
        self.metrics_path.write_text(json.dumps(payload), encoding="utf-8")

    def test_scheduler_write_then_observer_read_pass(self) -> None:
        """Scheduler PASS -> observer reads HEALTHY archiver."""
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._write_metrics([])

        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: self.test_now)
        result = scheduler.run_once()
        self.assertEqual(result["status"], "PASS")

        # Now observer reads it
        config = ObserverConfig(data_dir=self.base_dir, epoch=self.epoch, run_id=self.run_id)
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.test_now)

        self.assertEqual(evaluated.archiver.status, ComponentHealthState.HEALTHY.value)
        self.assertEqual(evaluated.archiver.last_closed_cohort, "2026-09-04_05")
        self.assertEqual(evaluated.archiver.archive_queue_depth, 0)

    def test_scheduler_write_then_observer_read_error(self) -> None:
        """Scheduler ERROR -> observer reads FAILED archiver."""
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._write_metrics([])

        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: self.test_now)

        with patch(
            "bithumb_coin_trader.archive_scheduler.orchestrate_closed_hour_archive",
            side_effect=RuntimeError("catastrophic"),
        ):
            result = scheduler.run_once()

        self.assertEqual(result["status"], "ERROR")

        config = ObserverConfig(data_dir=self.base_dir, epoch=self.epoch, run_id=self.run_id)
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.test_now)

        self.assertEqual(evaluated.archiver.status, ComponentHealthState.FAILED.value)
        self.assertEqual(evaluated.archiver.archive_errors, 1)

    def test_scheduler_write_then_observer_stale_detection(self) -> None:
        """Observer detects stale archiver when sidecar is >5 minutes old."""
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._write_metrics([])

        # Scheduler ran 6 minutes ago
        scheduler_time = self.test_now - timedelta(minutes=6)
        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: scheduler_time)
        result = scheduler.run_once()
        self.assertEqual(result["status"], "PASS")

        # Observer runs now
        config = ObserverConfig(data_dir=self.base_dir, epoch=self.epoch, run_id=self.run_id)
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.test_now)

        # Even though scheduler passed, observer sees it as stale
        self.assertEqual(evaluated.archiver.status, ComponentHealthState.STALE.value)

    def test_full_status_mapping_matrix(self) -> None:
        """Verify all run_once() statuses map to correct ComponentHealthState."""
        test_cases = [
            ("PASS", None, ComponentHealthState.HEALTHY.value),
            ("IDLE", None, ComponentHealthState.HEALTHY.value),  # no pending
            ("ERROR", None, ComponentHealthState.FAILED.value),
            ("STOPPED", None, ComponentHealthState.DEGRADED.value),
        ]
        for run_status, _, expected_health in test_cases:
            with self.subTest(run_status=run_status):
                # Create a fresh sidecar for each subtest
                archiver = ArchiverHealth(status=expected_health)
                if run_status == "PASS":
                    archiver.last_closed_cohort = "2026-09-04_05"
                if run_status == "ERROR":
                    archiver.archive_errors = 1

                snapshot = RuntimeHealthSnapshot(
                    epoch="map_epoch",
                    run_id="map_run",
                    observed_at=self.test_now.isoformat(),
                    archiver=archiver,
                )
                health_path = self.base_dir / "health" / "archiver_latest.json"
                write_health_snapshot_atomic(health_path, snapshot)

                config = ObserverConfig(
                    data_dir=self.base_dir,
                    epoch="map_epoch",
                    run_id="map_run",
                )
                observer = RuntimeObserver(config=config)
                evaluated = observer.run_cycle(now=self.test_now)

                self.assertEqual(
                    evaluated.archiver.status,
                    expected_health,
                    f"Status {run_status} should map to {expected_health}",
                )


if __name__ == "__main__":
    unittest.main()
