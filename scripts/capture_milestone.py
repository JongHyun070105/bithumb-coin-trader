#!/usr/bin/env python3
"""Capture read-only milestone evidence for the active 90m V3B soak run."""
import base64
import json
import sys
from datetime import datetime, timezone

from ssm_exec import run_single

EPOCH = "aws-validation-observability-90m-20260917-20260917T062500Z-v3"
RUN_ID = "aws-validation-observability-90m-run-20260917T062500Z-v3"
UNIT = f"bitcoin-trader-90m-{RUN_ID}.service"
OBSERVER_UNIT = "bitcoin-trader-90m-observer-v3.service"
BASE_DIR = f"/var/lib/bitcoin-trader/90m-validation/{EPOCH}"

REMOTE_PROBE = f"""
sudo -u bitcoin-trader /var/lib/bitcoin-trader/venv-pre-soak/bin/python -c '
import json, os, subprocess, sys, base64
from pathlib import Path

data_dir = Path("{BASE_DIR}")
out = {{}}

def get_props(unit):
    res = subprocess.run(["systemctl", "show", unit, "--property=ActiveState,SubState,MainPID,ExecMainStartTimestamp,Result,Type,NotifyAccess,WatchdogUSec,WatchdogTimestamp,WatchdogTimestampMonotonic,NRestarts"], capture_output=True, text=True)
    p = {{}}
    for line in res.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            p[k.strip()] = v.strip()
    return p

out["systemd_collector"] = get_props("{UNIT}")
out["systemd_observer"] = get_props("{OBSERVER_UNIT}")

cm_path = data_dir / "collector_metrics.json"
if cm_path.exists():
    try:
        out["collector_metrics"] = json.loads(cm_path.read_text())
    except Exception as e:
        out["collector_metrics"] = str(e)

cl_path = data_dir / "collector-lifecycle.json"
if cl_path.exists():
    try:
        out["collector_lifecycle"] = json.loads(cl_path.read_text())
    except Exception as e:
        out["collector_lifecycle"] = str(e)

obs_path = data_dir / "health" / "observer_latest.json"
if obs_path.exists():
    try:
        out["observer_latest"] = json.loads(obs_path.read_text())
    except Exception as e:
        out["observer_latest"] = str(e)

raw_dir = data_dir / "raw"
if raw_dir.exists():
    files = list(raw_dir.glob("**/*.jsonl"))
    out["raw_feed_count"] = len(files)
    cohorts = set()
    for f in files:
        cohorts.add(f.stem.split("_")[-2] + "_" + f.stem.split("_")[-1] if "_" in f.stem else "unknown")
    out["raw_cohorts"] = sorted(list(cohorts))
else:
    out["raw_feed_count"] = 0

receipts_dir = data_dir / "archive-receipts"
if receipts_dir.exists():
    out["archive_receipts"] = [f.name for f in receipts_dir.glob("*.json")]
else:
    out["archive_receipts"] = []

comp_dir = data_dir / "compressed"
if comp_dir.exists():
    out["compressed_files"] = [f.name for f in comp_dir.glob("**/*") if f.is_file()]
else:
    out["compressed_files"] = []

st = os.statvfs(str(data_dir))
out["disk_free_bytes"] = st.f_bavail * st.f_frsize
out["disk_total_bytes"] = st.f_blocks * st.f_frsize

dumped = json.dumps(out).encode("utf-8")
b64 = base64.b64encode(dumped).decode("ascii")
print("__B64_START__" + b64 + "__B64_END__")
'
"""

def capture(milestone_name: str) -> dict:
    now_utc = datetime.now(timezone.utc).isoformat()
    raw_output = run_single(REMOTE_PROBE.strip())
    
    data = {"milestone": milestone_name, "captured_at_utc": now_utc}
    if "__B64_START__" in raw_output and "__B64_END__" in raw_output:
        s = raw_output.rfind("__B64_START__") + len("__B64_START__")
        e = raw_output.rfind("__B64_END__")
        b64_str = "".join(raw_output[s:e].split())
        data["evidence"] = json.loads(base64.b64decode(b64_str.encode("ascii")).decode("utf-8"))
    else:
        data["raw_output"] = raw_output
        
    return data

if __name__ == "__main__":
    from pathlib import Path
    name = sys.argv[1] if len(sys.argv) > 1 else "TEST_PROBE"
    res = capture(name)
    out_path = Path("reliability-artifacts/aws-90m-v3/milestones") / f"{name}.json"
    out_path.write_text(json.dumps(res, indent=2))
    print(f"Saved to {out_path}")
    print(json.dumps(res, indent=2))
    sys.exit(0)

    name = sys.argv[1] if len(sys.argv) > 1 else "TEST_PROBE"
    res = capture(name)
    print(json.dumps(res, indent=2))
