"""Per-cohort archive evidence gates for official post-remediation audits."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_72h_soak import validate_archive_evidence_coverage


def _write_valid_evidence(root: Path, cohorts: list[str]) -> tuple[list[Path], list[Path]]:
    receipts: list[Path] = []
    scans: list[Path] = []
    for cohort in cohorts:
        receipt = root / f"BTC_KRW_{cohort}.jsonl.archive-receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "cohort": cohort,
                    "collector_epoch": "epoch-v1",
                    "run_id": "run-v1",
                    "restore_verified_at": "2026-09-09T00:00:00+00:00",
                    "state": "CLEANUP_ELIGIBLE",
                }
            ),
            encoding="utf-8",
        )
        report = root / f"full_scan_{cohort}_report.json"
        report.write_text(
            json.dumps(
                {
                    "cohort": cohort,
                    "epoch": "epoch-v1",
                    "run_id": "run-v1",
                    "integrity": {"totals": {"status": "PASS"}},
                }
            ),
            encoding="utf-8",
        )
        receipts.append(receipt)
        scans.append(report)
    return receipts, scans


def test_all_72_expected_cohorts_require_72_bound_receipts_and_scans(tmp_path: Path) -> None:
    cohorts = [f"2026-09-{5 + day:02d}_{hour:02d}" for day in range(3) for hour in range(24)]
    receipts, scans = _write_valid_evidence(tmp_path, cohorts)

    result = validate_archive_evidence_coverage(
        cohorts, receipts, scans, expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert result["blockers"] == []
    assert result["receipt_coverage"] == 72
    assert result["fullscan_coverage"] == 72


def test_missing_middle_fullscan_is_hard_failure(tmp_path: Path) -> None:
    cohorts = ["2026-09-05_05", "2026-09-06_05", "2026-09-07_05"]
    receipts, scans = _write_valid_evidence(tmp_path, cohorts)
    scans[1].unlink()

    result = validate_archive_evidence_coverage(
        cohorts, receipts, [scans[0], scans[2]], expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert any("FULLSCAN_COHORT_COVERAGE_INCOMPLETE" in item for item in result["blockers"])
    assert result["missing_fullscan_cohorts"] == ["2026-09-06_05"]


def test_wrong_date_same_hour_and_legacy_report_cannot_substitute(tmp_path: Path) -> None:
    expected = ["2026-09-06_05"]
    receipts, _ = _write_valid_evidence(tmp_path, expected)
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
    _, scans = _write_valid_evidence(tmp_path, ["2026-09-06_05"])
    receipt = tmp_path / "BTC_KRW_2026-09-06_05.jsonl.archive-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "cohort": "2026-09-05_05",
                "collector_epoch": "epoch-v1",
                "run_id": "run-v1",
                "restore_verified_at": "2026-09-09T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    result = validate_archive_evidence_coverage(
        ["2026-09-06_05"], [receipt], scans, expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert any("ARCHIVE_RECEIPT_MISSING" in item for item in result["blockers"])


def test_receipt_without_restore_verification_is_hard_failure(tmp_path: Path) -> None:
    receipts, scans = _write_valid_evidence(tmp_path, ["2026-09-05_05"])
    payload = json.loads(receipts[0].read_text(encoding="utf-8"))
    payload.pop("restore_verified_at")
    receipts[0].write_text(json.dumps(payload), encoding="utf-8")

    result = validate_archive_evidence_coverage(
        ["2026-09-05_05"], receipts, scans, expected_epoch="epoch-v1", expected_run_id="run-v1"
    )

    assert any("RESTORE_MISMATCH" in item for item in result["blockers"])
