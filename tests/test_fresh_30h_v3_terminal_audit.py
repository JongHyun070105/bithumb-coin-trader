from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.audit_fresh_30h_v3_terminal import (
    EXPECTED_RUNTIME_COMMIT,
    EXPECTED_RUNTIME_TREE,
    FAIL,
    NOT_VERIFIABLE,
    PASS,
    Fresh30HTerminalAuditor,
    _write_outputs,
)


COHORT = "2026-09-27_00"
EPOCH = "fresh-30h-v3-test"
RUN_ID = "fresh-30h-v3-run-test"


def _json(path: Path, payload: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    path.write_bytes(data)
    return data


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_bundle(root: Path) -> None:
    feed_universe = [
        {"exchange": "bithumb", "stream": "trade", "market": "KRW-BTC"},
        {"exchange": "bithumb", "stream": "orderbook", "market": "KRW-BTC"},
    ]
    _json(
        root / "audit-bundle.json",
        {
            "schema_version": 1,
            "paths": {
                "identity": "sealed/identity.json",
                "runtime": "sealed/runtime.json",
                "actual_start": "terminal/actual-start.json",
                "result": "terminal/result.json",
                "systemd": "terminal/systemd.json",
                "terminal_witness": "terminal/terminal-witness.json",
                "collector_lifecycle": "terminal/collector-lifecycle.json",
                "collector_metrics": "terminal/collector-metrics.json",
                "redundancy_metrics": "terminal/redundancy-metrics.json",
                "receipt_observations": "terminal/receipt-observations.json",
                "finalization_trace": "terminal/finalization-trace.json",
                "evidence_hash_index": "terminal/evidence-hash-index.json",
            },
            "directories": {
                "local_receipts": "terminal/archive-receipts",
                "s3_receipts": "terminal/s3-receipts",
            },
        },
    )
    _json(
        root / "sealed/identity.json",
        {
            "epoch": EPOCH,
            "run_id": RUN_ID,
            "software_commit_sha": EXPECTED_RUNTIME_COMMIT,
            "software_tree_sha": EXPECTED_RUNTIME_TREE,
            "duration_seconds": 3600,
            "feed_count": len(feed_universe),
            "qualifying_cohorts": [COHORT],
            "feed_universe": feed_universe,
        },
    )
    _json(
        root / "sealed/runtime.json",
        {
            "runtime_software_commit": EXPECTED_RUNTIME_COMMIT,
            "software_tree_sha": EXPECTED_RUNTIME_TREE,
            "duration_seconds": 3600,
            "schedule": {"qualifying_cohorts": [COHORT]},
            "bithumb_redundancy_enabled": True,
            "bithumb_connection_count": 2,
        },
    )
    _json(
        root / "terminal/actual-start.json",
        {"actual_start_time_utc": "2026-09-27T00:00:00Z"},
    )
    _json(
        root / "terminal/result.json",
        {
            "run_id": RUN_ID,
            "epoch": EPOCH,
            "started_at": "2026-09-27T00:00:00Z",
            "ended_at": "2026-09-27T01:00:00Z",
            "elapsed_seconds": 3600,
            "full_duration_satisfied": True,
            "collector_exit_code": 0,
            "archive_scheduler_exit_code": 0,
            "archive_scheduler_started": True,
            "publisher_exit_code": 0,
            "publisher_started": True,
        },
    )
    _json(
        root / "terminal/systemd.json",
        {"Result": "success", "NRestarts": 0, "ExecMainStatus": 0},
    )
    _json(
        root / "terminal/terminal-witness.json",
        {
            "run_id": RUN_ID,
            "epoch": EPOCH,
            "terminal_classification": "CLEAN_SUCCESS",
            "service_result": "success",
            "exit_status": "0",
            "s3_uploaded": True,
        },
    )
    _json(
        root / "terminal/collector-lifecycle.json",
        {"phase": "COMPLETE", "final_manifest_flush_observed": True},
    )
    _json(
        root / "terminal/collector-metrics.json",
        {
            "queue_size": 0,
            "unpersisted_event_count": 0,
            "bithumb_redundancy_enabled": True,
            "bithumb_connection_count": 2,
            "exchanges": {
                "bithumb": {
                    "writer_errors": 0,
                    "queue_dropped_events": 0,
                    "queue_backpressure_events": 3,
                    "conflicting_duplicate_frames": 0,
                    "deduplicated_frames": 5,
                },
                "binance": {
                    "writer_errors": 0,
                    "queue_dropped_events": 0,
                    "queue_backpressure_events": 0,
                },
                "upbit": {
                    "writer_errors": 0,
                    "queue_dropped_events": 0,
                    "queue_backpressure_events": 0,
                },
            },
        },
    )
    _json(root / "terminal/redundancy-metrics.json", {})

    local = root / "terminal/archive-receipts"
    s3 = root / "terminal/s3-receipts"
    _json(
        local / f"cohort_{COHORT}_finalized.json",
        {"cohort": COHORT, "status": "PASS", "total_slots": 2, "failed_count": 0},
    )
    _json(
        local / "coverage" / COHORT / "bithumb" / "trade" / "KRW-BTC.coverage.json.archive-receipt.json",
        {
            "cohort": COHORT,
            "exchange": "bithumb",
            "stream": "trade",
            "market": "KRW-BTC",
            "state": "RESTORE_VERIFIED",
        },
    )
    _json(
        local / "coverage" / COHORT / "bithumb" / "orderbook" / "KRW-BTC.coverage.json.archive-receipt.json",
        {
            "cohort": COHORT,
            "exchange": "bithumb",
            "stream": "orderbook",
            "market": "KRW-BTC",
            "state": "RESTORE_VERIFIED",
        },
    )
    for path in local.rglob("*.json"):
        rel = path.relative_to(local)
        (s3 / rel).parent.mkdir(parents=True, exist_ok=True)
        (s3 / rel).write_bytes(path.read_bytes())

    observations = []
    for path in local.rglob("*.json"):
        rel = path.relative_to(local).as_posix()
        digest = _sha256(path.read_bytes())
        observations.append(
            {
                "receipt_path": rel,
                "receipt_sha256_t1": digest,
                "receipt_sha256_t2": digest,
            }
        )
    _json(
        root / "terminal/receipt-observations.json",
        {"qualifying_receipts": observations},
    )
    _json(root / "terminal/finalization-trace.json", {"complete": True, "events": []})
    indexed = root / "terminal/collector-lifecycle.json"
    _json(
        root / "terminal/evidence-hash-index.json",
        {
            "files": [
                {
                    "path": "terminal/collector-lifecycle.json",
                    "sha256": _sha256(indexed.read_bytes()),
                }
            ]
        },
    )


def _check(report: dict, name: str) -> dict:
    return next(item for item in report["checks"] if item["name"] == name)


def test_terminal_audit_passes_synthetic_bundle_without_writing_to_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "bundle"
    _make_bundle(evidence)
    before = {
        path.relative_to(evidence): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in evidence.rglob("*")
        if path.is_file()
    }

    report = Fresh30HTerminalAuditor(evidence).audit()
    assert report["overall_status"] == PASS
    assert _check(report, "queue_persistence_and_writer")["details"]["backpressure_events"] == 3
    json_path, markdown_path = _write_outputs(report, tmp_path / "outputs", evidence)

    assert json_path.is_file()
    assert markdown_path.is_file()
    after = {
        path.relative_to(evidence): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in evidence.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_missing_systemd_evidence_is_not_verifiable(tmp_path: Path) -> None:
    evidence = tmp_path / "bundle"
    _make_bundle(evidence)
    (evidence / "terminal/systemd.json").unlink()

    report = Fresh30HTerminalAuditor(evidence).audit()

    assert _check(report, "systemd_result_and_restarts")["status"] == NOT_VERIFIABLE
    assert report["overall_status"] == NOT_VERIFIABLE


def test_runtime_failure_and_retry_path_fail_closed(tmp_path: Path) -> None:
    evidence = tmp_path / "bundle"
    _make_bundle(evidence)
    _json(
        evidence / "terminal/result.json",
        {
            "run_id": RUN_ID,
            "epoch": EPOCH,
            "ended_at": "2026-09-27T01:00:00Z",
            "elapsed_seconds": 3600,
            "full_duration_satisfied": False,
            "collector_exit_code": 0,
            "archive_scheduler_exit_code": 0,
            "archive_scheduler_started": True,
            "publisher_exit_code": 0,
            "publisher_started": True,
        },
    )
    _json(
        evidence / "terminal/finalization-trace.json",
        {
            "complete": True,
            "events": [
                {
                    "component": "scheduler",
                    "event": "retry",
                    "cohort": COHORT,
                },
                {
                    "component": "finalizer",
                    "event": "finalization_complete",
                    "cohort": COHORT,
                    "slot": "bithumb/trade/KRW-BTC",
                    "closed_at_utc": "2026-09-27T01:00:00Z",
                    "evidence_sha256": "1" * 64,
                },
                {
                    "component": "finalizer",
                    "event": "finalization_complete",
                    "cohort": COHORT,
                    "slot": "bithumb/trade/KRW-BTC",
                    "closed_at_utc": "2026-09-27T01:00:01Z",
                    "evidence_sha256": "2" * 64,
                },
            ],
        },
    )

    report = Fresh30HTerminalAuditor(evidence).audit()

    assert report["overall_status"] == FAIL
    assert _check(report, "actual_start_end_and_duration")["status"] == FAIL
    assert _check(report, "restart_idempotency_path")["status"] == FAIL
    assert _check(report, "closed_at_utc_stability")["status"] == FAIL
    assert _check(report, "finalization_evidence_hash_stability")["status"] == FAIL


def test_local_s3_receipt_mismatch_fails(tmp_path: Path) -> None:
    evidence = tmp_path / "bundle"
    _make_bundle(evidence)
    s3_receipt = next((evidence / "terminal/s3-receipts").rglob("*.archive-receipt.json"))
    s3_receipt.write_text('{"state":"different"}\n', encoding="utf-8")

    report = Fresh30HTerminalAuditor(evidence).audit()

    assert _check(report, "local_s3_receipt_hash_equality")["status"] == FAIL


def test_negative_component_exit_code_is_a_failure_not_missing_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "bundle"
    _make_bundle(evidence)
    result_path = evidence / "terminal/result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["collector_exit_code"] = -1
    _json(result_path, result)

    report = Fresh30HTerminalAuditor(evidence).audit()

    assert _check(report, "component_exit_codes")["status"] == FAIL


def test_non_finite_sealed_duration_is_not_verifiable(tmp_path: Path) -> None:
    evidence = tmp_path / "bundle"
    _make_bundle(evidence)
    _json(evidence / "sealed/runtime.json", {
        "runtime_software_commit": EXPECTED_RUNTIME_COMMIT,
        "software_tree_sha": EXPECTED_RUNTIME_TREE,
        "duration_seconds": float("nan"),
        "schedule": {"qualifying_cohorts": [COHORT]},
        "bithumb_redundancy_enabled": True,
        "bithumb_connection_count": 2,
    })

    report = Fresh30HTerminalAuditor(evidence).audit()

    assert _check(report, "actual_start_end_and_duration")["status"] == NOT_VERIFIABLE


def test_runtime_redundancy_configuration_must_match_final_metrics(tmp_path: Path) -> None:
    evidence = tmp_path / "bundle"
    _make_bundle(evidence)
    runtime_path = evidence / "sealed/runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime["bithumb_connection_count"] = 1
    _json(runtime_path, runtime)

    report = Fresh30HTerminalAuditor(evidence).audit()

    assert _check(report, "active_active_dedup_and_conflicts")["status"] == FAIL


def test_terminal_witness_must_include_successful_s3_upload(tmp_path: Path) -> None:
    evidence = tmp_path / "bundle"
    _make_bundle(evidence)
    witness_path = evidence / "terminal/terminal-witness.json"
    witness = json.loads(witness_path.read_text(encoding="utf-8"))
    witness["s3_uploaded"] = False
    _json(witness_path, witness)

    report = Fresh30HTerminalAuditor(evidence).audit()

    assert _check(report, "terminal_witness")["status"] == FAIL


def test_receipt_immutability_observation_must_match_exported_bytes(tmp_path: Path) -> None:
    evidence = tmp_path / "bundle"
    _make_bundle(evidence)
    local_receipt = next((evidence / "terminal/archive-receipts").rglob("*.archive-receipt.json"))
    _json(local_receipt, {"state": "changed-after-observation"})

    report = Fresh30HTerminalAuditor(evidence).audit()

    assert _check(report, "receipt_immutability")["status"] == FAIL


def test_audit_refuses_output_inside_evidence_bundle(tmp_path: Path) -> None:
    evidence = tmp_path / "bundle"
    _make_bundle(evidence)
    report = Fresh30HTerminalAuditor(evidence).audit()

    with pytest.raises(ValueError, match="outside the evidence directory"):
        _write_outputs(report, evidence / "audit-output", evidence)
