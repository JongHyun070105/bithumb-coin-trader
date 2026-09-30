#!/usr/bin/env python3
"""Independently verify the native finalization event chain and frozen summary."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bithumb_coin_trader.finalization_trace import (  # noqa: E402
    TRACE_SCHEMA_VERSION,
    producer_source_sha256,
)

PASS = "PASS"
FAIL = "FAIL"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
FROZEN_B4_COMMIT = "b4d482363e2f988dad9c6d29053f97e1e4160883"


def _strict_json(text: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _event_hash(event: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical({key: value for key, value in event.items() if key != "event_sha256"})).hexdigest()


def _derived_fields(events: list[dict[str, Any]]) -> dict[str, Any]:
    scheduler_pending: Counter[str] = Counter()
    scheduler_failed: set[str] = set()
    scheduler_retries = 0
    slot_attempts: Counter[str] = Counter()
    finalized_slots: Counter[str] = Counter()
    recovery_count = 0
    duplicate_count = 0
    recovery_path = False
    terminalization = False
    closed_at: dict[str, set[str]] = defaultdict(set)
    evidence: dict[str, set[str]] = defaultdict(set)
    for event in events:
        kind = event.get("event_type")
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if kind == "scheduler_attempt_started":
            cohort = str(payload.get("cohort", ""))
            if scheduler_pending[cohort] or cohort in scheduler_failed:
                scheduler_retries += 1
                scheduler_failed.discard(cohort)
            scheduler_pending[cohort] += 1
        elif kind in ("scheduler_attempt_failed", "scheduler_attempt_completed"):
            cohort = str(payload.get("cohort", ""))
            if scheduler_pending[cohort]:
                scheduler_pending[cohort] -= 1
            if kind == "scheduler_attempt_failed" or payload.get("status") in ("FAIL", "ERROR"):
                scheduler_failed.add(cohort)
        elif kind == "finalizer_slot_started":
            slot_attempts[f"{payload.get('cohort', '')}|{payload.get('feed_identity', '')}"] += 1
        elif kind == "recovery_invocation_started":
            recovery_count += 1
        elif kind == "recovery_path_available":
            recovery_path = (
                payload.get("callable") is True
                and payload.get("method") == "FinalizationProgressStore.reconcile"
            )
        elif kind == "terminalization_boundary":
            terminalization = True
        elif kind == "slot_finalized":
            cohort = str(payload.get("cohort", ""))
            feed = str(payload.get("feed_identity", ""))
            finalized_slots[f"{cohort}|{feed}"] += 1
            closed_at[cohort].add(str(payload.get("closed_at_utc", "")))
            evidence[f"{cohort}|{feed}"].add(str(payload.get("evidence_sha256", "")))

    finalizer_retries = sum(max(0, count - 1) for count in slot_attempts.values())
    duplicate_count = sum(max(0, count - 1) for count in finalized_slots.values())
    finalized = [event for event in events if event["event_type"] == "slot_finalized"]
    cohorts = {str(event["payload"].get("cohort", "")) for event in finalized}
    flushes = {
        str(event["payload"].get("cohort", ""))
        for event in events
        if event["event_type"] == "final_flush_completed"
    }
    closed_stable = bool(finalized) and terminalization and cohorts.issubset(flushes) and all(
        len(closed_at[cohort]) == 1 for cohort in cohorts
    )
    hash_stable = bool(finalized) and terminalization and all(
        SHA256_RE.fullmatch(next(iter(values), "")) is not None and len(values) == 1
        for values in evidence.values()
    )
    return {
        "scheduler_retries": scheduler_retries,
        "finalizer_retries": finalizer_retries,
        "recovery_invocations": recovery_count,
        "duplicate_finalization": duplicate_count,
        "closed_at_utc_stable": closed_stable,
        "evidence_hash_stable": hash_stable,
        "restart_idempotency_path_exposed": recovery_path,
    }


def audit_finalization_trace(
    path: Path,
    *,
    run_id: str,
    epoch: str,
    runtime_commit: str,
) -> dict[str, Any]:
    errors: list[str] = []
    if not COMMIT_RE.fullmatch(runtime_commit) or runtime_commit == FROZEN_B4_COMMIT:
        errors.append("runtime_commit_must_be_an_exact_future_runtime_commit")
    try:
        summary = _strict_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": FAIL, "errors": [f"invalid_trace_json:{type(exc).__name__}"]}
    if not isinstance(summary, dict):
        return {"status": FAIL, "errors": ["trace_summary_must_be_an_object"]}

    expected_top = {
        "run_id": run_id,
        "epoch": epoch,
        "source_classification": "NATIVE",
        "evidence_classification": "NATIVE_INSTRUMENTATION_PRESENT",
        "producer": "bithumb_coin_trader.finalization_trace.FinalizationTrace",
        "producer_schema_version": TRACE_SCHEMA_VERSION,
        "producer_source_sha256": producer_source_sha256(),
    }
    for key, expected in expected_top.items():
        if summary.get(key) != expected:
            errors.append(f"summary_binding_mismatch:{key}")
    events = summary.get("events")
    if not isinstance(events, list) or not events:
        return {"status": FAIL, "errors": errors + ["event_inventory_missing"]}

    previous: str | None = None
    reconstructed_lines: list[bytes] = []
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            errors.append(f"invalid_event:{index}")
            continue
        if event.get("schema_version") != TRACE_SCHEMA_VERSION:
            errors.append(f"event_schema_mismatch:{index}")
        if event.get("run_id") != run_id or event.get("epoch") != epoch:
            errors.append(f"event_identity_mismatch:{index}")
        if type(event.get("sequence")) is not int or event.get("sequence") != index:
            errors.append(f"event_sequence_mismatch:{index}")
        if event.get("previous_event_sha256") != previous:
            errors.append(f"event_previous_hash_mismatch:{index}")
        event_time = event.get("at_utc")
        try:
            if not isinstance(event_time, str) or datetime.fromisoformat(event_time.replace("Z", "+00:00")).utcoffset() is None:
                raise ValueError("not timezone aware")
        except Exception:
            errors.append(f"event_timestamp_invalid:{index}")
        if not isinstance(event.get("event_type"), str) or not isinstance(event.get("payload"), dict):
            errors.append(f"event_shape_invalid:{index}")
            continue
        calculated = _event_hash(event)
        if event.get("event_sha256") != calculated:
            errors.append(f"event_hash_mismatch:{index}")
        previous = event.get("event_sha256") if SHA256_RE.fullmatch(str(event.get("event_sha256", ""))) else None
        reconstructed_lines.append(_canonical(event) + b"\n")

    event_bytes = b"".join(reconstructed_lines)
    event_hash = hashlib.sha256(event_bytes).hexdigest()
    if summary.get("event_log_sha256") != event_hash:
        errors.append("event_log_hash_mismatch")
    if summary.get("event_log_path") != "finalization-trace/events.jsonl":
        errors.append("event_log_path_mismatch")
    if type(summary.get("event_count")) is not int or summary.get("event_count") != len(events):
        errors.append("event_count_mismatch")
    if not isinstance(events[0], dict) or summary.get("first_event_sha256") != events[0].get("event_sha256"):
        errors.append("first_event_hash_mismatch")
    if not isinstance(events[-1], dict) or summary.get("last_event_sha256") != events[-1].get("event_sha256"):
        errors.append("last_event_hash_mismatch")

    derived = _derived_fields([event for event in events if isinstance(event, dict) and isinstance(event.get("payload"), dict)])
    for key, value in derived.items():
        if summary.get(key) != value:
            errors.append(f"derived_summary_mismatch:{key}")
    for key in ("scheduler_retries", "finalizer_retries", "recovery_invocations", "duplicate_finalization"):
        value = summary.get(key)
        if type(value) is not int:
            errors.append(f"counter_type_invalid:{key}")
        elif value != 0:
            errors.append(f"frozen_contract_nonzero:{key}")
    for key in ("closed_at_utc_stable", "evidence_hash_stable", "restart_idempotency_path_exposed"):
        if summary.get(key) is not True:
            errors.append(f"frozen_contract_false:{key}")

    required_events = {
        "scheduler_attempt_started",
        "scheduler_attempt_completed",
        "cohort_finalization_started",
        "final_flush_started",
        "recovery_path_available",
        "finalizer_slot_started",
        "archive_receipt_write_complete",
        "cohort_closure",
        "slot_finalized",
        "cohort_receipt_write_started",
        "cohort_receipt_write_completed",
        "cohort_remote_receipt_upload_completed",
        "final_flush_completed",
        "terminalization_boundary",
    }
    found_events = {event.get("event_type") for event in events if isinstance(event, dict)}
    for required in sorted(required_events - found_events):
        errors.append(f"required_event_missing:{required}")
    return {
        "status": PASS if not errors else FAIL,
        "errors": errors,
        "run_id": run_id,
        "epoch": epoch,
        "runtime_commit": runtime_commit,
        "event_count": len(events),
        "event_log_sha256": event_hash,
        "derived": derived,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--epoch", required=True)
    parser.add_argument("--runtime-commit", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = audit_finalization_trace(
        args.trace,
        run_id=args.run_id,
        epoch=args.epoch,
        runtime_commit=args.runtime_commit,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["status"] == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
