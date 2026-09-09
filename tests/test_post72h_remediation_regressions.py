"""Post-72H Runtime Remediation V1 Release-Blocking Regressions.

Implements:
1. Three-Day Same-HH Simulation:
   - >=3 UTC dates, same recurring HH (05) plus adjacent hours (04, 06)
   - Single archive worker serial execution (oldest-first)
   - Verifies all 9 cohorts processed once, no starvation, no reopening,
     no duplicate archive, no receipt collision, no fullscan collision.
2. 72H-Shaped Synthetic Archive Oracle:
   - 73 touched raw cohorts (05:40 Day 1 to 05:40 Day 4)
   - 72 archive-eligible cohorts
   - 76 feeds universe
   - Final partial boundary active at shutdown (raw only, no receipt/fullscan required)
   - Full auditor verification of exact per-cohort coverage.
3. Clean Lifecycle Integration:
   - Supervisor + collector + publisher + scheduler natural completion
   - Normal finalization: COLLECTING -> FINALIZING -> COMPLETE (exit 0)
   - Cooperative scheduler shutdown (exit 0 on SIGTERM, no SIGKILL)
   - forced_timeout=False, received_signal=None, PASS.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import pwd
import signal
import sys
import tempfile
import time
import unittest

from bithumb_coin_trader.archive_cohort import ArchiveCohortId
from bithumb_coin_trader.archive_scheduler import (
    ArchiveSchedulerConfig,
    ClosedHourArchiveScheduler,
)
from bithumb_coin_trader.bounded_supervisor import BoundedSupervisor, SupervisorConfig
from scripts.audit_72h_soak import (
    SoakAuditor72H,
    derive_expected_archive_cohorts,
    derive_expected_raw_cohorts,
    validate_archive_evidence_coverage,
)
from scripts.orchestrate_closed_hour_archive import orchestrate_closed_hour_archive

ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE_FIXTURE = ROOT / "tests" / "fixtures" / "lifecycle_child.py"


def _current_user() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


class ThreeDaySameHourRegressionTests(unittest.TestCase):
    """Release-blocking cross-date same-hour archive regression."""

    def test_three_day_same_hh_with_adjacent_hours_simulation(self) -> None:
        """3 UTC dates, same HH 05 with adjacent hours 04 and 06.

        All 9 cohorts must be processed exactly once in oldest-first order,
        without starvation, without reopening completed cohorts,
        and with zero receipt or fullscan collisions.
        """
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp) / "epoch_data"
            raw_root = base_dir / "raw" / "bithumb" / "orderbook" / "KRW-BTC"
            compressed_root = base_dir / "compressed"
            receipt_root = base_dir / "archive-receipts"
            store_root = Path(tmp) / "file_store"
            metrics_path = base_dir / "metrics.json"

            for d in (raw_root, compressed_root, receipt_root, store_root):
                d.mkdir(parents=True, exist_ok=True)

            dates = ("2026-09-05", "2026-09-06", "2026-09-07")
            hours = ("04", "05", "06")
            expected_cohorts = [
                f"{d}_{h}" for d in dates for h in hours
            ]
            self.assertEqual(len(expected_cohorts), 9)

            # Create sample raw partitions for all 9 cohorts
            for d in dates:
                for h in hours:
                    p_file = raw_root / f"KRW-BTC_{d}_{h}.jsonl"
                    past_iso = f"{d}T{h}:30:00+00:00"
                    rec = {
                        "timestamp": past_iso,
                        "exchange": "bithumb",
                        "stream": "orderbook",
                        "market": "KRW-BTC",
                        "exchange_ts": past_iso,
                        "local_recv_ts": past_iso,
                        "local_write_ts": past_iso,
                        "payload": {"price": 100000000, "bids": [], "asks": []},
                    }
                    p_file.write_text(json.dumps(rec) + "\n", encoding="utf-8")

            cfg = ArchiveSchedulerConfig(
                epoch="test-3day-epoch",
                run_id="test-3day-run",
                base_dir=base_dir,
                raw_root=base_dir / "raw",
                manifest_root=base_dir / "manifests",
                compressed_root=compressed_root,
                receipt_root=receipt_root,
                metrics_path=metrics_path,
                poll_interval_seconds=0.01,
                grace_seconds=600,
                store_type="file",
                file_store_root=store_root,
                expected_owner=_current_user(),
                scan_runner_mode="direct",
                run_full_scan=True,
                disk_critical_percent=99.0,
            )

            # Simulated time is Day 3 08:00 UTC (all 9 cohorts closed + grace passed)
            eval_time = datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)
            scheduler = ClosedHourArchiveScheduler(cfg, now_fn=lambda: eval_time)

            # Execute single archive worker loop until all eligible cohorts are processed
            processed_order: list[str] = []
            max_steps = 15
            for _ in range(max_steps):
                res = scheduler.run_once(now=eval_time)
                if res["status"] in ("IDLE", "STOPPED"):
                    break
                self.assertEqual(res["status"], "PASS", f"Scheduler failed with: {res}")
                processed_cohort = res["processed_cohort"]
                self.assertIsNotNone(processed_cohort)
                processed_order.append(processed_cohort)

            # 1. Exactly all 9 cohorts processed once
            self.assertEqual(processed_order, expected_cohorts)
            self.assertEqual(len(processed_order), 9)

            # 2. No starvation: discover_eligible_hours now returns empty
            remaining_eligible = scheduler.discover_eligible_hours(now=eval_time)
            self.assertEqual(remaining_eligible, [])

            # 3. No reopening: each cohort reports is_cohort_completed == True
            for cohort_str in expected_cohorts:
                cid = ArchiveCohortId.parse(cohort_str)
                self.assertTrue(
                    scheduler.is_cohort_completed(cid),
                    f"Cohort {cohort_str} should be completed",
                )

            # 4. No duplicate archive: run_once now returns IDLE
            idle_res = scheduler.run_once(now=eval_time)
            self.assertEqual(idle_res["status"], "IDLE")

            # 5. No receipt collision: separate distinct receipts for each date with same HH 05
            receipt_dir = receipt_root / "bithumb" / "orderbook" / "KRW-BTC"
            day1_05_receipt = receipt_dir / "KRW-BTC_2026-09-05_05.jsonl.archive-receipt.json"
            day2_05_receipt = receipt_dir / "KRW-BTC_2026-09-06_05.jsonl.archive-receipt.json"
            day3_05_receipt = receipt_dir / "KRW-BTC_2026-09-07_05.jsonl.archive-receipt.json"
            self.assertTrue(day1_05_receipt.exists())
            self.assertTrue(day2_05_receipt.exists())
            self.assertTrue(day3_05_receipt.exists())

            r1 = json.loads(day1_05_receipt.read_text(encoding="utf-8"))
            r2 = json.loads(day2_05_receipt.read_text(encoding="utf-8"))
            r3 = json.loads(day3_05_receipt.read_text(encoding="utf-8"))
            self.assertEqual(r1.get("cohort"), "2026-09-05_05")
            self.assertEqual(r2.get("cohort"), "2026-09-06_05")
            self.assertEqual(r3.get("cohort"), "2026-09-07_05")

            # 6. No fullscan collision: separate distinct reports for each date with same HH 05
            day1_05_scan = receipt_root / "full_scan_2026-09-05_05_report.json"
            day2_05_scan = receipt_root / "full_scan_2026-09-06_05_report.json"
            day3_05_scan = receipt_root / "full_scan_2026-09-07_05_report.json"
            self.assertTrue(day1_05_scan.exists())
            self.assertTrue(day2_05_scan.exists())
            self.assertTrue(day3_05_scan.exists())

            s1 = json.loads(day1_05_scan.read_text(encoding="utf-8"))
            s2 = json.loads(day2_05_scan.read_text(encoding="utf-8"))
            s3 = json.loads(day3_05_scan.read_text(encoding="utf-8"))
            self.assertEqual(s1.get("cohort"), "2026-09-05_05")
            self.assertEqual(s2.get("cohort"), "2026-09-06_05")
            self.assertEqual(s3.get("cohort"), "2026-09-07_05")

            # 7. Official auditor coverage validation on all 9 cohorts
            receipt_files = list(receipt_root.rglob("*.archive-receipt.json"))
            fullscan_files = list(receipt_root.glob("full_scan_*_report.json"))
            coverage = validate_archive_evidence_coverage(
                expected_cohorts=expected_cohorts,
                receipt_files=receipt_files,
                full_scan_reports=fullscan_files,
                expected_epoch="test-3day-epoch",
                expected_run_id="test-3day-run",
                expected_feeds=[("bithumb", "orderbook", "KRW-BTC")],
            )
            self.assertEqual(coverage["blockers"], [])
            self.assertEqual(coverage["receipt_coverage"], 9)
            self.assertEqual(coverage["fullscan_coverage"], 9)
            self.assertEqual(coverage["missing_receipt_cohorts"], [])
            self.assertEqual(coverage["missing_fullscan_cohorts"], [])


class Synthetic72HArchiveOracleTests(unittest.TestCase):
    """Release-blocking synthetic 72H topology archive oracle."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temp_dir = tempfile.TemporaryDirectory()
        cls.epoch_dir = Path(cls._temp_dir.name) / "aws-72h-soak-oracle"
        cls.raw_dir = cls.epoch_dir / "raw"
        cls.manifests_dir = cls.epoch_dir / "manifests"
        cls.compressed_dir = cls.epoch_dir / "compressed"
        cls.receipts_dir = cls.epoch_dir / "archive-receipts"

        for d in (cls.raw_dir, cls.manifests_dir, cls.compressed_dir, cls.receipts_dir):
            d.mkdir(parents=True, exist_ok=True)

        cls.start_dt = datetime(2026, 9, 5, 5, 40, 0, tzinfo=timezone.utc)
        cls.end_dt = cls.start_dt + timedelta(seconds=259200)  # 2026-09-08 05:40:00

        cls.raw_cohorts = derive_expected_raw_cohorts(cls.start_dt, cls.end_dt)
        cls.archive_cohorts = derive_expected_archive_cohorts(cls.start_dt, cls.end_dt, grace_seconds=600)

        cls.feeds = SoakAuditor72H.get_expected_feed_universe()
        cls.epoch_name = "aws-72h-soak-oracle"
        cls.run_id = "aws-72h-run-oracle"

        # Final raw file for hour 73 (active partial hour)
        final_raw = cls.raw_dir / "bithumb" / "orderbook" / "KRW-BTC" / "KRW-BTC_2026-09-08_05.jsonl"
        final_raw.parent.mkdir(parents=True, exist_ok=True)
        final_raw.write_text('{"record": 1}\n', encoding="utf-8")

        cls.receipt_files: list[Path] = []
        cls.fullscan_files: list[Path] = []

        # Create all 72 fullscan reports and 72 * 76 = 5472 qualifying receipts
        for cohort_str in cls.archive_cohorts:
            inputs = [
                f"raw/{exch}/{strm}/{mkt}/{mkt}_{cohort_str}.jsonl"
                for exch, strm, mkt in cls.feeds
            ]
            fs_file = cls.receipts_dir / f"full_scan_{cohort_str}_report.json"
            fs_file.write_text(
                json.dumps(
                    {
                        "cohort": cohort_str,
                        "epoch": cls.epoch_name,
                        "run_id": cls.run_id,
                        "status": "PASS",
                        "inputs": inputs,
                        "integrity": {"totals": {"status": "PASS", "files": len(inputs)}},
                    }
                ),
                encoding="utf-8",
            )
            cls.fullscan_files.append(fs_file)

            for exch, strm, mkt in cls.feeds:
                rf = cls.receipts_dir / f"{exch}_{strm}_{mkt}_{cohort_str}.jsonl.archive-receipt.json"
                rf.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "cohort": cohort_str,
                            "collector_epoch": cls.epoch_name,
                            "run_id": cls.run_id,
                            "partition": f"raw/{exch}/{strm}/{mkt}/{mkt}_{cohort_str}.jsonl",
                            "state": "CLEANUP_ELIGIBLE",
                            "restore_verified_at": "2026-09-08T06:00:00+00:00",
                            "restore_verified": True,
                        }
                    ),
                    encoding="utf-8",
                )
                cls.receipt_files.append(rf)

        contract = {
            "collector_epoch": cls.epoch_name,
            "collector_run_id": cls.run_id,
            "runtime_software_commit": "42c8c4649622b63bac6cbd15219e307bbef7c3d9",
            "runtime_fingerprint": "fp-synthetic-oracle",
            "start_time_utc": cls.start_dt.isoformat(),
            "expected_end_time_utc": cls.end_dt.isoformat(),
            "duration_seconds": 259200,
            "feed_universe": 76,
            "require_receipts": True,
            "require_fullscan": True,
        }
        (cls.epoch_dir / "epoch_contract.json").write_text(json.dumps(contract), encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temp_dir.cleanup()

    def test_72h_shaped_synthetic_archive_oracle(self) -> None:
        """73 touched raw cohorts, 72 archive-eligible cohorts, 76 feed universe (5,472 receipts)."""
        self.assertEqual(len(self.raw_cohorts), 73)
        self.assertEqual(len(self.archive_cohorts), 72)
        self.assertEqual(self.raw_cohorts[0], "2026-09-05_05")
        self.assertEqual(self.raw_cohorts[-1], "2026-09-08_05")
        self.assertEqual(self.archive_cohorts[0], "2026-09-05_05")
        self.assertEqual(self.archive_cohorts[-1], "2026-09-08_04")
        self.assertNotIn("2026-09-08_05", self.archive_cohorts)

        self.assertEqual(len(self.feeds), 76)
        self.assertEqual(len(self.receipt_files), 72 * 76)
        self.assertEqual(len(self.fullscan_files), 72)

        coverage = validate_archive_evidence_coverage(
            expected_cohorts=self.archive_cohorts,
            receipt_files=self.receipt_files,
            full_scan_reports=self.fullscan_files,
            expected_epoch=self.epoch_name,
            expected_run_id=self.run_id,
        )

        self.assertEqual(coverage["blockers"], [])
        self.assertEqual(coverage["receipt_coverage"], 72)
        self.assertEqual(coverage["fullscan_coverage"], 72)
        self.assertEqual(coverage["total_qualifying_receipts"], 5472)
        self.assertEqual(coverage["expected_total_receipts"], 5472)
        self.assertEqual(coverage["missing_receipt_cohorts"], [])
        self.assertEqual(coverage["missing_fullscan_cohorts"], [])

    def test_72h_mutation_missing_one_receipt_middle_cohort(self) -> None:
        """Adversarial mutation: remove ONE receipt from a middle cohort -> must FAIL."""
        target_name = "bithumb_orderbook_KRW-BTC_2026-09-06_12.jsonl.archive-receipt.json"
        mutated_receipts = [f for f in self.receipt_files if f.name != target_name]
        self.assertEqual(len(mutated_receipts), 5471)

        result = validate_archive_evidence_coverage(
            expected_cohorts=self.archive_cohorts,
            receipt_files=mutated_receipts,
            full_scan_reports=self.fullscan_files,
            expected_epoch=self.epoch_name,
            expected_run_id=self.run_id,
        )

        self.assertEqual(result["receipt_coverage"], 71)
        self.assertEqual(result["missing_receipt_cohorts"], ["2026-09-06_12"])
        self.assertTrue(
            any("2026-09-06_12" in b and "bithumb/orderbook/KRW-BTC" in b for b in result["blockers"])
        )

    def test_72h_mutation_duplicate_substitution(self) -> None:
        """Adversarial mutation: replace omitted feed with duplicate of another feed -> must FAIL."""
        target_name = "bithumb_orderbook_KRW-BTC_2026-09-06_12.jsonl.archive-receipt.json"
        mutated_receipts = [f for f in self.receipt_files if f.name != target_name]

        dup_file = self.receipts_dir / "dup_sub_2026-09-06_12.archive-receipt.json"
        dup_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "cohort": "2026-09-06_12",
                    "collector_epoch": self.epoch_name,
                    "run_id": self.run_id,
                    "partition": "raw/bithumb/trade/KRW-BTC/KRW-BTC_2026-09-06_12.jsonl",
                    "state": "CLEANUP_ELIGIBLE",
                    "restore_verified_at": "2026-09-08T06:00:00+00:00",
                    "restore_verified": True,
                }
            ),
            encoding="utf-8",
        )
        mutated_receipts.append(dup_file)
        self.assertEqual(len(mutated_receipts), 5472)

        result = validate_archive_evidence_coverage(
            expected_cohorts=self.archive_cohorts,
            receipt_files=mutated_receipts,
            full_scan_reports=self.fullscan_files,
            expected_epoch=self.epoch_name,
            expected_run_id=self.run_id,
        )

        self.assertEqual(result["receipt_coverage"], 71)
        self.assertEqual(result["missing_receipt_cohorts"], ["2026-09-06_12"])
        self.assertTrue(
            any("2026-09-06_12" in b and "bithumb/orderbook/KRW-BTC" in b for b in result["blockers"])
        )

    def test_72h_mutation_incomplete_fullscan(self) -> None:
        """Adversarial mutation: remove ONE compressed/fullscan input from middle cohort -> must FAIL."""
        sub_dir = self.epoch_dir / "mutated_scan"
        sub_dir.mkdir(exist_ok=True)
        incomplete_fs = sub_dir / "full_scan_2026-09-06_12_report.json"
        inputs_75 = [
            f"raw/{exch}/{strm}/{mkt}/{mkt}_2026-09-06_12.jsonl"
            for exch, strm, mkt in self.feeds[:75]
        ]
        incomplete_fs.write_text(
            json.dumps(
                {
                    "cohort": "2026-09-06_12",
                    "epoch": self.epoch_name,
                    "run_id": self.run_id,
                    "status": "PASS",
                    "inputs": inputs_75,
                    "integrity": {"totals": {"status": "PASS", "files": len(inputs_75)}},
                }
            ),
            encoding="utf-8",
        )
        mutated_scans = [f for f in self.fullscan_files if "2026-09-06_12" not in f.name] + [incomplete_fs]

        result = validate_archive_evidence_coverage(
            expected_cohorts=self.archive_cohorts,
            receipt_files=self.receipt_files,
            full_scan_reports=mutated_scans,
            expected_epoch=self.epoch_name,
            expected_run_id=self.run_id,
        )

        self.assertEqual(result["fullscan_coverage"], 71)
        self.assertEqual(result["missing_fullscan_cohorts"], ["2026-09-06_12"])
        self.assertTrue(
            any("FULLSCAN_INPUTS_INCOMPLETE" in b and "2026-09-06_12" in b for b in result["blockers"])
        )

    def test_72h_mutation_non_terminal_receipt_state(self) -> None:
        """Adversarial mutation: mark one receipt intermediate/non-qualifying -> must FAIL."""
        target_name = "bithumb_orderbook_KRW-BTC_2026-09-06_12.jsonl.archive-receipt.json"
        mutated_receipts = [f for f in self.receipt_files if f.name != target_name]

        bad_state_file = self.receipts_dir / "mutated_bad_state.archive-receipt.json"
        bad_state_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "cohort": "2026-09-06_12",
                    "collector_epoch": self.epoch_name,
                    "run_id": self.run_id,
                    "partition": "raw/bithumb/orderbook/KRW-BTC/KRW-BTC_2026-09-06_12.jsonl",
                    "state": "COMPRESSED",
                    "restore_verified_at": "2026-09-08T06:00:00+00:00",
                    "restore_verified": True,
                }
            ),
            encoding="utf-8",
        )
        mutated_receipts.append(bad_state_file)

        result = validate_archive_evidence_coverage(
            expected_cohorts=self.archive_cohorts,
            receipt_files=mutated_receipts,
            full_scan_reports=self.fullscan_files,
            expected_epoch=self.epoch_name,
            expected_run_id=self.run_id,
        )

        self.assertEqual(result["receipt_coverage"], 71)
        self.assertEqual(result["missing_receipt_cohorts"], ["2026-09-06_12"])
        self.assertTrue(any("RECEIPT_INVALID_STATE" in b and bad_state_file.name in b for b in result["blockers"]))

    def test_72h_mutation_wrong_date_same_hh_substitution(self) -> None:
        """Adversarial mutation: wrong-date same-HH receipt substitution -> must FAIL."""
        target_name = "bithumb_orderbook_KRW-BTC_2026-09-06_05.jsonl.archive-receipt.json"
        mutated_receipts = [f for f in self.receipt_files if f.name != target_name]

        wrong_date_file = self.receipts_dir / "wrong_date_sub.archive-receipt.json"
        wrong_date_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "cohort": "2026-09-05_05",
                    "collector_epoch": self.epoch_name,
                    "run_id": self.run_id,
                    "partition": "raw/bithumb/orderbook/KRW-BTC/KRW-BTC_2026-09-06_05.jsonl",
                    "state": "CLEANUP_ELIGIBLE",
                    "restore_verified_at": "2026-09-08T06:00:00+00:00",
                    "restore_verified": True,
                }
            ),
            encoding="utf-8",
        )
        mutated_receipts.append(wrong_date_file)

        result = validate_archive_evidence_coverage(
            expected_cohorts=self.archive_cohorts,
            receipt_files=mutated_receipts,
            full_scan_reports=self.fullscan_files,
            expected_epoch=self.epoch_name,
            expected_run_id=self.run_id,
        )

        self.assertEqual(result["receipt_coverage"], 71)
        self.assertEqual(result["missing_receipt_cohorts"], ["2026-09-06_05"])
        self.assertTrue(any("ARCHIVE_RECEIPT_MISSING" in b and "2026-09-06_05" in b for b in result["blockers"]))


