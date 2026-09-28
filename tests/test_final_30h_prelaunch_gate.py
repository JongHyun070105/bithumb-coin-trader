from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path

from scripts.final_30h_prelaunch_gate import REQUIRED_CHECKS, _check_readiness, _check_smoke


def _identity() -> dict[str, object]:
    return {
        "epoch": "aws-validation-observability-30h-20260928T120000Z-v1",
        "run_id": "aws-validation-observability-30h-run-20260928T120000Z-v1",
        "software_commit_sha": "1" * 40,
        "software_tree_sha": "2" * 40,
        "target_full_hours": 30,
        "s3_bucket": "research-test-bucket",
        "s3_prefix": "market-data/temporary/aws-validation-test",
    }


def _evidence_ref(root: Path, name: str) -> dict[str, str]:
    path = root / "evidence" / f"{name}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"verified evidence for {name}\n".encode()
    path.write_bytes(payload)
    return {
        "evidence_ref": f"evidence/{name}.txt",
        "evidence_sha256": hashlib.sha256(payload).hexdigest(),
    }


def _readiness(root: Path) -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    checks: dict[str, dict[str, object]] = {
        name: {"status": "PASS", **_evidence_ref(root, name)}
        for name in REQUIRED_CHECKS
    }
    for name in (
        "collector_identity_conflict_clear",
        "observer_identity_conflict_clear",
        "scheduler_identity_conflict_clear",
        "finalizer_identity_conflict_clear",
        "transient_unit_identity_conflict_clear",
    ):
        checks[name].update({"run_id": _identity()["run_id"], "epoch": _identity()["epoch"], "conflicting_count": 0})
    checks["runtime_full_suite"].update({
        "runtime_commit": _identity()["software_commit_sha"],
        "runtime_tree_sha": _identity()["software_tree_sha"],
        "passed_tests": 1704,
        "skipped_tests": 2,
        "failures": 0,
    })
    checks["changed_scope_pyright"].update({
        "runtime_commit": _identity()["software_commit_sha"],
        "runtime_tree_sha": _identity()["software_tree_sha"],
        "errors": 0,
        "warnings": 0,
        "informations": 0,
    })
    checks["disk_capacity"].update({
        "free_bytes": 100 * 1024**3,
        "hourly_raw_peak_bytes": 1024**3,
        "nonraw_run_bytes": 5 * 1024**3,
        "run_hours": 30,
        "minimum_reserve_bytes": 50 * 1024**3,
    })
    checks["target_full_hours_30"]["target_full_hours"] = 30
    checks["s3_prefix_not_reused"].update({
        "bucket": _identity()["s3_bucket"],
        "prefix": _identity()["s3_prefix"],
        "checked_by": "arn:aws:iam::123456789012:role/read-only-provisioner",
    })
    checks["run_id_not_reused"]["run_id_exists"] = False
    checks["epoch_not_reused"]["epoch_exists"] = False
    checks["s3_prefix_not_reused"]["prefix_exists"] = False
    checks["local_evidence_dir_not_reused"]["path_exists"] = False
    return {
        "epoch": _identity()["epoch"],
        "run_id": _identity()["run_id"],
        "runtime_commit": _identity()["software_commit_sha"],
        "runtime_tree_sha": _identity()["software_tree_sha"],
        "captured_at_utc": now,
        "checks": checks,
    }


def _smoke(root: Path) -> dict[str, object]:
    digest = "a" * 64
    return {
        "status": "PASS",
        **_evidence_ref(root, "smoke-run"),
        "epoch": "aws-validation-witness-e2e-smoke-20260928T110000Z-v1",
        "run_id": "aws-validation-witness-e2e-smoke-run-20260928T110000Z-v1",
        "process_exit": 0,
        "witness_exists_local": True,
        "s3_bucket": "research-test-bucket",
        "s3_region": "ap-northeast-2",
        "s3_prefix": "market-data/temporary/aws-validation-witness-e2e-smoke-20260928T110000Z-v1",
        "s3_key": "market-data/temporary/aws-validation-witness-e2e-smoke-20260928T110000Z-v1/terminal/terminal-receipt.json",
        "s3_uploaded": True,
        "s3_exact_object_exists": True,
        "local_witness_sha256": digest,
        "remote_witness_sha256": digest,
        "auditor_identity_check": "PASS",
        "auditor_witness_check": "PASS",
        "auditor_terminal_contract": "PASS",
    }


def test_readiness_requires_fresh_exact_identity_and_every_pass_check(tmp_path: Path) -> None:
    assert _check_readiness(_readiness(tmp_path), _identity(), tmp_path) == []


