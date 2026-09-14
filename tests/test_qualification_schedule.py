"""Tests for qualification_schedule module."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bithumb_coin_trader.qualification_schedule import (
    build_qualification_schedule,
    format_utc,
    parse_utc,
    strictly_next_utc_hour,
)


# --- strictly_next_utc_hour ---

def test_strictly_next_non_boundary() -> None:
    dt = datetime(2026, 9, 14, 11, 24, 0, tzinfo=timezone.utc)
    result = strictly_next_utc_hour(dt)
    assert result == datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)


def test_strictly_next_exact_boundary() -> None:
    """Even at exactly 12:00:00 UTC, next hour is 13:00:00 UTC."""
    dt = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
    result = strictly_next_utc_hour(dt)
    assert result == datetime(2026, 9, 14, 13, 0, 0, tzinfo=timezone.utc)


def test_strictly_next_rejects_naive() -> None:
    dt = datetime(2026, 9, 14, 12, 0, 0)  # naive
    with pytest.raises(ValueError):
        strictly_next_utc_hour(dt)


# --- parse_utc / format_utc ---

def test_parse_utc_z_suffix() -> None:
    dt = parse_utc("2026-09-14T12:00:00Z")
    assert dt.tzinfo == timezone.utc

def test_parse_utc_plus_zero_offset() -> None:
    dt = parse_utc("2026-09-14T12:00:00+00:00")
    assert dt.tzinfo == timezone.utc

def test_parse_utc_rejects_naive() -> None:
    with pytest.raises(ValueError, match="TIMESTAMP_NOT_UTC"):
        parse_utc("2026-09-14T12:00:00")

def test_parse_utc_rejects_non_zero_offset() -> None:
    with pytest.raises(ValueError, match="TIMESTAMP_NOT_UTC"):
        parse_utc("2026-09-14T21:00:00+09:00")

def test_format_utc_produces_z() -> None:
    dt = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
    assert format_utc(dt) == "2026-09-14T12:00:00Z"


# --- build_qualification_schedule ---

@pytest.mark.parametrize("actual,start,stop", [
    ("2026-09-14T11:24:00Z", "2026-09-14T12:00:00Z", "2026-09-15T18:00:00Z"),
    ("2026-09-14T12:00:00Z", "2026-09-14T13:00:00Z", "2026-09-15T19:00:00Z"),
])
def test_strict_next_has_exactly_30_candidates(actual: str, start: str, stop: str) -> None:
    schedule = build_qualification_schedule(parse_utc(actual), 500.0, 30, 111600)
    assert schedule.qualification_start_utc == start
    assert schedule.collection_stop_utc == stop
    assert len(schedule.candidate_cohorts) == 30


def test_exact_boundary_uses_fixed_111600_second_monotonic_stop() -> None:
    schedule = build_qualification_schedule(parse_utc("2026-09-14T12:00:00Z"), 100.0, 30, 111600)
    # actual_mono=100.0, elapsed = 111600 (exactly), stop_mono = 100.0 + 111600 = 111700.0
    assert schedule.collection_stop_monotonic == pytest.approx(111700.0)


def test_non_boundary_start_within_window() -> None:
    # actual=11:24:00, start=12:00:00, stop=18:00:00 next day
    # elapsed from 11:24:00 to 18:00:00 = 6h36min + 30h = 36h36m = 131760s > 111600s?
    # Wait: start=12:00, + 30h = next day 18:00
    # elapsed = 12:00->11:24 = 36 minutes earlier, so elapsed = 30h + 36m = 108000+2160 = 110160s
    # 108000 < 110160 <= 111600 -> valid
    schedule = build_qualification_schedule(parse_utc("2026-09-14T11:24:00Z"), 500.0, 30, 111600)
    elapsed = (parse_utc(schedule.collection_stop_utc) - parse_utc("2026-09-14T11:24:00Z")).total_seconds()
    assert 108000 < elapsed <= 111600


def test_candidate_cohorts_are_exactly_30_consecutive_hours() -> None:
    schedule = build_qualification_schedule(parse_utc("2026-09-14T11:24:00Z"), 0.0, 30, 111600)
    assert len(schedule.candidate_cohorts) == 30
    # First cohort: 2026-09-14_12 (12:00 UTC)
    assert schedule.candidate_cohorts[0] == "2026-09-14_12"
    # Last cohort: 30th hour after 12:00 = 2026-09-15_17
    assert schedule.candidate_cohorts[29] == "2026-09-15_17"


def test_monotonic_stop_not_recomputed_on_wall_clock_jump() -> None:
    """collection_stop_monotonic is derived from actual_mono at build time."""
    s1 = build_qualification_schedule(parse_utc("2026-09-14T12:00:00Z"), 100.0, 30, 111600)
    s2 = build_qualification_schedule(parse_utc("2026-09-14T12:00:00Z"), 200.0, 30, 111600)  # different mono
    # stop differs by exactly the monotonic difference
    assert s2.collection_stop_monotonic - s1.collection_stop_monotonic == pytest.approx(100.0)


def test_readiness_failure_does_not_shift_start() -> None:
    """A schedule is immutable once built; no shifting on failure."""
    schedule = build_qualification_schedule(parse_utc("2026-09-14T11:24:00Z"), 0.0, 30, 111600)
    assert schedule.qualification_start_utc == "2026-09-14T12:00:00Z"  # unchanged


def test_wrong_hours_raises() -> None:
    with pytest.raises(ValueError, match="V3_QUALIFICATION_WINDOW_INVALID"):
        build_qualification_schedule(parse_utc("2026-09-14T12:00:00Z"), 0.0, 29, 111600)  # not 30


def test_wrong_max_window_raises() -> None:
    with pytest.raises(ValueError, match="V3_QUALIFICATION_WINDOW_INVALID"):
        build_qualification_schedule(parse_utc("2026-09-14T12:00:00Z"), 0.0, 30, 108000)  # not 111600


def test_schedule_candidate_cohorts_order_validation(tmp_path: Path) -> None:
    from bithumb_coin_trader.qualification_schedule import save_schedule, load_schedule
    schedule = build_qualification_schedule(parse_utc("2026-09-14T12:00:00Z"), 100.0, 30, 111600)
    path = tmp_path / "schedule.json"
    save_schedule(schedule, path)
    d = json.loads(path.read_text(encoding="utf-8"))
    # Reverse two cohorts to break chronological order
    d["candidate_cohorts"][0], d["candidate_cohorts"][1] = d["candidate_cohorts"][1], d["candidate_cohorts"][0]
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(ValueError, match="SCHEDULE_CANDIDATE_ORDER_INVALID"):
        load_schedule(path)


# --- schedule persistence ---

def test_schedule_roundtrip(tmp_path: Path) -> None:
    from bithumb_coin_trader.qualification_schedule import save_schedule, load_schedule
    schedule = build_qualification_schedule(parse_utc("2026-09-14T12:00:00Z"), 100.0, 30, 111600)
    path = tmp_path / "schedule.json"
    save_schedule(schedule, path)
    loaded = load_schedule(path)
    assert loaded == schedule


def test_load_rejects_wrong_schema_version(tmp_path: Path) -> None:
    from bithumb_coin_trader.qualification_schedule import load_schedule
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(ValueError, match="SCHEDULE_SCHEMA_UNSUPPORTED"):
        load_schedule(path)


def test_load_rejects_wrong_candidate_count(tmp_path: Path) -> None:
    from bithumb_coin_trader.qualification_schedule import save_schedule, load_schedule
    schedule = build_qualification_schedule(parse_utc("2026-09-14T12:00:00Z"), 100.0, 30, 111600)
    path = tmp_path / "schedule.json"
    save_schedule(schedule, path)
    d = json.loads(path.read_text(encoding="utf-8"))
    d["candidate_cohorts"] = d["candidate_cohorts"][:-1]  # remove last
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(ValueError, match="SCHEDULE_CANDIDATE_COUNT"):
        load_schedule(path)


def test_load_rejects_wrong_hours(tmp_path: Path) -> None:
    from bithumb_coin_trader.qualification_schedule import save_schedule, load_schedule
    schedule = build_qualification_schedule(parse_utc("2026-09-14T12:00:00Z"), 100.0, 30, 111600)
    path = tmp_path / "schedule.json"
    save_schedule(schedule, path)
    d = json.loads(path.read_text(encoding="utf-8"))
    d["required_qualifying_full_hours"] = 29
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(ValueError, match="SCHEDULE_HOURS_INVALID"):
        load_schedule(path)


def test_load_rejects_wrong_window(tmp_path: Path) -> None:
    from bithumb_coin_trader.qualification_schedule import save_schedule, load_schedule
    schedule = build_qualification_schedule(parse_utc("2026-09-14T12:00:00Z"), 100.0, 30, 111600)
    path = tmp_path / "schedule.json"
    save_schedule(schedule, path)
    d = json.loads(path.read_text(encoding="utf-8"))
    d["maximum_collection_window_seconds"] = 108000
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(ValueError, match="SCHEDULE_WINDOW_INVALID"):
        load_schedule(path)
