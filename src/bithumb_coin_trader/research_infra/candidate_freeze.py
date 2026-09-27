"""Fail-closed, content-bound freeze gate for completed governed experiments."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from .batch import _baseline_comparisons, _read_complete, _read_events, fold_run_from_dict
from .candidate_registry import CandidateLifecycle, CandidateLifecycleError, CandidateRegistry
from .costs import CostScenarioError, SpotCostScenario
from .definition_registry import DefinitionRegistryError, validate_definition_record
from .result_schema import ExperimentResult, RESULT_SCHEMA_VERSION
from .builtin_strategies import (
    candidate_family_for_strategy,
    governed_candidate_strategy_ids,
    strategy_source_modules,
)


_EXPERIMENT_ID = re.compile(r"exp_[0-9a-f]{64}")
_REQUIRED_COST_TIERS = ("base", "conservative", "stress", "extreme")


class CandidateFreezeError(ValueError):
    """Raised when experiment or candidate evidence is incomplete or unsafe."""


def freeze_candidate_experiment(
    *,
    experiment_id: str,
    research_root: Path,
    candidate_registry_path: Path,
    output_path: Path,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Validate and freeze one completed experiment, then append FROZEN evidence.

    The ledger must already be at CANDIDATE. This operation never selects or
    promotes a candidate. Its output is write-once; existing matching evidence
    can complete a ledger append interrupted after artifact creation.
    """
    if not isinstance(experiment_id, str) or _EXPERIMENT_ID.fullmatch(experiment_id) is None:
        raise CandidateFreezeError("experiment must be a content-derived exp_<sha256> id")
    selected_candidate_id = candidate_id or experiment_id
    if not isinstance(selected_candidate_id, str) or not selected_candidate_id.strip():
        raise CandidateFreezeError("candidate_id must be non-empty")
    root = Path(research_root).resolve()
    if not root.is_dir() or Path(research_root).is_symlink():
        raise CandidateFreezeError("research_root must be an existing non-symlink directory")
    registry_path = Path(candidate_registry_path).resolve()
    if not registry_path.is_file() or Path(candidate_registry_path).is_symlink():
        raise CandidateFreezeError("candidate registry must be an existing regular ledger")
    registry = CandidateRegistry(registry_path)
    history = registry.events_for(selected_candidate_id)
    if not history:
        raise CandidateFreezeError(f"candidate {selected_candidate_id!r} has no lifecycle evidence")
    statuses = [event["to_status"] for event in history]
    expected_prefix = [
        CandidateLifecycle.HYPOTHESIS.value,
        CandidateLifecycle.RETROSPECTIVE_EXPERIMENT.value,
        CandidateLifecycle.ROBUSTNESS_TESTED.value,
        CandidateLifecycle.CANDIDATE.value,
    ]
    if statuses not in (expected_prefix, [*expected_prefix, CandidateLifecycle.FROZEN.value]):
        raise CandidateFreezeError("candidate lifecycle must be complete through CANDIDATE and not advance beyond FROZEN")

    experiment = _load_experiment(root, experiment_id)
    identity = experiment["identity"]
    provenance = identity["dataset_provenance"]
    source_hashes = _validate_experiment(experiment_id, experiment)
    _validate_lifecycle_history(history, experiment_id, identity, provenance, source_hashes, experiment)

    frozen_event = history[-1] if statuses[-1] == CandidateLifecycle.FROZEN.value else None
    frozen_at_utc = (
        frozen_event["timestamp_utc"]
        if frozen_event is not None
        else datetime.now(timezone.utc).isoformat()
    )
    record = _make_freeze_record(
        candidate_id=selected_candidate_id,
        experiment_id=experiment_id,
        identity=identity,
        provenance=provenance,
        experiment=experiment,
        source_hashes=source_hashes,
        frozen_at_utc=frozen_at_utc,
    )
    record["freeze_hash"] = _hash_json({key: value for key, value in record.items() if key != "freeze_hash"})
    if Path(output_path).is_symlink():
        raise CandidateFreezeError("freeze output cannot be a symlink")
    destination = Path(output_path).resolve()
    if root not in destination.parents:
        raise CandidateFreezeError("freeze output must be inside research_root")
    if frozen_event is None and destination.exists():
        raise CandidateFreezeError("freeze output already exists without a matching FROZEN lifecycle event")

    if frozen_event is None:
        try:
            frozen_event = registry.transition(
                selected_candidate_id,
                CandidateLifecycle.FROZEN,
                {
                    "freeze_hash": record["freeze_hash"],
                    "candidate_freeze_sha256": _hash_json(record),
                    "source_research_sha256": source_hashes["metrics_sha256"],
                },
                timestamp_utc=frozen_at_utc,
            )
        except CandidateLifecycleError as exc:
            refreshed = registry.events_for(selected_candidate_id)
            if not refreshed or refreshed[-1].get("to_status") != CandidateLifecycle.FROZEN.value:
                raise CandidateFreezeError(f"unable to append candidate FROZEN transition: {exc}") from exc
            frozen_event = refreshed[-1]
            history = refreshed
            frozen_at_utc = frozen_event["timestamp_utc"]
            record = _make_freeze_record(
                candidate_id=selected_candidate_id,
                experiment_id=experiment_id,
                identity=identity,
                provenance=provenance,
                experiment=experiment,
                source_hashes=source_hashes,
                frozen_at_utc=frozen_at_utc,
            )
            record["freeze_hash"] = _hash_json({key: value for key, value in record.items() if key != "freeze_hash"})
    if (
        frozen_event["evidence"].get("freeze_hash") != record["freeze_hash"]
        or frozen_event["evidence"].get("candidate_freeze_sha256") != _hash_json(record)
        or frozen_event["evidence"].get("source_research_sha256") != source_hashes["metrics_sha256"]
    ):
        raise CandidateFreezeError("existing FROZEN transition does not bind this freeze artifact")
    history = registry.events_for(selected_candidate_id)
    if not history or history[-1].get("event_hash") != frozen_event.get("event_hash"):
        raise CandidateFreezeError("candidate lifecycle changed while the freeze artifact was being prepared")
    artifact: dict[str, Any] = {
        "schema_version": 2,
        "candidate": record,
        "source_research_sha256": source_hashes["metrics_sha256"],
        "lifecycle_events": history,
        "transition_evidence_sha256": frozen_event["event_hash"],
    }
    artifact["artifact_sha256"] = _hash_json(artifact)
    if destination.exists():
        _verify_existing_freeze(_read_json_object(destination), artifact)
    else:
        _write_once(destination, _canonical(artifact).encode("utf-8") + b"\n")
    return artifact


