#!/usr/bin/env bash
set -euo pipefail

EXPECTED_SELF_SHA="${1:-}"
EPOCH="aws-validation-observability-6h-20260922-20260922T035000Z-v4r1"
RUN_ID="aws-validation-observability-6h-run-20260922T035000Z-v4r1"
RUNTIME="/var/lib/bitcoin-trader/runtime-worktrees/aws-6h-v4-525a7d3"
ARTIFACTS="/var/lib/bitcoin-trader/launch-artifacts/${EPOCH}"
ATTESTATION="${ARTIFACTS}/prearm-s3-empty-attestation.json"
DATA_ROOT="/var/lib/bitcoin-trader/6h-validation/${EPOCH}"
AUDIT_ROOT="/var/lib/bitcoin-trader/run-audits/${EPOCH}"
MARKER_DIR="${AUDIT_ROOT}/launch-command-submitted"
RESULT_PATH="${AUDIT_ROOT}/prelaunch-result.json"
EXPECTED_COMMIT="525a7d339260481e63e36f1a3948ce08eb15d9be"
EXPECTED_TREE="08c89c63e0a5de8e94450a2715ea19664211b091"
EXPECTED_INSTANCE="i-008bc503c1136349f"
EXPECTED_ACCOUNT="080109295433"
BUCKET="bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433"
PREFIX="market-data/temporary/${EPOCH}"
EXPECTED_ATTESTATION_SHA="3ab449c823ab440f013a8ce459a1978d23fcd6b95738ea4f902d12a7c6bd91f1"
TIMER_BASE="bitcoin-trader-launch-6h-v4r1-20260922T035000Z"
T0_EPOCH=1790049000
DEADLINE_EPOCH=1790049300

write_result() {
  local status="$1"
  local reason="$2"
  local launch_submitted="$3"
  STATUS="$status" REASON="$reason" LAUNCH_SUBMITTED="$launch_submitted" \
    EPOCH="$EPOCH" RUN_ID="$RUN_ID" RESULT_PATH="$RESULT_PATH" \
    /usr/bin/python3 - <<'PY'
import json, os, pathlib, tempfile
from datetime import datetime, timezone

path = pathlib.Path(os.environ["RESULT_PATH"])
path.parent.mkdir(parents=True, exist_ok=True)
payload = {
    "schema_version": 1,
    "observed_at_utc": datetime.now(timezone.utc).isoformat(),
    "epoch": os.environ["EPOCH"],
    "run_id": os.environ["RUN_ID"],
    "status": os.environ["STATUS"],
    "reason": os.environ["REASON"],
    "launch_submitted": os.environ["LAUNCH_SUBMITTED"].lower() == "true",
}
fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2, sort_keys=True)
    fh.write("\n")
    fh.flush()
    os.fsync(fh.fileno())
os.replace(tmp, path)
PY
}

fail_closed() {
  local reason="$1"
  echo "[FAIL-CLOSED] $reason" >&2
  write_result "FAIL_CLOSED" "$reason" "false"
  exit 1
}

if [ "$(id -u)" -ne 0 ]; then
  fail_closed "guard_not_running_as_root"
fi

if [ -z "$EXPECTED_SELF_SHA" ]; then
  fail_closed "expected_guard_sha_missing"
fi
actual_self_sha=$(/usr/bin/sha256sum "$0" | /usr/bin/awk '{print $1}')
[ "$actual_self_sha" = "$EXPECTED_SELF_SHA" ] || fail_closed "guard_sha_mismatch"

now=$(/usr/bin/date -u +%s)
if [ "$now" -lt "$T0_EPOCH" ] || [ "$now" -gt "$DEADLINE_EPOCH" ]; then
  fail_closed "outside_sealed_freshness_window"
fi

[ ! -e "$DATA_ROOT" ] || fail_closed "new_data_root_already_exists"
[ ! -e "$MARKER_DIR" ] || fail_closed "launch_command_already_submitted"

actual_commit=$(/usr/bin/git -C "$RUNTIME" rev-parse HEAD 2>/dev/null) || fail_closed "runtime_commit_unreadable"
[ "$actual_commit" = "$EXPECTED_COMMIT" ] || fail_closed "runtime_commit_mismatch"
actual_tree=$(/usr/bin/git -C "$RUNTIME" rev-parse 'HEAD^{tree}' 2>/dev/null) || fail_closed "runtime_tree_unreadable"
[ "$actual_tree" = "$EXPECTED_TREE" ] || fail_closed "runtime_tree_mismatch"
[ -z "$(/usr/bin/git -C "$RUNTIME" status --porcelain --untracked-files=normal 2>/dev/null)" ] || fail_closed "runtime_worktree_dirty"

ARTIFACTS="$ARTIFACTS" EPOCH="$EPOCH" RUN_ID="$RUN_ID" EXPECTED_COMMIT="$EXPECTED_COMMIT" EXPECTED_TREE="$EXPECTED_TREE" \
  /usr/bin/python3 - <<'PY' || fail_closed "sealed_artifact_validation_failed"
import hashlib, json, os, pathlib

root = pathlib.Path(os.environ["ARTIFACTS"])
manifest = json.loads((root / "sealed-manifest.json").read_text(encoding="utf-8"))
identity_bytes = (root / "identity.json").read_bytes()
if hashlib.sha256(identity_bytes).hexdigest() != manifest["identity_sha256"]:
    raise SystemExit("identity hash mismatch")
