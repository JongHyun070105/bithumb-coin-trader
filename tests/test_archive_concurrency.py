"""Unit tests for archive orchestrator concurrency, backlog metrics, and fail-closed flows."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import pwd
import pytest

from bithumb_coin_trader.archive_cohort import ArchiveCohortId
from scripts.orchestrate_closed_hour_archive import (
    OrchestratorConcurrencyError,
    compute_backlog_metrics,
    orchestrator_lock,
    orchestrate_closed_hour_archive,
)
from bithumb_coin_trader.pre_soak_archive import OwnershipViolationError


def current_user_name() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def test_orchestrator_lock_single_instance_success(tmp_path: Path):
    lock_file = tmp_path / "test_orch.lock"
    assert not lock_file.exists()

    with orchestrator_lock(lock_file, expected_owner=current_user_name()):
        assert lock_file.exists()

    assert lock_file.exists()


def test_orchestrator_lock_concurrency_rejected(tmp_path: Path):
    lock_file = tmp_path / "test_orch.lock"

    with orchestrator_lock(lock_file, expected_owner=current_user_name()):
        with pytest.raises(OrchestratorConcurrencyError, match="Another orchestrator instance holds lock"):
            with orchestrator_lock(lock_file, expected_owner=current_user_name()):
                pass


def test_orchestrator_lock_reuse_after_release(tmp_path: Path):
    lock_file = tmp_path / "test_orch.lock"

    with orchestrator_lock(lock_file, expected_owner=current_user_name()):
        pass

    # Should be able to acquire again immediately
    with orchestrator_lock(lock_file, expected_owner=current_user_name()):
        pass


def test_compute_backlog_metrics(tmp_path: Path):
    raw_root = tmp_path / "raw"
    receipt_root = tmp_path / "archive-receipts"
    raw_root.mkdir(parents=True)
    receipt_root.mkdir(parents=True)

    cohort_05 = ArchiveCohortId("2026-09-04", "05")
    cohort_06 = ArchiveCohortId("2026-09-04", "06")
    file_05 = raw_root / "BTC_KRW_2026-09-04_05.jsonl"
    file_05.write_text('{"record": 1}\n', encoding="utf-8")
    file_06 = raw_root / "BTC_KRW_2026-09-04_06.jsonl"
    file_06.write_text('{"record": 2}\n', encoding="utf-8")

    # Set file_05 receipt to CLEANUP_ELIGIBLE
    rec_05 = receipt_root / "BTC_KRW_2026-09-04_05.jsonl.archive-receipt.json"
    rec_05.write_text(json.dumps({"cleanup_eligible": True, "state": "CLEANUP_ELIGIBLE"}), encoding="utf-8")

    now = datetime.now(timezone.utc)
    grace = timedelta(seconds=600)

    backlog = compute_backlog_metrics(
        raw_root=raw_root,
        receipt_root=receipt_root,
        active_paths=[],
        now=now,
        grace_period=grace,
        closed_files=[file_05, file_06],
        cohorts_seen=[cohort_05, cohort_06],
    )

    # file_05 is done, file_06 is pending
    assert backlog["pending_archive_jobs"] == 1
    assert backlog["pending_full_scan_jobs"] == 2  # neither full_scan report exists
    assert backlog["oldest_pending_age_seconds"] is not None

    # Now create the canonical cohort-bound full-scan report.
    (receipt_root / f"full_scan_{cohort_05.key}_report.json").write_text("{}", encoding="utf-8")

    backlog2 = compute_backlog_metrics(
        raw_root=raw_root,
        receipt_root=receipt_root,
        active_paths=[],
        now=now,
        grace_period=grace,
        closed_files=[file_05, file_06],
        cohorts_seen=[cohort_05, cohort_06],
    )
    assert backlog2["pending_full_scan_jobs"] == 1  # 06 still pending


def test_orchestrate_closed_hour_archive_end_to_end(tmp_path: Path):
    base_dir = tmp_path / "epoch_data"
    raw_root = base_dir / "raw"
    receipt_root = base_dir / "archive-receipts"
    raw_root.mkdir(parents=True)
    receipt_root.mkdir(parents=True)

    # Create closed partition file for hour 05
    # Timestamp set to 2 hours ago so is_closed_stable_partition returns True
    past_time = datetime.now(timezone.utc) - timedelta(hours=2)
    hour_str = f"{past_time.hour:02d}"
    date_str = past_time.strftime("%Y-%m-%d")
    p_file = raw_root / "upbit" / "BTC_KRW" / f"BTC_KRW_{date_str}_{hour_str}.jsonl"
    p_file.parent.mkdir(parents=True)
    ts_str = past_time.isoformat()
    valid_record = {
        "timestamp": ts_str,
        "exchange": "upbit",
        "stream": "ticker",
        "market": "KRW-BTC",
        "exchange_ts": ts_str,
        "local_recv_ts": ts_str,
        "local_write_ts": ts_str,
        "payload": {"trade_price": 50000000},
    }
    p_file.write_text(json.dumps(valid_record) + "\n", encoding="utf-8")

    # Run orchestrator in direct mode (local file store, direct runner)
    result = orchestrate_closed_hour_archive(
        epoch="test-epoch",
        run_id="test-run",
        base_dir=base_dir,
        environment_id="test-env",
        store_type="file",
        file_store_root=tmp_path / "file_store",
        expected_owner=current_user_name(),
        scan_runner_mode="direct",
        run_full_scan=True,
        disk_critical_percent=99.0,
    )

    print(f"\nDEBUG RESULT: {json.dumps(result, indent=2)}\n")
    assert result["archived_count"] == 1
    assert result["archive_job_failures"] == 0
    assert result["pending_archive_jobs"] == 0
    assert result["pending_full_scan_jobs"] == 0
    cohort = ArchiveCohortId(date_str, hour_str)
    assert cohort.key in result["scan_results"]
    assert result["scan_results"][cohort.key]["success"] is True

    # Check that report and backlog files were created
    assert (receipt_root / f"full_scan_{cohort.key}_report.json").exists()
    assert (receipt_root / "archive_backlog_metrics.json").exists()


def test_orchestrate_closed_hour_archive_ownership_fail_closed(tmp_path: Path, monkeypatch):
    base_dir = tmp_path / "epoch_data"
    raw_root = base_dir / "raw"
    raw_root.mkdir(parents=True)

    orig_stat = Path.stat

    def mock_stat(self, *args, **kwargs):
        st = orig_stat(self, *args, **kwargs)
        if self == raw_root:
            return os.stat_result((
                st.st_mode,
                st.st_ino,
                st.st_dev,
                st.st_nlink,
                0,  # root UID
                st.st_gid,
                st.st_size,
                st.st_atime,
                st.st_mtime,
                st.st_ctime,
            ))
        return st

    monkeypatch.setattr(Path, "stat", mock_stat)

    with pytest.raises(OwnershipViolationError, match="Fail-closed ownership violation"):
        orchestrate_closed_hour_archive(
            epoch="test-epoch",
            run_id="test-run",
            base_dir=base_dir,
            store_type="file",
            file_store_root=tmp_path / "file_store",
            expected_owner=current_user_name(),
        )


def test_orchestrator_selects_only_requested_date_hour_cohort(tmp_path: Path):
    base_dir = tmp_path / "epoch_data"
    raw_root = base_dir / "raw" / "upbit" / "ticker"
    raw_root.mkdir(parents=True)
    paths = {}
    for date_str in ("2026-09-05", "2026-09-06", "2026-09-07"):
        path = raw_root / f"BTC_KRW_{date_str}_05.jsonl"
        timestamp = f"{date_str}T05:00:00+00:00"
        path.write_text(
            json.dumps(
                {
                    "timestamp": timestamp,
                    "exchange": "upbit",
                    "stream": "ticker",
                    "market": "KRW-BTC",
                    "exchange_ts": timestamp,
                    "local_recv_ts": timestamp,
                    "local_write_ts": timestamp,
                    "payload": {"trade_price": 1},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        paths[date_str] = path

    result = orchestrate_closed_hour_archive(
        epoch="test-epoch",
        run_id="test-run",
        base_dir=base_dir,
        store_type="file",
        file_store_root=tmp_path / "file-store",
        expected_owner=current_user_name(),
        run_full_scan=False,
        disk_critical_percent=99.0,
        target_cohort=ArchiveCohortId("2026-09-06", "05"),
    )

    assert result["cohorts_detected"] == ["2026-09-06_05"]
    assert result["archived_count"] == 1
    assert not (base_dir / "archive-receipts" / "upbit" / "ticker" / f"{paths['2026-09-05'].name}.archive-receipt.json").exists()
    assert (base_dir / "archive-receipts" / "upbit" / "ticker" / f"{paths['2026-09-06'].name}.archive-receipt.json").exists()
    assert not (base_dir / "archive-receipts" / "upbit" / "ticker" / f"{paths['2026-09-07'].name}.archive-receipt.json").exists()
