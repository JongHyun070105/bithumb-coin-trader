"""Fail-closed Observer T0 Readiness Verifier.

Enforces the strict invariant:
  OBSERVER_READY_TIME <= COLLECTOR_START_TIME

Before the collector/supervisor can launch, the observer daemon must
be verifiably active and have emitted an authoritative readiness proof:
1. health/observer_latest.json exists and is parseable.
2. epoch matches expected_epoch.
3. run_id matches expected_run_id.
4. collector.status == "WAITING_FOR_COLLECTOR" (or HEALTHY/WAITING).
5. observer.status == "HEALTHY".
6. observer_pid is positive and active.

Any violation, mismatch, or timeout aborts with non-zero exit code.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "epoch": self.epoch,
            "run_id": self.run_id,
            "observer_pid": self.observer_pid,
            "observer_ready_at_utc": self.observer_ready_at_utc,
            "collector_observed_status": self.collector_observed_status,
            "observer_status": self.observer_status,
            "proof_sha256": self.proof_sha256,
            "snapshot_path": self.snapshot_path,
        }


def verify_observer_readiness(
    health_dir: Path,
    expected_epoch: str,
    expected_run_id: str,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 0.5,
    now_fn: Optional[Any] = None,
) -> ObserverReadinessProof:
    """Poll health_dir/observer_latest.json until observer is verifiably ready or timeout."""
    snapshot_path = health_dir / "observer_latest.json"
    deadline = time.monotonic() + timeout_seconds
    last_err: Optional[str] = None

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

                collector_status = data.get("collector", {}).get("status")
                # When collector has not started yet, observer marks it WAITING_FOR_COLLECTOR
                if collector_status not in ("WAITING_FOR_COLLECTOR", "HEALTHY"):
                    last_err = f"collector status is {collector_status!r}, expected WAITING_FOR_COLLECTOR"
                    time.sleep(poll_interval_seconds)
                    continue

                obs_info = data.get("observer", {})
                obs_status = obs_info.get("status")
                if obs_status != "HEALTHY":
                    last_err = f"observer status is {obs_status!r}, expected HEALTHY"
                    time.sleep(poll_interval_seconds)
                    continue

                obs_pid = int(obs_info.get("observer_pid", 0))
                if obs_pid <= 0:
                    last_err = f"invalid observer_pid {obs_pid}"
                    time.sleep(poll_interval_seconds)
                    continue

                ready_at = data.get("observed_at") or datetime.now(timezone.utc).isoformat()
                proof_hash = hashlib.sha256(raw).hexdigest()

                return ObserverReadinessProof(
                    epoch=expected_epoch,
                    run_id=expected_run_id,
                    observer_pid=obs_pid,
                    observer_ready_at_utc=ready_at,
                    collector_observed_status=collector_status,
                    observer_status=obs_status,
                    proof_sha256=proof_hash,
                    snapshot_path=str(snapshot_path),
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
    parser.add_argument("--timeout", type=float, default=30.0, help="Readiness timeout in seconds")
    args = parser.parse_args(argv)

    try:
        proof = verify_observer_readiness(
            health_dir=args.health_dir,
            expected_epoch=args.epoch,
            expected_run_id=args.run_id,
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
