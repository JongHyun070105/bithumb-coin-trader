"""Operate the fail-closed prospective-epoch archive pipeline.

The default store is local. S3 writes require both ``--store s3`` and the explicit
``--allow-aws-write`` flag so local validation cannot accidentally contact AWS.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Sequence

from bithumb_coin_trader.pre_soak_archive import (
    ArchivePipeline,
    FileArchiveStore,
    S3ArchiveStore,
    is_closed_stable_partition,
)


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data" / "microstructure"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("scan", "finalize", "verify", "restore", "verify-restore", "cleanup"),
    )
    parser.add_argument("--raw", type=Path)
    parser.add_argument("--raw-root", type=Path, default=DATA_ROOT / "raw")
    parser.add_argument("--manifest-root", type=Path, default=DATA_ROOT / "manifests")
    parser.add_argument("--compressed-root", type=Path, default=DATA_ROOT / "compressed")
    parser.add_argument("--receipt-root", type=Path, default=DATA_ROOT / "archive-receipts")
    parser.add_argument("--metrics-path", type=Path, default=DATA_ROOT / "collector_metrics.json")
    parser.add_argument("--store", choices=("file", "s3"), default="file")
    parser.add_argument("--file-store-root", type=Path, default=DATA_ROOT / "local-archive-fixture")
    parser.add_argument("--s3-bucket")
    parser.add_argument("--allow-aws-write", action="store_true")
    parser.add_argument("--environment-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--collector-epoch", required=True)
    parser.add_argument("--remote-prefix", required=True)
    parser.add_argument("--compression-level", type=int, default=1)
    parser.add_argument("--disk-critical-percent", type=float, default=90.0)
    parser.add_argument("--grace-seconds", type=int, default=600)
    parser.add_argument("--cleanup-verified", action="store_true")
    parser.add_argument("--verified-only", action="store_true")
    return parser


def _active_paths(metrics_path: Path, raw_root: Path) -> tuple[Path, ...]:
    try:
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return ()
    values = payload.get("active_partition_files", []) if isinstance(payload, dict) else []
    if not isinstance(values, list):
        return ()
    active = []
    resolved_root = raw_root.resolve()
    for value in values:
        if not isinstance(value, str):
            continue
        candidate = (resolved_root / value).resolve()
        if resolved_root in candidate.parents:
            active.append(candidate)
    return tuple(active)


def _pipeline(args: argparse.Namespace) -> ArchivePipeline:
    if args.store == "s3":
        if not args.allow_aws_write:
            raise SystemExit("S3 store requires explicit --allow-aws-write")
        if not args.s3_bucket:
            raise SystemExit("S3 store requires --s3-bucket")
        store = S3ArchiveStore(args.s3_bucket)
    else:
        store = FileArchiveStore(args.file_store_root)
    return ArchivePipeline(
        raw_root=args.raw_root,
        manifest_root=args.manifest_root,
        compressed_root=args.compressed_root,
        receipt_root=args.receipt_root,
        store=store,
        environment_id=args.environment_id,
        run_id=args.run_id,
        collector_epoch=args.collector_epoch,
        remote_prefix=args.remote_prefix,
        compression_level=args.compression_level,
        disk_critical_percent=args.disk_critical_percent,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    pipeline = _pipeline(args)
    active_paths = _active_paths(args.metrics_path, args.raw_root)
    if args.command == "scan":
        now = datetime.now(timezone.utc)
        eligible = []
        for path in sorted(args.raw_root.glob("**/*.jsonl")):
            if is_closed_stable_partition(
                path,
                args.raw_root,
                now=now,
                grace_period=timedelta(seconds=args.grace_seconds),
                active_paths=active_paths,
            ):
                eligible.append(str(path.relative_to(args.raw_root)))
        print(json.dumps({"eligible": eligible, "count": len(eligible)}, indent=2))
        return 0
    if args.raw is None:
        raise SystemExit("command requires --raw")
    if args.command == "finalize":
        receipt = pipeline.finalize(
            args.raw,
            cleanup_verified=args.cleanup_verified,
            grace_period=timedelta(seconds=args.grace_seconds),
            active_paths=active_paths,
        )
    elif args.command == "verify":
        receipt = pipeline.verify_compressed(args.raw)
    elif args.command in {"restore", "verify-restore"}:
        receipt = pipeline.verify_restore(args.raw)
    else:
        receipt = pipeline.cleanup(args.raw, verified_only=args.verified_only)
    print(json.dumps(receipt.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
