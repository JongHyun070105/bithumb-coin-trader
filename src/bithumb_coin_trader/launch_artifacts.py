"""Authoritative Single Source of Truth (SSOT) launch artifact generator and validator.

Guarantees:
1. Duration consistency across runtime config, collector command, and supervisor command.
2. Canonical template placeholders with exactly one {collector_epoch}.
3. Exact 1-to-1 path binding between resolved runtime config and command arguments.
4. Archive scheduler --base-dir binds to epoch root (<parent>/<epoch>).
5. Canonical config fingerprint binding.
6. Non-mutating production collector config validation dry-run.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Any, Sequence

from bithumb_coin_trader.utc_schedule_planner import (
    UtcSchedulePlan,
    compute_full_utc_hour_schedule,
)

BITHUMB_MARKETS: tuple[str, ...] = (
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE",
    "KRW-ADA", "KRW-XLM", "KRW-LINK", "KRW-AVAX", "KRW-BCH",
    "KRW-ETC", "KRW-NEAR", "KRW-SUI", "KRW-APT", "KRW-TRX",
    "KRW-SHIB", "KRW-SAND", "KRW-MANA", "KRW-AXS", "KRW-DOT",
)
BINANCE_SYMBOLS: tuple[str, ...] = ("btcusdt", "ethusdt", "solusdt", "xrpusdt")
UPBIT_MARKETS: tuple[str, ...] = ("KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP")

SUPPORTED_DURATIONS: tuple[int, ...] = (2700, 5400, 7200, 10800, 21600, 108000, 259200)

REQUIRED_PATH_TEMPLATES: tuple[str, ...] = (
    "raw_root_template",
    "manifest_root_template",
    "compressed_root_template",
    "receipt_root_template",
    "metrics_path_template",
    "publisher_state_path_template",
    "log_root_template",
)


def canonical_config_fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ValidationRunSpec:
    epoch: str
    run_id: str
    duration_seconds: int
    runtime_commit: str
    software_tree_sha: str | None = None
    target_full_hours: int = 1
    planned_start_time: str | None = None
    finalization_timeout_seconds: int = 180
    supervisor_hard_ceiling_seconds: int | None = None
    systemd_runtime_max_seconds: int | None = None
    base_data_parent: Path = Path("/var/lib/bitcoin-trader/90m-validation")
    runtime_worktree: Path = Path("/var/lib/bitcoin-trader/runtime-worktrees/aws-observability-90m-20260917")
    launch_artifacts_parent: Path = Path("/var/lib/bitcoin-trader/launch-artifacts")
    python_bin: Path = Path("/var/lib/bitcoin-trader/venv-pre-soak/bin/python")
    s3_bucket: str = "bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433"
    environment_id: str = "aws-apne2-research"
    region: str = "ap-northeast-2"

    def __post_init__(self) -> None:
        if self.duration_seconds not in SUPPORTED_DURATIONS:
            raise ValueError(
                f"duration_seconds {self.duration_seconds} not in supported list: {SUPPORTED_DURATIONS}"
            )
        if not self.epoch or not self.run_id or not self.runtime_commit:
            raise ValueError("epoch, run_id, and runtime_commit must be non-empty")
        if self.s3_bucket == "bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433" and not self.epoch.startswith("aws-validation-"):
            raise ValueError(
                f"S3 IAM policy for research bucket only permits 'market-data/temporary/aws-validation-*/*'; "
                f"epoch {self.epoch!r} must start with 'aws-validation-'"
            )

    @property
    def effective_hard_ceiling(self) -> int:
        if self.supervisor_hard_ceiling_seconds is not None:
            return self.supervisor_hard_ceiling_seconds
        return self.duration_seconds + self.finalization_timeout_seconds

    @property
    def effective_runtime_max(self) -> int:
        if self.systemd_runtime_max_seconds is not None:
            return self.systemd_runtime_max_seconds
        return self.effective_hard_ceiling + 60

    @property
    def epoch_data_root(self) -> Path:
        return self.base_data_parent / self.epoch

    @property
    def epoch_artifacts_dir(self) -> Path:
        return self.launch_artifacts_parent / self.epoch

    @property
    def schedule_plan(self) -> UtcSchedulePlan | None:
        if self.planned_start_time is None:
            return None
        dt_str = self.planned_start_time.replace("Z", "+00:00")
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return compute_full_utc_hour_schedule(dt, target_full_hours=self.target_full_hours)


@dataclass
class LaunchArtifactSet:
    spec: ValidationRunSpec
    runtime_config: dict[str, Any]
    config_fingerprint: str
    resolved_paths: dict[str, str]
    launch_command: dict[str, Any]
    identity: dict[str, Any]
    launch_sh: str
    launch_ec2_sh: str


def validate_template_placeholders(paths: dict[str, Any]) -> None:
    for field in REQUIRED_PATH_TEMPLATES:
        val = paths.get(field)
        if not isinstance(val, str) or val.count("{collector_epoch}") != 1:
            raise ValueError(f"{field} must contain exactly one {{collector_epoch}} placeholder, got {val!r}")
        rendered = val.replace("{collector_epoch}", "dummy")
        if "{" in rendered or "}" in rendered:
            raise ValueError(f"{field} contains an unsupported template placeholder: {val!r}")


def generate_canonical_runtime_config(spec: ValidationRunSpec) -> dict[str, Any]:
    parent_str = str(spec.base_data_parent).rstrip("/")
    config: dict[str, Any] = {
        "schema_version": 1,
        "runtime_software_commit": spec.runtime_commit,
        "environment_id": spec.environment_id,
        "region": spec.region,
        "availability_zone": "ap-northeast-2a",
        "instance_type": "t3.medium",
        "architecture": "x86_64",
        "raw_schema_version": 4,
        "clock_source": "Amazon Time Sync Service 169.254.169.123",
        "public_data_only": True,
        "private_api_enabled": False,
        "duration_seconds": spec.duration_seconds,
        "schedule": (
            {
                "qualification_rule": "IMMEDIATE" if spec.schedule_plan is None else "FULL_UTC_HOUR",
                "target_full_hours": spec.target_full_hours,
                "required_qualifying_full_hours": spec.target_full_hours if spec.schedule_plan is not None else 0,
                "maximum_collection_window_seconds": spec.duration_seconds,
                **({
                    "planned_start_utc": spec.schedule_plan.actual_start_utc,
                    "warmup_duration_seconds": spec.schedule_plan.warmup_duration_seconds,
                    "qualification_start_utc": spec.schedule_plan.qualification_start_utc,
                    "qualifying_cohorts": list(spec.schedule_plan.qualifying_cohorts),
                    "cohort_closure_utc": spec.schedule_plan.cohort_closure_utc,
                    "grace_seconds": spec.schedule_plan.grace_seconds,
                    "grace_expiry_utc": spec.schedule_plan.grace_expiry_utc,
                    "archive_settled_utc": spec.schedule_plan.archive_settled_utc,
                    "total_pipeline_duration_seconds": spec.schedule_plan.total_pipeline_duration_seconds,
                    "partial_start_cohort": spec.schedule_plan.partial_start_cohort,
                    "partial_end_cohort": spec.schedule_plan.partial_end_cohort,
                } if spec.schedule_plan is not None else {}),
            }
        ),
        "feeds": {
            "bithumb_market_count": len(BITHUMB_MARKETS),
            "bithumb_markets": list(BITHUMB_MARKETS),
            "binance_symbols": list(BINANCE_SYMBOLS),
            "upbit_markets": list(UPBIT_MARKETS),
        },
        "paths": {
            "raw_root_template": f"{parent_str}/{{collector_epoch}}/raw",
            "manifest_root_template": f"{parent_str}/{{collector_epoch}}/manifests",
            "compressed_root_template": f"{parent_str}/{{collector_epoch}}/compressed",
            "receipt_root_template": f"{parent_str}/{{collector_epoch}}/archive-receipts",
            "metrics_path_template": f"{parent_str}/{{collector_epoch}}/collector_metrics.json",
            "publisher_state_path_template": f"{parent_str}/{{collector_epoch}}/metric-publisher-state.json",
            "log_root_template": f"{parent_str}/{{collector_epoch}}/logs",
        },
        "archive": {
            "remote_class": "temporary",
            "temporary_prefix_template": "market-data/temporary/{collector_epoch}",
            "compression": {
                "algorithm": "zstd",
                "level": 1,
            },
            "worker_concurrency": 1,
            "grace_seconds": 600,
            "cleanup_enabled": False,
        },
        "metrics": {
            "namespace": "BitcoinTrader/Collector",
            "environment_dimension": spec.environment_id,
            "publish_cadence_seconds": 60,
            "false_green_protection": True,
        },
        "disk_threshold_percent": {
            "warning": 70,
            "high": 80,
            "critical": 90,
        },
        "execution": {
            "launch_mode": "bounded-transient-systemd",
            "collector_autostart": False,
            "systemd_enable": False,
            "cross_utc_hour_required": False,
            "finalization_timeout_seconds": spec.finalization_timeout_seconds,
            "supervisor_hard_ceiling_seconds": spec.effective_hard_ceiling,
            "systemd_runtime_max_seconds": spec.effective_runtime_max,
        },
    }
    validate_template_placeholders(config["paths"])
    return config


def resolve_epoch_paths(runtime_config: dict[str, Any], epoch: str) -> dict[str, str]:
    paths = runtime_config["paths"]
    validate_template_placeholders(paths)
    resolved = {}
    for key, template in paths.items():
        resolved[key] = template.replace("{collector_epoch}", epoch)
    archive = runtime_config["archive"]
    prefix_template = archive.get("temporary_prefix_template", "")
    if "{collector_epoch}" not in prefix_template:
        raise ValueError("archive temporary_prefix_template must contain {collector_epoch}")
    resolved["temporary_prefix"] = prefix_template.replace("{collector_epoch}", epoch)
    return resolved


def generate_launch_artifacts(
    spec: ValidationRunSpec,
    target_dir: Path | None = None,
) -> LaunchArtifactSet:
    runtime_config = generate_canonical_runtime_config(spec)
    fingerprint = canonical_config_fingerprint(runtime_config)
    resolved = resolve_epoch_paths(runtime_config, spec.epoch)

    python_str = str(spec.python_bin)
    worktree_str = str(spec.runtime_worktree)
    data_root = spec.epoch_data_root
    data_root_str = str(data_root)
    artifacts_dir = spec.epoch_artifacts_dir
    artifacts_dir_str = str(artifacts_dir)
    config_file_path = f"{artifacts_dir_str}/{spec.epoch}.runtime.json"

    collector_cmd = [
        python_str,
        f"{worktree_str}/scripts/run_cross_market_collector.py",
        "--bithumb-markets", str(len(BITHUMB_MARKETS)),
        "--duration", str(spec.duration_seconds),
        "--config-file", config_file_path,
        "--storage-base-dir", resolved["raw_root_template"],
        "--environment-id", spec.environment_id,
        "--collector-epoch", spec.epoch,
        "--run-id", spec.run_id,
        "--config-fingerprint", fingerprint,
        "--runtime-commit", spec.runtime_commit,
        "--lifecycle-status-path", f"{data_root_str}/collector-lifecycle.json",
    ]

    publisher_cmd = [
        python_str,
        f"{worktree_str}/scripts/publish_collector_metrics.py",
        "--environment-id", spec.environment_id,
        "--region", spec.region,
        "--metrics-path", resolved["metrics_path_template"],
        "--state-path", resolved["publisher_state_path_template"],
        "--storage-path", resolved["raw_root_template"],
        "--ops-log", f"{resolved['log_root_template']}/metric-publisher-ops.jsonl",
    ]

    scheduler_cmd = [
        python_str,
        f"{worktree_str}/scripts/run_closed_hour_archive_scheduler.py",
        "--epoch", spec.epoch,
        "--run-id", spec.run_id,
        "--base-dir", data_root_str,
        "--environment-id", spec.environment_id,
        "--git-commit", spec.runtime_commit,
        "--store", "s3",
        "--s3-bucket", spec.s3_bucket,
        "--allow-aws-write",
        "--remote-prefix", resolved["temporary_prefix"],
        "--poll-interval-seconds", "30.0",
        "--grace-seconds", "600",
        "--expected-owner", "bitcoin-trader",
        "--scan-runner", "auto",
        "--disk-critical-percent", "90.0",
    ]

    supervisor_command = [
        python_str,
        f"{worktree_str}/scripts/run_bounded_short_smoke.py",
        "--run-id", spec.run_id,
        "--collection-duration-seconds", str(spec.duration_seconds),
        "--finalization-timeout-seconds", str(spec.finalization_timeout_seconds),
        "--hard-ceiling-seconds", str(spec.effective_hard_ceiling),
        "--collector-command-json", json.dumps(collector_cmd),
        "--publisher-command-json", json.dumps(publisher_cmd),
        "--archive-scheduler-command-json", json.dumps(scheduler_cmd),
        "--metrics-path", resolved["metrics_path_template"],
        "--collector-lifecycle-path", f"{data_root_str}/collector-lifecycle.json",
        "--result-path", f"{data_root_str}/result.json",
        "--log-path", f"{resolved['log_root_template']}/supervisor.log",
        "--publisher-interval-seconds", "60",
        "--shutdown-grace-seconds", "45.0",
        "--require-full-duration",
    ]

    if spec.duration_seconds == 5400:
        unit_prefix = "bitcoin-trader-90m"
    elif spec.duration_seconds == 10800:
        unit_prefix = "bitcoin-trader-3h"
    elif spec.duration_seconds == 21600:
        unit_prefix = "bitcoin-trader-6h"
    elif spec.duration_seconds == 2700:
        unit_prefix = "bitcoin-trader-short-smoke"
    else:
        unit_prefix = "bitcoin-trader-transient"

    observer_cmd = [
        python_str,
        "-m", "bithumb_coin_trader.runtime_observer",
        "--data-dir", data_root_str,
        "--epoch", spec.epoch,
        "--run-id", spec.run_id,
        "--unit-name", f"{unit_prefix}-{spec.run_id}.service",
        "--poll-interval", "15.0",
        "--s3-publish-interval", "60.0",
        "--stale-threshold", "30.0",
        "--s3-bucket", spec.s3_bucket,
        "--s3-prefix", resolved["temporary_prefix"],
        "--allow-s3-write",
    ]

    launch_command = {
        "schema_version": 2,
        "runtime_worktree": worktree_str,
        "python": python_str,
        "data_root": data_root_str,
        "run_id": spec.run_id,
        "supervisor_command": supervisor_command,
        "observer_command": observer_cmd,
        "exec_stop_post_script": f"{worktree_str}/scripts/terminal_witness.py",
        "collection_duration_seconds": spec.duration_seconds,
        "finalization_timeout_seconds": spec.finalization_timeout_seconds,
        "supervisor_hard_ceiling_seconds": spec.effective_hard_ceiling,
        "systemd_runtime_max_seconds": spec.effective_runtime_max,
        "launch": False,
    }

    identity = {
        "schema_version": 1,
        "epoch": spec.epoch,
        "run_id": spec.run_id,
        "software_commit_sha": spec.runtime_commit,
        "software_tree_sha": spec.software_tree_sha or "unspecified",
        "config_fingerprint": fingerprint,
        "sealed_at_utc": "",  # populated on sealing
        "feed_count": 76,
        "duration_seconds": spec.duration_seconds,
        "grace_seconds": 600,
        "target_full_hours": spec.target_full_hours,
        **({
            "planned_start_utc": spec.schedule_plan.actual_start_utc,
            "warmup_duration_seconds": spec.schedule_plan.warmup_duration_seconds,
            "qualification_start_utc": spec.schedule_plan.qualification_start_utc,
            "qualifying_cohorts": list(spec.schedule_plan.qualifying_cohorts),
            "cohort_closure_utc": spec.schedule_plan.cohort_closure_utc,
            "grace_expiry_utc": spec.schedule_plan.grace_expiry_utc,
            "archive_settled_utc": spec.schedule_plan.archive_settled_utc,
            "total_pipeline_duration_seconds": spec.schedule_plan.total_pipeline_duration_seconds,
            "partial_start_cohort": spec.schedule_plan.partial_start_cohort,
            "partial_end_cohort": spec.schedule_plan.partial_end_cohort,
        } if spec.schedule_plan is not None else {}),
        "health_schema_version": 1,
        "observer_version": 1,
        "s3_bucket": spec.s3_bucket,
        "s3_prefix": resolved["temporary_prefix"],
        "runtime_worktree": worktree_str,
        "data_root": data_root_str,
        "python": python_str,
        "purpose": "INFRASTRUCTURE_VALIDATION_ONLY",
        "trading_safety": {
            "alpha": "UNPROVEN",
            "paper": "NOT_STARTED",
            "live": "DISABLED",
            "private_api": "DISABLED",
        },
    }

    launch_ec2_sh = f"""#!/usr/bin/env bash
