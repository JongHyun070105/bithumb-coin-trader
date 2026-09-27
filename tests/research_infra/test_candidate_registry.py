from __future__ import annotations

import json
from pathlib import Path

import pytest

from bithumb_coin_trader.research_infra.candidate_registry import (
    CandidateLifecycle,
    CandidateLifecycleError,
    CandidateRegistry,
)


def _definition(origin: str = "local research", tags: list[str] | None = None) -> dict[str, object]:
    return {
        "hypothesis_id": "H1",
        "origin": origin,
        "origin_tags": tags or [],
        "economic_intuition": "test only",
        "required_data": ["trades"],
        "feature_definitions": {"imbalance": "test"},
        "entry_concept": "test",
        "exit_concept": "test",
        "risk_concept": "test",
        "known_confounders": ["synthetic"],
        "falsification_criteria": ["test threshold"],
    }


def _retrospective(roles: list[str]) -> dict[str, object]:
    return {
        "experiment_id": "exp-test",
        "dataset_manifest_sha256": "a" * 64,
        "result_manifest_sha256": "b" * 64,
        "code_commit": "c" * 40,
        "strategy_config_sha256": "d" * 64,
        "feature_definition_sha256": "e" * 64,
        "cost_model_sha256": "f" * 64,
        "latency_assumptions_sha256": "1" * 64,
        "provenance_sha256": "2" * 64,
        "metrics_sha256": "3" * 64,
        "dataset_roles": roles,
        "training_range": {"start_utc": "2025-01-01T00:00:00Z", "end_utc": "2025-02-01T00:00:00Z"},
        "validation_range": {"start_utc": "2025-02-02T00:00:00Z", "end_utc": "2025-03-01T00:00:00Z"},
        "random_seed": 7,
        "deterministic_rerun": "PASS",
        "purge_embargo_seconds": 60.0,
    }


def test_candidate_registry_requires_ordered_machine_verifiable_evidence(tmp_path: Path) -> None:
    registry = CandidateRegistry(tmp_path / "candidate.jsonl")
    registry.create_hypothesis("candidate-1", _definition())

    with pytest.raises(CandidateLifecycleError, match="expected RETROSPECTIVE_EXPERIMENT"):
        registry.transition("candidate-1", CandidateLifecycle.ROBUSTNESS_TESTED, {})

    registry.transition("candidate-1", CandidateLifecycle.RETROSPECTIVE_EXPERIMENT, _retrospective(["CURRENT_PUBLIC"]))
    registry.transition("candidate-1", CandidateLifecycle.ROBUSTNESS_TESTED, {
        "robustness_report_sha256": "1" * 64,
        "baseline_report_sha256": "2" * 64,
        "placebo_report_sha256": "3" * 64,
        "walk_forward_status": "PASS",
        "cost_scenarios": ["base", "stress"],
    })
    registry.transition("candidate-1", CandidateLifecycle.CANDIDATE, {
        "promotion_report_sha256": "4" * 64,
        "acceptance_rules_sha256": "5" * 64,
        "decision": "PASS",
    })
    registry.transition("candidate-1", CandidateLifecycle.FROZEN, {
        "freeze_hash": "6" * 64,
        "candidate_freeze_sha256": "7" * 64,
        "source_research_sha256": "8" * 64,
    })

    assert [event["to_status"] for event in registry.events_for("candidate-1")] == [
        "HYPOTHESIS", "RETROSPECTIVE_EXPERIMENT", "ROBUSTNESS_TESTED", "CANDIDATE", "FROZEN"
    ]


def test_external_hypothesis_is_generation_only_and_cannot_advance(tmp_path: Path) -> None:
    registry = CandidateRegistry(tmp_path / "candidate.jsonl")
    with pytest.raises(CandidateLifecycleError, match="tagged HYPOTHESIS_GENERATION_ONLY"):
        registry.create_hypothesis("external-1", _definition("BitMEX expert results"))

    registry.create_hypothesis("external-1", _definition(
        "BitMEX expert results", ["HYPOTHESIS_GENERATION_ONLY"]
    ))
    registry.transition("external-1", CandidateLifecycle.RETROSPECTIVE_EXPERIMENT, _retrospective([
        "HYPOTHESIS_GENERATION_ONLY"
    ]))
    with pytest.raises(CandidateLifecycleError, match="cannot advance beyond retrospective research"):
        registry.transition("external-1", CandidateLifecycle.ROBUSTNESS_TESTED, {
            "robustness_report_sha256": "1" * 64,
            "baseline_report_sha256": "2" * 64,
            "placebo_report_sha256": "3" * 64,
            "walk_forward_status": "PASS",
            "cost_scenarios": ["base", "stress"],
        })

    events = registry.events_for("external-1")
    crafted_event = {
        "schema_version": 1,
        "candidate_id": "external-1",
        "sequence": 2,
        "from_status": CandidateLifecycle.RETROSPECTIVE_EXPERIMENT.value,
        "to_status": CandidateLifecycle.ROBUSTNESS_TESTED.value,
        "timestamp_utc": "2026-09-27T00:00:00+00:00",
        "previous_hash": events[-1]["event_hash"],
        "evidence": {},
        "event_hash": "0" * 64,
    }
    with pytest.raises(CandidateLifecycleError, match="cannot advance beyond retrospective research"):
        CandidateRegistry.verify_events([*events, crafted_event])


def test_candidate_registry_rejects_tampered_hash_chain(tmp_path: Path) -> None:
    path = tmp_path / "candidate.jsonl"
    registry = CandidateRegistry(path)
    registry.create_hypothesis("candidate-1", _definition())
    event = json.loads(path.read_text().splitlines()[0])
    event["evidence"]["definition"]["entry_concept"] = "tampered"
    path.write_text(json.dumps(event) + "\n")

    with pytest.raises(CandidateLifecycleError, match="event hash mismatch"):
        registry.verify_ledger()
