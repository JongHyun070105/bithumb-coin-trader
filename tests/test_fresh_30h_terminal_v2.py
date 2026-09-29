from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from scripts.audit_fresh_30h_terminal_v2 import AuditorV2, NOT_VERIFIABLE, PASS, FAIL, qualifying_hours
from scripts.export_fresh_30h_terminal_bundle import export_bundle


RUN_ID = "run-test"
EPOCH = "epoch-test"
COMMIT = "b" * 40
TREE = "c" * 40
INVOCATION = "invocation-test"


def _dump(path: Path, value: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    path.write_bytes(data)
    return data


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reindex(root: Path) -> None:
    # Recreate both self-declared inventories to model an editor regenerating
    # the bundle's local index after changing a payload.
    payloads = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.relative_to(root).as_posix() not in {"bundle-manifest.json", "terminal/evidence-hash-index.json"}):
        payloads.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size, "sha256": _sha(path.read_bytes())})
    manifest_path = root / "bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["payloads"] = payloads
    _dump(manifest_path, manifest)
    indexed = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.relative_to(root).as_posix() != "terminal/evidence-hash-index.json"):
        indexed.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size, "sha256": _sha(path.read_bytes())})
    _dump(root / "terminal/evidence-hash-index.json", {"schema": 1, "files": indexed})


def _capture_and_anchor_bundle(bundle: dict[str, object]) -> None:
    root = Path(bundle["root"])
    sources = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()
                       and p.relative_to(root).as_posix().startswith("terminal/")
                       and p.relative_to(root).as_posix() not in {
                           "terminal/capture-manifest.json", "terminal/evidence-hash-index.json",
                       }):
        raw = path.read_bytes()
        sources.append({"path": path.relative_to(root).as_posix(), "size": len(raw), "sha256": _sha(raw),
                        "source_kind": "test_fixture", "captured_at_utc": "2026-09-30T20:00:00Z"})
    capture = json.loads((root / "terminal/capture-manifest.json").read_text())
    capture["sources"] = sources
    capture_bytes = _dump(root / "terminal/capture-manifest.json", capture)
    bundle["capture_anchor"] = _sha(capture_bytes)
    manifest_path = root / "bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["external_capture_manifest_sha256"] = bundle["capture_anchor"]
    _dump(manifest_path, manifest)


