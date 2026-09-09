"""Unit tests for ClosedHourArchiveScheduler and 72H unattended archive supervision.

Follows TDD. Hardened against:
1. Active partition archive attempts (never archive active partition)
2. Grace period violations (<600s rejected, >=600s accepted)
3. Multiple pending hours processed oldest-first
4. Concurrency limits (concurrency=1)
5. Idempotent repeat runs without duplicating completed hours
6. Failed full scans surfaced and not marked complete
7. Fail-closed on ownership violations
8. Final active partial hour remaining RAW only
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import pwd
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from bithumb_coin_trader.archive_cohort import ArchiveCohortId
from bithumb_coin_trader.archive_scheduler import (
    ArchiveSchedulerConfig,
    ClosedHourArchiveScheduler,
    EligibleHour,
)
from bithumb_coin_trader.bounded_supervisor import BoundedSupervisor, SupervisorConfig


def current_user_name() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


class ArchiveSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.tmp_dir.name)
        self.raw_root = self.base_dir / "raw"
        self.manifest_root = self.base_dir / "manifests"
        self.compressed_root = self.base_dir / "compressed"
        self.receipt_root = self.base_dir / "archive-receipts"
        self.metrics_path = self.base_dir / "collector_metrics.json"

        for d in (self.raw_root, self.manifest_root, self.compressed_root, self.receipt_root):
            d.mkdir(parents=True, exist_ok=True)

        self.epoch = "aws-72h-soak-test"
        self.run_id = "aws-72h-soak-run-test"

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _create_raw_partition(self, market: str, date_str: str, hour_str: str, records: int = 5) -> Path:
        partition_dir = self.raw_root / "bithumb" / "orderbook"
        partition_dir.mkdir(parents=True, exist_ok=True)
        file_path = partition_dir / f"{market}_{date_str}_{hour_str}.jsonl"
        lines = [json.dumps({"record": i, "market": market, "seq": i}) + "\n" for i in range(records)]
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

    def _config(self, **kwargs) -> ArchiveSchedulerConfig:
        defaults = dict(
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
            scan_runner_mode="none",  # synchronous or simulated in tests
            run_full_scan=False,
            disk_critical_percent=99.0,
        )
        defaults.update(kwargs)
        return ArchiveSchedulerConfig(**defaults)

    def test_scheduler_identifies_first_eligible_closed_hour(self) -> None:
        # Create hour 05 partition on 2026-09-04
        p05 = self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        # Hour 06 partition (currently active)
        p06 = self._create_raw_partition("BTC_KRW", "2026-09-04", "06")

        # Active path is p06
        self._write_metrics([str(p06)])

        # At 06:10:00 UTC (06:00:00 + 600s), hour 05 is closed + passed grace
        test_now = datetime(2026, 9, 4, 6, 10, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: test_now)

        eligible = scheduler.discover_eligible_hours()
        self.assertEqual(len(eligible), 1)
        self.assertEqual(eligible[0].date_str, "2026-09-04")
        self.assertEqual(eligible[0].hour_str, "05")
        self.assertEqual(len(eligible[0].files), 1)
        self.assertEqual(eligible[0].files[0], p05)

    def test_scheduler_active_hour_exclusion(self) -> None:
        # p05 is closed, p06 is active
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        p06 = self._create_raw_partition("BTC_KRW", "2026-09-04", "06")

        self._write_metrics([str(p06)])

        # Even at 07:15:00 UTC, if p06 is still in active_partition_files, it MUST NOT be eligible
        test_now = datetime(2026, 9, 4, 7, 15, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: test_now)

        eligible = scheduler.discover_eligible_hours()
        hour_strs = [e.hour_str for e in eligible]
        self.assertIn("05", hour_strs)
        self.assertNotIn("06", hour_strs)

    def test_scheduler_grace_under_600_rejects(self) -> None:
        # Hour 05 partition closes at 06:00:00 UTC
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._write_metrics([])

        # At 06:09:59 UTC (599s after close) -> under 600s grace -> REJECT
        test_now = datetime(2026, 9, 4, 6, 9, 59, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: test_now)

        eligible = scheduler.discover_eligible_hours()
        self.assertEqual(len(eligible), 0)

    def test_scheduler_grace_at_or_over_600_accepts(self) -> None:
        # Hour 05 partition closes at 06:00:00 UTC
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._write_metrics([])

        # At exactly 06:10:00 UTC (600s after close) -> ACCEPT
        test_now = datetime(2026, 9, 4, 6, 10, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: test_now)

        eligible = scheduler.discover_eligible_hours()
        self.assertEqual(len(eligible), 1)
        self.assertEqual(eligible[0].hour_str, "05")

    def test_scheduler_multiple_pending_hours_oldest_first(self) -> None:
        # Hours 03, 04, 05 exist
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._create_raw_partition("BTC_KRW", "2026-09-04", "03")
        self._create_raw_partition("BTC_KRW", "2026-09-04", "04")
        self._write_metrics([])

        # At 06:30:00 UTC, all three are closed and past grace
        test_now = datetime(2026, 9, 4, 6, 30, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: test_now)

        eligible = scheduler.discover_eligible_hours()
        # Must be ordered oldest-first: 03, 04, 05
        self.assertEqual([e.hour_str for e in eligible], ["03", "04", "05"])

    def test_scheduler_duplicate_invocation_is_idempotent(self) -> None:
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._write_metrics([])

        test_now = datetime(2026, 9, 4, 6, 15, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: test_now)

        # Run first pass
        res1 = scheduler.run_once()
        self.assertEqual(res1["processed_cohort"], "2026-09-04_05")
        self.assertEqual(res1["status"], "PASS")

        # Run second pass immediately: hour 05 is already completed, no pending hours
        res2 = scheduler.run_once()
        self.assertIsNone(res2["processed_cohort"])
        self.assertEqual(res2["status"], "IDLE")

    def test_scheduler_archive_concurrency_single_instance(self) -> None:
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._write_metrics([])

        test_now = datetime(2026, 9, 4, 6, 15, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: test_now)

        # Hold orchestrator lock externally
        from scripts.orchestrate_closed_hour_archive import orchestrator_lock
        lock_path = self.receipt_root / ".orchestrator.lock"

        with orchestrator_lock(lock_path, expected_owner=current_user_name()):
            # Scheduler should detect lock and safely back off without failing
            res = scheduler.run_once()
            self.assertEqual(res["status"], "LOCKED")
            self.assertEqual(res["pending_cohorts"], ["2026-09-04_05"])

    def test_scheduler_full_scan_running_leaves_later_hour_pending(self) -> None:
        import fcntl
        from scripts.orchestrate_closed_hour_archive import FULL_SCAN_GLOBAL_LOCK_NAME

        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._create_raw_partition("BTC_KRW", "2026-09-04", "06")
        self._write_metrics([])

        # Simulate full scan lock held for hour 05
        scan_lock = self.receipt_root / FULL_SCAN_GLOBAL_LOCK_NAME
        scan_lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(scan_lock), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            test_now = datetime(2026, 9, 4, 7, 15, 0, tzinfo=timezone.utc)
            scheduler = ClosedHourArchiveScheduler(
                self._config(run_full_scan=True),
                now_fn=lambda: test_now,
            )
            # Both 05 and 06 eligible, but full scan lock held
            # Scheduler should not launch a concurrent full scan
            is_running = scheduler.is_full_scan_running()
            self.assertTrue(is_running)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_scheduler_completed_hour_not_reprocessed(self) -> None:
        p05 = self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._write_metrics([])

        # Mark receipt as CLEANUP_ELIGIBLE
        rec_path = self.receipt_root / f"{p05.name}.archive-receipt.json"
        rec_path.write_text(json.dumps({"cohort": "2026-09-04_05", "state": "CLEANUP_ELIGIBLE", "cleanup_eligible": True}), encoding="utf-8")

        test_now = datetime(2026, 9, 4, 6, 15, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: test_now)

        eligible = scheduler.discover_eligible_hours()
        # Hour 05 should not be in eligible list because it is completed
        self.assertEqual(len(eligible), 0)

    def test_scheduler_failed_scan_recorded_not_silently_complete(self) -> None:
        p05 = self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._write_metrics([])

        # Partition receipt exists
        rec_path = self.receipt_root / f"{p05.name}.archive-receipt.json"
        rec_path.write_text(json.dumps({"cohort": "2026-09-04_05", "state": "CLEANUP_ELIGIBLE", "cleanup_eligible": True}), encoding="utf-8")

        # But full scan report is FAIL!
        cohort = ArchiveCohortId("2026-09-04", "05")
        report_path = self.receipt_root / f"full_scan_{cohort.key}_report.json"
        report_path.write_text(
            json.dumps({"status": "FAIL", "cohort": cohort.key, "error": "corrupt record"}),
            encoding="utf-8",
        )

        test_now = datetime(2026, 9, 4, 6, 15, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(run_full_scan=True), now_fn=lambda: test_now)

        self.assertTrue(scheduler.has_cohort_failed(cohort))
        self.assertFalse(scheduler.is_cohort_completed(cohort))

    def test_scheduler_ownership_violation_fail_closed(self) -> None:
        self._create_raw_partition("BTC_KRW", "2026-09-04", "05")
        self._write_metrics([])

        # Pass an expected_owner that cannot possibly match the file owner
        scheduler = ClosedHourArchiveScheduler(
            self._config(expected_owner="nonexistent_user_99999"),
            now_fn=lambda: datetime(2026, 9, 4, 6, 15, 0, tzinfo=timezone.utc),
        )

        from bithumb_coin_trader.pre_soak_archive import OwnershipViolationError
        with self.assertRaises(OwnershipViolationError):
            scheduler.run_once()

    def test_scheduler_final_partial_hour_remains_raw_only(self) -> None:
        # Soak ends at 06:40:00 UTC. Hour 06 is partial (ran from 06:00 to 06:40).
        p06 = self._create_raw_partition("BTC_KRW", "2026-09-04", "06")
        # Collector marks p06 active during run, then at shutdown clears active_partition_files:
        self._write_metrics([])  # final metrics: active_partition_files=[]

        # At collector end 06:40:00 UTC:
        # Hour 06 has NOT closed yet (it closes at 07:00:00 UTC + 600s = 07:10:00 UTC)
        end_time = datetime(2026, 9, 4, 6, 40, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(), now_fn=lambda: end_time)

        eligible = scheduler.discover_eligible_hours()
        # Hour 06 MUST NOT be eligible for archive! It remains RAW only!
        self.assertEqual(len(eligible), 0)
        self.assertTrue(p06.exists())
        self.assertFalse((self.receipt_root / f"{p06.name}.archive-receipt.json").exists())

    def test_same_hour_on_later_date_does_not_reopen_completed_cohort(self) -> None:
        day1 = self._create_raw_partition("BTC_KRW", "2026-09-05", "05")
        self._write_metrics([])
        (self.receipt_root / f"{day1.name}.archive-receipt.json").write_text(
            json.dumps({"cohort": "2026-09-05_05", "state": "CLEANUP_ELIGIBLE", "cleanup_eligible": True}),
            encoding="utf-8",
        )
        scheduler = ClosedHourArchiveScheduler(self._config())
        cohort1 = ArchiveCohortId("2026-09-05", "05")

        self.assertTrue(scheduler.is_cohort_completed(cohort1))

        self._create_raw_partition("BTC_KRW", "2026-09-06", "05")

        self.assertTrue(scheduler.is_cohort_completed(cohort1))
        self.assertFalse(scheduler.is_cohort_completed(ArchiveCohortId("2026-09-06", "05")))

    def test_three_day_same_hour_selects_oldest_exact_cohort(self) -> None:
        for date_str in ("2026-09-05", "2026-09-06", "2026-09-07"):
            self._create_raw_partition("BTC_KRW", date_str, "05")
        self._write_metrics([])
        scheduler = ClosedHourArchiveScheduler(
            self._config(),
            now_fn=lambda: datetime(2026, 9, 8, 7, 0, tzinfo=timezone.utc),
        )

        eligible = scheduler.discover_eligible_hours()

        self.assertEqual(
            [item.cohort.key for item in eligible],
            ["2026-09-05_05", "2026-09-06_05", "2026-09-07_05"],
        )
        with patch(
            "bithumb_coin_trader.archive_scheduler.orchestrate_closed_hour_archive",
            return_value={"archive_job_failures": 0},
        ) as orchestrate:
            result = scheduler.run_once()

        self.assertEqual(result["processed_cohort"], "2026-09-05_05")
        self.assertEqual(result["pending_cohorts"], ["2026-09-06_05", "2026-09-07_05"])
        self.assertEqual(orchestrate.call_args.kwargs["target_cohort"], ArchiveCohortId("2026-09-05", "05"))

    def test_failed_scan_lookup_requires_exact_canonical_cohort(self) -> None:
        day1 = ArchiveCohortId("2026-09-05", "05")
        day2 = ArchiveCohortId("2026-09-06", "05")
        (self.receipt_root / f"full_scan_{day1.key}_report.json").write_text(
            json.dumps({"status": "FAIL", "cohort": day1.key}),
            encoding="utf-8",
        )
        scheduler = ClosedHourArchiveScheduler(self._config(run_full_scan=True))

        self.assertTrue(scheduler.has_cohort_failed(day1))
        self.assertFalse(scheduler.has_cohort_failed(day2))

    def test_wrong_date_receipt_cannot_complete_same_hour_cohort(self) -> None:
        cohort = ArchiveCohortId("2026-09-06", "05")
        partition = self._create_raw_partition("BTC_KRW", cohort.date_str, cohort.hour_str)
        self._write_metrics([])
        (self.receipt_root / f"{partition.name}.archive-receipt.json").write_text(
            json.dumps(
                {
                    "cohort": "2026-09-05_05",
                    "state": "CLEANUP_ELIGIBLE",
                    "cleanup_eligible": True,
                }
            ),
            encoding="utf-8",
        )
        scheduler = ClosedHourArchiveScheduler(self._config())

        self.assertFalse(scheduler.is_cohort_completed(cohort))

    def test_stop_wakes_poll_loop_promptly(self) -> None:
        self._write_metrics([])
        scheduler = ClosedHourArchiveScheduler(self._config(poll_interval_seconds=5.0))
        entered = threading.Event()
        original = scheduler.run_once

        def observed_run_once(*args, **kwargs):
            entered.set()
            return original(*args, **kwargs)

        scheduler.run_once = observed_run_once  # type: ignore[method-assign]
        thread = threading.Thread(target=scheduler.run_loop)
        thread.start()
        self.assertTrue(entered.wait(timeout=1.0))

        started = time.monotonic()
        scheduler.stop()
        thread.join(timeout=1.0)

        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - started, 0.5)

    def test_stop_requested_during_discovery_prevents_new_cohort_start(self) -> None:
        partition = self._create_raw_partition("BTC_KRW", "2026-09-05", "05")
        self._write_metrics([])
        scheduler = ClosedHourArchiveScheduler(
            self._config(),
            now_fn=lambda: datetime(2026, 9, 6, 7, 0, tzinfo=timezone.utc),
        )
        eligible = EligibleHour(
            cohort=ArchiveCohortId("2026-09-05", "05"),
            files=[partition],
            closed_at=datetime(2026, 9, 5, 6, 0, tzinfo=timezone.utc),
        )

        def stop_during_discovery(*_args, **_kwargs):
            scheduler.stop()
            return [eligible]

        with patch.object(scheduler, "discover_eligible_hours", side_effect=stop_during_discovery), patch(
            "bithumb_coin_trader.archive_scheduler.orchestrate_closed_hour_archive"
        ) as orchestrate:
            result = scheduler.run_once()

        self.assertEqual(result["status"], "STOPPED")
        orchestrate.assert_not_called()

    def test_idle_scheduler_stop_exits_cleanly(self) -> None:
        self._write_metrics([])
        scheduler = ClosedHourArchiveScheduler(self._config(poll_interval_seconds=10.0))
        scheduler.stop()
        started = time.monotonic()
        scheduler.run_loop()
        self.assertLess(time.monotonic() - started, 0.2)

    def test_stop_during_in_progress_operation_does_not_falsely_mark_cohort_complete(self) -> None:
        partition = self._create_raw_partition("BTC_KRW", "2026-09-05", "05")
        self._write_metrics([])
        cohort = ArchiveCohortId("2026-09-05", "05")
        scheduler = ClosedHourArchiveScheduler(
            self._config(),
            now_fn=lambda: datetime(2026, 9, 6, 7, 0, tzinfo=timezone.utc),
        )

        def interrupt_during_orchestrate(*_args, **_kwargs):
            scheduler.stop()
            raise RuntimeError("simulated interruption during archive transaction")

        with patch(
            "bithumb_coin_trader.archive_scheduler.orchestrate_closed_hour_archive",
            side_effect=interrupt_during_orchestrate,
        ):
            result = scheduler.run_once()

        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["processed_cohort"], "2026-09-05_05")
        self.assertFalse(scheduler.is_cohort_completed(cohort))


if __name__ == "__main__":
    unittest.main()
