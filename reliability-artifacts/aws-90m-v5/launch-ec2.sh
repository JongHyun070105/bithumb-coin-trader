#!/usr/bin/env bash
set -euo pipefail

worktree="/var/lib/bitcoin-trader/runtime-worktrees/aws-observability-90m-20260917"
python="/var/lib/bitcoin-trader/venv-pre-soak/bin/python"
artifacts_dir="/var/lib/bitcoin-trader/launch-artifacts/aws-validation-observability-90m-20260917-20260917T120000Z-v5"

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
  obs_unit="bitcoin-trader-obs-aws-validation-observability-90m-run-20260917T120000Z-v5.service"
  echo "[LAUNCH] Pre-starting runtime observer unit $obs_unit to guarantee OBSERVER_START <= COLLECTOR_START..."
  systemd-run \
    --unit="$obs_unit" \
    --description="Runtime Observer for aws-validation-observability-90m-run-20260917T120000Z-v5" \
    --service-type=simple \
    --no-block \
    --property="Environment=PYTHONPATH=$worktree/src" \
    "$python" -m bithumb_coin_trader.runtime_observer \
      --data-dir "/var/lib/bitcoin-trader/90m-validation/aws-validation-observability-90m-20260917-20260917T120000Z-v5" \
      --epoch "aws-validation-observability-90m-20260917-20260917T120000Z-v5" \
      --run-id "aws-validation-observability-90m-run-20260917T120000Z-v5" \
      --unit-name "bitcoin-trader-90m-aws-validation-observability-90m-run-20260917T120000Z-v5.service" \
      --poll-interval 15.0 \
      --s3-publish-interval 60.0 \
      --stale-threshold 30.0 \
      --s3-bucket "bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433" \
      --s3-prefix "market-data/temporary/aws-validation-observability-90m-20260917-20260917T120000Z-v5" \
      --allow-s3-write

  for i in $(seq 1 10); do
    if systemctl is-active --quiet "$obs_unit" 2>/dev/null; then
      echo "[LAUNCH] Observer is ACTIVE before collector launch (T0 verified)."
      break
    fi
    sleep 0.5
  done
fi

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