@pytest.fixture
def bundle(tmp_path: Path) -> dict[str, object]:
    root = tmp_path / "bundle"
    runtime = {
        "runtime_software_commit": COMMIT, "duration_seconds": 7200,
        "bithumb_redundancy": {"mode": "ACTIVE_ACTIVE", "physical_connections": 2},
        "feeds": {"bithumb_markets": ["KRW-BTC"], "binance_symbols": ["btcusdt"], "upbit_markets": ["KRW-BTC"]},
    }
    runtime_bytes = _dump(root / "sealed/runtime.json", runtime)
    artifact_hashes = {"current.runtime.json": _sha(runtime_bytes)}
    identity = {
        "run_id": RUN_ID, "epoch": EPOCH, "software_commit_sha": COMMIT,
        "software_tree_sha": TREE, "s3_bucket": "example-bucket",
        "s3_prefix": "market-data/test", "sealed_artifact_hashes": artifact_hashes,
    }
    identity_bytes = _dump(root / "sealed/identity.json", identity)
    sealed = {"identity_sha256": _sha(identity_bytes), "artifact_hashes": artifact_hashes}
    seal_bytes = _dump(root / "sealed/sealed-manifest.json", sealed)
    _dump(root / "sealed/artifacts/current.runtime.json", runtime)

    start, end = "2026-09-29T00:00:00Z", "2026-09-29T02:00:00Z"
    metrics = {
        "collector_started_at": start, "queue_size": 0,
        "unpersisted_event_count": 0,
        "bithumb_physical_connections": {"active_count": 2},
        "exchanges": {"bithumb": {"writer_errors": 0, "queue_dropped_events": 0,
                                    "max_event_loop_lag_seconds": 0.5,
                                    "conflicting_duplicate_frames": 0, "deduplicated_frames": 4}},
    }
    result = {
        "started_at": "2026-09-28T23:59:57Z", "ended_at": end,
        "full_duration_satisfied": True, "collector_exit_code": 0,
        "archive_scheduler_exit_code": 0, "publisher_exit_code": 0,
        "archive_scheduler_started": True, "publisher_started": True,
        "collector_completed_normally": True, "overall_status": "PASS",
        "received_signal": None, "forced_timeout": False, "timed_out": False,
    }
    lifecycle = {"phase": "COMPLETE", "final_manifest_flush_observed": True,
                 "collector_stopped_at": end,
                 "observer_unit_name": f"bitcoin-trader-30h-{RUN_ID}.service"}
    systemd = {
        "InvocationID": INVOCATION, "Result": "success", "ExecMainStatus": 0,
        "ExecMainCode": "exited", "MainPID": 123, "NRestarts": 0,
        "start_time": "2026-09-28T23:59:57Z", "stop_time": end, "runtime_duration_seconds": 7203,
        "watchdog_result": "not_triggered", "received_signal": None,
    }
    _dump(root / "terminal/result.json", result)
    _dump(root / "terminal/collector-lifecycle.json", lifecycle)
    _dump(root / "terminal/collector-metrics.json", metrics)
    _dump(root / "terminal/systemd-terminal.json", systemd)
    (root / "terminal/systemd-invocation.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (root / "terminal/systemd-invocation.jsonl").write_text(json.dumps({
        "_SYSTEMD_INVOCATION_ID": INVOCATION,
        "_SYSTEMD_UNIT": f"bitcoin-trader-30h-{RUN_ID}.service", "MESSAGE": "stop",
    }) + "\n")
    receipt = _dump(root / "terminal/terminal-receipt.json", {"run_id": RUN_ID, "epoch": EPOCH, "s3_uploaded": True})
    _dump(root / "terminal/s3-readback/terminal-receipt.json", json.loads(receipt))
    receipt_hash = _sha(receipt)
    _dump(root / "terminal/terminal-witness.json", {
        "run_id": RUN_ID, "epoch": EPOCH, "s3_uploaded": True,
        "s3_key": "market-data/test/terminal/terminal-receipt.json",
    })
    _dump(root / "terminal/s3-readback.json", {
        "outcome": "success", "bucket": "example-bucket",
        "key": "market-data/test/terminal/terminal-receipt.json", "VersionId": "v1",
        "version_ids": ["v1"], "sha256": receipt_hash, "byte_length": len(receipt),
        "http_status": 200, "request_id": "request-1", "caller_arn": "arn:aws:iam::123456789012:role/auditor",
        "captured_at_utc": "2026-09-30T20:00:00Z", "etag": "a1b2c3",
    })

    feeds = [f"{exchange}:{stream}:{market}" for exchange, streams, markets in (
        ("bithumb", ("orderbook", "trade", "ticker"), ("KRW-BTC",)),
        ("binance", ("trade", "orderbook"), ("btcusdt",)),
        ("upbit", ("orderbook", "trade"), ("KRW-BTC",)),
    ) for market in markets for stream in streams]
    cohort_local = _dump(root / "terminal/receipts/cohort.json", {"cohort_id": "2026-09-29_01"})
    cohort_remote = _dump(root / "terminal/s3-readback/cohort.json", {"cohort_id": "2026-09-29_01"})
    slot_rows, receipt_rows = [], []
    listing_keys = []
    for index, feed_id in enumerate(feeds):
        local_path = f"terminal/receipts/slot-{index}.json"
        remote_path = f"terminal/s3-readback/slot-{index}.json"
        s3_key = f"market-data/test/receipts/slot-{index}.json"
        listing_keys.append(s3_key)
        local = _dump(root / local_path, {"feed_id": feed_id})
        remote = _dump(root / remote_path, {"feed_id": feed_id})
        digest = _sha(local)
        slot_rows.append({"feed_id": feed_id, "local_receipt_path": local_path,
                          "local_receipt_sha256": digest, "s3_get_outcome": "success",
                          "s3_receipt_sha256": _sha(remote), "s3_readback_path": remote_path,
                          "version_id": f"slot-v{index}"})
        receipt_rows.append({"type": "SLOT_RECEIPT", "durability": "BOTH_REQUIRED",
                             "cohort_id": "2026-09-29_01", "feed_id": feed_id,
                             "s3_key": s3_key,
                             "local_path": local_path, "local_sha256": digest,
                             "s3_readback_path": remote_path, "s3_sha256": _sha(remote),
                             "s3_get_outcome": "success", "version_id": f"slot-v{index}"})
    journal_meta = {}
    journal_specs = (
        ("2026-09-29_00", "TOUCHED_PARTIAL", start, "2026-09-29T01:00:00Z"),
        ("2026-09-29_01", "QUALIFYING_FULL_HOUR", "2026-09-29T01:00:00Z", end),
        ("2026-09-29_02", "TOUCHED_PARTIAL", end, end),
    )
    for cohort_id, qualification, obs_start, obs_end in journal_specs:
        observations = [{
            "cohort_utc": cohort_id, "cohort_qualification": qualification,
            "observation_start_utc": obs_start, "observation_end_utc": obs_end,
            "feed": {"exchange": feed.split(":")[0], "stream": feed.split(":")[1], "market": feed.split(":")[2]},
        } for feed in feeds]
        journal = {"schema_version": 1, "cohort_utc": cohort_id,
                   "slot_count": len(observations), "observations": observations}
        journal_path = f"terminal/journals/journal_{cohort_id}.json"
        journal_bytes = _dump(root / journal_path, journal)
        journal_meta[cohort_id] = {"path": journal_path, "sha256": _sha(journal_bytes),
                                   "cohort_qualification": qualification,
                                   "observation_start_utc": obs_start, "observation_end_utc": obs_end}
    _dump(root / "terminal/cohorts.json", {
        "evidence_classification": "RECONSTRUCTED_OBSERVATION",
        "actual_start_utc": start, "actual_end_utc": end,
        "source_journals": [{"cohort_id": cohort_id, "path": meta["path"], "sha256": meta["sha256"]}
                            for cohort_id, meta in journal_meta.items()],
        "cohorts": [{
            "cohort_id": "2026-09-29_01", "cohort_qualification": "QUALIFYING_FULL_HOUR",
            "observation_start_utc": "2026-09-29T01:00:00Z", "observation_end_utc": end,
            "journal_path": journal_meta["2026-09-29_01"]["path"],
            "journal_sha256": journal_meta["2026-09-29_01"]["sha256"],
            "feed_slots": slot_rows,
            "local_receipt_path": "terminal/receipts/cohort.json", "local_receipt_sha256": _sha(cohort_local),
            "s3_readback_path": "terminal/s3-readback/cohort.json", "s3_receipt_sha256": _sha(cohort_remote),
            "s3_get_outcome": "success", "version_id": "cohort-v1",
        }],
        "partial_cohorts": [{
            "cohort_id": cohort_id, "cohort_qualification": meta["cohort_qualification"],
            "observation_start_utc": meta["observation_start_utc"], "observation_end_utc": meta["observation_end_utc"],
            "journal_path": meta["path"], "journal_sha256": meta["sha256"],
        } for cohort_id, meta in journal_meta.items() if meta["cohort_qualification"] == "TOUCHED_PARTIAL"],
    })
    receipt_rows.extend([
        {"type": "COHORT_RECEIPT", "durability": "BOTH_REQUIRED",
         "cohort_id": "2026-09-29_01", "receipt_id": "cohort-2026-09-29_01",
         "s3_key": "market-data/test/receipts/cohort.json",
         "local_path": "terminal/receipts/cohort.json", "local_sha256": _sha(cohort_local),
         "s3_readback_path": "terminal/s3-readback/cohort.json", "s3_sha256": _sha(cohort_remote),
         "s3_get_outcome": "success", "version_id": "cohort-v1"},
        {"type": "TERMINAL_RECEIPT", "durability": "BOTH_REQUIRED",
         "receipt_id": "terminal-receipt",
         "s3_key": "market-data/test/terminal/terminal-receipt.json",
         "local_path": "terminal/terminal-receipt.json", "local_sha256": receipt_hash,
         "s3_readback_path": "terminal/s3-readback/terminal-receipt.json", "s3_sha256": receipt_hash,
         "s3_get_outcome": "success", "version_id": "terminal-v1"},
        {"type": "SKIPPED_PARTIAL_HOUR_RECEIPT", "durability": "LOCAL_ONLY",
         "local_path": "terminal/receipts/skipped.json", "local_sha256": "d" * 64,
         "s3_get_outcome": "not_required"},
        {"type": "TIMESTAMPED_TERMINAL_RECEIPT", "durability": "OPTIONAL"},
        {"type": "OTHER", "durability": "OPTIONAL"},
    ])
    skipped_bytes = _dump(root / "terminal/receipts/skipped.json", {"partial": True})
    receipt_rows[-3]["local_sha256"] = _sha(skipped_bytes)
    cohort_hash = _sha(cohort_local)
    file_local = _dump(root / "terminal/receipts/file.json", {"file": "coverage"})
    file_remote = _dump(root / "terminal/s3-readback/file.json", {"file": "coverage"})
    file_key = "market-data/test/receipts/file.json"
    listing_keys.extend(("market-data/test/receipts/cohort.json",
                         "market-data/test/terminal/terminal-receipt.json", file_key))
    receipt_rows.append({"type": "FILE_RECEIPT", "durability": "BOTH_REQUIRED",
                         "s3_key": file_key, "local_path": "terminal/receipts/file.json",
                         "local_sha256": _sha(file_local), "s3_readback_path": "terminal/s3-readback/file.json",
                         "s3_sha256": _sha(file_remote), "s3_get_outcome": "success", "version_id": "file-v1"})
    _dump(root / "terminal/receipt-inventory.json", {
        "receipts": receipt_rows, "unexpected_s3_objects": [],
        "s3_prefix_listing": {
            "outcome": "success", "bucket": "example-bucket", "prefix": "market-data/test",
            "complete": True, "next_token": None, "objects": [{"key": key} for key in listing_keys],
            "request_id": "list-request", "caller_arn": "arn:aws:iam::123456789012:role/auditor",
            "captured_at_utc": "2026-09-30T20:00:00Z",
        },
        "required_immutable_receipt_ids": ["cohort-2026-09-29_01"],
        "observations": [
            {"receipt_id": "cohort-2026-09-29_01", "captured_at_utc": "2026-09-29T03:00:00Z", "sha256": cohort_hash, "version_id": "cohort-v1"},
            {"receipt_id": "cohort-2026-09-29_01", "captured_at_utc": "2026-09-29T03:30:00Z", "sha256": cohort_hash, "version_id": "cohort-v1"},
        ],
    })
    _dump(root / "terminal/finalization-trace.json", {
        "evidence_classification": "NATIVE_INSTRUMENTATION_PRESENT",
        "scheduler_retries": 0, "finalizer_retries": 0, "recovery_invocations": 0,
        "duplicate_finalization": 0, "closed_at_utc_stable": True,
        "evidence_hash_stable": True, "restart_idempotency_path_exposed": True,
    })
    captured_sources = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()
                       and p.relative_to(root).as_posix().startswith("terminal/")
                       and p.relative_to(root).as_posix() not in {
                           "terminal/capture-manifest.json", "terminal/evidence-hash-index.json",
                       }):
        raw = path.read_bytes()
        captured_sources.append({"path": path.relative_to(root).as_posix(),
                                 "size": len(raw), "sha256": _sha(raw),
                                 "source_kind": "test_fixture", "captured_at_utc": "2026-09-30T20:00:00Z"})
    capture_bytes = _dump(root / "terminal/capture-manifest.json", {
        "schema": "Fresh30HTerminalCapture", "version": 1,
        "run_id": RUN_ID, "epoch": EPOCH, "runtime_commit": COMMIT, "runtime_tree": TREE,
        "captured_at_utc": "2026-09-30T20:00:00Z", "sources": captured_sources,
    })
    manifest = {
        "schema": "Fresh30HTerminalBundle", "version": 2, "run_id": RUN_ID,
        "epoch": EPOCH, "runtime_commit": COMMIT, "runtime_tree": TREE,
        "external_sealed_manifest_sha256": _sha(seal_bytes),
        "external_capture_manifest_sha256": _sha(capture_bytes), "payloads": [],
    }
    _dump(root / "bundle-manifest.json", manifest)
    _reindex(root)
    return {"root": root, "anchor": _sha(seal_bytes), "capture_anchor": _sha(capture_bytes)}


