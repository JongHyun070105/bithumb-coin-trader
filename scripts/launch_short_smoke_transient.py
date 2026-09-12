#!/usr/bin/env python3
"""Render or explicitly launch the reviewed transient systemd smoke unit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Sequence

from bithumb_coin_trader.bounded_supervisor import TransientLaunchConfig, render_systemd_run


def _command(value: str) -> tuple[str, ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not parsed or not all(isinstance(item, str) and item for item in parsed):
        raise argparse.ArgumentTypeError("supervisor command must be a non-empty JSON array of strings")
    return tuple(parsed)


def _extract_supervisor_duration(supervisor_command: Sequence[str]) -> int:
    durations: list[int] = []
    idx = 0
    while idx < len(supervisor_command):
        token = supervisor_command[idx]
        if token == "--collection-duration-seconds":
            if idx + 1 >= len(supervisor_command):
                raise ValueError("missing value for supervisor command flag: --collection-duration-seconds")
            raw = supervisor_command[idx + 1]
            try:
                val = float(raw)
            except ValueError as err:
                raise ValueError(f"invalid supervisor command duration: {raw!r}") from err
            if not val.is_integer():
                raise ValueError(f"supervisor command duration must be integer: {raw!r}")
            durations.append(int(val))
            idx += 2
            continue
        if token.startswith("--collection-duration-seconds="):
            raw = token[len("--collection-duration-seconds=") :]
            if not raw:
                raise ValueError("missing value for supervisor command flag: --collection-duration-seconds=")
            try:
                val = float(raw)
            except ValueError as err:
                raise ValueError(f"invalid supervisor command duration: {raw!r}") from err
            if not val.is_integer():
                raise ValueError(f"supervisor command duration must be integer: {raw!r}")
            durations.append(int(val))
            idx += 1
            continue
        idx += 1

    if len(durations) == 0:
        raise ValueError("supervisor command must declare exactly one collection duration")
    if len(durations) > 1:
        raise ValueError("duplicate supervisor collection duration")
    return durations[0]


def _validate_cross_layer_duration(
    launcher_duration: int,
    supervisor_command: Sequence[str],
) -> None:
    supervisor_duration = _extract_supervisor_duration(supervisor_command)
    if supervisor_duration != launcher_duration:
        raise ValueError(
            f"collection duration mismatch: launcher declared {launcher_duration}s "
            f"but supervisor command specifies {supervisor_duration}s"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--supervisor-command-json", type=_command, required=True)
    parser.add_argument("--collection-duration-seconds", type=int, default=2700)
    parser.add_argument("--finalization-timeout-seconds", type=int, default=120)
    parser.add_argument("--supervisor-hard-ceiling-seconds", type=int, default=2820)
    parser.add_argument("--systemd-runtime-max-seconds", type=int, default=2880)
    parser.add_argument("--launch", action="store_true")
    args = parser.parse_args(argv)
    _validate_cross_layer_duration(args.collection_duration_seconds, args.supervisor_command_json)
    command = render_systemd_run(
        TransientLaunchConfig(
            run_id=args.run_id,
            workdir=args.workdir,
            supervisor_command=args.supervisor_command_json,
            collection_duration_seconds=args.collection_duration_seconds,
            finalization_timeout_seconds=args.finalization_timeout_seconds,
            supervisor_hard_ceiling_seconds=args.supervisor_hard_ceiling_seconds,
            systemd_runtime_max_seconds=args.systemd_runtime_max_seconds,
        )
    )
    if not args.launch:
        print(json.dumps(command, indent=2))
        return 0
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
