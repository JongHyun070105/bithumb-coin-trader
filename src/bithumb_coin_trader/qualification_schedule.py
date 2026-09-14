"""Strictly-next-hour qualification schedule with fixed monotonic deadline.

Global V3 contracts:
  qualification_start_utc = strictly_next_utc_hour(actual_start_utc)  # even at exact boundary
  required_qualifying_full_hours = 30
  maximum_collection_window_seconds = 111600
  collection_stop_monotonic derived once from actual_mono; never recomputed

Persistence uses write-to-temp + os.replace + fsync (mode 0o600).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Sequence

__all__ = [
    "QualificationSchedule",
    "build_qualification_schedule",
    "save_schedule",
    "load_schedule",
    "strictly_next_utc_hour",
    "parse_utc",
    "format_utc",
]

_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class QualificationSchedule:
    schema_version: int
    actual_start_utc: str
    actual_start_monotonic: float
    qualification_start_utc: str
    collection_stop_utc: str
    collection_stop_monotonic: float
    required_qualifying_full_hours: int
    maximum_collection_window_seconds: int
    candidate_cohorts: tuple[str, ...]  # immutable sequence


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("TIMESTAMP_NOT_UTC: naive datetime")
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("TIMESTAMP_NOT_UTC: non-zero or missing offset")
    return value


def strictly_next_utc_hour(value: datetime) -> datetime:
    """Return the strictly next UTC hour boundary. Even 12:00:00Z -> 13:00:00Z."""
    current = require_utc(value)
    return current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def parse_utc(value: str) -> datetime:
    """Parse UTC timestamp accepting Z or +00:00; reject naive and non-zero offset."""
    if not isinstance(value, str):
        raise ValueError("TIMESTAMP_NOT_UTC: not a string")
    if not (value.endswith("Z") or value.endswith("+00:00")):
        raise ValueError("TIMESTAMP_NOT_UTC: must end with Z or +00:00")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"TIMESTAMP_NOT_UTC: unparseable: {exc}") from exc
    return require_utc(parsed)


def format_utc(value: datetime) -> str:
    """Format UTC datetime as ISO 8601 with Z suffix."""
    return require_utc(value).isoformat().replace("+00:00", "Z")


def build_qualification_schedule(
    actual_utc: datetime,
    actual_mono: float,
    hours: int,
    max_window: int,
) -> QualificationSchedule:
    """Build a fixed, immutable qualification schedule.

    Raises ValueError("V3_QUALIFICATION_WINDOW_INVALID") for wrong hours or window.
    """
    require_utc(actual_utc)
    start = strictly_next_utc_hour(actual_utc)
    stop = start + timedelta(hours=hours)
    elapsed = (stop - actual_utc).total_seconds()
    # V3 requires exactly 30 hours and exactly 111600 second window
    # elapsed is always in (actual_start, stop) range:
    # min: start is next hour after actual, so elapsed >= hours*3600 (at exact boundary)
    # max: actual is just after an hour boundary, so elapsed < hours*3600 + 3600
    if hours != 30 or max_window != 111600 or not (108000 < elapsed <= max_window):
        raise ValueError("V3_QUALIFICATION_WINDOW_INVALID")
    cohorts = tuple(
        (start + timedelta(hours=i)).strftime("%Y-%m-%d_%H")
        for i in range(hours)
    )
    return QualificationSchedule(
        schema_version=_SCHEMA_VERSION,
        actual_start_utc=format_utc(actual_utc),
        actual_start_monotonic=actual_mono,
        qualification_start_utc=format_utc(start),
        collection_stop_utc=format_utc(stop),
        collection_stop_monotonic=actual_mono + elapsed,
        required_qualifying_full_hours=hours,
        maximum_collection_window_seconds=max_window,
        candidate_cohorts=cohorts,
    )


def _to_dict(schedule: QualificationSchedule) -> dict:
    return {
        "schema_version": schedule.schema_version,
        "actual_start_utc": schedule.actual_start_utc,
        "actual_start_monotonic": schedule.actual_start_monotonic,
        "qualification_start_utc": schedule.qualification_start_utc,
        "collection_stop_utc": schedule.collection_stop_utc,
        "collection_stop_monotonic": schedule.collection_stop_monotonic,
        "required_qualifying_full_hours": schedule.required_qualifying_full_hours,
        "maximum_collection_window_seconds": schedule.maximum_collection_window_seconds,
        "candidate_cohorts": list(schedule.candidate_cohorts),
    }


def save_schedule(schedule: QualificationSchedule, path: Path) -> None:
    """Atomically persist the schedule. Mode 0o600, fsync, os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(_to_dict(schedule), indent=2, ensure_ascii=True, sort_keys=False)
    tmp = path.with_suffix(".tmp")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        except Exception:
            try:
                os.unlink(str(tmp))
            except OSError:
                pass
            raise
    except Exception:
        raise
    os.replace(str(tmp), str(path))
    # fsync parent directory
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def load_schedule(path: Path) -> QualificationSchedule:
    """Load and validate a persisted schedule."""
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != _SCHEMA_VERSION:
        raise ValueError(f"SCHEDULE_SCHEMA_UNSUPPORTED: got {raw.get('schema_version')}")
    if raw.get("required_qualifying_full_hours") != 30:
        raise ValueError(f"SCHEDULE_HOURS_INVALID: expected 30, got {raw.get('required_qualifying_full_hours')}")
    if raw.get("maximum_collection_window_seconds") != 111600:
        raise ValueError(f"SCHEDULE_WINDOW_INVALID: expected 111600, got {raw.get('maximum_collection_window_seconds')}")
    if "qualification_start_utc" not in raw or not isinstance(raw["qualification_start_utc"], str):
        raise ValueError("SCHEDULE_QUALIFICATION_START_INVALID")
    start = parse_utc(raw["qualification_start_utc"])
    cohorts = raw.get("candidate_cohorts", [])
    if not isinstance(cohorts, list) or len(cohorts) != 30:
        raise ValueError(f"SCHEDULE_CANDIDATE_COUNT: expected 30, got {len(cohorts) if isinstance(cohorts, list) else type(cohorts)}")
    expected = tuple((start + timedelta(hours=i)).strftime("%Y-%m-%d_%H") for i in range(30))
    if tuple(cohorts) != expected:
        raise ValueError("SCHEDULE_CANDIDATE_ORDER_INVALID")

    return QualificationSchedule(
        schema_version=int(raw["schema_version"]),
        actual_start_utc=str(raw["actual_start_utc"]),
        actual_start_monotonic=float(raw["actual_start_monotonic"]),
        qualification_start_utc=str(raw["qualification_start_utc"]),
        collection_stop_utc=str(raw["collection_stop_utc"]),
        collection_stop_monotonic=float(raw["collection_stop_monotonic"]),
        required_qualifying_full_hours=int(raw["required_qualifying_full_hours"]),
        maximum_collection_window_seconds=int(raw["maximum_collection_window_seconds"]),
        candidate_cohorts=tuple(str(c) for c in cohorts),
    )
