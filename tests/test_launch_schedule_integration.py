"""Integration tests for full UTC hour schedule planning and launch artifact generation/validation."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from bithumb_coin_trader.launch_artifacts import (
    ValidationRunSpec,
    generate_launch_artifacts,
    validate_launch_artifacts,
)


def test_exact_boundary_schedule_passes_5400s(tmp_path: Path) -> None:
    """Exact UTC boundary start (e.g. 21:00:00Z) requires 4380s, which fits within 5400s (90m)."""
    spec = ValidationRunSpec(
        epoch="aws-validation-observability-90m-20260917-20260917T210000Z-test",
        run_id="aws-validation-observability-90m-run-20260917T210000Z-test",
        duration_seconds=5400,
        runtime_commit="140412e87428adb583bc76c90a97bde0368bf054",
        target_full_hours=1,
        planned_start_time="2026-09-17T21:00:00Z",
        base_data_parent=tmp_path / "data",
        runtime_worktree=Path("/var/lib/bitcoin-trader/runtime-worktrees/aws-observability-90m-20260917"),
        launch_artifacts_parent=tmp_path / "artifacts",
    )

    artifacts = generate_launch_artifacts(spec, target_dir=tmp_path / "out")
    validation = validate_launch_artifacts(
        spec=spec,
        runtime_config=artifacts.runtime_config,
        launch_command=artifacts.launch_command,
        target_dir=tmp_path / "out",
    )

    assert validation["status"] == "PASS"
    assert validation["target_full_hours"] == 1
    assert validation["qualification_start_utc"] == "2026-09-17T21:00:00+00:00"
    assert validation["qualifying_cohorts"] == ["2026-09-17_21"]
    assert validation["cohort_closure_utc"] == "2026-09-17T22:00:00+00:00"
    assert validation["grace_expiry_utc"] == "2026-09-17T22:10:00+00:00"
    assert validation["archive_settled_utc"] == "2026-09-17T22:13:00+00:00"

    # Verify identity.json and runtime.json have consistent schedule fields
    identity = artifacts.identity
    assert identity["target_full_hours"] == 1
    assert identity["qualification_start_utc"] == "2026-09-17T21:00:00+00:00"
    assert identity["qualifying_cohorts"] == ["2026-09-17_21"]

    rt_sched = artifacts.runtime_config["schedule"]
    assert rt_sched["qualification_rule"] == "FULL_UTC_HOUR"
    assert rt_sched["target_full_hours"] == 1
    assert rt_sched["qualifying_cohorts"] == ["2026-09-17_21"]


def test_non_boundary_schedule_fails_insufficient_duration(tmp_path: Path) -> None:
    """Non-boundary start (e.g. 20:15:00Z) requires 7080s, which exceeds 5400s -> FAILS PRELAUNCH."""
    spec = ValidationRunSpec(
        epoch="aws-validation-observability-90m-20260917-20260917T201500Z-test",
        run_id="aws-validation-observability-90m-run-20260917T201500Z-test",
        duration_seconds=5400,
        runtime_commit="140412e87428adb583bc76c90a97bde0368bf054",
        target_full_hours=1,
        planned_start_time="2026-09-17T20:15:00Z",
        base_data_parent=tmp_path / "data",
        runtime_worktree=Path("/var/lib/bitcoin-trader/runtime-worktrees/aws-observability-90m-20260917"),
        launch_artifacts_parent=tmp_path / "artifacts",
    )

    artifacts = generate_launch_artifacts(spec)
    with pytest.raises(ValueError, match="INSUFFICIENT_DURATION_FOR_FULL_UTC_HOUR"):
        validate_launch_artifacts(
            spec=spec,
            runtime_config=artifacts.runtime_config,
            launch_command=artifacts.launch_command,
        )


def test_non_boundary_schedule_passes_with_adequate_duration(tmp_path: Path) -> None:
    """Non-boundary start (20:15:00Z) with duration 7200s (120m) covers 7080s -> PASSES PRELAUNCH."""
    spec = ValidationRunSpec(
        epoch="aws-validation-observability-90m-20260917-20260917T201500Z-test",
        run_id="aws-validation-observability-90m-run-20260917T201500Z-test",
        duration_seconds=7200,
        runtime_commit="140412e87428adb583bc76c90a97bde0368bf054",
        target_full_hours=1,
        planned_start_time="2026-09-17T20:15:00Z",
        base_data_parent=tmp_path / "data",
        runtime_worktree=Path("/var/lib/bitcoin-trader/runtime-worktrees/aws-observability-90m-20260917"),
        launch_artifacts_parent=tmp_path / "artifacts",
    )

    artifacts = generate_launch_artifacts(spec, target_dir=tmp_path / "out")
    validation = validate_launch_artifacts(
        spec=spec,
        runtime_config=artifacts.runtime_config,
        launch_command=artifacts.launch_command,
        target_dir=tmp_path / "out",
    )

    assert validation["status"] == "PASS"
    assert validation["target_full_hours"] == 1
    assert validation["qualification_start_utc"] == "2026-09-17T21:00:00+00:00"
    assert validation["qualifying_cohorts"] == ["2026-09-17_21"]
    assert validation["cohort_closure_utc"] == "2026-09-17T22:00:00+00:00"
    assert validation["grace_expiry_utc"] == "2026-09-17T22:10:00+00:00"
    assert validation["archive_settled_utc"] == "2026-09-17T22:13:00+00:00"
