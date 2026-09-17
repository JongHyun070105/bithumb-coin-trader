"""Comprehensive tests for RuntimeObserver and Terminal Witness.

Validates:
1. Accurate classification of ComponentHealthState (HEALTHY, STALE, FAILED, DEGRADED).
2. Distinction between STALE collector (loop heartbeat > 30s) and quiet market (canonical event > 30s but loop heartbeat fresh).
3. Self-observation fields (observer_pid, observer_started_at, observer_last_cycle, observer_errors).
4. S3 minute witness publishing and fail-safe local degradation on upload errors.
5. Non-intervening invariant: observer never touches, modifies, or deletes collector data.
6. Terminal witness exit status capture, classification, receipt generation, and S3 upload.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

# Ensure imports succeed
ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
SCRIPTS_DIR = ROOT / "scripts"
for p in (SRC_DIR, SCRIPTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from bithumb_coin_trader.collector_state_model import (
    ArchiverHealth,
    CollectorHealth,
    ComponentHealthState,
    EvidenceHealth,
    ObserverHealth,
    RuntimeHealthSnapshot,
    SupervisorHealth,
    WriterHealth,
    read_health_snapshot,
    write_health_snapshot_atomic,
)
from bithumb_coin_trader.runtime_observer import (
    ObserverConfig,
    RuntimeObserver,
    parse_s3_location,
)
from terminal_witness import (
    classify_terminal_outcome,
    record_terminal_receipt,
)


class MockS3Client:
    """In-memory mock for S3 client operations."""

    def __init__(self, fail_on_put: bool = False) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.fail_on_put = fail_on_put

    def put_object(self, Bucket: str, Key: str, Body: bytes, **kwargs: object) -> dict[str, str]:
        if self.fail_on_put:
            raise RuntimeError("Simulated S3 network failure / access denied")
        self.objects[(Bucket, Key)] = Body
        return {"ETag": '"mock-etag"'}


class ObserverHealthClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        (self.data_dir / "health").mkdir(parents=True, exist_ok=True)
        self.now = datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_collector_snapshot(self, snapshot: RuntimeHealthSnapshot) -> None:
        path = self.data_dir / "health" / "latest.json"
        write_health_snapshot_atomic(path, snapshot)

    def test_healthy_collector_with_fresh_heartbeat_and_events(self) -> None:
        snapshot = RuntimeHealthSnapshot(
            epoch="epoch_test",
            run_id="run_001",
            collector=CollectorHealth(
                last_loop_heartbeat=(self.now - timedelta(seconds=5)).isoformat(),
                last_canonical_event=(self.now - timedelta(seconds=5)).isoformat(),
                reconnect_count=0,
            ),
        )
        self._write_collector_snapshot(snapshot)

        config = ObserverConfig(data_dir=self.data_dir, epoch="epoch_test", run_id="run_001")
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(evaluated.collector.status, ComponentHealthState.HEALTHY.value)

    def test_quiet_market_not_stale(self) -> None:
        """Crucial test: canonical event > 30s ago but loop heartbeat fresh -> HEALTHY (not STALE)."""
        snapshot = RuntimeHealthSnapshot(
            epoch="epoch_test",
            run_id="run_002",
            collector=CollectorHealth(
                # Loop heartbeat is fresh (10s ago)
                last_loop_heartbeat=(self.now - timedelta(seconds=10)).isoformat(),
                # Canonical event is very old (120s ago) because market is illiquid/quiet
                last_canonical_event=(self.now - timedelta(seconds=120)).isoformat(),
                reconnect_count=0,
            ),
        )
        self._write_collector_snapshot(snapshot)

        config = ObserverConfig(data_dir=self.data_dir, epoch="epoch_test", run_id="run_002")
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(
            evaluated.collector.status,
            ComponentHealthState.HEALTHY.value,
            "Quiet market with fresh loop heartbeat must be classified as HEALTHY, not STALE",
        )

    def test_stale_collector_when_loop_heartbeat_exceeds_threshold(self) -> None:
        """Loop heartbeat > 30s ago -> must be classified as STALE."""
        snapshot = RuntimeHealthSnapshot(
            epoch="epoch_test",
            run_id="run_003",
            collector=CollectorHealth(
                # Loop heartbeat is 45 seconds old (> 30s)
                last_loop_heartbeat=(self.now - timedelta(seconds=45)).isoformat(),
                last_canonical_event=(self.now - timedelta(seconds=10)).isoformat(),
            ),
        )
        self._write_collector_snapshot(snapshot)

        config = ObserverConfig(data_dir=self.data_dir, epoch="epoch_test", run_id="run_003")
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(evaluated.collector.status, ComponentHealthState.STALE.value)

    def test_stale_collector_when_heartbeat_none(self) -> None:
        snapshot = RuntimeHealthSnapshot(
            epoch="epoch_test",
            run_id="run_004",
            collector=CollectorHealth(
                last_loop_heartbeat=None,
                last_canonical_event=None,
            ),
        )
        self._write_collector_snapshot(snapshot)

        config = ObserverConfig(data_dir=self.data_dir, epoch="epoch_test", run_id="run_004")
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(evaluated.collector.status, ComponentHealthState.STALE.value)

    def test_collector_failed_on_fatal_error(self) -> None:
        snapshot = RuntimeHealthSnapshot(
            epoch="epoch_test",
            run_id="run_005",
            collector=CollectorHealth(
                last_loop_heartbeat=(self.now - timedelta(seconds=5)).isoformat(),
                fatal_error="Unrecoverable WebSocket Protocol Error",
            ),
        )
        self._write_collector_snapshot(snapshot)

        config = ObserverConfig(data_dir=self.data_dir, epoch="epoch_test", run_id="run_005")
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(evaluated.collector.status, ComponentHealthState.FAILED.value)

    def test_collector_failed_on_systemd_unit_failed(self) -> None:
        snapshot = RuntimeHealthSnapshot(
            epoch="epoch_test",
            run_id="run_006",
            collector=CollectorHealth(
                last_loop_heartbeat=(self.now - timedelta(seconds=5)).isoformat(),
            ),
        )
        self._write_collector_snapshot(snapshot)

        mock_query = MagicMock(return_value={
            "ActiveState": "failed",
            "SubState": "failed",
            "Result": "exit-code",
            "MainPID": "9999",
        })

        config = ObserverConfig(
            data_dir=self.data_dir,
            epoch="epoch_test",
            run_id="run_006",
            unit_name="bitcoin-trader-test.service",
        )
        observer = RuntimeObserver(config=config, systemd_query_fn=mock_query)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(evaluated.supervisor.active_state, "failed")
        self.assertEqual(evaluated.collector.status, ComponentHealthState.FAILED.value)
        self.assertIn("failed", evaluated.collector.fatal_error or "")

    def test_writer_and_archiver_health_evaluations(self) -> None:
        snapshot = RuntimeHealthSnapshot(
            epoch="epoch_test",
            run_id="run_007",
            collector=CollectorHealth(last_loop_heartbeat=(self.now - timedelta(seconds=5)).isoformat()),
            writer=WriterHealth(writer_errors=2, queue_depth=12000),
            archiver=ArchiverHealth(upload_failures=1),
        )
        self._write_collector_snapshot(snapshot)

        config = ObserverConfig(data_dir=self.data_dir, epoch="epoch_test", run_id="run_007")
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(evaluated.writer.status, ComponentHealthState.DEGRADED.value)
        self.assertEqual(evaluated.archiver.status, ComponentHealthState.DEGRADED.value)

    def test_missing_initial_snapshot_tolerantly_handled(self) -> None:
        """When health/latest.json does not exist, observer handles gracefully without crashing."""
        config = ObserverConfig(data_dir=self.data_dir, epoch="epoch_fresh", run_id="run_fresh")
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(evaluated.epoch, "epoch_fresh")
        self.assertEqual(evaluated.run_id, "run_fresh")
        self.assertEqual(evaluated.collector.status, ComponentHealthState.UNKNOWN.value)
        self.assertEqual(evaluated.observer.status, ComponentHealthState.HEALTHY.value)


class ObserverSelfObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        (self.data_dir / "health").mkdir(parents=True, exist_ok=True)
        self.now = datetime(2026, 9, 17, 10, 15, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_self_observation_fields_populated(self) -> None:
        config = ObserverConfig(data_dir=self.data_dir, epoch="epoch_obs", run_id="run_obs")
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(evaluated.observer.observer_pid, os.getpid())
        self.assertIsNotNone(evaluated.observer.observer_started_at)
        self.assertEqual(evaluated.observer.observer_last_cycle, self.now.isoformat())
        self.assertEqual(evaluated.observer.observer_errors, 0)
        self.assertEqual(evaluated.observer.status, ComponentHealthState.HEALTHY.value)

        # Verify atomic write to health/observer_latest.json
        observer_path = self.data_dir / "health" / "observer_latest.json"
        self.assertTrue(observer_path.exists())
        loaded = read_health_snapshot(observer_path)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.observer.observer_pid, os.getpid())
        self.assertEqual(loaded.observer.observer_last_cycle, self.now.isoformat())

    def test_s3_witness_upload_and_fail_safe_behavior(self) -> None:
        # 1. Success path
        mock_s3 = MockS3Client(fail_on_put=False)
        config = ObserverConfig(
            data_dir=self.data_dir,
            epoch="epoch_s3",
            run_id="run_s3",
            s3_bucket="test-bucket",
            s3_prefix="market-data/temporary/run_s3",
            allow_s3_write=True,
        )
        observer = RuntimeObserver(config=config, s3_client=mock_s3)
        evaluated = observer.run_cycle(now=self.now)

        expected_minute_key = "market-data/temporary/run_s3/observability/minute/20260917T101500Z.json"
        expected_latest_key = "market-data/temporary/run_s3/observability/latest.json"

        self.assertIn(("test-bucket", expected_minute_key), mock_s3.objects)
        self.assertIn(("test-bucket", expected_latest_key), mock_s3.objects)
        self.assertEqual(evaluated.evidence.status, ComponentHealthState.HEALTHY.value)

        # 2. Failure path: S3 put raises exception
        mock_failing_s3 = MockS3Client(fail_on_put=True)
        observer_fail = RuntimeObserver(config=config, s3_client=mock_failing_s3)
        evaluated_fail = observer_fail.run_cycle(now=self.now)

        # NON-INTERVENING: marks EVIDENCE = DEGRADED, records error, does not crash!
        self.assertEqual(evaluated_fail.evidence.status, ComponentHealthState.DEGRADED.value)
        self.assertEqual(evaluated_fail.observer.status, ComponentHealthState.DEGRADED.value)
        self.assertGreater(observer_fail.observer_errors, 0)
        self.assertEqual(evaluated_fail.last_exception.component, "EVIDENCE")

    def test_non_intervention_guarantee(self) -> None:
        """Observer NEVER modifies existing collector health file or deletes anything."""
        collector_path = self.data_dir / "health" / "latest.json"
        orig_snapshot = RuntimeHealthSnapshot(
            epoch="epoch_orig",
            run_id="run_orig",
            collector=CollectorHealth(
                last_loop_heartbeat=(self.now - timedelta(seconds=100)).isoformat(),
                fatal_error="Do Not Touch Me",
            ),
        )
        write_health_snapshot_atomic(collector_path, orig_snapshot)
        orig_content = collector_path.read_text(encoding="utf-8")

        config = ObserverConfig(data_dir=self.data_dir, epoch="epoch_orig", run_id="run_orig")
        observer = RuntimeObserver(config=config)
        observer.run_cycle(now=self.now)

        # Collector file must be 100% untouched
        after_content = collector_path.read_text(encoding="utf-8")
        self.assertEqual(orig_content, after_content)


class TerminalWitnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        (self.data_dir / "health").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_classification_matrix(self) -> None:
        self.assertEqual(classify_terminal_outcome("success", "exited", "0"), "CLEAN_SUCCESS")
        self.assertEqual(classify_terminal_outcome("none", "exited", "0"), "CLEAN_SUCCESS")
        self.assertEqual(classify_terminal_outcome("timeout", "killed", "0"), "TIMEOUT_EXPIRED")
        self.assertEqual(classify_terminal_outcome("watchdog", "killed", "0"), "WATCHDOG_KILLED")
        self.assertEqual(classify_terminal_outcome("resources", "killed", "0"), "RESOURCE_EXHAUSTED")
        self.assertEqual(classify_terminal_outcome("signal", "killed", "9"), "SIGNAL_TERMINATED_9")
        self.assertEqual(classify_terminal_outcome("exit-code", "exited", "1"), "PROCESS_EXIT_ERROR_1")
        self.assertEqual(classify_terminal_outcome("core-dump", "dumped", "11"), "CORE_DUMPED_STATUS_11")

    def test_terminal_witness_generates_receipt_and_reads_health(self) -> None:
        # Create mock health snapshots
        collector_snap = RuntimeHealthSnapshot(epoch="ep_term", run_id="run_term")
        collector_snap.collector.status = ComponentHealthState.HEALTHY.value
        write_health_snapshot_atomic(self.data_dir / "health" / "latest.json", collector_snap)

        obs_snap = RuntimeHealthSnapshot(epoch="ep_term", run_id="run_term")
        obs_snap.observer.observer_pid = 1234
        write_health_snapshot_atomic(self.data_dir / "health" / "observer_latest.json", obs_snap)

        mock_s3 = MockS3Client()
        receipt = record_terminal_receipt(
            data_dir=self.data_dir,
            service_result="exit-code",
            exit_code="exited",
            exit_status="2",
            s3_bucket="receipt-bucket",
            s3_prefix="witness/run_term",
            allow_s3_write=True,
            s3_client=mock_s3,
        )

        self.assertEqual(receipt["epoch"], "ep_term")
        self.assertEqual(receipt["run_id"], "run_term")
        self.assertEqual(receipt["service_result"], "exit-code")
        self.assertEqual(receipt["exit_status"], "2")
        self.assertEqual(receipt["terminal_classification"], "PROCESS_EXIT_ERROR_2")
        self.assertTrue(receipt["s3_uploaded"])
        self.assertIsNotNone(receipt["last_known_health"])
        self.assertIsNotNone(receipt["last_observer_health"])

        # Check local file write
        local_receipt = self.data_dir / "terminal" / "terminal-receipt.json"
        self.assertTrue(local_receipt.exists())
        loaded_receipt = json.loads(local_receipt.read_text(encoding="utf-8"))
        self.assertEqual(loaded_receipt["terminal_classification"], "PROCESS_EXIT_ERROR_2")

        # Check S3 upload
        s3_key = "witness/run_term/terminal/terminal-receipt.json"
        self.assertIn(("receipt-bucket", s3_key), mock_s3.objects)

    def test_terminal_witness_cli_invocation(self) -> None:
        """Verify terminal_witness.py runs as a subprocess script via CLI."""
        env = dict(os.environ)
        env["SERVICE_RESULT"] = "signal"
        env["EXIT_CODE"] = "killed"
        env["EXIT_STATUS"] = "15"

        script_path = SCRIPTS_DIR / "terminal_witness.py"
        res = subprocess.run(
            [
                sys.executable,
                str(script_path),
                f"--data-dir={self.data_dir}",
                "--epoch=cli_epoch",
                "--run-id=cli_run",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(res.returncode, 0, f"Script failed with output:\n{res.stdout}\n{res.stderr}")

        receipt_file = self.data_dir / "terminal" / "terminal-receipt.json"
        self.assertTrue(receipt_file.exists())
        data = json.loads(receipt_file.read_text(encoding="utf-8"))
        self.assertEqual(data["epoch"], "cli_epoch")
        self.assertEqual(data["service_result"], "signal")
        self.assertEqual(data["exit_status"], "15")
        self.assertEqual(data["terminal_classification"], "SIGNAL_TERMINATED_15")


class ObserverIntegrationAndEdgeCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        (self.data_dir / "health").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_parse_s3_location_variants(self) -> None:
        # 1. Standard bucket and prefix
        b, p = parse_s3_location("my-bucket", "some/prefix/")
        self.assertEqual(b, "my-bucket")
        self.assertEqual(p, "some/prefix")

        # 2. s3:// URI
        b, p = parse_s3_location(None, "s3://uri-bucket/nested/path/")
        self.assertEqual(b, "uri-bucket")
        self.assertEqual(p, "nested/path")

        # 3. None inputs
        b, p = parse_s3_location(None, None)
        self.assertIsNone(b)
        self.assertEqual(p, "")

    def test_corrupt_collector_health_tolerantly_handled(self) -> None:
        """Malformed JSON in latest.json should be treated as missing without crashing."""
        corrupt_path = self.data_dir / "health" / "latest.json"
        corrupt_path.write_text("NOT_JSON_DATA{{{", encoding="utf-8")

        config = ObserverConfig(data_dir=self.data_dir, epoch="ep_corrupt", run_id="run_corrupt")
        observer = RuntimeObserver(config=config)
        evaluated = observer.run_cycle()

        self.assertEqual(evaluated.collector.status, ComponentHealthState.UNKNOWN.value)
        self.assertEqual(evaluated.observer.status, ComponentHealthState.HEALTHY.value)

    def test_observer_cli_one_shot_execution(self) -> None:
        """Verify observer CLI --one-shot option runs single cycle and outputs valid JSON."""
        res = subprocess.run(
            [
                sys.executable,
                "-m",
                "bithumb_coin_trader.runtime_observer",
                f"--data-dir={self.data_dir}",
                "--epoch=cli_obs_epoch",
                "--run-id=cli_obs_run",
                "--one-shot",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Observer CLI failed:\n{res.stdout}\n{res.stderr}")
        data = json.loads(res.stdout)
        self.assertEqual(data["epoch"], "cli_obs_epoch")
        self.assertEqual(data["run_id"], "cli_obs_run")
        self.assertIn("observer", data)
        self.assertEqual(data["observer"]["observer_errors"], 0)

        # Verify observer_latest.json was written
        obs_latest = self.data_dir / "health" / "observer_latest.json"
        self.assertTrue(obs_latest.exists())


if __name__ == "__main__":
    unittest.main()
