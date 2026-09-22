#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "arm-host-timer.sh must run as root" >&2
  exit 1
fi

EPOCH="aws-validation-observability-6h-20260922-20260922T035000Z-v4r1"
RUN_ID="aws-validation-observability-6h-run-20260922T035000Z-v4r1"
ARTIFACTS="/var/lib/bitcoin-trader/launch-artifacts/${EPOCH}"
AUDIT_ROOT="/var/lib/bitcoin-trader/run-audits/${EPOCH}"
DATA_ROOT="/var/lib/bitcoin-trader/6h-validation/${EPOCH}"
TIMER_BASE="bitcoin-trader-launch-6h-v4r1-20260922T035000Z"
OBSERVER_UNIT="bitcoin-trader-receipt-hash-6h-v4r1-20260922T035000Z"
GUARD_SHA="c2b27c49647629d7682a78e9491902b875fe13c4a9e87bb5c57ca1dd2d6b53f0"
OBSERVER_SHA="1a6a81e4acdeb5b892c986c7b94142085571a0974f891fc4131d266c6bdc2c8d"

ARTIFACTS="$ARTIFACTS" /usr/bin/python3 - <<'PY'
import hashlib, json, os, pathlib

root = pathlib.Path(os.environ["ARTIFACTS"])
seal = json.loads((root / "run-seal.json").read_text(encoding="utf-8"))
for name, expected in seal["artifact_hashes"].items():
    actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"run seal mismatch: {name}")
auth = json.loads((root / "authorization-evidence.json").read_text(encoding="utf-8"))
if auth.get("prearm_s3_empty_attestation") != "PASS":
    raise SystemExit("pre-arm S3 attestation not PASS")
PY

printf '%s  %s\n' "$GUARD_SHA" "${ARTIFACTS}/host-launch-guard.sh" | /usr/bin/sha256sum -c -
printf '%s  %s\n' "$OBSERVER_SHA" "${ARTIFACTS}/receipt-hash-observer.py" | /usr/bin/sha256sum -c -

if /usr/bin/systemctl status "${TIMER_BASE}.timer" >/dev/null 2>&1; then
  echo "Refusing to replace existing timer ${TIMER_BASE}.timer" >&2
  exit 1
fi
if /usr/bin/systemctl status "${TIMER_BASE}.service" >/dev/null 2>&1; then
  echo "Refusing to replace existing service ${TIMER_BASE}.service" >&2
  exit 1
fi
if /usr/bin/systemctl status "${OBSERVER_UNIT}.service" >/dev/null 2>&1; then
  echo "Refusing to replace existing observer ${OBSERVER_UNIT}.service" >&2
  exit 1
fi

/usr/bin/install -d -o bitcoin-trader -g bitcoin-trader -m 0750 "${AUDIT_ROOT}/receipt-hashes"

/usr/bin/systemd-run \
  --unit="$OBSERVER_UNIT" \
  --description="Read-only receipt T1/T2 SHA observer for $RUN_ID" \
  --service-type=simple \
  --uid=bitcoin-trader \
  --property=Restart=no \
  --property=RuntimeMaxSec=33000 \
  --property="Environment=PYTHONDONTWRITEBYTECODE=1" \
  /usr/bin/python3 "${ARTIFACTS}/receipt-hash-observer.py" \
    --data-root "$DATA_ROOT" \
    --audit-dir "${AUDIT_ROOT}/receipt-hashes" \
    --epoch "$EPOCH" \
    --run-id "$RUN_ID" \
    --cohort 2026-09-22_04 \
    --cohort 2026-09-22_05 \
    --cohort 2026-09-22_06 \
    --cohort 2026-09-22_07 \
    --cohort 2026-09-22_08 \
    --poll-seconds 15 \
    --t2-delay-seconds 600 \
    --deadline-utc 2026-09-22T10:20:00Z

/usr/bin/systemd-run \
  --unit="$TIMER_BASE" \
  --description="Exactly-once guarded Fresh 6H-v4r1 launch #11" \
  --on-calendar="2026-09-22 03:50:00 UTC" \
  --timer-property=AccuracySec=1s \
  --timer-property=RandomizedDelaySec=0 \
  --timer-property=Persistent=no \
  --property=Type=oneshot \
  --property=Restart=no \
  --property=TimeoutStartSec=600 \
  "${ARTIFACTS}/host-launch-guard.sh" "$GUARD_SHA"

/usr/bin/systemctl is-active --quiet "${TIMER_BASE}.timer"
/usr/bin/systemctl is-active --quiet "${OBSERVER_UNIT}.service"

echo "ARMED_TIMER=${TIMER_BASE}.timer"
echo "TARGET_SERVICE=${TIMER_BASE}.service"
echo "TRIGGER_UTC=2026-09-22T03:50:00Z"
echo "RECURRENCE=NONE"
echo "GUARD_SHA256=${GUARD_SHA}"
echo "OBSERVER_UNIT=${OBSERVER_UNIT}.service"
echo "OBSERVER_SHA256=${OBSERVER_SHA}"