def _audit(bundle: dict[str, object]) -> dict[str, object]:
    auditor = AuditorV2(Path(bundle["root"]), run_id=RUN_ID, epoch=EPOCH,
                        runtime_commit=COMMIT, runtime_tree=TREE,
                        sealed_manifest_sha256=str(bundle["anchor"]),
                        capture_manifest_sha256=str(bundle["capture_anchor"]))
    return auditor.audit()


def _check(report: dict[str, object], name: str) -> dict[str, object]:
    return next(row for row in report["checks"] if row["name"] == name)


def test_valid_nested_schema_and_exact_receipt_scope_pass(bundle: dict[str, object]) -> None:
    report = _audit(bundle)
    assert report["overall_status"] == PASS
    assert _check(report, "bithumb_redundancy")["status"] == PASS
    assert _check(report, "receipt_scope")["status"] == PASS


@pytest.mark.parametrize("extra_path", ["untrusted/notes.json", "sealed/extra.json", "sealed/elsewhere/current.runtime.json"])
def test_bundle_scope_rejects_unallowlisted_and_duplicate_sealed_payloads(bundle: dict[str, object], extra_path: str) -> None:
    root = Path(bundle["root"])
    _dump(root / extra_path, {"untrusted": True})
    _reindex(root)
    assert _check(_audit(bundle), "bundle_scope")["status"] == FAIL


