"""Tests for evidence_hashing and actual_start_evidence modules."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bithumb_coin_trader.evidence_hashing import (
    canonical_json_bytes,
    canonical_sha256,
    file_sha256,
)
from bithumb_coin_trader.actual_start_evidence import (
    ActualStartIdentity,
    NormalizedActualStartEvidence,
    normalize_actual_start_evidence,
)

FIXTURE = Path(__file__).parent / "fixtures" / "aws_30h_v2_actual_start_evidence.json"


def expected_identity(payload: dict) -> ActualStartIdentity:
    """Build ActualStartIdentity from a V2 legacy payload."""
    return ActualStartIdentity(
        collector_epoch=payload["collector_epoch"],
        collector_run_id=payload["collector_run_id"],
        runtime_commit=payload["runtime_code_commit"],  # legacy name
        runtime_config_fingerprint=payload["runtime_config_fingerprint"],
    )


# ── canonical_json_bytes ───────────────────────────────────────────────────

def test_canonical_bytes_are_exact_and_reject_non_finite() -> None:
    assert canonical_json_bytes({"한글": 1, "b": [2, 1], "a": True}) == (
        b'{"a":true,"b":[2,1],"\\ud55c\\uae00":1}'
    )
    with pytest.raises(ValueError):
        canonical_json_bytes({"bad": float("nan")})


def test_canonical_sha256_excludes_field() -> None:
    d = {"a": 1, "b": 2}
    without_b = canonical_sha256(d, excluded=("b",))
    only_a = canonical_sha256({"a": 1})
    assert without_b == only_a


# ── V2 legacy fixture normalization ───────────────────────────────────────

def test_exact_v2_legacy_fixture_normalizes() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    normalized = normalize_actual_start_evidence(payload, expected_identity(payload))
    assert normalized.runtime_commit == payload["runtime_code_commit"]
    assert normalized.source == payload["systemd_unit"]
    assert normalized.captured_at_utc == "2026-09-12T11:24:51Z"
    assert normalized.schema_version == 1
    assert normalized.evidence_kind == "systemd-transient-actual-start-evidence"


@pytest.mark.parametrize("field,value,reason", [
    ("runtime_commit", "6576f632b3f44eb68645bad4304665c0ea87512d", "ACTUAL_START_ALIAS_MIXED"),
    ("schema_version", 3, "ACTUAL_START_SCHEMA_UNSUPPORTED"),
    ("actual_start_time_utc", "2026-09-12T20:24:51+09:00", "ACTUAL_START_TIMESTAMP_NOT_UTC"),
])
def test_legacy_mutations_fail_closed(field: str, value: object, reason: str) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # For the alias-mixed test, add the canonical key alongside the legacy key
    if reason == "ACTUAL_START_ALIAS_MIXED":
        # We add 'runtime_commit' (canonical) to trigger ALIAS_MIXED
        payload["runtime_commit"] = value
    else:
        payload[field] = value
    with pytest.raises(ValueError, match=reason):
        normalize_actual_start_evidence(payload, expected_identity(payload))


# ── Canonical v2 schema ───────────────────────────────────────────────────

def test_canonical_v2_normalizes() -> None:
    payload = {
        "schema_version": 2,
        "evidence_kind": "systemd-transient-actual-start-evidence",
        "collector_epoch": "epoch-test",
        "collector_run_id": "run-test",
        "runtime_commit": "aabbcc",
        "runtime_config_fingerprint": "ddeeff",
        "actual_start_time_utc": "2026-01-01T00:00:00Z",
        "source": "test.service",
        "captured_at_utc": "2026-01-01T00:00:01Z",
    }
    identity = ActualStartIdentity(
        collector_epoch="epoch-test",
        collector_run_id="run-test",
        runtime_commit="aabbcc",
        runtime_config_fingerprint="ddeeff",
    )
    normalized = normalize_actual_start_evidence(payload, identity)
    assert normalized.schema_version == 2
    assert normalized.runtime_commit == "aabbcc"
    assert normalized.source == "test.service"


@pytest.mark.parametrize("field,value", [
    ("collector_epoch", "wrong-epoch"),
    ("collector_run_id", "wrong-run"),
    ("runtime_commit", "wrong-commit"),
    ("runtime_config_fingerprint", "wrong-fp"),
])
def test_canonical_v2_identity_mismatch(field: str, value: str) -> None:
    payload = {
        "schema_version": 2,
        "evidence_kind": "systemd-transient-actual-start-evidence",
        "collector_epoch": "epoch-test",
        "collector_run_id": "run-test",
        "runtime_commit": "aabbcc",
        "runtime_config_fingerprint": "ddeeff",
        "actual_start_time_utc": "2026-01-01T00:00:00Z",
        "source": "test.service",
        "captured_at_utc": "2026-01-01T00:00:01Z",
    }
    identity = ActualStartIdentity(
        collector_epoch="epoch-test",
        collector_run_id="run-test",
        runtime_commit="aabbcc",
        runtime_config_fingerprint="ddeeff",
    )
    payload[field] = value
    with pytest.raises(ValueError, match="ACTUAL_START_"):
        normalize_actual_start_evidence(payload, identity)


@pytest.mark.parametrize("ts", [
    "2026-01-01T00:00:00",          # naive (no offset)
    "2026-01-01T00:00:00+09:00",   # non-zero offset
    "not-a-timestamp",
    None,
])
def test_canonical_v2_bad_timestamps(ts: object) -> None:
    payload = {
        "schema_version": 2,
        "evidence_kind": "systemd-transient-actual-start-evidence",
        "collector_epoch": "epoch-test",
        "collector_run_id": "run-test",
        "runtime_commit": "aabbcc",
        "runtime_config_fingerprint": "ddeeff",
        "actual_start_time_utc": ts,
        "source": "test.service",
        "captured_at_utc": "2026-01-01T00:00:01Z",
    }
    identity = ActualStartIdentity(
        collector_epoch="epoch-test",
        collector_run_id="run-test",
        runtime_commit="aabbcc",
        runtime_config_fingerprint="ddeeff",
    )
    with pytest.raises(ValueError, match="ACTUAL_START_TIMESTAMP_NOT_UTC"):
        normalize_actual_start_evidence(payload, identity)


def test_unsupported_schema_version() -> None:
    payload = {
        "schema_version": 99,
        "evidence_kind": "systemd-transient-actual-start-evidence",
    }
    identity = ActualStartIdentity(
        collector_epoch="e", collector_run_id="r", runtime_commit="c", runtime_config_fingerprint="f"
    )
    with pytest.raises(ValueError, match="ACTUAL_START_SCHEMA_UNSUPPORTED"):
        normalize_actual_start_evidence(payload, identity)


def test_wrong_evidence_kind() -> None:
    payload = {
        "schema_version": 1,
        "evidence_kind": "wrong-kind",
    }
    identity = ActualStartIdentity(
        collector_epoch="e", collector_run_id="r", runtime_commit="c", runtime_config_fingerprint="f"
    )
    with pytest.raises(ValueError, match="ACTUAL_START_SCHEMA_UNSUPPORTED"):
        normalize_actual_start_evidence(payload, identity)


@pytest.mark.parametrize("drop_key", [
    "runtime_commit",
    "source",
    "captured_at_utc",
    "actual_start_time_utc",
    "runtime_config_fingerprint",
])
def test_canonical_v2_missing_required_key(drop_key: str) -> None:
    payload = {
        "schema_version": 2,
        "evidence_kind": "systemd-transient-actual-start-evidence",
        "collector_epoch": "epoch-test",
        "collector_run_id": "run-test",
        "runtime_commit": "aabbcc",
        "runtime_config_fingerprint": "ddeeff",
        "actual_start_time_utc": "2026-01-01T00:00:00Z",
        "source": "test.service",
        "captured_at_utc": "2026-01-01T00:00:01Z",
    }
    identity = ActualStartIdentity(
        collector_epoch="epoch-test",
        collector_run_id="run-test",
        runtime_commit="aabbcc",
        runtime_config_fingerprint="ddeeff",
    )
    del payload[drop_key]
    with pytest.raises(ValueError, match="ACTUAL_START_SCHEMA_UNSUPPORTED"):
        normalize_actual_start_evidence(payload, identity)


@pytest.mark.parametrize("drop_key", [
    "runtime_code_commit",
    "systemd_unit",
    "observed_post_launch_utc",
    "timing_contract",
])
def test_legacy_v1_missing_required_key(drop_key: str) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    del payload[drop_key]
    with pytest.raises(ValueError, match="ACTUAL_START_SCHEMA_UNSUPPORTED"):
        normalize_actual_start_evidence(payload, expected_identity(
            json.loads(FIXTURE.read_text(encoding="utf-8"))  # use original for identity
        ))


def test_canonical_v2_with_legacy_alias_fails() -> None:
    """A canonical v2 payload that ALSO contains legacy alias keys must fail.
    Spec §4.4: reject duplicate semantic fields.
    """
    payload = {
        "schema_version": 2,
        "evidence_kind": "systemd-transient-actual-start-evidence",
        "collector_epoch": "epoch-test",
        "collector_run_id": "run-test",
        "runtime_commit": "aabbcc",
        "runtime_code_commit": "aabbcc",  # legacy alias present alongside canonical
        "runtime_config_fingerprint": "ddeeff",
        "actual_start_time_utc": "2026-01-01T00:00:00Z",
        "source": "test.service",
        "captured_at_utc": "2026-01-01T00:00:01Z",
    }
    identity = ActualStartIdentity(
        collector_epoch="epoch-test",
        collector_run_id="run-test",
        runtime_commit="aabbcc",
        runtime_config_fingerprint="ddeeff",
    )
    with pytest.raises(ValueError, match="ACTUAL_START_SCHEMA_UNSUPPORTED"):
        normalize_actual_start_evidence(payload, identity)
