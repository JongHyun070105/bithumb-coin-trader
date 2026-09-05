"""Unit tests for Research Governance enforcement tooling."""

import json
from pathlib import Path
import pytest

from scripts.validate_research_governance import (
    GovernanceValidationError,
    calculate_sha256,
    validate_ledger_against_preregistrations,
    validate_preregistration_file,
)


def test_valid_preregistration_in_repo():
    p = Path("research/preregistration/microstructure_v1.json")
    data = validate_preregistration_file(p)
    assert data["preregistration_id"] == "prereg-microstructure-20260905-v1"
    assert data["status"] == "FROZEN_BEFORE_DATA_INSPECTION"


def test_missing_sidecar_raises(tmp_path: Path):
    json_file = tmp_path / "test.json"
    json_file.write_text('{"preregistration_id": "test", "status": "FROZEN"}')
    with pytest.raises(GovernanceValidationError, match="Missing cryptographic SHA-256 sidecar"):
        validate_preregistration_file(json_file)


def test_mismatched_sidecar_raises(tmp_path: Path):
    json_file = tmp_path / "test.json"
    json_file.write_text('{"preregistration_id": "test", "status": "FROZEN"}')
    sidecar = tmp_path / "test.sha256"
    sidecar.write_text("0000000000000000000000000000000000000000000000000000000000000000 test.json")

    with pytest.raises(GovernanceValidationError, match="SHA-256 mismatch"):
        validate_preregistration_file(json_file)


def test_budget_overflow_detected(tmp_path: Path):
    prereg = {
        "preregistration_id": "PREREG-01",
        "trial_budget": {
            "cycle_id": "CYCLE-01",
            "max_primary_discovery_trials": 2,
        },
        "temporal_partitioning": {
            "sealed_prospective_holdout_offset_hours": 50,
        },
    }
    ledger = tmp_path / "ledger.jsonl"
    # Write 3 trials when max is 2
    ledger.write_text(
        '{"trial_id": "T1", "cycle_id": "CYCLE-01"}\n'
        '{"trial_id": "T2", "cycle_id": "CYCLE-01"}\n'
        '{"trial_id": "T3", "cycle_id": "CYCLE-01"}\n'
    )

    violations = validate_ledger_against_preregistrations(ledger, [prereg])
    assert len(violations) == 1
    assert "TRIAL BUDGET OVERFLOW" in violations[0]
    assert "Used 3 trials > Max Allowed 2" in violations[0]


def test_holdout_contamination_detected(tmp_path: Path):
    prereg = {
        "preregistration_id": "PREREG-01",
        "trial_budget": {
            "cycle_id": "CYCLE-01",
            "max_primary_discovery_trials": 10,
        },
        "temporal_partitioning": {
            "sealed_prospective_holdout_offset_hours": 50,
        },
    }
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        '{"trial_id": "T1", "cycle_id": "CYCLE-01", "partition": "SEALED_PROSPECTIVE_HOLDOUT", "holdout_unlocked": false}\n'
    )

    violations = validate_ledger_against_preregistrations(ledger, [prereg])
    assert len(violations) == 1
    assert "HOLDOUT CONTAMINATION" in violations[0]
