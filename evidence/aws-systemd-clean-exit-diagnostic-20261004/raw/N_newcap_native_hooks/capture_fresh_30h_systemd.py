#!/usr/bin/env python3
"""Runtime systemd terminal evidence for the Frozen V2 contract.

Empirical host facts (systemd 252, see evidence/aws-systemd-clean-exit-diagnostic-20261004):
  * a clean exit never produces a "Main process exited" manager record;
  * `journalctl _SYSTEMD_INVOCATION_ID=<id>` returns only unit-native rows (never manager rows);
  * transient units are garbage-collected after ExecStopPost, so `systemctl show` is only
    truthful while ExecStopPost runs (ActiveState=deactivating, SubState=stop-post).

Subcommands:
  start-marker  ExecStartPre: journal exactly one unit-native "Starting <unit>" row.
  stop-post     ExecStopPost: snapshot live `systemctl show`, cross-check it against the
                systemd-provided EXIT_CODE/EXIT_STATUS/SERVICE_RESULT/INVOCATION_ID, persist
                systemd-terminal.json, then journal exactly one unit-native
                "main process exited, code=<EXIT_CODE>, status=<EXIT_STATUS>" row.
  export        Post-run (privileged): read the invocation journal, verify that every row is
                unit-native and bound to the snapshot, and write the terminal evidence files.
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


SAFE_UNIT = re.compile(r"^bitcoin-trader-[A-Za-z0-9][A-Za-z0-9._-]{0,150}\.service$")
INVOCATION_ID = re.compile(r"^[0-9a-fA-F]{32}$")
SNAPSHOT_NAME = "systemd-terminal.json"
JOURNAL_NAME = "systemd-invocation.jsonl"
_SYSTEMD_CODE = {"1": "exited", "2": "killed", "3": "dumped"}
_ENV_CODE = {"exited", "killed", "dumped"}
_SHOW_PROPERTIES = (
    "InvocationID,LoadState,ActiveState,SubState,Result,ExecMainStatus,ExecMainCode,MainPID,"
    "ExecMainPID,NRestarts,ExecMainStartTimestamp,ExecMainExitTimestamp,WatchdogUSec"
)
_START_RE = re.compile(r"\bStarting\b", re.IGNORECASE)
_EXIT_RE = re.compile(r"main process exited,\s*code=([a-zA-Z0-9_-]+),\s*status=(-?[0-9]+)", re.IGNORECASE)
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


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
    # `--timestamp=us+utc` keeps microseconds; whole-second floors would sort before the supervisor's own ended_at.
    fmt = "%a %Y-%m-%d %H:%M:%S.%f %Z" if "." in value else "%a %Y-%m-%d %H:%M:%S %Z"
    try:
        parsed = datetime.strptime(value, fmt)
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


def _check_unit(unit: str) -> None:
    if not SAFE_UNIT.fullmatch(unit):
        raise ValueError("unit must be an exact run-specific bitcoin-trader service")


def start_marker(*, unit: str, environ: Mapping[str, str], out=None) -> str:
    """ExecStartPre: one unit-native start row bound to the live systemd InvocationID."""
    _check_unit(unit)
    invocation = environ.get("INVOCATION_ID", "")
    if not INVOCATION_ID.fullmatch(invocation):
        raise ValueError("systemd did not provide a valid INVOCATION_ID")
    line = f"Starting {unit}"
    stream = out if out is not None else sys.stdout
    stream.write(line + "\n")
    stream.flush()
    return line


def snapshot_terminal(
    *,
    unit: str,
    output_dir: Path,
    environ: Mapping[str, str],
    command: Runner = subprocess.run,
    out=None,
) -> tuple[Path, str]:
    """ExecStopPost: persist the live terminal snapshot, then journal the native terminal row."""
    _check_unit(unit)
    if output_dir.is_symlink() or (output_dir.exists() and not output_dir.is_dir()):
        raise ValueError("terminal snapshot output directory is not a real directory")
    env_invocation = environ.get("INVOCATION_ID", "")
    env_code = environ.get("EXIT_CODE", "")
    env_status = environ.get("EXIT_STATUS", "")
    env_result = environ.get("SERVICE_RESULT", "")
    if not INVOCATION_ID.fullmatch(env_invocation):
        raise ValueError("systemd did not provide a valid INVOCATION_ID")
    if env_code not in _ENV_CODE or not re.fullmatch(r"-?[0-9]+", env_status) or not env_result:
        raise ValueError("systemd did not provide EXIT_CODE/EXIT_STATUS/SERVICE_RESULT")

    shown = command(
        ["systemctl", "show", "--timestamp=us+utc", unit, f"--property={_SHOW_PROPERTIES}"],
        capture_output=True, text=True, timeout=30, check=False,
        env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
    )
    if shown.returncode != 0:
        raise RuntimeError(f"systemctl show failed with exit code {shown.returncode}")
    props = _parse_properties(shown.stdout)
    if props.get("LoadState") != "loaded" or props.get("ActiveState") != "deactivating" or props.get("SubState") != "stop-post":
        raise ValueError("systemctl show is not a live stop-post snapshot; refusing garbage-collected defaults")
    invocation = props.get("InvocationID", "")
    if not INVOCATION_ID.fullmatch(invocation) or invocation.lower() != env_invocation.lower():
        raise ValueError("systemctl InvocationID disagrees with the systemd-provided INVOCATION_ID")
    code = _SYSTEMD_CODE.get(props.get("ExecMainCode", ""))
    if code is None or code != env_code:
        raise ValueError("systemctl ExecMainCode disagrees with the systemd-provided EXIT_CODE")
    try:
        status = int(props["ExecMainStatus"])
        main_pid = int(props["ExecMainPID"])
        restarts = int(props["NRestarts"])
    except (KeyError, ValueError) as exc:
        raise ValueError("systemctl terminal status contains a missing or invalid integer") from exc
    if status != int(env_status):
        raise ValueError("systemctl ExecMainStatus disagrees with the systemd-provided EXIT_STATUS")
    result = props.get("Result", "")
    if not result or result != env_result:
        raise ValueError("systemctl Result disagrees with the systemd-provided SERVICE_RESULT")
    if main_pid <= 0 or restarts < 0:
        raise ValueError("systemctl terminal status has invalid MainPID or NRestarts")
    started = _parse_systemd_time(props.get("ExecMainStartTimestamp", ""), "start")
    stopped = _parse_systemd_time(props.get("ExecMainExitTimestamp", ""), "exit")
    if stopped < started:
        raise ValueError("systemctl exit timestamp precedes the process start")
    if result == "watchdog":
        watchdog_result = "triggered"
    elif result in {"success", "exit-code", "signal", "core-dump", "timeout", "resources", "protocol", "start-limit-hit"}:
        watchdog_result = "not_triggered"
    else:
        watchdog_result = "unknown"
    terminal = {
        "unit": unit,
        "InvocationID": invocation.lower(),
        "Result": result,
        "ExecMainStatus": status,
        "ExecMainCode": code,
        "MainPID": main_pid,
        "NRestarts": restarts,
        "start_time": started.isoformat().replace("+00:00", "Z"),
        "stop_time": stopped.isoformat().replace("+00:00", "Z"),
        "runtime_duration_seconds": int(round((stopped - started).total_seconds())),
        "watchdog_result": watchdog_result,
        "received_signal": status if code in {"killed", "dumped"} else None,
        "capture_source": "systemctl show during ExecStopPost; cross-checked with systemd EXIT_CODE/EXIT_STATUS/SERVICE_RESULT",
    }
    path = output_dir / SNAPSHOT_NAME
    _atomic_create(path, (json.dumps(terminal, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))
    line = f"main process exited, code={code}, status={status}"
    stream = out if out is not None else sys.stdout
    stream.write(line + "\n")
    stream.flush()
    return path, line


def export_terminal(
    *,
    unit: str,
    snapshot: Path,
    output_dir: Path,
    expected_invocation_id: str | None = None,
    command: Runner = subprocess.run,
) -> tuple[Path, Path]:
    """Post-run (privileged): verify the invocation journal against the snapshot and write evidence."""
    _check_unit(unit)
    if expected_invocation_id is not None and not INVOCATION_ID.fullmatch(expected_invocation_id):
        raise ValueError("expected InvocationID must be 32 hexadecimal characters")
    if output_dir.is_symlink() or (output_dir.exists() and not output_dir.is_dir()):
        raise ValueError("terminal evidence output path is not a real directory")
    if snapshot.is_symlink() or not snapshot.is_file():
        raise ValueError("systemd terminal snapshot is missing or not a regular file")
    snapshot_bytes = snapshot.read_bytes()
    terminal = json.loads(snapshot_bytes)
    if not isinstance(terminal, dict) or terminal.get("unit") != unit:
        raise ValueError("systemd terminal snapshot is not bound to this unit")
    invocation = str(terminal.get("InvocationID", "")).lower()
    if not INVOCATION_ID.fullmatch(invocation):
        raise ValueError("systemd terminal snapshot has no valid InvocationID")
    if expected_invocation_id and invocation != expected_invocation_id.lower():
        raise ValueError("snapshot InvocationID differs from the expected run invocation")

    journal = command(
        ["journalctl", "--no-pager", "--output=json", "--utc", f"_SYSTEMD_INVOCATION_ID={invocation}"],
        capture_output=True, text=True, timeout=300, check=False,
        env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
    )
    if journal.returncode != 0:
        raise RuntimeError(f"journalctl invocation query failed with exit code {journal.returncode}")
    lines = [line for line in journal.stdout.splitlines() if line.strip()]
    if not lines:
        raise ValueError("journalctl returned no rows for the exact InvocationID (unprivileged reader?)")
    starts = 0
    exits: list[tuple[str, int]] = []
    previous = -1
    for number, line in enumerate(lines, start=1):
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"journalctl row {number} is not an object")
        if str(row.get("_SYSTEMD_INVOCATION_ID", "")).lower() != invocation:
            raise ValueError(f"journalctl row {number} is outside the exact InvocationID")
        if row.get("_SYSTEMD_UNIT") != unit:
            raise ValueError(f"journalctl row {number} is not native to the exact unit")
        stamp = row.get("__REALTIME_TIMESTAMP")
        if not isinstance(stamp, str) or not stamp.isdecimal() or int(stamp) < previous:
            raise ValueError(f"journalctl row {number} has an invalid or out-of-order timestamp")
        previous = int(stamp)
        message = row.get("MESSAGE")
        if isinstance(message, str):
            if _START_RE.search(message):
                starts += 1
            match = _EXIT_RE.search(message)
            if match:
                exits.append((match.group(1).lower(), int(match.group(2))))
    if starts != 1:
        raise ValueError(f"expected exactly one unit-native start row, found {starts}")
    if exits != [(str(terminal.get("ExecMainCode")), int(terminal.get("ExecMainStatus", -1)))]:
        raise ValueError("unit-native terminal row is missing, duplicated, or disagrees with the snapshot")
    journal_bytes = journal.stdout.encode("utf-8")
    if not journal_bytes.endswith(b"\n"):
        journal_bytes += b"\n"
    terminal_path = output_dir / SNAPSHOT_NAME
    journal_path = output_dir / JOURNAL_NAME
    _atomic_create(terminal_path, snapshot_bytes)
    _atomic_create(journal_path, journal_bytes)
    return terminal_path, journal_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    start = sub.add_parser("start-marker")
    start.add_argument("--unit", required=True)
    stop = sub.add_parser("stop-post")
    stop.add_argument("--unit", required=True)
    stop.add_argument("--output-dir", required=True, type=Path)
    export = sub.add_parser("export")
    export.add_argument("--unit", required=True)
    export.add_argument("--snapshot", required=True, type=Path)
    export.add_argument("--output-dir", required=True, type=Path)
    export.add_argument("--expected-invocation-id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.mode == "start-marker":
            start_marker(unit=args.unit, environ=os.environ)
        elif args.mode == "stop-post":
            snapshot_terminal(unit=args.unit, output_dir=args.output_dir, environ=os.environ)
        else:
            paths = export_terminal(
                unit=args.unit, snapshot=args.snapshot, output_dir=args.output_dir,
                expected_invocation_id=args.expected_invocation_id,
            )
            print("CAPTURED: " + " ".join(str(path) for path in paths))
    except Exception as exc:
        print(f"CAPTURE_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
