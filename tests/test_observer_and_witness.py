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
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

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
from terminal_witness import (  # pyright: ignore[reportMissingImports]
    classify_terminal_outcome,
    main as terminal_witness_main,
    record_terminal_receipt,
)


class MockS3Client:
    """In-memory mock for S3 client operations."""

    def __init__(self, fail_on_put: bool = False, failure: Exception | None = None) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.fail_on_put = fail_on_put
        self.failure = failure

    def put_object(self, Bucket: str, Key: str, Body: bytes, **kwargs: object) -> dict[str, str]:
        if self.fail_on_put:
            raise self.failure or RuntimeError("Simulated S3 network failure / access denied")
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

        # Write archiver sidecar with upload_failures (the real source of archiver health)
        archiver_snapshot = RuntimeHealthSnapshot(
            epoch="epoch_test",
            run_id="run_007",
            observed_at=self.now.isoformat(),
            archiver=ArchiverHealth(
                status=ComponentHealthState.DEGRADED.value,
                upload_failures=1,
            ),
        )
        write_health_snapshot_atomic(
            self.data_dir / "health" / "archiver_latest.json",
            archiver_snapshot,
        )

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

    def test_collector_waiting_for_collector_when_unit_inactive(self) -> None:
        """Before collector starts, observer correctly classifies status as WAITING_FOR_COLLECTOR."""
        mock_query = MagicMock(return_value={
            "ActiveState": "inactive",
            "SubState": "dead",
            "Result": "success",
            "MainPID": "0",
        })
        config = ObserverConfig(
            data_dir=self.data_dir,
            epoch="epoch_wait",
            run_id="run_wait",
            unit_name="bitcoin-trader-test.service",
        )
        observer = RuntimeObserver(config=config, systemd_query_fn=mock_query)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(evaluated.supervisor.active_state, "inactive")
        self.assertEqual(evaluated.collector.status, ComponentHealthState.WAITING_FOR_COLLECTOR.value)
        self.assertEqual(evaluated.observer.status, ComponentHealthState.HEALTHY.value)

    def test_collector_waiting_for_collector_when_unit_activating_no_heartbeat(self) -> None:
        """During collector startup (activating/active before first heartbeat), status is WAITING_FOR_COLLECTOR."""
        mock_query = MagicMock(return_value={
            "ActiveState": "activating",
            "SubState": "start-pre",
            "Result": "success",
            "MainPID": "1234",
        })
        config = ObserverConfig(
            data_dir=self.data_dir,
            epoch="epoch_wait2",
            run_id="run_wait2",
            unit_name="bitcoin-trader-test.service",
        )
        observer = RuntimeObserver(config=config, systemd_query_fn=mock_query)
        evaluated = observer.run_cycle(now=self.now)

        self.assertEqual(evaluated.supervisor.active_state, "activating")
        self.assertEqual(evaluated.collector.status, ComponentHealthState.WAITING_FOR_COLLECTOR.value)
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
        with patch.dict(os.environ, {"INVOCATION_ID": "0123456789abcdef0123456789abcdef"}):
            receipt = record_terminal_receipt(
                data_dir=self.data_dir,
                epoch="ep_term",
                run_id="run_term",
                service_result="exit-code",
                exit_code="exited",
                exit_status="2",
                s3_bucket="receipt-bucket",
                s3_prefix="witness/run_term",
                s3_region="ap-northeast-2",
                allow_s3_write=True,
                s3_client=mock_s3,
            )

        self.assertEqual(receipt["epoch"], "ep_term")
        self.assertEqual(receipt["run_id"], "run_term")
        self.assertEqual(receipt["service_result"], "exit-code")
        self.assertEqual(receipt["exit_status"], "2")
        self.assertEqual(receipt["terminal_classification"], "PROCESS_EXIT_ERROR_2")
        self.assertEqual(receipt["systemd_invocation_id"], "0123456789abcdef0123456789abcdef")
        self.assertTrue(receipt["s3_uploaded"])
        self.assertEqual(receipt["s3_bucket"], "receipt-bucket")
        self.assertEqual(receipt["s3_region"], "ap-northeast-2")
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
        self.assertEqual(
            mock_s3.objects[("receipt-bucket", s3_key)],
            local_receipt.read_bytes(),
            "successful remote and local stable witness bytes must match exactly",
        )

    def test_terminal_witness_persists_false_while_stable_put_is_in_flight(self) -> None:
        local_receipt = self.data_dir / "terminal" / "terminal-receipt.json"

        class InspectingS3Client(MockS3Client):
            def put_object(inner_self, Bucket: str, Key: str, Body: bytes, **kwargs: object) -> dict[str, str]:
                current = json.loads(local_receipt.read_text(encoding="utf-8"))
                if Key.endswith("terminal-receipt.json"):
                    assert current["s3_uploaded"] is False
                    assert current["s3_key"] is None
                return super().put_object(Bucket, Key, Body, **kwargs)

        receipt = record_terminal_receipt(
            data_dir=self.data_dir,
            epoch="ep_order",
            run_id="run_order",
            service_result="success",
            exit_code="exited",
            exit_status="0",
            s3_bucket="receipt-bucket",
            s3_prefix="witness/run_order",
            s3_region="ap-northeast-2",
            allow_s3_write=True,
            s3_client=InspectingS3Client(),
        )
        self.assertTrue(receipt["s3_uploaded"])

    def test_terminal_witness_failures_preserve_false_and_record_only_error_type(self) -> None:
        class AccessDenied(Exception):
            pass

        for failure in (AccessDenied("denied"), TimeoutError("timeout"), ConnectionError("network")):
            with self.subTest(error=type(failure).__name__):
                data_dir = self.data_dir / type(failure).__name__
                data_dir.mkdir()
                receipt = record_terminal_receipt(
                    data_dir=data_dir,
                    epoch="ep_failure_case",
                    run_id="run_failure_case",
                    service_result="success",
                    exit_code="exited",
                    exit_status="0",
                    s3_bucket="receipt-bucket",
                    s3_prefix="witness/run_failure_case",
                    s3_region="ap-northeast-2",
                    allow_s3_write=True,
                    s3_client=MockS3Client(fail_on_put=True, failure=failure),
                )
                self.assertFalse(receipt["s3_uploaded"])
                self.assertIsNone(receipt["s3_key"])
                self.assertEqual(receipt["s3_upload_error_type"], type(failure).__name__)
                self.assertNotIn("s3_upload_error_message", receipt)

    def test_kill_during_stable_put_leaves_local_receipt_false(self) -> None:
        class KilledDuringPut:
            def put_object(self, **kwargs: object) -> dict[str, str]:
                raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            record_terminal_receipt(
                data_dir=self.data_dir,
                epoch="ep_killed",
                run_id="run_killed",
                service_result="success",
                exit_code="exited",
                exit_status="0",
                s3_bucket="receipt-bucket",
                s3_prefix="witness/run_killed",
                s3_region="ap-northeast-2",
                allow_s3_write=True,
                s3_client=KilledDuringPut(),
            )
        local = json.loads((self.data_dir / "terminal" / "terminal-receipt.json").read_text())
        self.assertFalse(local["s3_uploaded"])
        self.assertIsNone(local["s3_key"])

    def test_terminal_witness_retry_after_failure_writes_successful_exact_bytes(self) -> None:
        failing = record_terminal_receipt(
            data_dir=self.data_dir,
            epoch="ep_retry",
            run_id="run_retry",
            service_result="success",
            exit_code="exited",
            exit_status="0",
            s3_bucket="receipt-bucket",
            s3_prefix="witness/run_retry",
            s3_region="ap-northeast-2",
            allow_s3_write=True,
            s3_client=MockS3Client(fail_on_put=True),
        )
        self.assertFalse(failing["s3_uploaded"])

        successful_s3 = MockS3Client()
        succeeded = record_terminal_receipt(
            data_dir=self.data_dir,
            epoch="ep_retry",
            run_id="run_retry",
            service_result="success",
            exit_code="exited",
            exit_status="0",
            s3_bucket="receipt-bucket",
            s3_prefix="witness/run_retry",
            s3_region="ap-northeast-2",
            allow_s3_write=True,
            s3_client=successful_s3,
        )
        self.assertTrue(succeeded["s3_uploaded"])
        key = "witness/run_retry/terminal/terminal-receipt.json"
        local = self.data_dir / "terminal" / "terminal-receipt.json"
        self.assertEqual(successful_s3.objects[("receipt-bucket", key)], local.read_bytes())

    def test_terminal_witness_s3_failure_is_recorded_and_not_reported_as_uploaded(self) -> None:
        mock_s3 = MockS3Client(fail_on_put=True)
        receipt = record_terminal_receipt(
            data_dir=self.data_dir,
            epoch="ep_failure",
            run_id="run_failure",
            service_result="success",
            exit_code="exited",
            exit_status="0",
            s3_bucket="receipt-bucket",
            s3_prefix="witness/run_failure",
            s3_region="ap-northeast-2",
            allow_s3_write=True,
            s3_client=mock_s3,
        )
        self.assertFalse(receipt["s3_uploaded"])
        self.assertIsNone(receipt["s3_key"])
        local_receipt = self.data_dir / "terminal" / "terminal-receipt.json"
        self.assertEqual(json.loads(local_receipt.read_text())["s3_uploaded"], False)
        self.assertEqual(mock_s3.objects, {})

    def test_terminal_witness_cli_uses_exact_region_and_returns_failure_on_upload_error(self) -> None:
        failing_s3 = MockS3Client(fail_on_put=True)
        boto3_mock = MagicMock()
        boto3_mock.client.return_value = failing_s3
        args = [
            f"--data-dir={self.data_dir}",
            "--epoch=aws-validation-witness-e2e-smoke-20260928T043700Z-v1",
            "--run-id=aws-validation-witness-e2e-smoke-run-20260928T043700Z-v1",
            "--service-result=success",
            "--exit-code=exited",
            "--exit-status=0",
            "--s3-bucket=receipt-bucket",
            "--s3-prefix=market-data/temporary/aws-validation-witness-e2e-smoke-20260928T043700Z-v1",
            "--s3-region=ap-northeast-2",
            "--allow-s3-write",
        ]
        with patch.dict("sys.modules", {"boto3": boto3_mock}):
            result = terminal_witness_main(args)
        self.assertEqual(result, 1)
        boto3_mock.client.assert_called_once_with("s3", region_name="ap-northeast-2")
        failed_receipt = json.loads((self.data_dir / "terminal" / "terminal-receipt.json").read_text())
        self.assertFalse(failed_receipt["s3_uploaded"])
        self.assertIsNone(failed_receipt["s3_key"])

    def test_terminal_witness_cli_rejects_missing_s3_upload_contract(self) -> None:
        """The production ExecStopPost CLI must fail before writing an unbound receipt."""
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
        self.assertEqual(res.returncode, 2)
        self.assertIn("exact S3 bucket, prefix, region", res.stderr)

        receipt_file = self.data_dir / "terminal" / "terminal-receipt.json"
        self.assertFalse(receipt_file.exists())


