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
from typing import Any, Sequence


SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class SupervisorConfig:
    run_id: str
    duration_seconds: float
    collector_command: tuple[str, ...]
    metrics_path: Path
    collector_lifecycle_path: Path
    result_path: Path
    log_path: Path
    publisher_command: tuple[str, ...] | None = None
    poll_interval_seconds: float = 0.2
    publisher_interval_seconds: float = 60.0
    shutdown_grace_seconds: float = 45.0
    require_full_duration: bool = False

    def __post_init__(self) -> None:
        if not SAFE_RUN_ID.fullmatch(self.run_id):
            raise ValueError("run_id must be a safe identifier")
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if not self.collector_command or any(not item for item in self.collector_command):
            raise ValueError("collector_command must be non-empty")
        if self.publisher_command is not None and any(not item for item in self.publisher_command):
            raise ValueError("publisher_command entries must be non-empty")
        if self.poll_interval_seconds <= 0 or self.publisher_interval_seconds <= 0:
            raise ValueError("poll intervals must be positive")
        if self.shutdown_grace_seconds <= 0:
            raise ValueError("shutdown_grace_seconds must be positive")


@dataclass(frozen=True)
class TransientLaunchConfig:
    run_id: str
    workdir: Path
    supervisor_command: tuple[str, ...]
    supervisor_duration_seconds: int = 2700
    hard_ceiling_seconds: int = 2760
    pythonpath: str = "src"


def render_systemd_run(config: TransientLaunchConfig) -> list[str]:
    if not SAFE_RUN_ID.fullmatch(config.run_id):
        raise ValueError("run_id must be a safe identifier")
    if config.supervisor_duration_seconds != 2700:
        raise ValueError("production supervisor duration must be exactly 2700 seconds")
    if config.hard_ceiling_seconds <= config.supervisor_duration_seconds:
        raise ValueError("hard ceiling must exceed supervisor duration")
    if not config.workdir.is_absolute() or not config.supervisor_command:
        raise ValueError("workdir must be absolute and supervisor_command must be non-empty")
    unit_name = f"bitcoin-trader-short-smoke-{config.run_id}.service"
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
        f"--property=RuntimeMaxSec={config.hard_ceiling_seconds}s",
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

    def _forward_signal(self, signum: int, _frame: object = None) -> None:
        if self._received_signal is None:
            self._received_signal = signum
        collector = self._collector
        if collector is not None and collector.poll() is None:
            try:
                os.killpg(collector.pid, signum)
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
            and payload.get("schema_version") == 1
            and payload.get("collector_run_id") == self.config.run_id
            and payload.get("final_manifest_flush_observed") is True
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
        cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_descriptor = os.open(str(cfg.log_path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        started_at = _utc_iso()
        started_monotonic = time.monotonic()
        collector_exit: int | None = None
        publisher_exit: int | None = None
        publisher_failure: int | None = None
        publisher_pid: int | None = None
        publisher_started = False
        publisher_stopped_after_collector = False
        publisher: subprocess.Popen[bytes] | None = None
        forced_timeout = False
        old_handlers: dict[int, Any] = {}
        can_install_handlers = threading.current_thread() is threading.main_thread()
        try:
            if can_install_handlers:
                for signum in (signal.SIGINT, signal.SIGTERM):
                    old_handlers[signum] = signal.getsignal(signum)
                    signal.signal(signum, self._forward_signal)
            with os.fdopen(log_descriptor, "ab", buffering=0) as log_handle:
                self._collector = subprocess.Popen(
                    cfg.collector_command,
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
                collector_pid = self._collector.pid
                deadline = started_monotonic + cfg.duration_seconds
                next_publish_at = started_monotonic
                while self._collector.poll() is None:
                    now = time.monotonic()
                    if self._received_signal is not None:
                        break
                    if now >= deadline:
                        try:
                            collector_exit = self._collector.wait(timeout=cfg.shutdown_grace_seconds)
                        except subprocess.TimeoutExpired:
                            forced_timeout = True
                            self._forward_signal(signal.SIGTERM)
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
                    time.sleep(cfg.poll_interval_seconds)

                if self._collector.poll() is None:
                    try:
                        collector_exit = self._collector.wait(timeout=cfg.shutdown_grace_seconds)
                    except subprocess.TimeoutExpired:
                        forced_timeout = True
                        collector_exit = self._stop_process(self._collector, cfg.shutdown_grace_seconds)
                else:
                    collector_exit = self._collector.returncode
                if publisher is not None:
                    publisher_exit = self._stop_process(publisher, cfg.shutdown_grace_seconds)
                    publisher_stopped_after_collector = True
                    if publisher_exit not in {0, -signal.SIGTERM} and publisher_failure is None:
                        publisher_failure = publisher_exit
        finally:
            if can_install_handlers:
                for signum, handler in old_handlers.items():
                    signal.signal(signum, handler)

        ended_monotonic = time.monotonic()
        collector_pid = self._collector.pid if self._collector is not None else None
        final_metrics_valid = bool(collector_pid and self._final_metrics_valid(collector_pid))
        final_manifest_observed = self._final_manifest_observed()
        ran_long_enough = (
            not cfg.require_full_duration
            or ended_monotonic - started_monotonic >= cfg.duration_seconds - 0.05
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
        )
        overall_status = "PASS" if passed else ("INTERRUPTED" if self._received_signal else "FAIL")
        result: dict[str, object] = {
            "schema_version": 1,
            "run_id": cfg.run_id,
            "started_at": started_at,
            "ended_at": _utc_iso(),
            "duration_limit_seconds": cfg.duration_seconds,
            "elapsed_seconds": round(ended_monotonic - started_monotonic, 6),
            "supervisor_pid": os.getpid(),
            "collector_pid": collector_pid,
            "publisher_pid": publisher_pid,
            "received_signal": signal.Signals(self._received_signal).name if self._received_signal else None,
            "collector_exit_code": collector_exit,
            "publisher_exit_code": publisher_failure if publisher_failure is not None else publisher_exit,
            "publisher_started": publisher_started,
            "publisher_stopped_after_collector": publisher_stopped_after_collector,
            "final_metrics_valid": final_metrics_valid,
            "final_manifest_flush_observed": final_manifest_observed,
            "forced_timeout": forced_timeout,
            "full_duration_satisfied": ran_long_enough,
            "overall_status": overall_status,
        }
        _atomic_json(cfg.result_path, result)
        if passed:
            return 0
        if self._received_signal is not None:
            return 128 + self._received_signal
        return 1
