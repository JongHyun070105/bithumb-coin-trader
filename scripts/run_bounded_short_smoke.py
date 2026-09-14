#!/usr/bin/env python3
"""Run one bounded collector lifecycle and persist authoritative JSON evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from bithumb_coin_trader.bounded_supervisor import BoundedSupervisor, SupervisorConfig, V3ScheduleConfig


def _command(value: str) -> tuple[str, ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not parsed or not all(isinstance(item, str) and item for item in parsed):
        raise argparse.ArgumentTypeError("command JSON must be a non-empty array of strings")
    return tuple(parsed)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--collection-duration-seconds", type=float, default=0.0)
    parser.add_argument("--finalization-timeout-seconds", type=float, default=45.0)
    parser.add_argument("--hard-ceiling-seconds", type=float)
    parser.add_argument("--collector-command-json", type=_command, required=True)
    parser.add_argument("--publisher-command-json", type=_command)
    parser.add_argument("--archive-scheduler-command-json", type=_command)
    parser.add_argument("--metrics-path", type=Path, required=True)
    parser.add_argument("--collector-lifecycle-path", type=Path, required=True)
    parser.add_argument("--result-path", type=Path, required=True)
    parser.add_argument("--log-path", type=Path, required=True)
    parser.add_argument("--poll-interval-seconds", type=float, default=0.2)
    parser.add_argument("--publisher-interval-seconds", type=float, default=60.0)
    parser.add_argument("--shutdown-grace-seconds", type=float, default=45.0)
    parser.add_argument("--require-full-duration", action="store_true")
    parser.add_argument("--required-qualifying-full-hours", type=int)
    parser.add_argument("--maximum-collection-window-seconds", type=int)
    parser.add_argument("--qualification-schedule-path", type=Path)
    args = parser.parse_args(argv)

    v3_schedule = None
    if args.required_qualifying_full_hours is not None:
        v3_schedule = V3ScheduleConfig(
            required_qualifying_full_hours=args.required_qualifying_full_hours,
            maximum_collection_window_seconds=args.maximum_collection_window_seconds,
            schedule_path=args.qualification_schedule_path,
        )
    return BoundedSupervisor(
        SupervisorConfig(
            run_id=args.run_id,
            collection_duration_seconds=args.collection_duration_seconds,
            finalization_timeout_seconds=args.finalization_timeout_seconds,
            hard_ceiling_seconds=args.hard_ceiling_seconds,
            collector_command=args.collector_command_json,
            publisher_command=args.publisher_command_json,
            archive_scheduler_command=args.archive_scheduler_command_json,
            metrics_path=args.metrics_path,
            collector_lifecycle_path=args.collector_lifecycle_path,
            result_path=args.result_path,
            log_path=args.log_path,
            poll_interval_seconds=args.poll_interval_seconds,
            publisher_interval_seconds=args.publisher_interval_seconds,
            shutdown_grace_seconds=args.shutdown_grace_seconds,
            require_full_duration=args.require_full_duration,
            v3_schedule=v3_schedule,
        )
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
