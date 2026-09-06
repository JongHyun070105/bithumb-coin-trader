#!/usr/bin/env python3
"""Offline Sealed Epoch Contract Composer for 72-Hour Soak.

Composes epoch_contract.json strictly from tracked/frozen runtime seals
and pre-launch provenance artifacts without touching live AWS infrastructure.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


def _file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compose_epoch_contract(
    runtime_seal_path: Path,
    launch_provenance_path: Path,
    output_path: Path | None = None,
    strict: bool = True,
) -> dict[str, Any]:
    if not runtime_seal_path.exists():
        raise FileNotFoundError(f"Runtime seal not found: {runtime_seal_path}")
    if not launch_provenance_path.exists():
        raise FileNotFoundError(f"Launch provenance not found: {launch_provenance_path}")

    runtime_seal = json.loads(runtime_seal_path.read_text(encoding="utf-8"))
    launch_prov = json.loads(launch_provenance_path.read_text(encoding="utf-8"))

    # Cross-check identities
    runtime_commit = runtime_seal.get("runtime_software_commit")
    prov_commit = launch_prov.get("runtime_code_commit")
    if runtime_commit != prov_commit:
        raise ValueError(
            f"COMMIT_MISMATCH: runtime seal commit '{runtime_commit}' != launch provenance commit '{prov_commit}'"
        )

    runtime_fingerprint = launch_prov.get("runtime_config_fingerprint")
    if not runtime_fingerprint or len(runtime_fingerprint) != 64:
        raise ValueError(f"INVALID_FINGERPRINT: Invalid runtime fingerprint: {runtime_fingerprint}")

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

    duration_sec = launch_prov.get("duration_seconds") or runtime_seal.get("duration_seconds")
    if not duration_sec or duration_sec <= 0:
        raise ValueError(f"INVALID_DURATION: Duration seconds must be positive, got {duration_sec}")

    start_time_str = launch_prov.get("created_at_utc")
    if not start_time_str:
        raise ValueError("MISSING_START_TIME: created_at_utc missing in launch provenance")

    start_dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
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

    contract: dict[str, Any] = {
        "schema_version": 1,
        "contract_type": "OFFICIAL_72H_SOAK_CONTRACT",
        "collector_epoch": collector_epoch,
        "collector_run_id": collector_run_id,
        "runtime_software_commit": runtime_commit,
        "runtime_fingerprint": runtime_fingerprint,
        "start_time_utc": start_dt.isoformat(),
        "expected_end_time_utc": end_dt.isoformat(),
        "duration_seconds": duration_sec,
        "environment_id": launch_prov.get("environment_id", "aws-apne2-research"),
        "raw_schema_version": runtime_seal.get("raw_schema_version", 4),
        "runtime_seal_path": str(runtime_seal_path),
        "runtime_seal_sha256": seal_sha,
        "launch_provenance_path": str(launch_provenance_path),
        "launch_provenance_sha256": prov_sha,
        "feed_count": len(feed_universe),
        "feed_universe": feed_universe,
        "require_receipts": True,
        "require_fullscan": True,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    # Compute deterministic contract hash
    canon_bytes = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
    contract["contract_sha256"] = hashlib.sha256(canon_bytes).hexdigest()

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(contract, indent=2), encoding="utf-8")
        print(f"Wrote epoch contract to {output_path} (contract_sha256={contract['contract_sha256'][:16]})")

    return contract


def main() -> int:
    parser = argparse.ArgumentParser(description="Compose official epoch contract from sealed runtime artifacts.")
    parser.add_argument("--runtime-seal", type=Path, required=True, help="Path to runtime.json seal")
    parser.add_argument("--launch-provenance", type=Path, required=True, help="Path to launch-provenance.json")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Output epoch_contract.json path")
    parser.add_argument("--strict", action="store_true", default=True, help="Enforce strict contract checks")

    args = parser.parse_args()
    try:
        compose_epoch_contract(
            runtime_seal_path=args.runtime_seal,
            launch_provenance_path=args.launch_provenance,
            output_path=args.output,
            strict=args.strict,
        )
        return 0
    except Exception as e:
        print(f"ERROR: Failed composing epoch contract: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
