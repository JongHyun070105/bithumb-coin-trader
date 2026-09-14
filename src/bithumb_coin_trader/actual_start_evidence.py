"""Strict actual-start evidence normalization with exact V2 legacy compatibility.

Two schemas are supported:
  schema_version=2, evidence_kind=EVIDENCE_KIND  →  canonical v2 (V3 forward)
  schema_version=1, evidence_kind=EVIDENCE_KIND  →  V2 legacy v1 (read-only adapter)

All other combinations are rejected with ACTUAL_START_SCHEMA_UNSUPPORTED.
Mixed canonical/legacy aliases are rejected with ACTUAL_START_ALIAS_MIXED.
Wrong identity fields are rejected with ACTUAL_START_{field}_MISMATCH.
Invalid timestamps are rejected with ACTUAL_START_TIMESTAMP_NOT_UTC.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

__all__ = [
    "ActualStartIdentity",
    "NormalizedActualStartEvidence",
    "normalize_actual_start_evidence",
]

EVIDENCE_KIND = "systemd-transient-actual-start-evidence"

# ── Canonical v2 schema ────────────────────────────────────────────────────
V2_CANONICAL_REQUIRED_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "evidence_kind",
    "collector_epoch",
    "collector_run_id",
    "runtime_commit",
    "runtime_config_fingerprint",
    "actual_start_time_utc",
    "source",
    "captured_at_utc",
})
V2_CANONICAL_ALLOWED_KEYS: frozenset[str] = V2_CANONICAL_REQUIRED_KEYS

# ── V2 legacy v1 schema (exact V2 run artifact) ───────────────────────────
V2_LEGACY_V1_REQUIRED_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "evidence_kind",
    "collector_epoch",
    "collector_run_id",
    "runtime_code_commit",       # maps to runtime_commit
    "runtime_git_tree",
    "runtime_config_fingerprint",
    "v2_preparation_commit",
    "authorization_commit",
    "authorization_evidence_path",
    "systemd_unit",              # maps to source
    "main_pid",
    "actual_start_time_utc",
    "observed_pre_launch_utc",
    "observed_post_launch_utc",  # maps to captured_at_utc
    "systemd_start_timestamp",
    "systemd_until_timestamp",
    "launch_exit_code",
    "timing_contract",
    "scientific_status",
})
V2_LEGACY_V1_ALLOWED_KEYS: frozenset[str] = V2_LEGACY_V1_REQUIRED_KEYS

# Canonical names that must NOT appear in a legacy v1 payload
_CANONICAL_ALIASES = frozenset({"runtime_commit", "source", "captured_at_utc"})


@dataclass(frozen=True)
class ActualStartIdentity:
    collector_epoch: str
    collector_run_id: str
    runtime_commit: str
    runtime_config_fingerprint: str


@dataclass(frozen=True)
class NormalizedActualStartEvidence:
    schema_version: int
    evidence_kind: str
    collector_epoch: str
    collector_run_id: str
    runtime_commit: str
    runtime_config_fingerprint: str
    actual_start_time_utc: str   # canonical: Z suffix
    source: str
    captured_at_utc: str         # canonical: Z suffix


def normalize_actual_start_evidence(
    payload: Mapping[str, object],
    expected: ActualStartIdentity,
) -> NormalizedActualStartEvidence:
    """Normalize payload to NormalizedActualStartEvidence or raise ValueError."""
    discriminator = (payload.get("schema_version"), payload.get("evidence_kind"))
    if discriminator == (2, EVIDENCE_KIND):
        return _normalize_canonical_v2(payload, expected)
    if discriminator == (1, EVIDENCE_KIND):
        return _normalize_v2_legacy_v1(payload, expected)
    raise ValueError("ACTUAL_START_SCHEMA_UNSUPPORTED")


# ── Private helpers ────────────────────────────────────────────────────────

def _require_str(payload: Mapping[str, object], key: str, reason: str) -> str:
    val = payload.get(key)
    if not isinstance(val, str) or not val:
        raise ValueError(reason)
    return val


def _parse_utc_timestamp(value: object, field: str) -> str:
    """Accept Z or explicit zero offset; reject naive or non-zero offset."""
    if not isinstance(value, str):
        raise ValueError(f"ACTUAL_START_TIMESTAMP_NOT_UTC: {field} must be a string")
    normalized = value
    if value.endswith("Z"):
        normalized = value  # acceptable
    elif value.endswith("+00:00"):
        normalized = value[:-6] + "Z"  # normalize to Z
    else:
        raise ValueError(f"ACTUAL_START_TIMESTAMP_NOT_UTC: {field} must end with Z or +00:00")
    try:
        dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"ACTUAL_START_TIMESTAMP_NOT_UTC: {field} unparseable: {exc}") from exc
    offset = dt.utcoffset()
    if offset is None:
        raise ValueError(f"ACTUAL_START_TIMESTAMP_NOT_UTC: {field} is naive")
    if offset.total_seconds() != 0:
        raise ValueError(f"ACTUAL_START_TIMESTAMP_NOT_UTC: {field} has non-zero offset")
    return normalized


def _check_identity(
    payload: Mapping[str, object],
    expected: ActualStartIdentity,
    epoch_key: str,
    run_id_key: str,
    commit_key: str,
    fingerprint_key: str,
) -> None:
    for key, expected_val, code in (
        (epoch_key, expected.collector_epoch, "COLLECTOR_EPOCH"),
        (run_id_key, expected.collector_run_id, "COLLECTOR_RUN_ID"),
        (commit_key, expected.runtime_commit, "RUNTIME_COMMIT"),
        (fingerprint_key, expected.runtime_config_fingerprint, "RUNTIME_CONFIG_FINGERPRINT"),
    ):
        if payload.get(key) != expected_val:
            raise ValueError(f"ACTUAL_START_{code}_MISMATCH")


def _normalize_canonical_v2(
    payload: Mapping[str, object],
    expected: ActualStartIdentity,
) -> NormalizedActualStartEvidence:
    """Validate and normalize a canonical schema v2 payload."""
    keys = set(payload.keys())
    missing = V2_CANONICAL_REQUIRED_KEYS - keys
    if missing:
        raise ValueError(f"ACTUAL_START_SCHEMA_UNSUPPORTED: missing keys {sorted(missing)}")
    extra = keys - V2_CANONICAL_ALLOWED_KEYS
    if extra:
        raise ValueError(f"ACTUAL_START_SCHEMA_UNSUPPORTED: unexpected keys {sorted(extra)}")
    _check_identity(payload, expected, "collector_epoch", "collector_run_id", "runtime_commit", "runtime_config_fingerprint")
    source = _require_str(payload, "source", "ACTUAL_START_SOURCE_MISSING")
    actual_start = _parse_utc_timestamp(payload.get("actual_start_time_utc"), "actual_start_time_utc")
    captured_at = _parse_utc_timestamp(payload.get("captured_at_utc"), "captured_at_utc")
    return NormalizedActualStartEvidence(
        schema_version=2,
        evidence_kind=EVIDENCE_KIND,
        collector_epoch=str(payload["collector_epoch"]),
        collector_run_id=str(payload["collector_run_id"]),
        runtime_commit=str(payload["runtime_commit"]),
        runtime_config_fingerprint=str(payload["runtime_config_fingerprint"]),
        actual_start_time_utc=actual_start,
        source=source,
        captured_at_utc=captured_at,
    )


def _normalize_v2_legacy_v1(
    payload: Mapping[str, object],
    expected: ActualStartIdentity,
) -> NormalizedActualStartEvidence:
    """Validate and normalize a V2 legacy schema v1 payload."""
    keys = set(payload.keys())
    # Reject canonical aliases in a legacy payload
    mixed = _CANONICAL_ALIASES & keys
    if mixed:
        raise ValueError(f"ACTUAL_START_ALIAS_MIXED: canonical keys in legacy payload {sorted(mixed)}")
    missing = V2_LEGACY_V1_REQUIRED_KEYS - keys
    if missing:
        raise ValueError(f"ACTUAL_START_SCHEMA_UNSUPPORTED: missing legacy keys {sorted(missing)}")
    extra = keys - V2_LEGACY_V1_ALLOWED_KEYS
    if extra:
        raise ValueError(f"ACTUAL_START_SCHEMA_UNSUPPORTED: unexpected keys in legacy payload {sorted(extra)}")
    # Identity check using legacy field names
    _check_identity(payload, expected, "collector_epoch", "collector_run_id", "runtime_code_commit", "runtime_config_fingerprint")
    source = _require_str(payload, "systemd_unit", "ACTUAL_START_SOURCE_MISSING")
    actual_start = _parse_utc_timestamp(payload.get("actual_start_time_utc"), "actual_start_time_utc")
    captured_at = _parse_utc_timestamp(payload.get("observed_post_launch_utc"), "observed_post_launch_utc")
    return NormalizedActualStartEvidence(
        schema_version=1,
        evidence_kind=EVIDENCE_KIND,
        collector_epoch=str(payload["collector_epoch"]),
        collector_run_id=str(payload["collector_run_id"]),
        runtime_commit=str(payload["runtime_code_commit"]),
        runtime_config_fingerprint=str(payload["runtime_config_fingerprint"]),
        actual_start_time_utc=actual_start,
        source=source,
        captured_at_utc=captured_at,
    )
