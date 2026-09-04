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

    pending_full_scan_jobs = 0
    for h in hours_seen:
        report_file = receipt_root / f"full_scan_{h}_report.json"
        if not report_file.exists():
            pending_full_scan_jobs += 1

    return {
        "timestamp": now.isoformat(),
        "pending_archive_jobs": pending_archive_jobs,
        "pending_full_scan_jobs": pending_full_scan_jobs,
        "oldest_pending_age_seconds": oldest_pending_age_seconds,
    }


def launch_detached_full_scan(
    epoch: str,
    hour: str,
    base_dir: Path,
    expected_owner: Optional[str] = "bitcoin-trader",
    runner_mode: str = "auto",
) -> Tuple[bool, str]:
    """Launch detached transient full-scan via systemd-run, detached background process, or direct."""
    raw_root = base_dir / "raw"
    compressed_root = base_dir / "compressed"
    receipt_root = base_dir / "archive-receipts"
    quarantine_root = base_dir / "quarantine"

    raw_files = sorted(raw_root.glob(f"**/*_{hour}.jsonl"))
    compressed_files = sorted(compressed_root.glob(f"**/*_{hour}.jsonl.zst"))

    if not raw_files and not compressed_files:
        return False, f"No files found for hour {hour}"

    # Auto resolve runner mode
    mode = runner_mode
    if mode == "auto":
        if is_systemd_available() and hasattr(os, "geteuid") and os.geteuid() == 0:
            mode = "systemd"
        else:
            mode = "detached"

    unit_name = f"bitcoin-trader-full-scan-{hour}.service"

    if mode == "systemd":
        if is_unit_active(unit_name):
            return False, f"Unit {unit_name} is already active (concurrency=1 enforced)"

        cmd = [
            "systemd-run",
            "--no-block",
            "--collect",
            f"--unit={unit_name}",
            f"--description=Detached full-scan for hour {hour} ({epoch})",
            "--service-type=exec",
            f"--uid={expected_owner or 'bitcoin-trader'}",
            "--property=Restart=no",
            "--property=KillMode=mixed",
            "--property=RuntimeMaxSec=1800s",
            "--property=TimeoutStopSec=45s",
            f"--property=WorkingDirectory={str(ROOT)}",
            f"--property=Environment=PYTHONPATH={str(SRC_DIR)}:{str(SCRIPTS_DIR)}",
            sys.executable,
            "-c",
            f"""
import json, sys, time
from pathlib import Path
from audit_raw_integrity_offline import full_scan, _quarantine_summary

epoch = {repr(epoch)}
hour = {repr(hour)}
base_dir = Path({repr(str(base_dir))})
raw_root = base_dir / "raw"
compressed_root = base_dir / "compressed"
quarantine_root = base_dir / "quarantine"
receipt_root = base_dir / "archive-receipts"

all_inputs = sorted(list(raw_root.glob(f"**/*_{{hour}}.jsonl")) + list(compressed_root.glob(f"**/*_{{hour}}.jsonl.zst")))
print(f"[{{time.strftime('%X')}}] Starting detached scan of {{len(all_inputs)}} files for hour {{hour}}")
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
print(f"[{{time.strftime('%X')}}] Full-scan report saved to {{report_path}}, status={{scan_result['totals']['status']}}")
if scan_result["totals"]["status"] != "PASS":
    sys.exit(1)
""",
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            err_msg = res.stderr.strip()
            # If systemd-run failed due to permissions / access denied, gracefully fallback to detached
            if "Access denied" in err_msg or "Failed to start transient service unit" in err_msg:
                mode = "detached"
            else:
                return False, f"systemd-run failed ({res.returncode}): {err_msg}"
        else:
            return True, f"Started systemd unit {unit_name}"

    if mode == "detached":
        # Detached daemon process using setsid (immune to SSM session disconnect)
        pid_file = receipt_root / f".full_scan_{hour}.pid"
        if pid_file.exists():
            try:
                old_pid = int(pid_file.read_text(encoding="utf-8").strip())
                os.kill(old_pid, 0)
                return False, f"Full scan for hour {hour} already running (PID {old_pid}, concurrency=1 enforced)"
            except (OSError, ValueError):
                # Stale pid file
                pass

        log_file = receipt_root / f"full_scan_{hour}.log"
        script_code = f"""
import json, os, sys, time
from pathlib import Path
from audit_raw_integrity_offline import full_scan, _quarantine_summary

epoch = {repr(epoch)}
hour = {repr(hour)}
base_dir = Path({repr(str(base_dir))})
raw_root = base_dir / "raw"
compressed_root = base_dir / "compressed"
quarantine_root = base_dir / "quarantine"
receipt_root = base_dir / "archive-receipts"
pid_file = Path({repr(str(pid_file))})

try:
    all_inputs = sorted(list(raw_root.glob(f"**/*_{{hour}}.jsonl")) + list(compressed_root.glob(f"**/*_{{hour}}.jsonl.zst")))
    print(f"[{{time.strftime('%X')}}] [PID {{os.getpid()}}] Starting detached scan of {{len(all_inputs)}} files for hour {{hour}}", flush=True)
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
finally:
    try:
        if pid_file.exists():
            pid_file.unlink()
    except OSError:
        pass
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{str(SRC_DIR)}:{str(SCRIPTS_DIR)}"

        log_fd = open(log_file, "a", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-c", script_code],
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # setsid: completely detached from terminal/SSM pty
            close_fds=True,
            env=env,
        )
        pid_file.write_text(str(proc.pid), encoding="utf-8")
        return True, f"Launched detached background process PID {proc.pid}"

    elif mode == "direct":
        # Direct in-process or synchronous scan (used in local tests)
        try:
            from audit_raw_integrity_offline import full_scan, _quarantine_summary
        except ImportError:
            return False, "Could not import audit_raw_integrity_offline"

        all_inputs = sorted(raw_files + compressed_files)
        t0 = time.time()
        scan_result = full_scan(all_inputs)
        elapsed = time.time() - t0
        quarantine_files = list(quarantine_root.glob("**/*.jsonl")) if quarantine_root.exists() else []
        quarantine_result = _quarantine_summary(quarantine_files)

        report = {
            "scan": f"FULL_SCAN_{hour}_UTC_RAW_AND_ZSTD_PARTITIONS",
            "epoch": epoch,
            "hour": hour,
            "integrity": scan_result,
            "quarantine": quarantine_result,
            "elapsed_seconds": elapsed,
            "timestamp": time.time(),
        }
        report_path = receipt_root / f"full_scan_{hour}_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        if scan_result["totals"]["status"] != "PASS":
            return False, f"Direct full scan failed with status {scan_result['totals']['status']}: totals={scan_result['totals']}"
        return True, f"Completed direct scan in {elapsed:.2f}s: PASS"

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
