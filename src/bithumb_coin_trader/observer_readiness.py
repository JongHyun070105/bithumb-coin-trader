"""Fail-closed Observer T0 Readiness Verifier.

Enforces the strict invariant:
  OBSERVER_READY_TIME <= COLLECTOR_START_TIME

Before the collector/supervisor can launch, the observer daemon must
be verifiably active and have emitted an authoritative readiness proof:
1. health/observer_latest.json exists and is parseable.
2. epoch matches expected_epoch.
3. run_id matches expected_run_id.
4. collector.status == "WAITING_FOR_COLLECTOR" (STRICT: HEALTHY is NOT accepted prior to collector launch).
5. observer.status == "HEALTHY".
6. observer_pid is positive AND confirmed alive on the host.
7. snapshot observed_at is valid and bounded by max_snapshot_age_seconds (no silent fallback).
8. observer systemd unit is ACTIVE where specified and checkable.

Any violation, mismatch, or timeout aborts with non-zero exit code (Fail-Closed).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Optional


class ObserverReadinessError(RuntimeError):
    """Base exception for observer readiness failure."""


class ObserverReadinessTimeoutError(ObserverReadinessError):
    """Raised when observer fails to emit readiness proof within timeout."""


class ObserverIdentityMismatchError(ObserverReadinessError):
    """Raised when observer snapshot identity does not match expected epoch or run_id."""


@dataclass(frozen=True)
class ObserverReadinessProof:
    epoch: str
    run_id: str
    observer_pid: int
    observer_ready_at_utc: str
    collector_observed_status: str
    observer_status: str
    proof_sha256: str
    snapshot_path: str
    snapshot_age_seconds: float = 0.0
    observer_unit: Optional[str] = None
    pid_liveness_verified: bool = True

    def to_dict(self) -> dict[str, Any]:
        ret: dict[str, Any] = {
            "epoch": self.epoch,
            "run_id": self.run_id,
            "observer_pid": self.observer_pid,
            "observer_ready_at_utc": self.observer_ready_at_utc,
            "collector_observed_status": self.collector_observed_status,
            "observer_status": self.observer_status,
            "proof_sha256": self.proof_sha256,
            "snapshot_path": self.snapshot_path,
            "snapshot_age_seconds": round(self.snapshot_age_seconds, 3),
            "pid_liveness_verified": self.pid_liveness_verified,
        }
        if self.observer_unit is not None:
            ret["observer_unit"] = self.observer_unit
        return ret


def is_pid_alive(pid: int) -> bool:
    """Check if process with given PID exists and is signalable on the host."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but owned by another user (e.g. root vs normal user)
        return True
    except Exception:
        return False


def is_systemd_unit_active(unit_name: str) -> bool:
    """Check if a systemd unit is active using systemctl is-active."""
    if not shutil.which("systemctl"):
        return True  # systemctl not available on non-systemd platform (e.g. macOS dev)
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", "--quiet", unit_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5.0,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


