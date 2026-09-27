#!/usr/bin/env python3
"""CLI for generating authoritative launch artifacts for soak validations.

Usage:
  python3 scripts/generate_launch_artifacts.py \
    --epoch <epoch> \
    --run-id <run_id> \
    --duration <seconds> \
    --runtime-commit <sha> \
    --target-dir <dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
for d in (ROOT, SRC_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

from bithumb_coin_trader.launch_artifacts import (
    SUPPORTED_DURATIONS,
    ValidationRunSpec,
    generate_launch_artifacts,
    validate_launch_artifacts,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Authoritative Launch Artifact Generator")
    parser.add_argument("--epoch", required=True, help="Collector epoch name")
    parser.add_argument("--run-id", required=True, help="Collector run ID")
    parser.add_argument("--duration", type=int, required=True, choices=SUPPORTED_DURATIONS, help="Duration in seconds")
    parser.add_argument("--runtime-commit", required=True, help="Git commit SHA for runtime code")
    parser.add_argument("--software-tree-sha", help="Git tree SHA for runtime code")
    parser.add_argument("--target-dir", type=Path, required=True, help="Target directory for launch artifacts")
    parser.add_argument("--data-parent", type=Path, default=Path("/var/lib/bitcoin-trader/90m-validation"))
    parser.add_argument("--worktree", type=Path, default=Path("/var/lib/bitcoin-trader/runtime-worktrees/aws-observability-90m-20260917"))
    parser.add_argument("--artifacts-parent", type=Path, default=Path("/var/lib/bitcoin-trader/launch-artifacts"))
    parser.add_argument("--python-bin", type=Path, default=Path("/var/lib/bitcoin-trader/venv-pre-soak/bin/python"))
    parser.add_argument("--s3-bucket", default="bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433")
    parser.add_argument("--target-full-hours", type=int, default=1, help="Target qualifying full UTC hours (default: 1)")
    parser.add_argument("--planned-start-time", help="Planned start time in ISO format (e.g. 2026-09-17T21:00:00Z)")
    args = parser.parse_args(argv)

    spec = ValidationRunSpec(
        epoch=args.epoch,
        run_id=args.run_id,
        duration_seconds=args.duration,
        runtime_commit=args.runtime_commit,
        software_tree_sha=args.software_tree_sha,
        target_full_hours=args.target_full_hours,
        planned_start_time=args.planned_start_time,
        base_data_parent=args.data_parent,
        runtime_worktree=args.worktree,
        launch_artifacts_parent=args.artifacts_parent,
        python_bin=args.python_bin,
        s3_bucket=args.s3_bucket,
    )

    artifacts = generate_launch_artifacts(spec, target_dir=args.target_dir)

    # Self-validate generated artifacts
    validation = validate_launch_artifacts(
        spec=spec,
        runtime_config=artifacts.runtime_config,
        launch_command=artifacts.launch_command,
        target_dir=args.target_dir,
    )

    print(f"Successfully generated and self-validated launch artifacts in: {args.target_dir}")
    print(f"Epoch: {spec.epoch}")
    print(f"Run ID: {spec.run_id}")
    print(f"Duration: {spec.duration_seconds}s")
    print(f"Config Fingerprint: {artifacts.config_fingerprint}")
    print(f"Self-validation: {validation['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
