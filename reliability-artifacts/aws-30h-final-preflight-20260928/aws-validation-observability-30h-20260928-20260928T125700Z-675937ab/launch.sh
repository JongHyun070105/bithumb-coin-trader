#!/usr/bin/env bash
set -euo pipefail

worktree="/var/lib/bitcoin-trader/runtime-worktrees/aws-validation-observability-30h-20260928-20260928T125700Z-675937ab"
python="/var/lib/bitcoin-trader/venv-pre-soak/bin/python"

sup_cmd_json=$("$python" -c '
import json
with open("launch-command.json") as f:
    d = json.load(f)
print(json.dumps(d["supervisor_command"]))
')

export PYTHONPATH="$worktree/src"
exec "$python" "$worktree/scripts/launch_short_smoke_transient.py" \
  "$@" \
  --run-id "aws-validation-observability-30h-run-20260928T125700Z-675937ab" \
  --workdir "$worktree" \
  --supervisor-command-json "$sup_cmd_json" \
  --collection-duration-seconds 108000 \
  --finalization-timeout-seconds 180 \
  --supervisor-hard-ceiling-seconds 108180 \
  --systemd-runtime-max-seconds 108240 \
  --exec-stop-post-python "$python" \
  --exec-stop-post-script "$worktree/scripts/terminal_witness.py" \
  --data-dir "/var/lib/bitcoin-trader/30h-validation/aws-validation-observability-30h-20260928-20260928T125700Z-675937ab" \
  --exec-stop-post-epoch "aws-validation-observability-30h-20260928-20260928T125700Z-675937ab" \
  --exec-stop-post-s3-bucket "bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433" \
  --exec-stop-post-s3-prefix "market-data/temporary/aws-validation-observability-30h-20260928-20260928T125700Z-675937ab" \
  --exec-stop-post-s3-region "ap-northeast-2" \
  --exec-stop-post-allow-s3-write \
