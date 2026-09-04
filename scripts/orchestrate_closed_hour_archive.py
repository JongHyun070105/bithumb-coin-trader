#!/usr/bin/env python3
"""Unattended closed-hour archive orchestrator and detached transient full-scan launcher.

Hardened against:
1. Deviation A: Ownership violations on runtime files and locks (Fail-Closed, no silent chown).
2. Deviation B: SSM interactive session timeouts via detached transient systemd units and linear streaming parser.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

# Ensure package and script imports succeed
ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
SCRIPTS_DIR = ROOT / "scripts"
for d in (SRC_DIR, SCRIPTS_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

from bithumb_coin_trader.pre_soak_archive import (
    ArchivePipeline,
    ArchiveState,
    FileArchiveStore,
    OwnershipViolationError,
    S3ArchiveStore,
    is_closed_stable_partition,
    verify_runtime_ownership,
)
from bithumb_coin_trader.microstructure_storage import RawMicrostructureStorage


# Global full-scan kernel flock and metadata constants
FULL_SCAN_GLOBAL_LOCK_NAME = ".full_scan_runner.lock"
FULL_SCAN_METADATA_NAME = ".full_scan_runner.json"
DEFAULT_SCAN_TIMEOUT_SECONDS = 1800.0
SCAN_GRACE_KILL_SECONDS = 5.0


def is_global_full_scan_running(receipt_root: Path) -> bool:
    """Check whether any full-scan runner process currently holds the global kernel flock."""
    lock_file = receipt_root / FULL_SCAN_GLOBAL_LOCK_NAME
    if not lock_file.exists():
        return False
    try:
        fd = os.open(str(lock_file), os.O_RDWR)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Acquired -> no active process held it! Unlock and return False
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except (BlockingIOError, OSError):
            # Held by an active process
            return True
    finally:
        os.close(fd)


def read_full_scan_metadata(receipt_root: Path) -> Optional[Dict[str, Any]]:
    """Read full-scan runner observability metadata."""
    meta_path = receipt_root / FULL_SCAN_METADATA_NAME
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def verify_process_identity(
    pid: int,
    expected_start_time: Optional[str] = None,
    expected_cmd_substr: str = "audit_raw_integrity_offline",
) -> bool:
    """Check if PID is alive and matches expected scanner identity."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False

    cmd = ""
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if proc_cmdline.exists():
        try:
            cmd = proc_cmdline.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    else:
        # Fallback for systems without /proc (such as macOS dev environments)
        try:
            res = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            if res.returncode == 0:
                cmd = res.stdout
        except Exception:
            pass

    if expected_cmd_substr and expected_cmd_substr not in cmd:
        return False

    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists() and expected_start_time:
        try:
            stat_data = proc_stat.read_text(encoding="utf-8")
            fields = stat_data.split()
            if len(fields) > 21:
                starttime = fields[21]
                if str(starttime) != str(expected_start_time):
                    return False
        except OSError:
            pass

    return True


class OrchestratorConcurrencyError(RuntimeError):
    """Raised when another orchestrator process holds the exclusive lock."""
    pass


