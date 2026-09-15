#!/usr/bin/env python3
"""Generate and verify authoritative V3 validation launch seals."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
for d in (ROOT, SRC_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

from bithumb_coin_trader.bounded_supervisor import TransientLaunchConfig, render_systemd_run
from bithumb_coin_trader.evidence_hashing import canonical_sha256, file_sha256
from bithumb_coin_trader.session_evidence import FeedIdentity
from scripts.run_cross_market_collector import canonical_config_fingerprint as collector_config_fingerprint

BITHUMB_MARKETS = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE",
    "KRW-ADA", "KRW-XLM", "KRW-LINK", "KRW-AVAX", "KRW-BCH",
    "KRW-ETC", "KRW-NEAR", "KRW-SUI", "KRW-APT", "KRW-TRX",
    "KRW-SHIB", "KRW-SAND", "KRW-MANA", "KRW-AXS", "KRW-DOT"
]
BINANCE_SYMBOLS = ["btcusdt", "ethusdt", "solusdt", "xrpusdt"]
UPBIT_MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP"]


def canonical_config_fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def generate_feed_universe() -> dict[str, object]:
    feeds = []
    for mkt in BITHUMB_MARKETS:
        feeds.append(FeedIdentity("bithumb", "orderbook", mkt).canonical)
        feeds.append(FeedIdentity("bithumb", "trade", mkt).canonical)
        feeds.append(FeedIdentity("bithumb", "ticker", mkt).canonical)
    for sym in BINANCE_SYMBOLS:
        feeds.append(FeedIdentity("binance", "orderbook", sym).canonical)
        feeds.append(FeedIdentity("binance", "trade", sym).canonical)
    for mkt in UPBIT_MARKETS:
        feeds.append(FeedIdentity("upbit", "orderbook", mkt).canonical)
        feeds.append(FeedIdentity("upbit", "trade", mkt).canonical)

    feeds_sorted = sorted(feeds)
    if len(feeds_sorted) != 76 or len(set(feeds_sorted)) != 76:
        raise ValueError(f"expected 76 unique feeds, got {len(feeds_sorted)}")

    return {
        "schema_version": 1,
        "feed_count": len(feeds_sorted),
        "exchanges": {
            "bithumb": {
                "market_count": len(BITHUMB_MARKETS),
                "feed_count": len(BITHUMB_MARKETS) * 3,
                "markets": BITHUMB_MARKETS,
                "streams": ["orderbook", "trade", "ticker"]
            },
            "binance": {
                "symbol_count": len(BINANCE_SYMBOLS),
                "feed_count": len(BINANCE_SYMBOLS) * 2,
                "symbols": BINANCE_SYMBOLS,
                "streams": ["orderbook", "trade"]
            },
            "upbit": {
                "market_count": len(UPBIT_MARKETS),
                "feed_count": len(UPBIT_MARKETS) * 2,
                "markets": UPBIT_MARKETS,
                "streams": ["orderbook", "trade"]
            }
        },
        "feeds": feeds_sorted
    }


def generate_timing_contract() -> dict[str, object]:
    return {
        "schema_version": 1,
        "qualification_rule": "STRICTLY_NEXT_UTC_HOUR",
        "required_qualifying_full_hours": 30,
        "maximum_collection_window_seconds": 111600,
        "expected_candidate_cohorts": 30,
        "expected_slots_per_cohort": 76,
        "total_expected_coverage_slots": 2280,
        "qualification_start_policy": "strictly_next_utc_hour(actual_start_utc)",
        "monotonic_deadline_policy": "single_conversion_at_launch_immutable_under_wall_clock_adjustments"
    }


def generate_heartbeat_contract() -> dict[str, object]:
    return {
        "schema_version": 1,
        "heartbeat_probe_interval_seconds": 10.0,
        "heartbeat_timeout_seconds": 10.0,
        "maximum_accepted_liveness_gap_seconds": 30.0,
        "fail_threshold_seconds": 31.0,
        "exchanges": ["bithumb", "binance", "upbit"],
        "reconnect_policy": "NEW_SESSION_DISALLOWED_REUSE"
    }


def generate_runtime_config(commit: str, epoch: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "runtime_software_commit": commit,
        "environment_id": "aws-apne2-research",
        "region": "ap-northeast-2",
        "availability_zone": "ap-northeast-2a",
        "instance_type": "t3.medium",
        "architecture": "x86_64",
        "raw_schema_version": 4,
        "clock_source": "Amazon Time Sync Service 169.254.169.123",
        "public_data_only": True,
        "private_api_enabled": False,
        "duration_seconds": 0,
        "schedule": {
            "qualification_rule": "STRICTLY_NEXT_UTC_HOUR",
            "required_qualifying_full_hours": 30,
            "maximum_collection_window_seconds": 111600
        },
        "feeds": {
            "bithumb_market_count": 20,
            "bithumb_markets": BITHUMB_MARKETS,
            "binance_symbols": BINANCE_SYMBOLS,
            "upbit_markets": UPBIT_MARKETS
        },
        "paths": {
            "raw_root_template": f"/var/lib/bitcoin-trader/30h-validation/{epoch}/raw",
            "manifest_root_template": f"/var/lib/bitcoin-trader/30h-validation/{epoch}/manifests",
            "compressed_root_template": f"/var/lib/bitcoin-trader/30h-validation/{epoch}/compressed",
            "receipt_root_template": f"/var/lib/bitcoin-trader/30h-validation/{epoch}/archive-receipts",
            "metrics_path_template": f"/var/lib/bitcoin-trader/30h-validation/{epoch}/collector_metrics.json",
            "publisher_state_path_template": f"/var/lib/bitcoin-trader/30h-validation/{epoch}/metric-publisher-state.json",
            "log_root_template": f"/var/lib/bitcoin-trader/30h-validation/{epoch}/logs"
        },
        "archive": {
            "remote_class": "temporary",
            "temporary_prefix_template": f"market-data/temporary/{epoch}",
            "compression": {
                "algorithm": "zstd",
                "level": 1
            },
            "worker_concurrency": 1,
            "grace_seconds": 600,
            "cleanup_enabled": False
        },
        "metrics": {
            "namespace": "BitcoinTrader/Collector",
            "environment_dimension": "aws-apne2-research",
            "publish_cadence_seconds": 60,
            "false_green_protection": True
        },
        "disk_threshold_percent": {
            "warning": 70,
            "high": 80,
            "critical": 90
        },
        "execution": {
            "launch_mode": "bounded-transient-systemd",
            "collector_autostart": False,
            "systemd_enable": False,
            "cross_utc_hour_required": True,
            "finalization_timeout_seconds": 180,
            "supervisor_hard_ceiling_seconds": 111825,
            "systemd_runtime_max_seconds": 111900
        }
    }


def generate_launch_command(
    commit: str,
    epoch: str,
    run_id: str,
    fingerprint: str,
) -> dict[str, object]:
    worktree = f"/var/lib/bitcoin-trader/runtime-worktrees/{epoch}"
    python_bin = "/var/lib/bitcoin-trader/venv-pre-soak/bin/python"
    data_root = f"/var/lib/bitcoin-trader/30h-validation/{epoch}"
    artifacts_dir = f"/var/lib/bitcoin-trader/launch-artifacts/{epoch}"
    schedule_path = f"{data_root}/qualification_schedule.json"

    collector_cmd = [
        python_bin,
        f"{worktree}/scripts/run_cross_market_collector.py",
        "--bithumb-markets", "20",
        "--duration", "0",
        "--config-file", f"{artifacts_dir}/{epoch}.runtime.json",
        "--storage-base-dir", f"{data_root}/raw",
        "--environment-id", "aws-apne2-research",
        "--collector-epoch", epoch,
        "--run-id", run_id,
        "--config-fingerprint", fingerprint,
        "--runtime-commit", commit,
        "--lifecycle-status-path", f"{data_root}/collector-lifecycle.json",
        "--required-qualifying-full-hours", "30",
        "--maximum-collection-window-seconds", "111600",
        "--qualification-schedule-path", schedule_path
    ]

    publisher_cmd = [
        python_bin,
        f"{worktree}/scripts/publish_collector_metrics.py",
        "--environment-id", "aws-apne2-research",
        "--region", "ap-northeast-2",
        "--metrics-path", f"{data_root}/collector_metrics.json",
        "--state-path", f"{data_root}/metric-publisher-state.json",
        "--storage-path", f"{data_root}/raw",
        "--ops-log", f"{data_root}/logs/metric-publisher-ops.jsonl"
    ]

    scheduler_cmd = [
        python_bin,
        f"{worktree}/scripts/run_closed_hour_archive_scheduler.py",
        "--epoch", epoch,
        "--run-id", run_id,
        "--base-dir", data_root,
        "--environment-id", "aws-apne2-research",
        "--git-commit", commit,
        "--store", "s3",
        "--s3-bucket", "bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433",
        "--allow-aws-write",
        "--remote-prefix", f"market-data/temporary/{epoch}",
        "--poll-interval-seconds", "30.0",
        "--grace-seconds", "600",
        "--expected-owner", "bitcoin-trader",
        "--scan-runner", "auto",
        "--disk-critical-percent", "90.0"
    ]

    supervisor_command = [
        python_bin,
        f"{worktree}/scripts/run_bounded_short_smoke.py",
        "--run-id", run_id,
        "--required-qualifying-full-hours", "30",
        "--maximum-collection-window-seconds", "111600",
        "--qualification-schedule-path", schedule_path,
        "--finalization-timeout-seconds", "180",
        "--hard-ceiling-seconds", "111825",
        "--collector-command-json", json.dumps(collector_cmd),
        "--publisher-command-json", json.dumps(publisher_cmd),
        "--archive-scheduler-command-json", json.dumps(scheduler_cmd),
        "--metrics-path", f"{data_root}/collector_metrics.json",
        "--collector-lifecycle-path", f"{data_root}/collector-lifecycle.json",
        "--result-path", f"{data_root}/result.json",
        "--log-path", f"{data_root}/logs/supervisor.log",
        "--publisher-interval-seconds", "60",
        "--shutdown-grace-seconds", "45.0",
        "--require-full-duration"
    ]

    return {
        "schema_version": 2,
        "runtime_worktree": worktree,
        "python": python_bin,
        "data_root": data_root,
        "run_id": run_id,
        "supervisor_command": supervisor_command,
        "required_qualifying_full_hours": 30,
        "maximum_collection_window_seconds": 111600,
        "qualification_schedule_path": schedule_path,
        "finalization_timeout_seconds": 180,
        "supervisor_hard_ceiling_seconds": 111825,
        "systemd_runtime_max_seconds": 111900,
        "launch": False
    }


def generate_launch_wrapper(epoch: str, run_id: str) -> str:
    worktree = f"/var/lib/bitcoin-trader/runtime-worktrees/{epoch}"
    python_bin = "/var/lib/bitcoin-trader/venv-pre-soak/bin/python"
    artifacts_dir = f"/var/lib/bitcoin-trader/launch-artifacts/{epoch}"
    data_root = f"/var/lib/bitcoin-trader/30h-validation/{epoch}"
    schedule_path = f"{data_root}/qualification_schedule.json"

    return f"""#!/usr/bin/env bash
