from __future__ import annotations

import json
from pathlib import Path

import pytest

from bithumb_coin_trader.research_infra.cli import main
from bithumb_coin_trader.research_infra.paper_start_gate import (
    PaperStartGateError,
    _verify_reliability_seal,
)
from bithumb_coin_trader.research_infra.reliability_seal import (
    ReliabilitySealError,
    write_reliability_seal,
)


def _audit(status: str = "PASS") -> dict[str, object]:
    return {
        "schema_version": 1,
        "overall_status": status,
        "epoch": "epoch-test",
        "run_id": "run-test",
        "runtime_commit_expected": "a" * 40,
        "runtime_tree_expected": "b" * 40,
        "checks": [],
    }


def test_reliability_seal_requires_terminal_pass_and_is_write_once(tmp_path: Path) -> None:
    audit_path = tmp_path / "terminal-audit.json"
    seal_path = tmp_path / "reliability-seal.json"
    audit_path.write_text(json.dumps(_audit()), encoding="utf-8")

    seal = write_reliability_seal(audit_path, seal_path)
    assert seal["terminal_verdict"] == "PASS"
    assert seal["terminal_audit_sha256"] == seal["terminal_evidence_sha256"]
    with pytest.raises(ReliabilitySealError, match="must be new"):
        write_reliability_seal(audit_path, seal_path)


def test_reliability_seal_rejects_nonpass_and_changed_audit(tmp_path: Path) -> None:
    audit_path = tmp_path / "terminal-audit.json"
    audit_path.write_text(json.dumps(_audit("NOT_VERIFIABLE")), encoding="utf-8")
    with pytest.raises(ReliabilitySealError, match="only an explicit terminal-audit PASS"):
        write_reliability_seal(audit_path, tmp_path / "reliability-seal.json")

    audit_path.write_text(json.dumps(_audit()), encoding="utf-8")
    seal_path = tmp_path / "reliability-seal.json"
    write_reliability_seal(audit_path, seal_path)
    changed = _audit()
    changed["run_id"] = "different-run"
    audit_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(PaperStartGateError, match="does not bind a terminal PASS"):
        _verify_reliability_seal(seal_path)


def test_cli_seal_requires_a_new_output_and_reports_only_local_sealing(tmp_path: Path, capsys) -> None:
    audit_path = tmp_path / "terminal-audit.json"
    audit_path.write_text(json.dumps(_audit()), encoding="utf-8")
    result = main([
        "reliability-seal",
        "--terminal-audit", str(audit_path),
        "--output", str(tmp_path / "reliability-seal.json"),
    ])
    assert result == 0
    assert "does not start PAPER" in capsys.readouterr().out
