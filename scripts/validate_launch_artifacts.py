#!/usr/bin/env python3
"""CLI for validating launch artifacts before launch.

Loads runtime.json and launch-command.json from a target directory,
verifies duration consistency, placeholder format, path binding, fingerprint,
and executes non-mutating dry-run validation using the production collector parser.

Usage:
  python3 scripts/validate_launch_artifacts.py --artifacts-dir <dir>
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
    ValidationRunSpec,
    validate_launch_artifacts,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Authoritative Launch Artifact Validator")
    parser.add_argument("--artifacts-dir", type=Path, required=True, help="Directory containing launch artifacts")
    args = parser.parse_args(argv)

    artifacts_dir = args.artifacts_dir
    identity_file = artifacts_dir / "identity.json"
    launch_cmd_file = artifacts_dir / "launch-command.json"

    if not identity_file.exists():
        print(f"ERROR: identity.json not found in {artifacts_dir}", file=sys.stderr)
        return 1
    if not launch_cmd_file.exists():
        print(f"ERROR: launch-command.json not found in {artifacts_dir}", file=sys.stderr)
        return 1

    identity = json.loads(identity_file.read_text(encoding="utf-8"))
    launch_command = json.loads(launch_cmd_file.read_text(encoding="utf-8"))

    epoch = identity["epoch"]
    runtime_file = artifacts_dir / f"{epoch}.runtime.json"
    if not runtime_file.exists():
        print(f"ERROR: {epoch}.runtime.json not found in {artifacts_dir}", file=sys.stderr)
        return 1

    runtime_config = json.loads(runtime_file.read_text(encoding="utf-8"))

    spec = ValidationRunSpec(
        epoch=epoch,
        run_id=identity["run_id"],
        duration_seconds=identity["duration_seconds"],
        runtime_commit=identity["software_commit_sha"],
        software_tree_sha=identity.get("software_tree_sha"),
        base_data_parent=Path(identity.get("data_root", "/var/lib/bitcoin-trader/90m-validation")).parent,
        runtime_worktree=Path(identity.get("runtime_worktree", "/var/lib/bitcoin-trader/runtime-worktrees/aws-observability-90m-20260917")),
        python_bin=Path(identity.get("python", "/var/lib/bitcoin-trader/venv-pre-soak/bin/python")),
        s3_bucket=identity.get("s3_bucket", "bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433"),
    )

    try:
        res = validate_launch_artifacts(
            spec=spec,
            runtime_config=runtime_config,
            launch_command=launch_command,
            target_dir=artifacts_dir,
        )
        print("=== PRELAUNCH ARTIFACT VALIDATION: PASS ===")
        print(json.dumps(res, indent=2))
        return 0
    except Exception as exc:
        print(f"=== PRELAUNCH ARTIFACT VALIDATION: FAIL ===", file=sys.stderr)
        print(f"Reason: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
