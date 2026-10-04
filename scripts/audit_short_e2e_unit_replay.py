#!/usr/bin/env python3
"""Diagnostic replay of the unchanged Frozen V2 auditor for sub-30H E2E runs.

The frozen auditor hard-codes the unit name ``bitcoin-trader-30h-<run_id>.service``.
Shorter sealed runs are launched as ``bitcoin-trader-<3h|6h|...>-<run_id>.service``.
This replay subclasses (never edits) the frozen auditor and, in memory only,
maps the observed unit prefix onto the hard-coded ``30h`` prefix when the raw
journal is read. The bundle bytes are untouched. Output is labelled
DIAGNOSTIC_UNIT_PREFIX_REPLAY and is NOT acceptance evidence for a 30H run.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

FROZEN = Path(__file__).resolve().with_name("audit_fresh_30h_terminal_v2.py")
JOURNAL_REL = "terminal/systemd-invocation.jsonl"
SHORT_PREFIX = re.compile(r"bitcoin-trader-(?!30h-)[a-z0-9]+-(?=aws-validation-)")


def load_frozen() -> Any:
    spec = importlib.util.spec_from_file_location("frozen_audit_fresh_30h_terminal_v2", FROZEN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_replay_class(module: Any) -> type:
    class UnitPrefixReplayAuditor(module.AuditorV2):
        def _read_payload(self, rel: str, *, limit: int | None = module.MAX_JSON_BYTES) -> bytes:
            data = super()._read_payload(rel, limit=limit)
            if rel == JOURNAL_REL:
                return SHORT_PREFIX.sub("bitcoin-trader-30h-", data.decode("utf-8")).encode("utf-8")
            return data

    return UnitPrefixReplayAuditor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--epoch", required=True)
    parser.add_argument("--runtime-commit", required=True)
    parser.add_argument("--runtime-tree", required=True)
    parser.add_argument("--sealed-manifest-sha256", required=True)
    parser.add_argument("--capture-manifest-sha256", required=True)
    args = parser.parse_args(argv)
    module = load_frozen()
    auditor = build_replay_class(module)(
        args.bundle,
        run_id=args.run_id,
        epoch=args.epoch,
        runtime_commit=args.runtime_commit,
        runtime_tree=args.runtime_tree,
        sealed_manifest_sha256=args.sealed_manifest_sha256,
        capture_manifest_sha256=args.capture_manifest_sha256,
    )
    report = auditor.audit()
    report["classification"] = "DIAGNOSTIC_UNIT_PREFIX_REPLAY"
    report["acceptance_evidence"] = False
    report["note"] = "Frozen auditor logic unchanged; only the in-memory unit-name prefix of the raw journal is mapped to the hard-coded 30h prefix."
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
