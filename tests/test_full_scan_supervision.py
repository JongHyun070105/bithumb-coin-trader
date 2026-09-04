"""TDD Unit tests for global full-scan kernel flock supervision, timeout, and crash recovery."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import pwd
import signal
import subprocess
import sys
import time
import pytest

# Ensure root paths
ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
SCRIPTS_DIR = ROOT / "scripts"
for d in (ROOT, SRC_DIR, SCRIPTS_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

from scripts.orchestrate_closed_hour_archive import (
    FULL_SCAN_GLOBAL_LOCK_NAME,
    FULL_SCAN_METADATA_NAME,
    compute_backlog_metrics,
    is_global_full_scan_running,
    launch_detached_full_scan,
    orchestrate_closed_hour_archive,
    read_full_scan_metadata,
    run_full_scan_supervisor,
    verify_process_identity,
)


def current_user_name() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def create_sample_closed_partition(base_dir: Path, hour_str: str, date_str: str = "2026-09-04") -> Path:
    raw_root = base_dir / "raw"
    compressed_root = base_dir / "compressed"
    p_file = raw_root / "upbit" / "BTC_KRW" / f"BTC_KRW_{date_str}_{hour_str}.jsonl"
    p_file.parent.mkdir(parents=True, exist_ok=True)
    compressed_root.mkdir(parents=True, exist_ok=True)

    past_iso = f"{date_str}T{hour_str}:00:00.000Z"
    valid_record = {
        "timestamp": past_iso,
        "exchange": "upbit",
        "stream": "ticker",
        "market": "KRW-BTC",
        "exchange_ts": past_iso,
        "local_recv_ts": past_iso,
        "local_write_ts": past_iso,
        "payload": {"trade_price": 50000000},
    }
    p_file.write_text(json.dumps(valid_record) + "\n", encoding="utf-8")
    return p_file


# A. GLOBAL CONCURRENCY: Hour 05 running -> Hour 06 attempts launch -> second scanner does NOT start -> pending count increases
def test_global_concurrency_blocks_second_scan(tmp_path: Path):
    base_dir = tmp_path / "epoch_data"
    receipt_root = base_dir / "archive-receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)

    create_sample_closed_partition(base_dir, "05")
    create_sample_closed_partition(base_dir, "06")

    # Launch Hour 05 scanner supervisor
    ok1, msg1 = launch_detached_full_scan(
        epoch="test-epoch",
        hour="05",
        base_dir=base_dir,
        expected_owner=current_user_name(),
        runner_mode="detached",
        timeout_seconds=5,
    )
    assert ok1 is True
    assert is_global_full_scan_running(receipt_root) is True

    # Attempt to launch Hour 06 scanner while 05 is running
    ok2, msg2 = launch_detached_full_scan(
        epoch="test-epoch",
        hour="06",
        base_dir=base_dir,
        expected_owner=current_user_name(),
        runner_mode="detached",
        timeout_seconds=5,
    )
    assert ok2 is False
    assert "Global full scan runner is currently active" in msg2

    # Wait for 05 to finish
    time.sleep(1.5)
    assert is_global_full_scan_running(receipt_root) is False

    # Now Hour 06 can launch successfully
    ok3, msg3 = launch_detached_full_scan(
        epoch="test-epoch",
        hour="06",
        base_dir=base_dir,
        expected_owner=current_user_name(),
        runner_mode="detached",
        timeout_seconds=5,
    )
    assert ok3 is True
    time.sleep(1.5)
    assert (receipt_root / "full_scan_06_report.json").exists()


# B. FLOCK CRASH RECOVERY: SIGKILL supervisor -> kernel releases flock -> next orchestration acquires flock and stale metadata harmless
def test_flock_crash_recovery_on_sigkill(tmp_path: Path):
    base_dir = tmp_path / "epoch_data"
    receipt_root = base_dir / "archive-receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)

    # Spawn a supervisor process directly and kill it with SIGKILL
    lock_file = receipt_root / FULL_SCAN_GLOBAL_LOCK_NAME
    meta_file = receipt_root / FULL_SCAN_METADATA_NAME

    # Simulate supervisor acquiring flock and writing metadata
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"""
import fcntl, json, os, time
from pathlib import Path