@contextmanager
def orchestrator_lock(lock_path: Path, expected_owner: Optional[str] = None) -> Iterator[None]:
    """Acquire exclusive flock for orchestrator execution."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        verify_runtime_ownership((lock_path,), expected_owner=expected_owner)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            raise OrchestratorConcurrencyError(
                f"Another orchestrator instance holds lock on {lock_path}"
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def load_active_paths(metrics_path: Path, raw_root: Path) -> Tuple[Path, ...]:
    """Read collector active partition files to strictly exclude them from archive."""
    try:
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return ()
    values = payload.get("active_partition_files", []) if isinstance(payload, dict) else []
    if not isinstance(values, list):
        return ()
    active = []
    resolved_root = raw_root.resolve()
    for value in values:
        if not isinstance(value, str):
            continue
        candidate = (resolved_root / value).resolve()
        if resolved_root in candidate.parents:
            active.append(candidate)
    return tuple(active)


def is_systemd_available() -> bool:
    """Check if systemd-run and systemctl are available and systemd is the init system."""
    if shutil.which("systemd-run") is None or shutil.which("systemctl") is None:
        return False
    try:
        res = subprocess.run(
            ["systemctl", "is-system-running"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        return res.returncode in (0, 1)  # running, degraded, etc.
    except Exception:
        return False


def is_unit_active(unit_name: str) -> bool:
    """Check if a systemd unit is currently active or running."""
    try:
        res = subprocess.run(
            ["systemctl", "is-active", "--quiet", unit_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        return res.returncode == 0
    except Exception:
        return False


def compute_backlog_metrics(
    raw_root: Path,
    receipt_root: Path,
    active_paths: Sequence[Path],
    now: datetime,
    grace_period: timedelta,
    closed_files: Sequence[Path],
    hours_seen: Sequence[str],
) -> Dict[str, Any]:
    """Calculate archive and full-scan backlog and age metrics."""
    pending_archive_jobs = 0
    oldest_pending_age_seconds: Optional[float] = None
    resolved_raw_root = raw_root.resolve()

    for p in closed_files:
        rel = p.resolve().relative_to(resolved_raw_root)
        receipt_file = receipt_root / rel.parent / (rel.name + ".archive-receipt.json")
        is_done = False
        if receipt_file.exists():
            try:
                rec_data = json.loads(receipt_file.read_text(encoding="utf-8"))
                if rec_data.get("cleanup_eligible") or rec_data.get("state") in (
                    ArchiveState.CLEANUP_ELIGIBLE.value,
                    ArchiveState.VERIFIED.value,
                    ArchiveState.CLEANED.value,
                ):
                    is_done = True
            except Exception:
                is_done = False
        if not is_done:
            pending_archive_jobs += 1
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
                age = (now - mtime).total_seconds()
                if oldest_pending_age_seconds is None or age > oldest_pending_age_seconds:
                    oldest_pending_age_seconds = age
            except OSError:
                pass

    # Full-scan distinct metrics
    pending_full_scan_jobs = 0
    completed_full_scan_jobs = 0
    failed_full_scan_jobs = 0

    for h in hours_seen:
        report_file = receipt_root / f"full_scan_{h}_report.json"
        if not report_file.exists():
            pending_full_scan_jobs += 1
        else:
            try:
                rep = json.loads(report_file.read_text(encoding="utf-8"))
                status = rep.get("integrity", {}).get("totals", {}).get("status")
                if status == "PASS":
                    completed_full_scan_jobs += 1
                else:
                    failed_full_scan_jobs += 1
            except Exception:
                failed_full_scan_jobs += 1

    running_full_scan_jobs = 1 if is_global_full_scan_running(receipt_root) else 0

    return {
        "timestamp": now.isoformat(),
        "pending_archive_jobs": pending_archive_jobs,
        "pending_full_scan_jobs": pending_full_scan_jobs,
        "completed_full_scan_jobs": completed_full_scan_jobs,
        "failed_full_scan_jobs": failed_full_scan_jobs,
        "running_full_scan_jobs": running_full_scan_jobs,
        "oldest_pending_age_seconds": oldest_pending_age_seconds,
    }


def run_full_scan_supervisor(
    epoch: str,
    hour: str,
    base_dir: Path,
    timeout_seconds: float = DEFAULT_SCAN_TIMEOUT_SECONDS,
    grace_seconds: float = SCAN_GRACE_KILL_SECONDS,
    scanner_override_script: Optional[str] = None,
) -> int:
    """Run full-scan under exclusive global kernel flock with wall-clock timeout supervision."""
    base_dir = Path(base_dir)
    receipt_root = base_dir / "archive-receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)
    raw_root = base_dir / "raw"
    compressed_root = base_dir / "compressed"
    quarantine_root = base_dir / "quarantine"

    lock_file = receipt_root / FULL_SCAN_GLOBAL_LOCK_NAME
    meta_file = receipt_root / FULL_SCAN_METADATA_NAME
    log_file = receipt_root / f"full_scan_{hour}.log"
    report_path = receipt_root / f"full_scan_{hour}_report.json"

    # 1. Acquire exclusive non-blocking kernel flock
    try:
        lock_fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        print(f"[SUPERVISOR] Failed to open lock file {lock_file}: {exc}", file=sys.stderr)
        return 1

    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        os.close(lock_fd)
        print(f"[SUPERVISOR] Another full-scan is already active (flock held on {lock_file})", file=sys.stderr)
        return 1

    # 2. Write observability metadata
    try:
        meta_data = {
            "pid": os.getpid(),
            "hour": hour,
            "epoch": epoch,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "timeout_seconds": timeout_seconds,
            "status": "RUNNING",
        }
        proc_stat = Path(f"/proc/{os.getpid()}/stat")
        if proc_stat.exists():
            try:
                fields = proc_stat.read_text(encoding="utf-8").split()
                if len(fields) > 21:
                    meta_data["process_start_time"] = fields[21]
            except OSError:
                pass
        meta_file.write_text(json.dumps(meta_data, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[SUPERVISOR] Warning: failed to write metadata: {exc}", file=sys.stderr)

    # 3. Prepare child command
    if scanner_override_script is not None:
        child_cmd = [sys.executable, "-c", scanner_override_script]
    else:
        scanner_code = f"""
