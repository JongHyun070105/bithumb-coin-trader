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
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
GIB = 1024**3
MIN_DISK_RESERVE_BYTES = 50 * GIB
DISK_ESTIMATE_BUFFER_BYTES = 5 * GIB
PREFLIGHT_ONLY_PATHS = frozenset({"scripts/final_30h_prelaunch_gate.py"})
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
    "collector_identity_conflict_clear",
    "observer_identity_conflict_clear",
    "scheduler_identity_conflict_clear",
    "finalizer_identity_conflict_clear",
    "transient_unit_identity_conflict_clear",
    "identity_mount_conflict_clear",
    "identity_data_root_conflict_clear",
    "runtime_commit_on_guest",
    "runtime_full_suite",
    "changed_scope_pyright",
    "compileall",
    "shell_syntax",
    "git_diff_check",
    "target_full_hours_30",
    "artifact_determinism",
    "runtime_python_environment",
    "closed_at_utc_idempotency",
    "default_live_safe",
    "run_id_not_reused",
    "epoch_not_reused",
    "s3_prefix_not_reused",
    "local_evidence_dir_not_reused",
    "private_api_disabled",
    "paper_disabled",
    "live_disabled",
    "terminal_auditor_dry_run",
    "terminal_auditor_live_e2e",
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


