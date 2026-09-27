#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "[ERROR] launch-ec2.sh must be executed with root/sudo privileges so systemd-run can register units. (Observer/collector units are strictly pinned to --uid=bitcoin-trader)." >&2
  exit 1
fi

worktree="/var/lib/bitcoin-trader/runtime-worktrees/aws-observability-90m-20260917"
python="/var/lib/bitcoin-trader/venv-pre-soak/bin/python"
artifacts_dir="/var/lib/bitcoin-trader/launch-artifacts/aws-validation-observability-90m-20260918-20260918T005000Z-v12"

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
  # Step A: Wait until the sealed planned collector-start window is reached WITHOUT starting observer or collector.
  planned_start="2026-09-18T00:50:00+00:00"
  qual_start="2026-09-18T01:00:00+00:00"
  if [ -n "$planned_start" ] && [ -n "$qual_start" ]; then
    echo "[LAUNCH] Step A: Enforcing planned start window arrival before observer launch (FAIL-CLOSED)..."
    if ! "$python" -m bithumb_coin_trader.launch_freshness \
        --planned-start "$planned_start" \
        --qualification-start "$qual_start" \
        --max-delay 60.0; then
      echo "[LAUNCH] CRITICAL: Launch schedule freshness violation before observer start. ABORT." >&2
      exit 1
    fi
  fi

  # Step B: Start observer near planned collector start, pinned to user: bitcoin-trader.
  obs_unit="bitcoin-trader-obs-aws-validation-observability-90m-run-20260918T005000Z-v12.service"
  echo "[LAUNCH] Step B: Starting runtime observer unit $obs_unit (pinned to user: bitcoin-trader)..."
  systemd-run \
    --unit="$obs_unit" \
    --description="Runtime Observer for aws-validation-observability-90m-run-20260918T005000Z-v12" \
    --service-type=simple \
    --no-block \
    --uid=bitcoin-trader \
    --property="Environment=PYTHONPATH=$worktree/src" \
    "$python" -m bithumb_coin_trader.runtime_observer \
      --data-dir "/var/lib/bitcoin-trader/90m-validation/aws-validation-observability-90m-20260918-20260918T005000Z-v12" \
      --epoch "aws-validation-observability-90m-20260918-20260918T005000Z-v12" \
      --run-id "aws-validation-observability-90m-run-20260918T005000Z-v12" \
      --unit-name "bitcoin-trader-90m-aws-validation-observability-90m-run-20260918T005000Z-v12.service" \
      --poll-interval 15.0 \
      --s3-publish-interval 60.0 \
      --stale-threshold 30.0 \
      --s3-bucket "bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433" \
      --s3-prefix "market-data/temporary/aws-validation-observability-90m-20260918-20260918T005000Z-v12" \
      --allow-s3-write

  for i in $(seq 1 10); do
    if systemctl is-active --quiet "$obs_unit" 2>/dev/null; then
      echo "[LAUNCH] Observer is ACTIVE (T0 verified)."
      break
    fi
    sleep 0.5
  done

  # Step C: Verify Observer T0 readiness (STRICT WAITING_FOR_COLLECTOR, live PID, bounded freshness, systemd active).
  echo "[LAUNCH] Step C: Verifying Observer T0 readiness immediately before collector launch (FAIL-CLOSED)..."
  if ! "$python" -m bithumb_coin_trader.observer_readiness \
      --health-dir "/var/lib/bitcoin-trader/90m-validation/aws-validation-observability-90m-20260918-20260918T005000Z-v12/health" \
      --epoch "aws-validation-observability-90m-20260918-20260918T005000Z-v12" \
      --run-id "aws-validation-observability-90m-run-20260918T005000Z-v12" \
      --unit-name "$obs_unit" \
      --max-age 30.0 \
      --timeout 30.0; then
    echo "[LAUNCH] CRITICAL: Observer T0 readiness verification failed. COLLECTOR WILL NOT START." >&2
    exit 1
  fi
  echo "[LAUNCH] Observer T0 readiness VERIFIED: OBSERVER_READY_TIME <= COLLECTOR_START_TIME."

  # Step D: Final launch freshness check immediately after readiness proof.
  if [ -n "$planned_start" ] && [ -n "$qual_start" ]; then
    echo "[LAUNCH] Step D: Final pre-collector freshness re-check (FAIL-CLOSED)..."
    if ! "$python" -m bithumb_coin_trader.launch_freshness \
        --planned-start "$planned_start" \
        --qualification-start "$qual_start" \
        --max-delay 60.0; then
      echo "[LAUNCH] CRITICAL: Launch schedule freshness violation immediately before collector exec. ABORT." >&2
      exit 1
    fi
  fi
  echo "[LAUNCH] All pre-collector gates passed cleanly. Launching collector now."
fi

exec "$python" "$worktree/scripts/launch_short_smoke_transient.py" \
  --run-id "aws-validation-observability-90m-run-20260918T005000Z-v12" \
  --workdir "$worktree" \
  --supervisor-command-json "$sup_cmd_json" \
  --collection-duration-seconds 5400 \
  --finalization-timeout-seconds 180 \
  --supervisor-hard-ceiling-seconds 5580 \
  --systemd-runtime-max-seconds 5640 \
  --exec-stop-post-script "$worktree/scripts/terminal_witness.py" \
  --data-dir "/var/lib/bitcoin-trader/90m-validation/aws-validation-observability-90m-20260918-20260918T005000Z-v12" \
  "$@"
