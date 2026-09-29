#!/usr/bin/env python3
"""Terminal Witness Hook for Systemd ExecStopPost & Supervisor Shutdown.

Captures systemd exit environment variables ($SERVICE_RESULT, $EXIT_CODE, $EXIT_STATUS)
or CLI arguments, inspects the last known health snapshots, and writes an immutable
terminal receipt locally and to S3.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Mapping, Optional, Sequence

# Ensure src is on sys.path
ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bithumb_coin_trader.collector_state_model import (
    read_health_snapshot,
    utc_iso_now,
)
from bithumb_coin_trader.runtime_observer import parse_s3_location

logger = logging.getLogger("terminal_witness")
SAFE_WITNESS_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_S3_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
SAFE_S3_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
SAFE_AWS_REGION = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def classify_terminal_outcome(
    service_result: str,
    exit_code: str,
    exit_status: str,
) -> str:
    """Classify termination outcome based on systemd result and exit status."""
    norm_result = (service_result or "").strip().lower()
    norm_code = (exit_code or "").strip().lower()
    norm_status = (exit_status or "").strip()

    if norm_result in ("success", "none") and norm_status in ("0", ""):
        return "CLEAN_SUCCESS"
    if norm_result == "timeout":
        return "TIMEOUT_EXPIRED"
    if norm_result == "watchdog":
        return "WATCHDOG_KILLED"
    if norm_result == "resources":
        return "RESOURCE_EXHAUSTED"
    if norm_result == "core-dump" or norm_code == "dumped":
        return f"CORE_DUMPED_STATUS_{norm_status}" if norm_status else "CORE_DUMPED"
    if norm_result == "signal" or norm_code == "killed":
        return f"SIGNAL_TERMINATED_{norm_status}" if norm_status else "SIGNAL_TERMINATED"
    if norm_result == "start-limit-hit":
        return "START_LIMIT_HIT"
    if norm_result == "exit-code" or norm_status not in ("0", ""):
        return f"PROCESS_EXIT_ERROR_{norm_status}" if norm_status else "PROCESS_EXIT_ERROR"
    return "UNKNOWN_TERMINATION"


def write_receipt_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write terminal receipt atomically with fsync and rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=f".{os.getpid()}.tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
        # fsync parent directory for crash safety (POSIX metadata durability)
        try:
            parent_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        except OSError:
            pass
    except Exception:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise


def record_terminal_receipt(
    data_dir: Path,
    epoch: Optional[str] = None,
    run_id: Optional[str] = None,
    service_result: Optional[str] = None,
    exit_code: Optional[str] = None,
    exit_status: Optional[str] = None,
    s3_bucket: Optional[str] = None,
    s3_prefix: Optional[str] = None,
    s3_region: Optional[str] = None,
    allow_s3_write: bool = False,
    s3_client: Optional[Any] = None,
) -> dict[str, Any]:
    """Capture terminal status, inspect health snapshots, write local and S3 receipts."""
    if allow_s3_write:
        if not epoch or not SAFE_WITNESS_ID.fullmatch(epoch):
            raise ValueError("S3 terminal witness upload requires an exact safe epoch")
        if not run_id or not SAFE_WITNESS_ID.fullmatch(run_id):
            raise ValueError("S3 terminal witness upload requires an exact safe run ID")
        if not s3_bucket or not SAFE_S3_BUCKET.fullmatch(s3_bucket):
            raise ValueError("S3 terminal witness upload requires a valid bucket")
        if not s3_prefix or not SAFE_S3_PREFIX.fullmatch(s3_prefix):
            raise ValueError("S3 terminal witness upload requires a valid prefix")
        if any(part in {"", ".", ".."} for part in s3_prefix.split("/")):
            raise ValueError("S3 terminal witness prefix must not contain empty or dot path segments")
        if not s3_region or not SAFE_AWS_REGION.fullmatch(s3_region):
            raise ValueError("S3 terminal witness upload requires an explicit AWS region")

    recorded_at = utc_iso_now()

    # Capture systemd environment variables if not provided
    eff_service_result = service_result or os.environ.get("SERVICE_RESULT", "unknown")
    eff_exit_code = exit_code or os.environ.get("EXIT_CODE", "unknown")
    eff_exit_status = exit_status or os.environ.get("EXIT_STATUS", "unknown")

    classification = classify_terminal_outcome(
        eff_service_result,
        eff_exit_code,
        eff_exit_status,
    )

    # Read last known health snapshots
    collector_health_path = data_dir / "health" / "latest.json"
    observer_health_path = data_dir / "health" / "observer_latest.json"

    collector_snapshot = read_health_snapshot(collector_health_path)
    observer_snapshot = read_health_snapshot(observer_health_path)

    # Infer epoch / run_id from snapshots if not provided
    eff_epoch = epoch or ""
    eff_run_id = run_id or ""
    if not eff_epoch:
        if observer_snapshot and observer_snapshot.epoch:
            eff_epoch = observer_snapshot.epoch
        elif collector_snapshot and collector_snapshot.epoch:
            eff_epoch = collector_snapshot.epoch
    if not eff_run_id:
        if observer_snapshot and observer_snapshot.run_id:
            eff_run_id = observer_snapshot.run_id
        elif collector_snapshot and collector_snapshot.run_id:
            eff_run_id = collector_snapshot.run_id

    receipt: dict[str, Any] = {
        "schema_version": 1,
        "recorded_at": recorded_at,
        "epoch": eff_epoch,
        "run_id": eff_run_id,
        "service_result": eff_service_result,
        "exit_code": eff_exit_code,
        "exit_status": eff_exit_status,
        "terminal_classification": classification,
        "hostname": os.uname().nodename if hasattr(os, "uname") else "unknown",
        "witness_pid": os.getpid(),
        "last_known_health": collector_snapshot.to_dict() if collector_snapshot else None,
        "last_observer_health": observer_snapshot.to_dict() if observer_snapshot else None,
        "s3_uploaded": False,
        "s3_key": None,
    }

    # 1. Write immutable local receipts
    receipt_dir = data_dir / "terminal"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    terminal_receipt_path = receipt_dir / "terminal-receipt.json"

    dt_now = datetime.now(timezone.utc)
    ts_tag = dt_now.strftime("%Y%m%dT%H%M%SZ")
    timestamped_receipt_path = receipt_dir / f"terminal-receipt-{ts_tag}.json"

    write_receipt_atomic(terminal_receipt_path, receipt)
    try:
        write_receipt_atomic(timestamped_receipt_path, receipt)
    except Exception as exc:
        logger.warning("Could not write timestamped receipt: %s", exc)

    # 2. Upload to S3 if configured
    bucket, prefix = parse_s3_location(s3_bucket, s3_prefix)
    receipt["s3_bucket"] = bucket
    receipt["s3_prefix"] = prefix
    receipt["s3_region"] = s3_region
    write_receipt_atomic(terminal_receipt_path, receipt)
    try:
        write_receipt_atomic(timestamped_receipt_path, receipt)
    except Exception as exc:
        logger.warning("Could not refresh timestamped terminal receipt: %s", exc)
    if bucket and allow_s3_write:
        client = s3_client
        if client is None:
            try:
                import boto3  # pyright: ignore[reportMissingImports]

                client = boto3.client("s3", region_name=s3_region)
            except Exception as exc:
                logger.error("Failed to load boto3 S3 client for terminal receipt: %s", exc)
                client = None

        if client is not None:
            receipt_key = f"{prefix}/terminal/terminal-receipt.json" if prefix else "terminal/terminal-receipt.json"
            receipt_ts_key = (
                f"{prefix}/terminal/terminal-receipt-{ts_tag}.json"
                if prefix
                else f"terminal/terminal-receipt-{ts_tag}.json"
            )
            # Persist the exact successful payload before upload so a successful
            # S3 write and the local receipt have byte-for-byte identical content.
            receipt["s3_uploaded"] = True
            receipt["s3_key"] = receipt_key
            payload_bytes = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
            write_receipt_atomic(terminal_receipt_path, receipt)
            write_receipt_atomic(timestamped_receipt_path, receipt)
            try:
                client.put_object(
                    Bucket=bucket,
                    Key=receipt_key,
                    Body=payload_bytes,
                    ContentType="application/json",
                )
                try:
                    client.put_object(
                        Bucket=bucket,
                        Key=receipt_ts_key,
                        Body=payload_bytes,
                        ContentType="application/json",
                    )
                except Exception as exc:
                    # The stable receipt is the required audit object. Preserve
                    # its success and byte parity if the optional timestamped
                    # mirror cannot be written.
                    logger.warning("Could not upload timestamped terminal receipt: %s", exc)
                logger.info("Terminal receipt uploaded to s3://%s/%s", bucket, receipt_key)
            except Exception as exc:
                logger.error("Failed to upload terminal receipt to S3: %s", exc)
                receipt["s3_uploaded"] = False
                receipt["s3_key"] = None

    # Persist the final success/failure state. After a successful upload this
    # serializes the same fields and bytes already sent to the stable S3 key.
    write_receipt_atomic(terminal_receipt_path, receipt)
    try:
        write_receipt_atomic(timestamped_receipt_path, receipt)
    except Exception as exc:
        logger.warning("Could not persist timestamped terminal receipt: %s", exc)

    return receipt


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Terminal Witness Hook for Systemd ExecStopPost & Supervisor Shutdown"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Base data directory (defaults to DATA_DIR env or /var/lib/bitcoin-trader/<epoch>)",
    )
    parser.add_argument("--epoch", default=None, help="Epoch name")
    parser.add_argument("--run-id", default=None, help="Run ID")
    parser.add_argument("--service-result", default=None, help="Systemd $SERVICE_RESULT override")
    parser.add_argument("--exit-code", default=None, help="Systemd $EXIT_CODE override")
    parser.add_argument("--exit-status", default=None, help="Systemd $EXIT_STATUS override")
    parser.add_argument("--s3-bucket", default=None, help="S3 bucket")
    parser.add_argument("--s3-prefix", default=None, help="S3 key prefix")
    parser.add_argument("--s3-region", default=None, help="AWS region for the exact witness bucket")
    parser.add_argument("--allow-s3-write", action="store_true", help="Allow upload to S3")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.epoch or not args.run_id:
        parser.error("exact --epoch and --run-id are required")
    if not args.s3_bucket or not args.s3_prefix or not args.s3_region or not args.allow_s3_write:
        parser.error("exact S3 bucket, prefix, region, and --allow-s3-write are required")
    if not SAFE_WITNESS_ID.fullmatch(args.epoch) or not SAFE_WITNESS_ID.fullmatch(args.run_id):
        parser.error("epoch and run ID must be safe launch identifiers")
    if not SAFE_S3_BUCKET.fullmatch(args.s3_bucket) or not SAFE_S3_PREFIX.fullmatch(args.s3_prefix):
        parser.error("S3 bucket and prefix must use the supported safe identifier syntax")
    if any(part in {"", ".", ".."} for part in args.s3_prefix.split("/")):
        parser.error("S3 prefix must not contain empty or dot path segments")
    if not SAFE_AWS_REGION.fullmatch(args.s3_region):
        parser.error("S3 region must be a safe AWS region identifier")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
    )

    data_dir = args.data_dir
    if data_dir is None:
        env_dir = os.environ.get("DATA_DIR")
        if env_dir:
            data_dir = Path(env_dir)
        elif args.epoch:
            data_dir = Path(f"/var/lib/bitcoin-trader/{args.epoch}")
        else:
            data_dir = Path.cwd()

    try:
        receipt = record_terminal_receipt(
            data_dir=data_dir.resolve(),
            epoch=args.epoch,
            run_id=args.run_id,
            service_result=args.service_result,
            exit_code=args.exit_code,
            exit_status=args.exit_status,
            s3_bucket=args.s3_bucket,
            s3_prefix=args.s3_prefix,
            s3_region=args.s3_region,
            allow_s3_write=args.allow_s3_write,
        )
        print(json.dumps(receipt, indent=2))
        return 0 if receipt["s3_uploaded"] else 1
    except Exception as exc:
        print(f"FATAL: terminal witness failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
