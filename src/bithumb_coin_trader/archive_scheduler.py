"""Autonomous closed-hour archive scheduler for unattended 72-hour soaks.

Discovers eligible closed hours, enforces:
1. Active partition exclusion (never archives active hour)
2. 600-second grace past hour closure before eligibility
3. Oldest-first serial processing (concurrency=1)
4. Idempotency (completed hours never duplicated)
5. Fail-closed on ownership violations
6. Observable backlog metrics
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
for d in (ROOT, SCRIPTS_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

from bithumb_coin_trader.pre_soak_archive import (
    ArchiveState,
    OwnershipViolationError,
    PARTITION_PATTERN,
    verify_runtime_ownership,
)
from scripts.orchestrate_closed_hour_archive import (
    FULL_SCAN_GLOBAL_LOCK_NAME,
    OrchestratorConcurrencyError,
    is_global_full_scan_running,
    load_active_paths,
    orchestrate_closed_hour_archive,
    orchestrator_lock,
)


@dataclass(frozen=True)
class ArchiveSchedulerConfig:
    epoch: str
    run_id: str
    base_dir: Path
    raw_root: Path
    manifest_root: Path
    compressed_root: Path
    receipt_root: Path
    metrics_path: Path
    poll_interval_seconds: float = 30.0
    grace_seconds: int = 600
    expected_owner: Optional[str] = None
    environment_id: str = "aws-apne2-research"
    git_commit: str = "HEAD"
    store_type: str = "file"
    file_store_root: Optional[Path] = None
    s3_bucket: Optional[str] = None
    allow_aws_write: bool = False
    remote_prefix: Optional[str] = None
    scan_runner_mode: str = "auto"
    run_full_scan: bool = True
    disk_critical_percent: float = 90.0
    dry_run: bool = False


@dataclass(frozen=True)
class EligibleHour:
    date_str: str
    hour_str: str
    files: List[Path]
    closed_at: datetime


class ClosedHourArchiveScheduler:
    def __init__(
        self,
        config: ArchiveSchedulerConfig,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.config = config
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def is_full_scan_running(self) -> bool:
        return is_global_full_scan_running(self.config.receipt_root)

    def is_orchestrator_running(self) -> bool:
        lock_file = self.config.receipt_root / ".orchestrator.lock"
        if not lock_file.exists():
            return False
        try:
            fd = os.open(str(lock_file), os.O_RDWR)
        except OSError:
            return False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                return False
            except (BlockingIOError, OSError):
                return True
        finally:
            os.close(fd)

    def has_hour_failed(self, hour_str: str) -> bool:
        report_path = self.config.receipt_root / f"full_scan_{hour_str.zfill(2)}_report.json"
        if not report_path.exists():
            return False
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
            return data.get("status") != "PASS"
        except Exception:
            return True

    def is_hour_completed(self, hour_str: str) -> bool:
        hour_suffix = f"_{hour_str.zfill(2)}.jsonl"
        matching_files = [p for p in self.config.raw_root.glob("**/*.jsonl") if p.name.endswith(hour_suffix)]
        if not matching_files:
            return False

        for p in matching_files:
            try:
                rel = p.relative_to(self.config.raw_root)
                rec_path = self.config.receipt_root / rel.parent / f"{rel.name}.archive-receipt.json"
            except ValueError:
                rec_path = self.config.receipt_root / f"{p.name}.archive-receipt.json"

            if not rec_path.exists():
                rec_path = self.config.receipt_root / f"{p.name}.archive-receipt.json"
                if not rec_path.exists():
                    return False
            try:
                data = json.loads(rec_path.read_text(encoding="utf-8"))
                if not (data.get("cleanup_eligible") or data.get("state") in (
                    ArchiveState.CLEANUP_ELIGIBLE.value,
                    ArchiveState.VERIFIED.value,
                )):
                    return False
            except Exception:
                return False

        if self.config.run_full_scan:
            report_path = self.config.receipt_root / f"full_scan_{hour_str.zfill(2)}_report.json"
            if not report_path.exists():
                return False
            try:
                data = json.loads(report_path.read_text(encoding="utf-8"))
                if data.get("status") != "PASS":
                    return False
            except Exception:
                return False

        return True

    def discover_eligible_hours(self, now: Optional[datetime] = None) -> List[EligibleHour]:
        current_now = now or self._now_fn()
        active_paths = load_active_paths(self.config.metrics_path, self.config.raw_root)
        active_set = {p.resolve() for p in active_paths}

        # Verify ownership of raw root
        if self.config.raw_root.exists():
            verify_runtime_ownership((self.config.raw_root,), expected_owner=self.config.expected_owner)

        grouped: Dict[tuple[str, str], List[Path]] = {}
        for p in sorted(self.config.raw_root.glob("**/*.jsonl")):
            match = PARTITION_PATTERN.search(p.name)
            if not match:
                continue
            date_str = match.group(1)
            hour_str = match.group(2)
            grouped.setdefault((date_str, hour_str), []).append(p)

        eligible: List[EligibleHour] = []
        for (date_str, hour_str), files in grouped.items():
            # 1. Check if hour is completed
            if self.is_hour_completed(hour_str):
                continue

            # 2. Check if currently active (any partition in this hour is in active_paths)
            if any(f.resolve() in active_set for f in files):
                continue

            # 3. Check closed timestamp + grace
            try:
                closed_at = datetime.fromisoformat(f"{date_str}T{hour_str}:00:00+00:00") + timedelta(hours=1)
            except ValueError:
                continue

            grace_deadline = closed_at + timedelta(seconds=self.config.grace_seconds)
            if current_now < grace_deadline:
                continue

            # 4. Check ownership of files
            verify_runtime_ownership(tuple(files), expected_owner=self.config.expected_owner)

            eligible.append(EligibleHour(
                date_str=date_str,
                hour_str=hour_str,
                files=files,
                closed_at=closed_at,
            ))

        # Sort oldest first (chronological order)
        eligible.sort(key=lambda e: (e.date_str, e.hour_str))
        return eligible

    def run_once(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        eligible = self.discover_eligible_hours(now=now)
        if not eligible:
            return {
                "status": "IDLE",
                "processed_hour": None,
                "pending_hours": [],
                "timestamp": (now or self._now_fn()).isoformat(),
            }

        target = eligible[0]
        pending_hours = [e.hour_str for e in eligible]

        # Check concurrency locks: orchestrator or full-scan
        if self.is_orchestrator_running() or (self.config.run_full_scan and self.is_full_scan_running()):
            return {
                "status": "LOCKED",
                "processed_hour": None,
                "target_hour": target.hour_str,
                "pending_hours": pending_hours,
                "timestamp": (now or self._now_fn()).isoformat(),
            }

        cfg = self.config
        try:
            res = orchestrate_closed_hour_archive(
                epoch=cfg.epoch,
                run_id=cfg.run_id,
                base_dir=cfg.base_dir,
                environment_id=cfg.environment_id,
                git_commit=cfg.git_commit,
                store_type=cfg.store_type,
                file_store_root=cfg.file_store_root,
                s3_bucket=cfg.s3_bucket,
                allow_aws_write=cfg.allow_aws_write,
                remote_prefix=cfg.remote_prefix,
                grace_seconds=cfg.grace_seconds,
                target_hour=target.hour_str,
                expected_owner=cfg.expected_owner,
                scan_runner_mode=cfg.scan_runner_mode,
                run_full_scan=cfg.run_full_scan,
                dry_run=cfg.dry_run,
                disk_critical_percent=cfg.disk_critical_percent,
            )
            archive_failures = res.get("archive_job_failures", 0)
            status = "PASS" if archive_failures == 0 else "FAIL"
            return {
                "status": status,
                "processed_hour": target.hour_str,
                "pending_hours": [e.hour_str for e in eligible[1:]],
                "backlog": res,
                "timestamp": (now or self._now_fn()).isoformat(),
            }
        except OrchestratorConcurrencyError:
            return {
                "status": "LOCKED",
                "processed_hour": None,
                "target_hour": target.hour_str,
                "pending_hours": pending_hours,
                "timestamp": (now or self._now_fn()).isoformat(),
            }
        except Exception as exc:
            return {
                "status": "ERROR",
                "processed_hour": target.hour_str,
                "error": str(exc),
                "pending_hours": pending_hours,
                "timestamp": (now or self._now_fn()).isoformat(),
            }

    def run_loop(
        self,
        stop_event: Optional[threading.Event] = None,
        max_iterations: Optional[int] = None,
    ) -> None:
        event = stop_event or self._stop_event
        iterations = 0
        while not event.is_set():
            self.run_once()
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            event.wait(timeout=self.config.poll_interval_seconds)