WITNESS_COMMIT = "a" * 40
WITNESS_TREE = "b" * 40


class VersionedMockS3(MockS3Client):
    def put_object(self, Bucket: str, Key: str, Body: bytes, **kwargs: object) -> dict[str, str]:
        super().put_object(Bucket, Key, Body, **kwargs)
        return {"ETag": '"mock-etag"', "VersionId": "mock-version-1"}


class TerminalWitnessArtifactTests(unittest.TestCase):
    EPOCH = "ep_witness"
    RUN_ID = "run_witness"
    PREFIX = "witness/run_witness"

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.terminal = self.data_dir / "terminal"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _seal(self, **overrides: object) -> None:
        identity: dict[str, object] = {
            "run_id": self.RUN_ID, "epoch": self.EPOCH,
            "software_commit_sha": WITNESS_COMMIT, "software_tree_sha": WITNESS_TREE,
        }
        identity.update(overrides)
        (self.data_dir / "identity.json").write_text(json.dumps(identity), encoding="utf-8")

    def _record(self, s3_client: object, **overrides: object) -> dict:
        kwargs: dict = dict(
            data_dir=self.data_dir, epoch=self.EPOCH, run_id=self.RUN_ID,
            service_result="success", exit_code="exited", exit_status="0",
            s3_bucket="receipt-bucket", s3_prefix=self.PREFIX, s3_region="ap-northeast-2",
            allow_s3_write=True, s3_client=s3_client,
        )
        kwargs.update(overrides)
        return record_terminal_receipt(**kwargs)

    def _witness(self) -> dict:
        return json.loads((self.terminal / "terminal-witness.json").read_text(encoding="utf-8"))

    def test_clean_exit_zero_generates_bound_witness_with_exact_receipt_bytes(self) -> None:
        self._seal()
        s3 = VersionedMockS3()
        self._record(s3)
        witness = self._witness()
        key = f"{self.PREFIX}/terminal/terminal-receipt.json"
        receipt_bytes = (self.terminal / "terminal-receipt.json").read_bytes()
        self.assertEqual(witness["witness_kind"], "terminal-witness")
        self.assertEqual(witness["run_id"], self.RUN_ID)
        self.assertEqual(witness["epoch"], self.EPOCH)
        self.assertEqual(witness["terminal_classification"], "CLEAN_SUCCESS")
        self.assertTrue(witness["runtime_identity_bound"])
        self.assertEqual(witness["software_commit_sha"], WITNESS_COMMIT)
        self.assertEqual(witness["software_tree_sha"], WITNESS_TREE)
        self.assertTrue(witness["s3_uploaded"])
        self.assertEqual(witness["s3_key"], key)
        self.assertEqual(witness["s3_put_version_id"], "mock-version-1")
        self.assertEqual(witness["receipt_sha256"], hashlib.sha256(receipt_bytes).hexdigest())
        self.assertEqual(witness["receipt_byte_length"], len(receipt_bytes))
        self.assertEqual(s3.objects[("receipt-bucket", key)], receipt_bytes)
        self.assertNotEqual((self.terminal / "terminal-witness.json").read_bytes(), receipt_bytes)

    def test_witness_is_durable_locally_and_false_before_put_returns(self) -> None:
        self._seal()
        witness_path = self.terminal / "terminal-witness.json"
        seen: dict = {}

        class Inspecting(VersionedMockS3):
            def put_object(inner, Bucket: str, Key: str, Body: bytes, **kwargs: object) -> dict[str, str]:
                if Key.endswith("terminal/terminal-receipt.json"):
                    seen["witness"] = json.loads(witness_path.read_text(encoding="utf-8"))
                return super().put_object(Bucket, Key, Body, **kwargs)

        self._record(Inspecting())
        self.assertIn("witness", seen, "local witness must exist before the S3 PutObject is issued")
        self.assertFalse(seen["witness"]["s3_uploaded"])
        self.assertIsNone(seen["witness"]["s3_key"])
        self.assertIsNone(seen["witness"]["s3_put_version_id"])
        self.assertTrue(self._witness()["s3_uploaded"])

    def test_killed_put_leaves_durable_false_witness(self) -> None:
        self._seal()

        class Killed:
            def put_object(self, **kwargs: object) -> dict[str, str]:
                raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            self._record(Killed())
        witness = self._witness()
        self.assertFalse(witness["s3_uploaded"])
        self.assertIsNone(witness["s3_key"])

    def test_upload_failure_or_timeout_never_records_success(self) -> None:
        for failure in (TimeoutError("timeout"), ConnectionError("net"), RuntimeError("denied")):
            with self.subTest(error=type(failure).__name__):
                self._seal()
                self._record(MockS3Client(fail_on_put=True, failure=failure))
                witness = self._witness()
                self.assertFalse(witness["s3_uploaded"])
                self.assertIsNone(witness["s3_key"])
                self.assertIsNone(witness["s3_put_version_id"])
                self.assertEqual(witness["s3_upload_error_type"], type(failure).__name__)

    def test_unavailable_s3_client_cannot_record_success(self) -> None:
        self._seal()
        boto3_mock = MagicMock()
        boto3_mock.client.side_effect = RuntimeError("no credentials")
        with patch.dict("sys.modules", {"boto3": boto3_mock}):
            self._record(None)
        witness = self._witness()
        self.assertFalse(witness["s3_uploaded"])
        self.assertIsNone(witness["s3_key"])

    def test_missing_or_mismatched_sealed_identity_is_not_runtime_bound(self) -> None:
        self._record(VersionedMockS3())
        unsealed = self._witness()
        self.assertFalse(unsealed["runtime_identity_bound"])
        self.assertIsNone(unsealed["software_commit_sha"])
        for override in ({"run_id": "someone_else"}, {"epoch": "other_epoch"},
                         {"software_commit_sha": "short"}, {"software_tree_sha": None}):
            with self.subTest(override=override):
                self._seal(**override)
                self._record(VersionedMockS3())
                witness = self._witness()
                self.assertFalse(witness["runtime_identity_bound"])
                self.assertIsNone(witness["software_commit_sha"])
                self.assertIsNone(witness["software_tree_sha"])

    def test_cli_clean_exit_zero_writes_witness_and_returns_zero(self) -> None:
        self._seal()
        boto3_mock = MagicMock()
        boto3_mock.client.return_value = VersionedMockS3()
        args = [
            f"--data-dir={self.data_dir}", f"--epoch={self.EPOCH}", f"--run-id={self.RUN_ID}",
            "--service-result=success", "--exit-code=exited", "--exit-status=0",
            "--s3-bucket=receipt-bucket", f"--s3-prefix={self.PREFIX}",
            "--s3-region=ap-northeast-2", "--allow-s3-write",
        ]
        with patch.dict("sys.modules", {"boto3": boto3_mock}):
            self.assertEqual(terminal_witness_main(args), 0)
        self.assertTrue(self._witness()["s3_uploaded"])


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
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
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
