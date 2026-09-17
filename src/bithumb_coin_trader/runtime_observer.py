"""Independent Runtime Observer Daemon for Collector & Pipeline Infrastructure.

Maintains strict separation of concerns and non-intervention:
- Independent daemon that survives collector termination.
- Periodically (every 10-30s) inspects systemd unit status and health/latest.json.
- Evaluates ComponentHealthState for COLLECTOR, WRITER, ARCHIVER, EVIDENCE, SUPERVISOR.
- Distinguishes COLLECTOR = STALE (when loop heartbeat > 30s ago) from quiet market
  (canonical event > 30s ago but loop heartbeat fresh).
- Observes itself: records observer_pid, observer_started_at, observer_last_cycle, observer_errors.
- Writes local observer snapshot atomically to <data_dir>/health/observer_latest.json.
- Every 60 seconds, uploads immutable witness to S3:
  <s3_prefix>/observability/minute/YYYYMMDDTHHMMSSZ.json
  (and updates <s3_prefix>/observability/latest.json if S3 write allowed).
- NON-INTERVENING: NEVER restarts collector, modifies data, or deletes files.
  If S3 upload fails, marks EVIDENCE = DEGRADED and logs locally.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Optional, Sequence

from bithumb_coin_trader.collector_state_model import (
    ArchiverHealth,
    CollectorHealth,
    ComponentHealthState,
    CurrentCohortHealth,
    EvidenceHealth,
    LastExceptionInfo,
    ObserverHealth,
    ResourceTelemetry,
    RuntimeHealthSnapshot,
    SupervisorHealth,
    compute_exception_hash,
    read_health_snapshot,
    utc_iso_now,
    write_health_snapshot_atomic,
)

logger = logging.getLogger("runtime_observer")


def parse_s3_location(
    s3_bucket: Optional[str] = None,
    s3_prefix: Optional[str] = None,
) -> tuple[Optional[str], str]:
    """Parse and normalize S3 bucket and prefix from separate args or s3:// URI."""
    bucket = s3_bucket
    prefix = s3_prefix or ""

    if prefix.startswith("s3://"):
        without_scheme = prefix[5:]
        parts = without_scheme.split("/", 1)
        if not bucket:
            bucket = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""

    prefix = prefix.strip("/")
    return bucket, prefix


