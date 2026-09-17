"""Regression tests for Observer T0 Fail-Closed Readiness and Launch Freshness Guard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import pytest

from bithumb_coin_trader.launch_freshness import (
    LaunchFreshnessViolationError,
    enforce_launch_freshness,
)
from bithumb_coin_trader.observer_readiness import (
    ObserverIdentityMismatchError,
    ObserverReadinessProof,
    ObserverReadinessTimeoutError,
    verify_observer_readiness,
)


def _write_mock_observer_snapshot(
    health_dir: Path,
    epoch: str,
    run_id: str,
    collector_status: str = "WAITING_FOR_COLLECTOR",
    observer_status: str = "HEALTHY",
    observer_pid: int = 12345,
) -> Path:
    health_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "epoch": epoch,
        "run_id": run_id,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "collector": {
            "status": collector_status,
            "last_loop_heartbeat": None,
            "fatal_error": None,
        },
        "observer": {
            "status": observer_status,
            "observer_pid": observer_pid,
            "observer_errors": 0,
        },
    }
    path = health_dir / "observer_latest.json"
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return path


def test_observer_readiness_passes_with_waiting_for_collector_and_live_pid(tmp_path: Path) -> None:
    epoch = "test_epoch"
    run_id = "test_run"
    import os
    live_pid = os.getpid()  # current process is guaranteed alive
    _write_mock_observer_snapshot(tmp_path, epoch, run_id, collector_status="WAITING_FOR_COLLECTOR", observer_pid=live_pid)

    proof = verify_observer_readiness(
        health_dir=tmp_path,
        expected_epoch=epoch,
        expected_run_id=run_id,
        timeout_seconds=2.0,
        poll_interval_seconds=0.05,
    )
    assert isinstance(proof, ObserverReadinessProof)
    assert proof.epoch == epoch
    assert proof.run_id == run_id
    assert proof.observer_pid == live_pid
    assert proof.collector_observed_status == "WAITING_FOR_COLLECTOR"
    assert proof.observer_status == "HEALTHY"
    assert proof.pid_liveness_verified is True


def test_observer_readiness_fails_when_collector_status_healthy_before_launch(tmp_path: Path) -> None:
    """Pre-collector readiness must strictly reject HEALTHY and require WAITING_FOR_COLLECTOR."""
    import os
    _write_mock_observer_snapshot(tmp_path, "test_epoch", "test_run", collector_status="HEALTHY", observer_pid=os.getpid())

    with pytest.raises(ObserverReadinessTimeoutError, match="strictly expected 'WAITING_FOR_COLLECTOR'"):
        verify_observer_readiness(
            health_dir=tmp_path,
            expected_epoch="test_epoch",
            expected_run_id="test_run",
            timeout_seconds=0.2,
            poll_interval_seconds=0.05,
        )


def test_observer_readiness_fails_when_pid_nonexistent(tmp_path: Path) -> None:
    """Positive but nonexistent PID must fail closed."""
    # 99999999 is extraordinarily unlikely to exist as a PID
    _write_mock_observer_snapshot(tmp_path, "test_epoch", "test_run", observer_pid=99999999)

    with pytest.raises(ObserverReadinessTimeoutError, match="dead / not signalable on host"):
        verify_observer_readiness(
            health_dir=tmp_path,
            expected_epoch="test_epoch",
            expected_run_id="test_run",
            timeout_seconds=0.2,
            poll_interval_seconds=0.05,
        )


def test_observer_readiness_fails_when_pid_dead(tmp_path: Path) -> None:
    """Dead PID confirmed via liveness checker must fail closed."""
    _write_mock_observer_snapshot(tmp_path, "test_epoch", "test_run", observer_pid=12345)

    with pytest.raises(ObserverReadinessTimeoutError, match="dead / not signalable on host"):
        verify_observer_readiness(
            health_dir=tmp_path,
            expected_epoch="test_epoch",
            expected_run_id="test_run",
            timeout_seconds=0.2,
            poll_interval_seconds=0.05,
            pid_liveness_fn=lambda pid: False,
        )


def test_observer_readiness_fails_when_snapshot_stale(tmp_path: Path) -> None:
    """Snapshot older than max_snapshot_age_seconds must fail closed."""
    import os
    stale_dt = datetime.now(timezone.utc) - timedelta(seconds=45.0)
    health_dir = tmp_path
    health_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "epoch": "test_epoch",
        "run_id": "test_run",
        "observed_at": stale_dt.isoformat(),
        "collector": {"status": "WAITING_FOR_COLLECTOR"},
        "observer": {"status": "HEALTHY", "observer_pid": os.getpid()},
    }
    (health_dir / "observer_latest.json").write_text(json.dumps(snapshot), encoding="utf-8")

    with pytest.raises(ObserverReadinessTimeoutError, match="snapshot is stale"):
        verify_observer_readiness(
            health_dir=tmp_path,
            expected_epoch="test_epoch",
            expected_run_id="test_run",
            max_snapshot_age_seconds=30.0,
            timeout_seconds=0.2,
            poll_interval_seconds=0.05,
        )


def test_observer_readiness_fails_when_observed_at_missing(tmp_path: Path) -> None:
    """Missing observed_at must fail closed and never fall back to now."""
    import os
    health_dir = tmp_path
    health_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "epoch": "test_epoch",
        "run_id": "test_run",
        "collector": {"status": "WAITING_FOR_COLLECTOR"},
        "observer": {"status": "HEALTHY", "observer_pid": os.getpid()},
    }
    (health_dir / "observer_latest.json").write_text(json.dumps(snapshot), encoding="utf-8")

    with pytest.raises(ObserverReadinessTimeoutError, match="observed_at timestamp missing"):
        verify_observer_readiness(
            health_dir=tmp_path,
            expected_epoch="test_epoch",
            expected_run_id="test_run",
            timeout_seconds=0.2,
            poll_interval_seconds=0.05,
        )


def test_observer_readiness_fails_on_identity_mismatch(tmp_path: Path) -> None:
    _write_mock_observer_snapshot(tmp_path, "wrong_epoch", "wrong_run")

    with pytest.raises(ObserverIdentityMismatchError, match="Observer identity mismatch"):
        verify_observer_readiness(
            health_dir=tmp_path,
            expected_epoch="expected_epoch",
            expected_run_id="expected_run",
            timeout_seconds=1.0,
            poll_interval_seconds=0.05,
        )


def test_observer_readiness_fails_on_timeout_when_no_snapshot(tmp_path: Path) -> None:
    with pytest.raises(ObserverReadinessTimeoutError, match="failed to reach verified readiness within"):
        verify_observer_readiness(
            health_dir=tmp_path,
            expected_epoch="test_epoch",
            expected_run_id="test_run",
            timeout_seconds=0.2,
            poll_interval_seconds=0.05,
        )


def test_observer_readiness_fails_when_collector_status_unexpected(tmp_path: Path) -> None:
    _write_mock_observer_snapshot(tmp_path, "test_epoch", "test_run", collector_status="FAILED")

    with pytest.raises(ObserverReadinessTimeoutError, match="collector status is 'FAILED'"):
        verify_observer_readiness(
            health_dir=tmp_path,
            expected_epoch="test_epoch",
            expected_run_id="test_run",
            timeout_seconds=0.2,
            poll_interval_seconds=0.05,
        )


def test_observer_readiness_fails_when_observer_unhealthy(tmp_path: Path) -> None:
    _write_mock_observer_snapshot(tmp_path, "test_epoch", "test_run", observer_status="DEGRADED")

    with pytest.raises(ObserverReadinessTimeoutError, match="observer status is 'DEGRADED'"):
        verify_observer_readiness(
            health_dir=tmp_path,
            expected_epoch="test_epoch",
            expected_run_id="test_run",
            timeout_seconds=0.2,
            poll_interval_seconds=0.05,
        )


def test_launch_freshness_sleeps_on_early_invocation() -> None:
    planned = "2026-09-17T13:50:00Z"
    qual = "2026-09-17T14:00:00Z"

    # Current time: 13:48:00 (120s early)
    now_dt = datetime(2026, 9, 17, 13, 48, 0, tzinfo=timezone.utc)
    sleep_calls = []

    def mock_sleep(secs: float) -> None:
        sleep_calls.append(secs)
        nonlocal now_dt
        now_dt = now_dt + timedelta(seconds=secs)

    delay = enforce_launch_freshness(
        planned_start_utc=planned,
        qualification_start_utc=qual,
        max_delay_seconds=60.0,
        now_fn=lambda: now_dt,
        sleep_fn=mock_sleep,
    )
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 120.0
    assert delay == 0.0


def test_launch_freshness_passes_within_delay_tolerance() -> None:
    planned = "2026-09-17T13:50:00Z"
    qual = "2026-09-17T14:00:00Z"

    # Current time: 13:50:15 (15s delay, <= 60s)
    now_dt = datetime(2026, 9, 17, 13, 50, 15, tzinfo=timezone.utc)
    delay = enforce_launch_freshness(
        planned_start_utc=planned,
        qualification_start_utc=qual,
        max_delay_seconds=60.0,
        now_fn=lambda: now_dt,
    )
    assert delay == 15.0


def test_launch_freshness_fails_when_past_delay_tolerance() -> None:
    planned = "2026-09-17T13:50:00Z"
    qual = "2026-09-17T14:00:00Z"

    # Current time: 13:51:30 (90s delay > 60s)
    now_dt = datetime(2026, 9, 17, 13, 51, 30, tzinfo=timezone.utc)
    with pytest.raises(LaunchFreshnessViolationError, match="LAUNCH_FRESHNESS_VIOLATION"):
        enforce_launch_freshness(
            planned_start_utc=planned,
            qualification_start_utc=qual,
            max_delay_seconds=60.0,
            now_fn=lambda: now_dt,
        )


def test_launch_freshness_fails_when_past_qualification_boundary() -> None:
    planned = "2026-09-17T13:50:00Z"
    qual = "2026-09-17T14:00:00Z"

    # Current time: 14:00:01 (past qualification boundary!)
    now_dt = datetime(2026, 9, 17, 14, 0, 1, tzinfo=timezone.utc)
    with pytest.raises(LaunchFreshnessViolationError, match="QUALIFICATION_BOUNDARY_VIOLATED"):
        enforce_launch_freshness(
            planned_start_utc=planned,
            qualification_start_utc=qual,
            max_delay_seconds=1800.0,  # even with huge tolerance, boundary crossing must abort!
            now_fn=lambda: now_dt,
        )
