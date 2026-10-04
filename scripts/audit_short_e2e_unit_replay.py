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
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

FROZEN = Path(__file__).resolve().with_name("audit_fresh_30h_terminal_v2.py")
JOURNAL_REL = "terminal/systemd-invocation.jsonl"
SHORT_PREFIX = re.compile(r"bitcoin-trader-(?!30h-)[a-z0-9]+-(?=aws-validation-)")
SNAPSHOT_REL = "terminal/systemd-terminal.json"
INVOCATION_RE = re.compile(r"^[0-9a-f]{32}$")
EXIT_ROW_RE = re.compile(r"main process exited,\s*code=([a-zA-Z0-9_-]+),\s*status=(-?[0-9]+)", re.IGNORECASE)


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


def qualify_systemd_mechanics(bundle: Path, *, run_id: str) -> dict[str, Any]:
    """Diagnostic only: did the observed unit exit naturally and cleanly under its own InvocationID?

    This never substitutes for the frozen 30h acceptance check (SHORT_RUN_SYSTEMD_ACCEPTANCE=NOT_APPLICABLE).
    """
    failures: list[str] = []
    facts: dict[str, Any] = {}
    try:
        snapshot = json.loads((bundle / SNAPSHOT_REL).read_text(encoding="utf-8"))
        journal_bytes = (bundle / JOURNAL_REL).read_bytes()
        rows = [json.loads(line) for line in journal_bytes.decode("utf-8").splitlines() if line.strip()]
    except (OSError, ValueError) as exc:
        snapshot, journal_bytes, rows = {}, b"", []
        failures.append(f"evidence_unreadable:{type(exc).__name__}")
    if not isinstance(snapshot, dict):
        snapshot = {}
        failures.append("snapshot_not_an_object")
    unit = snapshot.get("unit")
    invocation = snapshot.get("InvocationID")
    unit_ok = isinstance(unit, str) and re.fullmatch(rf"bitcoin-trader-[a-z0-9]+-{re.escape(run_id)}\.service", unit) is not None
    if not unit_ok:
        failures.append("observed_unit_not_bound_to_run_id")
    if not isinstance(invocation, str) or not INVOCATION_RE.fullmatch(invocation):
        failures.append("invocation_id_invalid")
    for field, expected in (("Result", "success"), ("ExecMainCode", "exited"), ("ExecMainStatus", 0), ("NRestarts", 0)):
        if snapshot.get(field) != expected or isinstance(snapshot.get(field), bool):
            failures.append(f"{field}_not_{expected}")
    if not rows:
        failures.append("journal_empty")
    foreign = [
        index for index, row in enumerate(rows, 1)
        if not isinstance(row, dict) or row.get("_SYSTEMD_UNIT") != unit or row.get("_SYSTEMD_INVOCATION_ID") != invocation
    ]
    if foreign:
        failures.append("journal_rows_outside_exact_unit_and_invocation")
    messages = [str(row.get("MESSAGE", "")) for row in rows if isinstance(row, dict)]
    starts = [m for m in messages if re.search(r"\bStarting\b", m)]
    exits = [EXIT_ROW_RE.search(m) for m in messages]
    exits = [m for m in exits if m]
    if len(starts) < 1:
        failures.append("no_start_row")
    if len(exits) != 1 or (exits and (exits[0].group(1) != "exited" or int(exits[0].group(2)) != 0)):
        failures.append("journal_does_not_show_exactly_one_natural_exit_0")
    if any(re.search(r"\b(Stopping|Stopped by|Killing|SIGTERM|SIGKILL)\b", m) for m in messages):
        failures.append("journal_shows_an_external_stop_or_kill")
    facts.update({
        "unit": unit, "InvocationID": invocation, "Result": snapshot.get("Result"),
        "ExecMainCode": snapshot.get("ExecMainCode"), "ExecMainStatus": snapshot.get("ExecMainStatus"),
        "NRestarts": snapshot.get("NRestarts"), "runtime_duration_seconds": snapshot.get("runtime_duration_seconds"),
        "journal_rows": len(rows), "journal_sha256": hashlib.sha256(journal_bytes).hexdigest(),
        "snapshot_sha256": hashlib.sha256((bundle / SNAPSHOT_REL).read_bytes()).hexdigest() if (bundle / SNAPSHOT_REL).is_file() else None,
    })
    return {
        "classification": "DIAGNOSTIC_SYSTEMD_MECHANICS",
        "SYSTEMD_MECHANICS_QUALIFIED": "YES" if not failures else "NO",
        "SHORT_RUN_SYSTEMD_ACCEPTANCE": "NOT_APPLICABLE",
        "acceptance_evidence": False,
        "failures": failures,
        "facts": facts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mechanics-only", action="store_true",
                        help="emit only the labelled SYSTEMD_MECHANICS_QUALIFIED diagnostic")
    for name in ("epoch", "runtime-commit", "runtime-tree", "sealed-manifest-sha256", "capture-manifest-sha256"):
        parser.add_argument(f"--{name}")
    args = parser.parse_args(argv)
    if args.mechanics_only:
        report = qualify_systemd_mechanics(args.bundle, run_id=args.run_id)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["SYSTEMD_MECHANICS_QUALIFIED"] == "YES" else 1
    missing = [n for n in ("epoch", "runtime_commit", "runtime_tree", "sealed_manifest_sha256", "capture_manifest_sha256")
               if not getattr(args, n)]
    if missing:
        parser.error("replay requires: " + ", ".join(missing))
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
    report["systemd_mechanics"] = qualify_systemd_mechanics(args.bundle, run_id=args.run_id)
    report["note"] = "Frozen auditor logic unchanged; only the in-memory unit-name prefix of the raw journal is mapped to the hard-coded 30h prefix."
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