def _check_evidence_file(check: dict[str, Any], evidence_root: Path, label: str) -> list[str]:
    failures: list[str] = []
    reference = check.get("evidence_ref")
    digest = check.get("evidence_sha256")
    if not isinstance(reference, str) or not reference.strip():
        return [f"{label} has no evidence_ref"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return [f"{label} evidence_sha256 must be a lowercase SHA-256 digest"]

    relative = Path(reference)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != reference:
        return [f"{label} evidence_ref must be a normalized path relative to the evidence directory"]
    root = evidence_root.resolve()
    candidate = root / relative
    if candidate.is_symlink():
        return [f"{label} evidence_ref must not be a symlink: {reference}"]
    path = candidate.resolve()
    if not path.is_relative_to(root):
        return [f"{label} evidence_ref escapes the evidence directory"]
    if not path.is_file():
        return [f"{label} evidence file is missing or not a regular file: {reference}"]
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != digest:
        failures.append(f"{label} evidence SHA-256 does not match: {reference}")
    return failures


def _check_readiness(
    evidence: dict[str, Any],
    identity: dict[str, Any],
    evidence_root: Path,
) -> list[str]:
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
        failures.extend(_check_evidence_file(check, evidence_root, f"readiness check {name}"))

    exact_suite = checks.get("runtime_full_suite", {})
    if isinstance(exact_suite, dict):
        if exact_suite.get("runtime_commit") != identity.get("software_commit_sha"):
            failures.append("runtime_full_suite must bind the tested runtime commit")
        if exact_suite.get("runtime_tree_sha") != identity.get("software_tree_sha"):
            failures.append("runtime_full_suite must bind the tested runtime tree")
        if not isinstance(exact_suite.get("passed_tests"), int) or exact_suite["passed_tests"] <= 0:
            failures.append("runtime_full_suite must report a positive passed_tests count")
        if not isinstance(exact_suite.get("skipped_tests"), int) or exact_suite["skipped_tests"] < 0:
            failures.append("runtime_full_suite must report a non-negative skipped_tests count")
        if exact_suite.get("failures") != 0:
            failures.append("runtime_full_suite must report failures=0")
    pyright = checks.get("changed_scope_pyright", {})
    if isinstance(pyright, dict):
        if pyright.get("runtime_commit") != identity.get("software_commit_sha"):
            failures.append("changed_scope_pyright must bind the tested runtime commit")
        if pyright.get("runtime_tree_sha") != identity.get("software_tree_sha"):
            failures.append("changed_scope_pyright must bind the tested runtime tree")
        if pyright.get("errors") != 0:
            failures.append("changed_scope_pyright must report errors=0")
        if pyright.get("warnings") != 0 or pyright.get("informations") != 0:
            failures.append("changed_scope_pyright must report zero warnings and informations")
    target_hours = checks.get("target_full_hours_30", {})
    if isinstance(target_hours, dict) and target_hours.get("target_full_hours") != 30:
        failures.append("target_full_hours_30 must explicitly attest target_full_hours=30")

    for name in (
        "collector_identity_conflict_clear",
        "observer_identity_conflict_clear",
        "scheduler_identity_conflict_clear",
        "finalizer_identity_conflict_clear",
        "transient_unit_identity_conflict_clear",
    ):
        check = checks.get(name, {})
        if isinstance(check, dict):
            if check.get("run_id") != identity.get("run_id") or check.get("epoch") != identity.get("epoch"):
                failures.append(f"{name} must be scoped to the exact sealed run_id and epoch")
            if check.get("conflicting_count") != 0:
                failures.append(f"{name} must report conflicting_count=0")

    disk = checks.get("disk_capacity", {})
    if isinstance(disk, dict):
        numeric_fields = (
            "free_bytes",
            "hourly_raw_peak_bytes",
            "nonraw_run_bytes",
            "run_hours",
            "minimum_reserve_bytes",
        )
        if any(type(disk.get(field)) is not int or disk[field] < 0 for field in numeric_fields):
            failures.append("disk_capacity must include non-negative integer sizing evidence")
        else:
            if disk["run_hours"] != 30:
                failures.append("disk_capacity must project exactly 30 run hours")
            if disk["minimum_reserve_bytes"] != MIN_DISK_RESERVE_BYTES:
                failures.append("disk_capacity must preserve the existing 50 GiB minimum reserve")
            projected = disk["hourly_raw_peak_bytes"] * 30 + disk["nonraw_run_bytes"]
            post_run_free = disk["free_bytes"] - projected
            required_free = MIN_DISK_RESERVE_BYTES + DISK_ESTIMATE_BUFFER_BYTES
            if post_run_free < required_free:
                failures.append(
                    "disk_capacity conservative projection leaves less than 50 GiB reserve plus 5 GiB estimate buffer "
                    f"(projected_free_bytes={post_run_free}, required={required_free})"
                )

    freshness = checks.get("run_id_not_reused", {})
    if isinstance(freshness, dict) and freshness.get("run_id_exists") is not False:
        failures.append("run_id_not_reused must explicitly attest run_id_exists=false")
    freshness = checks.get("epoch_not_reused", {})
    if isinstance(freshness, dict) and freshness.get("epoch_exists") is not False:
        failures.append("epoch_not_reused must explicitly attest epoch_exists=false")
    freshness = checks.get("s3_prefix_not_reused", {})
    if isinstance(freshness, dict) and freshness.get("prefix_exists") is not False:
        failures.append("s3_prefix_not_reused must explicitly attest prefix_exists=false")
    if isinstance(freshness, dict):
        if freshness.get("bucket") != identity.get("s3_bucket") or freshness.get("prefix") != identity.get("s3_prefix"):
            failures.append("s3_prefix_not_reused must bind the exact sealed bucket and prefix")
        if not isinstance(freshness.get("checked_by"), str) or not freshness["checked_by"].strip():
            failures.append("s3_prefix_not_reused must identify the principal that checked the prefix")
    freshness = checks.get("local_evidence_dir_not_reused", {})
    if isinstance(freshness, dict) and freshness.get("path_exists") is not False:
        failures.append("local_evidence_dir_not_reused must explicitly attest path_exists=false")
    return failures


def _check_smoke(smoke: object, identity: dict[str, Any], evidence_root: Path) -> list[str]:
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
    failures.extend(_check_evidence_file(smoke, evidence_root, "terminal smoke"))

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
    smoke_prefix = smoke.get("s3_prefix")
    if not isinstance(smoke_prefix, str) or not smoke_prefix.strip() or smoke.get("s3_key") != f"{smoke_prefix.rstrip('/')}/terminal/terminal-receipt.json":
        failures.append("terminal smoke S3 key must be the exact terminal receipt key under its prefix")
    if not isinstance(smoke.get("s3_bucket"), str) or not smoke["s3_bucket"].strip():
        failures.append("terminal smoke must bind an exact S3 bucket")
    if not isinstance(smoke.get("s3_region"), str) or not smoke["s3_region"].strip():
        failures.append("terminal smoke must bind an exact S3 region")
    local_hash = smoke.get("local_witness_sha256")
    remote_hash = smoke.get("remote_witness_sha256")
    if (
        not isinstance(local_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", local_hash) is None
        or local_hash != remote_hash
    ):
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
        target_full_hours = identity.get("target_full_hours")
        if type(target_full_hours) is not int:
            failures.append("sealed identity must include integer target_full_hours=30")
            target_full_hours = 0
        spec = ValidationRunSpec(
            epoch=identity["epoch"],
            run_id=identity["run_id"],
            duration_seconds=identity["duration_seconds"],
            runtime_commit=identity["software_commit_sha"],
            software_tree_sha=identity.get("software_tree_sha"),
            target_full_hours=target_full_hours,
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
    if identity.get("target_full_hours") != 30:
        failures.append("prelaunch gate requires target_full_hours=30 explicitly")
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
            changed_runtime_paths = "\n".join(
                path for path in changed_runtime_paths.splitlines() if path not in PREFLIGHT_ONLY_PATHS
            )
            if changed_runtime_paths:
                failures.append("runtime source changed after the proposed tested commit: " + changed_runtime_paths.replace("\n", ", "))
        except subprocess.CalledProcessError as exc:
            failures.append(f"runtime source differential check failed: {exc}")

    if authorization.get("epoch") != identity.get("epoch") or authorization.get("run_id") != identity.get("run_id") or authorization.get("runtime_commit") != identity.get("software_commit_sha"):
        failures.append("authorization evidence does not bind the sealed identity")
    if authorization.get("launch_authorized") is not False or authorization.get("actual_start_time_utc") is not None or authorization.get("status") != "PREPARED_NOT_AUTHORIZED":
        failures.append("prelaunch readiness must remain unstarted and unauthorized pending separate exact-identity GO")

    failures.extend(_check_readiness(readiness, identity, readiness_path.parent))
    failures.extend(_check_smoke(readiness.get("terminal_smoke"), identity, readiness_path.parent))
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed readiness gate; it never authorizes or starts a 30H run")
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
    print("Technical readiness evidence passed; separate exact-identity human GO is still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
