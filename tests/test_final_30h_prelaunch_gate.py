from __future__ import annotations

from datetime import datetime, timezone

from scripts.final_30h_prelaunch_gate import REQUIRED_CHECKS, _check_readiness, _check_smoke


def _identity() -> dict[str, str]:
    return {
        "epoch": "aws-validation-observability-30h-20260928T120000Z-v1",
        "run_id": "aws-validation-observability-30h-run-20260928T120000Z-v1",
        "software_commit_sha": "1" * 40,
        "software_tree_sha": "2" * 40,
    }


def _readiness() -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    checks: dict[str, dict[str, object]] = {
        name: {"status": "PASS", "evidence_ref": f"read-only://evidence/{name}"}
        for name in REQUIRED_CHECKS
    }
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


def _smoke() -> dict[str, object]:
    digest = "a" * 64
    return {
        "status": "PASS",
        "evidence_ref": "read-only://evidence/smoke-run",
        "epoch": "aws-validation-witness-e2e-smoke-20260928T110000Z-v1",
        "run_id": "aws-validation-witness-e2e-smoke-run-20260928T110000Z-v1",
        "process_exit": 0,
        "witness_exists_local": True,
        "s3_key": "market-data/temporary/smoke/terminal-witness.json",
        "s3_uploaded": True,
        "s3_exact_object_exists": True,
        "local_witness_sha256": digest,
        "remote_witness_sha256": digest,
        "auditor_identity_check": "PASS",
        "auditor_witness_check": "PASS",
        "auditor_terminal_contract": "PASS",
    }


def test_readiness_requires_fresh_exact_identity_and_every_pass_check() -> None:
    assert _check_readiness(_readiness(), _identity()) == []


def test_readiness_rejects_missing_or_not_verifiable_observations() -> None:
    evidence = _readiness()
    checks = evidence["checks"]
    assert isinstance(checks, dict)
    checks["runtime_role_put_witness"]["status"] = "NOT_VERIFIABLE"
    del checks["disk_capacity"]
    failures = _check_readiness(evidence, _identity())
    assert any("runtime_role_put_witness" in failure for failure in failures)
    assert any("disk_capacity" in failure for failure in failures)


def test_readiness_rejects_reused_identity_and_stale_bundle() -> None:
    evidence = _readiness()
    checks = evidence["checks"]
    assert isinstance(checks, dict)
    checks["run_id_not_reused"]["run_id_exists"] = True
    evidence["captured_at_utc"] = "2026-01-01T00:00:00Z"
    failures = _check_readiness(evidence, _identity())
    assert any("run_id_exists=false" in failure for failure in failures)
    assert any("older than 15 minutes" in failure for failure in failures)


def test_smoke_requires_uploaded_exact_object_hash_parity_and_auditor_acceptance() -> None:
    assert _check_smoke(_smoke(), _identity()) == []
    smoke = _smoke()
    smoke["remote_witness_sha256"] = "b" * 64
    smoke["s3_uploaded"] = False
    failures = _check_smoke(smoke, _identity())
    assert any("local and remote witness SHA-256" in failure for failure in failures)
    assert any("s3_uploaded" in failure for failure in failures)


def test_smoke_identity_cannot_be_the_30h_identity() -> None:
    identity = _identity()
    smoke = _smoke()
    smoke["epoch"] = identity["epoch"]
    failures = _check_smoke(smoke, identity)
    assert any("must be separate" in failure for failure in failures)
