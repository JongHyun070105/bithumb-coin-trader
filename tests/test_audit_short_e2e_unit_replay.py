from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts import audit_short_e2e_unit_replay as replay
from scripts.audit_fresh_30h_terminal_v2 import AuditorV2
from scripts.capture_fresh_30h_systemd import export_terminal, snapshot_terminal
from tests.test_capture_fresh_30h_systemd import ENV, INVOCATION, _journal, _properties
import subprocess

RUN_ID = "aws-validation-observability-3h-run-20261004T044500Z-a4704f91"
UNIT = f"bitcoin-trader-3h-{RUN_ID}.service"


def _build(root: Path) -> None:
    start = 1_790_000_000
    fmt = lambda s: datetime.fromtimestamp(s, timezone.utc).strftime("%a %Y-%m-%d %H:%M:%S UTC")
    show = {"ActiveState": "deactivating", "SubState": "stop-post",
            "ExecMainStartTimestamp": fmt(start), "ExecMainExitTimestamp": fmt(start + 1800)}

    def show_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, _properties(**show), "")

    snapshot_terminal(unit=UNIT, output_dir=root / "ev", environ=ENV, command=show_runner, out=io.StringIO())
    micro = start * 1_000_000
    rows: list[dict[str, object]] = [
        {"_SYSTEMD_INVOCATION_ID": INVOCATION, "_SYSTEMD_UNIT": UNIT, "MESSAGE": f"Starting {UNIT}",
         "__REALTIME_TIMESTAMP": str(micro - 200_000)},
        {"_SYSTEMD_INVOCATION_ID": INVOCATION, "_SYSTEMD_UNIT": UNIT,
         "MESSAGE": "main process exited, code=exited, status=0",
         "__REALTIME_TIMESTAMP": str(micro + 1_800_000_000 + 300_000)},
    ]
    export_terminal(unit=UNIT, snapshot=root / "ev/systemd-terminal.json", output_dir=root / "bundle/terminal",
                    expected_invocation_id=INVOCATION, command=_journal(rows))


def _systemd_check(auditor: Any, root: Path) -> Any:
    for rel in ("terminal/systemd-terminal.json", "terminal/systemd-invocation.jsonl"):
        data = (root / rel).read_bytes()
        auditor.payload_hashes[rel] = (len(data), hashlib.sha256(data).hexdigest())
    auditor._audit_systemd(auditor.read_json("terminal/systemd-terminal.json"))
    return next(check for check in auditor.checks if check.name == "systemd_terminal")


def _args(root: Path) -> dict[str, Any]:
    return dict(run_id=RUN_ID, epoch="e", runtime_commit=None, runtime_tree=None,
                sealed_manifest_sha256=None, capture_manifest_sha256=None)


def test_frozen_auditor_rejects_short_unit_prefix_and_replay_maps_it_in_memory(tmp_path: Path) -> None:
    _build(tmp_path)
    bundle = tmp_path / "bundle"
    before = (bundle / "terminal/systemd-invocation.jsonl").read_bytes()
    assert _systemd_check(AuditorV2(bundle, **_args(bundle)), bundle).status == "FAIL"
    module = replay.load_frozen()
    replay_cls = replay.build_replay_class(module)
    assert _systemd_check(replay_cls(bundle, **_args(bundle)), bundle).status == "PASS"
    assert (bundle / "terminal/systemd-invocation.jsonl").read_bytes() == before


def test_replay_does_not_hide_a_foreign_unit(tmp_path: Path) -> None:
    _build(tmp_path)
    bundle = tmp_path / "bundle"
    path = bundle / "terminal/systemd-invocation.jsonl"
    path.write_bytes(path.read_bytes().replace(RUN_ID.encode(), b"another-run-id"))
    module = replay.load_frozen()
    replay_cls = replay.build_replay_class(module)
    assert _systemd_check(replay_cls(bundle, **_args(bundle)), bundle).status == "FAIL"


def test_systemd_mechanics_qualified_for_natural_clean_short_run_but_never_acceptance(tmp_path: Path) -> None:
    _build(tmp_path)
    report = replay.qualify_systemd_mechanics(tmp_path / "bundle", run_id=RUN_ID)
    assert report["SYSTEMD_MECHANICS_QUALIFIED"] == "YES", report["failures"]
    assert report["SHORT_RUN_SYSTEMD_ACCEPTANCE"] == "NOT_APPLICABLE"
    assert report["acceptance_evidence"] is False
    assert report["facts"]["InvocationID"] == INVOCATION and report["facts"]["NRestarts"] == 0
    assert len(report["facts"]["journal_sha256"]) == 64


def test_systemd_mechanics_rejects_foreign_run_restart_and_unclean_exit(tmp_path: Path) -> None:
    import json
    _build(tmp_path)
    bundle = tmp_path / "bundle"
    assert replay.qualify_systemd_mechanics(bundle, run_id="another-run-id")["SYSTEMD_MECHANICS_QUALIFIED"] == "NO"
    snap_path = bundle / "terminal/systemd-terminal.json"
    original = json.loads(snap_path.read_text())
    for field, value in (("NRestarts", 1), ("Result", "exit-code"), ("ExecMainStatus", 1),
                         ("ExecMainCode", "killed"), ("InvocationID", "zz")):
        snap_path.write_text(json.dumps({**original, field: value}))
        report = replay.qualify_systemd_mechanics(bundle, run_id=RUN_ID)
        assert report["SYSTEMD_MECHANICS_QUALIFIED"] == "NO", field
        assert report["acceptance_evidence"] is False
    snap_path.write_text(json.dumps(original))
    journal = bundle / "terminal/systemd-invocation.jsonl"
    journal.write_bytes(journal.read_bytes().replace(b"status=0", b"status=1"))
    assert replay.qualify_systemd_mechanics(bundle, run_id=RUN_ID)["SYSTEMD_MECHANICS_QUALIFIED"] == "NO"


def test_systemd_mechanics_fails_closed_when_evidence_is_missing(tmp_path: Path) -> None:
    report = replay.qualify_systemd_mechanics(tmp_path, run_id=RUN_ID)
    assert report["SYSTEMD_MECHANICS_QUALIFIED"] == "NO"
