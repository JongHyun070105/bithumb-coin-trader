"""Phase 6.1 Cohort Oracle and Scheduler Semantics Tests.

Implements P3:
Derives and tests:
- EXPECTED_RAW_COHORTS
- EXPECTED_ARCHIVE_COHORTS
- EXPECTED_FULLSCAN_COHORTS

Verifies exact boundary cases:
1. 03:40 -> 03:40 three days later:
   - start-day 03 exists in raw: YES
   - final-day 03 exists in raw: YES (total 73 raw cohorts)
   - start-day 03 requires archive receipt: YES
   - final-day 03 requires archive receipt: NO (total 72 archive cohorts)
2. Exact hour boundaries: 00:00 -> 01:00
3. Partial initial hour + partial final hour
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from scripts.audit_72h_soak import (
    derive_expected_raw_cohorts,
    derive_expected_archive_cohorts,
    derive_expected_fullscan_cohorts,
)


def test_p3_soak_72h_boundary_oracle_0340_to_0340() -> None:
    """Boundary oracle for official 72h soak: 03:40 -> 03:40 three days later."""
    start_dt = datetime(2026, 9, 1, 3, 40, 0, tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(seconds=259200)  # 72 hours later = 2026-09-04 03:40:00

    raw_cohorts = derive_expected_raw_cohorts(start_dt, end_dt)
    archive_cohorts = derive_expected_archive_cohorts(start_dt, end_dt, grace_seconds=600)
    fullscan_info = derive_expected_fullscan_cohorts(start_dt, end_dt)

    # 1. Raw cohorts
    assert "20260901-03" in raw_cohorts, "Start-day 03 cohort must exist in raw cohorts"
    assert "20260904-03" in raw_cohorts, "Final-day 03 cohort must exist in raw cohorts"
    assert len(raw_cohorts) == 73, f"Expected exactly 73 raw cohorts (1 initial partial + 71 interior + 1 final partial), got {len(raw_cohorts)}"

    # 2. Archive cohorts
    assert "20260901-03" in archive_cohorts, "Start-day 03 closed at 04:00, grace expired at 04:10, must be archived"
    assert "20260904-02" in archive_cohorts, "Final interior hour Day 4 02 closed at 03:00, grace at 03:10, must be archived"
    assert "20260904-03" not in archive_cohorts, "Final-day 03 was active at shutdown (03:40), never closed under scheduler, must NOT require receipt"
    assert len(archive_cohorts) == 72, f"Expected exactly 72 archive cohorts, got {len(archive_cohorts)}"

    # 3. Fullscan cohorts
    assert fullscan_info["hourly_fullscan_cohorts"] == archive_cohorts
    assert fullscan_info["terminal_fullscan_required"] is True


def test_p3_exact_hour_boundary() -> None:
    """Exact hour boundary: 00:00:00 -> 01:00:00 (1 hour)."""
    start_dt = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 9, 1, 1, 0, 0, tzinfo=timezone.utc)

    raw_cohorts = derive_expected_raw_cohorts(start_dt, end_dt)
    assert raw_cohorts == ["20260901-00"]

    # At 01:00:00, hour 00 closed at 01:00:00, but grace (600s) expires at 01:10:00 > 01:00:00
    # So with grace_seconds=600, it would not have autonomous receipt before 01:00
    archive_cohorts = derive_expected_archive_cohorts(start_dt, end_dt, grace_seconds=600)
    assert archive_cohorts == []

    # If grace_seconds=0
    archive_cohorts_no_grace = derive_expected_archive_cohorts(start_dt, end_dt, grace_seconds=0)
    assert archive_cohorts_no_grace == ["20260901-00"]


def test_p3_two_hours_cross_boundary() -> None:
    """Cross-boundary: 00:30:00 -> 02:30:00 (2 hours)."""
    start_dt = datetime(2026, 9, 1, 0, 30, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 9, 1, 2, 30, 0, tzinfo=timezone.utc)

    raw_cohorts = derive_expected_raw_cohorts(start_dt, end_dt)
    # 00:30 (hour 00), 01:00 (hour 01), 02:30 (hour 02) -> 3 raw cohorts
    assert raw_cohorts == ["20260901-00", "20260901-01", "20260901-02"]

    # Archive:
    # hour 00 closes 01:00, grace 01:10 <= 02:30 -> YES
    # hour 01 closes 02:00, grace 02:10 <= 02:30 -> YES
    # hour 02 closes 03:00, grace 03:10 > 02:30 -> NO
    archive_cohorts = derive_expected_archive_cohorts(start_dt, end_dt, grace_seconds=600)
    assert archive_cohorts == ["20260901-00", "20260901-01"]