def _load_experiment(root: Path, experiment_id: str) -> dict[str, Any]:
    found: dict[Path, list[tuple[Path, dict[str, Any]]]] = {}
    for manifest_path in root.glob(f"batches/*/runs/{experiment_id}/attempt-*/manifest.json"):
        attempt_dir = manifest_path.parent
        resolved_manifest = manifest_path.resolve()
        if root not in resolved_manifest.parents or manifest_path.is_symlink():
            raise CandidateFreezeError("experiment manifest must be a regular file inside research_root")
        manifest = _read_json_object(resolved_manifest)
        if manifest.get("experiment_id") != experiment_id or manifest.get("schema_version") != 1:
            raise CandidateFreezeError("experiment attempt manifest identity is invalid")
        if not isinstance(manifest.get("identity"), dict):
            raise CandidateFreezeError("experiment attempt manifest has no identity")
        found.setdefault(attempt_dir.parents[2], []).append((attempt_dir, manifest))
    if not found:
        raise CandidateFreezeError(f"no batch attempt exists for {experiment_id}")

    batches: list[dict[str, Any]] = []
    incomplete_attempts: list[str] = []
    for batch_root, attempts in found.items():
        attempts.sort(key=lambda item: int(item[1].get("attempt", -1)))
        attempt_dir, manifest = attempts[-1]
        try:
            attempt = _verify_attempt(attempt_dir, experiment_id, manifest)
        except CandidateFreezeError as exc:
            incomplete_attempts.append(f"{batch_root.name}: {exc}")
            continue
        batch_manifest = _read_json_object(batch_root / "manifest.json")
        report = _read_json_object(batch_root / "aggregate_report.json")
        batch_identity = batch_manifest.get("identity")
        if not isinstance(batch_identity, dict) or report.get("status") != "COMPLETE":
            raise CandidateFreezeError("source batch is incomplete")
        expected_batch_hash = _hash_json(batch_identity)
        if (
            batch_manifest.get("batch_id") != report.get("batch_id")
            or report.get("batch_identity_sha256") != expected_batch_hash
            or batch_manifest.get("batch_id") != batch_root.name
            or report.get("completed_count") != report.get("experiment_count")
        ):
            raise CandidateFreezeError("source batch manifest or aggregate report binding is invalid")
        if attempt["identity"] not in batch_identity.get("experiments", []):
            raise CandidateFreezeError("experiment is not present in its batch identity")
        if (
            batch_identity.get("dataset_sha256") != attempt["identity"].get("dataset_sha256")
            or batch_identity.get("dataset_id") != attempt["identity"].get("dataset_id")
            or batch_identity.get("code_revision") != attempt["identity"].get("code_revision")
            or batch_identity.get("fold_config") != attempt["identity"].get("fold_config")
            or batch_identity.get("cost_scenarios") != attempt["identity"].get("cost_config")
        ):
            raise CandidateFreezeError("batch and experiment identities disagree")
        batches.append({**attempt, "batch_root": batch_root, "batch_manifest": batch_manifest, "aggregate": report})
    if not batches:
        reason = "; ".join(incomplete_attempts)
        raise CandidateFreezeError(f"no completed source batch is available for {experiment_id}: {reason}")

    identities = {_canonical(item["identity"]) for item in batches}
    metric_hashes = {item["metrics_sha256"] for item in batches}
    if len(identities) != 1 or len(metric_hashes) != 1:
        raise CandidateFreezeError("experiment id resolves to conflicting attempt evidence")
    candidates = [item for item in batches if _has_complete_controls(item)]
    if not candidates:
        raise CandidateFreezeError("no complete batch contains cash, buy-and-hold, and placebo comparisons")
    verified_candidates: list[dict[str, Any]] = []
    for item in candidates:
        try:
            _verify_batch_comparisons(item, experiment_id)
        except CandidateFreezeError:
            continue
        verified_candidates.append(item)
    if not verified_candidates:
        raise CandidateFreezeError("baseline/placebo comparisons do not match verified batch metrics")
    selected = verified_candidates[0]
    if any(item["aggregate"] != selected["aggregate"] for item in verified_candidates[1:]):
        raise CandidateFreezeError("experiment appears in multiple batches with conflicting comparison evidence")
    selected["batch_manifest_sha256"] = _sha256_file(selected["batch_root"] / "manifest.json")
    selected["aggregate_sha256"] = _sha256_file(selected["batch_root"] / "aggregate_report.json")
    selected["complete_sha256"] = _sha256_file(selected["attempt_dir"] / "complete.json")
    selected["metrics_path"] = str(selected["metrics_path"].relative_to(root))
    return selected


