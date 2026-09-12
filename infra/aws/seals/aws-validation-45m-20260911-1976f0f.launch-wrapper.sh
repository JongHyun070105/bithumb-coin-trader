#!/usr/bin/env bash
set -euo pipefail

worktree="/var/lib/bitcoin-trader/runtime-worktrees/aws-validation-45m-20260911-1976f0f"
artifacts="/var/lib/bitcoin-trader/launch-artifacts/aws-validation-45m-20260911-1976f0f"
python="/var/lib/bitcoin-trader/venv-pre-soak/bin/python"

sup_cmd_json=$(/var/lib/bitcoin-trader/venv-pre-soak/bin/python -c '
import json
with open("/var/lib/bitcoin-trader/launch-artifacts/aws-validation-45m-20260911-1976f0f/aws-validation-45m-20260911-1976f0f.launch-command.json") as f:
    d = json.load(f)
print(json.dumps(d["supervisor_command"]))
')

export PYTHONPATH="$worktree/src"
exec "$python" "$worktree/scripts/launch_short_smoke_transient.py" \
  --run-id "aws-validation-45m-run-20260911T104000Z-1976f0f" \
  --workdir "$worktree" \
  --supervisor-command-json "$sup_cmd_json" \
  --finalization-timeout-seconds 120 \
  --supervisor-hard-ceiling-seconds 2820 \
  --systemd-runtime-max-seconds 2880 \
  "$@"