def test_readiness_rejects_missing_or_not_verifiable_observations(tmp_path: Path) -> None:
    evidence = _readiness(tmp_path)
    checks = evidence["checks"]
    assert isinstance(checks, dict)
    checks["runtime_role_put_witness"]["status"] = "NOT_VERIFIABLE"
    del checks["disk_capacity"]
    failures = _check_readiness(evidence, _identity(), tmp_path)
    assert any("runtime_role_put_witness" in failure for failure in failures)
    assert any("disk_capacity" in failure for failure in failures)


def test_readiness_rejects_reused_identity_and_stale_bundle(tmp_path: Path) -> None:
    evidence = _readiness(tmp_path)
    checks = evidence["checks"]
    assert isinstance(checks, dict)
    checks["run_id_not_reused"]["run_id_exists"] = True
    evidence["captured_at_utc"] = "2026-01-01T00:00:00Z"
    failures = _check_readiness(evidence, _identity(), tmp_path)
    assert any("run_id_exists=false" in failure for failure in failures)
    assert any("older than 15 minutes" in failure for failure in failures)


def test_readiness_verifies_evidence_hashes_and_exact_runtime_tests(tmp_path: Path) -> None:
    evidence = _readiness(tmp_path)
    checks = evidence["checks"]
    assert isinstance(checks, dict)
    checks["runtime_full_suite"]["runtime_commit"] = "f" * 40
    failures = _check_readiness(evidence, _identity(), tmp_path)
    assert any("runtime_full_suite must bind" in failure for failure in failures)

    check = checks["disk_capacity"]
    assert isinstance(check, dict)
    ref = tmp_path / str(check["evidence_ref"])
    ref.write_text("changed after bundle creation\n", encoding="utf-8")
    failures = _check_readiness(evidence, _identity(), tmp_path)
    assert any("evidence SHA-256 does not match" in failure for failure in failures)


def test_readiness_rejects_unscoped_processes_and_s3_collision_claims(tmp_path: Path) -> None:
    evidence = _readiness(tmp_path)
    checks = evidence["checks"]
    assert isinstance(checks, dict)
    checks["observer_identity_conflict_clear"]["conflicting_count"] = 3
    checks["s3_prefix_not_reused"]["prefix"] = "market-data/temporary/other"
    failures = _check_readiness(evidence, _identity(), tmp_path)
    assert any("conflicting_count=0" in failure for failure in failures)
    assert any("exact sealed bucket and prefix" in failure for failure in failures)


def test_readiness_rejects_disk_projection_without_conservative_margin(tmp_path: Path) -> None:
    evidence = _readiness(tmp_path)
    checks = evidence["checks"]
    assert isinstance(checks, dict)
    checks["disk_capacity"].update({
        "free_bytes": 95_822_610_432,
        "hourly_raw_peak_bytes": 1_217_418_119,
        "nonraw_run_bytes": 5_454_994_823,
    })
    failures = _check_readiness(evidence, _identity(), tmp_path)
    assert any("less than 50 GiB reserve plus 5 GiB estimate buffer" in failure for failure in failures)


def test_smoke_requires_uploaded_exact_object_hash_parity_and_auditor_acceptance(tmp_path: Path) -> None:
    assert _check_smoke(_smoke(tmp_path), _identity(), tmp_path) == []
    smoke = _smoke(tmp_path)
    smoke["remote_witness_sha256"] = "b" * 64
    smoke["s3_uploaded"] = False
    failures = _check_smoke(smoke, _identity(), tmp_path)
    assert any("local and remote witness SHA-256" in failure for failure in failures)
    assert any("s3_uploaded" in failure for failure in failures)


def test_smoke_identity_cannot_be_the_30h_identity(tmp_path: Path) -> None:
    identity = _identity()
    smoke = _smoke(tmp_path)
    smoke["epoch"] = identity["epoch"]
    failures = _check_smoke(smoke, identity, tmp_path)
    assert any("must be separate" in failure for failure in failures)


def test_smoke_rejects_a_non_exact_receipt_key_and_unhashed_reference(tmp_path: Path) -> None:
    smoke = _smoke(tmp_path)
    smoke["s3_key"] = "market-data/temporary/other/receipt.json"
    smoke["evidence_ref"] = "../outside.json"
    failures = _check_smoke(smoke, _identity(), tmp_path)
    assert any("exact terminal receipt key" in failure for failure in failures)
    assert any("normalized path relative" in failure for failure in failures)
