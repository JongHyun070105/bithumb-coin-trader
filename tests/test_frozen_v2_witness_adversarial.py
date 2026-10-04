"""Adversarial mutation coverage for the frozen V2 terminal-witness S3 check.

Test-only: exercises every FAIL/NOT_VERIFIABLE branch of the unchanged auditor.
A mutation must never produce PASS.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest

from scripts.audit_fresh_30h_terminal_v2 import FAIL, NOT_VERIFIABLE, PASS
from tests.test_fresh_30h_terminal_v2 import (
    _audit, _capture_and_anchor_bundle, _check, _dump, _reindex, bundle,  # noqa: F401
)

Mutator = Callable[[dict], None]

READBACK_MUTATIONS: dict[str, tuple[Mutator, str, str]] = {
    "duplicate_version_id_entry": (lambda r: r.update(version_ids=[r["VersionId"], r["VersionId"]]), FAIL, "duplicate_terminal_version_id_entry"),
    "version_id_absent_from_inventory": (lambda r: r.update(version_ids=["some-other-version"]), FAIL, "readback_version_id_not_in_version_inventory"),
    "empty_version_inventory": (lambda r: r.update(version_ids=[]), NOT_VERIFIABLE, "terminal_version_inventory_missing"),
    "non_string_version_inventory_entry": (lambda r: r.update(version_ids=[r["VersionId"], 7]), NOT_VERIFIABLE, "terminal_version_inventory_missing"),
    "content_length_mismatch": (lambda r: r.update(ContentLength=r["ContentLength"] + 1), FAIL, "content_length_mismatch"),
    "content_length_string": (lambda r: r.update(ContentLength=str(r["ContentLength"])), NOT_VERIFIABLE, "content_length_missing_or_not_integer"),
    "content_length_bool": (lambda r: r.update(ContentLength=True), NOT_VERIFIABLE, "content_length_missing_or_not_integer"),
    "byte_length_mismatch": (lambda r: r.update(byte_length=r["byte_length"] + 1), FAIL, "captured_byte_length_mismatch"),
    "readback_sha256_mismatch": (lambda r: r.update(sha256="0" * 64), FAIL, "sha256_mismatch"),
    "readback_sha256_malformed": (lambda r: r.update(sha256="xyz"), NOT_VERIFIABLE, "readback_hash_or_version_id_missing"),
    "missing_version_id": (lambda r: r.pop("VersionId"), FAIL, "readback_version_id_not_in_version_inventory"),
    "missing_etag": (lambda r: r.pop("ETag"), NOT_VERIFIABLE, "etag_missing"),
    "empty_etag": (lambda r: r.update(ETag=""), NOT_VERIFIABLE, "etag_missing"),
    "requested_version_id_absent": (lambda r: r.pop("requested_version_id"), NOT_VERIFIABLE, "unversioned_get_provenance_missing"),
    "requested_version_id_pinned": (lambda r: r.update(requested_version_id=r["VersionId"]), FAIL, "get_was_pinned_to_historical_version"),
    "latest_delete_marker_true": (lambda r: r.update(latest_delete_marker=True), FAIL, "latest_version_is_delete_marker"),
    "latest_delete_marker_string": (lambda r: r.update(latest_delete_marker="false"), NOT_VERIFIABLE, "latest_delete_marker_state_invalid"),
    "http_status_not_200": (lambda r: r.update(http_status=206), NOT_VERIFIABLE, "get_object_request_provenance_missing"),
    "http_status_string": (lambda r: r.update(http_status="200"), NOT_VERIFIABLE, "get_object_request_provenance_missing"),
    "missing_request_id": (lambda r: r.pop("request_id"), NOT_VERIFIABLE, "get_object_request_provenance_missing"),
    "missing_caller_arn": (lambda r: r.pop("caller_arn"), NOT_VERIFIABLE, "get_object_request_provenance_missing"),
    "missing_captured_at": (lambda r: r.pop("captured_at_utc"), NOT_VERIFIABLE, "get_object_request_provenance_missing"),
    "wrong_bucket": (lambda r: r.update(bucket="another-bucket"), FAIL, "terminal_s3_target_mismatch"),
    "wrong_key": (lambda r: r.update(key=r["key"] + ".bak"), FAIL, "terminal_s3_target_mismatch"),
}


def _apply_readback_mutation(bundle: dict[str, object], mutate: Mutator) -> dict:
    root = Path(bundle["root"])
    path = root / "terminal/s3-readback.json"
    readback = json.loads(path.read_text())
    mutate(readback)
    _dump(path, readback)
    _capture_and_anchor_bundle(bundle)
    _reindex(root)
    return _check(_audit(bundle), "terminal_witness_s3")


def test_unmutated_fixture_passes_witness_check(bundle: dict[str, object]) -> None:  # noqa: F811
    root = Path(bundle["root"])
    _capture_and_anchor_bundle(bundle)
    _reindex(root)
    check = _check(_audit(bundle), "terminal_witness_s3")
    assert check["status"] == PASS, check


@pytest.mark.parametrize("name", sorted(READBACK_MUTATIONS))
def test_readback_mutation_never_passes(bundle: dict[str, object], name: str) -> None:  # noqa: F811
    mutate, expected, reason = READBACK_MUTATIONS[name]
    check = _apply_readback_mutation(bundle, mutate)
    assert check["status"] == expected, check
    assert reason in check["details"]["failures"] + check["details"]["missing"], check


def test_local_and_remote_terminal_bytes_differ_fails(bundle: dict[str, object]) -> None:  # noqa: F811
    root = Path(bundle["root"])
    remote = root / "terminal/s3-readback/terminal-receipt.json"
    original = json.loads(remote.read_text())
    original["tampered_after_upload"] = True
    _dump(remote, original)
    _capture_and_anchor_bundle(bundle)
    _reindex(root)
    check = _check(_audit(bundle), "terminal_witness_s3")
    assert check["status"] == FAIL
    assert "bytes_differ" in check["details"]["failures"]


@pytest.mark.parametrize("field", ["run_id", "epoch"])
def test_witness_identity_mismatch_fails(bundle: dict[str, object], field: str) -> None:  # noqa: F811
    root = Path(bundle["root"])
    path = root / "terminal/terminal-witness.json"
    witness = json.loads(path.read_text())
    witness[field] = "someone-elses-" + field
    _dump(path, witness)
    _capture_and_anchor_bundle(bundle)
    _reindex(root)
    check = _check(_audit(bundle), "terminal_witness_s3")
    assert check["status"] == FAIL
    assert "witness_identity_mismatch" in check["details"]["failures"]


def test_witness_s3_key_mismatch_fails(bundle: dict[str, object]) -> None:  # noqa: F811
    root = Path(bundle["root"])
    path = root / "terminal/terminal-witness.json"
    witness = json.loads(path.read_text())
    witness["s3_key"] = "market-data/temporary/other-epoch/terminal/terminal-receipt.json"
    _dump(path, witness)
    _capture_and_anchor_bundle(bundle)
    _reindex(root)
    check = _check(_audit(bundle), "terminal_witness_s3")
    assert check["status"] == FAIL
    assert "terminal_s3_target_mismatch" in check["details"]["failures"]


@pytest.mark.parametrize("uploaded", [True, False])
def test_witness_s3_uploaded_boolean_alone_cannot_pass_or_fail_the_check(bundle: dict[str, object], uploaded: bool) -> None:  # noqa: F811
    root = Path(bundle["root"])
    path = root / "terminal/terminal-witness.json"
    witness = json.loads(path.read_text())
    witness["s3_uploaded"] = uploaded
    _dump(path, witness)
    _capture_and_anchor_bundle(bundle)
    _reindex(root)
    check = _check(_audit(bundle), "terminal_witness_s3")
    assert check["status"] == PASS
    assert check["details"]["witness_s3_uploaded"] is uploaded