set -euo pipefail

worktree="{worktree}"
python="{python_bin}"

sup_cmd_json=$("$python" -c '
import json
with open("{artifacts_dir}/{epoch}.launch-command.json") as f:
    d = json.load(f)
print(json.dumps(d["supervisor_command"]))
')

export PYTHONPATH="$worktree/src"
exec "$python" "$worktree/scripts/launch_short_smoke_transient.py" \\
  --run-id "{run_id}" \\
  --workdir "$worktree" \\
  --supervisor-command-json "$sup_cmd_json" \\
  --required-qualifying-full-hours 30 \\
  --maximum-collection-window-seconds 111600 \\
  --qualification-schedule-path "{schedule_path}" \\
  --finalization-timeout-seconds 180 \\
  --supervisor-hard-ceiling-seconds 111825 \\
  --systemd-runtime-max-seconds 111900 \\
  "$@"
"""


def validate_git_commit(commit: str, cwd: Path) -> tuple[str, str]:
    if not commit or not commit.strip():
        raise ValueError("runtime commit argument is missing or empty")
    commit_str = commit.strip()
    try:
        subprocess.run(
            ["git", "cat-file", "-e", f"{commit_str}^{{commit}}"],
            cwd=cwd,
            check=True,
            capture_output=True,
        )
        full_commit = subprocess.run(
            ["git", "rev-parse", "--verify", f"{commit_str}^{{commit}}"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "rev-parse", "--verify", f"{commit_str}^{{tree}}"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except Exception as e:
        raise ValueError(f"Commit validation failed for '{commit}': {e}") from e
    return full_commit, tree


def generate_launch_provenance(
    commit: str,
    runtime_git_tree: str,
    epoch: str,
    run_id: str,
    runtime_seal_sha256: str,
    runtime_fingerprint: str,
    launch_command_sha256: str,
    launch_wrapper_sha256: str,
    feed_universe_sha256: str,
    timing_contract_sha256: str,
    heartbeat_contract_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "runtime_code_commit": commit,
        "runtime_git_tree": runtime_git_tree,
        "runtime_config_seal_path": f"infra/aws/seals/{epoch}.runtime.json",
        "runtime_config_seal_sha256": runtime_seal_sha256,
        "runtime_config_fingerprint": runtime_fingerprint,
        "feed_universe_seal_path": f"infra/aws/seals/{epoch}.feed-universe.json",
        "feed_universe_sha256": feed_universe_sha256,
        "timing_contract_seal_path": f"infra/aws/seals/{epoch}.timing-contract.json",
        "timing_contract_sha256": timing_contract_sha256,
        "heartbeat_contract_seal_path": f"infra/aws/seals/{epoch}.heartbeat-contract.json",
        "heartbeat_contract_sha256": heartbeat_contract_sha256,
        "collector_epoch": epoch,
        "collector_run_id": run_id,
        "environment_id": "aws-apne2-research",
        "required_qualifying_full_hours": 30,
        "maximum_collection_window_seconds": 111600,
        "finalization_timeout_seconds": 180,
        "supervisor_hard_ceiling_seconds": 111825,
        "systemd_runtime_max_seconds": 111900,
        "archive_prefix": f"market-data/temporary/{epoch}",
        "local_runtime_root": f"/var/lib/bitcoin-trader/30h-validation/{epoch}",
        "exact_guest_runtime_worktree": f"/var/lib/bitcoin-trader/runtime-worktrees/{epoch}",
        "launch_command_path": f"infra/aws/seals/{epoch}.launch-command.json",
        "launch_command_sha256": launch_command_sha256,
        "launcher_artifact_path": f"infra/aws/seals/{epoch}.launch-wrapper.sh",
        "launcher_sha256": launch_wrapper_sha256,
        "launcher_kind": "guest-wrapper",
        "launcher_path": f"/var/lib/bitcoin-trader/launch-artifacts/{epoch}/launch.sh",
        "launcher_invocation": f"sudo /var/lib/bitcoin-trader/launch-artifacts/{epoch}/launch.sh --launch",
        "permissions_boundary_arn": "arn:aws:iam::080109295433:policy/bitcoin-trader-collector-boundary",
        "permissions_boundary_version": "v6",
        "permissions_boundary_normalized_sha256": "4bb6906ca631a018e82812b8b0201e43dd90c31aed0f7705a52e41b0394217cd",
        "feed_partition_counts": {
            "bithumb": 60,
            "binance": 8,
            "upbit": 8,
            "total": 76
        },
        "cleanup_enabled": False,
        "private_api_enabled": False,
        "alpha_enabled": False,
        "paper_enabled": False,
        "live_enabled": False,
        "launch_authorized": False,
        "actual_start_time_utc": None,
        "created_at_utc": "2026-09-15T01:30:00Z",
        "authorization_timestamp_utc": None,
        "systemd_control_requires_privilege": True,
        "runtime_service_uid": "bitcoin-trader",
        "launcher_reviewed": True
    }


def generate_authorization_evidence(epoch: str, run_id: str, commit: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "collector_epoch": epoch,
        "collector_run_id": run_id,
        "runtime_commit": commit,
        "launch_authorized": False,
        "actual_start_time_utc": None,
        "actual_start_evidence": None,
        "systemd_unit_active": False,
        "collector_process_running": False,
        "supervisor_process_running": False,
        "archive_scheduler_running": False,
        "official_s3_objects_written": 0,
        "market_data_records_written": 0,
        "status": "PREPARED_NOT_AUTHORIZED",
        "note": "Awaiting explicit human authorization to start official V3 30H validation."
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epoch", default="aws-validation-30h-20260915-v3")
    parser.add_argument("--run-id", default="aws-validation-30h-run-20260915T013000Z-v3")
    parser.add_argument(
        "--runtime-commit",
        "--commit",
        dest="commit",
        required=True,
        help="Authoritative runtime software git commit SHA (required, no stale fallback)",
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "infra" / "aws" / "seals")
    args = parser.parse_args()

    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    epoch = args.epoch
    run_id = args.run_id
    commit, git_tree = validate_git_commit(args.commit, cwd=ROOT)

    # 1. Feed Universe
    feed_data = generate_feed_universe()
    feed_path = out_dir / f"{epoch}.feed-universe.json"
    feed_bytes = json.dumps(feed_data, indent=2).encode("utf-8") + b"\n"
    feed_path.write_bytes(feed_bytes)
    feed_sha256 = canonical_sha256(feed_data)

    # 2. Timing Contract
    timing_data = generate_timing_contract()
    timing_path = out_dir / f"{epoch}.timing-contract.json"
    timing_bytes = json.dumps(timing_data, indent=2).encode("utf-8") + b"\n"
    timing_path.write_bytes(timing_bytes)
    timing_sha256 = canonical_sha256(timing_data)

    # 3. Heartbeat Contract
    heartbeat_data = generate_heartbeat_contract()
    heartbeat_path = out_dir / f"{epoch}.heartbeat-contract.json"
    heartbeat_bytes = json.dumps(heartbeat_data, indent=2).encode("utf-8") + b"\n"
    heartbeat_path.write_bytes(heartbeat_bytes)
    heartbeat_sha256 = canonical_sha256(heartbeat_data)

    # 4. Runtime Config
    runtime_data = generate_runtime_config(commit, epoch)
    runtime_path = out_dir / f"{epoch}.runtime.json"
    runtime_bytes = json.dumps(runtime_data, indent=2).encode("utf-8") + b"\n"
    runtime_path.write_bytes(runtime_bytes)
    runtime_sha256 = file_sha256(runtime_path)
    seal_fingerprint = canonical_config_fingerprint(runtime_data)
    collector_fingerprint = collector_config_fingerprint(runtime_data)
    if seal_fingerprint != collector_fingerprint:
        raise ValueError(
            f"Runtime config fingerprint mismatch! Seal generator: {seal_fingerprint}, "
            f"Collector: {collector_fingerprint}"
        )
    runtime_fingerprint = seal_fingerprint

    # 5. Launch Command
    launch_cmd_data = generate_launch_command(commit, epoch, run_id, runtime_fingerprint)
    launch_cmd_path = out_dir / f"{epoch}.launch-command.json"
    launch_cmd_bytes = json.dumps(launch_cmd_data, indent=2).encode("utf-8") + b"\n"
    launch_cmd_path.write_bytes(launch_cmd_bytes)
    launch_cmd_sha256 = file_sha256(launch_cmd_path)

    # 6. Launch Wrapper Script
    wrapper_text = generate_launch_wrapper(epoch, run_id)
    wrapper_path = out_dir / f"{epoch}.launch-wrapper.sh"
    wrapper_path.write_text(wrapper_text, encoding="utf-8")
    wrapper_path.chmod(wrapper_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    wrapper_sha256 = file_sha256(wrapper_path)

    # 7. Launch Provenance
    provenance_data = generate_launch_provenance(
        commit=commit,
        runtime_git_tree=git_tree,
        epoch=epoch,
        run_id=run_id,
        runtime_seal_sha256=runtime_sha256,
        runtime_fingerprint=runtime_fingerprint,
        launch_command_sha256=launch_cmd_sha256,
        launch_wrapper_sha256=wrapper_sha256,
        feed_universe_sha256=feed_sha256,
        timing_contract_sha256=timing_sha256,
        heartbeat_contract_sha256=heartbeat_sha256,
    )
    provenance_path = out_dir / f"{epoch}.launch-provenance.json"
    provenance_bytes = json.dumps(provenance_data, indent=2).encode("utf-8") + b"\n"
    provenance_path.write_bytes(provenance_bytes)
    provenance_sha256 = file_sha256(provenance_path)

    # 8. Authorization Evidence
    auth_data = generate_authorization_evidence(epoch, run_id, commit)
    auth_path = out_dir / f"{epoch}.authorization-evidence.json"
    auth_bytes = json.dumps(auth_data, indent=2).encode("utf-8") + b"\n"
    auth_path.write_bytes(auth_bytes)
    auth_sha256 = file_sha256(auth_path)

    print("=== OFFICIAL V3 30H LAUNCH SEALS GENERATED ===")
    print(f"Collector Epoch: {epoch}")
    print(f"Collector Run ID: {run_id}")
    print(f"Runtime Software Commit: {commit}")
    print(f"Runtime Git Tree: {git_tree}")
    print(f"Runtime Config Fingerprint: {runtime_fingerprint}")
    print(f"Feed Universe Hash: {feed_sha256}")
    print(f"Timing Contract Hash: {timing_sha256}")
    print(f"Heartbeat Contract Hash: {heartbeat_sha256}")
    print(f"Runtime Seal SHA-256: {runtime_sha256}")
    print(f"Launch Command SHA-256: {launch_cmd_sha256}")
    print(f"Launch Wrapper SHA-256: {wrapper_sha256}")
    print(f"Launch Provenance SHA-256: {provenance_sha256}")
    print(f"Authorization Evidence SHA-256: {auth_sha256}")

    # Dry-run validation of systemd render
    cmd_list = launch_cmd_data.get("supervisor_command")
    if not isinstance(cmd_list, list):
        raise ValueError("supervisor_command must be a list")
    transient_config = TransientLaunchConfig(
        run_id=run_id,
        workdir=Path(f"/var/lib/bitcoin-trader/runtime-worktrees/{epoch}"),
        supervisor_command=tuple(str(x) for x in cmd_list),
        collection_duration_seconds=None,
        maximum_collection_window_seconds=111600,
        finalization_timeout_seconds=180,
        supervisor_hard_ceiling_seconds=111825,
        systemd_runtime_max_seconds=111900,
    )
    rendered_systemd = render_systemd_run(transient_config)
    print(f"\nRendered Systemd Unit: {rendered_systemd[1]}")
    print("Rendered Systemd Command:")
    print(" ".join(rendered_systemd[:8]) + " ...")
    print("\n[PASS] All 8 authoritative seals generated and validated successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
