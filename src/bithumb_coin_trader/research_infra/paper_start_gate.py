"""Read-only, fail-closed gate check for the future local PAPER start path."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .paper_readiness import evaluate_paper_readiness
from .reliability_seal import validation_identity


REQUIRED_START_GATES = (
    "RELIABILITY_SEALED",
    "DATA_QUALIFIED",
    "CANDIDATE_FROZEN",
    "RISK_READY",
    "PAPER_ENGINE_READY",
    "OBSERVABILITY_READY",
    "SECRETS_SAFE",
    "PRIVATE_API_DISABLED",
)


class PaperStartGateError(ValueError):
    """Raised when a PAPER-start readiness input is malformed or inconsistent."""


def evaluate_paper_start_gates(
    *, evidence_dir: Path, candidate_freeze: Path, reliability_seal: Path
) -> dict[str, Any]:
    """Check readiness evidence only; this function never constructs a runtime."""
    root = Path(evidence_dir).resolve()
    report = evaluate_paper_readiness(root)
    checks = report.get("checks")
    if not isinstance(checks, dict):
        raise PaperStartGateError("PAPER readiness report has no checks")

    gates: dict[str, dict[str, str]] = {}
    gates["DATA_QUALIFIED"] = _map_check(checks, "DATA_READY")
    gates["CANDIDATE_FROZEN"] = _map_check(checks, "CANDIDATE_FROZEN")
    gates["RISK_READY"] = _map_check(checks, "RISK_READY")
    gates["PAPER_ENGINE_READY"] = _map_check(checks, "EXECUTION_READY")
    gates["OBSERVABILITY_READY"] = _map_check(checks, "OBSERVABILITY_READY")
    gates["SECRETS_SAFE"] = _map_check(checks, "SECRETS_SAFE")
    gates["PRIVATE_API_DISABLED"] = _map_check(checks, "PRIVATE_API_DISABLED")

    candidate_status = gates["CANDIDATE_FROZEN"]["status"]
    try:
        _verify_candidate_binding(root, Path(candidate_freeze))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        gates["CANDIDATE_FROZEN"] = {"status": "FAIL", "reason": str(exc)}
    else:
        if candidate_status != "PASS":
            gates["CANDIDATE_FROZEN"]["reason"] += "; freeze artifact binding verified but readiness check is not PASS"

    try:
        seal = _verify_reliability_seal(Path(reliability_seal))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        gates["RELIABILITY_SEALED"] = {"status": "FAIL", "reason": str(exc)}
    else:
        gates["RELIABILITY_SEALED"] = {
            "status": "PASS",
            "reason": f"sealed terminal PASS verified ({seal[:12]})",
        }

    return {
        "schema_version": 1,
        "PAPER_START_ALLOWED": all(gates[name]["status"] == "PASS" for name in REQUIRED_START_GATES),
        "PAPER": "NOT_STARTED",
        "checks": {name: gates[name] for name in REQUIRED_START_GATES},
    }


def _map_check(checks: dict[str, Any], name: str) -> dict[str, str]:
    value = checks.get(name)
    if not isinstance(value, dict) or value.get("status") != "PASS":
        status = value.get("status") if isinstance(value, dict) else "NOT_VERIFIABLE"
        reason = value.get("reason") if isinstance(value, dict) else "readiness check is missing"
        return {"status": str(status), "reason": str(reason)}
    return {"status": "PASS", "reason": str(value.get("reason", "verified"))}


def _verify_candidate_binding(evidence_root: Path, candidate_freeze: Path) -> None:
    bundle_path = evidence_root / "paper-readiness-bundle.json"
    if bundle_path.is_symlink() or not bundle_path.is_file():
        raise PaperStartGateError("PAPER readiness bundle is missing or a symlink")
    bundle = _read_object(bundle_path)
    artifacts = bundle.get("artifacts")
    candidate_ref = artifacts.get("candidate") if isinstance(artifacts, dict) else None
    if not isinstance(candidate_ref, dict) or not isinstance(candidate_ref.get("path"), str):
        raise PaperStartGateError("readiness bundle does not bind a candidate freeze")
    bundled_path = (evidence_root / candidate_ref["path"]).resolve()
    if evidence_root not in bundled_path.parents or bundled_path.is_symlink() or not bundled_path.is_file():
        raise PaperStartGateError("bundled candidate freeze is missing or outside the readiness directory")
    if candidate_freeze.is_symlink() or not candidate_freeze.is_file():
        raise PaperStartGateError("candidate freeze path is missing or a symlink")
    expected_hash = candidate_ref.get("sha256")
    bundled_hash = _sha256(bundled_path)
    if expected_hash != bundled_hash or _sha256(candidate_freeze) != bundled_hash:
        raise PaperStartGateError("candidate freeze does not match the readiness evidence hash")
    artifact = _read_object(candidate_freeze)
    unsigned = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    candidate = artifact.get("candidate")
    if (
        not isinstance(candidate, dict)
        or candidate.get("freeze_hash") != _hash_json({key: value for key, value in candidate.items() if key != "freeze_hash"})
    ):
        raise PaperStartGateError("candidate freeze or artifact content hash is invalid")
    if artifact.get("artifact_sha256") != _hash_json(unsigned):
        raise PaperStartGateError("candidate artifact hash does not bind its full manifest")


def _verify_reliability_seal(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise PaperStartGateError("reliability seal is missing or a symlink")
    payload = _read_object(path)
    required = {
        "schema_version", "gate", "terminal_verdict", "validation_identity_sha256",
        "terminal_evidence_sha256", "terminal_audit_path", "terminal_audit_sha256", "seal_sha256",
    }
    if set(payload) != required:
        raise PaperStartGateError("reliability seal fields do not match schema version 1")
    unsigned = {key: value for key, value in payload.items() if key != "seal_sha256"}
    audit_name = payload.get("terminal_audit_path")
    audit_path = path.parent / audit_name if isinstance(audit_name, str) and Path(audit_name).name == audit_name else None
    if audit_path is None or audit_path.is_symlink() or not audit_path.is_file():
        raise PaperStartGateError("sealed terminal audit is missing or is not a regular file")
    audit_bytes = audit_path.read_bytes()
    try:
        audit = json.loads(audit_bytes)
    except json.JSONDecodeError as exc:
        raise PaperStartGateError("sealed terminal audit is invalid JSON") from exc
    if not isinstance(audit, dict) or audit.get("overall_status") != "PASS":
        raise PaperStartGateError("reliability seal does not reference an explicit terminal PASS")
    audit_sha = _sha256(audit_path)
    identity_sha = _hash_json(validation_identity(audit))
    if (
        payload.get("schema_version") != 1
        or payload.get("gate") != "RELIABILITY_SEALED"
        or payload.get("terminal_verdict") != "PASS"
        or not _is_sha(payload.get("validation_identity_sha256"))
        or not _is_sha(payload.get("terminal_evidence_sha256"))
        or payload.get("terminal_audit_sha256") != audit_sha
        or payload.get("terminal_evidence_sha256") != audit_sha
        or payload.get("validation_identity_sha256") != identity_sha
        or payload.get("seal_sha256") != _hash_json(unsigned)
    ):
        raise PaperStartGateError("reliability seal does not bind a terminal PASS")
    return str(payload["seal_sha256"])


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PaperStartGateError(f"expected a JSON object in {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _is_sha(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