def test_flat_old_redundancy_schema_is_not_verifiable(bundle: dict[str, object]) -> None:
    path = Path(bundle["root"]) / "sealed/runtime.json"
    runtime = json.loads(path.read_text())
    runtime["bithumb_redundancy_enabled"] = True
    runtime["bithumb_connection_count"] = 2
    runtime.pop("bithumb_redundancy")
    _dump(path, runtime)
    _reindex(Path(bundle["root"]))
    report = _audit(bundle)
    assert _check(report, "bithumb_redundancy")["status"] == NOT_VERIFIABLE
    assert report["overall_status"] != PASS


def test_native_named_connection_statuses_map_to_active_socket_count(bundle: dict[str, object]) -> None:
    root = Path(bundle["root"])
    path = root / "terminal/collector-metrics.json"
    metrics = json.loads(path.read_text())
    metrics["bithumb_physical_connections"] = {"primary": "CONNECTED", "secondary": "CONNECTED"}
    _dump(path, metrics)
    _reindex(root)
    assert _check(_audit(bundle), "bithumb_redundancy")["status"] == PASS


def test_missing_feed_universe_and_wrong_cohort_list_fail_closed(bundle: dict[str, object]) -> None:
    root = Path(bundle["root"])
    cohorts_path = root / "terminal/cohorts.json"
    cohorts = json.loads(cohorts_path.read_text())
    cohorts["cohorts"][0]["feed_slots"] = []
    _dump(cohorts_path, cohorts)
    _reindex(root)
    assert _check(_audit(bundle), "cohorts_receipts")["status"] != PASS
    cohorts["cohorts"].append(dict(cohorts["cohorts"][0], cohort_id="2026-09-29_01"))
    _dump(cohorts_path, cohorts)
    _reindex(root)
    assert _check(_audit(bundle), "cohorts_receipts")["status"] == FAIL


