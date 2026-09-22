#!/usr/bin/env python3
"""Read-only T1/T2 SHA-256 observer for naturally finalized cohort receipts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def append_event(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def valid_final_receipt(path: Path, cohort: str) -> tuple[bytes, dict] | None:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("cohort") != cohort:
        return None
    if payload.get("cohort_qualification") != "QUALIFYING_FULL_HOUR":
        return None
    if payload.get("status") not in {"PASS", "FAIL"}:
        return None
    if not payload.get("finalized_at_utc"):
        return None
    return raw, payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--epoch", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--cohort", action="append", required=True)
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--t2-delay-seconds", type=float, default=600.0)
    parser.add_argument("--deadline-utc", required=True)
    args = parser.parse_args()

    deadline = datetime.fromisoformat(args.deadline_utc.replace("Z", "+00:00"))
    events_path = args.audit_dir / "receipt-hash-observations.jsonl"
    state_path = args.audit_dir / "receipt-hash-state.json"
    state: dict[str, dict] = {cohort: {} for cohort in args.cohort}
    last_seen_hash: dict[str, str] = {}

    while utc_now() <= deadline:
        for cohort in args.cohort:
            entry = state[cohort]
            if entry.get("t2"):
                continue
            path = args.data_root / "archive-receipts" / f"cohort_{cohort}_finalized.json"
            observed = valid_final_receipt(path, cohort)
            if observed is None:
                continue
            raw, payload = observed
            digest = hashlib.sha256(raw).hexdigest()
            now = utc_now()
            if not entry.get("t1"):
                event = {
                    "observation": "T1",
                    "observed_at_utc": now.isoformat(),
                    "epoch": args.epoch,
                    "run_id": args.run_id,
                    "cohort": cohort,
                    "receipt_path": str(path),
                    "receipt_sha256": digest,
                    "receipt_status": payload["status"],
                    "finalized_at_utc": payload["finalized_at_utc"],
                }
                entry["t1"] = event
                last_seen_hash[cohort] = digest
                append_event(events_path, event)
                atomic_json(state_path, state)
                continue

            if last_seen_hash.get(cohort) != digest:
                event = {
                    "observation": "HASH_CHANGE",
                    "observed_at_utc": now.isoformat(),
                    "epoch": args.epoch,
                    "run_id": args.run_id,
                    "cohort": cohort,
                    "receipt_path": str(path),
                    "previous_sha256": last_seen_hash.get(cohort),
                    "receipt_sha256": digest,
                }
                append_event(events_path, event)
                last_seen_hash[cohort] = digest

            t1_time = datetime.fromisoformat(entry["t1"]["observed_at_utc"])
            if (now - t1_time).total_seconds() >= args.t2_delay_seconds:
                event = {
                    "observation": "T2",
                    "observed_at_utc": now.isoformat(),
                    "epoch": args.epoch,
                    "run_id": args.run_id,
                    "cohort": cohort,
                    "receipt_path": str(path),
                    "receipt_sha256": digest,
                    "t1_sha256": entry["t1"]["receipt_sha256"],
                    "immutable": digest == entry["t1"]["receipt_sha256"],
                    "elapsed_since_t1_seconds": (now - t1_time).total_seconds(),
                }
                entry["t2"] = event
                append_event(events_path, event)
                atomic_json(state_path, state)

        if all(entry.get("t2") for entry in state.values()):
            atomic_json(args.audit_dir / "receipt-hash-complete.json", {
                "schema_version": 1,
                "completed_at_utc": utc_now().isoformat(),
                "epoch": args.epoch,
                "run_id": args.run_id,
                "state": state,
            })
            return 0
        time.sleep(args.poll_seconds)

    atomic_json(args.audit_dir / "receipt-hash-incomplete.json", {
        "schema_version": 1,
        "deadline_utc": deadline.isoformat(),
        "observed_at_utc": utc_now().isoformat(),
        "epoch": args.epoch,
        "run_id": args.run_id,
        "state": state,
    })
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
