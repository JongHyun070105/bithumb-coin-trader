#!/usr/bin/env bash
set -euo pipefail

worktree="/var/lib/bitcoin-trader/runtime-worktrees/aws-validation-30h-20260912-00464e5"
artifacts="/var/lib/bitcoin-trader/launch-artifacts/aws-validation-30h-20260912-00464e5"
python="/var/lib/bitcoin-trader/venv-pre-soak/bin/python"

sup_cmd_json=$("/var/lib/bitcoin-trader/venv-pre-soak/bin/python" -c '
import json
with open("/var/lib/bitcoin-trader/launch-artifacts/aws-validation-30h-20260912-00464e5/aws-validation-30h-20260912-00464e5.launch-command.json") as f:
    d = json.load(f)
print(json.dumps(d["supervisor_command"]))
')

export PYTHONPATH="$worktree/src"
exec "$python" "$worktree/scripts/launch_short_smoke_transient.py" \
  --run-id "aws-validation-30h-run-20260912T091500Z-00464e5" \
  --workdir "$worktree" \
  --supervisor-command-json "$sup_cmd_json" \
  --collection-duration-seconds 108000 \
  --finalization-timeout-seconds 180 \
  --supervisor-hard-ceiling-seconds 108300 \
  --systemd-runtime-max-seconds 108400 \
  "$@"
