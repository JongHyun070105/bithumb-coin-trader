#!/usr/bin/env python3
"""Fail-closed local and evidence-bundle gate for a 30-hour validation.

This command validates sealed local artifacts and binds operator-collected,
read-only AWS/guest/smoke observations to that exact identity. It does not
contact AWS or start a runtime. Missing, stale, malformed, or non-PASS
observations fail the gate.

Readiness bundle schema is intentionally strict; see REQUIRED_CHECKS below and
the project preflight report for the fields required in its `checks` mapping.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
for directory in (ROOT, SRC_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from bithumb_coin_trader.launch_artifacts import (
    ValidationRunSpec,
    resolve_epoch_paths,
    validate_launch_artifacts,
)

REQUIRED_CHECKS = (
    "runtime_role_put_witness",
    "runtime_role_put_receipts",
    "runtime_role_read_required_objects",
    "auditor_get_exact_witness",
    "auditor_get_exact_receipt",
    "auditor_head_exact_objects",
    "disk_capacity",
    "inode_capacity",
    "memory_and_swap",
    "collector_processes_clear",
    "observer_processes_clear",
    "scheduler_processes_clear",
    "finalizer_processes_clear",
    "transient_units_clear",
    "stale_mounts_clear",
    "stale_validation_roots_clear",
    "runtime_commit_on_guest",
    "run_id_not_reused",
    "epoch_not_reused",
    "s3_prefix_not_reused",
    "local_evidence_dir_not_reused",
    "private_api_disabled",
    "paper_disabled",
    "live_disabled",
    "terminal_auditor_dry_run",
)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"{label} unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{label} must include a UTC timezone")
    return parsed.astimezone(timezone.utc)


def _check_readiness(evidence: dict[str, Any], identity: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    expected_binding = {
        "epoch": identity.get("epoch"),
        "run_id": identity.get("run_id"),
        "runtime_commit": identity.get("software_commit_sha"),
        "runtime_tree_sha": identity.get("software_tree_sha"),
    }
    for field, expected in expected_binding.items():
        if evidence.get(field) != expected:
            failures.append(f"readiness evidence {field} does not match sealed identity")

    try:
        captured = _parse_utc(evidence.get("captured_at_utc"), "captured_at_utc")
        age = (datetime.now(timezone.utc) - captured).total_seconds()
        if age < 0 or age > 900:
            failures.append("readiness evidence is future-dated or older than 15 minutes")
    except ValueError as exc:
        failures.append(str(exc))

    checks = evidence.get("checks")
    if not isinstance(checks, dict):
        return failures + ["readiness evidence checks must be an object"]
    for name in REQUIRED_CHECKS:
        check = checks.get(name)
        if not isinstance(check, dict):
            failures.append(f"missing readiness check: {name}")
            continue
        if check.get("status") != "PASS":
            failures.append(f"readiness check is not PASS: {name}")
        if not isinstance(check.get("evidence_ref"), str) or not check["evidence_ref"].strip():
            failures.append(f"readiness check has no evidence_ref: {name}")

    freshness = checks.get("run_id_not_reused", {})
    if isinstance(freshness, dict) and freshness.get("run_id_exists") is not False:
        failures.append("run_id_not_reused must explicitly attest run_id_exists=false")
    freshness = checks.get("epoch_not_reused", {})
    if isinstance(freshness, dict) and freshness.get("epoch_exists") is not False:
        failures.append("epoch_not_reused must explicitly attest epoch_exists=false")
    freshness = checks.get("s3_prefix_not_reused", {})
    if isinstance(freshness, dict) and freshness.get("prefix_exists") is not False:
        failures.append("s3_prefix_not_reused must explicitly attest prefix_exists=false")
    freshness = checks.get("local_evidence_dir_not_reused", {})
    if isinstance(freshness, dict) and freshness.get("path_exists") is not False:
        failures.append("local_evidence_dir_not_reused must explicitly attest path_exists=false")
    return failures


def _check_smoke(smoke: object, identity: dict[str, Any]) -> list[str]:
    if not isinstance(smoke, dict):
        return ["missing terminal_smoke evidence object"]
    failures: list[str] = []
    required = {
        "status": "PASS",
        "process_exit": 0,
        "witness_exists_local": True,
        "s3_uploaded": True,
        "s3_exact_object_exists": True,
        "auditor_identity_check": "PASS",
        "auditor_witness_check": "PASS",
        "auditor_terminal_contract": "PASS",
    }
    for key, expected in required.items():
        if smoke.get(key) != expected:
            failures.append(f"terminal smoke {key} must be {expected!r}")
    if not isinstance(smoke.get("evidence_ref"), str) or not smoke["evidence_ref"].strip():
        failures.append("terminal smoke has no evidence_ref")

    smoke_epoch = smoke.get("epoch")
    smoke_run_id = smoke.get("run_id")
    if not isinstance(smoke_epoch, str) or not smoke_epoch.startswith("aws-validation-witness-e2e-smoke-"):
        failures.append("terminal smoke epoch is not a dedicated witness-e2e-smoke identity")
    if not isinstance(smoke_run_id, str) or not smoke_run_id.startswith("aws-validation-witness-e2e-smoke-run-"):
        failures.append("terminal smoke run_id is not a dedicated witness-e2e-smoke identity")
    if smoke_epoch == identity.get("epoch") or smoke_run_id == identity.get("run_id"):
        failures.append("terminal smoke identity must be separate from the proposed 30H identity")
    if not isinstance(smoke.get("s3_key"), str) or not smoke["s3_key"].strip():
        failures.append("terminal smoke s3_key must be non-empty")
    local_hash = smoke.get("local_witness_sha256")
    remote_hash = smoke.get("remote_witness_sha256")
    if not isinstance(local_hash, str) or len(local_hash) != 64 or local_hash != remote_hash:
        failures.append("terminal smoke local and remote witness SHA-256 values must match")
    return failures


def evaluate_gate(repo_root: Path, artifacts_dir: Path, readiness_path: Path) -> list[str]:
    failures: list[str] = []
    try:
        identity = _read_json(artifacts_dir / "identity.json", "identity.json")
        command = _read_json(artifacts_dir / "launch-command.json", "launch-command.json")
        runtime_path = artifacts_dir / f"{identity['epoch']}.runtime.json"
        runtime = _read_json(runtime_path, "runtime config")
        authorization = _read_json(artifacts_dir / "authorization-evidence.json", "authorization-evidence.json")
        readiness = _read_json(readiness_path, "readiness evidence")
    except (KeyError, ValueError, OSError) as exc:
        return [str(exc)]

    try:
        spec = ValidationRunSpec(
            epoch=identity["epoch"],
            run_id=identity["run_id"],
            duration_seconds=identity["duration_seconds"],
            runtime_commit=identity["software_commit_sha"],
            software_tree_sha=identity.get("software_tree_sha"),
            target_full_hours=identity.get("target_full_hours", 1),
            planned_start_time=identity.get("planned_start_utc"),
            base_data_parent=Path(identity.get("data_root", "/var/lib/bitcoin-trader/90m-validation")).parent,
            runtime_worktree=Path(identity.get("runtime_worktree", "/var/lib/bitcoin-trader/runtime-worktrees/aws-observability-90m-20260917")),
            python_bin=Path(identity.get("python", "/var/lib/bitcoin-trader/venv-pre-soak/bin/python")),
            s3_bucket=identity.get("s3_bucket", ""),
            region=identity.get("region", identity.get("s3_region", "")),
        )
        validate_launch_artifacts(spec, runtime, command, artifacts_dir)
    except Exception as exc:
        failures.append(f"sealed launch artifacts failed validation: {exc}")

    if identity.get("duration_seconds") != 108000:
        failures.append("prelaunch gate requires an exact 108000-second 30H identity")
    if identity.get("software_tree_sha") in (None, "", "unspecified"):
        failures.append("sealed identity must include a concrete runtime tree SHA")
    if identity.get("private_api_enabled") is True:
        failures.append("private API must remain disabled")
    safety = identity.get("trading_safety", {})
    if safety.get("paper") != "NOT_STARTED" or safety.get("live") != "DISABLED" or safety.get("private_api") != "DISABLED":
        failures.append("sealed trading safety state must keep PAPER/LIVE/PRIVATE_API disabled")
    if runtime.get("private_api_enabled") is not False:
        failures.append("runtime config private_api_enabled must be false")
    if runtime.get("runtime_software_commit") != identity.get("software_commit_sha"):
        failures.append("runtime config commit does not match sealed identity")
    if runtime.get("region") != identity.get("region"):
        failures.append("runtime config region does not match sealed identity")

    witness = command.get("terminal_witness", {})
    if witness.get("epoch") != identity.get("epoch") or witness.get("run_id") != identity.get("run_id"):
        failures.append("ExecStopPost witness identity does not match the sealed run")
    if witness.get("allow_s3_write") is not True:
        failures.append("terminal witness S3 upload permission is not enabled")
    if not identity.get("s3_bucket") or not identity.get("s3_prefix") or not identity.get("s3_region"):
        failures.append("sealed identity is missing bucket, prefix, or region")
    try:
        resolved = resolve_epoch_paths(runtime, identity["epoch"])
        if identity.get("s3_prefix") != resolved["temporary_prefix"]:
            failures.append("sealed identity S3 prefix does not match the runtime template")
        if identity.get("s3_region") != runtime.get("region"):
            failures.append("sealed identity S3 region does not match runtime region")
        if witness.get("s3_bucket") != identity.get("s3_bucket") or witness.get("s3_prefix") != identity.get("s3_prefix") or witness.get("s3_region") != identity.get("s3_region"):
            failures.append("ExecStopPost S3 target does not match the sealed identity")
        supervisor = command.get("supervisor_command", [])
        if "--archive-scheduler-command-json" in supervisor:
            scheduler = json.loads(supervisor[supervisor.index("--archive-scheduler-command-json") + 1])
            if scheduler[scheduler.index("--s3-bucket") + 1] != identity.get("s3_bucket"):
                failures.append("archive scheduler bucket does not match the sealed identity")
            if scheduler[scheduler.index("--remote-prefix") + 1] != identity.get("s3_prefix"):
                failures.append("archive scheduler prefix does not match the sealed identity")
        observer = command.get("observer_command", [])
        if observer[observer.index("--s3-bucket") + 1] != identity.get("s3_bucket"):
            failures.append("observer bucket does not match the sealed identity")
        if observer[observer.index("--s3-prefix") + 1] != identity.get("s3_prefix"):
            failures.append("observer prefix does not match the sealed identity")
    except Exception as exc:
        failures.append(f"runtime S3 template validation failed: {exc}")

    try:
        head = _git(repo_root, "rev-parse", "HEAD")
        runtime_tree = _git(repo_root, "rev-parse", f"{identity['software_commit_sha']}^{{tree}}")
        dirty = _git(repo_root, "status", "--porcelain", "--untracked-files=all")
        if runtime_tree != identity.get("software_tree_sha"):
            failures.append("runtime commit tree SHA does not match sealed identity")
    except subprocess.CalledProcessError as exc:
        failures.append(f"git identity check failed: {exc}")
    except Exception as exc:
        failures.append(f"git identity check failed: {exc}")
    else:
        if dirty:
            failures.append("runtime checkout is dirty")
        try:
            subprocess.run(
                ["git", "-C", str(repo_root), "merge-base", "--is-ancestor", identity["software_commit_sha"], head],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            failures.append("runtime commit is not an ancestor of the current checkout")
        try:
            changed_runtime_paths = _git(repo_root, "diff", "--name-only", f"{identity['software_commit_sha']}..HEAD", "--", "src", "scripts", "infra", "pyproject.toml")
            if changed_runtime_paths:
                failures.append("runtime source changed after the proposed tested commit: " + changed_runtime_paths.replace("\n", ", "))
        except subprocess.CalledProcessError as exc:
            failures.append(f"runtime source differential check failed: {exc}")

    if authorization.get("epoch") != identity.get("epoch") or authorization.get("run_id") != identity.get("run_id") or authorization.get("runtime_commit") != identity.get("software_commit_sha"):
        failures.append("authorization evidence does not bind the sealed identity")
    if authorization.get("launch_authorized") is not True or authorization.get("actual_start_time_utc") is not None or authorization.get("status") != "AUTHORIZED_NOT_STARTED":
        failures.append("launch requires exact-identity GO authorization and an unstarted status")

    failures.extend(_check_readiness(readiness, identity))
    failures.extend(_check_smoke(readiness.get("terminal_smoke"), identity))
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed final 30H prelaunch gate")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--readiness-evidence", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        failures = evaluate_gate(args.repo_root, args.artifacts_dir, args.readiness_evidence)
    except Exception as exc:
        failures = [f"gate evaluation error: {exc}"]
    if failures:
        print("PRELAUNCH_GATE=FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PRELAUNCH_GATE=PASS")
    print("All sealed identity, authorization, smoke, source, and readiness checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
