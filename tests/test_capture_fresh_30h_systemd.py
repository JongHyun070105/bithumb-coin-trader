from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from scripts.capture_fresh_30h_systemd import (
    export_terminal,
    snapshot_terminal,
    start_marker,
)


INVOCATION = "0123456789abcdef0123456789abcdef"
UNIT = "bitcoin-trader-30h-capture-test.service"
ENV = {"INVOCATION_ID": INVOCATION, "EXIT_CODE": "exited", "EXIT_STATUS": "0", "SERVICE_RESULT": "success"}


def _properties(**overrides: str) -> str:
    props = {
        "InvocationID": INVOCATION,
        "LoadState": "loaded",
        "ActiveState": "deactivating",
        "SubState": "stop-post",
        "Result": "success",
        "ExecMainStatus": "0",
        "ExecMainCode": "1",
        "MainPID": "0",
        "ExecMainPID": "2468",
        "NRestarts": "0",
        "ExecMainStartTimestamp": "Sat 2026-10-03 10:00:00 UTC",
        "ExecMainExitTimestamp": "Sat 2026-10-03 10:30:00 UTC",
        "WatchdogUSec": "1min",
    }
    props.update(overrides)
    return "".join(f"{key}={value}\n" for key, value in props.items())


def _show(**overrides: str):
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[:4] == ["systemctl", "show", "--timestamp=us+utc", UNIT]
        return subprocess.CompletedProcess(command, 0, _properties(**overrides), "")

    return run