import json, os, sys, time
from pathlib import Path

try:
    from audit_raw_integrity_offline import full_scan, _quarantine_summary
except ImportError:
    from scripts.audit_raw_integrity_offline import full_scan, _quarantine_summary

epoch = {repr(epoch)}
hour = {repr(hour)}
base_dir = Path({repr(str(base_dir))})
raw_root = base_dir / "raw"
compressed_root = base_dir / "compressed"
quarantine_root = base_dir / "quarantine"
receipt_root = base_dir / "archive-receipts"

all_inputs = sorted(list(raw_root.glob(f"**/*_{{hour}}.jsonl")) + list(compressed_root.glob(f"**/*_{{hour}}.jsonl.zst")))
print(f"[{{time.strftime('%X')}}] [PID {{os.getpid()}}] Starting scan of {{len(all_inputs)}} files for hour {{hour}}", flush=True)
t0 = time.time()
scan_result = full_scan(all_inputs)
elapsed = time.time() - t0
quarantine_files = list(quarantine_root.glob("**/*.jsonl")) if quarantine_root.exists() else []
quarantine_result = _quarantine_summary(quarantine_files)

report = {{
    "scan": f"FULL_SCAN_{{hour}}_UTC_RAW_AND_ZSTD_PARTITIONS",
    "epoch": epoch,
    "hour": hour,
    "integrity": scan_result,
    "quarantine": quarantine_result,
    "elapsed_seconds": elapsed,
    "timestamp": time.time(),
}}
report_path = receipt_root / f"full_scan_{{hour}}_report.json"
with open(report_path, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2)
print(f"[{{time.strftime('%X')}}] Full-scan report saved to {{report_path}}, status={{scan_result['totals']['status']}} in {{elapsed:.2f}}s", flush=True)
if scan_result["totals"]["status"] != "PASS":
    sys.exit(1)
