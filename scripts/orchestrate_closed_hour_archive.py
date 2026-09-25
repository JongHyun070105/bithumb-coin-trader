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
import hashlib
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
from bithumb_coin_trader.archive_cohort import ArchiveCohortId
from bithumb_coin_trader.closed_hour_finalizer import ClosedHourFinalizer
from bithumb_coin_trader.feed_hour_coverage import load_frozen_journal
from bithumb_coin_trader.incremental_finalizer import FinalizationProgressStore
from bithumb_coin_trader.microstructure_storage import RawMicrostructureStorage
from bithumb_coin_trader.session_evidence import HeartbeatPolicy


# Global full-scan kernel flock and metadata constants
ARCHIVE_ORCHESTRATOR_LOCK_NAME = ".archive_orchestrator.lock"
FULL_SCAN_GLOBAL_LOCK_NAME = ".full_scan_runner.lock"
FULL_SCAN_METADATA_NAME = ".full_scan_runner.json"
DEFAULT_SCAN_TIMEOUT_SECONDS = 1800.0
SCAN_GRACE_KILL_SECONDS = 5.0


class FinalReceiptCorruptionError(RuntimeError):
    """Raised when an existing finalized receipt is corrupted or has an identity mismatch."""
    pass


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps(payload, indent=2) + "\n"
    try:
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp_path), str(path))
        parent_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def _write_final_receipt_immutable(
    report_path: Path,
    payload: Dict[str, Any],
    cohort_key: str,
) -> tuple[Dict[str, Any], bool]:
    """Write finalized receipt exactly once with atomic create/rename semantics.

    If the receipt already exists:
    - Verifies identity and schema.
    - Fails closed on corruption or identity mismatch.
    - If valid, preserves existing receipt byte-for-byte and returns (existing_payload, False).
    If it does not exist:
    - Atomically writes payload and returns (payload, True).
    """
    if report_path.exists():
        try:
            existing_data = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise FinalReceiptCorruptionError(
                f"Existing finalized receipt is corrupted: {report_path}: {exc}"
            ) from exc

        existing_cohort = existing_data.get("cohort")
        if existing_cohort != cohort_key:
            raise FinalReceiptCorruptionError(
                f"Existing receipt cohort mismatch: expected {cohort_key}, found {existing_cohort} in {report_path}"
            )

        if "status" not in existing_data or "finalized_at_utc" not in existing_data:
            raise FinalReceiptCorruptionError(
                f"Existing receipt missing required schema fields in {report_path}"
            )

        # Existing receipt is valid and immutable: do not overwrite!
        return existing_data, False

    _atomic_write_json(report_path, payload)
    return payload, True


def _upload_json_to_store(store: Any, key: str, payload: Dict[str, Any], temp_dir: Path) -> None:
    data = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    sha256_hex = hashlib.sha256(data).hexdigest()
    tmp_path = temp_dir / f".tmp_upload_{os.getpid()}_{hashlib.md5(key.encode()).hexdigest()[:8]}.json"
    tmp_path.write_bytes(data)
    try:
        if hasattr(store, "upload"):
            store.upload(tmp_path, key, sha256_hex)
        elif hasattr(store, "put_bytes"):
            store.put_bytes(data, key)
    finally:
        tmp_path.unlink(missing_ok=True)


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
    cohorts_seen: Sequence[ArchiveCohortId],
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
                    ArchiveState.RESTORE_VERIFIED.value,
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

    for cohort in cohorts_seen:
        report_file = receipt_root / f"full_scan_{cohort.key}_report.json"
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
    run_id: str,
    cohort: ArchiveCohortId,
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
    log_file = receipt_root / f"full_scan_{cohort.key}.log"
    report_path = receipt_root / f"full_scan_{cohort.key}_report.json"

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
            "run_id": run_id,
            "cohort": cohort.key,
            "date": cohort.date_str,
            "hour": cohort.hour_str,
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
run_id = {repr(run_id)}
cohort = {repr(cohort.key)}
date_str = {repr(cohort.date_str)}
hour = {repr(cohort.hour_str)}
base_dir = Path({repr(str(base_dir))})
raw_root = base_dir / "raw"
compressed_root = base_dir / "compressed"
quarantine_root = base_dir / "quarantine"
receipt_root = base_dir / "archive-receipts"

