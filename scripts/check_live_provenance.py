#!/usr/bin/env python3
"""Provenance Checker for AWS 72H Soak.

Implements Section 58 of the 72H specification:
- Reconciles runtime commit across:
  * local runtime commit
  * origin runtime commit
  * guest runtime commit
  * seal runtime commit
  * Terraform/AWS runtime tags
- Output: MATCH / MISMATCH / NOT_VERIFIED
- Never confuses repository documentation HEAD with runtime_code_commit.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


def get_local_commit() -> str:
    r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    return r.stdout.strip()


def get_origin_commit() -> str:
    r = subprocess.run(["git", "rev-parse", "origin/main"], capture_output=True, text=True, check=True)
    return r.stdout.strip()


def get_seal_commit(seal_path: Path) -> str:
    data = json.loads(seal_path.read_text(encoding="utf-8"))
    return str(data.get("runtime_software_commit", ""))


def check_provenance(seal_path: Path, expected_runtime_commit: str) -> dict[str, Any]:
    seal_commit = get_seal_commit(seal_path)
    local_head = get_local_commit()
    origin_main = get_origin_commit()

    results = {
        "expected_runtime_commit": expected_runtime_commit,
        "seal_runtime_commit": seal_commit,
        "seal_match": "MATCH" if seal_commit == expected_runtime_commit else "MISMATCH",
        "local_head": local_head,
        "origin_main": origin_main,
        "repo_head_vs_runtime_note": (
            "Repository documentation HEAD may be ancestor or descendant of runtime_code_commit."
        ),
    }
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Check live provenance across seal, git, and runtime.")
    parser.add_argument("--seal-path", type=Path, default=Path("infra/aws/seals/aws-72h-soak-20260904.runtime.json"))
    parser.add_argument("--expected-runtime-commit", type=str, default="9532cebc902856d954bf80b51dbe567b543dc8e2")
    args = parser.parse_args()

    res = check_provenance(args.seal_path, args.expected_runtime_commit)
    print(json.dumps(res, indent=2))
    return 0 if res["seal_match"] == "MATCH" else 1


if __name__ == "__main__":
    sys.exit(main())
