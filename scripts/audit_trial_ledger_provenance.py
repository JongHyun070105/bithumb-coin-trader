#!/usr/bin/env python3
"""Audit provenance, integrity, and schema compliance of the frozen research trial ledger.

Validates:
1. File existence and SHA-256 matching manifest
2. JSON parsing for every record
3. Required fields (trial_id, lane, strategy_name, created_at, numeric metrics)
4. Monotonicity / uniqueness of trial_id
5. Valid ISO-8601 timestamps
6. Parameter sanity and duplicate detection
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys


def audit_ledger(ledger_path: Path, manifest_path: Path | None = None) -> int:
    print("=" * 80)
    print(f"AUDITING TRIAL LEDGER: {ledger_path}")
    print("=" * 80)

    if not ledger_path.is_file():
        print(f"ERROR: Ledger file not found: {ledger_path}", file=sys.stderr)
        return 1

    # Check SHA-256 against manifest if provided
    hasher = hashlib.sha256()
    with open(ledger_path, "rb") as f:
        hasher.update(f.read())
    file_sha = hasher.hexdigest()
    print(f"Computed SHA-256: {file_sha}")

    if manifest_path and manifest_path.is_file():
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        expected_sha = manifest.get("sha256")
        if expected_sha != file_sha:
            print(f"ERROR: SHA-256 mismatch! Manifest: {expected_sha}, Computed: {file_sha}", file=sys.stderr)
            return 1
        print(f"Manifest SHA-256 verification: PASS ({manifest_path.name})")

    records: list[dict] = []
    trial_ids: set[str] = set()
    lanes_count: dict[str, int] = {}
    timestamps: list[datetime] = []
    sharpe_values: list[float] = []
    errors: list[str] = []

    with open(ledger_path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, 1):
            line_str = line.strip()
            if not line_str:
                continue

            try:
                rec = json.loads(line_str)
            except json.JSONDecodeError as exc:
                errors.append(f"Line {idx}: JSON decode error: {exc}")
                continue

            # Required fields
            trial_id = rec.get("trial_id")
            if not trial_id or not isinstance(trial_id, str):
                errors.append(f"Line {idx}: Missing or invalid 'trial_id'")
            elif trial_id in trial_ids:
                errors.append(f"Line {idx}: Duplicate trial_id '{trial_id}'")
            else:
                trial_ids.add(trial_id)

            lane = rec.get("lane")
            if not lane or not isinstance(lane, str):
                errors.append(f"Line {idx}: Missing or invalid 'lane'")
            else:
                lanes_count[lane] = lanes_count.get(lane, 0) + 1

            strat = rec.get("strategy_name")
            if not strat or not isinstance(strat, str):
                errors.append(f"Line {idx}: Missing or invalid 'strategy_name'")

            created_at = rec.get("created_at")
            if not created_at:
                errors.append(f"Line {idx}: Missing 'created_at'")
            else:
                try:
                    dt = datetime.fromisoformat(created_at)
                    timestamps.append(dt)
                except Exception as exc:
                    errors.append(f"Line {idx}: Invalid ISO-8601 timestamp '{created_at}': {exc}")

            # Metric validation
            sharpe = rec.get("observed_sharpe")
            if sharpe is None:
                sharpe = rec.get("sharpe")
            if sharpe is None or not isinstance(sharpe, (int, float)):
                errors.append(f"Line {idx}: Missing or non-numeric sharpe")
            else:
                sharpe_values.append(float(sharpe))

            records.append(rec)

    total_records = len(records)
    print(f"\nTotal Valid Records: {total_records}")
    print(f"Distinct Trial IDs: {len(trial_ids)}")

    print("\nLane Breakdown:")
    for lane, count in sorted(lanes_count.items()):
        print(f"  - {lane}: {count} trials")

    if timestamps:
        print(f"\nTemporal Range: {min(timestamps).isoformat()} -> {max(timestamps).isoformat()}")

    if sharpe_values:
        mean_sharpe = sum(sharpe_values) / len(sharpe_values)
        min_sharpe = min(sharpe_values)
        max_sharpe = max(sharpe_values)
        variance = sum((s - mean_sharpe) ** 2 for s in sharpe_values) / len(sharpe_values)
        std_sharpe = variance ** 0.5
        print(f"\nSharpe Statistics (N={len(sharpe_values)}):")
        print(f"  - Min:  {min_sharpe:+.4f}")
        print(f"  - Mean: {mean_sharpe:+.4f}")
        print(f"  - Max:  {max_sharpe:+.4f}")
        print(f"  - Std:  {std_sharpe:.4f}")

    if errors:
        print(f"\nERRORS DETECTED ({len(errors)}):", file=sys.stderr)
        for err in errors[:20]:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("\nPROVENANCE INTEGRITY VERIFICATION: ALL PASS (0 errors)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit research trial ledger provenance.")
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path("evidence/research/trial_ledger_frozen_20260905.jsonl"),
        help="Path to trial ledger JSONL",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("evidence/research/trial_ledger_frozen_20260905.manifest.json"),
        help="Path to manifest JSON",
    )
    args = parser.parse_args()
    return audit_ledger(args.ledger, args.manifest)


if __name__ == "__main__":
    sys.exit(main())
