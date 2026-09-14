"""Bounded collector/publisher lifecycle with durable run-scoped evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bithumb_coin_trader.qualification_schedule import QualificationSchedule


SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class V3ScheduleConfig:
    required_qualifying_full_hours: int
    maximum_collection_window_seconds: int
    schedule_path: Path

    def __post_init__(self) -> None:
        if self.required_qualifying_full_hours != 30:
            raise ValueError("required_qualifying_full_hours must be 30 for V3")
        if self.maximum_collection_window_seconds != 111600:
            raise ValueError("maximum_collection_window_seconds must be 111600 for V3")
        if not self.schedule_path:
            raise ValueError("schedule_path must be provided")


@dataclass(frozen=True)
class SupervisorConfig:
    run_id: str
    collector_command: tuple[str, ...]
    metrics_path: Path
    collector_lifecycle_path: Path
    result_path: Path
    log_path: Path
    collection_duration_seconds: float | None = None
    finalization_timeout_seconds: float = 45.0
    hard_ceiling_seconds: float | None = None
    publisher_command: tuple[str, ...] | None = None
    archive_scheduler_command: tuple[str, ...] | None = None
    poll_interval_seconds: float = 0.2
    publisher_interval_seconds: float = 60.0
    shutdown_grace_seconds: float = 45.0
    require_full_duration: bool = False
    v3_schedule: V3ScheduleConfig | None = None

    def __post_init__(self) -> None:
        if not SAFE_RUN_ID.fullmatch(self.run_id):
            raise ValueError("run_id must be a safe identifier")
        if self.v3_schedule is not None and self.collection_duration_seconds is not None and self.collection_duration_seconds > 0:
            raise ValueError("SUPERVISOR_MODE_CONFLICT: cannot specify both collection_duration_seconds and v3_schedule")
        if self.v3_schedule is None and (self.collection_duration_seconds is None or self.collection_duration_seconds <= 0):
            raise ValueError("collection_duration_seconds must be positive")
        if self.finalization_timeout_seconds <= 0:
            raise ValueError("finalization_timeout_seconds must be positive")
        if self.v3_schedule is not None:
            min_ceiling = self.v3_schedule.maximum_collection_window_seconds + self.finalization_timeout_seconds
            if self.hard_ceiling_seconds is not None and self.hard_ceiling_seconds + 1e-9 < min_ceiling:
                raise ValueError("hard_ceiling_seconds must cover maximum collection window plus finalization")
        else:
            assert self.collection_duration_seconds is not None
            min_ceiling = self.collection_duration_seconds + self.finalization_timeout_seconds
            if self.hard_ceiling_seconds is not None and self.hard_ceiling_seconds + 1e-9 < min_ceiling:
                raise ValueError("hard_ceiling_seconds must cover collection plus finalization")
        if not self.collector_command or any(not item for item in self.collector_command):
            raise ValueError("collector_command must be non-empty")
        if self.publisher_command is not None and any(not item for item in self.publisher_command):
            raise ValueError("publisher_command entries must be non-empty")
        if self.archive_scheduler_command is not None and any(not item for item in self.archive_scheduler_command):
            raise ValueError("archive_scheduler_command entries must be non-empty")
        if self.poll_interval_seconds <= 0 or self.publisher_interval_seconds <= 0:
            raise ValueError("poll intervals must be positive")
        if self.shutdown_grace_seconds <= 0:
            raise ValueError("shutdown_grace_seconds must be positive")

    @property
    def effective_hard_ceiling_seconds(self) -> float:
        if self.hard_ceiling_seconds is not None:
            return self.hard_ceiling_seconds
        if self.v3_schedule is not None:
            return self.v3_schedule.maximum_collection_window_seconds + self.finalization_timeout_seconds
        assert self.collection_duration_seconds is not None
        return self.collection_duration_seconds + self.finalization_timeout_seconds


@dataclass(frozen=True)
class TransientLaunchConfig:
    run_id: str
    workdir: Path
    supervisor_command: tuple[str, ...]
    collection_duration_seconds: int | None = None
    finalization_timeout_seconds: int = 120
    supervisor_hard_ceiling_seconds: int = 2820
    systemd_runtime_max_seconds: int = 2880
    pythonpath: str = "src"
    maximum_collection_window_seconds: int | None = None


def render_systemd_run(config: TransientLaunchConfig) -> list[str]:
    if not SAFE_RUN_ID.fullmatch(config.run_id):
        raise ValueError("run_id must be a safe identifier")
    if not config.workdir.is_absolute() or not config.supervisor_command:
        raise ValueError("workdir must be absolute and supervisor_command must be non-empty")
    if config.maximum_collection_window_seconds is not None:
        if config.maximum_collection_window_seconds != 111600:
            raise ValueError("V3 maximum_collection_window_seconds must be 111600")
        if config.supervisor_hard_ceiling_seconds < (
            config.maximum_collection_window_seconds + config.finalization_timeout_seconds
        ):
            raise ValueError("supervisor hard ceiling must cover maximum collection window plus finalization")
        prefix = "bitcoin-trader-30h"
    else:
        # Legacy duration path:
        if config.collection_duration_seconds not in (2700, 7200, 108000, 259200):
            raise ValueError("production supervisor duration must be exactly 2700, 7200, 108000, or 259200 seconds")
        if config.supervisor_hard_ceiling_seconds < (
            config.collection_duration_seconds + config.finalization_timeout_seconds
        ):
            raise ValueError("supervisor hard ceiling must cover collection plus finalization")
        if config.collection_duration_seconds == 259200:
            prefix = "bitcoin-trader-72h-soak"
        elif config.collection_duration_seconds == 108000:
            prefix = "bitcoin-trader-30h"
        elif config.collection_duration_seconds == 7200:
            prefix = "bitcoin-trader-120m"
        else:
            prefix = "bitcoin-trader-short-smoke"
    if config.systemd_runtime_max_seconds <= config.supervisor_hard_ceiling_seconds:
        raise ValueError("systemd runtime max must exceed supervisor hard ceiling")
    unit_name = f"{prefix}-{config.run_id}.service"
    return [
        "systemd-run",
        f"--unit={unit_name}",
        "--no-block",
        "--collect",
        "--service-type=exec",
        "--uid=bitcoin-trader",
        f"--setenv=PYTHONPATH={config.pythonpath}",
        "--property=Restart=no",
        "--property=KillMode=mixed",
        f"--property=RuntimeMaxSec={config.systemd_runtime_max_seconds}s",
        "--property=TimeoutStopSec=55s",
        f"--working-directory={config.workdir}",
        "--",
        *config.supervisor_command,
    ]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


class BoundedSupervisor:
    def __init__(self, config: SupervisorConfig) -> None:
        self.config = config
        self._received_signal: int | None = None
        self._collector: subprocess.Popen[bytes] | None = None
        self._archive_scheduler: subprocess.Popen[bytes] | None = None

    def _forward_signal(self, signum: int, _frame: object = None) -> None:
        if self._received_signal is None:
            self._received_signal = signum
        for proc in (self._collector, self._archive_scheduler):
            if proc is not None and proc.poll() is None:
                try:
                    os.killpg(proc.pid, signum)
                except ProcessLookupError:
                    pass

    def _live_metrics_valid(self, collector_pid: int) -> bool:
        payload = _read_json(self.config.metrics_path)
        if payload is None:
            return False
        if payload.get("schema_version") != 1 or payload.get("collector_run_id") != self.config.run_id:
            return False
        if payload.get("process_id") != collector_pid:
            return False
        try:
            written = datetime.fromisoformat(str(payload["written_at"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            return False
        if written.tzinfo is None:
            return False
        age = (datetime.now(timezone.utc) - written.astimezone(timezone.utc)).total_seconds()
        return -5.0 <= age <= 30.0

    def _final_metrics_valid(self, collector_pid: int) -> bool:
        payload = _read_json(self.config.metrics_path)
        return bool(
            payload
            and payload.get("schema_version") == 1
            and payload.get("collector_run_id") == self.config.run_id
            and payload.get("process_id") == collector_pid
            and payload.get("active_partition_files") == []
        )

    def _final_manifest_observed(self) -> bool:
        payload = _read_json(self.config.collector_lifecycle_path)
        return bool(
            payload
            and payload.get("schema_version") in {1, 2}
            and payload.get("collector_run_id") == self.config.run_id
            and payload.get("final_manifest_flush_observed") is True
            and (payload.get("schema_version") == 1 or payload.get("phase") == "COMPLETE")
        )

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes], grace: float) -> int:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                return process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        return process.wait()

    def run(self) -> int:
        cfg = self.config

        schedule: QualificationSchedule | None = None
        if cfg.v3_schedule is not None:
            from bithumb_coin_trader.qualification_schedule import (
                build_qualification_schedule,
                save_schedule,
            )
            actual_utc = datetime.now(timezone.utc)
            actual_monotonic = time.monotonic()
            schedule = build_qualification_schedule(
                actual_utc=actual_utc,
                actual_mono=actual_monotonic,
                hours=cfg.v3_schedule.required_qualifying_full_hours,
                max_window=cfg.v3_schedule.maximum_collection_window_seconds,
            )
            save_schedule(schedule, cfg.v3_schedule.schedule_path)
            started_at = schedule.actual_start_utc
            started_monotonic = schedule.actual_start_monotonic
            hard_deadline = schedule.collection_stop_monotonic + cfg.finalization_timeout_seconds
        else:
            started_at = _utc_iso()
            started_monotonic = time.monotonic()
            hard_deadline = started_monotonic + cfg.effective_hard_ceiling_seconds

        cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_descriptor = os.open(str(cfg.log_path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)

        collector_exit: int | None = None
        publisher_exit: int | None = None
        publisher_failure: int | None = None
        publisher_pid: int | None = None
        publisher_started = False
        publisher_stopped_after_collector = False
        publisher: subprocess.Popen[bytes] | None = None

        archive_scheduler_exit: int | None = None
        archive_scheduler_failure: int | None = None
        archive_scheduler_pid: int | None = None
        archive_scheduler_started = False
        archive_scheduler_stopped_after_collector = False
        archive_scheduler: subprocess.Popen[bytes] | None = None

        forced_timeout = False
        old_handlers: dict[int, Any] = {}
        can_install_handlers = threading.current_thread() is threading.main_thread()
        log_opened = False
        try:
            if can_install_handlers:
                for signum in (signal.SIGINT, signal.SIGTERM):
                    old_handlers[signum] = signal.getsignal(signum)
                    signal.signal(signum, self._forward_signal)
            with os.fdopen(log_descriptor, "ab", buffering=0) as log_handle:
                log_opened = True
                self._collector = subprocess.Popen(
                    cfg.collector_command,
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
                collector_pid = self._collector.pid

                if cfg.archive_scheduler_command is not None:
                    archive_scheduler = subprocess.Popen(
                        cfg.archive_scheduler_command,
                        stdin=subprocess.DEVNULL,
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        close_fds=True,
                    )
                    self._archive_scheduler = archive_scheduler
                    archive_scheduler_pid = archive_scheduler.pid
                    archive_scheduler_started = True

                next_publish_at = started_monotonic
                while self._collector.poll() is None:
                    now = time.monotonic()
                    if self._received_signal is not None:
                        break
                    if now >= hard_deadline:
                        forced_timeout = True
                        break
                    if publisher is not None and publisher.poll() is not None:
                        publisher_exit = publisher.returncode
                        if publisher_exit != 0 and publisher_failure is None:
                            publisher_failure = publisher_exit
                        publisher = None
                        next_publish_at = now + cfg.publisher_interval_seconds
                    if (
                        cfg.publisher_command is not None
                        and publisher is None
                        and now >= next_publish_at
                        and self._live_metrics_valid(collector_pid)
                    ):
                        publisher = subprocess.Popen(
                            cfg.publisher_command,
                            stdin=subprocess.DEVNULL,
                            stdout=log_handle,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                            close_fds=True,
                        )
                        publisher_pid = publisher.pid
                        publisher_started = True

                    if archive_scheduler is not None and archive_scheduler.poll() is not None:
                        archive_scheduler_exit = archive_scheduler.returncode
                        if archive_scheduler_exit != 0 and archive_scheduler_failure is None:
                            archive_scheduler_failure = archive_scheduler_exit

                    time.sleep(cfg.poll_interval_seconds)

                if self._collector.poll() is None:
                    collector_exit = self._stop_process(self._collector, cfg.shutdown_grace_seconds)
                else:
                    collector_exit = self._collector.returncode
                if publisher is not None:
                    publisher_exit = self._stop_process(publisher, cfg.shutdown_grace_seconds)
                    publisher_stopped_after_collector = True
                    if publisher_exit not in {0, -signal.SIGTERM} and publisher_failure is None:
                        publisher_failure = publisher_exit
                if archive_scheduler is not None:
                    archive_scheduler_exit = self._stop_process(archive_scheduler, cfg.shutdown_grace_seconds)
                    archive_scheduler_stopped_after_collector = True
                    if archive_scheduler_exit not in {0, -signal.SIGTERM} and archive_scheduler_failure is None:
                        archive_scheduler_failure = archive_scheduler_exit
        finally:
            if not log_opened:
                try:
                    os.close(log_descriptor)
                except OSError:
                    pass
            if can_install_handlers:
                for signum, handler in old_handlers.items():
                    signal.signal(signum, handler)

        ended_monotonic = time.monotonic()
        collector_pid = self._collector.pid if self._collector is not None else None
        final_metrics_valid = bool(collector_pid and self._final_metrics_valid(collector_pid))
        final_manifest_observed = self._final_manifest_observed()
        if cfg.v3_schedule is not None and schedule is not None:
            ran_long_enough = (
                not cfg.require_full_duration
                or ended_monotonic >= schedule.collection_stop_monotonic - 0.05
            )
        else:
            ran_long_enough = (
                not cfg.require_full_duration
                or (
                    cfg.collection_duration_seconds is not None
                    and ended_monotonic - started_monotonic
                    >= cfg.collection_duration_seconds - 0.05
                )
            )
        passed = bool(
            collector_exit == 0
            and self._received_signal is None
            and not forced_timeout
            and ran_long_enough
            and final_metrics_valid
            and final_manifest_observed
            and publisher_failure is None
            and (cfg.publisher_command is None or publisher_started)
            and archive_scheduler_failure is None
            and (cfg.archive_scheduler_command is None or archive_scheduler_started)
        )
        overall_status = "PASS" if passed else ("INTERRUPTED" if self._received_signal else "FAIL")
        result: dict[str, object] = {
            "schema_version": 2,
            "run_id": cfg.run_id,
            "started_at": started_at,
            "ended_at": _utc_iso(),
            "collection_duration_seconds": cfg.collection_duration_seconds,
            "finalization_timeout_seconds": cfg.finalization_timeout_seconds,
            "hard_ceiling_seconds": cfg.effective_hard_ceiling_seconds,
            "elapsed_seconds": round(ended_monotonic - started_monotonic, 6),
            "supervisor_pid": os.getpid(),
            "collector_pid": collector_pid,
            "publisher_pid": publisher_pid,
            "archive_scheduler_pid": archive_scheduler_pid,
            "received_signal": signal.Signals(self._received_signal).name if self._received_signal else None,
            "collector_exit_code": collector_exit,
            "publisher_exit_code": publisher_failure if publisher_failure is not None else publisher_exit,
            "publisher_started": publisher_started,
            "publisher_stopped_after_collector": publisher_stopped_after_collector,
            "archive_scheduler_exit_code": archive_scheduler_failure if archive_scheduler_failure is not None else archive_scheduler_exit,
            "archive_scheduler_started": archive_scheduler_started,
            "archive_scheduler_stopped_after_collector": archive_scheduler_stopped_after_collector,
            "final_metrics_valid": final_metrics_valid,
            "final_manifest_flush_observed": final_manifest_observed,
            "forced_timeout": forced_timeout,
            "full_duration_satisfied": ran_long_enough,
            "deadline_recomputed": False,
            "overall_status": overall_status,
        }
        _atomic_json(cfg.result_path, result)
        if passed:
            return 0
        if self._received_signal is not None:
            return 128 + self._received_signal
        return 1
