#!/usr/bin/env python3
"""Run a command on EC2 via SSM StartSession (default document)."""
import subprocess
import sys
import os
import time
import select

def run_remote(cmd: str, timeout: int = 20) -> str:
    proc = subprocess.Popen(
        [
            "aws", "ssm", "start-session",
            "--target", "i-008bc503c1136349f",
            "--region", "ap-northeast-2",
            "--profile", "bitcoin-trader-bootstrap",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Send command + exit
    input_data = (cmd + "\nexit\n").encode()
    try:
        stdout, stderr = proc.communicate(input=input_data, timeout=timeout)
        return stdout.decode(errors="replace")
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return "TIMEOUT"

if __name__ == "__main__":
    cmd = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "whoami"
    print(run_remote(cmd))
