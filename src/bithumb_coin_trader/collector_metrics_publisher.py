"""Low-frequency, fail-closed CloudWatch publisher for durable collector metrics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any, Callable, Dict, List, Optional


METRIC_NAMESPACE = "BitcoinTrader/Collector"
METRIC_DIMENSION = "EnvironmentId"


@dataclass(frozen=True)
class PublishResult:
    published: bool
    metric_names: List[str]
    reason: str
    attempts: int


class CollectorMetricPublisher:
    def __init__(
        self,
        client: Any,
        environment_id: str,
        metrics_path: Path,
        state_path: Path,
        storage_path: Path,
        ops_log_path: Path,
        max_snapshot_age_seconds: float = 30.0,
        max_attempts: int = 3,
        process_checker: Optional[Callable[[int], bool]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not environment_id or environment_id in {"UNKNOWN", "NOT-SEALED"}:
            raise ValueError("environment_id must be sealed")
        if max_attempts < 1 or max_attempts > 5:
            raise ValueError("max_attempts must be between 1 and 5")
        self.client = client
        self.environment_id = environment_id
        self.metrics_path = metrics_path
        self.state_path = state_path
        self.storage_path = storage_path
        self.ops_log_path = ops_log_path
        self.max_snapshot_age_seconds = max_snapshot_age_seconds
        self.max_attempts = max_attempts
        self.process_checker = process_checker or _process_is_running
        self.sleep = sleep

    def publish_once(self, now: Optional[datetime] = None) -> PublishResult:
        current = now or datetime.now(timezone.utc)
        snapshot, invalid_reason = self._load_valid_snapshot(current)
        if snapshot is None:
            self._append_ops_event("metric_source_invalid", invalid_reason or "unknown")
            return PublishResult(False, [], invalid_reason or "invalid source", 0)

        run_id = str(snapshot["collector_run_id"])
        current_counters = _counter_totals(snapshot)
        previous = self._load_state()
        metrics: List[Dict[str, Any]] = []
        if previous is not None and previous.get("collector_run_id") == run_id:
            previous_counters = previous.get("counters")
            if isinstance(previous_counters, dict):
                writer_delta = current_counters["writer_errors"] - int(previous_counters.get("writer_errors", 0))
                queue_delta = current_counters["queue_drops"] - int(previous_counters.get("queue_drops", 0))
                if writer_delta >= 0 and queue_delta >= 0:
                    metrics.extend(
                        [
                            self._datum("WriterErrors", writer_delta, "Count", current),
                            self._datum("QueueDrops", queue_delta, "Count", current),
                        ]
                    )
                else:
                    self._append_ops_event("metric_counter_reset", "counter decreased within collector run")

        disk_usage = shutil.disk_usage(self.storage_path)
        if disk_usage.total <= 0:
            self._append_ops_event("disk_metric_invalid", "filesystem total is not positive")
            return PublishResult(False, [], "disk source invalid", 0)
        disk_percent = 100.0 * float(disk_usage.used) / float(disk_usage.total)
        metrics.append(self._datum("DiskUsedPercent", disk_percent, "Percent", current))

        attempts = 0
        for attempts in range(1, self.max_attempts + 1):
            try:
                self.client.put_metric_data(Namespace=METRIC_NAMESPACE, MetricData=metrics)
                self._write_state(run_id, current_counters, current)
                return PublishResult(True, [item["MetricName"] for item in metrics], "published", attempts)
            except Exception as exc:
                self._append_ops_event(
                    "cloudwatch_publish_failed",
                    "attempt {}: {}".format(attempts, type(exc).__name__),
                )
                if attempts < self.max_attempts:
                    self.sleep(min(4.0, float(2 ** (attempts - 1))))
        return PublishResult(False, [], "CloudWatch PutMetricData failed", attempts)

    def _load_valid_snapshot(self, now: datetime) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        try:
            payload = json.loads(self.metrics_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None, "durable metrics snapshot missing"
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None, "durable metrics snapshot unreadable"
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return None, "durable metrics snapshot schema invalid"
        required = {"collector_run_id", "process_id", "written_at", "exchanges", "unpersisted_event_count"}
        if not required.issubset(payload) or not isinstance(payload.get("exchanges"), dict):
            return None, "durable metrics snapshot incomplete"
        try:
            written_at = datetime.fromisoformat(str(payload["written_at"]).replace("Z", "+00:00"))
            process_id = int(payload["process_id"])
        except (TypeError, ValueError):
            return None, "durable metrics snapshot metadata invalid"
        if written_at.tzinfo is None or written_at.utcoffset() is None:
            return None, "durable metrics timestamp is naive"
        age = (now.astimezone(timezone.utc) - written_at.astimezone(timezone.utc)).total_seconds()
        if age < -5 or age > self.max_snapshot_age_seconds:
            return None, "durable metrics snapshot is stale"
        if process_id <= 0 or not self.process_checker(process_id):
            return None, "collector process is not running"
        try:
            _counter_totals(payload)
        except (TypeError, ValueError):
            return None, "durable metric counters are invalid"
        return payload, None

    def _datum(self, name: str, value: float, unit: str, timestamp: datetime) -> Dict[str, Any]:
        return {
            "MetricName": name,
            "Dimensions": [{"Name": METRIC_DIMENSION, "Value": self.environment_id}],
            "Timestamp": timestamp,
            "Value": value,
            "Unit": unit,
            "StorageResolution": 60,
        }

    def _load_state(self) -> Optional[Dict[str, Any]]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError):
            self._append_ops_event("publisher_state_invalid", "state could not be read")
            return None
        return payload if isinstance(payload, dict) and payload.get("schema_version") == 1 else None

    def _write_state(self, run_id: str, counters: Dict[str, int], now: datetime) -> None:
        _atomic_json(
            self.state_path,
            {
                "schema_version": 1,
                "collector_run_id": run_id,
                "counters": counters,
                "published_at": now.astimezone(timezone.utc).isoformat(),
            },
        )

    def _append_ops_event(self, event: str, detail: str) -> None:
        try:
            self.ops_log_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "detail": detail[:500],
            }
            descriptor = os.open(
                str(self.ops_log_path),
                os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                0o600,
            )
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            # Observability must not become a collector or publisher failure source.
            return


def _counter_totals(snapshot: Dict[str, Any]) -> Dict[str, int]:
    exchanges = snapshot["exchanges"]
    writer_errors = _non_negative_int(snapshot.get("unpersisted_event_count", 0))
    queue_drops = 0
    for payload in exchanges.values():
        if not isinstance(payload, dict):
            raise ValueError("invalid exchange metric payload")
        writer_value = _non_negative_int(payload.get("writer_errors", 0))
        drop_value = _non_negative_int(payload.get("queue_dropped_events", 0))
        writer_errors += writer_value
        queue_drops += drop_value
    return {"writer_errors": writer_errors, "queue_drops": queue_drops}


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("collector counters must be non-negative integers")
    return value


def _process_is_running(process_id: int) -> bool:
    command_line = Path("/proc") / str(process_id) / "cmdline"
    if command_line.exists():
        try:
            command = command_line.read_bytes().replace(b"\x00", b" ")
        except OSError:
            return False
        if b"run_cross_market_collector.py" not in command and b"cross_market_collector" not in command:
            return False
    try:
        os.kill(process_id, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".{}.{}.tmp".format(path.name, os.getpid()))
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
        directory_descriptor = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