@pytest.mark.parametrize("change", ["cohort_missing", "slot_local_only", "extra_s3", "hash_mismatch", "403"])
def test_receipt_scope_mutations_never_pass(bundle: dict[str, object], change: str) -> None:
    root = Path(bundle["root"])
    path = root / "terminal/receipt-inventory.json"
    data = json.loads(path.read_text())
    if change == "cohort_missing":
        data["receipts"] = [r for r in data["receipts"] if r["type"] != "COHORT_RECEIPT"]
    elif change == "slot_local_only":
        row = next(r for r in data["receipts"] if r["type"] == "SLOT_RECEIPT")
        row.pop("s3_get_outcome")
    elif change == "extra_s3":
        data["s3_prefix_listing"]["objects"].append({"key": "market-data/test/unexpected.json"})
    elif change == "hash_mismatch":
        next(r for r in data["receipts"] if r["type"] == "COHORT_RECEIPT")["s3_sha256"] = "e" * 64
    else:
        next(r for r in data["receipts"] if r["type"] == "COHORT_RECEIPT")["s3_get_outcome"] = "AccessDenied"
    _dump(path, data)
    _reindex(root)
    assert _check(_audit(bundle), "receipt_scope")["status"] != PASS


@pytest.mark.parametrize("outcome,expected", [
    ("AccessDenied", NOT_VERIFIABLE), ("403", NOT_VERIFIABLE),
    ("expired_credentials", NOT_VERIFIABLE), ("timeout", NOT_VERIFIABLE),
    ("network_error", NOT_VERIFIABLE), ("NoSuchKey", FAIL),
])
def test_terminal_get_distinguishes_denied_from_authorized_missing(bundle: dict[str, object], outcome: str, expected: str) -> None:
    root = Path(bundle["root"])
    path = root / "terminal/s3-readback.json"
    data = json.loads(path.read_text())
    data["outcome"] = outcome
    data["confirmed_authorized"] = outcome == "NoSuchKey"
    _dump(path, data)
    _reindex(root)
    assert _check(_audit(bundle), "terminal_witness_s3")["status"] == expected


def test_terminal_witness_true_without_object_and_corrupt_bytes_fail(bundle: dict[str, object]) -> None:
    root = Path(bundle["root"])
    path = root / "terminal/s3-readback.json"
    data = json.loads(path.read_text())
    data["outcome"] = "NoSuchKey"
    data["confirmed_authorized"] = True
    _dump(path, data)
    _reindex(root)
    assert _check(_audit(bundle), "terminal_witness_s3")["status"] == FAIL
    data["outcome"] = "success"
    data.pop("confirmed_authorized")
    _dump(root / "terminal/s3-readback/terminal-receipt.json", {"different": True})
    _dump(path, data)
    _reindex(root)
    assert _check(_audit(bundle), "terminal_witness_s3")["status"] == FAIL


def test_duplicate_terminal_versions_and_wrong_target_fail(bundle: dict[str, object]) -> None:
    root = Path(bundle["root"])
    path = root / "terminal/s3-readback.json"
    data = json.loads(path.read_text())
    data["version_ids"] = ["v1", "v2"]
    _dump(path, data)
    _reindex(root)
    assert _check(_audit(bundle), "terminal_witness_s3")["status"] == FAIL


