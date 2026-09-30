from __future__ import annotations

import json
from pathlib import Path

from bithumb_coin_trader.finalization_trace import FinalizationTrace
from scripts.audit_finalization_trace import FAIL, PASS, audit_finalization_trace


RUN_ID = "run-audit-trace"
EPOCH = "epoch-audit-trace"
RUNTIME_COMMIT = "a" * 40


def _write_good_trace(tmp_path: Path) -> Path:
    trace = FinalizationTrace(tmp_path / "data" / "finalization-trace", run_id=RUN_ID, epoch=EPOCH)
    trace.append("scheduler_attempt_started", cohort="2026-09-14_12")
    trace.append("cohort_finalization_started", cohort="2026-09-14_12")
    trace.append("final_flush_started", cohort="2026-09-14_12")
    trace.append(
        "recovery_path_available",
        callable=True,
        method="FinalizationProgressStore.reconcile",
    )
    trace.append("finalizer_slot_started", cohort="2026-09-14_12", feed_identity="bithumb/orderbook/KRW-BTC")
    trace.append("archive_receipt_write_complete", cohort="2026-09-14_12", feed_identity="bithumb/orderbook/KRW-BTC")
    trace.append("cohort_closure", cohort="2026-09-14_12", feed_identity="bithumb/orderbook/KRW-BTC")
    trace.append(
        "slot_finalized",
        cohort="2026-09-14_12",
        feed_identity="bithumb/orderbook/KRW-BTC",
        closed_at_utc="2026-09-14T13:00:00Z",
        evidence_sha256="b" * 64,
    )
    trace.append("cohort_receipt_write_started", cohort="2026-09-14_12")
    trace.append("cohort_receipt_write_completed", cohort="2026-09-14_12")
    trace.append("cohort_remote_receipt_upload_completed", cohort="2026-09-14_12", success=True)
    trace.append("final_flush_completed", cohort="2026-09-14_12")
    trace.append("scheduler_attempt_completed", cohort="2026-09-14_12", status="PASS")
    trace.append("terminalization_boundary")
    trace.write_terminal_summary()
    return trace.summary_path


def test_native_auditor_accepts_exact_hash_chained_summary(tmp_path: Path) -> None:
    summary_path = _write_good_trace(tmp_path)
    result = audit_finalization_trace(
        summary_path,
        run_id=RUN_ID,
        epoch=EPOCH,
        runtime_commit=RUNTIME_COMMIT,
    )
    assert result["status"] == PASS
    assert result["derived"]["restart_idempotency_path_exposed"] is True


def test_native_auditor_rejects_tampered_event_even_when_summary_label_is_native(tmp_path: Path) -> None:
    summary_path = _write_good_trace(tmp_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["events"][4]["payload"]["feed_identity"] = "forged/feed"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    result = audit_finalization_trace(
        summary_path,
        run_id=RUN_ID,
        epoch=EPOCH,
        runtime_commit=RUNTIME_COMMIT,
    )
    assert result["status"] == FAIL
    assert any("hash" in error for error in result["errors"])


def test_native_auditor_rejects_current_b4_runtime(tmp_path: Path) -> None:
    summary_path = _write_good_trace(tmp_path)
    result = audit_finalization_trace(
        summary_path,
        run_id=RUN_ID,
        epoch=EPOCH,
        runtime_commit="b4d482363e2f988dad9c6d29053f97e1e4160883",
    )
    assert result["status"] == FAIL
    assert "runtime_commit_must_be_an_exact_future_runtime_commit" in result["errors"]
