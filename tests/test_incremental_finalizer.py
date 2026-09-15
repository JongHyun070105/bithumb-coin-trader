"""Tests for incremental finalizer, durable progress store, and WAL recovery."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
import pytest

from bithumb_coin_trader.incremental_finalizer import (
    ArtifactBinding,
    FinalizationEntry,
    FinalizationEvidenceError,
    FinalizationIdentity,
    FinalizationProgressStore,
    FinalizationState,
    FinalizationSummary,
    IncrementalManifestFinalizer,
)
from bithumb_coin_trader.microstructure_storage import (
    RawMicrostructureStorage,
)


class SimulatedCrash(Exception):
    """Simulated crash for fault injection testing."""


def make_identity(
    cohort: str = "2026-09-14_12",
    raw_rel: str = "2026-09-14/bithumb/trade/bithumb_trade_krw-btc_2026-09-14_12.jsonl",
    run_id: str = "run-001",
    epoch: str = "epoch-001",
    exchange: str = "bithumb",
    stream: str = "trade",
    market: str = "KRW-BTC",
    feed_id: str = "bithumb:trade:KRW-BTC",
    env_id: str = "aws-v3",
) -> FinalizationIdentity:
    return FinalizationIdentity(
        environment_id=env_id,
        collector_epoch=epoch,
        collector_run_id=run_id,
        cohort=cohort,
        exchange=exchange,
        stream=stream,
        market=market,
        feed_identity=feed_id,
        raw_relative_path=raw_rel,
    )


def make_binding(
    source_size: int = 100,
    source_sha256: str = "a" * 64,
    source_record_count: int = 1,
    manifest_relative_path: str = "manifests/manifest_foo.json",
    manifest_file_sha256: str = "b" * 64,
    receipt_relative_path: str | None = None,
    receipt_file_sha256: str | None = None,
    receipt_state: str | None = None,
    artifact_kind: str = "RAW_DATA",
) -> ArtifactBinding:
    return ArtifactBinding(
        source_size=source_size,
        source_sha256=source_sha256,
        source_record_count=source_record_count,
        manifest_relative_path=manifest_relative_path,
        manifest_file_sha256=manifest_file_sha256,
        receipt_relative_path=receipt_relative_path,
        receipt_file_sha256=receipt_file_sha256,
        receipt_state=receipt_state,
        artifact_kind=artifact_kind,
    )


def test_pending_allows_null_bindings(tmp_path: Path) -> None:
    store = FinalizationProgressStore(tmp_path / "finalization-progress")
    ident = make_identity()
    entry = store.register_pending(ident)

    assert entry.state is FinalizationState.PENDING
    assert entry.source_size is None
    assert entry.source_sha256 is None
    assert entry.source_record_count is None
    assert entry.manifest_relative_path is None
    assert entry.manifest_file_sha256 is None
    assert entry.receipt_relative_path is None
    assert entry.receipt_file_sha256 is None
    assert entry.receipt_state is None
    assert entry.artifact_kind is None
    assert entry.failure_reason_code is None
    assert entry.started_at_utc is None
    assert entry.completed_at_utc is None
    assert len(entry.entry_sha256) == 64

    # Summary reflects 1 pending
    summary = store.summary()
    assert summary.total_count == 1
    assert summary.pending_count == 1
    assert summary.reused_count == 0
    assert summary.recomputed_count == 0
    assert summary.failed_count == 0


@pytest.mark.parametrize("crash_after", ["intent", "entry", "pending", "summary"])
def test_reconcile_every_transaction_boundary(tmp_path: Path, crash_after: str) -> None:
    progress_dir = tmp_path / "finalization-progress"

    # Setup crash hook that crashes during mark_recomputed
    hook_active = False

    def crash_hook(stage: str) -> None:
        if hook_active and stage == crash_after:
            raise SimulatedCrash(f"Crash simulated at {stage}")

    store = FinalizationProgressStore(progress_dir, crash_hook=crash_hook)
    ident = make_identity()
    entry = store.register_pending(ident)
    assert entry.state is FinalizationState.PENDING

    # Now activate hook for recomputation
    hook_active = True
    binding = make_binding()

    with pytest.raises(SimulatedCrash):
        store.mark_recomputed(entry.entry_id, binding)

    # Reopen fresh store without crash hook and reconcile
    recovered = FinalizationProgressStore(progress_dir)
    recovered_summary = recovered.reconcile()

    assert recovered_summary.total_count == 1

    if crash_after == "intent":
        # Entry write did not happen, so rolled back to PENDING
        assert recovered_summary.pending_count == 1
        assert recovered_summary.recomputed_count == 0
        entry_recovered = recovered.get_entry(entry.entry_id)
        assert entry_recovered.state is FinalizationState.PENDING
    else:
        # Entry write completed, so rolled forward to RECOMPUTED
        assert recovered_summary.pending_count == 0
        assert recovered_summary.recomputed_count == 1
        entry_recovered = recovered.get_entry(entry.entry_id)
        assert entry_recovered.state is FinalizationState.RECOMPUTED


def test_terminal_left_in_pending_reconciled(tmp_path: Path) -> None:
    progress_dir = tmp_path / "finalization-progress"
    store = FinalizationProgressStore(progress_dir)
    ident = make_identity()
    entry = store.register_pending(ident)
    store.mark_recomputed(entry.entry_id, make_binding())

    # Manually corrupt pending.json to include the already-recomputed entry_id
    pending_file = progress_dir / "pending.json"
    pending_data = json.loads(pending_file.read_text(encoding="utf-8"))
    pending_data["pending_ids"] = [entry.entry_id]
    pending_file.write_text(json.dumps(pending_data), encoding="utf-8")

    # Reconcile safely removes the terminal entry from pending
    recovered = FinalizationProgressStore(progress_dir)
    summary = recovered.reconcile()
    assert summary.pending_count == 0
    assert len(recovered.pending_entries()) == 0


def test_unexplained_generation_jump_fails_closed(tmp_path: Path) -> None:
    progress_dir = tmp_path / "finalization-progress"
    store = FinalizationProgressStore(progress_dir)
    ident = make_identity()
    store.register_pending(ident)

    # Manually corrupt summary.json generation without transaction
    summary_file = progress_dir / "summary.json"
    summary_data = json.loads(summary_file.read_text(encoding="utf-8"))
    summary_data["generation"] = 999
    summary_file.write_text(json.dumps(summary_data), encoding="utf-8")

    recovered = FinalizationProgressStore(progress_dir)
    with pytest.raises(FinalizationEvidenceError) as exc_info:
        recovered.reconcile()
    assert exc_info.value.reason_code == "FINALIZATION_PROGRESS_CORRUPT"


def test_neither_hash_match_fails_closed(tmp_path: Path) -> None:
    progress_dir = tmp_path / "finalization-progress"
    store = FinalizationProgressStore(progress_dir)
    ident = make_identity()
    entry = store.register_pending(ident)

    # Simulate in-flight transaction where entry was corrupted to neither hash
    tx_file = progress_dir / "transaction.json"
    tx_data = {
        "transaction_id": "tx-corrupt-01",
        "entry_id": entry.entry_id,
        "before_state": "PENDING",
        "before_hash": entry.entry_sha256,
        "after_state": "RECOMPUTED",
        "after_hash": "f" * 64,
        "before_pending_ids": [entry.entry_id],
        "after_pending_ids": [],
        "before_summary": store.summary().to_dict(),
        "after_summary": store.summary().to_dict(),
        "target_generation": 2,
    }
    tx_file.write_text(json.dumps(tx_data), encoding="utf-8")

    # Corrupt the entry file
    entry_file = progress_dir / "entries" / f"{entry.entry_id}.json"
    entry_dict = json.loads(entry_file.read_text(encoding="utf-8"))
    entry_dict["created_at_utc"] = "1970-01-01T00:00:00Z"
    entry_dict["entry_sha256"] = "bad" * 16
    entry_file.write_text(json.dumps(entry_dict), encoding="utf-8")

    recovered = FinalizationProgressStore(progress_dir)
    with pytest.raises(FinalizationEvidenceError) as exc_info:
        recovered.reconcile()
    assert exc_info.value.reason_code == "FINALIZATION_PROGRESS_CORRUPT"


def test_reused_trust_chain_never_opens_historical_raw(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    receipt_root = tmp_path / "receipts"
    progress_root = tmp_path / "progress"

    storage = RawMicrostructureStorage(base_dir=raw_root)
    store = FinalizationProgressStore(progress_root)

    # Create historical raw file
    raw_rel = "2026-09-14/bithumb/trade/bithumb_trade_krw-btc_2026-09-14_11.jsonl"
    raw_file = raw_root / raw_rel
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_content = b'{"exchange":"bithumb","stream":"trade","market":"KRW-BTC","payload":{}}\n'
    raw_file.write_bytes(raw_content)

    import hashlib
    raw_sha256 = hashlib.sha256(raw_content).hexdigest()

    # Pre-create manifest
    manifest_file = storage.manifest_dir / f"manifest_{raw_file.stem}.json"
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_data = {
        "partition_path": str(raw_file.relative_to(raw_root.parent.parent)),
        "sha256": raw_sha256,
        "bytes": len(raw_content),
        "record_count": 1,
    }
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")
    manifest_sha256 = hashlib.sha256(manifest_file.read_bytes()).hexdigest()

    # Create terminal receipt matching raw
    receipt_file = receipt_root / f"{raw_file.name}.archive-receipt.json"
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    receipt_dict = {
        "schema_version": 2,
        "state": "ARCHIVED",
        "environment_id": "aws-v3",
        "run_id": "run-001",
        "collector_epoch": "epoch-001",
        "partition": raw_rel,
        "cohort": "2026-09-14_11",
        "exchange": "bithumb",
        "stream": "trade",
        "market": "KRW-BTC",
        "feed_identity": "bithumb:trade:KRW-BTC",
        "raw_size": len(raw_content),
        "raw_sha256": raw_sha256,
        "raw_record_count": 1,
        "manifest_relative_path": str(manifest_file.relative_to(storage.manifest_dir.parent)),
        "manifest_file_sha256": manifest_sha256,
        "artifact_kind": "RAW_DATA",
    }
    receipt_file.write_text(json.dumps(receipt_dict), encoding="utf-8")

    ident = make_identity(
        cohort="2026-09-14_11",
        raw_rel=raw_rel,
    )
    store.register_pending(ident)

    finalizer = IncrementalManifestFinalizer(store, storage, receipt_root)

    # Track open calls to raw_file to prove it was never opened or read
    raw_opened = False
    original_open = Path.open

    def guarded_open(self: Path, *args, **kwargs):
        nonlocal raw_opened
        if self.resolve() == raw_file.resolve():
            raw_opened = True
            raise AssertionError(f"RAW file {raw_file} was opened!")
        return original_open(self, *args, **kwargs)

    with patch.object(Path, "open", guarded_open):
        summary = finalizer.finalize_pending()

    assert not raw_opened
    assert summary.reused_count == 1
    assert summary.recomputed_count == 0
    assert summary.pending_count == 0
    assert summary.failed_count == 0
    assert summary.historical_raw_files_opened == 0
    assert summary.historical_raw_bytes_read == 0
    assert summary.state == "COMPLETE"

    entry = store.get_entry(ident.entry_id)
    assert entry.state is FinalizationState.REUSED


def test_contradictory_receipt_fails_closed(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    receipt_root = tmp_path / "receipts"
    progress_root = tmp_path / "progress"

    storage = RawMicrostructureStorage(base_dir=raw_root)
    store = FinalizationProgressStore(progress_root)

    raw_rel = "2026-09-14/bithumb/trade/bithumb_trade_krw-btc_2026-09-14_11.jsonl"
    raw_file = raw_root / raw_rel
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_bytes(b"data")

    # Contradictory receipt: cohort mismatch
    receipt_file = receipt_root / f"{raw_file.name}.archive-receipt.json"
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    receipt_dict = {
        "schema_version": 2,
        "state": "ARCHIVED",
        "environment_id": "aws-v3",
        "run_id": "run-001",
        "collector_epoch": "epoch-001",
        "partition": raw_rel,
        "cohort": "CONTRADICTORY_COHORT",
        "raw_size": 4,
        "raw_sha256": "3a6eb0790f39ac87c94f3856b2dd2c5d110e6811602261a9a923d3bb23adc8b7",
        "raw_record_count": 1,
        "manifest_relative_path": "manifests/manifest_foo.json",
        "manifest_file_sha256": "a" * 64,
        "artifact_kind": "RAW_DATA",
    }
    receipt_file.write_text(json.dumps(receipt_dict), encoding="utf-8")

    ident = make_identity(
        cohort="2026-09-14_11",
        raw_rel=raw_rel,
    )
    store.register_pending(ident)

    finalizer = IncrementalManifestFinalizer(store, storage, receipt_root)
    summary = finalizer.finalize_pending()

    assert summary.failed_count == 1
    assert summary.reused_count == 0
    entry = store.get_entry(ident.entry_id)
    assert entry.state is FinalizationState.FAILED
    assert entry.failure_reason_code == "CONTRADICTORY_RECEIPT"


def test_finalize_pending_dirty_tail_only(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    receipt_root = tmp_path / "receipts"
    progress_root = tmp_path / "progress"

    storage = RawMicrostructureStorage(base_dir=raw_root)
    store = FinalizationProgressStore(progress_root)

    # 1. Archived partition with terminal receipt
    archived_rel = "2026-09-14/bithumb/trade/bithumb_trade_krw-btc_2026-09-14_11.jsonl"
    archived_file = raw_root / archived_rel
    archived_file.parent.mkdir(parents=True, exist_ok=True)
    archived_bytes = b'{"exchange":"bithumb","stream":"trade","market":"KRW-BTC","payload":{}}\n'
    archived_file.write_bytes(archived_bytes)

    import hashlib
    archived_sha = hashlib.sha256(archived_bytes).hexdigest()

    manifest_file = storage.manifest_dir / f"manifest_{archived_file.stem}.json"
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps({"sha256": archived_sha, "bytes": len(archived_bytes)}), encoding="utf-8")
    manifest_sha = hashlib.sha256(manifest_file.read_bytes()).hexdigest()

    receipt_file = receipt_root / f"{archived_file.name}.archive-receipt.json"
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    receipt_file.write_text(
        json.dumps({
            "schema_version": 2,
            "state": "ARCHIVED",
            "environment_id": "aws-v3",
            "run_id": "run-001",
            "collector_epoch": "epoch-001",
            "partition": archived_rel,
            "cohort": "2026-09-14_11",
            "exchange": "bithumb",
            "stream": "trade",
            "market": "KRW-BTC",
            "feed_identity": "bithumb:trade:KRW-BTC",
            "raw_size": len(archived_bytes),
            "raw_sha256": archived_sha,
            "raw_record_count": 1,
            "manifest_relative_path": str(manifest_file.relative_to(storage.manifest_dir.parent)),
            "manifest_file_sha256": manifest_sha,
            "artifact_kind": "RAW_DATA",
        }),
        encoding="utf-8",
    )

    # 2. Dirty ending partition (no receipt)
    dirty_rel = "2026-09-14/bithumb/trade/bithumb_trade_krw-btc_2026-09-14_12.jsonl"
    dirty_file = raw_root / dirty_rel
    dirty_file.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    dirty_record = {
        "exchange": "bithumb",
        "stream": "trade",
        "market": "KRW-BTC",
        "exchange_ts": now_iso,
        "local_recv_ts": now_iso,
        "local_write_ts": now_iso,
        "payload": {"price": "100000000", "size": "0.01"},
    }
    dirty_file.write_text(json.dumps(dirty_record) + "\n", encoding="utf-8")

    ident_archived = make_identity(cohort="2026-09-14_11", raw_rel=archived_rel)
    ident_dirty = make_identity(cohort="2026-09-14_12", raw_rel=dirty_rel)

    store.register_pending(ident_archived)
    store.register_pending(ident_dirty)

    finalizer = IncrementalManifestFinalizer(store, storage, receipt_root)
    summary = finalizer.finalize_pending()

    assert summary.reused_count == 1
    assert summary.recomputed_count == 1
    assert summary.pending_count == 0
    assert summary.failed_count == 0
    assert summary.historical_raw_files_opened == 0
    assert summary.historical_raw_bytes_read == 0
    assert summary.state == "COMPLETE"

    entry_archived = store.get_entry(ident_archived.entry_id)
    assert entry_archived.state is FinalizationState.REUSED

    entry_dirty = store.get_entry(ident_dirty.entry_id)
    assert entry_dirty.state is FinalizationState.RECOMPUTED
    assert entry_dirty.manifest_relative_path is not None
    assert entry_dirty.source_record_count == 1


def test_resolve_raw_path_escape_rejected(tmp_path: Path) -> None:
    storage = RawMicrostructureStorage(base_dir=tmp_path / "raw")
    with pytest.raises((FinalizationEvidenceError, ValueError)) as exc_info:
        storage.resolve_raw("../../etc/passwd")
    if isinstance(exc_info.value, FinalizationEvidenceError):
        assert exc_info.value.reason_code == "PATH_ESCAPE"

    with pytest.raises((FinalizationEvidenceError, ValueError)) as exc_info:
        storage.resolve_raw("/absolute/path")
    if isinstance(exc_info.value, FinalizationEvidenceError):
        assert exc_info.value.reason_code == "PATH_ESCAPE"


def test_resolve_raw_symlink_rejected(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir(parents=True, exist_ok=True)

    # 1. Symlink pointing outside base_dir
    outside_file = outside_dir / "secret.jsonl"
    outside_file.write_text("secret\n")
    outside_symlink = raw_dir / "outside_link.jsonl"
    outside_symlink.symlink_to(outside_file)

    storage = RawMicrostructureStorage(base_dir=raw_dir)
    with pytest.raises(FinalizationEvidenceError) as exc_info:
        storage.resolve_raw("outside_link.jsonl")
    assert exc_info.value.reason_code in ("PATH_ESCAPE", "SYMLINK_REJECTED")

    # 2. Symlink pointing inside base_dir
    real_file = raw_dir / "real.jsonl"
    real_file.write_text("payload\n")
    inside_symlink = raw_dir / "inside_link.jsonl"
    inside_symlink.symlink_to(real_file)

    with pytest.raises(FinalizationEvidenceError) as exc_info:
        storage.resolve_raw("inside_link.jsonl")
    assert exc_info.value.reason_code in ("SYMLINK_REJECTED", "PATH_ESCAPE")


def test_corrupt_receipt_marks_failed_without_recomputing(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    receipt_root = tmp_path / "receipts"
    progress_root = tmp_path / "progress"

    raw_root.mkdir(parents=True, exist_ok=True)
    receipt_root.mkdir(parents=True, exist_ok=True)
    progress_root.mkdir(parents=True, exist_ok=True)

    storage = RawMicrostructureStorage(base_dir=raw_root)
    store = FinalizationProgressStore(progress_root)

    raw_rel = "2026-09-14/bithumb/trade/bithumb_trade_krw-btc_2026-09-14_11.jsonl"
    raw_file = raw_root / raw_rel
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text("should_not_be_read\n")

    # Write corrupt JSON receipt
    receipt_file = receipt_root / f"{raw_file.name}.archive-receipt.json"
    receipt_file.write_text("{ corrupt json here !!!")

    ident = make_identity(cohort="2026-09-14_11", raw_rel=raw_rel)
    store.register_pending(ident)

    finalizer = IncrementalManifestFinalizer(store, storage, receipt_root)

    # Track open calls to raw_file to prove it was never opened
    raw_opened = False
    original_open = Path.open

    def guarded_open(self: Path, *args, **kwargs):
        nonlocal raw_opened
        if self.resolve() == raw_file.resolve():
            raw_opened = True
            raise AssertionError(f"RAW file {raw_file} was opened!")
        return original_open(self, *args, **kwargs)

    with patch.object(Path, "open", guarded_open):
        summary = finalizer.finalize_pending()

    assert not raw_opened
    assert summary.reused_count == 0
    assert summary.recomputed_count == 0
    assert summary.failed_count == 1
    assert summary.pending_count == 0

    entry = store.get_entry(ident.entry_id)
    assert entry.state is FinalizationState.FAILED
    assert entry.failure_reason_code == "CORRUPT_ARCHIVE_RECEIPT"


def test_receipt_source_path_mismatch_fails_closed(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    receipt_root = tmp_path / "receipts"
    progress_root = tmp_path / "progress"

    raw_root.mkdir(parents=True, exist_ok=True)
    receipt_root.mkdir(parents=True, exist_ok=True)
    progress_root.mkdir(parents=True, exist_ok=True)

    storage = RawMicrostructureStorage(base_dir=raw_root)
    store = FinalizationProgressStore(progress_root)

    raw_rel = "2026-09-14/bithumb/trade/bithumb_trade_krw-btc_2026-09-14_11.jsonl"
    raw_file = raw_root / raw_rel
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text("content\n")

    manifest_file = storage.manifest_dir / f"manifest_{raw_file.stem}.json"
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    import hashlib
    content_bytes = raw_file.read_bytes()
    manifest_data = {
        "partition_path": str(raw_file.relative_to(raw_root.parent.parent)),
        "sha256": hashlib.sha256(content_bytes).hexdigest(),
        "bytes": len(content_bytes),
        "record_count": 1,
    }
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")
    manifest_sha256 = hashlib.sha256(manifest_file.read_bytes()).hexdigest()

    # Receipt with mismatched partition
    receipt_file = receipt_root / f"{raw_file.name}.archive-receipt.json"
    receipt_dict = {
        "schema_version": 2,
        "state": "ARCHIVED",
        "environment_id": "aws-v3",
        "run_id": "run-001",
        "collector_epoch": "epoch-001",
        "partition": "2026-09-14/binance/depth/wrong_stream.jsonl",
        "cohort": "2026-09-14_11",
        "exchange": "bithumb",
        "stream": "trade",
        "market": "KRW-BTC",
        "feed_identity": "bithumb:trade:KRW-BTC",
        "raw_size": len(content_bytes),
        "raw_sha256": hashlib.sha256(content_bytes).hexdigest(),
        "raw_record_count": 1,
        "manifest_relative_path": str(manifest_file.relative_to(storage.manifest_dir.parent)),
        "manifest_file_sha256": manifest_sha256,
        "artifact_kind": "RAW_DATA",
    }
    receipt_file.write_text(json.dumps(receipt_dict), encoding="utf-8")

    ident = make_identity(cohort="2026-09-14_11", raw_rel=raw_rel)
    store.register_pending(ident)

    finalizer = IncrementalManifestFinalizer(store, storage, receipt_root)
    summary = finalizer.finalize_pending()

    assert summary.failed_count == 1
    assert summary.reused_count == 0
    entry = store.get_entry(ident.entry_id)
    assert entry.state is FinalizationState.FAILED
    assert entry.failure_reason_code == "RECEIPT_SOURCE_PATH_MISMATCH"


def test_mark_complete_transaction_safety(tmp_path: Path) -> None:
    progress_dir = tmp_path / "finalization-progress"
    store = FinalizationProgressStore(progress_dir)
    ident = make_identity()
    store.register_pending(ident)
    store.mark_recomputed(ident.entry_id, make_binding())

    # Summary is IN_PROGRESS with 0 pending and 0 failed
    summary = store.summary()
    assert summary.pending_count == 0
    assert summary.failed_count == 0
    gen_before = summary.generation

    # mark_complete transitions to COMPLETE
    completed_summary = store.mark_complete()
    assert completed_summary.state == "COMPLETE"

    # Generation in summary.json and pending.json must match
    summary_file = progress_dir / "summary.json"
    pending_file = progress_dir / "pending.json"
    s_data = json.loads(summary_file.read_text(encoding="utf-8"))
    p_data = json.loads(pending_file.read_text(encoding="utf-8"))

    assert s_data["generation"] == p_data["generation"]
    assert s_data["state"] == "COMPLETE"
    assert s_data["generation"] == gen_before

    # Reconcile safely succeeds without unexplained generation jump
    recovered = FinalizationProgressStore(progress_dir)
    rec_summary = recovered.reconcile()
    assert rec_summary.state == "COMPLETE"
    assert rec_summary.generation == gen_before