def _verify_attempt(attempt_dir: Path, experiment_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    if not attempt_dir.is_dir() or attempt_dir.is_symlink():
        raise CandidateFreezeError("experiment attempt directory is invalid")
    identity = manifest["identity"]
    if identity.get("experiment_id") not in (None, experiment_id):
        raise CandidateFreezeError("attempt identity refers to a different experiment")
    complete = _read_complete(attempt_dir)
    if complete is None or complete.get("experiment_id") != experiment_id:
        raise CandidateFreezeError("latest experiment attempt is not complete")
    metrics_path = attempt_dir / "metrics.json"
    metrics = _read_json_object(metrics_path)
    metric_hash = _sha256_file(metrics_path)
    if metric_hash != complete.get("metrics_sha256"):
        raise CandidateFreezeError("experiment metrics hash does not match its terminal record")
    events = _read_events(attempt_dir / "events.jsonl")
    if not events or events[-1].get("kind") != "ATTEMPT_COMPLETED":
        raise CandidateFreezeError("experiment event log has no terminal completion event")
    completion_event = events[-1]
    if any(completion_event.get(key) != value for key, value in complete.items()):
        raise CandidateFreezeError("experiment completion event disagrees with its terminal record")
    if complete.get("unsupported_fold_count") != 0:
        raise CandidateFreezeError("experiment contains explicitly unsupported execution folds")
    result_folds = metrics.get("folds")
    if not isinstance(result_folds, list) or len(result_folds) != complete.get("fold_count"):
        raise CandidateFreezeError("experiment completion record does not cover its persisted folds")
    for fold in result_folds:
        if not isinstance(fold, dict) or not isinstance(fold.get("result"), dict):
            raise CandidateFreezeError("experiment contains a malformed standardized fold result")
        try:
            if ExperimentResult.from_dict(fold["result"]).status.value != "COMPLETED":
                raise CandidateFreezeError("experiment contains an explicitly unsupported fold result")
        except (KeyError, TypeError, ValueError) as exc:
            raise CandidateFreezeError(f"experiment fold result schema is invalid: {exc}") from exc
    if (
        metrics.get("dataset_id") != identity.get("dataset_id")
        or metrics.get("dataset_sha256") != identity.get("dataset_sha256")
        or metrics.get("code_revision") != identity.get("code_revision")
        or metrics.get("candidate_family") != identity.get("candidate_family")
        or metrics.get("strategy_id") != identity.get("strategy_id")
        or metrics.get("window_mode") != identity.get("fold_config", {}).get("window_mode")
    ):
        raise CandidateFreezeError("experiment metrics do not match their content identity")
    if attempt_dir.name != f"attempt-{int(manifest.get('attempt', -1)):04d}":
        raise CandidateFreezeError("experiment attempt sequence does not match its manifest path")
    return {
        "attempt_dir": attempt_dir,
        "identity": identity,
        "metrics": metrics,
        "metrics_path": metrics_path,
        "metrics_sha256": metric_hash,
        "complete": complete,
        "attempt_manifest_sha256": _sha256_file(attempt_dir / "manifest.json"),
        "batch_root": attempt_dir.parents[2],
    }


def _has_complete_controls(experiment: Mapping[str, Any]) -> bool:
    report = experiment["aggregate"]
    runs = report.get("runs")
    comparisons = report.get("baseline_comparisons")
    if not isinstance(runs, list) or not isinstance(comparisons, list) or any(not isinstance(item, dict) for item in comparisons):
        return False
    candidate_id = experiment["attempt_dir"].parent.name
    candidate_comparisons = [item for item in comparisons if item.get("candidate_experiment_id") == candidate_id]
    baseline_ids = {
        item.get("baseline_strategy_id")
        for item in candidate_comparisons
        if item.get("status") == "COMPARABLE"
    }
    try:
        expected_keys = {
            (fold["fold_id"], fold["result"]["cost_scenario"], strategy_id)
            for fold in experiment["metrics"].get("folds", [])
            for strategy_id in ("cash", "buy_and_hold", "randomized_placebo")
        }
    except (KeyError, TypeError):
        return False
    actual_keys = {
        (item.get("fold"), item.get("cost_scenario"), item.get("baseline_strategy_id"))
        for item in candidate_comparisons
        if item.get("status") == "COMPARABLE"
    }
    return baseline_ids == {"cash", "buy_and_hold", "randomized_placebo"} and actual_keys == expected_keys


def _verify_batch_comparisons(experiment: Mapping[str, Any], experiment_id: str) -> None:
    report = experiment["aggregate"]
    summaries = report.get("runs")
    if not isinstance(summaries, list):
        raise CandidateFreezeError("aggregate report is missing run summaries")
    selected_summaries = [
        item for item in summaries
        if isinstance(item, dict) and item.get("experiment_id") == experiment_id
    ]
    if len(selected_summaries) != 1 or selected_summaries[0].get("status") != "COMPLETE":
        raise CandidateFreezeError("aggregate report does not contain exactly one completed candidate run")
    selected_summary = selected_summaries[0]
    if (
        selected_summary.get("candidate_family") != experiment["identity"].get("candidate_family")
        or selected_summary.get("strategy_id") != experiment["identity"].get("strategy_id")
    ):
        raise CandidateFreezeError("aggregate candidate run identity is inconsistent")

    verified_summaries: list[dict[str, Any]] = [{
        "experiment_id": experiment_id,
        "candidate_family": experiment["identity"]["candidate_family"],
        "strategy_id": experiment["identity"]["strategy_id"],
        "status": "COMPLETE",
        "metrics": str(experiment["metrics_path"]),
    }]
    baseline_by_strategy: dict[str, dict[str, Any]] = {}
    for summary in summaries:
        if not isinstance(summary, dict) or summary.get("strategy_id") not in {"cash", "buy_and_hold", "randomized_placebo"}:
            continue
        strategy_id = summary["strategy_id"]
        if strategy_id in baseline_by_strategy or summary.get("status") != "COMPLETE":
            raise CandidateFreezeError(f"aggregate baseline {strategy_id!r} is duplicated or incomplete")
        baseline_id = summary.get("experiment_id")
        if not isinstance(baseline_id, str) or not _EXPERIMENT_ID.fullmatch(baseline_id):
            raise CandidateFreezeError("aggregate baseline experiment id is malformed")
        baseline_attempts = sorted(
            (Path(path) for path in (experiment["batch_root"] / "runs" / baseline_id).glob("attempt-*/manifest.json")),
            key=lambda path: int(_read_json_object(path)["attempt"]),
        )
        if not baseline_attempts:
            raise CandidateFreezeError(f"baseline {strategy_id!r} has no attempt manifest")
        manifest_path = baseline_attempts[-1]
        baseline_attempt = _verify_attempt(manifest_path.parent, baseline_id, _read_json_object(manifest_path))
        baseline_identity = baseline_attempt["identity"]
        if (
            baseline_identity.get("candidate_family") != "baseline_controls"
            or baseline_identity.get("strategy_id") != strategy_id
            or baseline_identity.get("dataset_sha256") != experiment["identity"].get("dataset_sha256")
            or baseline_identity.get("code_revision") != experiment["identity"].get("code_revision")
            or baseline_identity.get("cost_config") != experiment["identity"].get("cost_config")
            or baseline_identity.get("fold_config") != experiment["identity"].get("fold_config")
        ):
            raise CandidateFreezeError(f"baseline {strategy_id!r} does not share the candidate experiment assumptions")
        baseline_by_strategy[strategy_id] = {
            "experiment_id": baseline_id,
            "candidate_family": "baseline_controls",
            "strategy_id": strategy_id,
            "status": "COMPLETE",
            "metrics": str(baseline_attempt["metrics_path"]),
        }
    if set(baseline_by_strategy) != {"cash", "buy_and_hold", "randomized_placebo"}:
        raise CandidateFreezeError("aggregate report lacks complete cash, buy-and-hold, or randomized-placebo controls")
    verified_summaries.extend(baseline_by_strategy[item["strategy_id"]] for item in summaries if isinstance(item, dict) and item.get("strategy_id") in baseline_by_strategy)
    expected = _baseline_comparisons(verified_summaries)
    expected = [item for item in expected if item.get("candidate_experiment_id") == experiment_id]
    actual = [
        item for item in report.get("baseline_comparisons", [])
        if isinstance(item, dict) and item.get("candidate_experiment_id") == experiment_id
    ]
    if sorted(expected, key=_comparison_key) != sorted(actual, key=_comparison_key):
        raise CandidateFreezeError("aggregate baseline/placebo results do not match verified run metrics")


def _comparison_key(value: Mapping[str, Any]) -> tuple[str, int, str]:
    return (
        str(value.get("baseline_strategy_id")),
        int(value.get("fold", -1)),
        str(value.get("cost_scenario")),
    )


def _validate_experiment(experiment_id: str, experiment: Mapping[str, Any]) -> dict[str, str]:
    identity = experiment["identity"]
    provenance = identity["dataset_provenance"]
    metrics = experiment["metrics"]
    strategy_id = identity.get("strategy_id")
    if (
        not isinstance(strategy_id, str)
        or strategy_id not in governed_candidate_strategy_ids()
        or identity.get("candidate_family") != candidate_family_for_strategy(strategy_id)
    ):
        raise CandidateFreezeError("only an inventoried project strategy with a governed adapter can be frozen")
    bindings = identity.get("definition_bindings")
    if not isinstance(bindings, dict):
        raise CandidateFreezeError("immutable strategy and feature definition bindings are missing")
    for kind, definition_id, config in (
        ("strategy", identity.get("strategy_id"), identity.get("strategy_config")),
        ("feature", "completed_candle_history", identity.get("feature_config")),
    ):
        binding = bindings.get(kind)
        if not isinstance(binding, dict):
            raise CandidateFreezeError(f"immutable {kind} definition binding is invalid")
        try:
            definition_record = validate_definition_record({
                key: value for key, value in binding.items() if key != "config_sha256"
            })
        except DefinitionRegistryError as exc:
            raise CandidateFreezeError(f"immutable {kind} definition binding is invalid: {exc}") from exc
        if (
            definition_record["kind"] != kind
            or definition_record["definition_id"] != definition_id
            or not _is_sha256(binding.get("config_sha256"))
            or binding["config_sha256"] != _hash_json(config)
        ):
            raise CandidateFreezeError(f"immutable {kind} definition/config binding is invalid")
    if identity.get("experiment_id") not in (None, experiment_id):
        raise CandidateFreezeError("experiment identity does not match the requested id")
    manifest = provenance.get("dataset_manifest")
    if not isinstance(manifest, dict):
        raise CandidateFreezeError("complete source dataset manifest is missing")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("dataset_id") != identity.get("dataset_id")
        or manifest.get("dataset_role") != "DEVELOPMENT_EXPLORATORY"
        or manifest.get("allowed_for_candidate_selection") is not True
        or manifest.get("integrity_status") != "PASS"
        or manifest.get("provenance_confidence") != "PROVEN"
        or provenance.get("dataset_role") != "DEVELOPMENT_EXPLORATORY"
        or provenance.get("allowed_for_candidate_selection") is not True
        or provenance.get("integrity_status") != "PASS"
        or provenance.get("provenance_confidence") != "PROVEN"
        or provenance.get("data_sha256") != identity.get("dataset_sha256")
    ):
        raise CandidateFreezeError("dataset provenance is incomplete, unqualified, or outside the development role")
    manifest_sha = provenance.get("dataset_manifest_sha256")
    content_sha = provenance.get("dataset_manifest_content_sha256")
    if not _is_sha256(manifest_sha) or not _is_sha256(content_sha) or content_sha != _hash_json(manifest):
        raise CandidateFreezeError("dataset manifest hash binding is incomplete or invalid")
    for key in ("dataset_id", "data_sha256", "candle_count"):
        expected = identity.get("dataset_id") if key == "dataset_id" else identity.get("dataset_sha256") if key == "data_sha256" else None
        if expected is not None and manifest.get(key) != expected:
            raise CandidateFreezeError(f"dataset manifest {key} does not match the batch identity")
    candle_count = manifest.get("candle_count")
    if isinstance(candle_count, bool) or not isinstance(candle_count, int) or candle_count <= 0:
        raise CandidateFreezeError("dataset manifest candle_count must be positive")
    cost_scenarios, names = _validate_cost_sensitivity(identity.get("cost_config"))
    fold_config = identity.get("fold_config")
    if not isinstance(fold_config, dict):
        raise CandidateFreezeError("walk-forward fold configuration is missing")
    n_folds = fold_config.get("n_folds")
    if (
        isinstance(n_folds, bool)
        or not isinstance(n_folds, int)
        or n_folds <= 0
        or fold_config.get("window_mode") not in {"ROLLING", "EXPANDING"}
        or any(
            isinstance(fold_config.get(name), bool)
            or not isinstance(fold_config.get(name), (int, float))
            or not math.isfinite(fold_config[name])
            or fold_config[name] < 0
            for name in ("purge_s", "embargo_s")
        )
        or n_folds != metrics.get("n_folds")
    ):
        raise CandidateFreezeError("walk-forward fold configuration is missing or incomplete")
    expected_results = int(fold_config["n_folds"]) * len(cost_scenarios)
    folds = metrics.get("folds")
    if not isinstance(folds, list) or len(folds) != expected_results:
        raise CandidateFreezeError("walk-forward results do not cover every fold and cost scenario")
    observed: set[tuple[int, str]] = set()
    result_rows: list[dict[str, Any]] = []
    parameter_hashes: dict[int, str] = {}
    for raw in folds:
        if not isinstance(raw, dict):
            raise CandidateFreezeError("walk-forward fold record is invalid")
        try:
            run = fold_run_from_dict(raw)
            result = ExperimentResult.from_dict(raw["result"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CandidateFreezeError(f"walk-forward result schema is invalid: {exc}") from exc
        key = (run.fold_id, result.cost_scenario)
        if key in observed or result.status.value != "COMPLETED":
            raise CandidateFreezeError("walk-forward results contain duplicate or unsupported fold/cost records")
        observed.add(key)
        if (
            run.fold_id != result.fold
            or result.experiment_id == ""
            or result.candidate_family != identity.get("candidate_family")
            or result.strategy_id != identity.get("strategy_id")
            or result.dataset_id != identity.get("dataset_id")
            or result.code_revision != identity.get("code_revision")
            or result.cost_scenario not in names
        ):
            raise CandidateFreezeError("standardized result identity does not match its experiment")
        if not _is_sha256(run.frozen_parameter_sha256):
            raise CandidateFreezeError("fold fitted-parameter hash is missing")
        previous = parameter_hashes.setdefault(run.fold_id, run.frozen_parameter_sha256)
        if previous != run.frozen_parameter_sha256:
            raise CandidateFreezeError("fitted strategy parameters changed across cost scenarios")
        if run.train_end_utc >= run.validation_start_utc:
            raise CandidateFreezeError("training and validation periods overlap")
        result_rows.append(result.to_dict())
    expected_keys = {(fold, name) for fold in range(int(fold_config["n_folds"])) for name in names}
    if observed != expected_keys:
        raise CandidateFreezeError("walk-forward fold/cost coverage is incomplete")
    for tier in ("conservative", "stress", "extreme"):
        if any(
            result.net_return is None or result.net_return <= 0.0
            for fold in folds
            for result in (ExperimentResult.from_dict(fold["result"]),)
            if result.cost_scenario == tier
        ):
            raise CandidateFreezeError(f"candidate does not retain positive net return at every {tier} fold")
    return {
        "metrics_sha256": experiment["metrics_sha256"],
        "dataset_manifest_sha256": str(manifest_sha),
        "dataset_manifest_content_sha256": str(content_sha),
        "cost_scenarios_sha256": _hash_json(cost_scenarios),
        "latency_assumptions_sha256": _hash_json([
            {"name": item["name"], "latency_ms": item["latency_ms"]} for item in cost_scenarios
        ]),
        "strategy_config_sha256": _hash_json(identity.get("strategy_config")),
        "feature_config_sha256": _hash_json(identity.get("feature_config")),
        "metrics_definition_sha256": _git_file_sha256(
            str(identity["code_revision"]), "src/bithumb_coin_trader/research_infra/result_schema.py"
        ),
        "strategy_source_sha256": _strategy_source_sha256(identity),
        "result_rows_sha256": _hash_json(result_rows),
    }


def _validate_cost_sensitivity(value: Any) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    if not isinstance(value, list) or len(value) < len(_REQUIRED_COST_TIERS):
        raise CandidateFreezeError("candidate freeze requires base, conservative, stress, and extreme cost scenarios")
    scenarios: list[dict[str, Any]] = []
    names: list[str] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise CandidateFreezeError("cost scenario must be an object")
        try:
            scenario = SpotCostScenario.from_dict(raw)
        except CostScenarioError as exc:
            raise CandidateFreezeError(f"invalid cost scenario: {exc}") from exc
        scenarios.append(scenario.to_dict())
        names.append(scenario.name)
    if tuple(names[:4]) != _REQUIRED_COST_TIERS or len(set(names)) != len(names):
        raise CandidateFreezeError("cost scenarios must include the ordered base/conservative/stress/extreme tiers")
    previous: tuple[float, ...] | None = None
    for raw in scenarios:
        current = tuple(float(raw[name]) for name in (
            "maker_fee_bps", "taker_fee_bps", "slippage_bps", "latency_ms",
            "minimum_order_notional", "tick_size", "lot_size",
        ))
        if previous is not None and (
            any(now < before for before, now in zip(previous, current))
            or not any(now > before for before, now in zip(previous, current))
        ):
            raise CandidateFreezeError("cost tiers must increase monotonically in explicit adverse assumptions")
        previous = current
    return scenarios, tuple(names)


def _validate_lifecycle_history(
    history: list[dict[str, Any]],
    experiment_id: str,
    identity: Mapping[str, Any],
    provenance: Mapping[str, Any],
    hashes: Mapping[str, str],
    experiment: Mapping[str, Any],
) -> None:
    definition = history[0]["evidence"].get("definition", {})
    if _contains_external(definition.get("origin"), definition.get("origin_tags", [])):
        raise CandidateFreezeError("external-origin hypotheses cannot be frozen")
    retrospective = history[1]["evidence"]
    expected_values = {
        "experiment_id": experiment_id,
        "dataset_manifest_sha256": hashes["dataset_manifest_sha256"],
        "result_manifest_sha256": experiment["complete_sha256"],
        "code_commit": identity.get("code_revision"),
        "strategy_config_sha256": hashes["strategy_config_sha256"],
        "feature_definition_sha256": hashes["feature_config_sha256"],
        "cost_model_sha256": hashes["cost_scenarios_sha256"],
        "latency_assumptions_sha256": hashes["latency_assumptions_sha256"],
        "provenance_sha256": _hash_json(provenance),
        "metrics_sha256": experiment["metrics_sha256"],
        "dataset_roles": ["DEVELOPMENT_EXPLORATORY"],
        "random_seed": identity.get("seed"),
        "deterministic_rerun": "PASS",
        "purge_embargo_seconds": identity.get("fold_config", {}).get("purge_s", 0)
        + identity.get("fold_config", {}).get("embargo_s", 0),
    }
    for key, expected in expected_values.items():
        if retrospective.get(key) != expected:
            raise CandidateFreezeError(f"candidate retrospective lifecycle evidence does not match experiment field {key}")
    first_fold = next(
        (fold for fold in experiment["metrics"]["folds"] if fold.get("fold_id") == 0),
        None,
    )
    if not isinstance(first_fold, dict):
        raise CandidateFreezeError("walk-forward evidence is missing its first fold")
    expected_ranges = {
        "training_range": {
            "start_utc": _utc_timestamp_z(first_fold["train_start_utc"]),
            "end_utc": _utc_timestamp_z(first_fold["train_end_utc"]),
        },
        "validation_range": {
            "start_utc": _utc_timestamp_z(first_fold["validation_start_utc"]),
            "end_utc": _utc_timestamp_z(first_fold["validation_end_utc"]),
        },
    }
    if any(retrospective.get(name) != value for name, value in expected_ranges.items()):
        raise CandidateFreezeError("candidate retrospective time ranges do not match walk-forward fold zero")
    robustness = history[2]["evidence"]
    costs = identity["cost_config"]
    comparisons = experiment["aggregate"]["baseline_comparisons"]
    baseline_comparisons = [item for item in comparisons if item.get("candidate_experiment_id") == experiment["attempt_dir"].parent.name and item.get("baseline_strategy_id") != "randomized_placebo"]
    placebo_comparisons = [item for item in comparisons if item.get("candidate_experiment_id") == experiment["attempt_dir"].parent.name and item.get("baseline_strategy_id") == "randomized_placebo"]
    required_robustness = {
        "robustness_report_sha256": experiment["aggregate_sha256"],
        "baseline_report_sha256": _hash_json(baseline_comparisons),
        "placebo_report_sha256": _hash_json(placebo_comparisons),
        "walk_forward_status": "PASS",
        "cost_scenarios": [scenario["name"] for scenario in costs],
    }
    for key, expected in required_robustness.items():
        if robustness.get(key) != expected:
            raise CandidateFreezeError(f"candidate robustness lifecycle evidence does not match {key}")
    if history[3]["evidence"].get("decision") != "PASS":
        raise CandidateFreezeError("candidate has no recorded PASS selection decision")


def _make_freeze_record(
    *,
    candidate_id: str,
    experiment_id: str,
    identity: Mapping[str, Any],
    provenance: Mapping[str, Any],
    experiment: Mapping[str, Any],
    source_hashes: Mapping[str, str],
    frozen_at_utc: str,
) -> dict[str, Any]:
    aggregate = experiment["aggregate"]
    metrics = experiment["metrics"]
    record: dict[str, Any] = {
        "schema_version": 2,
        "candidate_id": candidate_id,
        "experiment_id": experiment_id,
        "candidate_family": identity["candidate_family"],
        "strategy_id": identity["strategy_id"],
        "frozen_at_utc": frozen_at_utc,
        "code_revision": identity["code_revision"],
        "strategy_config": identity["strategy_config"],
        "strategy_config_sha256": source_hashes["strategy_config_sha256"],
        "feature_config": identity["feature_config"],
        "feature_config_sha256": source_hashes["feature_config_sha256"],
        "definition_bindings": identity["definition_bindings"],
        "strategy_source_sha256": source_hashes["strategy_source_sha256"],
        "metrics_schema_version": RESULT_SCHEMA_VERSION,
        "metrics_definition_sha256": source_hashes["metrics_definition_sha256"],
        "dataset_id": identity["dataset_id"],
        "dataset_sha256": identity["dataset_sha256"],
        "dataset_manifest_sha256": source_hashes["dataset_manifest_sha256"],
        "dataset_manifest_content_sha256": source_hashes["dataset_manifest_content_sha256"],
        "dataset_provenance": provenance,
        "cost_scenarios": identity["cost_config"],
        "cost_scenarios_sha256": source_hashes["cost_scenarios_sha256"],
        "fold_config": identity["fold_config"],
        "seed": identity["seed"],
        "experiment_metrics_sha256": experiment["metrics_sha256"],
        "experiment_results_sha256": source_hashes["result_rows_sha256"],
        "metrics_relative_path": str(experiment["metrics_path"]),
        "batch_id": aggregate["batch_id"],
        "batch_manifest_sha256": experiment["batch_manifest_sha256"],
        "aggregate_report_sha256": experiment["aggregate_sha256"],
        "baseline_comparisons_sha256": _hash_json([
            item for item in aggregate["baseline_comparisons"]
            if item.get("candidate_experiment_id") == experiment["attempt_dir"].parent.name
            and item.get("baseline_strategy_id") != "randomized_placebo"
        ]),
        "placebo_comparisons_sha256": _hash_json([
            item for item in aggregate["baseline_comparisons"]
            if item.get("candidate_experiment_id") == experiment["attempt_dir"].parent.name
            and item.get("baseline_strategy_id") == "randomized_placebo"
        ]),
        "dataset_roles": [provenance["dataset_role"]],
        "source_research_sha256": experiment["metrics_sha256"],
        "readiness": "FROZEN_REQUIRES_SEPARATE_PAPER_READINESS_AND_AUTHORIZATION",
    }
    if not metrics.get("folds"):
        raise CandidateFreezeError("freeze cannot bind an empty walk-forward result set")
    return record


def _verify_existing_freeze(existing: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    if existing != expected:
        raise CandidateFreezeError("existing candidate freeze artifact does not match current immutable evidence")
    if (
        not _is_sha256(existing.get("artifact_sha256"))
        or _hash_json({key: value for key, value in existing.items() if key != "artifact_sha256"})
        != existing.get("artifact_sha256")
    ):
        raise CandidateFreezeError("existing candidate freeze artifact hash is invalid")


def _strategy_source_sha256(identity: Mapping[str, Any]) -> str:
    strategy_id = identity.get("strategy_id")
    if not isinstance(strategy_id, str) or strategy_id not in governed_candidate_strategy_ids():
        raise CandidateFreezeError("strategy source has no registered immutable source mapping")
    paths = [f"src/bithumb_coin_trader/{module}" for module in strategy_source_modules(strategy_id)]
    raw: list[dict[str, str]] = []
    for path in paths:
        raw.append({"path": path, "sha256": _git_file_sha256(str(identity["code_revision"]), path)})
    return _hash_json(raw)


def _git_file_sha256(revision: str, path: str) -> str:
    if re.fullmatch(r"[0-9a-fA-F]{40}", revision) is None:
        raise CandidateFreezeError("code_revision must be a full Git commit hash")
    try:
        result = subprocess.run(
            ["git", "show", f"{revision}:{path}"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CandidateFreezeError(f"unable to read committed source {path}: {exc}") from exc
    if result.returncode != 0:
        raise CandidateFreezeError(f"committed source {path} is missing at code_revision")
    return hashlib.sha256(result.stdout).hexdigest()


def _contains_external(origin: Any, tags: Any) -> bool:
    text = " ".join([str(origin), *(str(tag) for tag in tags)]).upper() if isinstance(tags, list) else str(origin).upper()
    return any(token in text for token in ("EXTERNAL", "AOA", "BITMEX", "EXPERT", "HYPOTHESIS_GENERATION_ONLY"))


def _read_json_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise CandidateFreezeError(f"evidence file is missing or a symlink: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateFreezeError(f"unable to read evidence JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CandidateFreezeError(f"evidence JSON must contain an object: {path}")
    return value


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise CandidateFreezeError(f"refusing to overwrite prior candidate freeze: {path}")
        return
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        if path.read_bytes() != payload:
            raise CandidateFreezeError(f"refusing to overwrite prior candidate freeze: {path}") from exc
        return
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("candidate freeze write made no progress")
            offset += written
        os.fsync(descriptor)
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        os.close(descriptor)


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise CandidateFreezeError(f"cannot hash required evidence file {path}: {exc}") from exc


def _hash_json(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CandidateFreezeError("candidate freeze data must be canonical finite JSON") from exc


def _utc_timestamp_z(value: Any) -> str:
    if not isinstance(value, str):
        raise CandidateFreezeError("walk-forward time range contains a non-string timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CandidateFreezeError("walk-forward time range contains an invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CandidateFreezeError("walk-forward time range timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None
