"""Per-cohort archive evidence gates for official post-remediation audits."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from scripts.audit_72h_soak import SoakAuditor72H, validate_archive_evidence_coverage


def _write_cohort_receipts(
    root: Path,
    cohort: str,
    feeds: list[tuple[str, str, str]],
    *,
    epoch: str = "epoch-v1",
    run_id: str = "run-v1",
    state: str = "CLEANUP_ELIGIBLE",
    restore_verified_at: str | None = "2026-09-09T00:00:00+00:00",
    restore_verified: bool = True,
) -> list[Path]:
    receipts: list[Path] = []
    for exch, strm, mkt in feeds:
        path = root / f"{exch}_{strm}_{mkt}_{cohort}.jsonl.archive-receipt.json"
        partition = f"raw/{exch}/{strm}/{mkt}/{mkt}_{cohort}.jsonl"
        payload = {
            "schema_version": 1,
            "cohort": cohort,
            "collector_epoch": epoch,
            "run_id": run_id,
            "partition": partition,
            "state": state,
        }
        if restore_verified_at:
            payload["restore_verified_at"] = restore_verified_at
        if restore_verified:
            payload["restore_verified"] = restore_verified
        path.write_text(json.dumps(payload), encoding="utf-8")
        receipts.append(path)
    return receipts


def _write_cohort_fullscan(
    root: Path,
    cohort: str,
    feeds: list[tuple[str, str, str]],
    *,
    epoch: str = "epoch-v1",
    run_id: str = "run-v1",
    status: str = "PASS",
) -> Path:
    report = root / f"full_scan_{cohort}_report.json"
    inputs = [f"raw/{exch}/{strm}/{mkt}/{mkt}_{cohort}.jsonl" for exch, strm, mkt in feeds]
    report.write_text(
        json.dumps(
            {
                "cohort": cohort,
                "epoch": epoch,
                "run_id": run_id,
                "inputs": inputs,
                "integrity": {
                    "totals": {
                        "status": status,
                        "files": len(inputs),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return report


def test_frozen_feed_universe_count() -> None:
    feeds = SoakAuditor72H.get_expected_feed_universe()
    assert len(feeds) == 76


def test_complete_cohort_with_76_receipts_and_fullscan_passes(tmp_path: Path) -> None:
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, cohort, feeds)
    scan = _write_cohort_fullscan(tmp_path, cohort, feeds)

    result = validate_archive_evidence_coverage(
        [cohort], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert result["blockers"] == []
    assert result["receipt_coverage"] == 1
    assert result["fullscan_coverage"] == 1
    assert result.get("total_qualifying_receipts") == 76


def test_single_receipt_reproduces_false_positive_hole(tmp_path: Path) -> None:
    """1/76 receipt must FAIL with ARCHIVE_RECEIPT_MISSING (current code falsely passes this)."""
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    # Only write 1 feed out of 76
    single_receipt = _write_cohort_receipts(tmp_path, cohort, [feeds[0]])
    scan = _write_cohort_fullscan(tmp_path, cohort, feeds)

    result = validate_archive_evidence_coverage(
        [cohort], single_receipt, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert any("ARCHIVE_RECEIPT_MISSING" in item for item in result["blockers"])
    assert result["receipt_coverage"] == 0
    assert cohort in result["missing_receipt_cohorts"]


def test_75_of_76_receipts_is_hard_failure(tmp_path: Path) -> None:
    """75/76 receipts (1 missing) must FAIL with ARCHIVE_RECEIPT_MISSING."""
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    # Write 75 out of 76 feeds
    partial_receipts = _write_cohort_receipts(tmp_path, cohort, feeds[:75])
    scan = _write_cohort_fullscan(tmp_path, cohort, feeds)

    result = validate_archive_evidence_coverage(
        [cohort], partial_receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert any("ARCHIVE_RECEIPT_MISSING" in item for item in result["blockers"])
    assert result["receipt_coverage"] == 0
    missing_feed = feeds[75]
    diag = result.get("per_cohort_receipt_diagnostics", {}).get(cohort, {})
    if diag:
        assert diag["qualifying"] == 75
        assert any(missing_feed[2] in m for m in diag["missing"])


def test_duplicate_receipt_cannot_substitute_missing_feed(tmp_path: Path) -> None:
    """76 receipts where one feed is duplicated and another missing must FAIL."""
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, cohort, feeds[:75])

    # Add duplicate of feeds[0] with a distinct filename
    dup_path = tmp_path / f"dup_{cohort}.archive-receipt.json"
    dup_partition = f"raw/{feeds[0][0]}/{feeds[0][1]}/{feeds[0][2]}/{feeds[0][2]}_{cohort}.jsonl"
    dup_path.write_text(
        json.dumps(
            {
                "cohort": cohort,
                "collector_epoch": "epoch-v1",
                "run_id": "run-v1",
                "partition": dup_partition,
                "state": "CLEANUP_ELIGIBLE",
                "restore_verified_at": "2026-09-09T00:00:00+00:00",
                "restore_verified": True,
            }
        ),
        encoding="utf-8",
    )
    receipts.append(dup_path)
    assert len(receipts) == 76  # 76 files present, but only 75 unique feeds

    scan = _write_cohort_fullscan(tmp_path, cohort, feeds)
    result = validate_archive_evidence_coverage(
        [cohort], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert any("ARCHIVE_RECEIPT_MISSING" in item for item in result["blockers"])
    assert result["receipt_coverage"] == 0


def test_receipt_wrong_feed_attributes_rejected(tmp_path: Path) -> None:
    """Receipt with wrong market, wrong stream, or wrong cohort in partition must FAIL."""
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, cohort, feeds[:75])

    # Add 76th receipt with unknown/wrong market
    wrong_path = tmp_path / f"wrong_mkt_{cohort}.archive-receipt.json"
    wrong_path.write_text(
        json.dumps(
            {
                "cohort": cohort,
                "collector_epoch": "epoch-v1",
                "run_id": "run-v1",
                "partition": f"raw/bithumb/orderbook/KRW-NONEXISTENT/KRW-NONEXISTENT_{cohort}.jsonl",
                "state": "CLEANUP_ELIGIBLE",
                "restore_verified_at": "2026-09-09T00:00:00+00:00",
                "restore_verified": True,
            }
        ),
        encoding="utf-8",
    )
    receipts.append(wrong_path)

    scan = _write_cohort_fullscan(tmp_path, cohort, feeds)
    result = validate_archive_evidence_coverage(
        [cohort], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert any("ARCHIVE_RECEIPT_MISSING" in item for item in result["blockers"])


def test_receipt_non_terminal_state_rejected(tmp_path: Path) -> None:
    """Non-terminal states (DISCOVERED, RAW_VERIFIED, COMPRESSED, ARCHIVED, FAILED) must FAIL."""
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    scan = _write_cohort_fullscan(tmp_path, cohort, feeds)

    for bad_state in ("DISCOVERED", "RAW_VERIFIED", "COMPRESSED", "ARCHIVED", "FAILED"):
        sub_dir = tmp_path / bad_state
        sub_dir.mkdir()
        receipts = _write_cohort_receipts(sub_dir, cohort, feeds, state=bad_state)
        result = validate_archive_evidence_coverage(
            [cohort], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
        )
        assert result["receipt_coverage"] == 0
        assert any(
            "ARCHIVE_RECEIPT_MISSING" in item or "RECEIPT_INVALID_STATE" in item
            for item in result["blockers"]
        )

    # Terminal states must pass
    for good_state in ("CLEANUP_ELIGIBLE", "CLEANED", "RESTORE_VERIFIED"):
        sub_dir = tmp_path / good_state
        sub_dir.mkdir()
        receipts = _write_cohort_receipts(sub_dir, cohort, feeds, state=good_state)
        result = validate_archive_evidence_coverage(
            [cohort], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
        )
        assert result["receipt_coverage"] == 1
        assert result["blockers"] == []


def test_fullscan_with_only_sample_partition_rejected(tmp_path: Path) -> None:
    """Fullscan report with only 1 sample partition must NOT qualify official cohort."""
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, cohort, feeds)

    # Fullscan report with only 1 feed in inputs
    partial_scan = _write_cohort_fullscan(tmp_path, cohort, [feeds[0]])
    result = validate_archive_evidence_coverage(
        [cohort], receipts, [partial_scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert result["fullscan_coverage"] == 0
    assert any("FULLSCAN_INPUTS_INCOMPLETE" in item or "FULLSCAN_COHORT_COVERAGE_INCOMPLETE" in item for item in result["blockers"])


def test_fullscan_incomplete_inputs_rejected(tmp_path: Path) -> None:
    """Fullscan report covering 75/76 feeds must FAIL."""
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, cohort, feeds)

    partial_scan = _write_cohort_fullscan(tmp_path, cohort, feeds[:75])
    result = validate_archive_evidence_coverage(
        [cohort], receipts, [partial_scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert result["fullscan_coverage"] == 0
    assert any("FULLSCAN_INPUTS_INCOMPLETE" in item or "FULLSCAN_COHORT_COVERAGE_INCOMPLETE" in item for item in result["blockers"])


def test_fullscan_wrong_date_same_hh_in_inputs_rejected(tmp_path: Path) -> None:
    """Fullscan report containing inputs from a different date must FAIL."""
    cohort = "2026-09-06_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, cohort, feeds)

    # Fullscan report with inputs claiming 2026-09-05_05 for 2026-09-06_05
    report = tmp_path / f"full_scan_{cohort}_report.json"
    inputs = [f"raw/{exch}/{strm}/{mkt}/{mkt}_2026-09-05_05.jsonl" for exch, strm, mkt in feeds]
    report.write_text(
        json.dumps(
            {
                "cohort": cohort,
                "epoch": "epoch-v1",
                "run_id": "run-v1",
                "inputs": inputs,
                "integrity": {"totals": {"status": "PASS", "files": len(inputs)}},
            }
        ),
        encoding="utf-8",
    )

    result = validate_archive_evidence_coverage(
        [cohort], receipts, [report], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert result["fullscan_coverage"] == 0
    assert any("FULLSCAN_INPUTS_INCOMPLETE" in item or "FULLSCAN_COHORT_COVERAGE_INCOMPLETE" in item for item in result["blockers"])


def test_missing_middle_fullscan_is_hard_failure(tmp_path: Path) -> None:
    cohorts = ["2026-09-05_05", "2026-09-06_05", "2026-09-07_05"]
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts: list[Path] = []
    scans: list[Path] = []
    for c in cohorts:
        receipts.extend(_write_cohort_receipts(tmp_path, c, feeds))
        scans.append(_write_cohort_fullscan(tmp_path, c, feeds))
    scans[1].unlink()

    result = validate_archive_evidence_coverage(
        cohorts, receipts, [scans[0], scans[2]], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert any("FULLSCAN_COHORT_COVERAGE_INCOMPLETE" in item for item in result["blockers"])
    assert result["missing_fullscan_cohorts"] == ["2026-09-06_05"]


def test_wrong_date_same_hour_and_legacy_report_cannot_substitute(tmp_path: Path) -> None:
    expected = ["2026-09-06_05"]
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, "2026-09-06_05", feeds)
    wrong = tmp_path / "full_scan_2026-09-06_05_report.json"
    wrong.write_text(
        json.dumps(
            {
                "cohort": "2026-09-05_05",
                "epoch": "epoch-v1",
                "run_id": "run-v1",
                "integrity": {"totals": {"status": "PASS"}},
            }
        ),
        encoding="utf-8",
    )
    legacy = tmp_path / "full_scan_05_report.json"
    legacy.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")

    result = validate_archive_evidence_coverage(
        expected, receipts, [wrong, legacy], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert result["fullscan_coverage"] == 0
    assert result["legacy_fullscan_artifacts"] == [legacy.name]
    assert any("FULLSCAN_COHORT_COVERAGE_INCOMPLETE" in item for item in result["blockers"])


def test_wrong_date_receipt_cannot_satisfy_expected_cohort(tmp_path: Path) -> None:
    feeds = SoakAuditor72H.get_expected_feed_universe()
    scan = _write_cohort_fullscan(tmp_path, "2026-09-06_05", feeds)
    receipts = _write_cohort_receipts(tmp_path, "2026-09-05_05", feeds)

    result = validate_archive_evidence_coverage(
        ["2026-09-06_05"], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert any("ARCHIVE_RECEIPT_MISSING" in item for item in result["blockers"])


def test_receipt_without_restore_verification_is_hard_failure(tmp_path: Path) -> None:
    feeds = SoakAuditor72H.get_expected_feed_universe()
    scan = _write_cohort_fullscan(tmp_path, "2026-09-05_05", feeds)
    receipts = _write_cohort_receipts(
        tmp_path, "2026-09-05_05", feeds, restore_verified_at=None, restore_verified=False
    )

    result = validate_archive_evidence_coverage(
        ["2026-09-05_05"], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert any("RESTORE_MISMATCH" in item for item in result["blockers"])

