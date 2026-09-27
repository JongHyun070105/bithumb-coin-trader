from __future__ import annotations

from pathlib import Path

from bithumb_coin_trader.research_infra.paper_start_gate import (
    REQUIRED_START_GATES,
    evaluate_paper_start_gates,
)


def test_paper_start_gate_fails_closed_when_any_seal_or_readiness_bundle_is_missing(
    tmp_path: Path,
) -> None:
    report = evaluate_paper_start_gates(
        evidence_dir=tmp_path / "missing-readiness",
        candidate_freeze=tmp_path / "missing-candidate.json",
        reliability_seal=tmp_path / "missing-reliability-seal.json",
    )

    assert report["PAPER_START_ALLOWED"] is False
    assert report["PAPER"] == "NOT_STARTED"
    assert tuple(report["checks"]) == REQUIRED_START_GATES
    assert all(report["checks"][name]["status"] != "PASS" for name in REQUIRED_START_GATES)