identity = json.loads(identity_bytes)
expected = {
    "epoch": os.environ["EPOCH"],
    "run_id": os.environ["RUN_ID"],
    "software_commit_sha": os.environ["EXPECTED_COMMIT"],
    "software_tree_sha": os.environ["EXPECTED_TREE"],
    "planned_start_utc": "2026-09-22T03:50:00+00:00",
    "qualification_start_utc": "2026-09-22T04:00:00+00:00",
}
for key, value in expected.items():
    if identity.get(key) != value:
        raise SystemExit(f"identity {key} mismatch")
for name, expected_hash in manifest["artifact_hashes"].items():
    actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
    if actual != expected_hash:
        raise SystemExit(f"artifact hash mismatch: {name}")
PY

token=$(/usr/bin/curl -fsS --max-time 3 -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' http://169.254.169.254/latest/api/token) || fail_closed "imds_token_failed"
instance=$(/usr/bin/curl -fsS --max-time 3 -H "X-aws-ec2-metadata-token: $token" http://169.254.169.254/latest/meta-data/instance-id) || fail_closed "instance_identity_unreadable"
[ "$instance" = "$EXPECTED_INSTANCE" ] || fail_closed "instance_identity_mismatch"

account=$(/usr/local/bin/aws sts get-caller-identity --query Account --output text 2>/dev/null || /usr/bin/aws sts get-caller-identity --query Account --output text 2>/dev/null) || fail_closed "aws_account_unreadable"
[ "$account" = "$EXPECTED_ACCOUNT" ] || fail_closed "aws_account_mismatch"

ATTESTATION="$ATTESTATION" EXPECTED_ATTESTATION_SHA="$EXPECTED_ATTESTATION_SHA" \
  EPOCH="$EPOCH" RUN_ID="$RUN_ID" EXPECTED_ACCOUNT="$EXPECTED_ACCOUNT" BUCKET="$BUCKET" PREFIX="$PREFIX" \
  /usr/bin/python3 - <<'PY' || fail_closed "prearm_s3_empty_attestation_invalid"
import hashlib, json, os, pathlib

path = pathlib.Path(os.environ["ATTESTATION"])
raw = path.read_bytes()
if hashlib.sha256(raw).hexdigest() != os.environ["EXPECTED_ATTESTATION_SHA"]:
    raise SystemExit("attestation byte hash mismatch")
payload = json.loads(raw)
expected = {
    "schema_version": 1,
    "aws_account_id": os.environ["EXPECTED_ACCOUNT"],
    "region": "ap-northeast-2",
    "bucket": os.environ["BUCKET"],
    "prefix": os.environ["PREFIX"] + "/",
    "object_count": 0,
    "epoch": os.environ["EPOCH"],
    "run_id": os.environ["RUN_ID"],
    "s3_emptiness_guard_mode": "PROVISIONER_PREARM_ATTESTATION",
    "prearm_s3_empty_attestation": "PASS",
    "t0_s3_empty_directly_verified": False,
    "residual_toctou_risk": "ACKNOWLEDGED",
}
for key, value in expected.items():
    if payload.get(key) != value:
        raise SystemExit(f"attestation {key} mismatch")
arn = payload.get("provisioner_identity_arn", "")
if ":assumed-role/bitcoin-trader-terraform-provisioner/" not in arn:
    raise SystemExit("unexpected provisioner role")
if not payload.get("observed_at_utc"):
    raise SystemExit("attestation observed_at_utc missing")
PY

if /usr/bin/pgrep -af 'run_cross_market_collector.py|run_bounded_short_smoke.py|run_closed_hour_archive_scheduler.py' >/tmp/${TIMER_BASE}.conflicts 2>/dev/null; then
  fail_closed "conflicting_collector_supervisor_or_archive_scheduler"
fi

other_timers=$(/usr/bin/systemctl list-timers --all --no-legend --no-pager 2>/dev/null | /usr/bin/grep 'bitcoin-trader-launch-' | /usr/bin/grep -v "$TIMER_BASE" || true)
[ -z "$other_timers" ] || fail_closed "conflicting_launch_timer"

for unit in "bitcoin-trader-6h-${RUN_ID}.service" "bitcoin-trader-obs-${RUN_ID}.service"; do
  if /usr/bin/systemctl is-active --quiet "$unit" 2>/dev/null; then
    fail_closed "run_specific_unit_already_active"
  fi
done

now=$(/usr/bin/date -u +%s)
if [ "$now" -lt "$T0_EPOCH" ] || [ "$now" -gt "$DEADLINE_EPOCH" ]; then
  fail_closed "freshness_window_expired_during_checks"
fi

write_result "PRELAUNCH_PASS" "all_execution_time_checks_passed" "false"
/usr/bin/mkdir -p "$AUDIT_ROOT"
if ! /usr/bin/mkdir "$MARKER_DIR" 2>/dev/null; then
  fail_closed "launch_submission_marker_race"
fi
/usr/bin/date -u '+%Y-%m-%dT%H:%M:%SZ' >"${MARKER_DIR}/submitted_at_utc.txt"
printf '%s\n' "$EXPECTED_SELF_SHA" >"${MARKER_DIR}/guard_sha256.txt"

echo "[LAUNCH] All execution-time checks passed; submitting launch #11 exactly once."
set +e
"${ARTIFACTS}/launch-ec2.sh" --launch
rc=$?
set -e
if [ "$rc" -eq 0 ]; then
  write_result "LAUNCH_COMMAND_COMPLETED" "launch_ec2_returned_zero" "true"
else
  write_result "LAUNCH_COMMAND_FAILED" "launch_ec2_returned_${rc}" "true"
fi
exit "$rc"
