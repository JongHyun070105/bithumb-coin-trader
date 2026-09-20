#!/usr/bin/env python3
"""CLI runner for Hour-Close Completeness Canary.

Audits exactly 76 feeds for a closed UTC cohort, emitting structured report and
optionally uploading evidence artifact to S3.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

# Ensure package import
ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bithumb_coin_trader.hour_close_canary import (
    CohortNotEligibleError,
    HourCloseCanary,
    HourCloseCanaryError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hour-Close Completeness Canary for Closed UTC Cohorts (76 Feeds).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--cohort",
        required=True,
        help="Closed hour cohort in format YYYY-MM-DD_HH (e.g. 2026-09-14_12).",
    )
    parser.add_argument(
        "--base-dir",
        default=None,
        help="Base directory containing raw, coverage, compressed, archive-receipts.",
    )
    parser.add_argument(
        "--raw-root",
        default=None,
        help="Override raw data directory.",
    )
    parser.add_argument(
        "--coverage-root",
        default=None,
        help="Override coverage evidence directory.",
    )
    parser.add_argument(
        "--compressed-root",
        default=None,
        help="Override compressed data directory (.jsonl.zst).",
    )
    parser.add_argument(
        "--receipt-root",
        default=None,
        help="Override archive receipt directory.",
    )
    parser.add_argument(
        "--artifact-dir",
        default=None,
        help="Directory to save local canary artifact (hour-close-<cohort>.json).",
    )
    parser.add_argument(
        "--grace-seconds",
        type=float,
        default=600.0,
        help="Grace seconds after hour close before cohort becomes eligible.",
    )
    parser.add_argument(
        "--allow-early",
        action="store_true",
        help="Allow canary to run before grace period expiration.",
    )
    parser.add_argument(
        "--s3-bucket",
        default=None,
        help="S3 bucket to upload canary report to.",
    )
    parser.add_argument(
        "--s3-prefix",
        default="",
        help="S3 key prefix for canary artifact (<s3_prefix>/canary/hour-close-<cohort>.json).",
    )
    parser.add_argument(
        "--no-s3-upload",
        action="store_true",
        help="Disable uploading artifact to S3 even if bucket is configured.",
    )
    parser.add_argument(
        "--no-local-emit",
        action="store_true",
        help="Disable saving local canary JSON file.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Override current time for deterministic evaluation (ISO 8601 string).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON report to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    now_dt: datetime | None = None
    if args.now:
        try:
            now_dt = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        except ValueError as exc:
            sys.stderr.write(f"ERROR: Invalid --now format: {exc}\n")
            return 1

    canary = HourCloseCanary(
        base_dir=args.base_dir,
        raw_root=args.raw_root,
        coverage_root=args.coverage_root,
        compressed_root=args.compressed_root,
        receipt_root=args.receipt_root,
        artifact_dir=args.artifact_dir,
        grace_seconds=args.grace_seconds,
        s3_bucket=args.s3_bucket,
        s3_prefix=args.s3_prefix,
    )

    try:
        report = canary.inspect_cohort(
            cohort=args.cohort,
            now=now_dt,
            allow_early=args.allow_early,
            emit_local=not args.no_local_emit,
            upload_s3=not args.no_s3_upload and bool(args.s3_bucket),
        )
    except CohortNotEligibleError as exc:
        sys.stderr.write(f"NOT_ELIGIBLE: {exc}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1

    report_dict = report.to_dict()

    if args.json:
        print(json.dumps(report_dict, indent=2, ensure_ascii=False))
    else:
        print(f"=== Hour-Close Canary Report: {report.cohort} ===")
        print(f"Status:             {report.status}")
        print(f"Expected Feeds:     {report.expected_feeds}")
        print(f"Raw Terminal:       {report.raw_terminal}/{report.expected_feeds}")
        print(f"Coverage Terminal:  {report.coverage_terminal}/{report.expected_feeds}")
        print(f"Compressed Terminal:{report.compressed_terminal}/{report.expected_feeds}")
        print(f"Receipts Terminal:  {report.receipts_terminal}/{report.expected_feeds}")
        print(f"Unknown Missing:    {report.unknown_missing}/{report.expected_feeds}")
        print(f"Archive Lag:        {report.archive_lag_seconds:.1f}s")
        if report.s3_uploaded:
            print(f"S3 Artifact:        s3://{args.s3_bucket}/{report.s3_key}")
        if report.observations:
            print("Observations:")
            for obs in report.observations:
                print(f"  - {obs}")

    if report.status == "PASS":
        return 0
    elif report.status == "DEGRADED":
        return 2
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
