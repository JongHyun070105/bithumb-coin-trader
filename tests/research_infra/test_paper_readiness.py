from __future__ import annotations

import hashlib
import json
from pathlib import Path

from bithumb_coin_trader.research_infra import cli
from bithumb_coin_trader.research_infra.candidate_registry import CandidateLifecycle, CandidateRegistry
from bithumb_coin_trader.research_infra.freeze import FrozenCandidate
from bithumb_coin_trader.research_infra.paper_readiness import (
    CHECK_NAMES,
    FAIL,
    NOT_VERIFIABLE,
    PASS,
    REQUIRED_RETROSPECTIVE_METRICS,
    evaluate_paper_readiness,
    write_paper_readiness_report,
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_json(root: Path, relative: str, payload: dict) -> dict[str, str]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(raw)
    return {"path": relative, "sha256": _sha(raw)}


def _valid_bundle(root: Path) -> None:
    event_bytes = b'{"event":"synthetic"}\n'
    event_path = root / "dataset" / "events.jsonl"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.write_bytes(event_bytes)
    source_files = [{"path": "raw/trades.jsonl", "sha256": "a" * 64, "size_bytes": 10}]
    source_set_hash = _sha(
        json.dumps(source_files, sort_keys=True, separators=(",", ":")).encode()
    )
    dataset_ref = _write_json(root, "dataset/manifest.json", {
        "build_status": "DATA_READY",
        "dq_status": PASS,
        "dataset_role": "PROSPECTIVE_RESEARCH",
        "candidate_selection_allowed": True,
        "holdout_evaluated": False,
        "source_files": source_files,
        "source_set_sha256": source_set_hash,
        "events_file": "events.jsonl",
        "events_sha256": _sha(event_bytes),
        "event_count": 1,
    })
    research_ref = _write_json(root, "research/batch.json", {
        "batch_status": PASS,
        "reproducible_inputs_complete": True,
        "experiment_id": "exp_synthetic",
        "dataset_manifest_sha256": dataset_ref["sha256"],
        "walk_forward_status": PASS,
        "placebo_status": PASS,
        "baseline_status": PASS,
        "cost_scenarios": [
            {
                "name": "conservative",
                "maker_fee_bps": 25.0,
                "taker_fee_bps": 25.0,
                "slippage_bps": 8.0,
                "latency_ms": 100.0,
                "minimum_order_notional": 5000.0,
                "tick_size": 1.0,
                "lot_size": 0.00000001,
                "partial_fill_status": "UNSUPPORTED",
            },
            {
                "name": "stress",
                "maker_fee_bps": 50.0,
                "taker_fee_bps": 50.0,
                "slippage_bps": 16.0,
                "latency_ms": 250.0,
                "minimum_order_notional": 5000.0,
                "tick_size": 1.0,
                "lot_size": 0.00000001,
                "partial_fill_probability": 0.5,
            },
        ],
        "metrics": {
            **{metric: 0.0 for metric in REQUIRED_RETROSPECTIVE_METRICS - {"regime_results", "fold_results"}},
            "trade_count": 10,
            "regime_results": [{"name": "synthetic", "net_return": 0.0}],
            "fold_results": [{"fold": 1, "net_return": 0.0}],
        },
        "dataset_roles": ["PROSPECTIVE_RESEARCH"],
    })
    candidate = FrozenCandidate(
        freeze_id="freeze-synthetic",
        frozen_at="2026-09-27T00:00:00Z",
        git_commit="a" * 40,
        hypothesis_id="H1",
        hypothesis_description="synthetic readiness fixture",
        feature_names=("mid_price",),
        feature_parameters={"window": 2},
        target_horizon_s=5,
        target_type="mid_return",
        execution_assumptions={"fee_rate": 0.0025, "slippage_bps": 8.0},
        fee_regime="synthetic_conservative",
        fee_rate=0.0025,
        slippage_bps=8.0,
        latency_ms=100.0,
        position_size_krw=10000.0,
        model_type="threshold",
        model_parameters={"threshold": 0.1},
        entry_threshold=0.1,
        exit_threshold=0.0,
        evaluation_metric="net_return",
        expected_sign="positive",
        source_manifest_fingerprint="manifest-hash",
        source_dataset_ids=("dataset-synthetic",),
        source_dataset_roles=("PROSPECTIVE_RESEARCH",),
        exploratory_ic=0.1,
        exploratory_hit_rate=0.6,
        exploratory_sharpe=1.0,
        exploratory_net_pnl=100.0,
    )
    candidate_sha = _sha(json.dumps(candidate.to_dict(), sort_keys=True, separators=(",", ":")).encode())
    lifecycle = CandidateRegistry(root / "candidate" / "lifecycle.jsonl")
    lifecycle.create_hypothesis(candidate.freeze_id, {
        "hypothesis_id": "H1",
        "origin": "local synthetic fixture",
        "origin_tags": [],
        "economic_intuition": "synthetic test only",
        "required_data": ["synthetic events"],
        "feature_definitions": {"mid_price": "synthetic"},
        "entry_concept": "test",
        "exit_concept": "test",
        "risk_concept": "test",
        "known_confounders": ["synthetic"],
        "falsification_criteria": ["synthetic"],
    })
    lifecycle.transition(candidate.freeze_id, CandidateLifecycle.RETROSPECTIVE_EXPERIMENT, {
        "experiment_id": "exp_synthetic",
        "dataset_manifest_sha256": dataset_ref["sha256"],
        "result_manifest_sha256": "1" * 64,
        "code_commit": "a" * 40,
        "strategy_config_sha256": "2" * 64,
        "feature_definition_sha256": "3" * 64,
        "cost_model_sha256": "4" * 64,
        "latency_assumptions_sha256": "5" * 64,
        "provenance_sha256": "6" * 64,
        "metrics_sha256": "7" * 64,
        "dataset_roles": ["PROSPECTIVE_RESEARCH"],
        "training_range": {"start_utc": "2025-01-01T00:00:00Z", "end_utc": "2025-02-01T00:00:00Z"},
        "validation_range": {"start_utc": "2025-02-02T00:00:00Z", "end_utc": "2025-03-01T00:00:00Z"},
        "random_seed": 7,
        "deterministic_rerun": PASS,
        "purge_embargo_seconds": 60.0,
    })
    lifecycle.transition(candidate.freeze_id, CandidateLifecycle.ROBUSTNESS_TESTED, {
        "robustness_report_sha256": "2" * 64,
        "baseline_report_sha256": "3" * 64,
        "placebo_report_sha256": "4" * 64,
        "walk_forward_status": PASS,
        "cost_scenarios": ["conservative", "stress"],
    })
    lifecycle.transition(candidate.freeze_id, CandidateLifecycle.CANDIDATE, {
        "promotion_report_sha256": "5" * 64,
        "acceptance_rules_sha256": "6" * 64,
        "decision": PASS,
    })
    lifecycle.transition(candidate.freeze_id, CandidateLifecycle.FROZEN, {
        "freeze_hash": candidate.freeze_hash,
        "candidate_freeze_sha256": candidate_sha,
        "source_research_sha256": research_ref["sha256"],
    })
    lifecycle_events = lifecycle.events_for(candidate.freeze_id)
    candidate_ref = _write_json(root, "candidate/frozen.json", {
        "candidate": candidate.to_dict(),
        "source_research_sha256": research_ref["sha256"],
        "lifecycle_status": "FROZEN",
        "transition_evidence_sha256": lifecycle_events[-1]["event_hash"],
        "lifecycle_events": lifecycle_events,
    })
    risk_ref = _write_json(root, "risk/config.json", {
        "max_position_notional": 100000.0,
        "max_total_exposure": 0.8,
        "max_order_notional": 20000.0,
        "max_daily_loss": 0.05,
        "max_drawdown": 0.15,
        "max_consecutive_failures": 3,
        "max_api_error_rate": 0.05,
        "max_stale_data_age_seconds": 5.0,
        "max_spread_bps": 50.0,
        "kill_switch_enabled": True,
        "circuit_breaker_enabled": True,
    })
    execution_ref = _write_json(root, "execution/config.json", {
        "backend": "PAPER",
        "durable_journal": True,
        "restart_recovery": PASS,
        "fill_idempotency": PASS,
        "partial_fills": PASS,
        "cancel_reconciliation": PASS,
        "private_api_enabled": False,
        "live_enabled": False,
        "private_api_env_gate": "DISABLED",
        "live_env_gate": "DISABLED",
    })
    observability_ref = _write_json(root, "observability/metrics.json", {
        "metrics": [
            "strategy_state", "market_data_freshness", "signals", "orders", "fills",
            "positions", "balance", "pnl", "fees", "drawdown", "risk_gates",
            "errors", "restarts",
        ],
        "journal_source": "paper.sqlite3",
        "restart_metrics": True,
    })
    security_ref = _write_json(root, "security/scan.json", {
        "scan_status": PASS,
        "secret_count": 0,
        "scanner_sha256": "b" * 64,
        "scanned_tree_sha256": "c" * 64,
    })
    bundle = {
        "schema_version": 1,
        "artifacts": {
            "dataset": dataset_ref,
            "research": research_ref,
            "candidate": candidate_ref,
            "risk": risk_ref,
            "execution": execution_ref,
            "observability": observability_ref,
            "security": security_ref,
        },
    }
    (root / "paper-readiness-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")


def test_missing_bundle_is_fail_closed_and_not_verifiable(tmp_path: Path) -> None:
    report = evaluate_paper_readiness(tmp_path)

    assert report["PAPER_ELIGIBLE"] is False
    assert all(report["checks"][name]["status"] == NOT_VERIFIABLE for name in CHECK_NAMES)
    assert report["scientific_state"]["PAPER"] == "NOT_STARTED"


def test_complete_synthetic_bundle_passes_and_cli_writes_report(tmp_path: Path, capsys) -> None:
    evidence_dir = tmp_path / "bundle"
    evidence_dir.mkdir()
    _valid_bundle(evidence_dir)

    report = evaluate_paper_readiness(evidence_dir)
    assert report["PAPER_ELIGIBLE"] is True
    assert all(report["checks"][name]["status"] == PASS for name in CHECK_NAMES)

    output_dir = tmp_path / "report"
    result = cli.main([
        "paper-readiness",
        "--evidence-dir", str(evidence_dir),
        "--output-dir", str(output_dir),
    ])
    assert result == 0
    assert json.loads((output_dir / "paper-readiness.json").read_text())["PAPER_ELIGIBLE"] is True
    assert (output_dir / "paper-readiness.md").is_file()
    assert "PAPER_ELIGIBLE=True" in capsys.readouterr().out


def test_hash_mismatch_fails_readiness(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "bundle"
    evidence_dir.mkdir()
    _valid_bundle(evidence_dir)
    event_path = evidence_dir / "dataset" / "events.jsonl"
    event_path.write_bytes(event_path.read_bytes() + b"tampered\n")

    report = evaluate_paper_readiness(evidence_dir)

    assert report["PAPER_ELIGIBLE"] is False
    assert report["checks"]["DATA_READY"]["status"] == FAIL


def test_external_only_research_cannot_pass(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "bundle"
    evidence_dir.mkdir()
    _valid_bundle(evidence_dir)
    research_path = evidence_dir / "research" / "batch.json"
    research = json.loads(research_path.read_text())
    research["dataset_roles"] = ["HYPOTHESIS_GENERATION_ONLY"]
    research_ref = _write_json(evidence_dir, "research/batch.json", research)
    bundle_path = evidence_dir / "paper-readiness-bundle.json"
    bundle = json.loads(bundle_path.read_text())
    bundle["artifacts"]["research"] = research_ref
    bundle_path.write_text(json.dumps(bundle))

    report = evaluate_paper_readiness(evidence_dir)

    assert report["PAPER_ELIGIBLE"] is False
    assert report["checks"]["RESEARCH_READY"]["status"] == FAIL


def test_research_cost_scenarios_use_shared_fail_closed_model(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "bundle"
    evidence_dir.mkdir()
    _valid_bundle(evidence_dir)
    research_path = evidence_dir / "research" / "batch.json"
    research = json.loads(research_path.read_text())
    research["cost_scenarios"][0]["tick_size"] = 0.0
    research_ref = _write_json(evidence_dir, "research/batch.json", research)
    bundle_path = evidence_dir / "paper-readiness-bundle.json"
    bundle = json.loads(bundle_path.read_text())
    bundle["artifacts"]["research"] = research_ref
    bundle_path.write_text(json.dumps(bundle))

    report = evaluate_paper_readiness(evidence_dir)

    assert report["PAPER_ELIGIBLE"] is False
    assert report["checks"]["RESEARCH_READY"]["status"] == FAIL
    assert "tick_size" in report["checks"]["RESEARCH_READY"]["reason"]


def test_candidate_lifecycle_chain_is_verified_and_bound_to_freeze(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "bundle"
    evidence_dir.mkdir()
    _valid_bundle(evidence_dir)
    candidate_path = evidence_dir / "candidate" / "frozen.json"
    candidate_payload = json.loads(candidate_path.read_text())
    candidate_payload["lifecycle_events"][-1]["evidence"]["source_research_sha256"] = "f" * 64
    candidate_payload["lifecycle_events"][-1]["event_hash"] = "e" * 64
    candidate_ref = _write_json(evidence_dir, "candidate/frozen.json", candidate_payload)
    bundle_path = evidence_dir / "paper-readiness-bundle.json"
    bundle = json.loads(bundle_path.read_text())
    bundle["artifacts"]["candidate"] = candidate_ref
    bundle_path.write_text(json.dumps(bundle))

    report = evaluate_paper_readiness(evidence_dir)

    assert report["PAPER_ELIGIBLE"] is False
    assert report["checks"]["CANDIDATE_FROZEN"]["status"] == FAIL


def test_report_cannot_be_written_inside_evidence_bundle(tmp_path: Path) -> None:
    report = evaluate_paper_readiness(tmp_path)
    try:
        write_paper_readiness_report(report, tmp_path / "report", tmp_path)
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("report inside the evidence bundle must be rejected")
