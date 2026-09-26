"""Unit and integration tests for Hour-Close Completeness Canary.

Verifies:
1. PASS when all 76 feeds have raw, coverage, compressed, and receipt.
2. Correct handling of VERIFIED_ZERO feeds according to contract.
3. Explicit FAIL when RAW is missing (detecting V4-style failure: coverage present but RAW 0/76).
4. Canary non-mutation invariant (strictly read-only on data stores).
5. Eligibility grace period enforcement.
6. Optional S3 evidence emission.
7. CLI runner integration and exit codes.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from bithumb_coin_trader.closed_hour_finalizer import SEALED_FEED_UNIVERSE
from bithumb_coin_trader.evidence_hashing import canonical_sha256
from bithumb_coin_trader.hour_close_canary import (
    CohortNotEligibleError,
    HourCloseCanary,
    HourCloseCanaryReport,
)
from bithumb_coin_trader.session_evidence import FeedIdentity
from scripts.run_hour_close_canary import main as cli_main


def _make_coverage_json(
    feed: FeedIdentity,
    cohort: str = "2026-09-14_12",
    coverage_state: str = "DATA_PRESENT",
    event_count: int = 10,
    closed_at_utc: str = "2026-09-14T13:05:00Z",
) -> dict[str, object]:
    cov_dict: dict[str, object] = {
        "schema_version": 1,
        "artifact_kind": "COVERAGE_EVIDENCE",
        "environment_id": "test-env",
        "collector_epoch": "epoch-1",
        "collector_run_id": "run-1",
        "runtime_commit": "test-commit",
        "runtime_config_fingerprint": "fp-test",
        "cohort_utc": cohort,
        "interval_start_utc": "2026-09-14T12:00:00Z",
        "interval_end_utc": "2026-09-14T13:00:00Z",
        "cohort_qualification": "QUALIFYING_FULL_HOUR",
        "observation_start_utc": "2026-09-14T12:00:00Z",
        "observation_end_utc": "2026-09-14T13:00:00Z",
        "exchange": feed.exchange,
        "stream": feed.stream,
        "market": feed.market,
        "feed_identity": feed.canonical,
        "configured": True,
        "coverage_state": coverage_state,
        "event_count": event_count if coverage_state == "DATA_PRESENT" else 0,
        "first_event_timestamp": "2026-09-14T12:05:00Z" if coverage_state == "DATA_PRESENT" else None,
        "last_event_timestamp": "2026-09-14T12:55:00Z" if coverage_state == "DATA_PRESENT" else None,
        "session_segments": [],
        "disconnect_count": 0,
        "reconnect_count": 0,
        "writer_error_count": 0,
        "queue_dropped_events": 0,
        "unpersisted_event_count": 0,
        "fatal_writer_error_type": None,
        "data_artifact_binding": None,
        "failure_reason_codes": [],
        "closed_at_utc": closed_at_utc,
        "evidence_sha256": "",
    }
    cov_dict["evidence_sha256"] = canonical_sha256(cov_dict, excluded=("evidence_sha256",))
    return cov_dict


def _make_receipt_json(
    feed: FeedIdentity,
    cohort: str = "2026-09-14_12",
    artifact_kind: str = "RAW_DATA",
    state: str = "RESTORE_VERIFIED",
    restore_verified_at: str = "2026-09-14T13:08:00Z",
) -> dict[str, object]:
    return {
        "schema_version": 3,
        "artifact_kind": artifact_kind,
        "state": state,
        "environment_id": "test-env",
        "run_id": "run-1",
        "collector_epoch": "epoch-1",
        "cohort": cohort,
        "exchange": feed.exchange,
        "stream": feed.stream,
        "market": feed.market,
        "source_path": f"some/path",
        "source_size": 1024,
        "source_sha256": "a" * 64,
        "source_record_count": 10,
        "restore_verified_at": restore_verified_at,
    }


def _setup_full_environment(
    tmp_path: Path,
    cohort: str = "2026-09-14_12",
    verified_zero_feeds: set[str] | None = None,
    omit_raw: bool = False,
) -> dict[str, Path]:
    raw_root = tmp_path / "raw"
    coverage_root = tmp_path / "coverage"
    compressed_root = tmp_path / "compressed"
    receipt_root = tmp_path / "archive-receipts"
    artifact_dir = tmp_path / "canary"

    for d in (raw_root, coverage_root, compressed_root, receipt_root, artifact_dir):
        d.mkdir(parents=True, exist_ok=True)

    dt_str, hour_str = cohort.split("_")
    zero_feeds = verified_zero_feeds or set()

    for feed in SEALED_FEED_UNIVERSE:
        clean_market = feed.market.replace("/", "-").replace(":", "-").lower()
        is_zero = feed.canonical in zero_feeds

        # 1. Coverage evidence
        cov_dir = coverage_root / cohort / feed.exchange.lower() / feed.stream.lower()
        cov_dir.mkdir(parents=True, exist_ok=True)
        cov_file = cov_dir / f"{feed.market}.coverage.json"
        cov_data = _make_coverage_json(
            feed,
            cohort=cohort,
            coverage_state="VERIFIED_ZERO_EVENT" if is_zero else "DATA_PRESENT",
            event_count=0 if is_zero else 15,
        )
        cov_file.write_text(json.dumps(cov_data, indent=2), encoding="utf-8")

        if is_zero:
            # VERIFIED_ZERO contract: coverage receipt exists, no raw file, no raw compressed, no raw receipt
            cov_rec_dir = receipt_root / "coverage" / cohort / feed.exchange.lower() / feed.stream.lower()
            cov_rec_dir.mkdir(parents=True, exist_ok=True)
            cov_rec_file = cov_rec_dir / f"{feed.market}.coverage.json.archive-receipt.json"
            cov_rec_data = _make_receipt_json(
                feed,
                cohort=cohort,
                artifact_kind="COVERAGE_EVIDENCE",
                state="RESTORE_VERIFIED",
                restore_verified_at="2026-09-14T13:06:00Z",
            )
            cov_rec_file.write_text(json.dumps(cov_rec_data, indent=2), encoding="utf-8")

            # Coverage compressed file
            cov_comp_dir = compressed_root / "coverage" / cohort / feed.exchange.lower() / feed.stream.lower()
            cov_comp_dir.mkdir(parents=True, exist_ok=True)
            cov_comp_file = cov_comp_dir / f"{feed.market}.coverage.json.zst"
            cov_comp_file.write_bytes(b"\x28\xb5\x2f\xfd\x00\x00\x01\x00\x00dummy-coverage-zst")
        else:
            if not omit_raw:
                # Raw data file
                raw_dir = raw_root / dt_str / feed.exchange.lower() / feed.stream.lower()
                raw_dir.mkdir(parents=True, exist_ok=True)
                raw_file = raw_dir / f"{feed.exchange.lower()}_{feed.stream.lower()}_{clean_market}_{dt_str}_{hour_str}.jsonl"
                raw_file.write_text('{"event": 1}\n{"event": 2}\n', encoding="utf-8")

                # Raw compressed file
                comp_dir = compressed_root / dt_str / feed.exchange.lower() / feed.stream.lower()
                comp_dir.mkdir(parents=True, exist_ok=True)
                comp_file = comp_dir / f"{feed.exchange.lower()}_{feed.stream.lower()}_{clean_market}_{dt_str}_{hour_str}.jsonl.zst"
                comp_file.write_bytes(b"\x28\xb5\x2f\xfd\x00\x00\x01\x00\x00dummy-raw-zst")

                # Raw receipt file
                rec_dir = receipt_root / dt_str / feed.exchange.lower() / feed.stream.lower()
                rec_dir.mkdir(parents=True, exist_ok=True)
                rec_file = rec_dir / f"{feed.exchange.lower()}_{feed.stream.lower()}_{clean_market}_{dt_str}_{hour_str}.jsonl.archive-receipt.json"
                rec_data = _make_receipt_json(
                    feed,
                    cohort=cohort,
                    artifact_kind="RAW_DATA",
                    state="RESTORE_VERIFIED",
                    restore_verified_at="2026-09-14T13:08:30Z",
                )
                rec_file.write_text(json.dumps(rec_data, indent=2), encoding="utf-8")

    return {
        "raw_root": raw_root,
        "coverage_root": coverage_root,
        "compressed_root": compressed_root,
        "receipt_root": receipt_root,
        "artifact_dir": artifact_dir,
    }


def _snapshot_directory_state(roots: list[Path]) -> dict[str, str]:
    """Capture sha256 checksums of all files under specified roots."""
    snapshot: dict[str, str] = {}
    for r in roots:
        if not r.exists():
            continue
        for p in sorted(r.rglob("*")):
            if p.is_file():
                snapshot[str(p.resolve())] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snapshot


def test_canary_pass_all_76_feeds(tmp_path: Path):
    """Verifies PASS when all 76 feeds have raw, coverage, compressed, and receipt."""
    cohort = "2026-09-14_12"
    paths = _setup_full_environment(tmp_path, cohort=cohort)

    canary = HourCloseCanary(
        raw_root=paths["raw_root"],
        coverage_root=paths["coverage_root"],
        compressed_root=paths["compressed_root"],
        receipt_root=paths["receipt_root"],
        artifact_dir=paths["artifact_dir"],
        grace_seconds=600.0,
    )

    now_eligible = datetime(2026, 9, 14, 13, 15, 0, tzinfo=timezone.utc)
    report = canary.inspect_cohort(cohort, now=now_eligible)

    assert report.cohort == cohort
    assert report.expected_feeds == 76
    assert report.raw_terminal == 76
    assert report.coverage_terminal == 76
    assert report.compressed_terminal == 76
    assert report.receipts_terminal == 76
    assert report.unknown_missing == 0
    assert report.status == "PASS"
    assert report.archive_lag_seconds >= 0.0
    assert report.is_eligible is True

    # Check local emitted artifact
    artifact_path = paths["artifact_dir"] / f"hour-close-{cohort}.json"
    assert artifact_path.is_file()
    saved_data = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert saved_data["status"] == "PASS"
    assert saved_data["expected_feeds"] == 76
    assert saved_data["raw_terminal"] == 76
    assert saved_data["coverage_terminal"] == 76
    assert saved_data["compressed_terminal"] == 76
    assert saved_data["receipts_terminal"] == 76


def test_canary_correct_handling_of_verified_zero_feeds(tmp_path: Path):
    """Verifies correct handling of VERIFIED_ZERO feeds according to contract.

    Tests both mixed scenario (70 positive + 6 zero) and 100% verified zero scenario.
    """
    cohort = "2026-09-14_12"
    all_feeds = [f.canonical for f in SEALED_FEED_UNIVERSE]
    zero_feed_names = set(all_feeds[:6])  # 6 feeds verified zero

    paths = _setup_full_environment(
        tmp_path / "mixed",
        cohort=cohort,
        verified_zero_feeds=zero_feed_names,
    )

    canary = HourCloseCanary(
        raw_root=paths["raw_root"],
        coverage_root=paths["coverage_root"],
        compressed_root=paths["compressed_root"],
        receipt_root=paths["receipt_root"],
        artifact_dir=paths["artifact_dir"],
    )

    now_eligible = datetime(2026, 9, 14, 13, 20, 0, tzinfo=timezone.utc)
    report = canary.inspect_cohort(cohort, now=now_eligible)

    assert report.status == "PASS"
    assert report.expected_feeds == 76
    assert report.raw_terminal == 76
    assert report.coverage_terminal == 76
    assert report.compressed_terminal == 76
    assert report.receipts_terminal == 76
    assert report.unknown_missing == 0

    # Test 100% verified zero feeds
    all_zero_names = set(all_feeds)
    paths_all_zero = _setup_full_environment(
        tmp_path / "all_zero",
        cohort=cohort,
        verified_zero_feeds=all_zero_names,
    )
    canary_all_zero = HourCloseCanary(
        raw_root=paths_all_zero["raw_root"],
        coverage_root=paths_all_zero["coverage_root"],
        compressed_root=paths_all_zero["compressed_root"],
        receipt_root=paths_all_zero["receipt_root"],
        artifact_dir=paths_all_zero["artifact_dir"],
    )
    report_all_zero = canary_all_zero.inspect_cohort(cohort, now=now_eligible)
    assert report_all_zero.status == "PASS"
    assert report_all_zero.raw_terminal == 76
    assert report_all_zero.coverage_terminal == 76
    assert report_all_zero.compressed_terminal == 76
    assert report_all_zero.receipts_terminal == 76
    assert report_all_zero.unknown_missing == 0


def test_canary_explicit_fail_on_v4_missing_raw(tmp_path: Path):
    """Verifies explicit FAIL when RAW is missing (detecting V4 failure: coverage present but RAW 0/76).

    In V4 failure mode, coverage evidence is present for all 76 feeds claiming events,
    but RAW data is completely missing (0/76).
    Canary must record HIGH_SEVERITY_OBSERVATION, status FAIL, and not mutate or fabricate data.
    """
    cohort = "2026-09-14_12"
    paths = _setup_full_environment(tmp_path, cohort=cohort, omit_raw=True)

    canary = HourCloseCanary(
        raw_root=paths["raw_root"],
        coverage_root=paths["coverage_root"],
        compressed_root=paths["compressed_root"],
        receipt_root=paths["receipt_root"],
        artifact_dir=paths["artifact_dir"],
    )

    now_eligible = datetime(2026, 9, 14, 13, 15, 0, tzinfo=timezone.utc)
    report = canary.inspect_cohort(cohort, now=now_eligible)

    assert report.status == "FAIL"
    assert report.coverage_terminal == 76
    assert report.raw_terminal == 0
    assert report.compressed_terminal == 0
    assert report.receipts_terminal == 0

    # Ensure HIGH_SEVERITY_OBSERVATION is recorded
    obs_text = " ".join(report.observations)
    assert "HIGH_SEVERITY_OBSERVATION" in obs_text
    assert "V4" in obs_text
    assert "RAW data missing" in obs_text


def test_canary_non_mutation_invariant(tmp_path: Path):
    """Verifies canary non-mutation invariant: canary never modifies, adds, or deletes collector data."""
    cohort = "2026-09-14_12"
    paths = _setup_full_environment(tmp_path, cohort=cohort)

    inspected_roots = [
        paths["raw_root"],
        paths["coverage_root"],
        paths["compressed_root"],
        paths["receipt_root"],
    ]

    # Snapshot before running
    snapshot_before = _snapshot_directory_state(inspected_roots)
    assert len(snapshot_before) > 0

    canary = HourCloseCanary(
        raw_root=paths["raw_root"],
        coverage_root=paths["coverage_root"],
        compressed_root=paths["compressed_root"],
        receipt_root=paths["receipt_root"],
        artifact_dir=paths["artifact_dir"],
    )

    now_eligible = datetime(2026, 9, 14, 13, 20, 0, tzinfo=timezone.utc)
    report = canary.inspect_cohort(cohort, now=now_eligible)
    assert report.status == "PASS"

    # Snapshot after running
    snapshot_after = _snapshot_directory_state(inspected_roots)
    assert snapshot_before == snapshot_after, "Canary violated non-mutation invariant: files were modified or added!"

    # Now verify on V4 failure scenario as well
    paths_v4 = _setup_full_environment(tmp_path / "v4", cohort=cohort, omit_raw=True)
    inspected_v4_roots = [
        paths_v4["raw_root"],
        paths_v4["coverage_root"],
        paths_v4["compressed_root"],
        paths_v4["receipt_root"],
    ]
    snapshot_v4_before = _snapshot_directory_state(inspected_v4_roots)

    canary_v4 = HourCloseCanary(
        raw_root=paths_v4["raw_root"],
        coverage_root=paths_v4["coverage_root"],
        compressed_root=paths_v4["compressed_root"],
        receipt_root=paths_v4["receipt_root"],
        artifact_dir=paths_v4["artifact_dir"],
    )
    report_v4 = canary_v4.inspect_cohort(cohort, now=now_eligible)
    assert report_v4.status == "FAIL"

    snapshot_v4_after = _snapshot_directory_state(inspected_v4_roots)
    assert snapshot_v4_before == snapshot_v4_after, "Canary mutated data in V4 failure scenario!"


def test_canary_grace_period_eligibility(tmp_path: Path):
    """Verifies that canary fails-closed if run before cohort eligibility grace period expires."""
    cohort = "2026-09-14_12"
    paths = _setup_full_environment(tmp_path, cohort=cohort)

    canary = HourCloseCanary(
        raw_root=paths["raw_root"],
        coverage_root=paths["coverage_root"],
        compressed_root=paths["compressed_root"],
        receipt_root=paths["receipt_root"],
        artifact_dir=paths["artifact_dir"],
        grace_seconds=600.0,
    )

    # 13:05 is only 300s after hour close (not eligible yet)
    early_now = datetime(2026, 9, 14, 13, 5, 0, tzinfo=timezone.utc)
    with pytest.raises(CohortNotEligibleError) as exc_info:
        canary.inspect_cohort(cohort, now=early_now, allow_early=False)
    assert "eligible after grace" in str(exc_info.value)

    # With allow_early=True, inspection proceeds
    report = canary.inspect_cohort(cohort, now=early_now, allow_early=True)
    assert report.status == "PASS"
    assert report.is_eligible is False


def test_canary_s3_upload(tmp_path: Path):
    """Verifies that canary optionally uploads report to S3 <s3_prefix>/canary/hour-close-<cohort>.json."""
    cohort = "2026-09-14_12"
    paths = _setup_full_environment(tmp_path, cohort=cohort)

    mock_s3 = MagicMock()

    canary = HourCloseCanary(
        raw_root=paths["raw_root"],
        coverage_root=paths["coverage_root"],
        compressed_root=paths["compressed_root"],
        receipt_root=paths["receipt_root"],
        artifact_dir=paths["artifact_dir"],
        s3_bucket="my-telemetry-bucket",
        s3_prefix="production/monitoring",
        s3_client=mock_s3,
    )

    now_eligible = datetime(2026, 9, 14, 13, 15, 0, tzinfo=timezone.utc)
    report = canary.inspect_cohort(cohort, now=now_eligible, upload_s3=True)

    assert report.s3_uploaded is True
    assert report.s3_key == "production/monitoring/canary/hour-close-2026-09-14_12.json"

    # Verify S3 call
    mock_s3.put_object.assert_called_once()
    call_kwargs = mock_s3.put_object.call_args.kwargs
    assert call_kwargs["Bucket"] == "my-telemetry-bucket"
    assert call_kwargs["Key"] == "production/monitoring/canary/hour-close-2026-09-14_12.json"
    uploaded_json = json.loads(call_kwargs["Body"].decode("utf-8"))
    assert uploaded_json["cohort"] == cohort
    assert uploaded_json["status"] == "PASS"
    assert uploaded_json["expected_feeds"] == 76


def test_canary_cli_runner(tmp_path: Path):
    """Verifies CLI runner scripts/run_hour_close_canary.py behavior and exit codes."""
    cohort = "2026-09-14_12"
    paths = _setup_full_environment(tmp_path, cohort=cohort)

    # 1. PASS exit code (0)
    args_pass = [
        "--cohort", cohort,
        "--raw-root", str(paths["raw_root"]),
        "--coverage-root", str(paths["coverage_root"]),
        "--compressed-root", str(paths["compressed_root"]),
        "--receipt-root", str(paths["receipt_root"]),
        "--artifact-dir", str(paths["artifact_dir"]),
        "--now", "2026-09-14T13:15:00Z",
        "--json",
    ]
    exit_code_pass = cli_main(args_pass)
    assert exit_code_pass == 0

    # 2. FAIL exit code (1) when RAW missing
    paths_fail = _setup_full_environment(tmp_path / "cli_fail", cohort=cohort, omit_raw=True)
    args_fail = [
        "--cohort", cohort,
        "--raw-root", str(paths_fail["raw_root"]),
        "--coverage-root", str(paths_fail["coverage_root"]),
        "--compressed-root", str(paths_fail["compressed_root"]),
        "--receipt-root", str(paths_fail["receipt_root"]),
        "--artifact-dir", str(paths_fail["artifact_dir"]),
        "--now", "2026-09-14T13:15:00Z",
    ]
    exit_code_fail = cli_main(args_fail)
    assert exit_code_fail == 1


def test_canary_tampered_coverage_hash(tmp_path: Path):
    """Verifies that tampered coverage hash fails coverage_terminal validation."""
    cohort = "2026-09-14_12"
    paths = _setup_full_environment(tmp_path, cohort=cohort)

    # Deliberately tamper a coverage evidence file
    first_cov_file = next(paths["coverage_root"].rglob("*.coverage.json"))
    cov_data = json.loads(first_cov_file.read_text(encoding="utf-8"))
    cov_data["evidence_sha256"] = "f" * 64  # Tamper hash
    first_cov_file.write_text(json.dumps(cov_data, indent=2), encoding="utf-8")

    canary = HourCloseCanary(
        raw_root=paths["raw_root"],
        coverage_root=paths["coverage_root"],
        compressed_root=paths["compressed_root"],
        receipt_root=paths["receipt_root"],
        artifact_dir=paths["artifact_dir"],
    )

    now_eligible = datetime(2026, 9, 14, 13, 15, 0, tzinfo=timezone.utc)
    report = canary.inspect_cohort(cohort, now=now_eligible)

    # 75 feeds pass, 1 feed has tampered coverage
    assert report.coverage_terminal == 75
    assert report.status == "DEGRADED"
