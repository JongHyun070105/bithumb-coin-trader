"""Fail-closed Launch-Time Schedule Freshness Guard.

Enforces that the collector/supervisor invocation strictly respects the
sealed full UTC hour schedule at actual runtime on the host:
1. If invoked BEFORE planned_start_utc:
   - Log early invocation and sleep until planned_start_utc.
2. If invoked within [planned_start_utc, planned_start_utc + max_delay_seconds]:
   - Valid launch window, proceed to launch.
3. If invoked AFTER planned_start_utc + max_delay_seconds, OR at/after qualification_start_utc:
   - FAIL-CLOSED ABORT: Collector MUST NOT START.
   - Identity remains a failed/expired prepared identity.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import sys
import time
from typing import Callable, Optional


class LaunchFreshnessViolationError(RuntimeError):
    """Raised when current execution time violates the sealed schedule start window."""


def parse_utc_iso(ts_str: str) -> datetime:
    clean = ts_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(clean)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def enforce_launch_freshness(
    planned_start_utc: str,
    qualification_start_utc: str,
    max_delay_seconds: float = 60.0,
    now_fn: Optional[Callable[[], datetime]] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
) -> float:
    """Validate runtime freshness against sealed schedule. Sleeps if early, raises if late."""
    _now = now_fn or (lambda: datetime.now(timezone.utc))
    _sleep = sleep_fn or time.sleep

    planned_start = parse_utc_iso(planned_start_utc)
    qual_start = parse_utc_iso(qualification_start_utc)

    now = _now()

    # 1. Early invocation: sleep until planned start
    if now < planned_start:
        remaining = (planned_start - now).total_seconds()
        print(
            f"[LAUNCH_FRESHNESS_GUARD] Invoked {remaining:.1f}s early (now={now.isoformat()}, planned={planned_start_utc}). "
            f"Waiting until planned start boundary...",
            flush=True,
        )
        _sleep(remaining)
        now = _now()

    # 2. Qualification boundary check: collector MUST be running before qualification starts
    if now >= qual_start:
        raise LaunchFreshnessViolationError(
            f"QUALIFICATION_BOUNDARY_VIOLATED: current time {now.isoformat()} has crossed qualification start {qualification_start_utc}. "
            f"A complete qualifying full UTC hour cannot be guaranteed. COLLECTOR WILL NOT START."
        )

    # 3. Maximum start delay check: cannot start too late past planned start
    delay = (now - planned_start).total_seconds()
    if delay > max_delay_seconds:
        raise LaunchFreshnessViolationError(
            f"LAUNCH_FRESHNESS_VIOLATION: current time {now.isoformat()} is {delay:.1f}s past planned start {planned_start_utc} "
            f"(maximum permitted delay is {max_delay_seconds:.1f}s). COLLECTOR WILL NOT START."
        )

    print(
        f"[LAUNCH_FRESHNESS_GUARD] Launch freshness verified: now={now.isoformat()}, "
        f"delay={delay:.2f}s <= {max_delay_seconds}s, qual_start={qualification_start_utc} (PASS).",
        flush=True,
    )
    return delay


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed Launch Freshness Guard")
    parser.add_argument("--planned-start", required=True, help="Sealed planned collector start in UTC ISO format")
    parser.add_argument("--qualification-start", required=True, help="Sealed qualification start in UTC ISO format")
    parser.add_argument("--max-delay", type=float, default=60.0, help="Maximum allowed launch delay in seconds")
    args = parser.parse_args(argv)

    try:
        enforce_launch_freshness(
            planned_start_utc=args.planned_start,
            qualification_start_utc=args.qualification_start,
            max_delay_seconds=args.max_delay,
        )
        print("=== LAUNCH FRESHNESS GUARD: PASS ===")
        return 0
    except Exception as exc:
        print(f"=== LAUNCH FRESHNESS GUARD FAILED: {exc} ===", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