@pytest.mark.parametrize("mutation", ["wrong_commit", "wrong_tree", "wrong_identity_sha", "tampered_artifact"])
def test_sealed_anchor_rejects_identity_or_artifact_tampering(bundle: dict[str, object], mutation: str) -> None:
    root = Path(bundle["root"])
    if mutation in ("wrong_commit", "wrong_tree"):
        identity_path = root / "sealed/identity.json"
        identity = json.loads(identity_path.read_text())
        identity["software_commit_sha" if mutation == "wrong_commit" else "software_tree_sha"] = "0" * 40
        _dump(identity_path, identity)
    elif mutation == "wrong_identity_sha":
        sealed_path = root / "sealed/sealed-manifest.json"
        sealed = json.loads(sealed_path.read_text())
        sealed["identity_sha256"] = "f" * 64
        _dump(sealed_path, sealed)
    else:
        _dump(root / "sealed/artifacts/current.runtime.json", {"modified": True})
    _reindex(root)
    assert _check(_audit(bundle), "runtime_identity")["status"] == FAIL


def test_regenerated_self_index_cannot_reanchor_sealed_manifest(bundle: dict[str, object]) -> None:
    root = Path(bundle["root"])
    runtime_path = root / "sealed/runtime.json"
    runtime = json.loads(runtime_path.read_text())
    runtime["runtime_software_commit"] = "0" * 40
    _dump(runtime_path, runtime)
    _reindex(root)
    report = _audit(bundle)
    assert _check(report, "runtime_identity")["status"] == FAIL
    assert report["overall_status"] == FAIL


@pytest.mark.parametrize("trace_class,expected", [(None, NOT_VERIFIABLE), ("RECONSTRUCTED_OBSERVATION", NOT_VERIFIABLE)])
def test_finalization_requires_native_trace(bundle: dict[str, object], trace_class: str | None, expected: str) -> None:
    path = Path(bundle["root"]) / "terminal/finalization-trace.json"
    trace = json.loads(path.read_text())
    if trace_class is None:
        path.unlink()
    else:
        trace["evidence_classification"] = trace_class
        _dump(path, trace)
    _reindex(Path(bundle["root"]))
    assert _check(_audit(bundle), "finalization_trace")["status"] == expected


def test_systemd_requires_exact_invocation_and_restarts_zero(bundle: dict[str, object]) -> None:
    root = Path(bundle["root"])
    raw = root / "terminal/systemd-invocation.jsonl"
    raw.write_text(json.dumps({"_SYSTEMD_INVOCATION_ID": "other"}) + "\n")
    _reindex(root)
    assert _check(_audit(bundle), "systemd_terminal")["status"] == FAIL
    raw.write_text(json.dumps({"_SYSTEMD_INVOCATION_ID": INVOCATION}) + "\n")
    terminal_path = root / "terminal/systemd-terminal.json"
    terminal = json.loads(terminal_path.read_text())
    terminal["NRestarts"] = 1
    _dump(terminal_path, terminal)
    _reindex(root)
    assert _check(_audit(bundle), "systemd_terminal")["status"] == FAIL


def test_missing_systemd_unit_and_combined_unknowns_cannot_pass(bundle: dict[str, object]) -> None:
    root = Path(bundle["root"])
    systemd_path = root / "terminal/systemd-terminal.json"
    systemd = json.loads(systemd_path.read_text())
    systemd_path.unlink()
    _reindex(root)
    assert _check(_audit(bundle), "systemd_terminal")["status"] == NOT_VERIFIABLE
    _dump(systemd_path, systemd)
    witness_path = root / "terminal/s3-readback.json"
    witness = json.loads(witness_path.read_text())
    witness["outcome"] = "AccessDenied"
    _dump(witness_path, witness)
    trace_path = root / "terminal/finalization-trace.json"
    trace = json.loads(trace_path.read_text())
    trace["evidence_classification"] = "RECONSTRUCTED_OBSERVATION"
    _dump(trace_path, trace)
    _capture_and_anchor_bundle(bundle)
    _reindex(root)
    report = _audit(bundle)
    assert _check(report, "terminal_witness_s3")["status"] == NOT_VERIFIABLE
    assert _check(report, "finalization_trace")["status"] == NOT_VERIFIABLE
    assert report["overall_status"] == NOT_VERIFIABLE


