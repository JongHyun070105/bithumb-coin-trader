from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from scripts.post30h_orchestrator import (
    Post30HError,
    _hash_json,
    _verify_dataset_qualification,
    _verify_integration_receipt,
)
import scripts.post30h_orchestrator as post30h_orchestrator


def _hash_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def test_dataset_qualification_binds_manifest_bytes_and_dq_report(tmp_path: Path) -> None:
    manifest_path = tmp_path / "dataset.json"
    manifest = {
        "schema_version": 1,
        "dataset_id": "qualified-public-data",
        "dataset_role": "DEVELOPMENT_EXPLORATORY",
        "allowed_for_candidate_selection": True,
        "integrity_status": "PASS",
        "provenance_confidence": "PROVEN",
        "data_path": "candles.csv",
        "data_sha256": "a" * 64,
        "candle_count": 10,
    }
    _write_json(manifest_path, manifest)
    dq_report_path = tmp_path / "dq-report.json"
    _write_json(dq_report_path, {"status": "PASS", "dataset_id": manifest["dataset_id"]})
    qualification = {
        "schema_version": 1,
        "status": "PASS",
        "dataset_id": manifest["dataset_id"],
        "dataset_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "data_sha256": manifest["data_sha256"],
        "dq_report_path": dq_report_path.name,
        "dq_report_sha256": hashlib.sha256(dq_report_path.read_bytes()).hexdigest(),
        "dataset_role": "DEVELOPMENT_EXPLORATORY",
        "provenance_confidence": "PROVEN",
    }
    qualification["qualification_sha256"] = _hash_json(qualification)
    qualification_path = tmp_path / "qualification.json"
    _write_json(qualification_path, qualification)

    result = _verify_dataset_qualification(manifest_path, qualification_path)
    assert result["dataset_id"] == manifest["dataset_id"]

    manifest["dataset_role"] = "FROZEN_HOLDOUT"
    _write_json(manifest_path, manifest)
    with pytest.raises(Post30HError, match="not a qualified development dataset"):
        _verify_dataset_qualification(manifest_path, qualification_path)


def test_integration_receipt_binds_current_clean_git_tree_and_test_report(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    (repo / "source.txt").write_text("integrated source\n", encoding="utf-8")
    subprocess.run(["git", "add", "source.txt"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "source"],
        cwd=repo, capture_output=True, check=True,
    )
    monkeypatch.setattr(post30h_orchestrator, "ROOT", repo)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    test_report = tmp_path / "focused-tests.json"
    test_report.write_text('{"status":"PASS"}\n', encoding="utf-8")
    payload = {
        "schema_version": 1,
        "status": "PASS",
        "base_commit": head,
        "integrated_commit": head,
        "integrated_tree": tree,
        "focused_test_report_path": test_report.name,
        "focused_test_report_sha256": hashlib.sha256(test_report.read_bytes()).hexdigest(),
    }
    payload["receipt_sha256"] = _hash_json(payload)
    receipt = tmp_path / "integration-receipt.json"
    _write_json(receipt, payload)

    assert _verify_integration_receipt(receipt)["integrated_commit"] == head

    test_report.write_text('{"status":"FAIL"}\n', encoding="utf-8")
    with pytest.raises(Post30HError, match="test report hash"):
        _verify_integration_receipt(receipt)
