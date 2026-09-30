from __future__ import annotations

import json
from pathlib import Path

import pytest

from bithumb_coin_trader.finalization_trace import FinalizationTrace, FinalizationTraceError


RUN_ID = "run-trace-test"
EPOCH = "epoch-trace-test"
SLOT_HASH = "a" * 64


def _trace(root: Path) -> FinalizationTrace:
    return FinalizationTrace(root, run_id=RUN_ID, epoch=EPOCH)


def test_append_chain_survives_restart_and_summary_checks_native_boundaries(tmp_path: Path) -> None:
    root = tmp_path / "data" / "finalization-trace"
    trace = _trace(root)
    trace.append("recovery_path_available", callable=True, method="FinalizationProgressStore.reconcile")
    trace.append("scheduler_attempt_started", cohort="2026-09-14_12")
    trace.append("finalizer_slot_started", cohort="2026-09-14_12", feed_identity="bithumb/orderbook/KRW-BTC")
    trace.append(
        "slot_finalized",
        cohort="2026-09-14_12",
        feed_identity="bithumb/orderbook/KRW-BTC",
        closed_at_utc="2026-09-14T13:00:00Z",
        evidence_sha256=SLOT_HASH,
    )
    trace.append("final_flush_completed", cohort="2026-09-14_12", slot_count=1, failed_count=0)

    restarted = _trace(root)
    boundary = restarted.append("terminalization_boundary", service_result="success")
    assert boundary["sequence"] == 6
    events = restarted.read_events()
    assert [event["sequence"] for event in events] == list(range(1, 7))
    assert events[-1]["previous_event_sha256"] == events[-2]["event_sha256"]

    summary = restarted.write_terminal_summary()
    assert summary["source_classification"] == "NATIVE"
    assert summary["evidence_classification"] == "NATIVE_INSTRUMENTATION_PRESENT"
    assert summary["scheduler_retries"] == 0
    assert summary["finalizer_retries"] == 0
    assert summary["recovery_invocations"] == 0
    assert summary["duplicate_finalization"] == 0
    assert summary["closed_at_utc_stable"] is True
    assert summary["evidence_hash_stable"] is True
    assert summary["restart_idempotency_path_exposed"] is True
    written = json.loads(restarted.summary_path.read_text(encoding="utf-8"))
    assert written["event_log_sha256"] == summary["event_log_sha256"]
    assert written["events"] == events


def test_summary_records_retries_recovery_and_duplicates_without_hiding_them(tmp_path: Path) -> None:
    trace = _trace(tmp_path / "data" / "finalization-trace")
    trace.append("recovery_path_available", callable=True)
    for _ in range(2):
        trace.append("scheduler_attempt_started", cohort="2026-09-14_12")
        trace.append("finalizer_slot_started", cohort="2026-09-14_12", feed_identity="feed-a")
    trace.append("recovery_invocation_started", cohort="2026-09-14_12")
    trace.append("cohort_receipt_reused", cohort="2026-09-14_12")
    for _ in range(2):
        trace.append("slot_finalized", cohort="2026-09-14_12", feed_identity="feed-a", closed_at_utc="t0", evidence_sha256=SLOT_HASH)
    trace.append("final_flush_completed", cohort="2026-09-14_12")
    trace.append("terminalization_boundary")

    summary = trace.terminal_summary()
    assert summary["scheduler_retries"] == 1
    assert summary["finalizer_retries"] == 1
    assert summary["recovery_invocations"] == 1
    assert summary["duplicate_finalization"] == 1


def test_tampered_hash_chain_is_rejected_on_restart(tmp_path: Path) -> None:
    root = tmp_path / "data" / "finalization-trace"
    _trace(root).append("scheduler_attempt_started", cohort="2026-09-14_12")
    event_path = root / "events.jsonl"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["payload"]["cohort"] = "forged"
    event_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    with pytest.raises(FinalizationTraceError, match="hash mismatch"):
        _trace(root)


def test_partial_event_write_fails_closed_and_is_not_silently_repaired(tmp_path: Path) -> None:
    root = tmp_path / "data" / "finalization-trace"
    root.mkdir(parents=True)
    (root / "events.jsonl").write_bytes(b'{"schema_version":1,"event_type":"partial"}')

    with pytest.raises(FinalizationTraceError, match="incomplete trailing event"):
        _trace(root)


def test_duplicate_json_keys_in_event_log_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "data" / "finalization-trace"
    root.mkdir(parents=True)
    (root / "events.jsonl").write_bytes(b'{"schema_version":1,"schema_version":1}\n')

    with pytest.raises(FinalizationTraceError, match="invalid finalization event at line 1"):
        _trace(root)
