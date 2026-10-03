from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from scripts.capture_fresh_30h_systemd import capture_systemd_terminal


INVOCATION = "0123456789abcdef0123456789abcdef"
UNIT = "bitcoin-trader-30h-capture-test.service"


def _properties(**overrides: str) -> str:
    props = {
        "InvocationID": INVOCATION,
        "Result": "success",
        "ExecMainStatus": "0",
        "ExecMainCode": "1",
        "MainPID": "0",
        "ExecMainPID": "2468",
        "NRestarts": "0",
        "ExecMainStartTimestamp": "Sat 2026-10-03 10:00:00 UTC",
        "ExecMainExitTimestamp": "Sat 2026-10-03 10:30:00 UTC",
        "WatchdogUSec": "0",
    }
    props.update(overrides)
    return "".join(f"{key}={value}\n" for key, value in props.items())


def _runner(rows: list[dict[str, object]], commands: list[list[str]] | None = None):
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if commands is not None:
            commands.append(command)
        if command[0] == "systemctl":
            return subprocess.CompletedProcess(command, 0, _properties(), "")
        return subprocess.CompletedProcess(
            command,
            0,
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            "",
        )

    return run


def test_captures_all_invocation_rows_including_init_scope_and_clean_exit_without_expected_message(
    tmp_path: Path,
) -> None:
    rows = [
        {
            "_SYSTEMD_INVOCATION_ID": INVOCATION,
            "_SYSTEMD_UNIT": UNIT,
            "MESSAGE": "Starting collector",
            "__REALTIME_TIMESTAMP": "1791021600000000",
        },
        {
            "_SYSTEMD_INVOCATION_ID": INVOCATION,
            "_SYSTEMD_UNIT": "init.scope",
            "MESSAGE": "systemd manager recorded service completion",
            "__REALTIME_TIMESTAMP": "1791023400000000",
        },
        {
            "_SYSTEMD_INVOCATION_ID": INVOCATION,
            "_SYSTEMD_UNIT": UNIT,
            "MESSAGE": "Deactivated successfully.",
            "__REALTIME_TIMESTAMP": "1791023400000000",
        },
    ]
    commands: list[list[str]] = []
    terminal_path, journal_path = capture_systemd_terminal(
        unit=UNIT,
        output_dir=tmp_path / "terminal",
        expected_invocation_id=INVOCATION,
        command=_runner(rows, commands),
    )

    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    captured_rows = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    assert terminal["InvocationID"] == INVOCATION
    assert terminal["ExecMainCode"] == "exited"
    assert terminal["MainPID"] == 2468
    assert terminal["runtime_duration_seconds"] == 1800
    assert [row["_SYSTEMD_UNIT"] for row in captured_rows] == [UNIT, "init.scope", UNIT]
    journal_command = commands[1]
    assert f"_SYSTEMD_INVOCATION_ID={INVOCATION}" in journal_command
    assert not any(arg.startswith("_SYSTEMD_UNIT=") for arg in journal_command)


def test_rejects_journal_row_from_another_invocation(tmp_path: Path) -> None:
    rows = [{
        "_SYSTEMD_INVOCATION_ID": "f" * 32,
        "_SYSTEMD_UNIT": UNIT,
        "MESSAGE": "unrelated",
        "__REALTIME_TIMESTAMP": "1791021600000000",
    }]
    with pytest.raises(ValueError, match="outside the exact InvocationID"):
        capture_systemd_terminal(unit=UNIT, output_dir=tmp_path / "terminal", command=_runner(rows))


def test_expected_invocation_mismatch_stops_before_journal_query(tmp_path: Path) -> None:
    commands: list[list[str]] = []
    with pytest.raises(ValueError, match="differs from the expected run invocation"):
        capture_systemd_terminal(
            unit=UNIT,
            output_dir=tmp_path / "terminal",
            expected_invocation_id="f" * 32,
            command=_runner([], commands),
        )
    assert len(commands) == 1


def test_capture_is_create_only(tmp_path: Path) -> None:
    out_dir = tmp_path / "terminal"
    row = {"_SYSTEMD_INVOCATION_ID": INVOCATION, "_SYSTEMD_UNIT": UNIT, "MESSAGE": "done",
           "__REALTIME_TIMESTAMP": "1791023400000000"}
    capture_systemd_terminal(unit=UNIT, output_dir=out_dir, command=_runner([row]))
    with pytest.raises(FileExistsError):
        capture_systemd_terminal(unit=UNIT, output_dir=out_dir, command=_runner([row]))
