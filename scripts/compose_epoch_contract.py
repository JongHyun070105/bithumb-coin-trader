#!/usr/bin/env python3
"""Offline Sealed Epoch Contract Composer for 72-Hour Soak.

Composes epoch_contract.json strictly from tracked/frozen runtime seals
and pre-launch provenance artifacts without touching live AWS infrastructure.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
SCRIPTS_DIR = ROOT / "scripts"
for d in (ROOT, SRC_DIR, SCRIPTS_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

try:
    from scripts.evidence_contract import canonical_sha256, file_sha256 as _file_sha256
except ModuleNotFoundError:
    from evidence_contract import canonical_sha256, file_sha256 as _file_sha256

from bithumb_coin_trader.actual_start_evidence import (
    ActualStartIdentity,
    normalize_actual_start_evidence,
)
from bithumb_coin_trader.qualification_schedule import (
    build_qualification_schedule,
    format_utc,
    parse_utc,
)


def compose_epoch_contract(
    runtime_seal_path: Path,
    launch_provenance_path: Path,
    output_path: Path | None = None,
    actual_start_evidence_path: Path | None = None,
    synthetic_actual_start_time_utc: str | None = None,
    strict: bool = True,
    schema_version: int | None = None,
) -> dict[str, Any]:
    if not runtime_seal_path.exists():
        raise FileNotFoundError(f"Runtime seal not found: {runtime_seal_path}")
    if not launch_provenance_path.exists():
        raise FileNotFoundError(f"Launch provenance not found: {launch_provenance_path}")

    runtime_seal = json.loads(runtime_seal_path.read_text(encoding="utf-8"))
    launch_prov = json.loads(launch_provenance_path.read_text(encoding="utf-8"))

    # Cross-check identities
    runtime_commit = runtime_seal.get("runtime_software_commit")
    if not runtime_commit:
        raise ValueError("SEAL_MISSING_RUNTIME_COMMIT")

    prov_commit = launch_prov.get("runtime_code_commit")
    if not prov_commit:
        raise ValueError("PROV_MISSING_RUNTIME_COMMIT")

    if runtime_commit != prov_commit:
        raise ValueError(
            f"RUNTIME_COMMIT_MISMATCH: runtime seal commit '{runtime_commit}' != launch provenance commit '{prov_commit}'"
        )

    runtime_fingerprint = launch_prov.get("runtime_config_fingerprint")
    if not runtime_fingerprint or (len(runtime_fingerprint) != 64 and strict and not runtime_fingerprint.startswith("fp-")):
        raise ValueError(f"RUNTIME_FINGERPRINT_MISMATCH: Invalid runtime fingerprint: {runtime_fingerprint}")

    seal_sha = _file_sha256(runtime_seal_path)
    prov_seal_sha = launch_prov.get("runtime_config_seal_sha256")
    if prov_seal_sha and seal_sha != prov_seal_sha:
        raise ValueError(
            f"SEAL_HASH_MISMATCH: computed runtime seal SHA '{seal_sha}' != launch provenance record '{prov_seal_sha}'"
        )

    prov_sha = _file_sha256(launch_provenance_path)

    collector_epoch = launch_prov.get("collector_epoch")
    collector_run_id = launch_prov.get("collector_run_id")
    if not collector_epoch or not collector_run_id:
        raise ValueError("MISSING_EPOCH_OR_RUN_ID: collector_epoch and collector_run_id are required")

    duration_sec = launch_prov.get("duration_seconds", 259200)
    if duration_sec <= 0:
        raise ValueError(f"INVALID_DURATION: Duration seconds must be positive, got {duration_sec}")

    # P0 / P0.1: Actual start time MUST come from explicit execution-start evidence.
    # NEVER use launch_prov["created_at_utc"] as actual start time!
    actual_start_str = None
    start_evidence_sha = ""
    if actual_start_evidence_path:
        if not actual_start_evidence_path.exists():
            raise FileNotFoundError(f"ACTUAL_START_EVIDENCE_MISSING: Evidence file not found: {actual_start_evidence_path}")
        start_evidence_sha = _file_sha256(actual_start_evidence_path)
        ev_data = json.loads(actual_start_evidence_path.read_text(encoding="utf-8"))
        expected = ActualStartIdentity(
            collector_epoch=collector_epoch,
            collector_run_id=collector_run_id,
            runtime_commit=runtime_commit,
            runtime_config_fingerprint=runtime_fingerprint,
        )
        normalized = normalize_actual_start_evidence(ev_data, expected)
        actual_start_str = normalized.actual_start_time_utc
    elif synthetic_actual_start_time_utc:
        if strict:
            raise ValueError("ACTUAL_START_EVIDENCE_MISSING: synthetic timestamp forbidden in official mode")
        actual_start_str = synthetic_actual_start_time_utc

    if not actual_start_str:
        raise ValueError(
            "ACTUAL_START_EVIDENCE_MISSING: Authoritative actual-start evidence artifact required. "
            "Do NOT infer actual start from provenance file created_at_utc."
        )

    start_dt = datetime.fromisoformat(actual_start_str.replace("Z", "+00:00"))
    end_dt = start_dt + timedelta(seconds=duration_sec)

    feeds = runtime_seal.get("feeds", {})
    bithumb_mkts = feeds.get("bithumb_markets", [])
    binance_syms = feeds.get("binance_symbols", [])
    upbit_mkts = feeds.get("upbit_markets", [])

    feed_universe: list[dict[str, str]] = []
    for m in bithumb_mkts:
        for s in ("orderbook", "trade", "ticker"):
            feed_universe.append({"exchange": "bithumb", "stream": s, "market": m})
    for m in binance_syms:
        for s in ("orderbook", "trade"):
            feed_universe.append({"exchange": "binance", "stream": s, "market": m})
    for m in upbit_mkts:
        for s in ("orderbook", "trade"):
            feed_universe.append({"exchange": "upbit", "stream": s, "market": m})

    if len(feed_universe) != 76 and strict:
        raise ValueError(f"FEED_UNIVERSE_MISMATCH: Expected 76 feeds, got {len(feed_universe)}")

    target_schema = schema_version
    if target_schema is None:
        if (
            launch_prov.get("contract_type") == "OFFICIAL_30H_V3_COVERAGE_CONTRACT"
            or runtime_seal.get("contract_type") == "OFFICIAL_30H_V3_COVERAGE_CONTRACT"
            or launch_prov.get("schema_version") == 2
            or runtime_seal.get("schema_version") == 2
            or launch_prov.get("duration_seconds") == 111600
            or launch_prov.get("maximum_collection_window_seconds") == 111600
        ):
            target_schema = 2
        else:
            target_schema = 1

    if target_schema == 2:
        if launch_prov.get("duration_seconds") == 108000:
            raise ValueError(
                "V3_DERIVED_108000_END_FORBIDDEN: Fixed 108000s duration is forbidden in V3 qualification schedule"
            )

        req_hours = launch_prov.get("required_qualifying_full_hours", 30)
        max_window = launch_prov.get("maximum_collection_window_seconds", 111600)
        if req_hours != 30:
            raise ValueError(f"V3_QUALIFICATION_HOURS_INVALID: Expected 30 qualifying hours, got {req_hours}")
        if max_window != 111600:
            raise ValueError(f"V3_QUALIFICATION_WINDOW_INVALID: Expected 111600s max window, got {max_window}")

        sealed_heartbeat_policy = runtime_seal.get("heartbeat_policy")
        if not sealed_heartbeat_policy:
            sealed_heartbeat_policy = {
                "heartbeat_probe_interval_seconds": 10,
                "heartbeat_timeout_seconds": 10,
                "max_allowed_heartbeat_gap_seconds": {
                    "bithumb": 30,
                    "binance": 30,
                    "upbit": 30,
                },
            }
        gaps = sealed_heartbeat_policy.get("max_allowed_heartbeat_gap_seconds", {})
        for ex in ("bithumb", "binance", "upbit"):
            if ex not in gaps or not isinstance(gaps[ex], (int, float)) or gaps[ex] <= 0:
                raise ValueError(f"INVALID_HEARTBEAT_POLICY: Missing or invalid gap threshold for {ex}")

        if actual_start_str.endswith("Z") or actual_start_str.endswith("+00:00"):
            actual_utc = parse_utc(actual_start_str)
        else:
            actual_utc = datetime.fromisoformat(actual_start_str)
            if actual_utc.tzinfo is None:
                actual_utc = actual_utc.replace(tzinfo=timezone.utc)

        schedule = build_qualification_schedule(actual_utc, 0.0, req_hours, max_window)

        v3_contract: dict[str, Any] = {
            "schema_version": 2,
            "contract_type": "OFFICIAL_30H_V3_COVERAGE_CONTRACT",
            "collector_epoch": collector_epoch,
            "collector_run_id": collector_run_id,
            "actual_start_time_utc": format_utc(actual_utc),
            "qualification_start_utc": schedule.qualification_start_utc,
            "qualification_end_utc": schedule.collection_stop_utc,
            "required_qualifying_full_hours": 30,
            "maximum_collection_window_seconds": 111600,
            "candidate_cohorts": list(schedule.candidate_cohorts),
            "expected_coverage_slots_per_cohort": 76,
            "heartbeat_policy": sealed_heartbeat_policy,
            "feed_universe": feed_universe,
            "require_coverage_receipts": True,
            "require_state_dependent_fullscan": True,
            "runtime_software_commit": runtime_commit,
            "runtime_fingerprint": runtime_fingerprint,
            "environment_id": launch_prov.get("environment_id", "aws-apne2-research"),
            "raw_schema_version": runtime_seal.get("raw_schema_version", 4),
            "runtime_seal_path": str(runtime_seal_path),
            "runtime_seal_sha256": seal_sha,
            "launch_provenance_path": str(launch_provenance_path),
            "launch_provenance_sha256": prov_sha,
            "actual_start_evidence_path": str(actual_start_evidence_path) if actual_start_evidence_path else "",
            "actual_start_evidence_file_sha256": start_evidence_sha,
            "feed_count": len(feed_universe),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        v3_contract["contract_sha256"] = canonical_sha256(v3_contract)

        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(v3_contract, indent=2), encoding="utf-8")
            print(f"Wrote epoch contract to {output_path} (contract_sha256={v3_contract['contract_sha256'][:16]})")

        return v3_contract

    contract: dict[str, Any] = {
        "schema_version": 1,
        "contract_type": "OFFICIAL_72H_SOAK_CONTRACT",
        "collector_epoch": collector_epoch,
        "collector_run_id": collector_run_id,
        "runtime_software_commit": runtime_commit,
        "runtime_fingerprint": runtime_fingerprint,
        "start_time_utc": start_dt.isoformat(),
        "actual_start_time_utc": start_dt.isoformat(),
        "expected_end_time_utc": end_dt.isoformat(),
        "duration_seconds": duration_sec,
        "environment_id": launch_prov.get("environment_id", "aws-apne2-research"),
        "raw_schema_version": runtime_seal.get("raw_schema_version", 4),
        "runtime_seal_path": str(runtime_seal_path),
        "runtime_seal_sha256": seal_sha,
        "launch_provenance_path": str(launch_provenance_path),
        "launch_provenance_sha256": prov_sha,
        "actual_start_evidence_path": str(actual_start_evidence_path) if actual_start_evidence_path else "",
        "actual_start_evidence_file_sha256": start_evidence_sha,
        "feed_count": len(feed_universe),
        "feed_universe": feed_universe,
        "require_receipts": True,
        "require_fullscan": True,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    # Compute deterministic contract hash
    contract["contract_sha256"] = canonical_sha256(contract)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(contract, indent=2), encoding="utf-8")
        print(f"Wrote epoch contract to {output_path} (contract_sha256={contract['contract_sha256'][:16]})")

    return contract


def main() -> int:
    parser = argparse.ArgumentParser(description="Compose official epoch contract from sealed runtime artifacts.")
    parser.add_argument("--runtime-seal", type=Path, required=True, help="Path to runtime.json seal")
    parser.add_argument("--launch-provenance", type=Path, required=True, help="Path to launch-provenance.json")
    parser.add_argument("--actual-start-evidence", type=Path, default=None, help="Path to actual start evidence JSON")
    parser.add_argument("--synthetic-actual-start", type=str, default=None, help="Synthetic actual start ISO timestamp")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Output epoch_contract.json path")
    parser.add_argument("--strict", action="store_true", default=True, help="Enforce strict contract checks")
    parser.add_argument("--schema-version", type=int, default=None, choices=[1, 2], help="Contract schema version")

    args = parser.parse_args()
    try:
        compose_epoch_contract(
            runtime_seal_path=args.runtime_seal,
            launch_provenance_path=args.launch_provenance,
            output_path=args.output,
            actual_start_evidence_path=args.actual_start_evidence,
            synthetic_actual_start_time_utc=args.synthetic_actual_start,
            strict=args.strict,
            schema_version=args.schema_version,
        )
        return 0
    except Exception as e:
        print(f"ERROR: Failed composing epoch contract: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
