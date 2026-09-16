"""V4 Read-Only Health Monitor

Checks V4 EC2 instance state and S3 data presence.
Read-only: no modifications to V4.

Usage:
    .venv/bin/python scripts/monitor_v4.py
"""

from __future__ import annotations

import subprocess
import json
import sys
from datetime import datetime, timezone

PROFILE = "bitcoin-trader-provisioner"
REGION = "ap-northeast-2"
INSTANCE_ID = "i-008bc503c1136349f"
V4_S3_PREFIX = "market-data/temporary/aws-validation-30h-20260915-v4/"


def aws_cmd(args: list[str]) -> str:
    cmd = ["/opt/homebrew/bin/aws"] + args + ["--profile", PROFILE, "--region", REGION]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.stdout.strip()


def check_ec2():
    out = aws_cmd([
        "ec2", "describe-instances",
        "--instance-ids", INSTANCE_ID,
        "--query", "Reservations[0].Instances[0].{State:State.Name,LaunchTime:LaunchTime,PublicIp:PublicIpAddress}"
    ])
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"error": out}


def check_ssm():
    out = aws_cmd([
        "ssm", "describe-instance-information",
        "--filters", f"Key=InstanceIds,Values={INSTANCE_ID}",
        "--query", "InstanceInformationList[0].{Ping:PingStatus,Agent:AgentVersion}"
    ])
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"error": out}


def check_s3_coverage():
    out = aws_cmd([
        "s3", "ls", f"s3://bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433/{V4_S3_PREFIX}coverage/",
    ])
    hours = []
    for line in out.strip().split("\n"):
        if "PRE" in line:
            hours.append(line.strip().replace("PRE ", "").rstrip("/"))
    return hours


def check_s3_objects():
    out = aws_cmd([
        "s3", "ls", f"s3://bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433/{V4_S3_PREFIX}",
        "--recursive",
    ])
    count = 0
    total_bytes = 0
    for line in out.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 3:
            count += 1
            try:
                total_bytes += int(parts[2])
            except ValueError:
                pass
    return count, total_bytes


def main():
    now = datetime.now(timezone.utc).isoformat()
    print(f"V4 Health Check — {now}")
    print("=" * 50)

    ec2 = check_ec2()
    print(f"EC2 State: {ec2.get('State', 'UNKNOWN')}")
    print(f"Launch Time: {ec2.get('LaunchTime', 'UNKNOWN')}")
    print(f"Public IP: {ec2.get('PublicIp', 'UNKNOWN')}")

    ssm = check_ssm()
    print(f"SSM Ping: {ssm.get('Ping', 'UNKNOWN')}")
    print(f"SSM Agent: {ssm.get('Agent', 'UNKNOWN')}")

    cov_hours = check_s3_coverage()
    print(f"Coverage hours: {len(cov_hours)} — {cov_hours}")

    obj_count, total_bytes = check_s3_objects()
    print(f"S3 objects: {obj_count}")
    print(f"S3 bytes: {total_bytes:,} ({total_bytes/1024/1024:.1f} MB)")

    # Assessment
    state = ec2.get("State", "unknown")
    if state == "running":
        if len(cov_hours) == 0:
            print("\nSTATUS: RUNNING — no coverage data yet")
        elif obj_count == 0:
            print(f"\nSTATUS: RUNNING — {len(cov_hours)}h coverage, no raw data visible")
        else:
            print(f"\nSTATUS: RUNNING — {len(cov_hours)}h coverage, {obj_count} objects")
    elif state == "stopped":
        print("\nSTATUS: STOPPED — may need finalization audit")
    elif state == "terminated":
        print("\nSTATUS: TERMINATED — perform final evidence audit")
    else:
        print(f"\nSTATUS: {state}")

    print("\nFINAL VERDICT: NOT YET AVAILABLE (process must reach terminal state)")


if __name__ == "__main__":
    main()
