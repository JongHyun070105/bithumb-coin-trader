#!/usr/bin/env bash
set -euo pipefail

worktree="/var/lib/bitcoin-trader/runtime-worktrees/aws-observability-90m-20260917"
python="/var/lib/bitcoin-trader/venv-pre-soak/bin/python"
artifacts_dir="/var/lib/bitcoin-trader/launch-artifacts/aws-observability-90m-20260917-20260917T043600Z-v1"

sup_cmd_json=$("$python" -c '
import json
import sys
with open("'"$artifacts_dir"'/launch-command.json") as f:
    d = json.load(f)
print(json.dumps(d["supervisor_command"]))
')

export PYTHONPATH="$worktree/src"
exec "$python" "$worktree/scripts/launch_short_smoke_transient.py" \
  --run-id "aws-observability-90m-run-20260917T043600Z-v1" \
  --workdir "$worktree" \
  --supervisor-command-json "$sup_cmd_json" \
  --collection-duration-seconds 5400 \
  --finalization-timeout-seconds 180 \
  --supervisor-hard-ceiling-seconds 5580 \
  --systemd-runtime-max-seconds 5640 \
  "$@"
