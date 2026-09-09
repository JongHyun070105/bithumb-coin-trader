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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--supervisor-command-json", type=_command, required=True)
    parser.add_argument("--finalization-timeout-seconds", type=int, default=120)
    parser.add_argument("--supervisor-hard-ceiling-seconds", type=int, default=2820)
    parser.add_argument("--systemd-runtime-max-seconds", type=int, default=2880)
    parser.add_argument("--launch", action="store_true")
    args = parser.parse_args(argv)
    command = render_systemd_run(
        TransientLaunchConfig(
            run_id=args.run_id,
            workdir=args.workdir,
            supervisor_command=args.supervisor_command_json,
            collection_duration_seconds=2700,
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
