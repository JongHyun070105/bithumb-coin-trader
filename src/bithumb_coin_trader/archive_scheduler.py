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
import gc
import json
import os
from pathlib import Path
import sys
import threading
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
for d in (ROOT, SCRIPTS_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

from bithumb_coin_trader.archive_cohort import ArchiveCohortId
from bithumb_coin_trader.closed_hour_finalizer import SEALED_FEED_UNIVERSE
from bithumb_coin_trader.collector_state_model import (
    ArchiverHealth,
    ComponentHealthState,
    RuntimeHealthSnapshot,
    write_health_snapshot_atomic,
)
from bithumb_coin_trader.pre_soak_archive import (
    ArchiveState,
    verify_runtime_ownership,
)
from scripts.orchestrate_closed_hour_archive import (
    ARCHIVE_ORCHESTRATOR_LOCK_NAME,
    OrchestratorConcurrencyError,
    is_global_full_scan_running,
    load_active_paths,
    orchestrate_closed_hour_archive,
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
    health_path: Optional[Path] = None

    def get_health_path(self) -> Path:
        if self.health_path is not None:
            return self.health_path
        return self.base_dir / "health" / "archiver_latest.json"


@dataclass(frozen=True)
class EligibleHour:
    cohort: ArchiveCohortId
    files: List[Path]
    closed_at: datetime

    @property
    def date_str(self) -> str:
        return self.cohort.date_str

    @property
    def hour_str(self) -> str:
        return self.cohort.hour_str


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

    def _write_archiver_health(
        self,
        result: Dict[str, Any],
        eligible: List[EligibleHour],
    ) -> None:
        """Write archiver health snapshot as sidecar JSON after each run_once() cycle."""
        status = result.get("status", "IDLE")
        now = self._now_fn()
        ts = now.isoformat()

        archiver = ArchiverHealth()
        pending_cohorts = result.get("pending_cohorts", [])
        archiver.archive_queue_depth = len(pending_cohorts)

        if status == "PASS":
            archiver.status = ComponentHealthState.HEALTHY.value
            target_key = result.get("processed_cohort")
            if target_key:
                archiver.last_closed_cohort = target_key
            archiver.last_compression = ts
            archiver.last_receipt = ts
            # If S3 store was used, record the S3 put timestamp
            if self.config.store_type == "s3" and self.config.s3_bucket:
                archiver.last_s3_put = ts
        elif status == "FAIL":
            archiver.status = ComponentHealthState.DEGRADED.value
            archiver.archive_errors = 1
            res = result.get("backlog", {})
            archiver.upload_failures = res.get("archive_job_failures", 0)
        elif status == "ERROR":
            archiver.status = ComponentHealthState.FAILED.value
            archiver.archive_errors = 1
        elif status == "IDLE":
            # No progress: check if stale (no progress for >600s)
            # Only set STALE if there are pending cohorts but nothing is happening
            if pending_cohorts:
                archiver.status = ComponentHealthState.STALE.value
            else:
                archiver.status = ComponentHealthState.HEALTHY.value
        elif status in ("STOPPED", "LOCKED"):
            archiver.status = ComponentHealthState.DEGRADED.value
        else:
            archiver.status = ComponentHealthState.UNKNOWN.value

        snapshot = RuntimeHealthSnapshot(
            epoch=self.config.epoch,
            run_id=self.config.run_id,
            observed_at=ts,
            archiver=archiver,
        )
        try:
            write_health_snapshot_atomic(self.config.get_health_path(), snapshot)
        except Exception:
            pass  # Health writes must never crash the scheduler

    def is_full_scan_running(self) -> bool:
        return is_global_full_scan_running(self.config.receipt_root)

    def is_orchestrator_running(self) -> bool:
        lock_file = self.config.receipt_root / ARCHIVE_ORCHESTRATOR_LOCK_NAME
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

    def _full_scan_report_path(self, cohort: ArchiveCohortId) -> Path:
        return self.config.receipt_root / f"full_scan_{cohort.key}_report.json"

    def _full_scan_passed(self, cohort: ArchiveCohortId) -> bool:
        if not self.config.run_full_scan:
            return True
        scan_rep = self._full_scan_report_path(cohort)
        if not scan_rep.exists():
            return False
        try:
            s_data = json.loads(scan_rep.read_text(encoding="utf-8"))
            s_status = s_data.get("status") or s_data.get("integrity", {}).get("totals", {}).get("status")
            return s_data.get("cohort") == cohort.key and s_status == "PASS"
        except Exception:
            return False

    def has_cohort_failed(self, cohort: ArchiveCohortId) -> bool:
        cohort_report = self.config.receipt_root / f"cohort_{cohort.key}_finalized.json"
        if cohort_report.exists():
            try:
                data = json.loads(cohort_report.read_text(encoding="utf-8"))
                if data.get("cohort") == cohort.key and data.get("status") != "PASS":
                    return True
            except Exception:
                return True

        report_path = self._full_scan_report_path(cohort)
        if not report_path.exists():
            return False
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
            status = data.get("status") or data.get("integrity", {}).get("totals", {}).get("status")
            return data.get("cohort") != cohort.key or status != "PASS"
        except Exception:
            return True

    def is_cohort_completed(self, cohort: ArchiveCohortId) -> bool:
        # V3 check: if frozen journal exists
        journal_file = self.config.base_dir / "coverage" / "journals" / f"journal_{cohort.key}.json"
        if journal_file.exists():
            report_path = self.config.receipt_root / f"cohort_{cohort.key}_finalized.json"
            if report_path.exists():
                try:
                    data = json.loads(report_path.read_text(encoding="utf-8"))
                    if data.get("cohort") == cohort.key:
                        st = data.get("status")
                        if st in ("SKIPPED_NON_QUALIFYING", "INELIGIBLE_PARTIAL"):
                            return True
                        if st != "PASS":
                            return False
                        return self._full_scan_passed(cohort)
                except Exception:
                    return False
            # Check coverage receipts directly
            cov_receipt_dir = self.config.receipt_root / "coverage"
            if cov_receipt_dir.exists():
                all_found = True
                for feed in SEALED_FEED_UNIVERSE:
                    rec_file = (
                        cov_receipt_dir
                        / cohort.key
                        / feed.exchange
                        / feed.stream
                        / f"{feed.market}.coverage.json.archive-receipt.json"
                    )
                    if not rec_file.exists():
                        all_found = False
                        break
                    try:
                        rec_data = json.loads(rec_file.read_text(encoding="utf-8"))
                        if not rec_data.get("restore_verified_at") or rec_data.get("state") == ArchiveState.FAILED.value:
                            all_found = False
                            break
                        cov_file = (
                            self.config.base_dir
                            / "coverage"
                            / cohort.key
                            / feed.exchange
                            / feed.stream
                            / f"{feed.market}.coverage.json"
                        )
                        if cov_file.exists():
                            cov_data = json.loads(cov_file.read_text(encoding="utf-8"))
                            if cov_data.get("coverage_state") == "FAILED":
                                all_found = False
                                break
                    except Exception:
                        all_found = False
                        break
                if all_found:
                    return self._full_scan_passed(cohort)
            return False

        matching_files = []
        for path in self.config.raw_root.glob("**/*.jsonl"):
            try:
                if ArchiveCohortId.from_partition_name(path.name) == cohort:
                    matching_files.append(path)
            except ValueError:
                continue
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
                if data.get("cohort") != cohort.key:
                    return False
                if not (data.get("cleanup_eligible") or data.get("state") in (
                    ArchiveState.CLEANUP_ELIGIBLE.value,
                    ArchiveState.RESTORE_VERIFIED.value,
                )):
                    return False
            except Exception:
                return False

        return self._full_scan_passed(cohort)

    def discover_eligible_hours(self, now: Optional[datetime] = None) -> List[EligibleHour]:
        current_now = now or self._now_fn()
        active_paths = load_active_paths(self.config.metrics_path, self.config.raw_root)
        active_set = {p.resolve() for p in active_paths}

        journals_dir = self.config.base_dir / "coverage" / "journals"
        v3_journals = sorted(journals_dir.glob("journal_*.json")) if journals_dir.exists() else []

        if v3_journals:
            # V3 journal-driven discovery
            active_cohort_keys = set()
            for p in active_paths:
                try:
                    active_cohort_keys.add(ArchiveCohortId.from_partition_name(p.name).key)
                except ValueError:
                    pass

            eligible: List[EligibleHour] = []
            for jf in v3_journals:
                cohort_key = jf.stem.replace("journal_", "")
                try:
                    d_str, h_str = cohort_key.split("_")
                    cohort = ArchiveCohortId(d_str, h_str)
                except ValueError:
                    continue

                if self.is_cohort_completed(cohort):
                    continue

                # Active check: skip if currently active cohort
                if cohort.key in active_cohort_keys:
                    continue

                try:
                    closed_at = datetime.fromisoformat(
                        f"{cohort.date_str}T{cohort.hour_str}:00:00+00:00"
                    ) + timedelta(hours=1)
                except ValueError:
                    continue

                # Defect A: Enforce 600-second grace period past hour closure
                if current_now < closed_at + timedelta(seconds=self.config.grace_seconds):
                    continue

                verify_runtime_ownership((jf,), expected_owner=self.config.expected_owner)

                matching_files = []
                for p in self.config.raw_root.glob("**/*.jsonl"):
                    try:
                        if ArchiveCohortId.from_partition_name(p.name) == cohort:
                            matching_files.append(p)
                    except ValueError:
                        continue

                if matching_files:
                    verify_runtime_ownership(tuple(matching_files), expected_owner=self.config.expected_owner)

                eligible.append(EligibleHour(
                    cohort=cohort,
                    files=matching_files,
                    closed_at=closed_at,
                ))

            eligible.sort(key=lambda e: e.cohort)
            return eligible

        # Legacy RAW discovery
        if self.config.raw_root.exists():
            verify_runtime_ownership((self.config.raw_root,), expected_owner=self.config.expected_owner)

        grouped: Dict[ArchiveCohortId, List[Path]] = {}
        for p in sorted(self.config.raw_root.glob("**/*.jsonl")):
            try:
                cohort = ArchiveCohortId.from_partition_name(p.name)
            except ValueError:
                continue
            grouped.setdefault(cohort, []).append(p)

        eligible = []
        for cohort, files in grouped.items():
            # 1. Check if hour is completed
            if self.is_cohort_completed(cohort):
                continue

            # 2. Check if currently active (any partition in this hour is in active_paths)
            if any(f.resolve() in active_set for f in files):
                continue

            # 3. Check closed timestamp + grace
            try:
                closed_at = datetime.fromisoformat(
                    f"{cohort.date_str}T{cohort.hour_str}:00:00+00:00"
                ) + timedelta(hours=1)
            except ValueError:
                continue

            grace_deadline = closed_at + timedelta(seconds=self.config.grace_seconds)
            if current_now < grace_deadline:
                continue

            # 4. Check ownership of files
            verify_runtime_ownership(tuple(files), expected_owner=self.config.expected_owner)

            eligible.append(EligibleHour(
                cohort=cohort,
                files=files,
                closed_at=closed_at,
            ))

        # Sort oldest first (chronological order)
        eligible.sort(key=lambda e: e.cohort)
        return eligible

    def run_once(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        if self._stop_event.is_set():
            result: Dict[str, Any] = {
                "status": "STOPPED",
                "processed_cohort": None,
                "pending_cohorts": [],
                "timestamp": (now or self._now_fn()).isoformat(),
            }
            self._write_archiver_health(result, [])
            return result
        eligible = self.discover_eligible_hours(now=now)
        if not eligible:
            result = {
                "status": "IDLE",
                "processed_cohort": None,
                "pending_cohorts": [],
                "timestamp": (now or self._now_fn()).isoformat(),
            }
            self._write_archiver_health(result, eligible)
            return result

        target = eligible[0]
        pending_cohorts = [e.cohort.key for e in eligible]

        if self._stop_event.is_set():
            result = {
                "status": "STOPPED",
                "processed_cohort": None,
                "pending_cohorts": pending_cohorts,
                "timestamp": (now or self._now_fn()).isoformat(),
            }
            self._write_archiver_health(result, eligible)
            return result

        # Check concurrency locks: orchestrator or full-scan
        if self.is_orchestrator_running() or (self.config.run_full_scan and self.is_full_scan_running()):
            result = {
                "status": "LOCKED",
                "processed_cohort": None,
                "target_cohort": target.cohort.key,
                "pending_cohorts": pending_cohorts,
                "timestamp": (now or self._now_fn()).isoformat(),
            }
            self._write_archiver_health(result, eligible)
            return result

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
                target_cohort=target.cohort,
                expected_owner=cfg.expected_owner,
                scan_runner_mode=cfg.scan_runner_mode,
                run_full_scan=cfg.run_full_scan,
                dry_run=cfg.dry_run,
                disk_critical_percent=cfg.disk_critical_percent,
                now=(now or self._now_fn()),
            )
            archive_failures = res.get("archive_job_failures", 0)
            res_status = res.get("status")
            if res_status in ("SKIPPED_NON_QUALIFYING", "INELIGIBLE_PARTIAL", "WAITING_FOR_GRACE"):
                status = res_status
            else:
                status = "PASS" if archive_failures == 0 else "FAIL"
            result = {
                "status": status,
                "processed_cohort": target.cohort.key,
                "pending_cohorts": [e.cohort.key for e in eligible[1:]],
                "backlog": res,
                "timestamp": (now or self._now_fn()).isoformat(),
            }
            self._write_archiver_health(result, eligible)
            return result
        except OrchestratorConcurrencyError:
            result = {
                "status": "LOCKED",
                "processed_cohort": None,
                "target_cohort": target.cohort.key,
                "pending_cohorts": pending_cohorts,
                "timestamp": (now or self._now_fn()).isoformat(),
            }
            self._write_archiver_health(result, eligible)
            return result
        except Exception as exc:
            result = {
                "status": "ERROR",
                "processed_cohort": target.cohort.key,
                "error": str(exc),
                "pending_cohorts": pending_cohorts,
                "timestamp": (now or self._now_fn()).isoformat(),
            }
            self._write_archiver_health(result, eligible)
            return result

    def run_loop(
        self,
        stop_event: Optional[threading.Event] = None,
        max_iterations: Optional[int] = None,
    ) -> None:
        event = stop_event or self._stop_event
        iterations = 0
        while not event.is_set():
            self.run_once()
            gc.collect()
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            event.wait(timeout=self.config.poll_interval_seconds)