def _journal(rows: list[dict[str, object]], commands: list[list[str]] | None = None):
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if commands is not None:
            commands.append(command)
        return subprocess.CompletedProcess(command, 0, "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), "")

    return run


def _row(message: str, stamp: int, unit: str = UNIT, invocation: str = INVOCATION) -> dict[str, object]:
    return {
        "_SYSTEMD_INVOCATION_ID": invocation,
        "_SYSTEMD_UNIT": unit,
        "MESSAGE": message,
        "__REALTIME_TIMESTAMP": str(1791021600000000 + stamp),
    }


def test_start_marker_emits_exactly_one_start_line() -> None:
    out = io.StringIO()
    assert start_marker(unit=UNIT, environ=ENV, out=out) == f"Starting {UNIT}"
    assert out.getvalue() == f"Starting {UNIT}\n"


def test_start_marker_requires_systemd_invocation_id() -> None:
    with pytest.raises(ValueError, match="INVOCATION_ID"):
        start_marker(unit=UNIT, environ={}, out=io.StringIO())


def test_stop_post_snapshot_is_cross_checked_and_emits_native_exit_row(tmp_path: Path) -> None:
    out = io.StringIO()
    path, line = snapshot_terminal(unit=UNIT, output_dir=tmp_path / "ev", environ=ENV, command=_show(), out=out)
    terminal = json.loads(path.read_text(encoding="utf-8"))
    assert terminal["InvocationID"] == INVOCATION
    assert terminal["ExecMainCode"] == "exited" and terminal["ExecMainStatus"] == 0
    assert terminal["MainPID"] == 2468 and terminal["runtime_duration_seconds"] == 1800
    assert terminal["watchdog_result"] == "not_triggered"
    assert line == "main process exited, code=exited, status=0"
    assert out.getvalue() == line + "\n"


def test_stop_post_nonzero_exit_records_status(tmp_path: Path) -> None:
    env = {**ENV, "EXIT_STATUS": "3", "SERVICE_RESULT": "exit-code"}
    path, line = snapshot_terminal(
        unit=UNIT, output_dir=tmp_path / "ev", environ=env,
        command=_show(Result="exit-code", ExecMainStatus="3"), out=io.StringIO(),
    )
    assert line == "main process exited, code=exited, status=3"
    assert json.loads(path.read_text(encoding="utf-8"))["Result"] == "exit-code"


@pytest.mark.parametrize(
    "overrides",
    [
        {"ActiveState": "inactive", "SubState": "dead"},
        {"InvocationID": "f" * 32},
        {"ExecMainCode": "2"},
        {"ExecMainStatus": "9"},
        {"Result": "timeout"},
        {"ExecMainStartTimestamp": "n/a"},
    ],
)
def test_stop_post_refuses_inconsistent_or_gced_snapshot(tmp_path: Path, overrides: dict[str, str]) -> None:
    out = io.StringIO()
    with pytest.raises(ValueError):
        snapshot_terminal(unit=UNIT, output_dir=tmp_path / "ev", environ=ENV, command=_show(**overrides), out=out)
    assert out.getvalue() == ""
    assert not (tmp_path / "ev" / "systemd-terminal.json").exists()


def test_stop_post_snapshot_is_create_only(tmp_path: Path) -> None:
    snapshot_terminal(unit=UNIT, output_dir=tmp_path / "ev", environ=ENV, command=_show(), out=io.StringIO())
    with pytest.raises(FileExistsError):
        snapshot_terminal(unit=UNIT, output_dir=tmp_path / "ev", environ=ENV, command=_show(), out=io.StringIO())


def _snapshot(tmp_path: Path, **kwargs: object) -> Path:
    path, _ = snapshot_terminal(unit=UNIT, output_dir=tmp_path / "ev", environ=ENV, command=_show(), out=io.StringIO())
    return path


def _good_rows() -> list[dict[str, object]]:
    return [
        _row(f"Starting {UNIT}", 0),
        _row("collector running", 10),
        _row("main process exited, code=exited, status=0", 20),
        _row("Deactivated successfully.", 30),
    ]


def test_export_writes_snapshot_and_unmodified_journal(tmp_path: Path) -> None:
    snap = _snapshot(tmp_path)
    commands: list[list[str]] = []
    terminal_path, journal_path = export_terminal(
        unit=UNIT, snapshot=snap, output_dir=tmp_path / "terminal",
        expected_invocation_id=INVOCATION, command=_journal(_good_rows(), commands),
    )
    assert terminal_path.read_bytes() == snap.read_bytes()
    assert len(journal_path.read_text(encoding="utf-8").splitlines()) == 4
    assert f"_SYSTEMD_INVOCATION_ID={INVOCATION}" in commands[0]
    assert not any(arg.startswith("_SYSTEMD_UNIT=") for arg in commands[0])


def test_export_rejects_manager_rows_and_foreign_invocation(tmp_path: Path) -> None:
    snap = _snapshot(tmp_path)
    rows = _good_rows() + [_row("manager row", 40, unit="init.scope")]
    with pytest.raises(ValueError, match="not native"):
        export_terminal(unit=UNIT, snapshot=snap, output_dir=tmp_path / "t1", command=_journal(rows))
    rows = _good_rows() + [_row("x", 40, invocation="f" * 32)]
    with pytest.raises(ValueError, match="outside the exact InvocationID"):
        export_terminal(unit=UNIT, snapshot=snap, output_dir=tmp_path / "t2", command=_journal(rows))


def test_export_rejects_missing_duplicate_or_conflicting_native_rows(tmp_path: Path) -> None:
    snap = _snapshot(tmp_path)
    rows = _good_rows()
    with pytest.raises(ValueError, match="start row"):
        export_terminal(unit=UNIT, snapshot=snap, output_dir=tmp_path / "a", command=_journal(rows[1:]))
    with pytest.raises(ValueError, match="start row"):
        export_terminal(unit=UNIT, snapshot=snap, output_dir=tmp_path / "b", command=_journal(rows + [_row("Starting again", 40)]))
    with pytest.raises(ValueError, match="terminal row"):
        export_terminal(unit=UNIT, snapshot=snap, output_dir=tmp_path / "c", command=_journal(rows[:2]))
    with pytest.raises(ValueError, match="terminal row"):
        export_terminal(
            unit=UNIT, snapshot=snap, output_dir=tmp_path / "d",
            command=_journal(rows + [_row("main process exited, code=exited, status=1", 40)]),
        )


def test_export_rejects_empty_journal_and_wrong_expected_invocation(tmp_path: Path) -> None:
    snap = _snapshot(tmp_path)
    with pytest.raises(ValueError, match="no rows"):
        export_terminal(unit=UNIT, snapshot=snap, output_dir=tmp_path / "a", command=_journal([]))
    with pytest.raises(ValueError, match="differs from the expected"):
        export_terminal(
            unit=UNIT, snapshot=snap, output_dir=tmp_path / "b",
            expected_invocation_id="f" * 32, command=_journal(_good_rows()),
        )


def test_export_is_create_only(tmp_path: Path) -> None:
    snap = _snapshot(tmp_path)
    export_terminal(unit=UNIT, snapshot=snap, output_dir=tmp_path / "terminal", command=_journal(_good_rows()))
    with pytest.raises(FileExistsError):
        export_terminal(unit=UNIT, snapshot=snap, output_dir=tmp_path / "terminal", command=_journal(_good_rows()))


def _frozen_systemd_check(root: Path, run_id: str) -> Any:
    import hashlib

    from scripts.audit_fresh_30h_terminal_v2 import AuditorV2

    auditor = AuditorV2(root, run_id=run_id, epoch="e", runtime_commit=None, runtime_tree=None,
                        sealed_manifest_sha256=None, capture_manifest_sha256=None)
    for rel in ("terminal/systemd-terminal.json", "terminal/systemd-invocation.jsonl"):
        data = (root / rel).read_bytes()
        auditor.payload_hashes[rel] = (len(data), hashlib.sha256(data).hexdigest())
    auditor._audit_systemd(auditor.read_json("terminal/systemd-terminal.json"))
    return next(check for check in auditor.checks if check.name == "systemd_terminal")


def _frozen_pipeline(
    tmp_path: Path, *, env: dict[str, str], show: dict[str, str]
) -> tuple[str, list[dict[str, Any]], str]:
    run_id = "capture-compat"
    unit = f"bitcoin-trader-30h-{run_id}.service"
    show_all = {"ActiveState": "deactivating", "SubState": "stop-post", **show}

    def show_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, _properties(**show_all), "")

    snapshot_terminal(unit=unit, output_dir=tmp_path / "ev", environ=env, command=show_runner, out=io.StringIO())
    start = 1_790_000_000_000_000
    stop = start + 1_800_000_000
    rows = [
        {"_SYSTEMD_INVOCATION_ID": INVOCATION, "_SYSTEMD_UNIT": unit, "MESSAGE": f"Starting {unit}",
         "__REALTIME_TIMESTAMP": str(start - 200_000)},
        {"_SYSTEMD_INVOCATION_ID": INVOCATION, "_SYSTEMD_UNIT": unit, "MESSAGE": "collector heartbeat",
         "__REALTIME_TIMESTAMP": str(start + 1_000_000)},
        {"_SYSTEMD_INVOCATION_ID": INVOCATION, "_SYSTEMD_UNIT": unit,
         "MESSAGE": f"main process exited, code={env['EXIT_CODE']}, status={env['EXIT_STATUS']}",
         "__REALTIME_TIMESTAMP": str(stop + 300_000)},
    ]
    return unit, rows, run_id