set -euo pipefail

worktree="{worktree_str}"
python="{python_str}"
artifacts_dir="{artifacts_dir_str}"

sup_cmd_json=$("$python" -c '
import json
import sys
with open("'"$artifacts_dir"'/launch-command.json") as f:
    d = json.load(f)
print(json.dumps(d["supervisor_command"]))
')

export PYTHONPATH="$worktree/src"

# T0 Observer Sequencing Contract: Ensure OBSERVER_START <= COLLECTOR_START
is_launch=false
for arg in "$@"; do
  if [ "$arg" = "--launch" ]; then
    is_launch=true
    break
  fi
done

if [ "$is_launch" = true ]; then
  obs_unit="bitcoin-trader-obs-{spec.run_id}.service"
  echo "[LAUNCH] Pre-starting runtime observer unit $obs_unit to guarantee OBSERVER_START <= COLLECTOR_START..."
  systemd-run \\
    --unit="$obs_unit" \\
    --description="Runtime Observer for {spec.run_id}" \\
    --service-type=simple \\
    --no-block \\
    --property="Environment=PYTHONPATH=$worktree/src" \\
    "$python" -m bithumb_coin_trader.runtime_observer \\
      --data-dir "{data_root_str}" \\
      --epoch "{spec.epoch}" \\
      --run-id "{spec.run_id}" \\
      --unit-name "{unit_prefix}-{spec.run_id}.service" \\
      --poll-interval 15.0 \\
      --s3-publish-interval 60.0 \\
      --stale-threshold 30.0 \\
      --s3-bucket "{spec.s3_bucket}" \\
      --s3-prefix "{resolved['temporary_prefix']}" \\
      --allow-s3-write

  for i in $(seq 1 10); do
    if systemctl is-active --quiet "$obs_unit" 2>/dev/null; then
      echo "[LAUNCH] Observer is ACTIVE before collector launch (T0 verified)."
      break
    fi
    sleep 0.5
  done

  # Fail-closed Observer T0 Readiness Verification!
  echo "[LAUNCH] Verifying Observer T0 readiness (FAIL-CLOSED)..."
  if ! "$python" -m bithumb_coin_trader.observer_readiness \\
      --health-dir "{data_root_str}/health" \\
      --epoch "{spec.epoch}" \\
      --run-id "{spec.run_id}" \\
      --timeout 30.0; then
    echo "[LAUNCH] CRITICAL: Observer T0 readiness verification failed. COLLECTOR WILL NOT START." >&2
    exit 1
  fi
  echo "[LAUNCH] Observer T0 readiness VERIFIED: OBSERVER_READY_TIME <= COLLECTOR_START_TIME."

  # Fail-closed Launch-Time Schedule Freshness Enforcement!
  planned_start="{spec.schedule_plan.actual_start_utc if spec.schedule_plan else ''}"
  qual_start="{spec.schedule_plan.qualification_start_utc if spec.schedule_plan else ''}"
  if [ -n "$planned_start" ] && [ -n "$qual_start" ]; then
    echo "[LAUNCH] Enforcing launch freshness against sealed schedule (FAIL-CLOSED)..."
    if ! "$python" -m bithumb_coin_trader.launch_freshness \\
        --planned-start "$planned_start" \\
        --qualification-start "$qual_start" \\
        --max-delay 60.0; then
      echo "[LAUNCH] CRITICAL: Launch schedule freshness violation. COLLECTOR WILL NOT START." >&2
      exit 1
    fi
  fi
fi

exec "$python" "$worktree/scripts/launch_short_smoke_transient.py" \\
  --run-id "{spec.run_id}" \\
  --workdir "$worktree" \\
  --supervisor-command-json "$sup_cmd_json" \\
  --collection-duration-seconds {spec.duration_seconds} \\
  --finalization-timeout-seconds {spec.finalization_timeout_seconds} \\
  --supervisor-hard-ceiling-seconds {spec.effective_hard_ceiling} \\
  --systemd-runtime-max-seconds {spec.effective_runtime_max} \\
  --exec-stop-post-script "$worktree/scripts/terminal_witness.py" \\
  --data-dir "{data_root_str}" \\
  "$@"
"""

    launch_sh = f"""#!/usr/bin/env bash
set -euo pipefail

worktree="{worktree_str}"
python="{python_str}"

sup_cmd_json=$("$python" -c '
import json
with open("launch-command.json") as f:
    d = json.load(f)
print(json.dumps(d["supervisor_command"]))
')

export PYTHONPATH="$worktree/src"
exec "$python" "$worktree/scripts/launch_short_smoke_transient.py" \\
  --run-id "{spec.run_id}" \\
  --workdir "$worktree" \\
  --supervisor-command-json "$sup_cmd_json" \\
  --collection-duration-seconds {spec.duration_seconds} \\
  --finalization-timeout-seconds {spec.finalization_timeout_seconds} \\
  --supervisor-hard-ceiling-seconds {spec.effective_hard_ceiling} \\
  --systemd-runtime-max-seconds {spec.effective_runtime_max} \\
  --exec-stop-post-script "$worktree/scripts/terminal_witness.py" \\
  --data-dir "{data_root_str}" \\
  "$@"
"""

    sealed_at = datetime.now(timezone.utc).isoformat()
    identity["sealed_at_utc"] = sealed_at

    artifacts = LaunchArtifactSet(
        spec=spec,
        runtime_config=runtime_config,
        config_fingerprint=fingerprint,
        resolved_paths=resolved,
        launch_command=launch_command,
        identity=identity,
        launch_sh=launch_sh,
        launch_ec2_sh=launch_ec2_sh,
    )

    if target_dir is not None:
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / f"{spec.epoch}.runtime.json").write_text(
            json.dumps(runtime_config, indent=2) + "\n", encoding="utf-8"
        )
        (target_dir / "launch-command.json").write_text(
            json.dumps(launch_command, indent=2) + "\n", encoding="utf-8"
        )
        launch_ec2_path = target_dir / "launch-ec2.sh"
        launch_ec2_path.write_text(launch_ec2_sh, encoding="utf-8")
        launch_ec2_path.chmod(launch_ec2_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        launch_path = target_dir / "launch.sh"
        launch_path.write_text(launch_sh, encoding="utf-8")
        launch_path.chmod(launch_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        # Compute SHA-256 for sealed artifacts and bind into identity
        rt_hash = hashlib.sha256((target_dir / f"{spec.epoch}.runtime.json").read_bytes()).hexdigest()
        cmd_hash = hashlib.sha256((target_dir / "launch-command.json").read_bytes()).hexdigest()
        ec2_hash = hashlib.sha256(launch_ec2_path.read_bytes()).hexdigest()
        sh_hash = hashlib.sha256(launch_path.read_bytes()).hexdigest()

        identity["sealed_artifact_hashes"] = {
            f"{spec.epoch}.runtime.json": rt_hash,
            "launch-command.json": cmd_hash,
            "launch-ec2.sh": ec2_hash,
            "launch.sh": sh_hash,
        }

        identity_bytes = (json.dumps(identity, indent=2) + "\n").encode("utf-8")
        (target_dir / "identity.json").write_bytes(identity_bytes)
        identity_hash = hashlib.sha256(identity_bytes).hexdigest()

        manifest = {
            "epoch": spec.epoch,
            "run_id": spec.run_id,
            "sealed_at_utc": sealed_at,
            "identity_sha256": identity_hash,
            "artifact_hashes": identity["sealed_artifact_hashes"],
        }
        (target_dir / "sealed-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return artifacts


def validate_launch_artifacts(
    spec: ValidationRunSpec,
    runtime_config: dict[str, Any],
    launch_command: dict[str, Any] | None = None,
    target_dir: Path | None = None,
) -> dict[str, Any]:
    """Strictly validate launch artifacts against all regression rules."""
    # Rule 1: DURATION CONSISTENCY
    rt_duration = runtime_config.get("duration_seconds")
    if not isinstance(rt_duration, (int, float)) or rt_duration != spec.duration_seconds or rt_duration <= 0:
        raise ValueError(
            f"duration inconsistency: runtime config duration_seconds={rt_duration} "
            f"does not match expected positive duration {spec.duration_seconds}"
        )

    # Rule 2: TEMPLATE PLACEHOLDERS
    paths = runtime_config.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("runtime config paths section must be a dictionary")
    validate_template_placeholders(paths)

    resolved = resolve_epoch_paths(runtime_config, spec.epoch)

    # Rule 3: FINGERPRINT INTEGRITY
    actual_fp = canonical_config_fingerprint(runtime_config)

    # If launch_command is provided, perform deep binding checks
    if launch_command is not None:
        sup_cmd = launch_command.get("supervisor_command")
        if not isinstance(sup_cmd, list):
            raise ValueError("supervisor_command must be a list")

        # 3.1: Supervisor duration check
        if "--collection-duration-seconds" not in sup_cmd:
            raise ValueError("supervisor_command missing --collection-duration-seconds")
        s_idx = sup_cmd.index("--collection-duration-seconds")
        sup_duration = int(sup_cmd[s_idx + 1])
        if sup_duration != spec.duration_seconds:
            raise ValueError(
                f"duration inconsistency: supervisor duration {sup_duration} "
                f"does not match target duration {spec.duration_seconds}"
            )

        # 3.2: Collector command checks
        c_idx = sup_cmd.index("--collector-command-json")
        collector_cmd = json.loads(sup_cmd[c_idx + 1])
        d_idx = collector_cmd.index("--duration")
        coll_duration = float(collector_cmd[d_idx + 1])
        if coll_duration != spec.duration_seconds or coll_duration <= 0:
            raise ValueError(
                f"duration inconsistency: collector --duration {coll_duration} "
                f"does not match target duration {spec.duration_seconds}"
            )

        # 3.3: Storage base dir binding
        sb_idx = collector_cmd.index("--storage-base-dir")
        coll_storage_dir = collector_cmd[sb_idx + 1]
        if coll_storage_dir != resolved["raw_root_template"]:
            raise ValueError(
                f"path binding failure: collector --storage-base-dir {coll_storage_dir!r} "
                f"does not match resolved raw_root {resolved['raw_root_template']!r}"
            )

        # 3.4: Collector fingerprint binding
        fp_idx = collector_cmd.index("--config-fingerprint")
        coll_fp = collector_cmd[fp_idx + 1]
        if coll_fp != actual_fp:
            raise ValueError(
                f"fingerprint mismatch: collector declared {coll_fp} but actual runtime config hash is {actual_fp}"
            )

        # 3.5: Archive scheduler base dir binding (MUST be epoch root)
        if "--archive-scheduler-command-json" in sup_cmd:
            a_idx = sup_cmd.index("--archive-scheduler-command-json")
            sched_cmd = json.loads(sup_cmd[a_idx + 1])
            ab_idx = sched_cmd.index("--base-dir")
            sched_base_dir = sched_cmd[ab_idx + 1]
            expected_epoch_root = str(spec.epoch_data_root)
            if sched_base_dir != expected_epoch_root:
                raise ValueError(
                    f"archive scheduler base-dir binding failure: expected epoch root {expected_epoch_root!r} "
                    f"but got {sched_base_dir!r}. Scheduler expects directory containing raw/manifests/compressed."
                )
            # Prefix binding
            p_idx = sched_cmd.index("--remote-prefix")
            sched_prefix = sched_cmd[p_idx + 1]
            if sched_prefix != resolved["temporary_prefix"]:
                raise ValueError(
                    f"archive scheduler prefix binding failure: {sched_prefix!r} != {resolved['temporary_prefix']!r}"
                )

        # 3.6: Observer command checks (T0 observer sequencing)
        obs_cmd = launch_command.get("observer_command")
        if obs_cmd is not None:
            if not isinstance(obs_cmd, list):
                raise ValueError("observer_command must be a list")
            if "--data-dir" not in obs_cmd or "--epoch" not in obs_cmd or "--run-id" not in obs_cmd:
                raise ValueError("observer_command missing essential flags")
            o_data_idx = obs_cmd.index("--data-dir")
            if obs_cmd[o_data_idx + 1] != str(spec.epoch_data_root):
                raise ValueError("observer_command data-dir binding failure")

    # Rule 4: DRY RUN PRODUCTION COLLECTOR CONFIG VALIDATION
    try:
        from scripts.run_cross_market_collector import _validate_runtime_config
        dry_args = argparse.Namespace(
            collector_epoch=spec.epoch,
            run_id=spec.run_id,
            storage_base_dir=Path(resolved["raw_root_template"]),
            runtime_commit=spec.runtime_commit,
            environment_id=spec.environment_id,
            duration=spec.duration_seconds,
            qualification_schedule_path=None,
            bithumb_markets=len(BITHUMB_MARKETS),
        )
        _validate_runtime_config(
            runtime_config,
            dry_args,
            list(BITHUMB_MARKETS),
            list(BINANCE_SYMBOLS),
            list(UPBIT_MARKETS),
        )
    except Exception as exc:
        raise ValueError(f"production collector config validation dry-run failed: {exc}") from exc

    # Rule 5: FULL UTC HOUR SCHEDULE CONSISTENCY
    if spec.planned_start_time is not None:
        plan = spec.schedule_plan
        if plan is not None:
            if spec.duration_seconds < plan.total_pipeline_duration_seconds:
                raise ValueError(
                    f"INSUFFICIENT_DURATION_FOR_FULL_UTC_HOUR: planned start {spec.planned_start_time} "
                    f"requires at least {plan.total_pipeline_duration_seconds:.0f}s for {spec.target_full_hours} "
                    f"full UTC hour(s) (warmup={plan.warmup_duration_seconds:.0f}s, "
                    f"full_hours={plan.full_hours_duration_seconds}s, grace={plan.grace_seconds}s, "
                    f"settle={plan.post_grace_settle_seconds}s), but spec duration_seconds is {spec.duration_seconds}s"
                )
            rt_schedule = runtime_config.get("schedule", {})
            if rt_schedule.get("qualification_start_utc") != plan.qualification_start_utc:
                raise ValueError("schedule qualification_start_utc mismatch between spec and runtime config")
            if rt_schedule.get("target_full_hours") != spec.target_full_hours:
                raise ValueError("schedule target_full_hours mismatch between spec and runtime config")

    # Rule 6: ARTIFACT SEAL AND IMMUTABILITY INTEGRITY
    verified_sealed_hashes = False
    sealed_at = None
    if target_dir is not None:
        target_dir = Path(target_dir)
        identity_path = target_dir / "identity.json"
        if identity_path.exists():
            try:
                id_data = json.loads(identity_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ValueError(f"identity.json unreadable in {target_dir}: {exc}") from exc

            sealed_at = id_data.get("sealed_at_utc")
            if not sealed_at or not isinstance(sealed_at, str) or not sealed_at.strip():
                raise ValueError(f"identity.json in {target_dir} missing or empty sealed_at_utc")

            sealed_hashes = id_data.get("sealed_artifact_hashes")
            if sealed_hashes:
                if not isinstance(sealed_hashes, dict):
                    raise ValueError(f"identity.json in {target_dir} sealed_artifact_hashes must be a dictionary")
                for fname, expected_hash in sealed_hashes.items():
                    fpath = target_dir / fname
                    if not fpath.exists():
                        raise ValueError(f"sealed artifact {fname} missing from {target_dir}")
                    actual_file_hash = hashlib.sha256(fpath.read_bytes()).hexdigest()
                    if actual_file_hash != expected_hash:
                        raise ValueError(
                            f"sealed artifact {fname} hash mismatch: recorded {expected_hash} but found {actual_file_hash}"
                        )
                verified_sealed_hashes = True

            manifest_path = target_dir / "sealed-manifest.json"
            if manifest_path.exists():
                try:
                    m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    raise ValueError(f"sealed-manifest.json unreadable in {target_dir}: {exc}") from exc
                actual_id_hash = hashlib.sha256(identity_path.read_bytes()).hexdigest()
                if m_data.get("identity_sha256") != actual_id_hash:
                    raise ValueError(
                        f"sealed-manifest.json identity_sha256 mismatch: recorded {m_data.get('identity_sha256')} "
                        f"but identity.json actual sha256 is {actual_id_hash}"
                    )
                if sealed_hashes and m_data.get("artifact_hashes") != sealed_hashes:
                    raise ValueError(
                        "sealed-manifest.json artifact_hashes does not match identity.json sealed_artifact_hashes"
                    )

    ret = {
        "status": "PASS",
        "duration_seconds": spec.duration_seconds,
        "config_fingerprint": actual_fp,
        "epoch": spec.epoch,
        "run_id": spec.run_id,
    }
    if sealed_at:
        ret["sealed_at_utc"] = sealed_at
    if verified_sealed_hashes:
        ret["sealed_artifact_hashes_verified"] = True
    if spec.schedule_plan is not None:
        ret["target_full_hours"] = spec.target_full_hours
        ret["qualification_start_utc"] = spec.schedule_plan.qualification_start_utc
        ret["qualifying_cohorts"] = list(spec.schedule_plan.qualifying_cohorts)
        ret["cohort_closure_utc"] = spec.schedule_plan.cohort_closure_utc
        ret["grace_expiry_utc"] = spec.schedule_plan.grace_expiry_utc
        ret["archive_settled_utc"] = spec.schedule_plan.archive_settled_utc
    return ret
