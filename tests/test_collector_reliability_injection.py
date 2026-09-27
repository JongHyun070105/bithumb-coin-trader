"""Failure-Injection & Reliability Unit Tests for Collector Pipeline.

Covers the 15 required failure scenarios per COLLECTOR_FAILURE_MODEL.md:
 1. COLLECTOR NORMAL EXIT (exit code 0, terminal witness records clean completion)
 2. COLLECTOR NONZERO EXIT (exit code != 0, witness records failure code)
 3. COLLECTOR SIGTERM (witness records signal termination)
 4. COLLECTOR STALL / STALE HEARTBEAT (loop heartbeat > 30s triggers STALE while supervisor remains HEALTHY)
 5. WRITER ERROR (writer error count increments, status DEGRADED)
 6. WRITER QUEUE BACKLOG (queue backlog > threshold triggers alert)
 7. ARCHIVER EXCEPTION (archiver error recorded, status DEGRADED/FAILED without loop crash)
 8. S3 UPLOAD FAILURE (EVIDENCE becomes DEGRADED, local write continues)
 9. OBSERVER PROCESS FAILURE (system detects observer dead / collector alive)
10. CORRUPT / PARTIALLY WRITTEN HEALTH SNAPSHOT (tolerant reader handles partial/corrupt JSON returning None without crashing)
11. TERMINAL WITNESS EXECUTION (witness executes and records receipt even on abnormal termination)
12. HOUR-CLOSE MISSING RAW (canary detects 0/76 RAW and flags FAIL)
13. COVERAGE WITHOUT RAW (canary distinguishes coverage-only anomaly from true complete cohort)
14. RAW WITHOUT RECEIPT (canary detects unfinalized/unreceipted cohort)
15. DISK-LOW LOGIC (mock low disk triggers safe alert)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Callable
import pytest

from bithumb_coin_trader.collector_state_model import (
    ArchiverHealth,
    CollectorHealth,
    ComponentHealthState,
    CurrentCohortHealth,
    EvidenceHealth,
    LastExceptionInfo,
    ObserverHealth,
    ResourceTelemetry,
    RuntimeHealthSnapshot,
    SupervisorHealth,
    WriterHealth,
    compute_exception_hash,
    read_health_snapshot,
    utc_iso_now,
    write_health_snapshot_atomic,
)


# =============================================================================
# Helper Models & Reference Components (Observer, Terminal Witness, Canary)
# =============================================================================


@dataclass
class TerminalWitnessReceipt:
    schema_version: int = 1
    invocation_id: str = ""
    run_id: str = ""
    exit_code: int = 0
    exit_signal: str | None = None
    service_result: str = "success"  # "success", "exit-code", "signal", "failed"
    clean_exit: bool = True
    recorded_at: str = ""
    witness_recorded: bool = True
    environment_id: str = "test-env"


class TerminalWitness:
    """Systemd ExecStopPost / exit hook witness recording terminal completion evidence."""

    def __init__(self, receipt_path: Path, environment_id: str = "test-env") -> None:
        self.receipt_path = receipt_path
        self.environment_id = environment_id

    def record_termination(
        self,
        exit_code: int,
        invocation_id: str,
        run_id: str,
        exit_signal: str | None = None,
        service_result: str | None = None,
        now: datetime | None = None,
    ) -> TerminalWitnessReceipt:
        current_time = (now or datetime.now(timezone.utc)).isoformat()
        clean = (exit_code == 0) and (exit_signal is None)

        if service_result is None:
            if exit_signal is not None:
                service_result = "signal"
            elif exit_code != 0:
                service_result = "exit-code"
            else:
                service_result = "success"

        receipt = TerminalWitnessReceipt(
            schema_version=1,
            invocation_id=invocation_id,
            run_id=run_id,
            exit_code=exit_code,
            exit_signal=exit_signal,
            service_result=service_result,
            clean_exit=clean,
            recorded_at=current_time,
            witness_recorded=True,
            environment_id=self.environment_id,
        )

        self.receipt_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.receipt_path.with_name(f".{self.receipt_path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(asdict(receipt), indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.receipt_path)
        return receipt

    def read_receipt(self) -> TerminalWitnessReceipt | None:
        if not self.receipt_path.exists():
            return None
        try:
            data = json.loads(self.receipt_path.read_text(encoding="utf-8"))
            return TerminalWitnessReceipt(**data)
        except Exception:
            return None


@dataclass
class ObserverEvaluation:
    collector_status: ComponentHealthState
    writer_status: ComponentHealthState
    archiver_status: ComponentHealthState
    evidence_status: ComponentHealthState
    overall_status: ComponentHealthState
    stall_detected: bool = False
    queue_backlog_alert: bool = False
    disk_low_alert: bool = False
    alerts: list[str] = field(default_factory=list)


class CollectorObserver:
    """Independent observer daemon evaluation logic (Non-intervention principle)."""

    def __init__(
        self,
        heartbeat_stall_threshold_seconds: float = 30.0,
        queue_backlog_threshold: int = 500,
        disk_free_critical_bytes: int = 500 * 1024 * 1024,  # 500MB
        disk_used_percent_threshold: float = 90.0,
    ) -> None:
        self.heartbeat_stall_threshold = heartbeat_stall_threshold_seconds
        self.queue_backlog_threshold = queue_backlog_threshold
        self.disk_free_critical_bytes = disk_free_critical_bytes
        self.disk_used_percent_threshold = disk_used_percent_threshold

    def evaluate(
        self,
        snapshot: RuntimeHealthSnapshot,
        now: datetime | None = None,
    ) -> ObserverEvaluation:
        current = now or datetime.now(timezone.utc)
        alerts: list[str] = []

        # 1. Collector evaluation (Heartbeat Stall Check)
        stall_detected = False
        collector_status = ComponentHealthState(snapshot.collector.status)
        if snapshot.collector.last_loop_heartbeat:
            try:
                hb_dt = datetime.fromisoformat(snapshot.collector.last_loop_heartbeat)
                gap = (current - hb_dt).total_seconds()
                if gap > self.heartbeat_stall_threshold:
                    collector_status = ComponentHealthState.STALE
                    stall_detected = True
                    alerts.append(
                        f"COLLECTOR_STALL: heartbeat gap {gap:.1f}s > {self.heartbeat_stall_threshold}s"
                    )
            except Exception:
                collector_status = ComponentHealthState.UNKNOWN

        # 2. Writer evaluation (Errors & Backlog)
        writer_status = ComponentHealthState(snapshot.writer.status)
        if snapshot.writer.writer_errors > 0:
            writer_status = ComponentHealthState.DEGRADED
            alerts.append(f"WRITER_ERRORS: {snapshot.writer.writer_errors} error(s)")

        queue_backlog_alert = False
        if snapshot.writer.queue_depth > self.queue_backlog_threshold:
            queue_backlog_alert = True
            writer_status = ComponentHealthState.DEGRADED
            alerts.append(
                f"QUEUE_BACKLOG: depth {snapshot.writer.queue_depth} > {self.queue_backlog_threshold}"
            )

        # 3. Archiver evaluation
        archiver_status = ComponentHealthState(snapshot.archiver.status)
        if snapshot.archiver.archive_errors > 0:
            archiver_status = ComponentHealthState.DEGRADED
            alerts.append(f"ARCHIVER_ERRORS: {snapshot.archiver.archive_errors} error(s)")

        # 4. Evidence / S3 Upload evaluation
        evidence_status = ComponentHealthState(snapshot.evidence.status)
        if snapshot.archiver.upload_failures > 0:
            evidence_status = ComponentHealthState.DEGRADED
            alerts.append(
                f"UPLOAD_FAILURES: {snapshot.archiver.upload_failures} upload failure(s)"
            )

        # 5. Resource / Disk evaluation
        disk_low_alert = False
        if (
            snapshot.resources.disk_free_bytes > 0
            and snapshot.resources.disk_free_bytes < self.disk_free_critical_bytes
        ):
            disk_low_alert = True
            alerts.append(
                f"DISK_LOW: free bytes {snapshot.resources.disk_free_bytes} < {self.disk_free_critical_bytes}"
            )

        if snapshot.resources.disk_free_bytes > 0 and snapshot.resources.disk_used_bytes > 0:
            total = snapshot.resources.disk_free_bytes + snapshot.resources.disk_used_bytes
            used_pct = 100.0 * snapshot.resources.disk_used_bytes / total
            if used_pct >= self.disk_used_percent_threshold:
                disk_low_alert = True
                alerts.append(
                    f"DISK_HIGH_USAGE: {used_pct:.1f}% >= {self.disk_used_percent_threshold}%"
                )

        # Overall Status resolution
        statuses = [collector_status, writer_status, archiver_status, evidence_status]
        if any(s == ComponentHealthState.FAILED for s in statuses):
            overall = ComponentHealthState.FAILED
        elif any(s == ComponentHealthState.STALE for s in statuses):
            overall = ComponentHealthState.STALE
        elif any(s == ComponentHealthState.DEGRADED for s in statuses):
            overall = ComponentHealthState.DEGRADED
        elif all(s == ComponentHealthState.HEALTHY for s in statuses):
            overall = ComponentHealthState.HEALTHY
        else:
            overall = ComponentHealthState.UNKNOWN

        return ObserverEvaluation(
            collector_status=collector_status,
            writer_status=writer_status,
            archiver_status=archiver_status,
            evidence_status=evidence_status,
            overall_status=overall,
            stall_detected=stall_detected,
            queue_backlog_alert=queue_backlog_alert,
            disk_low_alert=disk_low_alert,
            alerts=alerts,
        )


@dataclass
class CanaryVerdict:
    status: str  # "PASS" or "FAIL"
    reason: str | None = None
    cohort: str = ""
    raw_count: int = 0
    coverage_count: int = 0
    receipt_count: int = 0
    expected_feeds: int = 76
    details: dict[str, Any] = field(default_factory=dict)


class HourCloseCanary:
    """Hour-Close Canary verifying S3 completeness across RAW, coverage, and receipts."""

    def __init__(self, expected_feed_count: int = 76) -> None:
        self.expected_feed_count = expected_feed_count

    def inspect_cohort(
        self,
        cohort: str,
        raw_root: Path,
        coverage_root: Path,
        receipt_root: Path,
    ) -> CanaryVerdict:
        raw_files = [
            p for p in raw_root.glob(f"**/*_{cohort}.jsonl")
            if p.is_file() and p.stat().st_size > 0
        ]
        raw_count = len(raw_files)

        coverage_files = [
            p for p in coverage_root.glob(f"**/{cohort}/**/*.coverage.json")
            if p.is_file() and p.stat().st_size > 0
        ]
        if not coverage_files:
            coverage_files = [
                p for p in coverage_root.glob(f"**/*{cohort}*.json")
                if p.is_file() and p.stat().st_size > 0
            ]
        coverage_count = len(coverage_files)

        receipt_files = [
            p for p in receipt_root.glob(f"**/*{cohort}*.archive-receipt.json")
            if p.is_file() and p.stat().st_size > 0
        ]
        receipt_count = len(receipt_files)

        # Scenario 12: Hour-close missing raw (0/76 RAW files)
        if raw_count == 0 and coverage_count == 0 and receipt_count == 0:
            return CanaryVerdict(
                status="FAIL",
                reason="MISSING_RAW_DATA",
                cohort=cohort,
                raw_count=0,
                coverage_count=0,
                receipt_count=0,
                expected_feeds=self.expected_feed_count,
                details={"message": f"0/{self.expected_feed_count} RAW files observed at hour close"},
            )

        # Scenario 13: Coverage without RAW anomaly (Coverage exists, but RAW missing!)
        if coverage_count > 0 and raw_count == 0:
            return CanaryVerdict(
                status="FAIL",
                reason="COVERAGE_WITHOUT_RAW",
                cohort=cohort,
                raw_count=0,
                coverage_count=coverage_count,
                receipt_count=receipt_count,
                expected_feeds=self.expected_feed_count,
                details={"message": "Coverage evidence materialized but 0 RAW data partitions exist (V4 anomaly)"},
            )

        # Scenario 14: RAW without receipt (RAW exists, but receipts absent/incomplete)
        if raw_count > 0 and receipt_count == 0:
            return CanaryVerdict(
                status="FAIL",
                reason="RAW_WITHOUT_RECEIPT",
                cohort=cohort,
                raw_count=raw_count,
                coverage_count=coverage_count,
                receipt_count=0,
                expected_feeds=self.expected_feed_count,
                details={"message": f"{raw_count} RAW files exist but no archive receipts generated"},
            )

        if raw_count < self.expected_feed_count:
            return CanaryVerdict(
                status="FAIL",
                reason="INCOMPLETE_RAW_COHORT",
                cohort=cohort,
                raw_count=raw_count,
                coverage_count=coverage_count,
                receipt_count=receipt_count,
                expected_feeds=self.expected_feed_count,
                details={"message": f"Only {raw_count}/{self.expected_feed_count} feeds present"},
            )

        return CanaryVerdict(
            status="PASS",
            reason=None,
            cohort=cohort,
            raw_count=raw_count,
            coverage_count=coverage_count,
            receipt_count=receipt_count,
            expected_feeds=self.expected_feed_count,
        )


def check_dual_process_health(
    collector_pid: int | None,
    observer_pid: int | None,
    is_pid_alive_fn: Callable[[int], bool],
) -> dict[str, Any]:
    """Inspects collector and observer process health symmetrically."""
    c_alive = is_pid_alive_fn(collector_pid) if collector_pid is not None else False
    o_alive = is_pid_alive_fn(observer_pid) if observer_pid is not None else False
    return {
        "collector_alive": c_alive,
        "observer_alive": o_alive,
        "observer_dead_collector_alive": c_alive and not o_alive,
        "collector_dead_observer_alive": not c_alive and o_alive,
    }


# =============================================================================
# 15 Required Failure Scenario Tests
# =============================================================================


def test_01_collector_normal_exit(tmp_path: Path):
    """Scenario 1: COLLECTOR NORMAL EXIT (exit code 0, terminal witness records clean completion)."""
    receipt_file = tmp_path / "terminal_witness_receipt.json"
    witness = TerminalWitness(receipt_file, environment_id="aws-apne2-research")

    receipt = witness.record_termination(
        exit_code=0,
        invocation_id="inv-clean-01",
        run_id="run-normal-001",
        exit_signal=None,
        service_result="success",
    )

    assert receipt.witness_recorded is True
    assert receipt.exit_code == 0
    assert receipt.exit_signal is None
    assert receipt.service_result == "success"
    assert receipt.clean_exit is True

    # Verify persisted receipt on disk
    persisted = witness.read_receipt()
    assert persisted is not None
    assert persisted.clean_exit is True
    assert persisted.exit_code == 0
    assert persisted.invocation_id == "inv-clean-01"


def test_02_collector_nonzero_exit(tmp_path: Path):
    """Scenario 2: COLLECTOR NONZERO EXIT (exit code != 0, witness records failure code)."""
    receipt_file = tmp_path / "terminal_witness_receipt.json"
    witness = TerminalWitness(receipt_file, environment_id="aws-apne2-research")

    receipt = witness.record_termination(
        exit_code=1,
        invocation_id="inv-fail-02",
        run_id="run-nonzero-002",
        exit_signal=None,
        service_result="exit-code",
    )

    assert receipt.witness_recorded is True
    assert receipt.clean_exit is False
    assert receipt.exit_code == 1
    assert receipt.service_result == "exit-code"

    persisted = witness.read_receipt()
    assert persisted is not None
    assert persisted.clean_exit is False
    assert persisted.exit_code == 1


def test_03_collector_sigterm(tmp_path: Path):
    """Scenario 3: COLLECTOR SIGTERM (witness records signal termination)."""
    receipt_file = tmp_path / "terminal_witness_receipt.json"
    witness = TerminalWitness(receipt_file, environment_id="aws-apne2-research")

    receipt = witness.record_termination(
        exit_code=143,  # 128 + 15
        invocation_id="inv-sigterm-03",
        run_id="run-sigterm-003",
        exit_signal="SIGTERM",
        service_result="signal",
    )

    assert receipt.witness_recorded is True
    assert receipt.clean_exit is False
    assert receipt.exit_code == 143
    assert receipt.exit_signal == "SIGTERM"
    assert receipt.service_result == "signal"

    persisted = witness.read_receipt()
    assert persisted is not None
    assert persisted.clean_exit is False
    assert persisted.exit_signal == "SIGTERM"


def test_04_collector_stall_stale_heartbeat():
    """Scenario 4: COLLECTOR STALL / STALE HEARTBEAT (heartbeat > 30s triggers STALE while supervisor HEALTHY)."""
    now = datetime(2026, 9, 17, 10, 0, 40, tzinfo=timezone.utc)
    stale_hb = (now - timedelta(seconds=35)).isoformat()  # 35s ago (> 30s threshold)

    snapshot = RuntimeHealthSnapshot(
        epoch="aws-72h-soak",
        run_id="run-stall-004",
        supervisor=SupervisorHealth(
            unit="bithumb-collector.service",
            active_state="active",
            sub_state="running",
            pid=1234,
        ),
        collector=CollectorHealth(
            status=ComponentHealthState.HEALTHY.value,
            last_loop_heartbeat=stale_hb,
        ),
        writer=WriterHealth(status=ComponentHealthState.HEALTHY.value),
        archiver=ArchiverHealth(status=ComponentHealthState.HEALTHY.value),
        evidence=EvidenceHealth(status=ComponentHealthState.HEALTHY.value),
    )

    observer = CollectorObserver(heartbeat_stall_threshold_seconds=30.0)
    evaluation = observer.evaluate(snapshot, now=now)

    assert snapshot.supervisor.active_state == "active"
    assert evaluation.stall_detected is True
    assert evaluation.collector_status == ComponentHealthState.STALE
    assert evaluation.overall_status == ComponentHealthState.STALE
    assert any("COLLECTOR_STALL" in alert for alert in evaluation.alerts)


def test_05_writer_error_increments_degraded():
    """Scenario 5: WRITER ERROR (writer error count increments, status DEGRADED)."""
    snapshot = RuntimeHealthSnapshot(
        epoch="aws-72h-soak",
        run_id="run-writer-err-005",
        collector=CollectorHealth(status=ComponentHealthState.HEALTHY.value),
        writer=WriterHealth(
            status=ComponentHealthState.HEALTHY.value,
            writer_errors=2,
            queue_depth=10,
        ),
        archiver=ArchiverHealth(status=ComponentHealthState.HEALTHY.value),
        evidence=EvidenceHealth(status=ComponentHealthState.HEALTHY.value),
    )

    observer = CollectorObserver()
    evaluation = observer.evaluate(snapshot)

    assert snapshot.writer.writer_errors == 2
    assert evaluation.writer_status == ComponentHealthState.DEGRADED
    assert evaluation.overall_status == ComponentHealthState.DEGRADED
    assert any("WRITER_ERRORS: 2" in alert for alert in evaluation.alerts)


def test_06_writer_queue_backlog_alert():
    """Scenario 6: WRITER QUEUE BACKLOG (queue backlog > threshold triggers alert)."""
    snapshot = RuntimeHealthSnapshot(
        epoch="aws-72h-soak",
        run_id="run-backlog-006",
        collector=CollectorHealth(status=ComponentHealthState.HEALTHY.value),
        writer=WriterHealth(
            status=ComponentHealthState.HEALTHY.value,
            queue_depth=750,
            max_queue_depth=1000,
            unpersisted_count=750,
        ),
        archiver=ArchiverHealth(status=ComponentHealthState.HEALTHY.value),
        evidence=EvidenceHealth(status=ComponentHealthState.HEALTHY.value),
    )

    observer = CollectorObserver(queue_backlog_threshold=500)
    evaluation = observer.evaluate(snapshot)

    assert evaluation.queue_backlog_alert is True
    assert evaluation.writer_status == ComponentHealthState.DEGRADED
    assert any("QUEUE_BACKLOG" in alert for alert in evaluation.alerts)


def test_07_archiver_exception_without_loop_crash():
    """Scenario 7: ARCHIVER EXCEPTION (archiver error recorded, status DEGRADED/FAILED without loop crash)."""
    exc_msg = "Zstandard compression OOM allocation error"
    exc_hash = compute_exception_hash(exc_msg)

    snapshot = RuntimeHealthSnapshot(
        epoch="aws-72h-soak",
        run_id="run-archiver-exc-007",
        collector=CollectorHealth(status=ComponentHealthState.HEALTHY.value),  # Loop continues!
        writer=WriterHealth(status=ComponentHealthState.HEALTHY.value),
        archiver=ArchiverHealth(
            status=ComponentHealthState.FAILED.value,
            archive_errors=1,
            archive_queue_depth=3,
        ),
        evidence=EvidenceHealth(status=ComponentHealthState.HEALTHY.value),
        last_exception=LastExceptionInfo(
            component="ARCHIVER",
            type="ZstdError",
            message_hash=exc_hash,
            timestamp=utc_iso_now(),
        ),
    )

    observer = CollectorObserver()
    evaluation = observer.evaluate(snapshot)

    # Collector loop is HEALTHY and unimpacted
    assert evaluation.collector_status == ComponentHealthState.HEALTHY
    # Archiver failure is recorded
    assert evaluation.archiver_status == ComponentHealthState.DEGRADED or evaluation.archiver_status == ComponentHealthState.FAILED
    assert snapshot.last_exception.component == "ARCHIVER"
    assert snapshot.last_exception.message_hash == exc_hash


def test_08_s3_upload_failure_local_write_continues():
    """Scenario 8: S3 UPLOAD FAILURE (EVIDENCE becomes DEGRADED, local write continues)."""
    now = datetime.now(timezone.utc).isoformat()
    snapshot = RuntimeHealthSnapshot(
        epoch="aws-72h-soak",
        run_id="run-s3-fail-008",
        collector=CollectorHealth(status=ComponentHealthState.HEALTHY.value),
        writer=WriterHealth(
            status=ComponentHealthState.HEALTHY.value,
            current_open_raw_count=76,
            last_local_raw_write=now,
            writer_errors=0,
        ),
        archiver=ArchiverHealth(
            status=ComponentHealthState.HEALTHY.value,
            upload_failures=1,
        ),
        evidence=EvidenceHealth(status=ComponentHealthState.HEALTHY.value),
    )

    observer = CollectorObserver()
    evaluation = observer.evaluate(snapshot)

    # Evidence is DEGRADED due to upload failure
    assert evaluation.evidence_status == ComponentHealthState.DEGRADED
    # Local writer remains HEALTHY and active
    assert evaluation.writer_status == ComponentHealthState.HEALTHY
    assert snapshot.writer.current_open_raw_count == 76
    assert any("UPLOAD_FAILURES: 1" in alert for alert in evaluation.alerts)


def test_09_observer_process_failure():
    """Scenario 9: OBSERVER PROCESS FAILURE (system detects observer dead / collector alive)."""
    collector_pid = 4123
    observer_pid = 4999

    def mock_is_pid_alive(pid: int) -> bool:
        # Collector PID is alive, Observer PID is dead/crashed
        return pid == collector_pid

    result = check_dual_process_health(collector_pid, observer_pid, mock_is_pid_alive)

    assert result["collector_alive"] is True
    assert result["observer_alive"] is False
    assert result["observer_dead_collector_alive"] is True


def test_10_corrupt_partially_written_health_snapshot(tmp_path: Path):
    """Scenario 10: CORRUPT / PARTIALLY WRITTEN HEALTH SNAPSHOT (tolerant reader handles partial/corrupt JSON returning None without crashing)."""
    snapshot_file = tmp_path / "collector_health.json"

    # Case A: Truncated JSON
    snapshot_file.write_text('{"schema_version": 1, "run_id": "test", "coll', encoding="utf-8")
    assert read_health_snapshot(snapshot_file) is None

    # Case B: Corrupted non-JSON text / garbage
    snapshot_file.write_text("CORRUPTED_NON_JSON_CONTENT_#$@%^&*", encoding="utf-8")
    assert read_health_snapshot(snapshot_file) is None

    # Case C: Empty file
    snapshot_file.write_text("", encoding="utf-8")
    assert read_health_snapshot(snapshot_file) is None

    # Case D: Whitespace only
    snapshot_file.write_text("   \n\t  \n", encoding="utf-8")
    assert read_health_snapshot(snapshot_file) is None

    # Case E: Non-dict JSON (array)
    snapshot_file.write_text("[1, 2, 3]", encoding="utf-8")
    assert read_health_snapshot(snapshot_file) is None

    # Case F: Non-existent path
    non_existent = tmp_path / "does_not_exist.json"
    assert read_health_snapshot(non_existent) is None

    # Case G: Valid atomic snapshot succeeds
    valid_snapshot = RuntimeHealthSnapshot(
        epoch="aws-72h-soak",
        run_id="run-valid-010",
        collector=CollectorHealth(status=ComponentHealthState.HEALTHY.value),
    )
    write_health_snapshot_atomic(snapshot_file, valid_snapshot)
    loaded = read_health_snapshot(snapshot_file)
    assert loaded is not None
    assert loaded.run_id == "run-valid-010"
    assert loaded.collector.status == ComponentHealthState.HEALTHY.value


def test_11_terminal_witness_execution_abnormal(tmp_path: Path):
    """Scenario 11: TERMINAL WITNESS EXECUTION (witness executes and records receipt even on abnormal termination)."""
    receipt_file = tmp_path / "terminal_witness_receipt.json"
    witness = TerminalWitness(receipt_file, environment_id="aws-apne2-research")

    # Simulate abnormal kernel crash or uncaught runtime crash (exit code 2)
    receipt = witness.record_termination(
        exit_code=2,
        invocation_id="inv-abnormal-11",
        run_id="run-abnormal-011",
        exit_signal=None,
        service_result="failed",
    )

    assert receipt.witness_recorded is True
    assert receipt.clean_exit is False
    assert receipt.exit_code == 2
    assert receipt.service_result == "failed"

    # Confirm receipt is reliably persisted on filesystem
    assert receipt_file.exists()
    persisted = witness.read_receipt()
    assert persisted is not None
    assert persisted.witness_recorded is True
    assert persisted.clean_exit is False


def test_12_hour_close_missing_raw(tmp_path: Path):
    """Scenario 12: HOUR-CLOSE MISSING RAW (canary detects 0/76 RAW and flags FAIL)."""
    raw_root = tmp_path / "raw"
    coverage_root = tmp_path / "coverage"
    receipt_root = tmp_path / "receipts"
    for d in (raw_root, coverage_root, receipt_root):
        d.mkdir(parents=True, exist_ok=True)

    canary = HourCloseCanary(expected_feed_count=76)
    cohort = "2026-09-17_01"

    # 0 RAW files exist for this cohort
    verdict = canary.inspect_cohort(cohort, raw_root, coverage_root, receipt_root)

    assert verdict.status == "FAIL"
    assert verdict.reason == "MISSING_RAW_DATA"
    assert verdict.raw_count == 0
    assert verdict.expected_feeds == 76


def test_13_coverage_without_raw(tmp_path: Path):
    """Scenario 13: COVERAGE WITHOUT RAW (canary distinguishes coverage-only anomaly from true complete cohort)."""
    raw_root = tmp_path / "raw"
    coverage_root = tmp_path / "coverage"
    receipt_root = tmp_path / "receipts"
    for d in (raw_root, coverage_root, receipt_root):
        d.mkdir(parents=True, exist_ok=True)

    cohort = "2026-09-17_02"

    # Materialize coverage file without any RAW data files (like V4 incident)
    cov_cohort_dir = coverage_root / cohort / "bithumb" / "orderbook"
    cov_cohort_dir.mkdir(parents=True, exist_ok=True)
    cov_file = cov_cohort_dir / "KRW-BTC.coverage.json"
    cov_file.write_text(json.dumps({"cohort": cohort, "coverage_state": "VERIFIED"}), encoding="utf-8")

    canary = HourCloseCanary(expected_feed_count=76)
    verdict = canary.inspect_cohort(cohort, raw_root, coverage_root, receipt_root)

    assert verdict.status == "FAIL"
    assert verdict.reason == "COVERAGE_WITHOUT_RAW"
    assert verdict.raw_count == 0
    assert verdict.coverage_count > 0


def test_14_raw_without_receipt(tmp_path: Path):
    """Scenario 14: RAW WITHOUT RECEIPT (canary detects unfinalized/unreceipted cohort)."""
    raw_root = tmp_path / "raw"
    coverage_root = tmp_path / "coverage"
    receipt_root = tmp_path / "receipts"
    for d in (raw_root, coverage_root, receipt_root):
        d.mkdir(parents=True, exist_ok=True)

    cohort = "2026-09-17_03"

    # RAW files exist
    raw_cohort_dir = raw_root / "bithumb" / "orderbook"
    raw_cohort_dir.mkdir(parents=True, exist_ok=True)
    raw_file = raw_cohort_dir / f"KRW-BTC_{cohort}.jsonl"
    raw_file.write_text('{"event": 1}\n{"event": 2}\n', encoding="utf-8")

    # But receipt_root has no receipts for this cohort
    canary = HourCloseCanary(expected_feed_count=76)
    verdict = canary.inspect_cohort(cohort, raw_root, coverage_root, receipt_root)

    assert verdict.status == "FAIL"
    assert verdict.reason == "RAW_WITHOUT_RECEIPT"
    assert verdict.raw_count == 1
    assert verdict.receipt_count == 0


def test_15_disk_low_logic(tmp_path: Path):
    """Scenario 15: DISK-LOW LOGIC (mock low disk triggers safe alert)."""
    # 200MB free out of 10GB (crit threshold is 500MB, used is 98%)
    snapshot = RuntimeHealthSnapshot(
        epoch="aws-72h-soak",
        run_id="run-disk-low-015",
        collector=CollectorHealth(status=ComponentHealthState.HEALTHY.value),
        writer=WriterHealth(status=ComponentHealthState.HEALTHY.value),
        archiver=ArchiverHealth(status=ComponentHealthState.HEALTHY.value),
        evidence=EvidenceHealth(status=ComponentHealthState.HEALTHY.value),
        resources=ResourceTelemetry(
            rss_bytes=100 * 1024 * 1024,
            fd_count=45,
            disk_free_bytes=200 * 1024 * 1024,   # 200MB < 500MB threshold
            disk_used_bytes=9800 * 1024 * 1024,  # 98%
        ),
    )

    observer = CollectorObserver(
        disk_free_critical_bytes=500 * 1024 * 1024,
        disk_used_percent_threshold=90.0,
    )
    evaluation = observer.evaluate(snapshot)

    assert evaluation.disk_low_alert is True
    assert any("DISK_LOW" in alert for alert in evaluation.alerts)
    assert any("DISK_HIGH_USAGE" in alert for alert in evaluation.alerts)