def test_native_evidence_passes_unchanged_frozen_systemd_audit(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    start = 1_790_000_000
    fmt = lambda s: datetime.fromtimestamp(s, timezone.utc).strftime("%a %Y-%m-%d %H:%M:%S UTC")
    unit, rows, run_id = _frozen_pipeline(
        tmp_path, env=ENV,
        show={"ExecMainStartTimestamp": fmt(start), "ExecMainExitTimestamp": fmt(start + 1800)},
    )
    export_terminal(unit=unit, snapshot=tmp_path / "ev/systemd-terminal.json", output_dir=tmp_path / "bundle/terminal",
                    expected_invocation_id=INVOCATION, command=_journal(rows))
    check = _frozen_systemd_check(tmp_path / "bundle", run_id)
    assert check.status == "PASS", check.summary


def test_native_evidence_nonzero_exit_fails_frozen_systemd_audit_honestly(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    start = 1_790_000_000
    fmt = lambda s: datetime.fromtimestamp(s, timezone.utc).strftime("%a %Y-%m-%d %H:%M:%S UTC")
    env = {**ENV, "EXIT_STATUS": "3", "SERVICE_RESULT": "exit-code"}
    unit, rows, run_id = _frozen_pipeline(
        tmp_path, env=env,
        show={"Result": "exit-code", "ExecMainStatus": "3",
              "ExecMainStartTimestamp": fmt(start), "ExecMainExitTimestamp": fmt(start + 1800)},
    )
    export_terminal(unit=unit, snapshot=tmp_path / "ev/systemd-terminal.json", output_dir=tmp_path / "bundle/terminal",
                    command=_journal(rows))
    check = _frozen_systemd_check(tmp_path / "bundle", run_id)
    assert check.status == "FAIL"


def test_stop_post_keeps_microsecond_precision(tmp_path: Path) -> None:
    path, _ = snapshot_terminal(
        unit=UNIT, output_dir=tmp_path / "ev", environ=ENV, out=io.StringIO(),
        command=_show(ExecMainStartTimestamp="Sat 2026-10-03 10:00:00.250000 UTC",
                      ExecMainExitTimestamp="Sat 2026-10-03 10:30:00.430123 UTC"),
    )
    terminal = json.loads(path.read_text())
    assert terminal["start_time"] == "2026-10-03T10:00:00.250000Z"
    assert terminal["stop_time"] == "2026-10-03T10:30:00.430123Z"
