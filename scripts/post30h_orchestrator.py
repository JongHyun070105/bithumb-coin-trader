#!/usr/bin/env python3
"""Fail-stop local post-30H pipeline; never connects to AWS or starts PAPER.

This runner launches only repository-owned offline commands. Reliability source
integration and dataset qualification remain explicit reviewer-produced
attestations; this script verifies their bindings and never merges or promotes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
CLI_MODULE = "bithumb_coin_trader.research_infra.cli"
_EXPERIMENT_ID = re.compile(r"^exp_[0-9a-f]{64}$")


class Post30HError(ValueError):
    """Raised when a post-run stage cannot safely proceed."""


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.dry_run:
        for stage in (
            "terminal-audit", "reliability-seal", "verify-integration",
            "verify-dataset-qualification", "research-batch", "candidate-report",
            "candidate-freeze", "paper-readiness",
        ):
            print(f"PLAN: {stage}")
        print("PAPER_START: NEVER INVOKED")
        return 0

    try:
        return _run(args)
    except (OSError, Post30HError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        print("PAPER: NOT_STARTED", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:
    audit_output = Path(args.audit_output_dir).resolve()
    if audit_output.exists() or audit_output.is_symlink():
        raise Post30HError("audit output directory must be new to preserve prior reports")
    audit_output.mkdir(parents=True)
    _stage("terminal-audit")
    result = _run_command([
        sys.executable,
        str(ROOT / "scripts" / "audit_fresh_30h_v3_terminal.py"),
        "--evidence-dir", str(Path(args.offline_bundle).resolve()),
        "--output-dir", str(audit_output),
    ])
    if result != 0:
        return result
    audits = list(audit_output.glob("fresh-30h-v3-*/terminal-audit.json"))
    if len(audits) != 1:
        raise Post30HError("terminal auditor did not produce exactly one report")
    audit_path = audits[0]
    seal_path = audit_path.parent / "reliability-seal.json"

    _stage("reliability-seal")
    result = _run_command([
        sys.executable, "-m", CLI_MODULE, "reliability-seal",
        "--terminal-audit", str(audit_path), "--output", str(seal_path),
    ])
    if result != 0:
        return result

    _stage("integrate")
    _verify_integration_receipt(Path(args.integration_receipt))
    _stage("qualify-dataset")
    manifest = _verify_dataset_qualification(
        Path(args.dataset_manifest), Path(args.dataset_qualification)
    )

    _stage("research-batch")
    batch_output = Path(args.research_output).resolve()
    result = _run_command([
        sys.executable, "-m", CLI_MODULE, "research-batch",
        "--dataset-manifest", str(Path(args.dataset_manifest).resolve()),
        "--hypotheses", str(Path(args.hypotheses).resolve()),
        "--cost-grid", args.cost_grid,
        "--walk-forward",
        "--folds", str(args.folds),
        "--window-mode", args.window_mode,
        "--purge-seconds", str(args.purge_seconds),
        "--embargo-seconds", str(args.embargo_seconds),
        "--output", str(batch_output),
        "--definition-registry", str(batch_output / "definition-registry.jsonl"),
    ] + (["--retry-failed"] if args.retry_failed else []))
    if result != 0:
        return result

    _stage("candidate-report")
    report_path = Path(args.candidate_report).resolve()
    report = _write_candidate_report(batch_output, args.candidate_experiment, report_path, manifest)
    print(f"Candidate report: {report_path} ({report['status']})")

    _stage("candidate-freeze")
    result = _run_command([
        sys.executable, "-m", CLI_MODULE, "candidate-freeze",
        "--experiment", args.candidate_experiment,
        "--candidate-id", args.candidate_id,
        "--research-root", str(batch_output),
        "--candidate-registry", str(Path(args.candidate_registry).resolve()),
        "--output", str(Path(args.candidate_freeze).resolve()),
    ])
    if result != 0:
        return result

    _stage("paper-readiness")
    return _run_command([
        sys.executable, "-m", CLI_MODULE, "paper-readiness",
        "--evidence-dir", str(Path(args.paper_evidence_dir).resolve()),
        "--output-dir", str(Path(args.paper_readiness_output).resolve()),
    ])


def _verify_integration_receipt(path: Path) -> dict[str, Any]:
    payload = _read_json_file(path, "integration receipt")
    fields = {
        "schema_version", "status", "base_commit", "integrated_commit",
        "integrated_tree", "focused_test_report_path", "focused_test_report_sha256", "receipt_sha256",
    }
    if set(payload) != fields or payload.get("schema_version") != 1 or payload.get("status") != "PASS":
        raise Post30HError("integration receipt is missing, malformed, or not PASS")
    unsigned = {key: value for key, value in payload.items() if key != "receipt_sha256"}
    if payload.get("receipt_sha256") != _hash_json(unsigned):
        raise Post30HError("integration receipt content hash is invalid")
    for key in ("base_commit", "integrated_commit"):
        if not _is_hex(payload.get(key), 40):
            raise Post30HError(f"integration receipt {key} must be a full Git commit")
    if not _is_hex(payload.get("integrated_tree"), 40):
        raise Post30HError("integration receipt integrated_tree must be a full Git tree hash")
    if not _is_hex(payload.get("focused_test_report_sha256"), 64):
        raise Post30HError("integration receipt focused_test_report_sha256 must be SHA-256")
    report_name = payload.get("focused_test_report_path")
    report_path = path.parent / report_name if isinstance(report_name, str) and Path(report_name).name == report_name else None
    if report_path is None or report_path.is_symlink() or not report_path.is_file():
        raise Post30HError("focused test report is missing or outside the integration receipt directory")
    if _sha256_file(report_path) != payload["focused_test_report_sha256"]:
        raise Post30HError("focused test report hash does not match the integration receipt")
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=10,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=10,
    )
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", str(payload["integrated_commit"]), "HEAD"],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=10,
    )
    if status.returncode != 0 or status.stdout.strip() or head.returncode != 0:
        raise Post30HError("source integration requires a clean committed checkout")
    if ancestor.returncode != 0:
        raise Post30HError("integrated source commit is not an ancestor of the current checkout")
    base_ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", str(payload["base_commit"]), str(payload["integrated_commit"])],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=10,
    )
    tree = subprocess.run(
        ["git", "rev-parse", f"{payload['integrated_commit']}^{{tree}}"],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=10,
    )
    if base_ancestor.returncode != 0:
        raise Post30HError("integration receipt base commit is not an ancestor of the integrated commit")
    if tree.returncode != 0 or tree.stdout.strip() != payload["integrated_tree"]:
        raise Post30HError("integration receipt Git tree does not match the integrated commit")
    return payload


def _verify_dataset_qualification(manifest_path: Path, qualification_path: Path) -> dict[str, Any]:
    manifest = _read_json_file(manifest_path, "dataset manifest")
    qualification = _read_json_file(qualification_path, "dataset qualification")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("dataset_role") != "DEVELOPMENT_EXPLORATORY"
        or manifest.get("allowed_for_candidate_selection") is not True
        or manifest.get("integrity_status") != "PASS"
        or manifest.get("provenance_confidence") != "PROVEN"
    ):
        raise Post30HError("post-30H research dataset is not a qualified development dataset")
    fields = {
        "schema_version", "status", "dataset_id", "dataset_manifest_sha256",
        "data_sha256", "dq_report_path", "dq_report_sha256", "dataset_role", "provenance_confidence",
        "qualification_sha256",
    }
    if (
        set(qualification) != fields
        or qualification.get("schema_version") != 1
        or qualification.get("status") != "PASS"
        or qualification.get("dataset_id") != manifest.get("dataset_id")
        or qualification.get("dataset_role") != "DEVELOPMENT_EXPLORATORY"
        or qualification.get("provenance_confidence") != "PROVEN"
        or qualification.get("data_sha256") != manifest.get("data_sha256")
        or qualification.get("dataset_manifest_sha256") != _sha256_file(manifest_path)
    ):
        raise Post30HError("dataset qualification does not bind the manifest and a PASS DQ report")
    dq_name = qualification.get("dq_report_path")
    dq_path = qualification_path.parent / dq_name if isinstance(dq_name, str) and Path(dq_name).name == dq_name else None
    if dq_path is None or dq_path.is_symlink() or not dq_path.is_file():
        raise Post30HError("qualified DQ report is missing or outside the qualification directory")
    if not _is_hex(qualification.get("dq_report_sha256"), 64) or _sha256_file(dq_path) != qualification["dq_report_sha256"]:
        raise Post30HError("qualified DQ report hash does not match its evidence bytes")
    unsigned = {key: value for key, value in qualification.items() if key != "qualification_sha256"}
    if qualification.get("qualification_sha256") != _hash_json(unsigned):
        raise Post30HError("dataset qualification content hash is invalid")
    return manifest


def _write_candidate_report(
    research_root: Path,
    experiment_id: str,
    output_path: Path,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    if _EXPERIMENT_ID.fullmatch(experiment_id) is None:
        raise Post30HError("candidate experiment must be a full content-derived exp_<sha256> id")
    matches = list(research_root.glob(f"batches/*/runs/{experiment_id}/attempt-*/complete.json"))
    if len(matches) != 1:
        raise Post30HError(f"candidate experiment must have one complete attempt; found {len(matches)}")
    attempt_dir = matches[0].parent
    completion = _read_json_file(matches[0], "experiment completion")
    metrics_path = attempt_dir / "metrics.json"
    metrics = _read_json_file(metrics_path, "experiment metrics")
    if (
        completion.get("experiment_id") != experiment_id
        or completion.get("metrics_sha256") != _sha256_file(metrics_path)
        or completion.get("unsupported_fold_count") != 0
        or metrics.get("dataset_id") != manifest.get("dataset_id")
    ):
        raise Post30HError("candidate report inputs are incomplete or do not match qualified data")
    batch_root = attempt_dir.parents[2]
    aggregate_path = batch_root / "aggregate_report.json"
    aggregate = _read_json_file(aggregate_path, "batch aggregate report")
    comparisons = [
        row for row in aggregate.get("baseline_comparisons", [])
        if isinstance(row, dict) and row.get("candidate_experiment_id") == experiment_id
    ]
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "EVIDENCE_ONLY_NO_SELECTION",
        "experiment_id": experiment_id,
        "candidate_family": metrics.get("candidate_family"),
        "strategy_id": metrics.get("strategy_id"),
        "dataset_id": manifest.get("dataset_id"),
        "dataset_sha256": manifest.get("data_sha256"),
        "attempt_manifest_sha256": _sha256_file(attempt_dir / "manifest.json"),
        "metrics_sha256": _sha256_file(metrics_path),
        "aggregate_report_sha256": _sha256_file(aggregate_path),
        "fold_results": metrics.get("folds"),
        "baseline_comparisons": comparisons,
    }
    report["report_sha256"] = _hash_json(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.is_symlink():
        raise Post30HError("candidate report output must not be a symlink")
    try:
        with output_path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, sort_keys=True, indent=2) + "\n")
    except FileExistsError as exc:
        raise Post30HError("candidate report output already exists; refusing overwrite") from exc
    return report


def _run_command(command: Sequence[str]) -> int:
    completed = subprocess.run(
        list(command), cwd=ROOT, capture_output=True, text=True, check=False, env=_python_env(),
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        print(f"STOP: command exited {completed.returncode}", file=sys.stderr)
    return completed.returncode


def _python_env() -> dict[str, str]:
    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(ROOT / "src") + (os.pathsep + existing if existing else "")
    return environment


def _stage(name: str) -> None:
    print(f"STAGE: {name}", flush=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline-bundle", required=True)
    parser.add_argument("--audit-output-dir", required=True)
    parser.add_argument("--integration-receipt", required=True)
    parser.add_argument("--dataset-manifest", required=True)
    parser.add_argument("--dataset-qualification", required=True)
    parser.add_argument("--hypotheses", required=True)
    parser.add_argument("--cost-grid", required=True)
    parser.add_argument("--folds", required=True, type=int)
    parser.add_argument("--window-mode", required=True, choices=("ROLLING", "EXPANDING"))
    parser.add_argument("--purge-seconds", required=True, type=float)
    parser.add_argument("--embargo-seconds", required=True, type=float)
    parser.add_argument("--research-output", required=True)
    parser.add_argument("--candidate-experiment", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--candidate-registry", required=True)
    parser.add_argument("--candidate-report", required=True)
    parser.add_argument("--candidate-freeze", required=True)
    parser.add_argument("--paper-evidence-dir", required=True)
    parser.add_argument("--paper-readiness-output", required=True)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _read_json_file(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise Post30HError(f"{label} is missing or is a symlink: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Post30HError(f"{label} is invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise Post30HError(f"{label} must be a JSON object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _is_hex(value: Any, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)


if __name__ == "__main__":
    raise SystemExit(main())
