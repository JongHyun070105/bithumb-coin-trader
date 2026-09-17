#!/usr/bin/env python3
"""Execute commands on EC2 via SSM interactive session."""
import re
import subprocess
import sys
import time

INSTANCE = "i-008bc503c1136349f"
REGION = "ap-northeast-2"
PROFILE = "bitcoin-trader-bootstrap"

def strip_ansi(text: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\[\?[0-9]*[a-z]', '', text)

def run_commands(commands: list[str]) -> list[str]:
    proc = subprocess.Popen(
        ["aws", "ssm", "start-session",
         "--target", INSTANCE, "--region", REGION, "--profile", PROFILE],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    time.sleep(3)

    for i, cmd in enumerate(commands):
        marker = f"__M{i}__"
        proc.stdin.write(f"echo {marker}\n".encode()); proc.stdin.flush(); time.sleep(1)
        proc.stdin.write(f"{cmd}\n".encode()); proc.stdin.flush(); time.sleep(1.5)
        proc.stdin.write(f"echo {marker}END\n".encode()); proc.stdin.flush(); time.sleep(1)

    proc.stdin.write(b"exit\n"); proc.stdin.flush()
    try:
        stdout, _ = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill(); stdout, _ = proc.communicate()

    clean = strip_ansi(stdout.decode(errors="replace"))
    results = []
    for i in range(len(commands)):
        start = f"__M{i}__"
        end = f"__M{i}__END"
        s = clean.find(start)
        e = clean.find(end)
        if s >= 0 and e > s:
            chunk = clean[s + len(start):e]
            # Remove marker echo line and trailing prompt+end-marker echo
            lines = chunk.splitlines()
            # Filter out shell prompts and marker echo lines
            filtered = []
            for l in lines:
                stripped = l.strip()
                if stripped.startswith("sh-") and stripped.endswith("$"):
                    continue
                if stripped.startswith("echo __M"):
                    continue
                if stripped == start:
                    continue
                filtered.append(l)
            results.append("\n".join(filtered).strip())
        else:
            results.append("[PARSE_FAILED]")
    return results

def run_single(command: str) -> str:
    return run_commands([command])[0]

if __name__ == "__main__":
    cmds = sys.argv[1:]
    if not cmds:
        print("Usage: ssm_exec.py <command> [command2 ...]"); sys.exit(1)
    for cmd, out in zip(cmds, run_commands(cmds)):
        print(f"=== {cmd} ===")
        print(out)
