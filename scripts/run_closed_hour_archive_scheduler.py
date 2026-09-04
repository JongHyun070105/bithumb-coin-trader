#!/usr/bin/env python3
"""CLI runner for ClosedHourArchiveScheduler.

Runs periodically to archive closed hourly cohorts during a soak run.
Enforces:
1. Active partition exclusion
2. >=600s grace after hour closure
3. Oldest-first serial processing (concurrency=1)
4. Fail-closed ownership validation
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
for d in (ROOT, SRC_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

from bithumb_coin_trader.archive_scheduler import (
    ArchiveSchedulerConfig,
    ClosedHourArchiveScheduler,
)
from bithumb_coin_trader.pre_soak_archive import OwnershipViolationError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epoch", required=True, help="Collector epoch name")
    parser.add_argument("--run-id", required=True, help="Collector run ID")
    parser.add_argument("--base-dir", type=Path, help="Base directory for epoch data")
    parser.add_argument("--environment-id", default="aws-apne2-research")
    parser.add_argument("--git-commit", default="HEAD")
    parser.add_argument("--store", choices=("file", "s3"), default="file")
    parser.add_argument("--file-store-root", type=Path)
    parser.add_argument("--s3-bucket")
    parser.add_argument("--allow-aws-write", action="store_true")
    parser.add_argument("--remote-prefix")
    parser.add_argument("--poll-interval-seconds", type=float, default=30.0)
    parser.add_argument("--grace-seconds", type=int, default=600)
    parser.add_argument("--expected-owner", default="bitcoin-trader")
    parser.add_argument("--scan-runner", choices=("auto", "systemd", "detached", "direct", "none"), default="auto")
    parser.add_argument("--no-full-scan", action="store_true")
    parser.add_argument("--disk-critical-percent", type=float, default=90.0)
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit")
    parser.add_argument("--max-iterations", type=int, help="Maximum loop iterations before exiting")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    base_dir = args.base_dir
    if base_dir is None:
        base_dir = Path(f"/var/lib/bitcoin-trader/{args.epoch}")

    raw_root = base_dir / "raw"
    manifest_root = base_dir / "manifests"
    compressed_root = base_dir / "compressed"
    receipt_root = base_dir / "archive-receipts"
    metrics_path = base_dir / "collector_metrics.json"

    cfg = ArchiveSchedulerConfig(
        epoch=args.epoch,
        run_id=args.run_id,
        base_dir=base_dir,
        raw_root=raw_root,
        manifest_root=manifest_root,
        compressed_root=compressed_root,
        receipt_root=receipt_root,
        metrics_path=metrics_path,
        poll_interval_seconds=args.poll_interval_seconds,
        grace_seconds=args.grace_seconds,
        expected_owner=args.expected_owner,
        environment_id=args.environment_id,
        git_commit=args.git_commit,
        store_type=args.store,
        file_store_root=args.file_store_root,
        s3_bucket=args.s3_bucket,
        allow_aws_write=args.allow_aws_write,
        remote_prefix=args.remote_prefix,
        scan_runner_mode=args.scan_runner,
        run_full_scan=not args.no_full_scan and args.scan_runner != "none",
        disk_critical_percent=args.disk_critical_percent,
    )

    scheduler = ClosedHourArchiveScheduler(cfg)

    # Signal handling
    def _sig_handler(signum: int, _frame: object) -> None:
        print(f"Archive scheduler received signal {signum}, stopping...", file=sys.stderr)
        scheduler.stop()

    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, _sig_handler)

    print(f"=== CLOSED-HOUR ARCHIVE SCHEDULER: epoch={args.epoch} ===")
    print(f"Base Dir: {base_dir}, Grace: {args.grace_seconds}s, Poll: {args.poll_interval_seconds}s")

    try:
        if args.once:
            res = scheduler.run_once()
            print(json.dumps(res, indent=2))
            return 0 if res.get("status") in ("PASS", "IDLE", "LOCKED") else 1

        scheduler.run_loop(max_iterations=args.max_iterations)
        return 0
    except OwnershipViolationError as exc:
        print(f"FATAL OWNERSHIP VIOLATION (Fail-Closed): {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"FATAL SCHEDULER ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
