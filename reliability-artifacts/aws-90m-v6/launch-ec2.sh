#!/usr/bin/env bash
set -euo pipefail

worktree="/var/lib/bitcoin-trader/runtime-worktrees/aws-observability-90m-20260917"
python="/var/lib/bitcoin-trader/venv-pre-soak/bin/python"
artifacts_dir="/var/lib/bitcoin-trader/launch-artifacts/aws-validation-observability-90m-20260917-20260917T135000Z-v6"

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
  obs_unit="bitcoin-trader-obs-aws-validation-observability-90m-run-20260917T135000Z-v6.service"
  echo "[LAUNCH] Pre-starting runtime observer unit $obs_unit to guarantee OBSERVER_START <= COLLECTOR_START..."
  systemd-run \
    --unit="$obs_unit" \
    --description="Runtime Observer for aws-validation-observability-90m-run-20260917T135000Z-v6" \
    --service-type=simple \
    --no-block \
    --property="Environment=PYTHONPATH=$worktree/src" \
    "$python" -m bithumb_coin_trader.runtime_observer \
      --data-dir "/var/lib/bitcoin-trader/90m-validation/aws-validation-observability-90m-20260917-20260917T135000Z-v6" \
      --epoch "aws-validation-observability-90m-20260917-20260917T135000Z-v6" \
      --run-id "aws-validation-observability-90m-run-20260917T135000Z-v6" \
      --unit-name "bitcoin-trader-90m-aws-validation-observability-90m-run-20260917T135000Z-v6.service" \
      --poll-interval 15.0 \
      --s3-publish-interval 60.0 \
      --stale-threshold 30.0 \
      --s3-bucket "bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433" \
      --s3-prefix "market-data/temporary/aws-validation-observability-90m-20260917-20260917T135000Z-v6" \
      --allow-s3-write

  for i in $(seq 1 10); do
    if systemctl is-active --quiet "$obs_unit" 2>/dev/null; then
      echo "[LAUNCH] Observer is ACTIVE before collector launch (T0 verified)."
      break
    fi
    sleep 0.5
  done

  # Fail-closed Observer T0 Readiness Verification!
  echo "[LAUNCH] Verifying Observer T0 readiness (FAIL-CLOSED)..."
  if ! "$python" -m bithumb_coin_trader.observer_readiness \
      --health-dir "/var/lib/bitcoin-trader/90m-validation/aws-validation-observability-90m-20260917-20260917T135000Z-v6/health" \
      --epoch "aws-validation-observability-90m-20260917-20260917T135000Z-v6" \
      --run-id "aws-validation-observability-90m-run-20260917T135000Z-v6" \
      --timeout 30.0; then
    echo "[LAUNCH] CRITICAL: Observer T0 readiness verification failed. COLLECTOR WILL NOT START." >&2
    exit 1
  fi
  echo "[LAUNCH] Observer T0 readiness VERIFIED: OBSERVER_READY_TIME <= COLLECTOR_START_TIME."

  # Fail-closed Launch-Time Schedule Freshness Enforcement!
  planned_start="2026-09-17T13:50:00+00:00"
  qual_start="2026-09-17T14:00:00+00:00"
  if [ -n "$planned_start" ] && [ -n "$qual_start" ]; then
    echo "[LAUNCH] Enforcing launch freshness against sealed schedule (FAIL-CLOSED)..."
    if ! "$python" -m bithumb_coin_trader.launch_freshness \
        --planned-start "$planned_start" \
        --qualification-start "$qual_start" \
        --max-delay 60.0; then
      echo "[LAUNCH] CRITICAL: Launch schedule freshness violation. COLLECTOR WILL NOT START." >&2
      exit 1
    fi
  fi
fi

exec "$python" "$worktree/scripts/launch_short_smoke_transient.py" \
  --run-id "aws-validation-observability-90m-run-20260917T135000Z-v6" \
  --workdir "$worktree" \
  --supervisor-command-json "$sup_cmd_json" \
  --collection-duration-seconds 5400 \
  --finalization-timeout-seconds 180 \
  --supervisor-hard-ceiling-seconds 5580 \
  --systemd-runtime-max-seconds 5640 \
  --exec-stop-post-script "$worktree/scripts/terminal_witness.py" \
  --data-dir "/var/lib/bitcoin-trader/90m-validation/aws-validation-observability-90m-20260917-20260917T135000Z-v6" \
  "$@"
