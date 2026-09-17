"""Collector & Pipeline Component Health Model.

Explicitly separates:
- COLLECTOR (WebSocket / canonical events)
- WRITER (local RAW write & queue)
- ARCHIVER (compression & S3 staging)
- EVIDENCE (coverage & receipts)
- SUPERVISOR (systemd service)
- OBSERVER (independent observer daemon)
- TERMINAL WITNESS (ExecStopPost hook)

Provides atomic local state writing and tolerant deserialization.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any


class ComponentHealthState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


def utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SupervisorHealth:
    unit: str = ""
    invocation_id: str = ""
    active_state: str = "UNKNOWN"
    sub_state: str = "UNKNOWN"
    pid: int | None = None
    process_start: str | None = None


@dataclass
class CollectorHealth:
    status: str = ComponentHealthState.UNKNOWN.value
    last_loop_heartbeat: str | None = None
    last_websocket_activity: str | None = None
    last_canonical_event: str | None = None
    websocket_sessions: dict[str, str] = field(default_factory=dict)
    reconnect_count: int = 0
    fatal_error: str | None = None


@dataclass
class WriterHealth:
    status: str = ComponentHealthState.UNKNOWN.value
    queue_depth: int = 0
    max_queue_depth: int = 0
    last_dequeue: str | None = None
    last_local_raw_write: str | None = None
    current_open_raw_count: int = 0
    unpersisted_count: int = 0
    writer_errors: int = 0


@dataclass
class ArchiverHealth:
    status: str = ComponentHealthState.UNKNOWN.value
    archive_queue_depth: int = 0
    last_closed_cohort: str | None = None
    last_compression: str | None = None
    last_s3_put: str | None = None
    last_receipt: str | None = None
    archive_errors: int = 0
    upload_failures: int = 0


@dataclass
class EvidenceHealth:
    status: str = ComponentHealthState.UNKNOWN.value
    last_coverage_evidence: str | None = None
    last_heartbeat_evidence: str | None = None
    last_manifest_update: str | None = None
    current_hour_expected_slots: int = 76
    current_hour_terminal_slots: int = 0


@dataclass
class ObserverHealth:
    status: str = ComponentHealthState.UNKNOWN.value
    observer_pid: int | None = None
    observer_started_at: str | None = None
    observer_last_cycle: str | None = None
    observer_errors: int = 0


@dataclass
class ResourceTelemetry:
    rss_bytes: int = 0
    fd_count: int = 0
    disk_free_bytes: int = 0
    disk_used_bytes: int = 0


@dataclass
class CurrentCohortHealth:
    utc_hour: str = ""
    observed_feed_count: int = 0
    expected_feed_count: int = 76


@dataclass
class LastExceptionInfo:
    component: str | None = None
    type: str | None = None
    message_hash: str | None = None
    timestamp: str | None = None


@dataclass
class RuntimeHealthSnapshot:
    schema_version: int = 1
    epoch: str = ""
    run_id: str = ""
    software_sha: str = ""
    config_fingerprint: str = ""
    observed_at: str = field(default_factory=utc_iso_now)
    supervisor: SupervisorHealth = field(default_factory=SupervisorHealth)
    collector: CollectorHealth = field(default_factory=CollectorHealth)
    writer: WriterHealth = field(default_factory=WriterHealth)
    archiver: ArchiverHealth = field(default_factory=ArchiverHealth)
    evidence: EvidenceHealth = field(default_factory=EvidenceHealth)
    observer: ObserverHealth = field(default_factory=ObserverHealth)
    resources: ResourceTelemetry = field(default_factory=ResourceTelemetry)
    current_cohort: CurrentCohortHealth = field(default_factory=CurrentCohortHealth)
    last_exception: LastExceptionInfo = field(default_factory=LastExceptionInfo)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeHealthSnapshot:
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            epoch=str(data.get("epoch", "")),
            run_id=str(data.get("run_id", "")),
            software_sha=str(data.get("software_sha", "")),
            config_fingerprint=str(data.get("config_fingerprint", "")),
            observed_at=str(data.get("observed_at", utc_iso_now())),
            supervisor=SupervisorHealth(**data.get("supervisor", {})),
            collector=CollectorHealth(**data.get("collector", {})),
            writer=WriterHealth(**data.get("writer", {})),
            archiver=ArchiverHealth(**data.get("archiver", {})),
            evidence=EvidenceHealth(**data.get("evidence", {})),
            observer=ObserverHealth(**data.get("observer", {})),
            resources=ResourceTelemetry(**data.get("resources", {})),
            current_cohort=CurrentCohortHealth(**data.get("current_cohort", {})),
            last_exception=LastExceptionInfo(**data.get("last_exception", {})),
        )


def compute_exception_hash(msg: str) -> str:
    return hashlib.sha256(msg.encode("utf-8", errors="replace")).hexdigest()[:16]


def write_health_snapshot_atomic(path: Path, snapshot: RuntimeHealthSnapshot) -> None:
    """Atomically write a runtime health snapshot via temp file + fsync + rename.
    
    Guarantees no reader sees a partial, unbuffered, or corrupt file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = path.parent
    data = snapshot.to_dict()
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"

    # Create temporary file in same directory to ensure atomic rename
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=temp_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
        # fsync parent directory for crash safety (POSIX metadata durability)
        try:
            parent_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        except OSError:
            pass
    except Exception:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise


def read_health_snapshot(path: Path) -> RuntimeHealthSnapshot | None:
    """Tolerantly read a runtime health snapshot.
    
    Returns None if file is missing, empty, or unparseable.
    """
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            return None
        data = json.loads(content)
        if not isinstance(data, dict):
            return None
        return RuntimeHealthSnapshot.from_dict(data)
    except (OSError, json.JSONDecodeError, TypeError, KeyError):
        return None
