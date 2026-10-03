#!/usr/bin/env python3
"""Capture exact systemd terminal properties and the complete invocation journal.

The journal query is scoped by the run's systemd InvocationID. It deliberately
retains manager records such as init.scope when systemd associates them with the
same invocation; it does not filter journal rows by _SYSTEMD_UNIT.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable, Mapping, Sequence


SAFE_UNIT = re.compile(r"^bitcoin-trader-30h-[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.service$")
INVOCATION_ID = re.compile(r"^[0-9a-fA-F]{32}$")
_SYSTEMD_CODE = {"0": "", "1": "exited", "2": "killed", "3": "dumped"}


def _parse_properties(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in result:
            raise ValueError(f"systemctl returned duplicate property: {key}")
        result[key] = value
    return result


def _parse_systemd_time(value: str, field: str) -> datetime:
    try:
        # LC_ALL=C and TZ=UTC make systemctl's timestamp format deterministic.
        parsed = datetime.strptime(value, "%a %Y-%m-%d %H:%M:%S %Z")
    except ValueError as exc:
        raise ValueError(f"systemctl returned an invalid {field} timestamp") from exc
    if value.rsplit(" ", 1)[-1] != "UTC":
        raise ValueError(f"systemctl {field} timestamp is not explicitly UTC")
    return parsed.replace(tzinfo=timezone.utc)


def _atomic_create(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError(f"evidence destination must not be a symlink: {path}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        if stream.write(data) != len(data):
            raise OSError(f"short write while capturing {path}")
        stream.flush()
        os.fsync(stream.fileno())
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def capture_systemd_terminal(
    *,
    unit: str,
    output_dir: Path,
    expected_invocation_id: str | None = None,
    command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[Path, Path]:
    """Capture parsed systemd properties and every JSON journal row for its invocation."""
    if not SAFE_UNIT.fullmatch(unit):
        raise ValueError("unit must be the exact run-specific bitcoin-trader-30h service")
    if expected_invocation_id is not None and not INVOCATION_ID.fullmatch(expected_invocation_id):
        raise ValueError("expected InvocationID must be 32 hexadecimal characters")
    if output_dir.is_symlink():
        raise ValueError("terminal evidence output directory must not be a symlink")
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError("terminal evidence output path is not a directory")

    env = {**os.environ, "LC_ALL": "C", "TZ": "UTC"}
    shown = command(
        ["systemctl", "show", unit,
         "--property=InvocationID,Result,ExecMainStatus,ExecMainCode,MainPID,ExecMainPID,NRestarts,ExecMainStartTimestamp,ExecMainExitTimestamp,WatchdogUSec"],
        capture_output=True, text=True, timeout=30, check=False, env=env,
    )
    if shown.returncode != 0:
        raise RuntimeError(f"systemctl show failed with exit code {shown.returncode}")
    props = _parse_properties(shown.stdout)
    invocation = props.get("InvocationID", "")
    if not INVOCATION_ID.fullmatch(invocation):
        raise ValueError("systemctl did not return a valid InvocationID")
    if expected_invocation_id and invocation.lower() != expected_invocation_id.lower():
        raise ValueError("systemd unit InvocationID differs from the expected run invocation")

    exit_code = props.get("ExecMainCode", "")
    if exit_code not in _SYSTEMD_CODE or not _SYSTEMD_CODE[exit_code]:
        raise ValueError("systemctl returned an unsupported ExecMainCode")
    try:
        status = int(props["ExecMainStatus"])
        main_pid = int(props.get("ExecMainPID", props.get("MainPID", "")))
        restarts = int(props["NRestarts"])
    except (KeyError, ValueError) as exc:
        raise ValueError("systemctl terminal status contains a missing or invalid integer") from exc
    if main_pid <= 0 or restarts < 0:
        raise ValueError("systemctl terminal status has invalid MainPID or NRestarts")
    started = _parse_systemd_time(props.get("ExecMainStartTimestamp", ""), "start")
    stopped = _parse_systemd_time(props.get("ExecMainExitTimestamp", ""), "exit")
    if stopped < started:
        raise ValueError("systemctl exit timestamp precedes the process start")
    result = props.get("Result", "")
    if not result:
        raise ValueError("systemctl Result is missing")
    if result == "watchdog":
        watchdog_result = "triggered"
    elif result in {"success", "exit-code", "signal", "core-dump", "timeout", "resources", "protocol", "start-limit-hit"}:
        watchdog_result = "not_triggered"
    else:
        watchdog_result = "unknown"

    received_signal: int | None = None
    if _SYSTEMD_CODE[exit_code] in {"killed", "dumped"}:
        received_signal = status
    terminal = {
        "unit": unit,
        "InvocationID": invocation.lower(),
        "Result": result,
        "ExecMainStatus": status,
        "ExecMainCode": _SYSTEMD_CODE[exit_code],
        "MainPID": main_pid,
        "NRestarts": restarts,
        "start_time": started.isoformat().replace("+00:00", "Z"),
        "stop_time": stopped.isoformat().replace("+00:00", "Z"),
        "runtime_duration_seconds": int((stopped - started).total_seconds()),
        "watchdog_result": watchdog_result,
        "received_signal": received_signal,
        "capture_source": "systemctl show",
    }

    journal = command(
        ["journalctl", "--no-pager", "--output=json", "--utc", f"_SYSTEMD_INVOCATION_ID={invocation.lower()}"],
        capture_output=True, text=True, timeout=300, check=False, env=env,
    )
    if journal.returncode != 0:
        raise RuntimeError(f"journalctl invocation query failed with exit code {journal.returncode}")
    lines = [line for line in journal.stdout.splitlines() if line.strip()]
    if not lines:
        raise ValueError("journalctl returned no rows for the exact InvocationID")
    for line_number, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"journalctl returned invalid JSON on row {line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"journalctl row {line_number} is not an object")
        row_id = row.get("_SYSTEMD_INVOCATION_ID", row.get("INVOCATION_ID"))
        if not isinstance(row_id, str) or row_id.lower() != invocation.lower():
            raise ValueError(f"journalctl row {line_number} is outside the exact InvocationID")
    # Preserve journalctl output verbatim, including manager records bound to this invocation.
    journal_bytes = journal.stdout.encode("utf-8")
    if not journal_bytes.endswith(b"\n"):
        journal_bytes += b"\n"
    terminal_bytes = (json.dumps(terminal, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    terminal_path = output_dir / "systemd-terminal.json"
    journal_path = output_dir / "systemd-invocation.jsonl"
    _atomic_create(terminal_path, terminal_bytes)
    try:
        _atomic_create(journal_path, journal_bytes)
    except BaseException:
        # Preserve create-only semantics; leave the already-created terminal file as evidence
        # of an incomplete capture instead of deleting it on an error.
        raise
    return terminal_path, journal_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-invocation-id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        paths = capture_systemd_terminal(
            unit=args.unit, output_dir=args.output_dir,
            expected_invocation_id=args.expected_invocation_id,
        )
    except Exception as exc:
        print(f"CAPTURE_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("CAPTURED: " + " ".join(str(path) for path in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
