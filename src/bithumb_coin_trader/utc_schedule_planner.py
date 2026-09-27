"""Deterministic Full UTC Hour Schedule Planner.

Calculates exact warmup duration to next UTC hour boundary,
required qualifying full hours (N * 3600s),
and post-closure archive grace window (grace_seconds + settle margin),
ensuring that at least N full UTC hour cohorts (00:00-59:59) are completely
collected, closed, evaluated, and archived without ambiguity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence


@dataclass(frozen=True)
class UtcSchedulePlan:
    actual_start_utc: str
    warmup_duration_seconds: float
    qualification_start_utc: str
    target_full_hours: int
    full_hours_duration_seconds: int
    cohort_closure_utc: str
    grace_seconds: int
    grace_expiry_utc: str
    post_grace_settle_seconds: int
    archive_settled_utc: str
    collection_duration_seconds: float
    total_pipeline_duration_seconds: float
    partial_start_cohort: str | None
    qualifying_cohorts: tuple[str, ...]
    partial_end_cohort: str | None


def _require_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("TIMESTAMP_NOT_UTC: datetime must be timezone-aware UTC")
    offset = dt.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("TIMESTAMP_NOT_UTC: datetime must have UTC offset 0")
    return dt


def compute_full_utc_hour_schedule(
    start_time: datetime,
    target_full_hours: int = 1,
    grace_seconds: int = 600,
    post_grace_settle_seconds: int = 180,
    keep_collecting_through_grace: bool = True,
) -> UtcSchedulePlan:
    """Compute deterministic full UTC hour schedule plan for soak and validation runs."""
    start = _require_utc(start_time)
    if target_full_hours < 1:
        raise ValueError("target_full_hours must be at least 1")
    if grace_seconds < 600:
        raise ValueError("grace_seconds must be at least 600 per immutable archive contract")
    if post_grace_settle_seconds < 0:
        raise ValueError("post_grace_settle_seconds cannot be negative")

    is_exact_boundary = (
        start.minute == 0 and start.second == 0 and start.microsecond == 0
    )

    if is_exact_boundary:
        warmup_seconds = 0.0
        qualification_start = start
        partial_start_cohort = None
    else:
        next_boundary = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        warmup_seconds = (next_boundary - start).total_seconds()
        qualification_start = next_boundary
        partial_start_cohort = start.strftime("%Y-%m-%d_%H")

    full_hours_seconds = target_full_hours * 3600
    cohort_closure = qualification_start + timedelta(seconds=full_hours_seconds)
    grace_expiry = cohort_closure + timedelta(seconds=grace_seconds)
    archive_settled = grace_expiry + timedelta(seconds=post_grace_settle_seconds)

    qualifying_cohorts = tuple(
        (qualification_start + timedelta(hours=i)).strftime("%Y-%m-%d_%H")
        for i in range(target_full_hours)
    )

    total_pipeline_duration = (archive_settled - start).total_seconds()

    if keep_collecting_through_grace:
        collection_duration = total_pipeline_duration
        partial_end_cohort = cohort_closure.strftime("%Y-%m-%d_%H")
    else:
        collection_duration = (cohort_closure - start).total_seconds()
        partial_end_cohort = None

    return UtcSchedulePlan(
        actual_start_utc=start.isoformat(),
        warmup_duration_seconds=warmup_seconds,
        qualification_start_utc=qualification_start.isoformat(),
        target_full_hours=target_full_hours,
        full_hours_duration_seconds=full_hours_seconds,
        cohort_closure_utc=cohort_closure.isoformat(),
        grace_seconds=grace_seconds,
        grace_expiry_utc=grace_expiry.isoformat(),
        post_grace_settle_seconds=post_grace_settle_seconds,
        archive_settled_utc=archive_settled.isoformat(),
        collection_duration_seconds=collection_duration,
        total_pipeline_duration_seconds=total_pipeline_duration,
        partial_start_cohort=partial_start_cohort,
        qualifying_cohorts=qualifying_cohorts,
        partial_end_cohort=partial_end_cohort,
    )