def test_inflated_journal_interval_cannot_be_reanchored_by_local_index(bundle: dict[str, object]) -> None:
    root = Path(bundle["root"])
    journal_path = root / "terminal/journals/journal_2026-09-29_01.json"
    journal = json.loads(journal_path.read_text())
    for observation in journal["observations"]:
        observation["observation_end_utc"] = "2026-09-30T23:00:00Z"
    _dump(journal_path, journal)
    cohorts_path = root / "terminal/cohorts.json"
    cohorts = json.loads(cohorts_path.read_text())
    for source in cohorts["source_journals"]:
        if source["cohort_id"] == "2026-09-29_01":
            source["sha256"] = _sha(journal_path.read_bytes())
    for row in cohorts["cohorts"]:
        if row["cohort_id"] == "2026-09-29_01":
            row["journal_sha256"] = _sha(journal_path.read_bytes())
    _dump(cohorts_path, cohorts)
    _reindex(root)
    report = _audit(bundle)
    assert _check(report, "capture_provenance")["status"] == FAIL
    assert report["overall_status"] == FAIL


def test_capture_manifest_rejects_noncanonical_duplicate_path(bundle: dict[str, object]) -> None:
    root = Path(bundle["root"])
    capture_path = root / "terminal/capture-manifest.json"
    capture = json.loads(capture_path.read_text())
    existing = next(row for row in capture["sources"] if row["path"] == "terminal/s3-readback.json")
    capture["sources"].append(dict(existing, path="terminal//s3-readback.json"))
    capture_bytes = _dump(capture_path, capture)
    bundle["capture_anchor"] = _sha(capture_bytes)
    manifest_path = root / "bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["external_capture_manifest_sha256"] = bundle["capture_anchor"]
    _dump(manifest_path, manifest)
    _reindex(root)
    assert _check(_audit(bundle), "capture_provenance")["status"] == FAIL


@pytest.mark.parametrize("field", ["bucket", "key"])
def test_terminal_readback_wrong_bucket_or_prefix_fails(bundle: dict[str, object], field: str) -> None:
    root = Path(bundle["root"])
    path = root / "terminal/s3-readback.json"
    readback = json.loads(path.read_text())
    readback[field] = "another-bucket" if field == "bucket" else "market-data/other/terminal/terminal-receipt.json"
    _dump(path, readback)
    _reindex(root)
    assert _check(_audit(bundle), "terminal_witness_s3")["status"] == FAIL


@pytest.mark.parametrize("publisher,after,expected", [(-15, True, PASS), (-15, False, FAIL), (1, True, FAIL), (-9, True, FAIL)])
def test_publisher_shutdown_is_narrowly_scoped(bundle: dict[str, object], publisher: int, after: bool, expected: str) -> None:
    root = Path(bundle["root"])
    path = root / "terminal/result.json"
    result = json.loads(path.read_text())
    result["publisher_exit_code"] = publisher
    result["publisher_stopped_after_collector"] = after
    _dump(path, result)
    _reindex(root)
    assert _check(_audit(bundle), "component_exit_codes")["status"] == expected


def test_observer_wrong_unit_is_visible_as_nonblocking_diagnostic(bundle: dict[str, object]) -> None:
    root = Path(bundle["root"])
    path = root / "terminal/collector-lifecycle.json"
    lifecycle = json.loads(path.read_text())
    lifecycle["observer_unit_name"] = "bitcoin-trader-transient-run-test.service"
    _dump(path, lifecycle)
    _reindex(root)
    report = _audit(bundle)
    diagnostic = _check(report, "observer_unit_diagnostic")
    assert diagnostic["status"] == NOT_VERIFIABLE
    assert diagnostic["details"]["informational"] is True


@pytest.mark.parametrize("minute", [0, 1, 34, 59])
def test_qualifying_hour_rule_for_start_offsets_and_day_rollover(minute: int) -> None:
    start = datetime(2026, 9, 29, 9, minute, tzinfo=timezone.utc)
    end = start + timedelta(seconds=108000)
    hours, partial, ending_partial = qualifying_hours(start, end)
    assert len(hours) == 29
    assert hours == sorted(hours)
    assert hours[0] == "2026-09-29_10"
    assert hours[-1].startswith("2026-09-30_")
    assert partial == "2026-09-29_09"
    assert ending_partial == "2026-09-30_15"


def test_missing_s3_version_or_slot_readback_never_passes(bundle: dict[str, object]) -> None:
    root = Path(bundle["root"])
    path = root / "terminal/cohorts.json"
    cohorts = json.loads(path.read_text())
    cohorts["cohorts"][0]["feed_slots"][0]["version_id"] = None
    cohorts["cohorts"][0]["feed_slots"][0]["s3_get_outcome"] = "success"
    _dump(path, cohorts)
    _reindex(root)
    assert _check(_audit(bundle), "cohorts_receipts")["status"] == NOT_VERIFIABLE


