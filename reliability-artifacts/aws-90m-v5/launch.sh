#!/usr/bin/env bash
set -euo pipefail

worktree="/var/lib/bitcoin-trader/runtime-worktrees/aws-observability-90m-20260917"
python="/var/lib/bitcoin-trader/venv-pre-soak/bin/python"

sup_cmd_json=$("$python" -c '
import json
with open("launch-command.json") as f:
    d = json.load(f)
print(json.dumps(d["supervisor_command"]))
')

export PYTHONPATH="$worktree/src"
exec "$python" "$worktree/scripts/launch_short_smoke_transient.py" \
  --run-id "aws-validation-observability-90m-run-20260917T120000Z-v5" \
  --workdir "$worktree" \
  --supervisor-command-json "$sup_cmd_json" \
  --collection-duration-seconds 5400 \
  --finalization-timeout-seconds 180 \
  --supervisor-hard-ceiling-seconds 5580 \
  --systemd-runtime-max-seconds 5640 \
  --exec-stop-post-script "$worktree/scripts/terminal_witness.py" \
  --data-dir "/var/lib/bitcoin-trader/90m-validation/aws-validation-observability-90m-20260917-20260917T120000Z-v5" \
  "$@"