"""
        child_cmd = [sys.executable, "-c", scanner_code]

    # 4. Launch scanner subprocess with its own process group (pgid == child_proc.pid)
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{str(ROOT)}:{str(SRC_DIR)}:{str(SCRIPTS_DIR)}"
    log_fd = open(log_file, "a", encoding="utf-8")

    try:
        child_proc = subprocess.Popen(
            child_cmd,
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # setsid & separate process group
            close_fds=True,
            env=env,
        )

        deadline = time.monotonic() + timeout_seconds
        timed_out = False
        while True:
            ret = child_proc.poll()
            if ret is not None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            time.sleep(min(0.05, max(0.01, remaining)))

        if timed_out:
            log_fd.write(f"\n[SUPERVISOR] Timeout ({timeout_seconds}s) expired. Sending SIGTERM to pgid {child_proc.pid}...\n")
            log_fd.flush()
            try:
                os.killpg(child_proc.pid, signal.SIGTERM)
            except OSError:
                pass

            grace_deadline = time.monotonic() + grace_seconds
            while child_proc.poll() is None and time.monotonic() < grace_deadline:
                time.sleep(0.05)

            if child_proc.poll() is None:
                log_fd.write(f"[SUPERVISOR] Grace period expired. Escalating to SIGKILL for pgid {child_proc.pid}...\n")
                log_fd.flush()
                try:
                    os.killpg(child_proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                child_proc.wait()

            terminal_report = {
                "scan": f"FULL_SCAN_{hour}_UTC_RAW_AND_ZSTD_PARTITIONS",
                "epoch": epoch,
                "hour": hour,
                "integrity": {
                    "totals": {
                        "status": "FAIL",
                    }
                },
                "error": "TIMEOUT",
                "timeout_seconds": timeout_seconds,
                "timestamp": time.time(),
            }
            report_path.write_text(json.dumps(terminal_report, indent=2), encoding="utf-8")
            return 124

        exit_code = child_proc.returncode
        if exit_code != 0:
            needs_report = True
            if report_path.exists():
                try:
                    rep = json.loads(report_path.read_text(encoding="utf-8"))
                    if rep.get("integrity", {}).get("totals", {}).get("status") is not None:
                        needs_report = False
                except Exception:
                    pass
            if needs_report:
                terminal_report = {
                    "scan": f"FULL_SCAN_{hour}_UTC_RAW_AND_ZSTD_PARTITIONS",
                    "epoch": epoch,
                    "hour": hour,
                    "integrity": {
                        "totals": {
                            "status": "FAIL",
                        }
                    },
                    "error": f"CHILD_EXIT_{exit_code}",
                    "returncode": exit_code,
                    "timestamp": time.time(),
                }
                report_path.write_text(json.dumps(terminal_report, indent=2), encoding="utf-8")
        return exit_code

    finally:
        log_fd.close()
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)


def launch_detached_full_scan(
    epoch: str,
    hour: str,
    base_dir: Path,
    expected_owner: Optional[str] = "bitcoin-trader",
    runner_mode: str = "auto",
    timeout_seconds: float = DEFAULT_SCAN_TIMEOUT_SECONDS,
) -> Tuple[bool, str]:
    """Launch detached transient full-scan via systemd-run, detached background process, or direct."""
    base_dir = Path(base_dir)
    raw_root = base_dir / "raw"
    compressed_root = base_dir / "compressed"
    receipt_root = base_dir / "archive-receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)

    raw_files = sorted(raw_root.glob(f"**/*_{hour}.jsonl"))
    compressed_files = sorted(compressed_root.glob(f"**/*_{hour}.jsonl.zst"))

    if not raw_files and not compressed_files:
        return False, f"No files found for hour {hour}"

    # Check if this hour is already successfully verified PASS
    report_path = receipt_root / f"full_scan_{hour}_report.json"
    if report_path.exists():
        try:
            rep = json.loads(report_path.read_text(encoding="utf-8"))
            if rep.get("integrity", {}).get("totals", {}).get("status") == "PASS":
                return True, f"Full scan for hour {hour} already completed PASS"
        except Exception:
            pass

    # Concurrency 1 enforcement via global kernel flock check
    if is_global_full_scan_running(receipt_root):
        return False, "Global full scan runner is currently active (concurrency=1 enforced)"

    mode = runner_mode
    if mode == "auto":
        if is_systemd_available() and hasattr(os, "geteuid") and os.geteuid() == 0:
            mode = "systemd"
        else:
            mode = "detached"

    if mode == "direct":
        code = run_full_scan_supervisor(
            epoch=epoch,
            hour=hour,
            base_dir=base_dir,
            timeout_seconds=timeout_seconds,
        )
        if code == 0:
            return True, f"Direct scan for hour {hour} completed PASS"
        return False, f"Direct scan for hour {hour} failed with code {code}"

    if mode == "detached":
        supervisor_code = f"""
import sys
from pathlib import Path
try:
    from scripts.orchestrate_closed_hour_archive import run_full_scan_supervisor
except ImportError:
    from orchestrate_closed_hour_archive import run_full_scan_supervisor

