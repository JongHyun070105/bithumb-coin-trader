"""Per-cohort archive evidence gates for official post-remediation audits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest

from scripts.audit_72h_soak import (
    SoakAuditor72H,
    validate_archive_evidence_coverage,
    validate_v3_coverage_evidence,
)
from bithumb_coin_trader.evidence_hashing import canonical_sha256


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


def _write_cohort_fullscan_inputs(
    cohort: str,
    feeds: list[tuple[str, str, str]],
    *,
    include_raw: bool = True,
    include_compressed: bool = True,
) -> list[str]:
    inputs: list[str] = []
    if include_raw:
        for exch, strm, mkt in feeds:
            inputs.append(f"raw/{exch}/{strm}/{mkt}/{mkt}_{cohort}.jsonl")
    if include_compressed:
        for exch, strm, mkt in feeds:
            inputs.append(f"compressed/{exch}/{strm}/{mkt}/{mkt}_{cohort}.jsonl.zst")
    return inputs


def _write_cohort_fullscan(
    root: Path,
    cohort: str,
    feeds: list[tuple[str, str, str]],
    *,
    epoch: str = "epoch-v1",
    run_id: str = "run-v1",
    status: str = "PASS",
    inputs: list[str] | None = None,
    explicit_inputs: bool = True,
    files_count: int | None = None,
) -> Path:
    report = root / f"full_scan_{cohort}_report.json"
    if inputs is None:
        inputs = _write_cohort_fullscan_inputs(cohort, feeds, include_raw=True, include_compressed=True)
    payload: dict[str, Any] = {
        "cohort": cohort,
        "epoch": epoch,
        "run_id": run_id,
        "integrity": {
            "totals": {
                "status": status,
                "files": files_count if files_count is not None else len(inputs),
            }
        },
    }
    if explicit_inputs:
        payload["inputs"] = inputs
    report.write_text(json.dumps(payload), encoding="utf-8")
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


def test_fullscan_count_only_rejected(tmp_path: Path) -> None:
    """1. COUNT-ONLY REPORT: status PASS, files=152, but NO explicit inputs -> HARD FAIL."""
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, cohort, feeds)
    scan = _write_cohort_fullscan(tmp_path, cohort, feeds, explicit_inputs=False, files_count=152)

    result = validate_archive_evidence_coverage(
        [cohort], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert result["fullscan_coverage"] == 0
    assert any("FULLSCAN_INPUTS_INCOMPLETE" in item or "FULLSCAN_COHORT_COVERAGE_INCOMPLETE" in item for item in result["blockers"])


def test_fullscan_raw_only_rejected(tmp_path: Path) -> None:
    """2. RAW-ONLY: 76 RAW, 0 COMPRESSED -> FAIL."""
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, cohort, feeds)
    raw_inputs = _write_cohort_fullscan_inputs(cohort, feeds, include_raw=True, include_compressed=False)
    scan = _write_cohort_fullscan(tmp_path, cohort, feeds, inputs=raw_inputs)

    result = validate_archive_evidence_coverage(
        [cohort], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert result["fullscan_coverage"] == 0
    assert any("FULLSCAN_INPUTS_INCOMPLETE" in item or "FULLSCAN_COHORT_COVERAGE_INCOMPLETE" in item for item in result["blockers"])


def test_fullscan_compressed_only_rejected(tmp_path: Path) -> None:
    """3. COMPRESSED-ONLY: 0 RAW, 76 COMPRESSED -> FAIL."""
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, cohort, feeds)
    comp_inputs = _write_cohort_fullscan_inputs(cohort, feeds, include_raw=False, include_compressed=True)
    scan = _write_cohort_fullscan(tmp_path, cohort, feeds, inputs=comp_inputs)

    result = validate_archive_evidence_coverage(
        [cohort], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert result["fullscan_coverage"] == 0
    assert any("FULLSCAN_INPUTS_INCOMPLETE" in item or "FULLSCAN_COHORT_COVERAGE_INCOMPLETE" in item for item in result["blockers"])


def test_fullscan_one_compressed_missing(tmp_path: Path) -> None:
    """4. ONE COMPRESSED MISSING: 76 RAW, 75 COMPRESSED -> FAIL."""
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, cohort, feeds)
    raw_inputs = _write_cohort_fullscan_inputs(cohort, feeds, include_raw=True, include_compressed=False)
    comp_inputs_75 = _write_cohort_fullscan_inputs(cohort, feeds[1:], include_raw=False, include_compressed=True)
    inputs = sorted(raw_inputs + comp_inputs_75)
    scan = _write_cohort_fullscan(tmp_path, cohort, feeds, inputs=inputs)

    result = validate_archive_evidence_coverage(
        [cohort], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert result["fullscan_coverage"] == 0
    assert any("FULLSCAN_INPUTS_INCOMPLETE" in item or "FULLSCAN_COHORT_COVERAGE_INCOMPLETE" in item for item in result["blockers"])


def test_fullscan_one_raw_missing(tmp_path: Path) -> None:
    """5. ONE RAW MISSING: 75 RAW, 76 COMPRESSED -> FAIL."""
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, cohort, feeds)
    raw_inputs_75 = _write_cohort_fullscan_inputs(cohort, feeds[1:], include_raw=True, include_compressed=False)
    comp_inputs = _write_cohort_fullscan_inputs(cohort, feeds, include_raw=False, include_compressed=True)
    inputs = sorted(raw_inputs_75 + comp_inputs)
    scan = _write_cohort_fullscan(tmp_path, cohort, feeds, inputs=inputs)

    result = validate_archive_evidence_coverage(
        [cohort], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert result["fullscan_coverage"] == 0
    assert any("FULLSCAN_INPUTS_INCOMPLETE" in item or "FULLSCAN_COHORT_COVERAGE_INCOMPLETE" in item for item in result["blockers"])


def test_fullscan_complete_152_inputs(tmp_path: Path) -> None:
    """6. COMPLETE: 76 RAW, 76 COMPRESSED -> PASS."""
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, cohort, feeds)
    inputs = _write_cohort_fullscan_inputs(cohort, feeds, include_raw=True, include_compressed=True)
    assert len(inputs) == 152
    scan = _write_cohort_fullscan(tmp_path, cohort, feeds, inputs=inputs)

    result = validate_archive_evidence_coverage(
        [cohort], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert result["fullscan_coverage"] == 1
    assert result["blockers"] == []


def test_fullscan_duplicate_modality_substitution(tmp_path: Path) -> None:
    """7. DUPLICATE MODALITY SUBSTITUTION: 76 RAW, 75 COMPRESSED + duplicate compressed -> FAIL."""
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, cohort, feeds)
    raw_inputs = _write_cohort_fullscan_inputs(cohort, feeds, include_raw=True, include_compressed=False)
    comp_inputs_75 = _write_cohort_fullscan_inputs(cohort, feeds[1:], include_raw=False, include_compressed=True)
    dup_comp = f"compressed/{feeds[1][0]}/{feeds[1][1]}/{feeds[1][2]}/{feeds[1][2]}_{cohort}.jsonl.zst"
    inputs = raw_inputs + comp_inputs_75 + [dup_comp]
    assert len(inputs) == 152
    scan = _write_cohort_fullscan(tmp_path, cohort, feeds, inputs=inputs)

    result = validate_archive_evidence_coverage(
        [cohort], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert result["fullscan_coverage"] == 0
    assert any("FULLSCAN_INPUTS_INCOMPLETE" in item or "FULLSCAN_COHORT_COVERAGE_INCOMPLETE" in item for item in result["blockers"])


def test_fullscan_wrong_date_compressed_substitution(tmp_path: Path) -> None:
    """8. WRONG-DATE COMPRESSED: 76 RAW, 75 COMPRESSED + 1 compressed from same HH on wrong date -> FAIL."""
    cohort = "2026-09-05_05"
    wrong_cohort = "2026-09-04_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, cohort, feeds)
    raw_inputs = _write_cohort_fullscan_inputs(cohort, feeds, include_raw=True, include_compressed=False)
    comp_inputs_75 = _write_cohort_fullscan_inputs(cohort, feeds[1:], include_raw=False, include_compressed=True)
    wrong_date_comp = f"compressed/{feeds[0][0]}/{feeds[0][1]}/{feeds[0][2]}/{feeds[0][2]}_{wrong_cohort}.jsonl.zst"
    inputs = raw_inputs + comp_inputs_75 + [wrong_date_comp]
    assert len(inputs) == 152
    scan = _write_cohort_fullscan(tmp_path, cohort, feeds, inputs=inputs)

    result = validate_archive_evidence_coverage(
        [cohort], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert result["fullscan_coverage"] == 0
    assert any("FULLSCAN_INPUTS_INCOMPLETE" in item or "FULLSCAN_COHORT_COVERAGE_INCOMPLETE" in item for item in result["blockers"])


def test_receipt_duplicate_contamination_rejected(tmp_path: Path) -> None:
    """76 valid expected receipts + duplicate qualifying receipt -> FAIL (RECEIPT_CONTAMINATED)."""
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, cohort, feeds)
    # Add a 77th receipt which is a duplicate of feeds[0] with a distinct path
    dup_path = tmp_path / f"dup_{feeds[0][0]}_{feeds[0][1]}_{feeds[0][2]}_{cohort}.archive-receipt.json"
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
    assert len(receipts) == 77
    scan = _write_cohort_fullscan(tmp_path, cohort, feeds)

    result = validate_archive_evidence_coverage(
        [cohort], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert result["receipt_coverage"] == 0
    assert any("RECEIPT_CONTAMINATED" in item or "ARCHIVE_RECEIPT_MISSING" in item for item in result["blockers"])


def test_receipt_unexpected_feed_contamination_rejected(tmp_path: Path) -> None:
    """76 valid expected receipts + unexpected foreign feed receipt -> FAIL (RECEIPT_CONTAMINATED)."""
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, cohort, feeds)
    # Add a 77th receipt with a foreign exchange/feed
    foreign_path = tmp_path / f"foreign_kraken_trade_{cohort}.archive-receipt.json"
    foreign_path.write_text(
        json.dumps(
            {
                "cohort": cohort,
                "collector_epoch": "epoch-v1",
                "run_id": "run-v1",
                "exchange": "kraken",
                "stream": "trade",
                "market": "BTC-USD",
                "partition": f"raw/kraken/trade/BTC-USD/BTC-USD_{cohort}.jsonl",
                "state": "CLEANUP_ELIGIBLE",
                "restore_verified_at": "2026-09-09T00:00:00+00:00",
                "restore_verified": True,
            }
        ),
        encoding="utf-8",
    )
    receipts.append(foreign_path)
    assert len(receipts) == 77
    scan = _write_cohort_fullscan(tmp_path, cohort, feeds)

    result = validate_archive_evidence_coverage(
        [cohort], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert result["receipt_coverage"] == 0
    assert any("RECEIPT_CONTAMINATED" in item or "ARCHIVE_RECEIPT_MISSING" in item for item in result["blockers"])


def test_receipt_identity_invalid_contamination_rejected(tmp_path: Path) -> None:
    """76 valid expected receipts + identity-invalid target-epoch receipt -> FAIL."""
    cohort = "2026-09-05_05"
    feeds = SoakAuditor72H.get_expected_feed_universe()
    receipts = _write_cohort_receipts(tmp_path, cohort, feeds)
    # Add an identity-invalid receipt
    bad_path = tmp_path / f"corrupt_{cohort}.archive-receipt.json"
    bad_path.write_text("CORRUPT_NOT_JSON{{{", encoding="utf-8")
    receipts.append(bad_path)
    scan = _write_cohort_fullscan(tmp_path, cohort, feeds)

    result = validate_archive_evidence_coverage(
        [cohort], receipts, [scan], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert result["receipt_coverage"] == 0
    assert any("RECEIPT_CORRUPT" in item or "RECEIPT_IDENTITY_INVALID" in item or "RECEIPT_CONTAMINATED" in item for item in result["blockers"])


# ---------------------------------------------------------------------------
# V3 Coverage Exact-Slot Tests
# ---------------------------------------------------------------------------

from bithumb_coin_trader.evidence_hashing import (
    canonical_sha256 as _canonical_sha256,
    file_sha256 as _cov_file_sha256,
)


def v3_bundle(
    root: Path,
    present: int,
    zero: int,
    failed: int,
    *,
    cohort: str = "2026-09-14_12",
    confirmation_utc: str = "2026-09-14T11:51:00Z",
    heartbeat_gap: float = 10.0,
    disconnect_count: int = 0,
    reconnect_count: int = 0,
    writer_error_count: int = 0,
    cohort_qualification: str = "QUALIFYING_FULL_HOUR",
    count_mismatch: bool = False,
    include_raw_for_zero: bool = False,
    candidate_cohorts: list[str] | None = None,
) -> dict[str, Any]:
    feeds = SoakAuditor72H.get_expected_feed_universe()
    if candidate_cohorts is None:
        candidate_cohorts = [cohort]

    feed_universe = [
        {"exchange": e, "stream": s, "market": m}
        for e, s, m in feeds
    ]

    contract: dict[str, Any] = {
        "schema_version": 2,
        "contract_type": "OFFICIAL_30H_V3_COVERAGE_CONTRACT",
        "collector_epoch": "epoch-v3",
        "collector_run_id": "run-v3",
        "actual_start_time_utc": "2026-09-14T11:55:00Z",
        "qualification_start_utc": "2026-09-14T12:00:00Z",
        "qualification_end_utc": "2026-09-14T13:00:00Z",
        "required_qualifying_full_hours": len(candidate_cohorts),
        "maximum_collection_window_seconds": 111600,
        "candidate_cohorts": candidate_cohorts,
        "expected_coverage_slots_per_cohort": 76,
        "heartbeat_policy": {
            "heartbeat_probe_interval_seconds": 10,
            "heartbeat_timeout_seconds": 10,
            "max_allowed_heartbeat_gap_seconds": {
                "bithumb": 30,
                "binance": 30,
                "upbit": 30,
            },
        },
        "feed_universe": feed_universe,
        "require_coverage_receipts": True,
        "require_state_dependent_fullscan": True,
    }
    contract["contract_sha256"] = _canonical_sha256(contract)

    cov_dir = root / "coverage" / cohort
    rcpt_dir = root / "archive-receipts"
    raw_dir = root / "raw"
    man_dir = root / "manifests"

    cov_dir.mkdir(parents=True, exist_ok=True)
    rcpt_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    man_dir.mkdir(parents=True, exist_ok=True)

    coverage_files: list[Path] = []
    receipt_files: list[Path] = []
    full_scan_reports: list[Path] = []
    scanned_inputs: list[str] = []

    # 1. Present slots
    for i in range(present):
        exch, strm, mkt = feeds[i]
        raw_p = raw_dir / exch / strm / mkt / f"{mkt}_{cohort}.jsonl"
        raw_p.parent.mkdir(parents=True, exist_ok=True)
        raw_p.write_text('{"event": "trade", "price": 100}\n' * 10, encoding="utf-8")
        raw_sha = _cov_file_sha256(raw_p)
        raw_size = raw_p.stat().st_size

        man_p = man_dir / exch / strm / mkt / f"manifest_{mkt}_{cohort}.json"
        man_p.parent.mkdir(parents=True, exist_ok=True)
        man_data = {
            "partition_path": str(raw_p.relative_to(root)),
            "sha256": raw_sha,
            "record_count": 10,
            "bytes": raw_size,
        }
        man_p.write_text(json.dumps(man_data), encoding="utf-8")
        man_sha = _cov_file_sha256(man_p)

        raw_rcpt_p = rcpt_dir / exch / strm / mkt / f"{mkt}_{cohort}.jsonl.archive-receipt.json"
        raw_rcpt_p.parent.mkdir(parents=True, exist_ok=True)
        raw_rcpt_data = {
            "schema_version": 3,
            "artifact_kind": "RAW_DATA",
            "cohort": cohort,
            "exchange": exch,
            "stream": strm,
            "market": mkt,
            "source_path": str(raw_p.relative_to(root)),
            "source_size": raw_size,
            "source_sha256": raw_sha,
            "source_record_count": 10,
            "manifest_path": str(man_p.relative_to(root)),
            "manifest_sha256": man_sha,
            "state": "CLEANUP_ELIGIBLE",
            "restore_verified_at": "2026-09-14T13:05:00Z",
            "collector_epoch": "epoch-v3",
            "run_id": "run-v3",
        }
        raw_rcpt_p.write_text(json.dumps(raw_rcpt_data), encoding="utf-8")
        raw_rcpt_sha = _cov_file_sha256(raw_rcpt_p)
        receipt_files.append(raw_rcpt_p)

        scanned_inputs.append(str(raw_p.relative_to(root)))

        binding_record_count = 10 if not count_mismatch else 15
        cov_p = cov_dir / exch / strm / f"{mkt}.coverage.json"
        cov_p.parent.mkdir(parents=True, exist_ok=True)
        cov_data: dict[str, Any] = {
            "schema_version": 1,
            "artifact_kind": "COVERAGE_EVIDENCE",
            "environment_id": "aws-apne2-research",
            "collector_epoch": "epoch-v3",
            "collector_run_id": "run-v3",
            "runtime_commit": "unknown",
            "runtime_config_fingerprint": "unknown",
            "cohort_utc": cohort,
            "interval_start_utc": "2026-09-14T12:00:00Z",
            "interval_end_utc": "2026-09-14T13:00:00Z",
            "cohort_qualification": cohort_qualification,
            "observation_start_utc": "2026-09-14T12:00:00Z",
            "observation_end_utc": "2026-09-14T13:00:00Z",
            "exchange": exch,
            "stream": strm,
            "market": mkt,
            "feed_identity": f"{exch}/{strm}/{mkt}",
            "configured": True,
            "coverage_state": "DATA_PRESENT",
            "event_count": 10,
            "first_event_timestamp": "2026-09-14T12:05:00Z",
            "last_event_timestamp": "2026-09-14T12:55:00Z",
            "session_segments": [
                {
                    "exchange": exch,
                    "session_id": f"sess-{i}",
                    "connected_at_utc": "2026-09-14T11:50:00Z",
                    "disconnected_at_utc": None,
                    "requested_feeds": [f"{exch}/{strm}/{mkt}"],
                    "requested_subscription_sha256": "h1",
                    "confirmation_method": "LIST_SUBSCRIPTIONS",
                    "confirmed_at_utc": confirmation_utc,
                    "confirmed_feeds": [f"{exch}/{strm}/{mkt}"],
                    "confirmed_subscription_sha256": "h2",
                    "response_evidence_sha256": "h3",
                    "heartbeat_observations_utc": [
                        "2026-09-14T12:00:00Z",
                        "2026-09-14T12:30:00Z",
                        "2026-09-14T13:00:00Z",
                    ],
                    "maximum_heartbeat_gap_seconds": heartbeat_gap,
                    "disconnect_reason": None,
                    "reconnect_successor_id": None,
                    "collector_epoch": "epoch-v3",
                    "collector_run_id": "run-v3",
                }
            ],
            "disconnect_count": disconnect_count,
            "reconnect_count": reconnect_count,
            "writer_error_count": writer_error_count,
            "queue_dropped_events": 0,
            "unpersisted_event_count": 0,
            "fatal_writer_error_type": None,
            "data_artifact_binding": {
                "raw_relative_path": str(raw_p.relative_to(root)),
                "raw_size": raw_size,
                "raw_sha256": raw_sha,
                "manifest_relative_path": str(man_p.relative_to(root)),
                "manifest_file_sha256": man_sha,
                "manifest_record_count": 10,
                "receipt_relative_path": str(raw_rcpt_p.relative_to(root)),
                "receipt_file_sha256": raw_rcpt_sha,
                "receipt_source_record_count": binding_record_count,
            },
            "failure_reason_codes": [],
            "closed_at_utc": "2026-09-14T13:00:05Z",
        }
        cov_data["evidence_sha256"] = _canonical_sha256(cov_data, excluded=("evidence_sha256",))
        cov_p.write_text(json.dumps(cov_data, indent=2), encoding="utf-8")
        coverage_files.append(cov_p)

        cov_rcpt_p = rcpt_dir / "coverage" / cohort / exch / strm / f"{mkt}.coverage.json.archive-receipt.json"
        cov_rcpt_p.parent.mkdir(parents=True, exist_ok=True)
        cov_rcpt_data = {
            "schema_version": 3,
            "artifact_kind": "COVERAGE_EVIDENCE",
            "cohort": cohort,
            "exchange": exch,
            "stream": strm,
            "market": mkt,
            "source_path": str(cov_p.relative_to(root)),
            "source_size": cov_p.stat().st_size,
            "source_sha256": _cov_file_sha256(cov_p),
            "source_record_count": None,
            "manifest_path": None,
            "manifest_sha256": None,
            "state": "CLEANUP_ELIGIBLE",
            "restore_verified_at": "2026-09-14T13:05:00Z",
            "collector_epoch": "epoch-v3",
            "run_id": "run-v3",
        }
        cov_rcpt_p.write_text(json.dumps(cov_rcpt_data), encoding="utf-8")
        receipt_files.append(cov_rcpt_p)

    # 2. Zero slots
    for i in range(present, present + zero):
        exch, strm, mkt = feeds[i]
        cov_p = cov_dir / exch / strm / f"{mkt}.coverage.json"
        cov_p.parent.mkdir(parents=True, exist_ok=True)
        cov_data = {
            "schema_version": 1,
            "artifact_kind": "COVERAGE_EVIDENCE",
            "environment_id": "aws-apne2-research",
            "collector_epoch": "epoch-v3",
            "collector_run_id": "run-v3",
            "runtime_commit": "unknown",
            "runtime_config_fingerprint": "unknown",
            "cohort_utc": cohort,
            "interval_start_utc": "2026-09-14T12:00:00Z",
            "interval_end_utc": "2026-09-14T13:00:00Z",
            "cohort_qualification": cohort_qualification,
            "observation_start_utc": "2026-09-14T12:00:00Z",
            "observation_end_utc": "2026-09-14T13:00:00Z",
            "exchange": exch,
            "stream": strm,
            "market": mkt,
            "feed_identity": f"{exch}/{strm}/{mkt}",
            "configured": True,
            "coverage_state": "VERIFIED_ZERO_EVENT",
            "event_count": 0,
            "first_event_timestamp": None,
            "last_event_timestamp": None,
            "session_segments": [
                {
                    "exchange": exch,
                    "session_id": f"sess-{i}",
                    "connected_at_utc": "2026-09-14T11:50:00Z",
                    "disconnected_at_utc": None,
                    "requested_feeds": [f"{exch}/{strm}/{mkt}"],
                    "requested_subscription_sha256": "h1",
                    "confirmation_method": "LIST_SUBSCRIPTIONS",
                    "confirmed_at_utc": confirmation_utc,
                    "confirmed_feeds": [f"{exch}/{strm}/{mkt}"],
                    "confirmed_subscription_sha256": "h2",
                    "response_evidence_sha256": "h3",
                    "heartbeat_observations_utc": [
                        "2026-09-14T12:00:00Z",
                        "2026-09-14T12:30:00Z",
                        "2026-09-14T13:00:00Z",
                    ],
                    "maximum_heartbeat_gap_seconds": heartbeat_gap,
                    "disconnect_reason": None,
                    "reconnect_successor_id": None,
                    "collector_epoch": "epoch-v3",
                    "collector_run_id": "run-v3",
                }
            ],
            "disconnect_count": disconnect_count,
            "reconnect_count": reconnect_count,
            "writer_error_count": writer_error_count,
            "queue_dropped_events": 0,
            "unpersisted_event_count": 0,
            "fatal_writer_error_type": None,
            "data_artifact_binding": None,
            "failure_reason_codes": [],
            "closed_at_utc": "2026-09-14T13:00:05Z",
        }
        if include_raw_for_zero:
            cov_data["data_artifact_binding"] = {
                "raw_relative_path": "dummy",
                "raw_size": 10,
                "raw_sha256": "dummy",
                "manifest_relative_path": "dummy",
                "manifest_file_sha256": "dummy",
                "manifest_record_count": 0,
                "receipt_relative_path": "dummy",
                "receipt_file_sha256": "dummy",
                "receipt_source_record_count": 0,
            }
        cov_data["evidence_sha256"] = _canonical_sha256(cov_data, excluded=("evidence_sha256",))
        cov_p.write_text(json.dumps(cov_data, indent=2), encoding="utf-8")
        coverage_files.append(cov_p)

        cov_rcpt_p = rcpt_dir / "coverage" / cohort / exch / strm / f"{mkt}.coverage.json.archive-receipt.json"
        cov_rcpt_p.parent.mkdir(parents=True, exist_ok=True)
        cov_rcpt_data = {
            "schema_version": 3,
            "artifact_kind": "COVERAGE_EVIDENCE",
            "cohort": cohort,
            "exchange": exch,
            "stream": strm,
            "market": mkt,
            "source_path": str(cov_p.relative_to(root)),
            "source_size": cov_p.stat().st_size,
            "source_sha256": _cov_file_sha256(cov_p),
            "source_record_count": None,
            "manifest_path": None,
            "manifest_sha256": None,
            "state": "CLEANUP_ELIGIBLE",
            "restore_verified_at": "2026-09-14T13:05:00Z",
            "collector_epoch": "epoch-v3",
            "run_id": "run-v3",
        }
        cov_rcpt_p.write_text(json.dumps(cov_rcpt_data), encoding="utf-8")
        receipt_files.append(cov_rcpt_p)

    # 3. Failed slots
    for i in range(present + zero, present + zero + failed):
        exch, strm, mkt = feeds[i]
        cov_p = cov_dir / exch / strm / f"{mkt}.coverage.json"
        cov_p.parent.mkdir(parents=True, exist_ok=True)
        cov_data = {
            "schema_version": 1,
            "artifact_kind": "COVERAGE_EVIDENCE",
            "environment_id": "aws-apne2-research",
            "collector_epoch": "epoch-v3",
            "collector_run_id": "run-v3",
            "runtime_commit": "unknown",
            "runtime_config_fingerprint": "unknown",
            "cohort_utc": cohort,
            "interval_start_utc": "2026-09-14T12:00:00Z",
            "interval_end_utc": "2026-09-14T13:00:00Z",
            "cohort_qualification": cohort_qualification,
            "observation_start_utc": "2026-09-14T12:00:00Z",
            "observation_end_utc": "2026-09-14T13:00:00Z",
            "exchange": exch,
            "stream": strm,
            "market": mkt,
            "feed_identity": f"{exch}/{strm}/{mkt}",
            "configured": True,
            "coverage_state": "FAILED",
            "event_count": 0,
            "first_event_timestamp": None,
            "last_event_timestamp": None,
            "session_segments": [
                {
                    "exchange": exch,
                    "session_id": f"sess-{i}",
                    "connected_at_utc": "2026-09-14T11:50:00Z",
                    "disconnected_at_utc": None,
                    "requested_feeds": [f"{exch}/{strm}/{mkt}"],
                    "requested_subscription_sha256": "h1",
                    "confirmation_method": "LIST_SUBSCRIPTIONS",
                    "confirmed_at_utc": confirmation_utc,
                    "confirmed_feeds": [f"{exch}/{strm}/{mkt}"],
                    "confirmed_subscription_sha256": "h2",
                    "response_evidence_sha256": "h3",
                    "heartbeat_observations_utc": [
                        "2026-09-14T12:00:00Z",
                        "2026-09-14T12:30:00Z",
                        "2026-09-14T13:00:00Z",
                    ],
                    "maximum_heartbeat_gap_seconds": heartbeat_gap,
                    "disconnect_reason": None,
                    "reconnect_successor_id": None,
                    "collector_epoch": "epoch-v3",
                    "collector_run_id": "run-v3",
                }
            ],
            "disconnect_count": disconnect_count,
            "reconnect_count": reconnect_count,
            "writer_error_count": 1,
            "queue_dropped_events": 0,
            "unpersisted_event_count": 0,
            "fatal_writer_error_type": None,
            "data_artifact_binding": None,
            "failure_reason_codes": ["WRITER_HEALTH_DEGRADED"],
            "closed_at_utc": "2026-09-14T13:00:05Z",
        }
        cov_data["evidence_sha256"] = _canonical_sha256(cov_data, excluded=("evidence_sha256",))
        cov_p.write_text(json.dumps(cov_data, indent=2), encoding="utf-8")
        coverage_files.append(cov_p)

        cov_rcpt_p = rcpt_dir / "coverage" / cohort / exch / strm / f"{mkt}.coverage.json.archive-receipt.json"
        cov_rcpt_p.parent.mkdir(parents=True, exist_ok=True)
        cov_rcpt_data = {
            "schema_version": 3,
            "artifact_kind": "COVERAGE_EVIDENCE",
            "cohort": cohort,
            "exchange": exch,
            "stream": strm,
            "market": mkt,
            "source_path": str(cov_p.relative_to(root)),
            "source_size": cov_p.stat().st_size,
            "source_sha256": _cov_file_sha256(cov_p),
            "source_record_count": None,
            "manifest_path": None,
            "manifest_sha256": None,
            "state": "CLEANUP_ELIGIBLE",
            "restore_verified_at": "2026-09-14T13:05:00Z",
            "collector_epoch": "epoch-v3",
            "run_id": "run-v3",
        }
        cov_rcpt_p.write_text(json.dumps(cov_rcpt_data), encoding="utf-8")
        receipt_files.append(cov_rcpt_p)

    # 4. State-dependent fullscan report
    if present > 0:
        fs_p = rcpt_dir / f"full_scan_{cohort}_report.json"
        fs_data = {
            "cohort": cohort,
            "epoch": "epoch-v3",
            "run_id": "run-v3",
            "status": "PASS",
            "inputs": scanned_inputs,
            "integrity": {
                "totals": {
                    "status": "PASS",
                    "files": len(scanned_inputs),
                    "records": present * 10,
                }
            },
        }
        fs_p.write_text(json.dumps(fs_data), encoding="utf-8")
        full_scan_reports.append(fs_p)

    return {
        "contract": contract,
        "coverage_files": coverage_files,
        "receipt_files": receipt_files,
        "full_scan_reports": full_scan_reports,
    }


def one_positive_bundle(tmp_path: Path, confirmation: str = "2026-09-14T11:51:00Z") -> dict[str, Any]:
    return v3_bundle(tmp_path, present=76, zero=0, failed=0, confirmation_utc=confirmation)


def one_zero_bundle(tmp_path: Path) -> dict[str, Any]:
    return v3_bundle(tmp_path, present=0, zero=76, failed=0)


@pytest.mark.parametrize(
    "present,zero,failed,status",
    [
        (76, 0, 0, "PASS"),
        (74, 2, 0, "PASS"),
        (74, 1, 0, "FAIL"),
        (75, 0, 1, "FAIL"),
    ],
)
def test_verdict_uses_coverage_slots(present: int, zero: int, failed: int, status: str, tmp_path: Path) -> None:
    result = validate_v3_coverage_evidence(**v3_bundle(tmp_path, present, zero, failed))
    assert result["status"] == status


def test_positive_data_still_requires_early_subscription(tmp_path: Path) -> None:
    result = validate_v3_coverage_evidence(
        **one_positive_bundle(tmp_path, confirmation="2026-09-14T12:00:01Z")
    )
    assert "SUBSCRIPTION_NOT_CONFIRMED_BEFORE_INTERVAL" in result["blockers"]


def test_zero_does_not_add_market_records(tmp_path: Path) -> None:
    result = validate_v3_coverage_evidence(**one_zero_bundle(tmp_path))
    assert result["scientific_record_count"] == 0


def test_foreign_slot_rejected(tmp_path: Path) -> None:
    bundle = v3_bundle(tmp_path, 76, 0, 0)
    foreign_p = tmp_path / "coverage" / "2026-09-14_12" / "kraken" / "trade" / "BTC-USD.coverage.json"
    foreign_p.parent.mkdir(parents=True, exist_ok=True)
    cov_data = {
        "schema_version": 1,
        "artifact_kind": "COVERAGE_EVIDENCE",
        "cohort_utc": "2026-09-14_12",
        "exchange": "kraken",
        "stream": "trade",
        "market": "BTC-USD",
        "coverage_state": "DATA_PRESENT",
        "event_count": 5,
        "interval_start_utc": "2026-09-14T12:00:00Z",
        "interval_end_utc": "2026-09-14T13:00:00Z",
        "cohort_qualification": "QUALIFYING_FULL_HOUR",
        "session_segments": [],
    }
    cov_data["evidence_sha256"] = _canonical_sha256(cov_data, excluded=("evidence_sha256",))
    foreign_p.write_text(json.dumps(cov_data), encoding="utf-8")
    bundle["coverage_files"].append(foreign_p)

    result = validate_v3_coverage_evidence(**bundle)
    assert result["status"] == "FAIL"
    assert any("SLOT_FOREIGN" in b for b in result["blockers"])


def test_duplicate_slot_rejected(tmp_path: Path) -> None:
    bundle = v3_bundle(tmp_path, 76, 0, 0)
    dup_p = tmp_path / "coverage" / "dup.coverage.json"
    dup_p.write_bytes(bundle["coverage_files"][0].read_bytes())
    bundle["coverage_files"].append(dup_p)

    result = validate_v3_coverage_evidence(**bundle)
    assert result["status"] == "FAIL"
    assert any("SLOT_DUPLICATE" in b for b in result["blockers"])


def test_missing_slot_rejected(tmp_path: Path) -> None:
    bundle = v3_bundle(tmp_path, 76, 0, 0)
    bundle["coverage_files"].pop()

    result = validate_v3_coverage_evidence(**bundle)
    assert result["status"] == "FAIL"
    assert any("SLOT_MISSING" in b for b in result["blockers"])


def test_coverage_receipt_missing_rejected(tmp_path: Path) -> None:
    bundle = v3_bundle(tmp_path, 76, 0, 0)
    cov_receipts = [r for r in bundle["receipt_files"] if ".coverage." in r.name]
    cov_receipts[0].unlink()
    bundle["receipt_files"].remove(cov_receipts[0])

    result = validate_v3_coverage_evidence(**bundle)
    assert result["status"] == "FAIL"
    assert any("COVERAGE_RECEIPT_MISSING" in b for b in result["blockers"])


def test_coverage_receipt_unverified_rejected(tmp_path: Path) -> None:
    bundle = v3_bundle(tmp_path, 76, 0, 0)
    cov_receipts = [r for r in bundle["receipt_files"] if ".coverage." in r.name]
    data = json.loads(cov_receipts[0].read_text(encoding="utf-8"))
    data["restore_verified_at"] = None
    data["restore_verified"] = False
    cov_receipts[0].write_text(json.dumps(data), encoding="utf-8")

    result = validate_v3_coverage_evidence(**bundle)
    assert result["status"] == "FAIL"
    assert any("COVERAGE_RECEIPT_RESTORE_MISMATCH" in b for b in result["blockers"])


def test_coverage_hash_tampered_rejected(tmp_path: Path) -> None:
    bundle = v3_bundle(tmp_path, 76, 0, 0)
    cov_file = bundle["coverage_files"][0]
    data = json.loads(cov_file.read_text(encoding="utf-8"))
    data["event_count"] = 999
    cov_file.write_text(json.dumps(data), encoding="utf-8")

    result = validate_v3_coverage_evidence(**bundle)
    assert result["status"] == "FAIL"
    assert any("COVERAGE_HASH_MISMATCH" in b for b in result["blockers"])


def test_positive_count_mismatch_rejected(tmp_path: Path) -> None:
    bundle = v3_bundle(tmp_path, 76, 0, 0, count_mismatch=True)
    result = validate_v3_coverage_evidence(**bundle)
    assert result["status"] == "FAIL"
    assert any("RECORD_COUNT_MISMATCH" in b for b in result["blockers"])


def test_zero_with_raw_binding_rejected(tmp_path: Path) -> None:
    bundle = v3_bundle(tmp_path, 0, 76, 0, include_raw_for_zero=True)
    result = validate_v3_coverage_evidence(**bundle)
    assert result["status"] == "FAIL"
    assert any("UNEXPECTED_DATA_BINDING_FOR_ZERO_EVENT" in b for b in result["blockers"])


def test_heartbeat_gap_boundary_enforced(tmp_path: Path) -> None:
    bundle = v3_bundle(tmp_path, 76, 0, 0, heartbeat_gap=31.0)
    result = validate_v3_coverage_evidence(**bundle)
    assert result["status"] == "FAIL"
    assert any("HEARTBEAT_GAP_EXCEEDED" in b for b in result["blockers"])


def test_no_replacement_cohort_allowed(tmp_path: Path) -> None:
    bundle = v3_bundle(tmp_path, 76, 0, 0, cohort="2026-09-14_13", candidate_cohorts=["2026-09-14_12"])
    result = validate_v3_coverage_evidence(**bundle)
    assert result["status"] == "FAIL"
    assert any("COHORT_UNEXPECTED" in b for b in result["blockers"])


def test_missing_coverage_receipt_hash_fails_closed(tmp_path: Path) -> None:
    bundle = v3_bundle(tmp_path, 76, 0, 0)
    for rp in bundle["receipt_files"]:
        if ".coverage." in rp.name:
            data = json.loads(rp.read_text(encoding="utf-8"))
            data.pop("source_sha256", None)
            data.pop("raw_sha256", None)
            rp.write_text(json.dumps(data), encoding="utf-8")
            break
    result = validate_v3_coverage_evidence(**bundle)
    assert result["status"] == "FAIL"
    assert any("COVERAGE_RECEIPT_HASH_MISSING" in b for b in result["blockers"])


def test_raw_receipt_invalid_state_rejected(tmp_path: Path) -> None:
    bundle = v3_bundle(tmp_path, 76, 0, 0)
    for rp in bundle["receipt_files"]:
        if ".coverage." not in rp.name:
            data = json.loads(rp.read_text(encoding="utf-8"))
            data["state"] = "FAILED"
            rp.write_text(json.dumps(data), encoding="utf-8")
            break
    result = validate_v3_coverage_evidence(**bundle)
    assert result["status"] == "FAIL"
    assert any("RAW_RECEIPT_INVALID_STATE" in b for b in result["blockers"])


def test_missing_slot_recorded_in_coverage_slots(tmp_path: Path) -> None:
    bundle = v3_bundle(tmp_path, 75, 0, 0)
    result = validate_v3_coverage_evidence(**bundle)
    assert result["status"] == "FAIL"
    missing_slots = [s for s in result["coverage_slots"] if s["coverage_state"] == "MISSING"]
    assert len(missing_slots) == 1


def test_subscription_confirmation_timestamp_corrupt_fails_closed(tmp_path: Path) -> None:
    bundle = v3_bundle(tmp_path, 76, 0, 0, confirmation_utc="INVALID-TIMESTAMP")
    result = validate_v3_coverage_evidence(**bundle)
    assert result["status"] == "FAIL"
    assert any("SUBSCRIPTION_CONFIRMATION_TIMESTAMP_CORRUPT" in b for b in result["blockers"])


def test_heartbeat_observations_timestamp_corrupt_fails_closed(tmp_path: Path) -> None:
    bundle = v3_bundle(tmp_path, 76, 0, 0)
    cov_file = bundle["coverage_files"][0]
    data = json.loads(cov_file.read_text(encoding="utf-8"))
    data["session_segments"][0]["heartbeat_observations_utc"] = ["NOT-A-VALID-TIMESTAMP"]
    data["coverage_sha256"] = canonical_sha256(data, excluded=("coverage_sha256",))
    cov_file.write_text(json.dumps(data), encoding="utf-8")

    result = validate_v3_coverage_evidence(**bundle)
    assert result["status"] == "FAIL"
    assert any("HEARTBEAT_OBSERVATIONS_CORRUPT" in b for b in result["blockers"])


def test_missing_interval_coordinates_fails_closed(tmp_path: Path) -> None:
    bundle = v3_bundle(tmp_path, 76, 0, 0)
    cov_file = bundle["coverage_files"][0]
    data = json.loads(cov_file.read_text(encoding="utf-8"))
    data.pop("interval_start_utc", None)
    data["coverage_sha256"] = canonical_sha256(data, excluded=("coverage_sha256",))
    cov_file.write_text(json.dumps(data), encoding="utf-8")

    result = validate_v3_coverage_evidence(**bundle)
    assert result["status"] == "FAIL"
    assert any("INTERVAL_COORDINATES_MISSING" in b for b in result["blockers"])


def test_build_epoch_manifest_non_qualifying_coverage_state(tmp_path: Path) -> None:
    from scripts.build_epoch_manifest import build_epoch_manifest, verify_epoch_manifest
    bundle = v3_bundle(tmp_path, 76, 0, 0)
    contract_p = tmp_path / "contract.json"
    contract_p.write_text(json.dumps(bundle["contract"]), encoding="utf-8")

    # Set one coverage file to UNKNOWN state
    cov_file = bundle["coverage_files"][0]
    data = json.loads(cov_file.read_text(encoding="utf-8"))
    data["coverage_state"] = "UNKNOWN"
    data["coverage_sha256"] = canonical_sha256(data, excluded=("coverage_sha256",))
    cov_file.write_text(json.dumps(data), encoding="utf-8")

    manifest_p = tmp_path / "epoch_manifest.json"
    manifest = build_epoch_manifest(
        tmp_path,
        contract_path=contract_p,
        output_path=manifest_p,
        strict=False,
        mode="lenient",
    )
    assert manifest["status"] == "INCOMPLETE"
    assert manifest["sealed_complete"] is False
    assert manifest["failed_count"] == 1
    assert any("NON_QUALIFYING_COVERAGE_SLOT" in item for item in manifest["missing_items"])

    with pytest.raises(ValueError) as exc:
        verify_epoch_manifest(manifest_p, contract_path=contract_p)
    assert "EPOCH_MANIFEST_INCOMPLETE" in str(exc.value)


def test_build_epoch_manifest_v3_complete(tmp_path: Path) -> None:
    from scripts.build_epoch_manifest import build_epoch_manifest, verify_epoch_manifest
    bundle = v3_bundle(tmp_path, present=74, zero=2, failed=0)
    contract = bundle["contract"]
    contract_p = tmp_path / "epoch_contract.json"
    contract_p.write_text(json.dumps(contract, indent=2))

    manifest_p = tmp_path / "manifests/epoch_manifest.json"
    manifest = build_epoch_manifest(
        epoch_dir=tmp_path,
        contract_path=contract_p,
        output_path=manifest_p,
        strict=False,
        mode="lenient",
    )
    assert manifest["schema_version"] == "3.0.0"
    assert manifest["coverage_slots_count"] == 76
    assert manifest["data_present_count"] == 74
    assert manifest["verified_zero_count"] == 2
    assert manifest["failed_count"] == 0
    assert manifest["status"] == "SEALED_COMPLETE"
    assert manifest["sealed_complete"] is True
    assert len(manifest["missing_items"]) == 0

    verified = verify_epoch_manifest(manifest_p, contract_p)
    assert verified["epoch_manifest_sha256"] == manifest["epoch_manifest_sha256"]