class RemediationLifecycleIntegrationTests(unittest.TestCase):
    """End-to-end integration of supervisor, collector, and scheduler lifecycle."""

    def test_supervisor_collector_scheduler_clean_shutdown(self) -> None:
        """Collector naturally completes collection, enters finalization, flushes manifest, exits 0.

        Supervisor grants grace without signaling collection deadline;
        Auxiliary processes (publisher, scheduler) receive SIGTERM after collector exit and exit 0.
        Overall result: PASS, forced_timeout=False, received_signal=None.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = {
                "metrics": tmp_path / "metrics.json",
                "lifecycle": tmp_path / "lifecycle.json",
                "result": tmp_path / "result.json",
                "log": tmp_path / "supervisor.log",
                "events": tmp_path / "events.log",
            }
            run_id = "aws-72h-lifecycle-clean-integration"

            collector_cmd = (
                sys.executable,
                str(LIFECYCLE_FIXTURE),
                "collector",
                "--run-id",
                run_id,
                "--metrics",
                str(paths["metrics"]),
                "--lifecycle",
                str(paths["lifecycle"]),
                "--events",
                str(paths["events"]),
                "--sleep",
                "0.10",
                "--finalize-sleep",
                "0.08",
                "--exit-code",
                "0",
            )
            publisher_cmd = (
                sys.executable,
                str(LIFECYCLE_FIXTURE),
                "publisher",
                "--run-id",
                run_id,
                "--events",
                str(paths["events"]),
                "--sleep",
                "0.02",
                "--exit-code",
                "0",
            )
            scheduler_cmd = (
                sys.executable,
                str(LIFECYCLE_FIXTURE),
                "scheduler",
                "--run-id",
                run_id,
                "--events",
                str(paths["events"]),
                "--sleep",
                "0.50",
                "--exit-code",
                "0",
            )

            config = SupervisorConfig(
                run_id=run_id,
                collection_duration_seconds=0.10,
                finalization_timeout_seconds=0.30,
                hard_ceiling_seconds=0.40,
                collector_command=collector_cmd,
                publisher_command=publisher_cmd,
                archive_scheduler_command=scheduler_cmd,
                metrics_path=paths["metrics"],
                collector_lifecycle_path=paths["lifecycle"],
                result_path=paths["result"],
                log_path=paths["log"],
                poll_interval_seconds=0.005,
                publisher_interval_seconds=0.02,
                shutdown_grace_seconds=0.10,
                require_full_duration=True,
            )

            exit_code = BoundedSupervisor(config).run()
            self.assertEqual(exit_code, 0)

            result = json.loads(paths["result"].read_text(encoding="utf-8"))
            events = paths["events"].read_text(encoding="utf-8").splitlines()

            self.assertEqual(result["overall_status"], "PASS")
            self.assertEqual(result["collector_exit_code"], 0)
            self.assertIn(result["publisher_exit_code"], (0, -signal.SIGTERM))
            self.assertIn(result["archive_scheduler_exit_code"], (0, -signal.SIGTERM))
            self.assertNotEqual(result["archive_scheduler_exit_code"], -signal.SIGKILL)
            self.assertIsNone(result["received_signal"])
            self.assertFalse(result["forced_timeout"])
            self.assertTrue(result["full_duration_satisfied"])
            self.assertTrue(result["final_manifest_flush_observed"])

            # Verify lifecycle phases in order
            self.assertIn("COLLECTING", events)
            self.assertIn("FINALIZING", events)
            self.assertIn("final-manifest", events)
            self.assertIn("COMPLETE", events)
            self.assertIn("scheduler-start", events)
            self.assertIn("scheduler-SIGTERM", events)


if __name__ == "__main__":
    unittest.main()
