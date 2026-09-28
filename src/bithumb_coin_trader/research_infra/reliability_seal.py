"""Write-once local seal binding a terminal PASS to its exact audit bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ReliabilitySealError(ValueError):
    """Raised when terminal evidence cannot be safely sealed."""


def write_reliability_seal(terminal_audit: Path, output: Path) -> dict[str, Any]:
    audit_path = Path(terminal_audit)
    output_path = Path(output)
    if audit_path.is_symlink() or not audit_path.is_file():
        raise ReliabilitySealError("terminal audit must be a regular local file")
    if output_path.is_symlink() or output_path.exists():
        raise ReliabilitySealError("reliability seal output must be new and must not be a symlink")
    if audit_path.resolve().parent != output_path.resolve().parent:
        raise ReliabilitySealError("reliability seal must be beside its terminal audit report")
    try:
        audit_bytes = audit_path.read_bytes()
        report = json.loads(audit_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise ReliabilitySealError("terminal audit is unreadable JSON") from exc
    if not isinstance(report, dict) or report.get("overall_status") != "PASS":
        raise ReliabilitySealError("only an explicit terminal-audit PASS can be sealed")
    identity = _validation_identity(report)
    audit_sha256 = hashlib.sha256(audit_bytes).hexdigest()
    unsigned: dict[str, Any] = {
        "schema_version": 1,
        "gate": "RELIABILITY_SEALED",
        "terminal_verdict": "PASS",
        "validation_identity_sha256": _hash_json(identity),
        "terminal_evidence_sha256": audit_sha256,
        "terminal_audit_path": audit_path.name,
        "terminal_audit_sha256": audit_sha256,
    }
    seal = {**unsigned, "seal_sha256": _hash_json(unsigned)}
    encoded = json.dumps(seal, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    try:
        with output_path.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
    except FileExistsError as exc:
        raise ReliabilitySealError("reliability seal output already exists; refusing overwrite") from exc
    return seal


def validation_identity(report: dict[str, Any]) -> dict[str, str]:
    return _validation_identity(report)


def _validation_identity(report: dict[str, Any]) -> dict[str, str]:
    values = {
        "epoch": report.get("epoch"),
        "run_id": report.get("run_id"),
        "runtime_commit_expected": report.get("runtime_commit_expected"),
        "runtime_tree_expected": report.get("runtime_tree_expected"),
    }
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise ReliabilitySealError("terminal audit does not contain a complete validation identity")
    return {key: str(value) for key, value in values.items()}


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
