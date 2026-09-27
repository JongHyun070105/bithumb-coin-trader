"""Fail-closed validator for a machine-verifiable prospective PAPER bundle."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
from typing import Any

from .candidate_registry import CandidateLifecycle, CandidateLifecycleError, CandidateRegistry
from .costs import CostScenarioError, SpotCostScenario
from .freeze import FrozenCandidate


PASS = "PASS"
FAIL = "FAIL"
NOT_VERIFIABLE = "NOT_VERIFIABLE"
CHECK_NAMES = (
    "DATA_READY",
    "RESEARCH_READY",
    "CANDIDATE_FROZEN",
    "RISK_READY",
    "EXECUTION_READY",
    "OBSERVABILITY_READY",
    "SECRETS_SAFE",
    "PRIVATE_API_DISABLED",
)
REQUIRED_RISK_LIMITS = (
    "max_position_notional",
    "max_total_exposure",
    "max_order_notional",
    "max_daily_loss",
    "max_drawdown",
    "max_consecutive_failures",
    "max_api_error_rate",
    "max_stale_data_age_seconds",
    "max_spread_bps",
)
REQUIRED_RETROSPECTIVE_METRICS = {
    "net_return",
    "cagr",
    "max_drawdown",
    "sharpe",
    "sortino",
    "calmar",
    "profit_factor",
    "win_rate",
    "expectancy",
    "turnover",
    "trade_count",
    "exposure",
    "tail_loss",
    "worst_day",
    "worst_week",
    "fees_paid",
    "slippage_cost",
    "regime_results",
    "fold_results",
}
REQUIRED_COST_PARAMETERS = (
    "maker_fee_bps",
    "taker_fee_bps",
    "slippage_bps",
    "latency_ms",
    "minimum_order_notional",
    "tick_size",
    "lot_size",
)
REQUIRED_PAPER_METRICS = {
    "strategy_state",
    "market_data_freshness",
    "signals",
    "orders",
    "fills",
    "positions",
    "balance",
    "pnl",
    "fees",
    "drawdown",
    "risk_gates",
    "errors",
    "restarts",
}
ARTIFACT_NAMES = (
    "dataset",
    "research",
    "candidate",
    "risk",
    "execution",
    "observability",
    "security",
)


def evaluate_paper_readiness(evidence_dir: Path) -> dict[str, Any]:
    """Verify a readiness bundle without starting PAPER or contacting services."""
    root = Path(evidence_dir).resolve()
    bundle_path = root / "paper-readiness-bundle.json"
    checks = {
        name: {"status": NOT_VERIFIABLE, "reason": "readiness bundle is missing"}
        for name in CHECK_NAMES
    }
    artifacts: dict[str, tuple[dict[str, Any], str] | None] = {name: None for name in ARTIFACT_NAMES}
    if bundle_path.is_symlink() or not bundle_path.is_file() or root not in bundle_path.resolve().parents:
        return _report(checks)

    try:
        bundle = _read_json(bundle_path)
    except (OSError, ValueError) as exc:
        for name in CHECK_NAMES:
            checks[name] = {"status": NOT_VERIFIABLE, "reason": f"bundle cannot be read: {exc}"}
        return _report(checks)

    if bundle.get("schema_version") != 1 or not isinstance(bundle.get("artifacts"), dict):
        for name in CHECK_NAMES:
            checks[name] = {"status": FAIL, "reason": "bundle schema is invalid"}
        return _report(checks)

    for name in ARTIFACT_NAMES:
        try:
            artifacts[name] = _load_artifact(root, bundle["artifacts"].get(name), name)
        except (KeyError, OSError, ValueError, TypeError) as exc:
            artifacts[name] = None
            mapped_check = {
                "dataset": "DATA_READY",
                "research": "RESEARCH_READY",
                "candidate": "CANDIDATE_FROZEN",
                "risk": "RISK_READY",
                "execution": "EXECUTION_READY",
                "observability": "OBSERVABILITY_READY",
                "security": "SECRETS_SAFE",
            }[name]
            checks[mapped_check] = {"status": NOT_VERIFIABLE, "reason": str(exc)}

    dataset = artifacts["dataset"]
    if dataset is not None:
        payload, artifact_hash = dataset
        checks["DATA_READY"] = _check_data(payload, root, bundle["artifacts"]["dataset"], artifact_hash)

    research = artifacts["research"]
    if research is not None:
        payload, research_hash = research
        dataset_hash = artifacts["dataset"][1] if artifacts["dataset"] else None
        checks["RESEARCH_READY"] = _check_research(payload, dataset_hash)
    else:
        research_hash = None

    if artifacts["candidate"] is not None:
        payload, _ = artifacts["candidate"]
        checks["CANDIDATE_FROZEN"] = _check_candidate(payload, research_hash)

    if artifacts["risk"] is not None:
        checks["RISK_READY"] = _check_risk(artifacts["risk"][0])

    if artifacts["execution"] is not None:
        execution_payload = artifacts["execution"][0]
        checks["EXECUTION_READY"] = _check_execution(execution_payload)
        checks["PRIVATE_API_DISABLED"] = _check_private_disabled(execution_payload)

    if artifacts["observability"] is not None:
        checks["OBSERVABILITY_READY"] = _check_observability(artifacts["observability"][0])

    if artifacts["security"] is not None:
        checks["SECRETS_SAFE"] = _check_security(artifacts["security"][0])

    execution_payload = artifacts["execution"][0] if artifacts["execution"] is not None else None
    return _report(checks, execution_payload)


def write_paper_readiness_report(report: dict[str, Any], output_dir: Path, evidence_dir: Path) -> None:
    destination = Path(output_dir).resolve()
    evidence_root = Path(evidence_dir).resolve()
    if destination == evidence_root or evidence_root in destination.parents:
        raise ValueError("readiness report output must be outside the evidence bundle")
    if destination.exists():
        raise ValueError(f"report output already exists; refusing to overwrite: {destination}")
    destination.mkdir(parents=True)
    (destination / "paper-readiness.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = ["# PAPER Readiness", "", f"PAPER_ELIGIBLE: {report['PAPER_ELIGIBLE']}", "", "| Check | Status | Reason |", "|---|---|---|"]
    lines.extend(
        f"| {name} | {check['status']} | {check['reason']} |"
        for name, check in report["checks"].items()
    )
    lines.append("")
    (destination / "paper-readiness.md").write_text("\n".join(lines), encoding="utf-8")


def _load_artifact(root: Path, reference: Any, name: str) -> tuple[dict[str, Any], str]:
    if not isinstance(reference, dict):
        raise ValueError(f"{name} evidence reference is missing")
    relative = reference.get("path")
    expected_hash = reference.get("sha256")
    if not isinstance(relative, str) or not isinstance(expected_hash, str):
        raise ValueError(f"{name} evidence path or hash is missing")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash):
        raise ValueError(f"{name} evidence SHA-256 is malformed")
    posix = PurePosixPath(relative)
    if posix.is_absolute() or ".." in posix.parts:
        raise ValueError(f"{name} evidence path must remain inside the bundle")
    path = (root / Path(*posix.parts)).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError(f"{name} evidence file is missing or outside the bundle")
    actual_hash = _hash_file(path)
    if actual_hash.lower() != expected_hash.lower():
        raise ValueError(f"{name} evidence hash mismatch")
    payload = _read_json(path)
    return payload, actual_hash


def _check_data(payload: dict[str, Any], root: Path, reference: dict[str, Any], artifact_hash: str) -> dict[str, str]:
    if payload.get("build_status") != "DATA_READY" or payload.get("dq_status") != PASS:
        return _status(FAIL, "canonical build and DQ are not both PASS")
    if payload.get("dataset_role") != "PROSPECTIVE_RESEARCH":
        return _status(FAIL, "PAPER requires a PROSPECTIVE_RESEARCH dataset")
    if payload.get("candidate_selection_allowed") is not True or payload.get("holdout_evaluated") is not False:
        return _status(FAIL, "dataset role flags are unsafe for candidate research")
    if not _valid_sha(payload.get("events_sha256")) or not isinstance(payload.get("events_file"), str):
        return _status(NOT_VERIFIABLE, "canonical event file/hash is missing")
    artifact_path = (root / reference["path"]).resolve()
    event_path = (artifact_path.parent / payload["events_file"]).resolve()
    if artifact_path.parent not in event_path.parents or not event_path.is_file():
        return _status(NOT_VERIFIABLE, "canonical event file is missing or outside its dataset directory")
    if _hash_file(event_path) != payload["events_sha256"]:
        return _status(FAIL, "canonical event file hash does not match its manifest")
    source_files = payload.get("source_files")
    if not isinstance(source_files, list) or not source_files or any(
        not isinstance(item, dict)
        or not isinstance(item.get("path"), str)
        or not _valid_sha(item.get("sha256"))
        for item in source_files
    ):
        return _status(NOT_VERIFIABLE, "source-file hashes are incomplete")
    source_set_hash = _hash_text(json.dumps(source_files, sort_keys=True, separators=(",", ":"), allow_nan=False))
    if payload.get("source_set_sha256") != source_set_hash:
        return _status(FAIL, "source-file set hash does not match the canonical build manifest")
    return _status(PASS, f"source-bound DATA_READY manifest verified ({artifact_hash[:12]})")


def _check_research(payload: dict[str, Any], dataset_hash: str | None) -> dict[str, str]:
    if payload.get("batch_status") != PASS or payload.get("reproducible_inputs_complete") is not True:
        return _status(FAIL, "research batch is not PASS or reproducible inputs are incomplete")
    if not payload.get("experiment_id") or payload.get("dataset_manifest_sha256") != dataset_hash:
        return _status(FAIL, "research evidence does not bind the DATA_READY manifest")
    if payload.get("walk_forward_status") != PASS or payload.get("placebo_status") != PASS:
        return _status(FAIL, "walk-forward or placebo evidence is not PASS")
    if payload.get("baseline_status") != PASS:
        return _status(FAIL, "baseline comparison is not PASS")
    scenarios = payload.get("cost_scenarios")
    if not isinstance(scenarios, list) or len(scenarios) < 2:
        return _status(FAIL, "at least two cost scenarios are required")
    scenario_names: set[str] = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict) or not isinstance(scenario.get("name"), str):
            return _status(NOT_VERIFIABLE, "cost scenario identity/parameters are missing")
        if scenario["name"] in scenario_names:
            return _status(FAIL, "cost scenario names must be unique")
        scenario_names.add(scenario["name"])
        missing_cost_fields = set(REQUIRED_COST_PARAMETERS) - scenario.keys()
        if missing_cost_fields:
            return _status(NOT_VERIFIABLE, f"cost parameters missing: {', '.join(sorted(missing_cost_fields))}")
        if "partial_fill_probability" not in scenario and scenario.get("partial_fill_status") != "UNSUPPORTED":
            return _status(NOT_VERIFIABLE, "partial-fill probability or explicit unsupported state is missing")
        try:
            SpotCostScenario.from_dict(scenario)
        except CostScenarioError as exc:
            return _status(FAIL, f"invalid cost scenario: {exc}")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        return _status(NOT_VERIFIABLE, "retrospective metrics are missing")
    missing_metrics = REQUIRED_RETROSPECTIVE_METRICS - metrics.keys()
    if missing_metrics:
        return _status(NOT_VERIFIABLE, f"retrospective metrics missing: {', '.join(sorted(missing_metrics))}")
    for name in REQUIRED_RETROSPECTIVE_METRICS - {"regime_results", "fold_results"}:
        value = metrics[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return _status(FAIL, f"retrospective metric {name} must be finite")
    if metrics["trade_count"] < 1:
        return _status(FAIL, "retrospective evidence contains no trades")
    for name in ("regime_results", "fold_results"):
        if not isinstance(metrics[name], list) or not metrics[name]:
            return _status(NOT_VERIFIABLE, f"retrospective {name} are missing")
    roles = payload.get("dataset_roles")
    if not isinstance(roles, list) or not roles or any("EXTERNAL" in str(role).upper() or "HYPOTHESIS_GENERATION_ONLY" in str(role).upper() for role in roles):
        return _status(FAIL, "research evidence includes unqualified external-only data")
    return _status(PASS, "reproducible batch, walk-forward, cost-grid and placebo evidence verified")


def _check_candidate(payload: dict[str, Any], research_hash: str | None) -> dict[str, str]:
    candidate_payload = payload.get("candidate")
    if not isinstance(candidate_payload, dict):
        return _status(NOT_VERIFIABLE, "frozen candidate record is missing")
    try:
        candidate = FrozenCandidate.from_dict(candidate_payload)
    except (KeyError, TypeError, ValueError) as exc:
        return _status(NOT_VERIFIABLE, f"frozen candidate record is incomplete: {exc}")
    recorded_hash = candidate_payload.get("freeze_hash")
    if not isinstance(recorded_hash, str) or recorded_hash != candidate.freeze_hash:
        return _status(FAIL, "candidate freeze hash mismatch")
    if payload.get("source_research_sha256") != research_hash:
        return _status(FAIL, "candidate does not bind the verified research report")
    events = payload.get("lifecycle_events")
    if not isinstance(events, list):
        return _status(NOT_VERIFIABLE, "candidate lifecycle event chain is missing")
    try:
        CandidateRegistry.verify_events(events)
    except CandidateLifecycleError as exc:
        return _status(FAIL, f"candidate lifecycle event chain is invalid: {exc}")
    required_states = [status.value for status in (
        CandidateLifecycle.HYPOTHESIS,
        CandidateLifecycle.RETROSPECTIVE_EXPERIMENT,
        CandidateLifecycle.ROBUSTNESS_TESTED,
        CandidateLifecycle.CANDIDATE,
        CandidateLifecycle.FROZEN,
    )]
    if [event.get("to_status") for event in events] != required_states:
        return _status(FAIL, "candidate lifecycle must have verified evidence through FROZEN")
    frozen_event = events[-1]
    frozen_evidence = frozen_event["evidence"]
    candidate_sha = _hash_text(json.dumps(candidate.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False))
    if (
        frozen_event.get("candidate_id") != candidate.freeze_id
        or frozen_event.get("event_hash") != payload.get("transition_evidence_sha256")
        or frozen_evidence.get("freeze_hash") != candidate.freeze_hash
        or frozen_evidence.get("candidate_freeze_sha256") != candidate_sha
        or frozen_evidence.get("source_research_sha256") != research_hash
    ):
        return _status(FAIL, "FROZEN lifecycle evidence does not bind this candidate and research report")
    if not candidate.git_commit or candidate.git_commit == "unknown" or not candidate.source_manifest_fingerprint:
        return _status(FAIL, "candidate lacks a bound code revision or research manifest")
    roles = {role.upper() for role in candidate.source_dataset_roles}
    if not roles or any("EXTERNAL" in role or "HYPOTHESIS_GENERATION_ONLY" in role for role in roles):
        return _status(FAIL, "candidate was promoted from external-only evidence")
    return _status(PASS, f"frozen candidate hash verified ({recorded_hash[:12]})")


def _check_risk(payload: dict[str, Any]) -> dict[str, str]:
    missing = [name for name in REQUIRED_RISK_LIMITS if name not in payload]
    if missing:
        return _status(NOT_VERIFIABLE, f"risk limits missing: {', '.join(missing)}")
    for name in REQUIRED_RISK_LIMITS:
        value = payload[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            return _status(FAIL, f"risk limit {name} must be finite and positive")
    if payload["max_total_exposure"] > 1.0 or payload["max_api_error_rate"] > 1.0 or payload["max_daily_loss"] > 1.0 or payload["max_drawdown"] > 1.0:
        return _status(FAIL, "fractional risk limits must be at most 1.0")
    if payload["max_order_notional"] > payload["max_position_notional"]:
        return _status(FAIL, "max order notional cannot exceed max position notional")
    if payload.get("kill_switch_enabled") is not True or payload.get("circuit_breaker_enabled") is not True:
        return _status(FAIL, "kill switch and circuit breaker must be enabled")
    return _status(PASS, "all required risk limits and safety switches are configured")


def _check_execution(payload: dict[str, Any]) -> dict[str, str]:
    if payload.get("backend") != "PAPER":
        return _status(FAIL, "execution backend is not PAPER")
    if payload.get("durable_journal") is not True:
        return _status(FAIL, "durable paper journal is not enabled")
    for name in ("restart_recovery", "fill_idempotency", "partial_fills", "cancel_reconciliation"):
        if payload.get(name) != PASS:
            return _status(NOT_VERIFIABLE, f"execution evidence {name} is not PASS")
    return _status(PASS, "paper execution recovery and order-event controls are verified")


def _check_private_disabled(payload: dict[str, Any]) -> dict[str, str]:
    for key in ("private_api_enabled", "live_enabled"):
        if payload.get(key) is True:
            return _status(FAIL, f"{key} is enabled")
        if payload.get(key) is not False:
            return _status(NOT_VERIFIABLE, f"{key} is not explicitly disabled")
    for key in ("private_api_env_gate", "live_env_gate"):
        if payload.get(key) == "ENABLED":
            return _status(FAIL, f"{key} is enabled")
        if payload.get(key) != "DISABLED":
            return _status(NOT_VERIFIABLE, f"{key} is not explicitly disabled")
    return _status(PASS, "private API and LIVE are explicitly disabled in execution config")


def _check_observability(payload: dict[str, Any]) -> dict[str, str]:
    metrics = payload.get("metrics")
    if not isinstance(metrics, list):
        return _status(NOT_VERIFIABLE, "paper observability metric list is missing")
    missing = REQUIRED_PAPER_METRICS - set(metrics)
    if missing:
        return _status(FAIL, f"paper observability metrics missing: {', '.join(sorted(missing))}")
    if not payload.get("journal_source") or payload.get("restart_metrics") is not True:
        return _status(FAIL, "observability is not bound to the journal/restart state")
    return _status(PASS, "required paper metrics and journal/restart binding are present")


def _check_security(payload: dict[str, Any]) -> dict[str, str]:
    if payload.get("scan_status") != PASS or payload.get("secret_count") != 0:
        return _status(FAIL, "repository secret scan is not a clean PASS")
    if not _valid_sha(payload.get("scanner_sha256")) or not _valid_sha(payload.get("scanned_tree_sha256")):
        return _status(NOT_VERIFIABLE, "security scan is not bound to scanner/tree hashes")
    return _status(PASS, "zero-secret scan result is bound to scanner and source-tree hashes")


def _report(
    checks: dict[str, dict[str, str]],
    execution_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    eligible = all(checks[name]["status"] == PASS for name in CHECK_NAMES)

    def gate_state(flag_name: str, env_name: str) -> str:
        if execution_payload is None:
            return NOT_VERIFIABLE
        enabled = execution_payload.get(flag_name)
        env_gate = execution_payload.get(env_name)
        if enabled is True or env_gate == "ENABLED":
            return "ENABLED"
        if enabled is False and env_gate == "DISABLED":
            return "DISABLED"
        return NOT_VERIFIABLE

    return {
        "schema_version": 1,
        "checks": checks,
        "PAPER_ELIGIBLE": eligible,
        "scientific_state": {
            "ALPHA": "UNPROVEN",
            "PAPER": "NOT_STARTED",
            "LIVE": gate_state("live_enabled", "live_env_gate"),
            "PRIVATE_API": gate_state("private_api_enabled", "private_api_env_gate"),
        },
    }


def _status(status: str, reason: str) -> dict[str, str]:
    return {"status": status, "reason": reason}


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object expected at {path}")
    return payload


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None