lock_path = Path({repr(str(lock_file))})
meta_path = Path({repr(str(meta_file))})
fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
meta_path.write_text(json.dumps({{"pid": os.getpid(), "hour": "05"}}))
time.sleep(60)
""",
        ]
    )
    time.sleep(0.5)
    assert is_global_full_scan_running(receipt_root) is True
    assert meta_file.exists()

    # Force kill with SIGKILL (bypassing python finally blocks)
    proc.kill()
    proc.wait()

    # Kernel MUST automatically release flock upon process termination
    assert is_global_full_scan_running(receipt_root) is False

    # Stale metadata file still exists, but kernel flock is free
    assert meta_file.exists()

    # Next launch can acquire flock despite stale metadata
    create_sample_closed_partition(base_dir, "05")
    ok, msg = launch_detached_full_scan(
        epoch="test-epoch",
        hour="05",
        base_dir=base_dir,
        expected_owner=current_user_name(),
        runner_mode="detached",
        timeout_seconds=5,
    )
    assert ok is True
    time.sleep(1.5)
    assert (receipt_root / "full_scan_05_report.json").exists()


# C. PID REUSE: Metadata contains PID X, PID X belongs to unrelated process -> identity mismatch detected -> new scan not blocked
def test_pid_reuse_does_not_block_new_scan(tmp_path: Path):
    base_dir = tmp_path / "epoch_data"
    receipt_root = base_dir / "archive-receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)

    meta_file = receipt_root / FULL_SCAN_METADATA_NAME
    # Use PID 1 (init/launchd) or current process which is NOT audit_raw_integrity_offline
    unrelated_pid = os.getpid()
    meta_file.write_text(
        json.dumps({
            "pid": unrelated_pid,
            "hour": "05",
            "epoch": "test-epoch",
            "process_start_time": "12345",
            "command": "unrelated_process",
        }),
        encoding="utf-8",
    )

    # Process identity check fails because current pytest process is not audit_raw_integrity_offline
    is_valid_scanner = verify_process_identity(unrelated_pid, expected_cmd_substr="audit_raw_integrity_offline")
    assert is_valid_scanner is False

    # Because flock is not held, new scan must not be blocked
    create_sample_closed_partition(base_dir, "05")
    ok, msg = launch_detached_full_scan(
        epoch="test-epoch",
        hour="05",
        base_dir=base_dir,
        expected_owner=current_user_name(),
        runner_mode="detached",
        timeout_seconds=5,
    )
    assert ok is True
    time.sleep(1.5)
    assert (receipt_root / "full_scan_05_report.json").exists()


# D. TIMEOUT: Scanner fixture sleeps beyond timeout -> supervisor SIGTERM -> grace -> SIGKILL -> TIMEOUT report -> failure metric
def test_timeout_escalation_and_terminal_report(tmp_path: Path):
    base_dir = tmp_path / "epoch_data"
    receipt_root = base_dir / "archive-receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)
    raw_root = base_dir / "raw"
    p_file = create_sample_closed_partition(base_dir, "05")

    # Run supervisor with an intentional hanging scanner mock (timeout=1.0s, grace=0.5s)
    hanging_scanner_code = """
import time
# Hang indefinitely
while True:
    time.sleep(1)
"""
    log_file = receipt_root / "full_scan_05.log"
    report_file = receipt_root / "full_scan_05_report.json"

    ret = run_full_scan_supervisor(
        epoch="test-epoch",
        hour="05",
        base_dir=base_dir,
        timeout_seconds=1.0,
        grace_seconds=0.5,
        scanner_override_script=hanging_scanner_code,
    )
    assert ret != 0  # exits non-zero on timeout

    # Check terminal report was written with status FAIL and error TIMEOUT
    assert report_file.exists()
    report = json.loads(report_file.read_text(encoding="utf-8"))
    assert report["integrity"]["totals"]["status"] == "FAIL"
    assert report["error"] == "TIMEOUT"
    assert report["timeout_seconds"] == 1.0

    # Flock must be released
    assert is_global_full_scan_running(receipt_root) is False


# E. NON-ZERO CHILD: child exits 1 -> terminal FAIL report -> failed_full_scan_jobs increments
def test_nonzero_child_writes_fail_report(tmp_path: Path):
    base_dir = tmp_path / "epoch_data"
    receipt_root = base_dir / "archive-receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)
    create_sample_closed_partition(base_dir, "05")

    failing_scanner_code = """