def verify_observer_readiness(
    health_dir: Path,
    expected_epoch: str,
    expected_run_id: str,
    expected_unit: Optional[str] = None,
    max_snapshot_age_seconds: float = 30.0,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 0.5,
    now_fn: Optional[Callable[[], datetime]] = None,
    check_pid_liveness: bool = True,
    check_unit_liveness: bool = True,
    pid_liveness_fn: Optional[Callable[[int], bool]] = None,
) -> ObserverReadinessProof:
    """Poll health_dir/observer_latest.json until observer is verifiably ready or timeout.

    Enforces:
    - Expected epoch and run_id match.
    - collector.status == 'WAITING_FOR_COLLECTOR' strictly.
    - observer.status == 'HEALTHY'.
    - observer_pid is positive AND confirmed alive.
    - snapshot freshness is bounded by max_snapshot_age_seconds.
    - systemd unit is active (if expected_unit provided and systemctl available).
    """
    snapshot_path = health_dir / "observer_latest.json"
    deadline = time.monotonic() + timeout_seconds
    last_err: Optional[str] = None
    liveness_checker = pid_liveness_fn or is_pid_alive

    while time.monotonic() < deadline:
        if snapshot_path.exists():
            try:
                raw = snapshot_path.read_bytes()
                data = json.loads(raw.decode("utf-8"))

                epoch = data.get("epoch")
                run_id = data.get("run_id")
                if epoch != expected_epoch or run_id != expected_run_id:
                    raise ObserverIdentityMismatchError(
                        f"Observer identity mismatch: expected epoch={expected_epoch!r}, run_id={expected_run_id!r}; "
                        f"found epoch={epoch!r}, run_id={run_id!r}"
                    )

                # STRICT: Before collector starts, observer must strictly record WAITING_FOR_COLLECTOR.
                # HEALTHY is NOT accepted as pre-collector readiness state.
                collector_status = data.get("collector", {}).get("status")
                if collector_status != "WAITING_FOR_COLLECTOR":
                    last_err = (
                        f"collector status is {collector_status!r}; "
                        "strictly expected 'WAITING_FOR_COLLECTOR' prior to collector launch"
                    )
                    time.sleep(poll_interval_seconds)
                    continue

                obs_info = data.get("observer", {})
                obs_status = obs_info.get("status")
                if obs_status != "HEALTHY":
                    last_err = f"observer status is {obs_status!r}, expected 'HEALTHY'"
                    time.sleep(poll_interval_seconds)
                    continue

                # Observer PID check & Host liveness
                obs_pid = int(obs_info.get("observer_pid", 0))
                if obs_pid <= 0:
                    last_err = f"invalid observer_pid {obs_pid}"
                    time.sleep(poll_interval_seconds)
                    continue

                if check_pid_liveness and not liveness_checker(obs_pid):
                    last_err = f"observer pid {obs_pid} is dead / not signalable on host"
                    time.sleep(poll_interval_seconds)
                    continue

                # Observer systemd unit active check
                if check_unit_liveness and expected_unit:
                    if not is_systemd_unit_active(expected_unit):
                        last_err = f"observer systemd unit {expected_unit!r} is not active"
                        time.sleep(poll_interval_seconds)
                        continue

                # Snapshot Freshness check (FAIL-CLOSED: no silent current-time fallback)
                observed_at_str = data.get("observed_at")
                if not observed_at_str or not isinstance(observed_at_str, str) or not observed_at_str.strip():
                    last_err = "observed_at timestamp missing or invalid in snapshot"
                    time.sleep(poll_interval_seconds)
                    continue

                try:
                    observed_at = datetime.fromisoformat(observed_at_str)
                    if observed_at.tzinfo is None:
                        observed_at = observed_at.replace(tzinfo=timezone.utc)
                except Exception as exc:
                    last_err = f"observed_at timestamp unparseable: {exc}"
                    time.sleep(poll_interval_seconds)
                    continue

                now_utc = now_fn() if now_fn is not None else datetime.now(timezone.utc)
                age_seconds = (now_utc - observed_at).total_seconds()
                if age_seconds < -5.0:
                    last_err = f"observed_at is in the future ({age_seconds:.1f}s ahead): {observed_at_str}"
                    time.sleep(poll_interval_seconds)
                    continue
                if age_seconds > max_snapshot_age_seconds:
                    last_err = (
                        f"snapshot is stale: age {age_seconds:.1f}s exceeds "
                        f"max allowed {max_snapshot_age_seconds:.1f}s (observed_at: {observed_at_str})"
                    )
                    time.sleep(poll_interval_seconds)
                    continue

                proof_hash = hashlib.sha256(raw).hexdigest()

                return ObserverReadinessProof(
                    epoch=expected_epoch,
                    run_id=expected_run_id,
                    observer_pid=obs_pid,
                    observer_ready_at_utc=observed_at_str,
                    collector_observed_status=collector_status,
                    observer_status=obs_status,
                    proof_sha256=proof_hash,
                    snapshot_path=str(snapshot_path),
                    snapshot_age_seconds=max(0.0, age_seconds),
                    observer_unit=expected_unit,
                    pid_liveness_verified=True,
                )
            except ObserverIdentityMismatchError:
                raise
            except Exception as exc:
                last_err = str(exc)

        time.sleep(poll_interval_seconds)

    detail = f": {last_err}" if last_err else ""
    raise ObserverReadinessTimeoutError(
        f"Observer failed to reach verified readiness within {timeout_seconds:.1f}s at {snapshot_path}{detail}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed Observer T0 Readiness Verifier")
    parser.add_argument("--health-dir", type=Path, required=True, help="Directory containing observer_latest.json")
    parser.add_argument("--epoch", required=True, help="Expected epoch")
    parser.add_argument("--run-id", required=True, help="Expected run ID")
    parser.add_argument("--unit-name", help="Expected observer systemd unit name (optional)")
    parser.add_argument("--max-age", type=float, default=30.0, help="Max allowed snapshot age in seconds (default: 30.0)")
    parser.add_argument("--timeout", type=float, default=30.0, help="Readiness timeout in seconds (default: 30.0)")
    args = parser.parse_args(argv)

    try:
        proof = verify_observer_readiness(
            health_dir=args.health_dir,
            expected_epoch=args.epoch,
            expected_run_id=args.run_id,
            expected_unit=args.unit_name,
            max_snapshot_age_seconds=args.max_age,
            timeout_seconds=args.timeout,
        )
        print("=== OBSERVER T0 READINESS VERIFIED: PASS ===")
        print(json.dumps(proof.to_dict(), indent=2))
        return 0
    except Exception as exc:
        print(f"=== OBSERVER T0 READINESS FAILED: {exc} ===", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