all_inputs = sorted(list(raw_root.glob(f"**/*_{{cohort}}.jsonl")) + list(compressed_root.glob(f"**/*_{{cohort}}.jsonl.zst")))
print(f"[{{time.strftime('%X')}}] [PID {{os.getpid()}}] Starting scan of {{len(all_inputs)}} files for cohort {{cohort}}", flush=True)
t0 = time.time()
scan_result = full_scan(all_inputs)
elapsed = time.time() - t0
quarantine_files = list(quarantine_root.glob("**/*.jsonl")) if quarantine_root.exists() else []
quarantine_result = _quarantine_summary(quarantine_files)

report = {{
    "scan": f"FULL_SCAN_{{cohort}}_UTC_RAW_AND_ZSTD_PARTITIONS",
    "epoch": epoch,
    "run_id": run_id,
    "cohort": cohort,
    "date": date_str,
    "hour": hour,
    "inputs": [str(p.relative_to(base_dir)) for p in all_inputs],
    "integrity": scan_result,
    "quarantine": quarantine_result,
    "elapsed_seconds": elapsed,
    "timestamp": time.time(),
}}
report_path = receipt_root / f"full_scan_{{cohort}}_report.json"
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
                "scan": f"FULL_SCAN_{cohort.key}_UTC_RAW_AND_ZSTD_PARTITIONS",
                "epoch": epoch,
                "run_id": run_id,
                "cohort": cohort.key,
                "date": cohort.date_str,
                "hour": cohort.hour_str,
                "integrity": {
                    "totals": {
                        "status": "FAIL",
                    }
                },
                "error": "TIMEOUT",
                "timeout_seconds": timeout_seconds,
                "timestamp": time.time(),
            }
            _atomic_write_json(report_path, terminal_report)
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
                    "scan": f"FULL_SCAN_{cohort.key}_UTC_RAW_AND_ZSTD_PARTITIONS",
                    "epoch": epoch,
                    "run_id": run_id,
                    "cohort": cohort.key,
                    "date": cohort.date_str,
                    "hour": cohort.hour_str,
                    "integrity": {
                        "totals": {
                            "status": "FAIL",
                        }
                    },
                    "error": f"CHILD_EXIT_{exit_code}",
                    "returncode": exit_code,
                    "timestamp": time.time(),
                }
                _atomic_write_json(report_path, terminal_report)
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
    run_id: str,
    cohort: ArchiveCohortId,
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

    raw_files = sorted(raw_root.glob(f"**/*_{cohort.key}.jsonl"))
    compressed_files = sorted(compressed_root.glob(f"**/*_{cohort.key}.jsonl.zst"))

    if not raw_files and not compressed_files:
        return False, f"No files found for cohort {cohort.key}"

    # Check if this hour is already successfully verified PASS
    report_path = receipt_root / f"full_scan_{cohort.key}_report.json"
    if report_path.exists():
        try:
            rep = json.loads(report_path.read_text(encoding="utf-8"))
            if rep.get("integrity", {}).get("totals", {}).get("status") == "PASS":
                if rep.get("cohort") == cohort.key:
                    return True, f"Full scan for cohort {cohort.key} already completed PASS"
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
            run_id=run_id,
            cohort=cohort,
            base_dir=base_dir,
            timeout_seconds=timeout_seconds,
        )
        if code == 0:
            return True, f"Direct scan for cohort {cohort.key} completed PASS"
        return False, f"Direct scan for cohort {cohort.key} failed with code {code}"

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
    run_id={repr(run_id)},
    cohort=__import__('bithumb_coin_trader.archive_cohort', fromlist=['ArchiveCohortId']).ArchiveCohortId.parse({repr(cohort.key)}),
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
        unit_name = f"bitcoin-trader-full-scan-{cohort.key}.service"
        if is_unit_active(unit_name):
            return False, f"Unit {unit_name} is already active"

        cmd = [
            "systemd-run",
            "--no-block",
            "--collect",
            f"--unit={unit_name}",
            f"--description=Detached full-scan supervisor for cohort {cohort.key} ({epoch})",
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
    run_id={repr(run_id)},
    cohort=__import__('bithumb_coin_trader.archive_cohort', fromlist=['ArchiveCohortId']).ArchiveCohortId.parse({repr(cohort.key)}),
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
                    run_id=run_id,
                    cohort=cohort,
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
    target_cohort: Optional[ArchiveCohortId] = None,
    expected_owner: Optional[str] = None,
    scan_runner_mode: str = "auto",
    run_full_scan: bool = True,
    dry_run: bool = False,
    disk_critical_percent: float = 90.0,
    scan_timeout_seconds: float = DEFAULT_SCAN_TIMEOUT_SECONDS,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Execute preflight ownership check, partition archiving, and detached full-scan launch."""
    raw_root = base_dir / "raw"
    manifest_root = base_dir / "manifests"
    compressed_root = base_dir / "compressed"
    receipt_root = base_dir / "archive-receipts"
    metrics_path = base_dir / "collector_metrics.json"

    # 1. Ownership Preflight - Fail-Closed
    # Ensure all directories exist and are owned by expected_owner
    for d in (raw_root, manifest_root, compressed_root, receipt_root):
        d.mkdir(parents=True, exist_ok=True)
    verify_runtime_ownership(
        (raw_root, manifest_root, compressed_root, receipt_root),
        expected_owner=expected_owner,
    )

    # 2. Concurrency Lock - Single orchestrator instance
    lock_file = receipt_root / ARCHIVE_ORCHESTRATOR_LOCK_NAME

    with orchestrator_lock(lock_file, expected_owner=expected_owner):
        current_now = now or datetime.now(timezone.utc)
        grace_period = timedelta(seconds=grace_seconds)
        active_paths = load_active_paths(metrics_path, raw_root)

        journals_dir = base_dir / "coverage" / "journals"
        coverage_dir = base_dir / "coverage"

        v3_target_cohort: Optional[ArchiveCohortId] = None
        if target_cohort is not None and (journals_dir / f"journal_{target_cohort.key}.json").exists():
            v3_target_cohort = target_cohort
        elif target_cohort is None and journals_dir.exists():
            v3_files = sorted(journals_dir.glob("journal_*.json"))
            if v3_files:
                ck = v3_files[0].stem.replace("journal_", "")
                try:
                    d, h = ck.split("_")
                    v3_target_cohort = ArchiveCohortId(d, h)
                except ValueError:
                    pass

        if v3_target_cohort is not None:
            # Defect A: Enforce canonical grace period past cohort closure
            cohort_close_time = datetime.fromisoformat(
                f"{v3_target_cohort.date_str}T{v3_target_cohort.hour_str}:00:00+00:00"
            ) + timedelta(hours=1)
            if current_now < cohort_close_time + grace_period:
                return {
                    "status": "WAITING_FOR_GRACE",
                    "cohort": v3_target_cohort.key,
                    "message": f"Cohort {v3_target_cohort.key} is within grace period (closes {cohort_close_time.isoformat()}, grace {grace_seconds}s, now {current_now.isoformat()})",
                    "archive_job_failures": 0,
                    "closed_files_count": 0,
                    "archived_count": 0,
                    "already_verified_count": 0,
                    "failed_count": 0,
                    "manifests_generated": 0,
                    "archive_errors": [],
                    "scan_launched": False,
                }

            # Defect C: Check if finalized receipt already exists (immutable)
            cohort_report_path = receipt_root / f"cohort_{v3_target_cohort.key}_finalized.json"
            if cohort_report_path.exists():
                existing_data, was_written = _write_final_receipt_immutable(
                    cohort_report_path, {}, v3_target_cohort.key
                )
                existing_status = existing_data.get("status", "UNKNOWN")
                existing_failures = existing_data.get("failed_count", 0)
                return {
                    "status": existing_status,
                    "cohort": v3_target_cohort.key,
                    "cohort_qualification": existing_data.get("cohort_qualification", "UNKNOWN"),
                    "already_finalized": True,
                    "closed_files_count": existing_data.get("data_present_count", 0),
                    "archived_count": existing_data.get("data_present_count", 0) + existing_data.get("verified_zero_count", 0),
                    "already_verified_count": 0,
                    "failed_count": existing_failures,
                    "manifests_generated": 0,
                    "archive_job_failures": existing_failures,
                    "archive_errors": [f"{f}: EXISTING_FAILURE" for f in existing_data.get("failed_feeds", [])],
                    "scan_launched": False,
                    "scan_results": {},
                    "total_slots": existing_data.get("total_slots", 0),
                    "data_present_count": existing_data.get("data_present_count", 0),
                    "verified_zero_count": existing_data.get("verified_zero_count", 0),
                }

            # V3 journal-driven finalization
            journal_path = journals_dir / f"journal_{v3_target_cohort.key}.json"
            observations = load_frozen_journal(journal_path)

            # Defect B: Partial Cohort Eligibility Check
            qualifications = {obs.cohort_qualification for obs in observations}
            if not qualifications:
                raise ValueError(f"Empty observations in journal {journal_path}")

            known_qualifications = {"QUALIFYING_FULL_HOUR", "TOUCHED_PARTIAL"}
            for q in qualifications:
                if q not in known_qualifications:
                    raise ValueError(f"Unknown or ambiguous cohort qualification '{q}' in journal {journal_path}")

            is_qualifying_full_hour = (qualifications == {"QUALIFYING_FULL_HOUR"})
            if not is_qualifying_full_hour:
                # Ineligible partial cohort: do NOT submit to full-hour integrity finalization
                report_payload = {
                    "status": "SKIPPED_NON_QUALIFYING",
                    "cohort": v3_target_cohort.key,
                    "epoch": epoch,
                    "run_id": run_id,
                    "cohort_qualification": "TOUCHED_PARTIAL",
                    "total_slots": len(observations),
                    "data_present_count": 0,
                    "verified_zero_count": 0,
                    "failed_count": 0,
                    "failed_feeds": [],
                    "reason": "Cohort was touched partially and is ineligible for full-hour integrity finalization",
                    "finalized_at_utc": current_now.isoformat(),
                }
                final_data, _ = _write_final_receipt_immutable(
                    cohort_report_path, report_payload, v3_target_cohort.key
                )
                return {
                    "status": "SKIPPED_NON_QUALIFYING",
                    "cohort": v3_target_cohort.key,
                    "cohort_qualification": "TOUCHED_PARTIAL",
                    "closed_files_count": 0,
                    "archived_count": 0,
                    "already_verified_count": 0,
                    "failed_count": 0,
                    "manifests_generated": 0,
                    "archive_job_failures": 0,
                    "archive_errors": [],
                    "scan_launched": False,
                    "scan_results": {},
                    "total_slots": len(observations),
                    "data_present_count": 0,
                    "verified_zero_count": 0,
                }

            if dry_run:
                actions: list[dict[str, Any]] = []
                for obs in observations:
                    if obs.event_count > 0:
                        actions.append({
                            "action": "finalize_slot",
                            "feed": obs.feed.canonical,
                            "event_count": obs.event_count,
                            "steps": [
                                "manifest_raw",
                                "archive_raw",
                                "verify_raw_restore",
                                "materialize_data_present",
                                "archive_coverage",
                                "verify_coverage_restore",
                            ],
                        })
                    else:
                        actions.append({
                            "action": "finalize_slot",
                            "feed": obs.feed.canonical,
                            "event_count": 0,
                            "steps": [
                                "materialize_verified_zero_event",
                                "archive_coverage",
                                "verify_coverage_restore",
                            ],
                        })
                return {
                    "status": "DRY_RUN",
                    "cohort": v3_target_cohort.key,
                    "dry_run": True,
                    "actions": actions,
                    "archive_job_failures": 0,
                    "closed_files_count": len([obs for obs in observations if obs.event_count > 0]),
                    "archived_count": 0,
                    "already_verified_count": 0,
                    "failed_count": 0,
                    "manifests_generated": 0,
                    "archive_errors": [],
                    "scan_launched": False,
                }

            # Initialize archive store & pipelines
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

            coverage_archive = ArchivePipeline(
                raw_root=coverage_dir,
                manifest_root=manifest_root,
                compressed_root=compressed_root / "coverage",
                receipt_root=receipt_root / "coverage",
                store=store,
                environment_id=environment_id,
                run_id=run_id,
                collector_epoch=epoch,
                remote_prefix=f"{prefix}/coverage",
                compression_level=1,
                disk_critical_percent=disk_critical_percent,
                expected_owner=expected_owner,
            )

            progress_store = FinalizationProgressStore(base_dir / "finalization-progress")
            heartbeat_policy = HeartbeatPolicy(
                heartbeat_probe_interval_seconds=10,
                heartbeat_timeout_seconds=10,
                max_allowed_heartbeat_gap_seconds={"bithumb": 30, "binance": 30, "upbit": 30},
            )
            finalizer = ClosedHourFinalizer(
                raw_archive=pipeline,
                coverage_archive=coverage_archive,
                heartbeat_policy=heartbeat_policy,
                progress_store=progress_store,
                journals_dir=journals_dir,
                coverage_dir=coverage_dir,
                environment_id=environment_id,
                runtime_commit=git_commit or "HEAD",
                stability_wait_seconds=0.0 if "pytest" in sys.modules else 1.0,
            )
            results = finalizer.finalize_cohort(v3_target_cohort.key)
            failed_slots = [r for r in results if r.coverage.coverage_state == "FAILED"]
            data_present_slots = [r for r in results if r.coverage.coverage_state == "DATA_PRESENT"]
            verified_zero_slots = [r for r in results if r.coverage.coverage_state == "VERIFIED_ZERO_EVENT"]
            failures = len(failed_slots)
            status = "PASS" if failures == 0 else "FAIL"

            report_payload = {
                "status": status,
                "cohort": v3_target_cohort.key,
                "epoch": epoch,
                "run_id": run_id,
                "cohort_qualification": "QUALIFYING_FULL_HOUR",
                "total_slots": len(results),
                "data_present_count": len(data_present_slots),
                "verified_zero_count": len(verified_zero_slots),
                "failed_count": failures,
                "failed_feeds": [r.coverage.feed_identity for r in failed_slots],
                "finalized_at_utc": current_now.isoformat(),
            }
            final_report_data, was_written = _write_final_receipt_immutable(
                cohort_report_path, report_payload, v3_target_cohort.key
            )

            # Remote durability of cohort receipts and failure evidence
            archive_errors_list = [f"{r.coverage.feed_identity}: {list(r.failure_reason_codes)}" for r in failed_slots]
            receipt_rel_key = f"{prefix}/archive-receipts/cohort_{v3_target_cohort.key}_finalized.json"
            try:
                _upload_json_to_store(store, receipt_rel_key, final_report_data, receipt_root)
            except Exception as exc:
                archive_errors_list.append(f"FAILED_REMOTE_RECEIPT_UPLOAD: {exc}")

            if status == "FAIL":
                failure_payload = {
                    "schema_version": 1,
                    "artifact_kind": "ARCHIVE_FAILURE_EVIDENCE",
                    "epoch": epoch,
                    "run_id": run_id,
                    "cohort": v3_target_cohort.key,
                    "cohort_qualification": "QUALIFYING_FULL_HOUR",
                    "terminal_archive_state": "FAIL",
                    "failed_count": failures,
                    "total_slots": len(results),
                    "failure_reason_summary": {
                        r.coverage.feed_identity: list(r.failure_reason_codes) for r in failed_slots
                    },
                    "receipt_checksum": hashlib.sha256(json.dumps(final_report_data, sort_keys=True).encode("utf-8")).hexdigest(),
                    "captured_at_utc": current_now.isoformat(),
                }
                failure_rel_key = f"{prefix}/archive-failures/{v3_target_cohort.key}/failure_{v3_target_cohort.key}.json"
                try:
                    _upload_json_to_store(store, failure_rel_key, failure_payload, receipt_root)
                except Exception as exc:
                    archive_errors_list.append(f"FAILED_REMOTE_FAILURE_UPLOAD: {exc}")

            scan_results: Dict[str, Any] = {}
            if run_full_scan and failures == 0:
                ok, msg = launch_detached_full_scan(
                    epoch=epoch,
                    run_id=run_id,
                    cohort=v3_target_cohort,
                    base_dir=base_dir,
                    expected_owner=expected_owner,
                    runner_mode=scan_runner_mode,
                    timeout_seconds=scan_timeout_seconds,
                )
                scan_results[v3_target_cohort.key] = {"success": ok, "message": msg}

            return {
                "status": status,
                "cohort": v3_target_cohort.key,
                "closed_files_count": len(data_present_slots),
                "archived_count": len(data_present_slots) + len(verified_zero_slots),
                "already_verified_count": 0,
                "failed_count": failures,
                "manifests_generated": len(data_present_slots),
                "archive_job_failures": failures,
                "archive_errors": archive_errors_list,
                "scan_launched": len(scan_results) > 0,
                "scan_results": scan_results,
                "total_slots": len(results),
                "data_present_count": len(data_present_slots),
                "verified_zero_count": len(verified_zero_slots),
                "results": [r.coverage.to_dict() for r in results],
            }

        # Discovered closed files (legacy mode)
        all_jsonl = sorted(raw_root.glob("**/*.jsonl"))
        closed_files: List[Path] = []
        cohorts_detected: set[ArchiveCohortId] = set()

        for p in all_jsonl:
            try:
                partition_cohort = ArchiveCohortId.from_partition_name(p.name)
            except ValueError:
                continue
            if target_cohort is not None and partition_cohort != target_cohort:
                continue
            if is_closed_stable_partition(
                p,
                raw_root,
                now=now,
                grace_period=grace_period,
                active_paths=active_paths,
            ):
                closed_files.append(p)
                cohorts_detected.add(partition_cohort)

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
                                ArchiveState.RESTORE_VERIFIED.value,
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
            for cohort in sorted(cohorts_detected):
                ok, msg = launch_detached_full_scan(
                    epoch=epoch,
                    run_id=run_id,
                    cohort=cohort,
                    base_dir=base_dir,
                    expected_owner=expected_owner,
                    runner_mode=scan_runner_mode,
                    timeout_seconds=scan_timeout_seconds,
                )
                scan_results[cohort.key] = {"success": ok, "message": msg}

        # 6. Backlog metrics calculation
        sorted_cohorts = sorted(cohorts_detected)
        backlog = compute_backlog_metrics(
            raw_root=raw_root,
            receipt_root=receipt_root,
            active_paths=active_paths,
            now=current_now,
            grace_period=grace_period,
            closed_files=closed_files,
            cohorts_seen=sorted_cohorts,
        )

        backlog["archive_job_failures"] = failed_count
        backlog["archive_errors"] = archive_errors
        backlog["archived_count"] = archived_count
        backlog["already_verified_count"] = already_verified_count
        backlog["manifests_generated"] = manifests_generated
        backlog["cohorts_detected"] = [cohort.key for cohort in sorted_cohorts]
        backlog["scan_results"] = scan_results

        # Write backlog metrics to receipt_root
        metrics_file = receipt_root / "archive_backlog_metrics.json"
        if not dry_run:
            _atomic_write_json(metrics_file, backlog)

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
    parser.add_argument("--cohort", help="Specific closed UTC cohort to process (YYYY-MM-DD_HH)")
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
            target_cohort=ArchiveCohortId.parse(args.cohort) if args.cohort else None,
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