import sys
sys.exit(1)
"""
    report_file = receipt_root / "full_scan_05_report.json"

    ret = run_full_scan_supervisor(
        epoch="test-epoch",
        hour="05",
        base_dir=base_dir,
        timeout_seconds=5.0,
        scanner_override_script=failing_scanner_code,
    )
    assert ret != 0
    assert report_file.exists()
    report = json.loads(report_file.read_text(encoding="utf-8"))
    assert report["integrity"]["totals"]["status"] == "FAIL"
    assert report["error"] == "CHILD_EXIT_1"

    # Compute backlog metrics should count failed_full_scan_jobs = 1
    metrics = compute_backlog_metrics(
        raw_root=base_dir / "raw",
        receipt_root=receipt_root,
        active_paths=[],
        now=datetime.now(timezone.utc),
        grace_period=timedelta(seconds=600),
        closed_files=[base_dir / "raw" / "upbit" / "BTC_KRW" / "BTC_KRW_2026-09-04_05.jsonl"],
        hours_seen=["05"],
    )
    assert metrics["failed_full_scan_jobs"] == 1
    assert metrics["completed_full_scan_jobs"] == 0
    assert metrics["pending_full_scan_jobs"] == 0


# F. PASS CHILD: normal pass -> completed counter increments, failed=0
def test_pass_child_increments_completed(tmp_path: Path):
    base_dir = tmp_path / "epoch_data"
    receipt_root = base_dir / "archive-receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)
    p_file = create_sample_closed_partition(base_dir, "05")

    ret = run_full_scan_supervisor(
        epoch="test-epoch",
        hour="05",
        base_dir=base_dir,
        timeout_seconds=5.0,
    )
    assert ret == 0

    report_file = receipt_root / "full_scan_05_report.json"
    assert report_file.exists()
    report = json.loads(report_file.read_text(encoding="utf-8"))
    assert report["integrity"]["totals"]["status"] == "PASS"

    metrics = compute_backlog_metrics(
        raw_root=base_dir / "raw",
        receipt_root=receipt_root,
        active_paths=[],
        now=datetime.now(timezone.utc),
        grace_period=timedelta(seconds=600),
        closed_files=[p_file],
        hours_seen=["05"],
    )
    assert metrics["completed_full_scan_jobs"] == 1
    assert metrics["failed_full_scan_jobs"] == 0
    assert metrics["pending_full_scan_jobs"] == 0


# G. BACKLOG ORDER: multiple pending hours -> oldest first, launches exactly 1
def test_backlog_ordering_oldest_first(tmp_path: Path):
    base_dir = tmp_path / "epoch_data"
    receipt_root = base_dir / "archive-receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)

    p04 = create_sample_closed_partition(base_dir, "04")
    p05 = create_sample_closed_partition(base_dir, "05")
    p06 = create_sample_closed_partition(base_dir, "06")

    # Orchestrate with store=file, dry_run=False
    res = orchestrate_closed_hour_archive(
        epoch="test-epoch",
        run_id="test-run",
        base_dir=base_dir,
        store_type="file",
        file_store_root=tmp_path / "file_store",
        expected_owner=current_user_name(),
        scan_runner_mode="detached",
        run_full_scan=True,
        disk_critical_percent=99.0,
    )

    # Concurrency 1 means only the OLDEST hour (04) should have been launched!
    # Hours 05 and 06 should remain pending!
    assert "04" in res["scan_results"]
    assert res["scan_results"]["04"]["success"] is True
    # 05 and 06 should not have been launched concurrently
    assert res["scan_results"].get("05", {}).get("success", False) is False
    assert res["scan_results"].get("06", {}).get("success", False) is False
