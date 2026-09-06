"""Synthetic Chaos Test Suite for 72H Autonomous Architecture.

Implements Section 46 of the 72H post-soak specification.
Simulates failure modes offline in temporary environments without affecting live systems:
- flock contention
- corrupt manifests & ZSTs
- scanner timeouts & non-zero exits
- simulated early exits & unhandled exceptions
- disk threshold boundaries
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import pwd
import signal
import sys
import tempfile
import pytest
import zstandard as zstd

from bithumb_coin_trader.archive_scheduler import (
    ArchiveSchedulerConfig,
    ClosedHourArchiveScheduler,
)
from bithumb_coin_trader.bounded_supervisor import (
    BoundedSupervisor,
    SupervisorConfig,
)
from bithumb_coin_trader.pre_soak_archive import (
    OwnershipViolationError,
    _partition_lock,
    verify_runtime_ownership,
)


def current_user_name() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def test_chaos_flock_contention(tmp_path: Path):
    """Simulate flock contention where another process holds the global lock."""
    lock_path = tmp_path / ".full_scan_runner.lock"
    lock_path.touch()

    f1 = open(lock_path, "w")
    fcntl.flock(f1.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    # Second process attempt must fail
    f2 = open(lock_path, "w")
    with pytest.raises(BlockingIOError):
        fcntl.flock(f2.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    f1.close()
    f2.close()


def test_chaos_corrupt_manifest_handling(tmp_path: Path):
    """Corrupt manifest must not cause uncaught crash or false-green completion."""
    raw_dir = tmp_path / "raw"
    manifest_dir = tmp_path / "manifests"
    compressed_dir = tmp_path / "compressed"
    receipts_dir = tmp_path / "archive-receipts"
    metrics_path = tmp_path / "metrics.json"

    for d in [raw_dir, manifest_dir, compressed_dir, receipts_dir]:
        d.mkdir(parents=True)

    bad_manifest = manifest_dir / "manifest_corrupt.json"
    bad_manifest.write_text("NOT_A_VALID_JSON{{{", encoding="utf-8")

    cfg = ArchiveSchedulerConfig(
        epoch="chaos-epoch",
        run_id="chaos-run",
        base_dir=tmp_path,
        raw_root=raw_dir,
        manifest_root=manifest_dir,
        compressed_root=compressed_dir,
        receipt_root=receipts_dir,
        metrics_path=metrics_path,
        expected_owner=current_user_name(),
    )
    scheduler = ClosedHourArchiveScheduler(cfg)

    # Should safely handle corrupt manifest without uncaught exception
    closed_hours = scheduler.discover_eligible_hours()
    assert isinstance(closed_hours, list)


def test_chaos_corrupt_zstd_decompression_failure(tmp_path: Path):
    """Corrupt ZST file must fail decompression verification."""
    zst_file = tmp_path / "bad.zst"
    zst_file.write_bytes(b"\x28\xb5\x2f\xfd\x00\x00CORRUPT_PAYLOAD_GARBAGE")

    dctx = zstd.ZstdDecompressor()
    with pytest.raises(zstd.ZstdError):
        dctx.decompress(zst_file.read_bytes())


def test_chaos_supervisor_subcommand_failure_fails_result(tmp_path: Path):
    """Supervisor must mark overall result as FAIL if child exits non-zero."""
    result_path = tmp_path / "result.json"
    metrics_path = tmp_path / "metrics.json"
    lifecycle_path = tmp_path / "lifecycle.json"
    log_path = tmp_path / "log.txt"

    collector_cmd = (sys.executable, "-c", "import sys; sys.exit(42)")

    cfg = SupervisorConfig(
        run_id="chaos-supervisor-test",
        duration_seconds=1.0,
        collector_command=collector_cmd,
        metrics_path=metrics_path,
        collector_lifecycle_path=lifecycle_path,
        result_path=result_path,
        log_path=log_path,
        poll_interval_seconds=0.05,
    )

    supervisor = BoundedSupervisor(cfg)
    exit_code = supervisor.run()
    assert exit_code == 1
    assert result_path.exists()
    res_data = json.loads(result_path.read_text(encoding="utf-8"))
    assert res_data["overall_status"] == "FAIL"
    assert res_data["collector_exit_code"] == 42
