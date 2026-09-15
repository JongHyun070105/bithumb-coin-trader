#!/usr/bin/env bash
set -euo pipefail

worktree="/var/lib/bitcoin-trader/runtime-worktrees/aws-validation-30h-20260915-v3"
python="/var/lib/bitcoin-trader/venv-pre-soak/bin/python"

sup_cmd_json=$("$python" -c '
import json
with open("/var/lib/bitcoin-trader/launch-artifacts/aws-validation-30h-20260915-v3/aws-validation-30h-20260915-v3.launch-command.json") as f:
    d = json.load(f)
print(json.dumps(d["supervisor_command"]))
')

export PYTHONPATH="$worktree/src"
exec "$python" "$worktree/scripts/launch_short_smoke_transient.py" \
  --run-id "aws-validation-30h-run-20260915T013000Z-v3" \
  --workdir "$worktree" \
  --supervisor-command-json "$sup_cmd_json" \
  --required-qualifying-full-hours 30 \
  --maximum-collection-window-seconds 111600 \
  --qualification-schedule-path "/var/lib/bitcoin-trader/30h-validation/aws-validation-30h-20260915-v3/qualification_schedule.json" \
  --finalization-timeout-seconds 180 \
  --supervisor-hard-ceiling-seconds 111825 \
  --systemd-runtime-max-seconds 111900 \
  "$@"
