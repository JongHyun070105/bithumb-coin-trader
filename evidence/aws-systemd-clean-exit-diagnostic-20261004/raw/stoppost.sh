#!/bin/bash
# ExecStopPost snapshot: records systemd-provided environment and unit properties while the unit still exists
OUT=$1
{
 echo "date_utc=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)"
 echo "EXIT_CODE=${EXIT_CODE:-}"; echo "EXIT_STATUS=${EXIT_STATUS:-}"; echo "SERVICE_RESULT=${SERVICE_RESULT:-}"
 echo "INVOCATION_ID_ENV=${INVOCATION_ID:-}"
 echo "MAINPID_ENV=${MAINPID:-}"
} > $OUT.env
systemctl show "$2" > $OUT.show 2>&1
echo "stoppost-ran exit_code=${EXIT_CODE:-} exit_status=${EXIT_STATUS:-} service_result=${SERVICE_RESULT:-}"
