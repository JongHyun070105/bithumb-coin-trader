"""FULL-SCAN microstructure JSONL and JSONL.ZST inputs without temporary expansion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable

from bithumb_coin_trader.microstructure_io import scan_jsonl


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "microstructure"
RAW_DIR = DATA_DIR / "raw"
COMPRESSED_DIR = DATA_DIR / "compressed"
QUARANTINE_DIR = DATA_DIR / "quarantine"


def discover_inputs(raw_dir: Path, compressed_dir: Path) -> list[Path]:
    raw = list(raw_dir.glob("**/*.jsonl")) if raw_dir.exists() else []
    compressed = list(compressed_dir.glob("**/*.jsonl.zst")) if compressed_dir.exists() else []
    return sorted(raw + compressed)


def full_scan(paths: Iterable[Path]) -> Dict[str, Any]:
    totals = {
        "files": 0,
        "logical_bytes": 0,
        "records": 0,
        "valid_records": 0,
        "invalid_json": 0,
        "schema_mismatch": 0,
        "missing_required_fields": 0,
        "non_finite_numeric": 0,
        "malformed_timestamps": 0,
        "unknown_market": 0,
        "scan_failures": 0,
        "compressed_files": 0,
    }
    failures = []
    for path in paths:
        totals["files"] += 1
        if path.name.endswith(".zst"):
            totals["compressed_files"] += 1
        try:
            result = scan_jsonl(path)
        except Exception as exc:
            totals["scan_failures"] += 1
            failures.append({"path": str(path), "error": type(exc).__name__})
            continue
        payload = result.to_dict()
        for key in (
            "logical_bytes",
            "records",
            "valid_records",
            "invalid_json",
            "schema_mismatch",
            "missing_required_fields",
            "non_finite_numeric",
            "malformed_timestamps",
            "unknown_market",
        ):
            totals[key] += int(payload[key])
    quality_failure_keys = (
        "invalid_json",
        "schema_mismatch",
        "missing_required_fields",
        "non_finite_numeric",
        "malformed_timestamps",
        "unknown_market",
        "scan_failures",
    )
    totals["status"] = "PASS" if not any(totals[key] for key in quality_failure_keys) else "FAIL"
    return {"totals": totals, "failures": failures}


def _quarantine_summary(paths: Iterable[Path]) -> Dict[str, Any]:
    total = 0
    reasons: Dict[str, int] = {}
    malformed = 0
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                total += 1
                try:
                    payload = json.loads(line)
                    reason = payload.get("error_reason", "unknown") if isinstance(payload, dict) else "invalid-schema"
                    reasons[str(reason)] = reasons.get(str(reason), 0) + 1
                except (UnicodeError, json.JSONDecodeError):
                    malformed += 1
    return {"records": total, "reasons": reasons, "malformed": malformed}


def main() -> None:
    inputs = discover_inputs(RAW_DIR, COMPRESSED_DIR)
    quarantine = list(QUARANTINE_DIR.glob("**/*.jsonl")) if QUARANTINE_DIR.exists() else []
    report = {
        "scan": "FULL_SCAN_ALL_DISCOVERED_RAW_AND_ZSTD_PARTITIONS",
        "integrity": full_scan(inputs),
        "quarantine": _quarantine_summary(quarantine),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["integrity"]["totals"]["status"] != "PASS":
        raise SystemExit("FULL-SCAN integrity failed")


if __name__ == "__main__":
    main()