def test_exporter_is_deterministic_and_preserves_exact_explicit_sources(bundle: dict[str, object], tmp_path: Path) -> None:
    root = Path(bundle["root"])
    sources = [(p.relative_to(root).as_posix(), p) for p in root.rglob("*") if p.is_file() and p.relative_to(root).as_posix() not in {"bundle-manifest.json", "terminal/evidence-hash-index.json"}]
    outputs = []
    for name in ("first", "second"):
        output = tmp_path / name
        export_bundle(output_dir=output, sources=sources, run_id=RUN_ID, epoch=EPOCH,
                      runtime_commit=COMMIT, runtime_tree=TREE,
                      sealed_manifest_sha256=str(bundle["anchor"]),
                      capture_manifest_sha256=str(bundle["capture_anchor"]))
        outputs.append({p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob("*") if p.is_file()})
    assert outputs[0] == outputs[1]


def test_exporter_rejects_unanchored_or_overwrite_sources(bundle: dict[str, object], tmp_path: Path) -> None:
    root = Path(bundle["root"])
    sources = [(p.relative_to(root).as_posix(), p) for p in root.rglob("*") if p.is_file() and p.relative_to(root).as_posix() not in {"bundle-manifest.json", "terminal/evidence-hash-index.json"}]
    with pytest.raises(ValueError, match="external hash anchor"):
        export_bundle(output_dir=tmp_path / "wrong-anchor", sources=sources, run_id=RUN_ID,
                      epoch=EPOCH, runtime_commit=COMMIT, runtime_tree=TREE,
                      sealed_manifest_sha256="0" * 64,
                      capture_manifest_sha256=str(bundle["capture_anchor"]))
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("keep")
    with pytest.raises(FileExistsError):
        export_bundle(output_dir=occupied, sources=sources, run_id=RUN_ID,
                      epoch=EPOCH, runtime_commit=COMMIT, runtime_tree=TREE,
                      sealed_manifest_sha256=str(bundle["anchor"]),
                      capture_manifest_sha256=str(bundle["capture_anchor"]))


def test_exporter_rejects_noncanonical_destination_paths(bundle: dict[str, object], tmp_path: Path) -> None:
    root = Path(bundle["root"])
    sources = [(p.relative_to(root).as_posix(), p) for p in root.rglob("*") if p.is_file()
               and p.relative_to(root).as_posix() not in {"bundle-manifest.json", "terminal/evidence-hash-index.json"}]
    sources.append(("terminal//result.json", root / "terminal/result.json"))
    with pytest.raises(ValueError, match="safe relative path"):
        export_bundle(output_dir=tmp_path / "noncanonical", sources=sources, run_id=RUN_ID,
                      epoch=EPOCH, runtime_commit=COMMIT, runtime_tree=TREE,
                      sealed_manifest_sha256=str(bundle["anchor"]),
                      capture_manifest_sha256=str(bundle["capture_anchor"]))


def test_rebuilt_bundle_index_cannot_reanchor_edited_s3_readback(bundle: dict[str, object]) -> None:
    root = Path(bundle["root"])
    readback_path = root / "terminal/s3-readback.json"
    readback = json.loads(readback_path.read_text())
    readback["VersionId"] = "invented-version"
    _dump(readback_path, readback)
    _reindex(root)
    report = _audit(bundle)
    assert _check(report, "capture_provenance")["status"] == FAIL
    assert report["overall_status"] == FAIL


def test_immutability_requires_two_observations_for_each_qualifying_cohort(bundle: dict[str, object]) -> None:
    root = Path(bundle["root"])
    path = root / "terminal/receipt-inventory.json"
    inventory = json.loads(path.read_text())
    inventory["observations"] = inventory["observations"][:1]
    _dump(path, inventory)
    _reindex(root)
    assert _check(_audit(bundle), "receipt_immutability")["status"] == NOT_VERIFIABLE


def test_run_hour_math_uses_pinned_76_feed_universe_and_2204_slots() -> None:
    from scripts.audit_fresh_30h_terminal_v2 import expected_feed_ids

    runtime = {"feeds": {
        "bithumb_markets": [f"KRW-{i:02d}" for i in range(20)],
        "binance_symbols": [f"coin{i}usdt" for i in range(4)],
        "upbit_markets": [f"KRW-{i:02d}" for i in range(4)],
    }}
    feeds = expected_feed_ids(runtime)
    assert feeds is not None and len(feeds) == len(set(feeds)) == 76
    start = datetime(2026, 9, 29, 9, 34, 57, tzinfo=timezone.utc)
    hours, _, _ = qualifying_hours(start, start + timedelta(seconds=108000))
    assert len(hours) == 29
    assert len(hours) * len(feeds) == 2204