code = run_full_scan_supervisor(
    epoch={repr(epoch)},
    hour={repr(hour)},
    base_dir=Path({repr(str(base_dir))}),
    timeout_seconds={timeout_seconds},
)
sys.exit(code)
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{str(ROOT)}:{str(SRC_DIR)}:{str(SCRIPTS_DIR)}"
        proc = subprocess.Popen(
            [sys.executable, "-c", supervisor_code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detached session
            close_fds=True,
            env=env,
        )

        # Wait briefly for supervisor to acquire flock
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if is_global_full_scan_running(receipt_root):
                break
            if proc.poll() is not None:
                return False, f"Supervisor process exited prematurely with code {proc.returncode}"
            time.sleep(0.02)

        return True, f"Launched detached background supervisor PID {proc.pid}"

    if mode == "systemd":
        unit_name = f"bitcoin-trader-full-scan-{hour}.service"
        if is_unit_active(unit_name):
            return False, f"Unit {unit_name} is already active"

        cmd = [
            "systemd-run",
            "--no-block",
            "--collect",
            f"--unit={unit_name}",
            f"--description=Detached full-scan supervisor for hour {hour} ({epoch})",
            "--service-type=exec",
            f"--uid={expected_owner or 'bitcoin-trader'}",
            "--property=Restart=no",
            "--property=KillMode=mixed",
            f"--property=RuntimeMaxSec={int(timeout_seconds + 60)}s",
            "--property=TimeoutStopSec=45s",
            f"--property=WorkingDirectory={str(ROOT)}",
            f"--property=Environment=PYTHONPATH={str(ROOT)}:{str(SRC_DIR)}:{str(SCRIPTS_DIR)}",
            sys.executable,
            "-c",
            f"""
import sys
from pathlib import Path
try:
    from scripts.orchestrate_closed_hour_archive import run_full_scan_supervisor
except ImportError:
    from orchestrate_closed_hour_archive import run_full_scan_supervisor

code = run_full_scan_supervisor(
    epoch={repr(epoch)},
    hour={repr(hour)},
    base_dir=Path({repr(str(base_dir))}),
    timeout_seconds={timeout_seconds},
)
sys.exit(code)
""",
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            err_msg = res.stderr.strip()
            if "Access denied" in err_msg or "Failed to start transient service unit" in err_msg:
                # Fallback to detached mode
                return launch_detached_full_scan(
                    epoch=epoch,
                    hour=hour,
                    base_dir=base_dir,
                    expected_owner=expected_owner,
                    runner_mode="detached",
                    timeout_seconds=timeout_seconds,
                )
            return False, f"systemd-run failed ({res.returncode}): {err_msg}"
        return True, f"Started systemd unit {unit_name}"

    return False, f"Unknown runner mode: {runner_mode}"


def orchestrate_closed_hour_archive(
    epoch: str,
    run_id: str,
    base_dir: Path,
    environment_id: str = "aws-apne2-research",
    git_commit: str = "HEAD",
    store_type: str = "file",
    file_store_root: Optional[Path] = None,
    s3_bucket: Optional[str] = None,
    allow_aws_write: bool = False,
    remote_prefix: Optional[str] = None,
    grace_seconds: int = 600,
    target_hour: Optional[str] = None,
    expected_owner: Optional[str] = None,
    scan_runner_mode: str = "auto",
    run_full_scan: bool = True,
    dry_run: bool = False,
    disk_critical_percent: float = 90.0,
    scan_timeout_seconds: float = DEFAULT_SCAN_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Execute preflight ownership check, partition archiving, and detached full-scan launch."""
    raw_root = base_dir / "raw"
    manifest_root = base_dir / "manifests"
    compressed_root = base_dir / "compressed"
    receipt_root = base_dir / "archive-receipts"
    metrics_path = base_dir / "collector_metrics.json"
    quarantine_root = base_dir / "quarantine"

    # 1. Ownership Preflight - Fail-Closed
    # Ensure all directories exist and are owned by expected_owner
    for d in (raw_root, manifest_root, compressed_root, receipt_root):
        d.mkdir(parents=True, exist_ok=True)
    verify_runtime_ownership(
        (raw_root, manifest_root, compressed_root, receipt_root),
        expected_owner=expected_owner,
    )

    # 2. Concurrency Lock - Single orchestrator instance
    lock_file = receipt_root / ".archive_orchestrator.lock"

    with orchestrator_lock(lock_file, expected_owner=expected_owner):
        now = datetime.now(timezone.utc)
        grace_period = timedelta(seconds=grace_seconds)
        active_paths = load_active_paths(metrics_path, raw_root)

        # Discovered closed files
        all_jsonl = sorted(raw_root.glob("**/*.jsonl"))
        closed_files: List[Path] = []
        hours_detected = set()

        for p in all_jsonl:
            # If target_hour is provided, filter
            if target_hour is not None and not p.name.endswith(f"_{target_hour.zfill(2)}.jsonl"):
                continue
            if is_closed_stable_partition(
                p,
                raw_root,
                now=now,
                grace_period=grace_period,
                active_paths=active_paths,
            ):
                closed_files.append(p)
                # extract hour from filename using PARTITION_PATTERN e.g. BTC_KRW_2026-09-04_05.jsonl -> 05
                from bithumb_coin_trader.pre_soak_archive import PARTITION_PATTERN
                match = PARTITION_PATTERN.search(p.name)
                if match:
                    hours_detected.add(match.group(2))

        # Initialize archive store & pipeline
        if store_type == "s3":
            if not allow_aws_write:
                raise ValueError("S3 store requires explicit allow_aws_write=True")
            if not s3_bucket:
                raise ValueError("S3 store requires s3_bucket")
            store = S3ArchiveStore(s3_bucket)
        else:
            f_root = file_store_root or (base_dir / "local-archive-fixture")
            store = FileArchiveStore(f_root)

        prefix = remote_prefix or f"market-data/temporary/{epoch}"
        pipeline = ArchivePipeline(
            raw_root=raw_root,
            manifest_root=manifest_root,
            compressed_root=compressed_root,
            receipt_root=receipt_root,
            store=store,
            environment_id=environment_id,
            run_id=run_id,
            collector_epoch=epoch,
            remote_prefix=prefix,
            compression_level=1,
            disk_critical_percent=disk_critical_percent,
            expected_owner=expected_owner,
        )

        # 3. Generate manifests for closed partitions if missing
        storage = RawMicrostructureStorage(
            raw_root,
            manifest_dir=manifest_root,
            git_commit=git_commit or "HEAD",
        )
        manifests_generated = 0
        if not dry_run:
            for p in closed_files:
                candidate = manifest_root / f"manifest_{p.stem}.json"
                if not candidate.exists():
                    storage.generate_partition_manifest(p)
                    manifests_generated += 1

        # 4. Finalize archiving for each closed partition
        archived_count = 0
        already_verified_count = 0
        failed_count = 0
        archive_errors: List[str] = []

        if not dry_run:
            for p in closed_files:
                try:
                    # Check if already verified
                    rec_path = pipeline.receipt_path(p)
                    if rec_path.exists():
                        try:
                            rec_data = json.loads(rec_path.read_text(encoding="utf-8"))
                            if rec_data.get("cleanup_eligible") or rec_data.get("state") in (
                                ArchiveState.CLEANUP_ELIGIBLE.value,
                                ArchiveState.VERIFIED.value,
                            ):
                                already_verified_count += 1
                                continue
                        except Exception:
                            pass

                    receipt = pipeline.finalize(
                        p,
                        cleanup_verified=False,  # CLEANUP_OFF
                        grace_period=grace_period,
                        active_paths=active_paths,
                    )
                    if receipt.cleanup_eligible or receipt.state == ArchiveState.CLEANUP_ELIGIBLE.value:
                        archived_count += 1
                    else:
                        failed_count += 1
                        archive_errors.append(f"{p.name}: unexpected receipt state {receipt.state}")
                except Exception as exc:
                    failed_count += 1
                    archive_errors.append(f"{p.name}: {exc}")

        # 5. Detached Full-Scan Launch
        scan_results: Dict[str, Any] = {}
        if run_full_scan and not dry_run and failed_count == 0:
            for h in sorted(hours_detected):
                ok, msg = launch_detached_full_scan(
                    epoch=epoch,
                    hour=h,
                    base_dir=base_dir,
                    expected_owner=expected_owner,
                    runner_mode=scan_runner_mode,
                    timeout_seconds=scan_timeout_seconds,
                )
                scan_results[h] = {"success": ok, "message": msg}

        # 6. Backlog metrics calculation
        sorted_hours = sorted(hours_detected)
        backlog = compute_backlog_metrics(
            raw_root=raw_root,
            receipt_root=receipt_root,
            active_paths=active_paths,
            now=now,
            grace_period=grace_period,
            closed_files=closed_files,
            hours_seen=sorted_hours,
        )

        backlog["archive_job_failures"] = failed_count
        backlog["archive_errors"] = archive_errors
        backlog["archived_count"] = archived_count
        backlog["already_verified_count"] = already_verified_count
        backlog["manifests_generated"] = manifests_generated
        backlog["hours_detected"] = sorted_hours
        backlog["scan_results"] = scan_results

        # Write backlog metrics to receipt_root
        metrics_file = receipt_root / "archive_backlog_metrics.json"
        if not dry_run:
            metrics_file.write_text(json.dumps(backlog, indent=2), encoding="utf-8")

        return backlog


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epoch", required=True, help="Collector epoch name")
    parser.add_argument("--run-id", required=True, help="Collector run ID")
    parser.add_argument("--base-dir", type=Path, help="Base directory for epoch data")
    parser.add_argument("--environment-id", default="aws-apne2-research")
    parser.add_argument("--git-commit", default="HEAD")
    parser.add_argument("--store", choices=("file", "s3"), default="file")
    parser.add_argument("--file-store-root", type=Path)
    parser.add_argument("--s3-bucket")
    parser.add_argument("--allow-aws-write", action="store_true")
    parser.add_argument("--remote-prefix")
    parser.add_argument("--grace-seconds", type=int, default=600)
    parser.add_argument("--disk-critical-percent", type=float, default=90.0)
    parser.add_argument("--hour", help="Specific closed UTC hour to process (e.g. 05)")
    parser.add_argument("--expected-owner", default="bitcoin-trader")
    parser.add_argument("--scan-runner", choices=("auto", "systemd", "detached", "direct", "none"), default="auto")
    parser.add_argument("--no-full-scan", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    base_dir = args.base_dir
    if base_dir is None:
        base_dir = Path(f"/var/lib/bitcoin-trader/{args.epoch}")

    print(f"=== ORCHESTRATE CLOSED-HOUR ARCHIVE: epoch={args.epoch} ===")
    print(f"Base Dir: {base_dir}")
    print(f"Store: {args.store}, Runner: {args.scan_runner}, Owner: {args.expected_owner}")

    try:
        res = orchestrate_closed_hour_archive(
            epoch=args.epoch,
            run_id=args.run_id,
            base_dir=base_dir,
            environment_id=args.environment_id,
            git_commit=args.git_commit,
            store_type=args.store,
            file_store_root=args.file_store_root,
            s3_bucket=args.s3_bucket,
            allow_aws_write=args.allow_aws_write,
            remote_prefix=args.remote_prefix,
            grace_seconds=args.grace_seconds,
            target_hour=args.hour,
            expected_owner=args.expected_owner,
            scan_runner_mode=args.scan_runner,
            run_full_scan=not args.no_full_scan and args.scan_runner != "none",
            dry_run=args.dry_run,
            disk_critical_percent=args.disk_critical_percent,
        )
        print(json.dumps(res, indent=2))
        if res.get("archive_job_failures", 0) > 0:
            return 1
        return 0
    except OwnershipViolationError as exc:
        print(f"FATAL OWNERSHIP VIOLATION (Fail-Closed): {exc}", file=sys.stderr)
        return 2
    except OrchestratorConcurrencyError as exc:
        print(f"CONCURRENCY BLOCKED: {exc}", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