def query_systemd_unit_status(unit_name: str) -> dict[str, str] | None:
    """Query systemd unit status via systemctl show if available."""
    if not unit_name:
        return None
    try:
        res = subprocess.run(
            [
                "systemctl",
                "show",
                unit_name,
                "--property=ActiveState,SubState,MainPID,ExecMainStartTimestamp,InvocationID,Result",
            ],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if res.returncode != 0:
            return None
        props: dict[str, str] = {}
        for line in res.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
        return props
    except Exception:
        return None


def _parse_utc_iso(ts_str: str | None) -> datetime | None:
    """Parse an ISO timestamp into a timezone-aware UTC datetime."""
    if not ts_str:
        return None
    try:
        clean = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


@dataclass(frozen=True)
class ObserverConfig:
    data_dir: Path
    epoch: str = ""
    run_id: str = ""
    unit_name: Optional[str] = None
    poll_interval_seconds: float = 15.0
    s3_publish_interval_seconds: float = 60.0
    stale_heartbeat_threshold_seconds: float = 30.0
    quiet_market_threshold_seconds: float = 30.0
    s3_bucket: Optional[str] = None
    s3_prefix: Optional[str] = None
    allow_s3_write: bool = False

    def __post_init__(self) -> None:
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if self.s3_publish_interval_seconds <= 0:
            raise ValueError("s3_publish_interval_seconds must be positive")
        if self.stale_heartbeat_threshold_seconds <= 0:
            raise ValueError("stale_heartbeat_threshold_seconds must be positive")


class RuntimeObserver:
    """Independent daemon that observes collector and pipeline health without intervening."""

    def __init__(
        self,
        config: ObserverConfig,
        s3_client: Optional[Any] = None,
        systemd_query_fn: Optional[Callable[[str], dict[str, str] | None]] = None,
    ) -> None:
        self.config = config
        self._s3_client = s3_client
        self._systemd_query_fn = systemd_query_fn or query_systemd_unit_status

        self.observer_pid = os.getpid()
        self.observer_started_at = utc_iso_now()
        self.observer_last_cycle: str | None = None
        self.observer_errors: int = 0

        self.last_s3_upload_mono: float = 0.0
        self.last_evaluated_snapshot: Optional[RuntimeHealthSnapshot] = None
        self._shutdown_requested = False

    def _get_s3_client(self) -> Any:
        if self._s3_client is not None:
            return self._s3_client
        if not self.config.allow_s3_write:
            return None
        try:
            import boto3  # pyright: ignore[reportMissingImports]

            self._s3_client = boto3.client("s3")
            return self._s3_client
        except Exception as exc:
            logger.warning("Failed to initialize boto3 S3 client: %s", exc)
            return None

    def evaluate_health(
        self,
        snapshot: Optional[RuntimeHealthSnapshot],
        unit_props: Optional[dict[str, str]],
        now: datetime,
    ) -> RuntimeHealthSnapshot:
        """Evaluate ComponentHealthState for COLLECTOR, WRITER, ARCHIVER, EVIDENCE, SUPERVISOR, and OBSERVER."""
        if snapshot is None:
            eval_snapshot = RuntimeHealthSnapshot(
                epoch=self.config.epoch,
                run_id=self.config.run_id,
                observed_at=now.isoformat(),
            )
        else:
            eval_snapshot = RuntimeHealthSnapshot.from_dict(snapshot.to_dict())
            eval_snapshot.observed_at = now.isoformat()
            if not eval_snapshot.epoch and self.config.epoch:
                eval_snapshot.epoch = self.config.epoch
            if not eval_snapshot.run_id and self.config.run_id:
                eval_snapshot.run_id = self.config.run_id

        # 1. SUPERVISOR EVALUATION
        if unit_props:
            active_state = unit_props.get("ActiveState", "UNKNOWN")
            sub_state = unit_props.get("SubState", "UNKNOWN")
            eval_snapshot.supervisor.active_state = active_state
            eval_snapshot.supervisor.sub_state = sub_state
            if self.config.unit_name:
                eval_snapshot.supervisor.unit = self.config.unit_name
            if "InvocationID" in unit_props:
                eval_snapshot.supervisor.invocation_id = unit_props["InvocationID"]
            if "MainPID" in unit_props and unit_props["MainPID"].isdigit():
                main_pid = int(unit_props["MainPID"])
                if main_pid > 0:
                    eval_snapshot.supervisor.pid = main_pid
            if "ExecMainStartTimestamp" in unit_props and unit_props["ExecMainStartTimestamp"]:
                eval_snapshot.supervisor.process_start = unit_props["ExecMainStartTimestamp"]

        # 2. COLLECTOR EVALUATION
        # Check systemd failure first
        unit_active_state = eval_snapshot.supervisor.active_state
        if unit_active_state == "failed":
            eval_snapshot.collector.status = ComponentHealthState.FAILED.value
            if not eval_snapshot.collector.fatal_error:
                eval_snapshot.collector.fatal_error = (
                    f"Systemd service failed: {unit_props.get('Result', 'unknown') if unit_props else 'failed'}"
                )
        elif eval_snapshot.collector.fatal_error:
            eval_snapshot.collector.status = ComponentHealthState.FAILED.value
        else:
            # Distinguish STALE from quiet market
            dt_heartbeat = _parse_utc_iso(eval_snapshot.collector.last_loop_heartbeat)
            if dt_heartbeat is None:
                # If unit is active and never gave heartbeat, or snapshot empty
                eval_snapshot.collector.status = (
                    ComponentHealthState.UNKNOWN.value if snapshot is None else ComponentHealthState.STALE.value
                )
            else:
                heartbeat_age = (now - dt_heartbeat).total_seconds()
                if heartbeat_age > self.config.stale_heartbeat_threshold_seconds:
                    eval_snapshot.collector.status = ComponentHealthState.STALE.value
                else:
                    # Loop heartbeat is fresh!
                    # Check canonical event: even if > 30s ago, market is quiet -> HEALTHY!
                    dt_event = _parse_utc_iso(eval_snapshot.collector.last_canonical_event)
                    # Loop is executing normally regardless of incoming trade frequency
                    if eval_snapshot.collector.reconnect_count > 10:
                        eval_snapshot.collector.status = ComponentHealthState.DEGRADED.value
                    else:
                        eval_snapshot.collector.status = ComponentHealthState.HEALTHY.value

        # 3. WRITER EVALUATION
        if eval_snapshot.writer.writer_errors > 0:
            eval_snapshot.writer.status = ComponentHealthState.DEGRADED.value
        elif eval_snapshot.writer.queue_depth > 10000 or eval_snapshot.writer.unpersisted_count > 5000:
            eval_snapshot.writer.status = ComponentHealthState.DEGRADED.value
        elif eval_snapshot.writer.status in (ComponentHealthState.UNKNOWN.value, ""):
            if snapshot is not None and eval_snapshot.writer.last_local_raw_write is not None:
                eval_snapshot.writer.status = ComponentHealthState.HEALTHY.value
            elif snapshot is not None:
                eval_snapshot.writer.status = ComponentHealthState.HEALTHY.value
            else:
                eval_snapshot.writer.status = ComponentHealthState.UNKNOWN.value

        # 4. ARCHIVER EVALUATION
        if eval_snapshot.archiver.upload_failures > 0 or eval_snapshot.archiver.archive_errors > 0:
            eval_snapshot.archiver.status = ComponentHealthState.DEGRADED.value
        elif eval_snapshot.archiver.status in (ComponentHealthState.UNKNOWN.value, ""):
            if snapshot is not None:
                eval_snapshot.archiver.status = ComponentHealthState.HEALTHY.value
            else:
                eval_snapshot.archiver.status = ComponentHealthState.UNKNOWN.value

        # 5. EVIDENCE EVALUATION
        if eval_snapshot.evidence.status in (ComponentHealthState.UNKNOWN.value, ""):
            eval_snapshot.evidence.status = ComponentHealthState.HEALTHY.value

        # 6. OBSERVER SELF-OBSERVATION
        eval_snapshot.observer.observer_pid = self.observer_pid
        eval_snapshot.observer.observer_started_at = self.observer_started_at
        eval_snapshot.observer.observer_last_cycle = now.isoformat()
        eval_snapshot.observer.observer_errors = self.observer_errors
        eval_snapshot.observer.status = (
            ComponentHealthState.HEALTHY.value if self.observer_errors == 0 else ComponentHealthState.DEGRADED.value
        )

        # 7. RESOURCE TELEMETRY
        try:
            vfs = os.statvfs(str(self.config.data_dir))
            eval_snapshot.resources.disk_free_bytes = vfs.f_bavail * vfs.f_frsize
            eval_snapshot.resources.disk_used_bytes = (vfs.f_blocks - vfs.f_bfree) * vfs.f_frsize
        except Exception:
            pass

        return eval_snapshot

    def _upload_to_s3_fail_safe(
        self,
        snapshot: RuntimeHealthSnapshot,
        now: datetime,
    ) -> bool:
        """Upload minute witness and latest snapshot to S3 if allowed.
        
        Returns True if upload succeeded or S3 upload is not configured.
        Returns False and marks EVIDENCE = DEGRADED if configured upload fails.
        """
        bucket, prefix = parse_s3_location(self.config.s3_bucket, self.config.s3_prefix)
        if not bucket or not self.config.allow_s3_write:
            return True

        client = self._get_s3_client()
        if client is None:
            self.observer_errors += 1
            snapshot.evidence.status = ComponentHealthState.DEGRADED.value
            logger.error("S3 upload failed: boto3 client is not available")
            return False

        minute_tag = now.strftime("%Y%m%dT%H%M%SZ")
        minute_key = (
            f"{prefix}/observability/minute/{minute_tag}.json"
            if prefix
            else f"observability/minute/{minute_tag}.json"
        )
        latest_key = f"{prefix}/observability/latest.json" if prefix else "observability/latest.json"

        payload = json.dumps(snapshot.to_dict(), indent=2, sort_keys=True) + "\n"
        body_bytes = payload.encode("utf-8")

        try:
            client.put_object(
                Bucket=bucket,
                Key=minute_key,
                Body=body_bytes,
                ContentType="application/json",
            )
            client.put_object(
                Bucket=bucket,
                Key=latest_key,
                Body=body_bytes,
                ContentType="application/json",
            )
            snapshot.archiver.last_s3_put = now.isoformat()
            return True
        except Exception as exc:
            self.observer_errors += 1
            snapshot.evidence.status = ComponentHealthState.DEGRADED.value
            snapshot.last_exception = LastExceptionInfo(
                component="EVIDENCE",
                type=exc.__class__.__name__,
                message_hash=compute_exception_hash(str(exc)),
                timestamp=now.isoformat(),
            )
            eval_obs_err = self.observer_errors
            snapshot.observer.observer_errors = eval_obs_err
            snapshot.observer.status = ComponentHealthState.DEGRADED.value
            logger.error("S3 witness upload failed: %s (marked EVIDENCE=DEGRADED)", exc)
            return False

    def run_cycle(self, now: Optional[datetime] = None) -> RuntimeHealthSnapshot:
        """Execute a single observation cycle."""
        actual_now = now or datetime.now(timezone.utc)
        now_mono = time.monotonic()

        # 1. Query systemd unit if unit_name provided
        unit_props = None
        if self.config.unit_name:
            unit_props = self._systemd_query_fn(self.config.unit_name)

        # 2. Read latest collector snapshot
        collector_health_path = self.config.data_dir / "health" / "latest.json"
        existing_snapshot = read_health_snapshot(collector_health_path)

        # 3. Evaluate health states
        evaluated = self.evaluate_health(existing_snapshot, unit_props, actual_now)

        # 3b. Read archiver health sidecar and merge into evaluated archiver
        archiver_health_path = self.config.data_dir / "health" / "archiver_latest.json"
        archiver_snapshot = read_health_snapshot(archiver_health_path)
        if archiver_snapshot is not None:
            archiver_data = archiver_snapshot.archiver
            # Check staleness: if observed_at is > 5 minutes ago, mark STALE
            observed_dt = _parse_utc_iso(archiver_snapshot.observed_at)
            age_seconds = (actual_now - observed_dt).total_seconds() if observed_dt else float("inf")
            if age_seconds > 300:
                evaluated.archiver.status = ComponentHealthState.STALE.value
                evaluated.archiver.archive_queue_depth = archiver_data.archive_queue_depth
            else:
                # Merge real archiver fields from scheduler
                evaluated.archiver.status = archiver_data.status
                evaluated.archiver.archive_queue_depth = archiver_data.archive_queue_depth
                evaluated.archiver.last_closed_cohort = archiver_data.last_closed_cohort
                evaluated.archiver.last_compression = archiver_data.last_compression
                evaluated.archiver.last_s3_put = archiver_data.last_s3_put
                evaluated.archiver.last_receipt = archiver_data.last_receipt
                evaluated.archiver.archive_errors = archiver_data.archive_errors
                evaluated.archiver.upload_failures = archiver_data.upload_failures
        else:
            # No archiver health file at all -> STALE
            evaluated.archiver.status = ComponentHealthState.STALE.value

        # 4. Check if S3 upload window reached (every 60 seconds)
        should_upload_s3 = (
            self.last_s3_upload_mono == 0.0
            or (now_mono - self.last_s3_upload_mono) >= self.config.s3_publish_interval_seconds
        )
        if should_upload_s3:
            s3_success = self._upload_to_s3_fail_safe(evaluated, actual_now)
            if s3_success:
                self.last_s3_upload_mono = now_mono

        # 5. Write local observer snapshot atomically to health/observer_latest.json
        observer_latest_path = self.config.data_dir / "health" / "observer_latest.json"
        try:
            write_health_snapshot_atomic(observer_latest_path, evaluated)
        except Exception as exc:
            self.observer_errors += 1
            logger.error("Failed to write local observer snapshot: %s", exc)

        self.observer_last_cycle = actual_now.isoformat()
        self.last_evaluated_snapshot = evaluated
        return evaluated

    def run_forever(self) -> None:
        """Run observation loop until SIGINT/SIGTERM received."""
        logger.info(
            "Starting RuntimeObserver daemon (pid=%d, poll=%.1fs, s3_interval=%.1fs, unit=%s)",
            self.observer_pid,
            self.config.poll_interval_seconds,
            self.config.s3_publish_interval_seconds,
            self.config.unit_name,
        )

        def _handle_signal(signum: int, _frame: object = None) -> None:
            logger.info("Received termination signal %d, stopping observer gracefully...", signum)
            self._shutdown_requested = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handle_signal)
            except (ValueError, OSError):
                pass

        while not self._shutdown_requested:
            try:
                self.run_cycle()
            except Exception as exc:
                self.observer_errors += 1
                logger.error("Unexpected error in observer loop: %s", exc, exc_info=True)

            sleep_time = self.config.poll_interval_seconds
            step = 0.5
            slept = 0.0
            while slept < sleep_time and not self._shutdown_requested:
                time.sleep(min(step, sleep_time - slept))
                slept += step

        logger.info("RuntimeObserver daemon stopped.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Independent Runtime Observer Daemon")
    parser.add_argument("--data-dir", type=Path, required=True, help="Base data directory (e.g. /var/lib/bitcoin-trader/<epoch>)")
    parser.add_argument("--epoch", default="", help="Epoch name")
    parser.add_argument("--run-id", default="", help="Run ID")
    parser.add_argument("--unit-name", default=None, help="Systemd service unit name to query")
    parser.add_argument("--poll-interval", type=float, default=15.0, help="Local polling interval in seconds (10-30s)")
    parser.add_argument("--s3-publish-interval", type=float, default=60.0, help="S3 publish interval in seconds")
    parser.add_argument("--stale-threshold", type=float, default=30.0, help="Stale loop heartbeat threshold in seconds")
    parser.add_argument("--s3-bucket", default=None, help="S3 bucket for immutable witness")
    parser.add_argument("--s3-prefix", default=None, help="S3 key prefix for immutable witness")
    parser.add_argument("--allow-s3-write", action="store_true", help="Allow uploading witnesses to AWS S3")
    parser.add_argument("--one-shot", action="store_true", help="Run a single cycle and exit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
    )

    config = ObserverConfig(
        data_dir=args.data_dir.resolve(),
        epoch=args.epoch,
        run_id=args.run_id,
        unit_name=args.unit_name,
        poll_interval_seconds=args.poll_interval,
        s3_publish_interval_seconds=args.s3_publish_interval,
        stale_heartbeat_threshold_seconds=args.stale_threshold,
        s3_bucket=args.s3_bucket,
        s3_prefix=args.s3_prefix,
        allow_s3_write=args.allow_s3_write,
    )

    observer = RuntimeObserver(config=config)

    if args.one_shot:
        snapshot = observer.run_cycle()
        print(json.dumps(snapshot.to_dict(), indent=2))
        return 0

    observer.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
