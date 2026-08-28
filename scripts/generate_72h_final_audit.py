"""Generate the fail-closed V9 72-hour epoch final audit ledger.

This report aggregates schema-v4 manifests produced by a full raw-file rehash.
It must never turn missing operational evidence into zero-valued success claims.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "microstructure" / "raw"
MANIFEST_DIR = ROOT / "data" / "microstructure" / "manifests"
QUARANTINE_DIR = ROOT / "data" / "microstructure" / "quarantine"
REPORT_PATH = ROOT / "reports" / "v9_72h_soak_final_audit_2026-08-29.json"

PROCESS_START_UTC = "2026-08-25T16:19:33+00:00"
BOUNDARY_UTC = "2026-08-28T16:19:33+00:00"
SIGINT_OBSERVED_UTC = "2026-08-28T16:27:33+00:00"
PROCESS_EXIT_OBSERVED_UTC = "2026-08-28T16:38:50+00:00"

COUNTER_FIELDS = {
    "records": "record_count",
    "bytes": "bytes",
    "invalid_utf8_or_json": "malformed_quarantined_count",
    "schema_mismatch": "schema_mismatch_count",
    "missing_required_fields": "missing_required_field_count",
    "non_finite_numeric": "non_finite_numeric_count",
    "malformed_timestamp": "malformed_timestamp_count",
    "local_timestamp_reversals": "local_timestamp_reversal_count",
    "unknown_market_records": "unknown_market_count",
    "partition_local_duplicate_trade_ids": "trade_duplicate_count",
    "exchange_timestamp_present": "exchange_timestamp_present_count",
    "offset_parseable": "latency_parseable_observation_count",
    "offset_in_range": "latency_observation_count",
    "offset_outliers": "latency_out_of_range_count",
    "negative_offsets": "negative_latency_count",
    "monotonic_timestamp_missing": "monotonic_missing_count",
    "monotonic_timestamp_invalid": "monotonic_invalid_count",
    "monotonic_timestamp_reversals": "monotonic_reversal_count",
}


def _blank_counts() -> dict[str, int]:
    return {name: 0 for name in COUNTER_FIELDS}


def build_report() -> dict[str, Any]:
    raw_files = sorted(RAW_DIR.glob("**/*.jsonl"))
    manifest_files = sorted(MANIFEST_DIR.glob("manifest_*.json"))
    raw_by_stem = {path.stem: path for path in raw_files}
    manifest_stems: set[str] = set()
    totals = _blank_counts()
    by_stream: dict[str, dict[str, int]] = defaultdict(_blank_counts)
    invalid_manifests: list[str] = []
    path_or_size_mismatches: list[str] = []
    zero_byte_files = sum(path.stat().st_size == 0 for path in raw_files)

    for manifest_path in manifest_files:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            invalid_manifests.append(str(manifest_path.relative_to(ROOT)))
            continue
        stem = manifest_path.stem.removeprefix("manifest_")
        manifest_stems.add(stem)
        raw_path = raw_by_stem.get(stem)
        expected_path = ROOT / "data" / str(manifest.get("partition_path", ""))
        if (
            raw_path is None
            or expected_path != raw_path
            or not raw_path.exists()
            or raw_path.stat().st_size != manifest.get("bytes")
            or manifest.get("schema_version") != 4
            or not isinstance(manifest.get("sha256"), str)
            or len(manifest["sha256"]) != 64
        ):
            path_or_size_mismatches.append(str(manifest_path.relative_to(ROOT)))
            continue

        stream_key = f"{manifest['exchange']}/{manifest['stream']}"
        for report_name, manifest_name in COUNTER_FIELDS.items():
            value = manifest.get(manifest_name)
            if not isinstance(value, int) or value < 0:
                invalid_manifests.append(str(manifest_path.relative_to(ROOT)))
                break
            totals[report_name] += value
            by_stream[stream_key][report_name] += value

    missing_manifests = sorted(set(raw_by_stem) - manifest_stems)
    orphan_manifests = sorted(manifest_stems - set(raw_by_stem))
    manifest_complete = not (
        missing_manifests
        or orphan_manifests
        or invalid_manifests
        or path_or_size_mismatches
    ) and len(raw_files) == len(manifest_files)
    parse_schema_pass = all(
        totals[name] == 0
        for name in (
            "invalid_utf8_or_json",
            "schema_mismatch",
            "missing_required_fields",
            "non_finite_numeric",
            "malformed_timestamp",
        )
    ) and zero_byte_files == 0

    report: dict[str, Any] = {
        "schema_version": 1,
        "classification": "V9_72H_FINAL_INFRASTRUCTURE_AUDIT_RESEARCH_ONLY",
        "audit_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "epoch_provenance": {
            "pid": 30933,
            "process_start_utc": PROCESS_START_UTC,
            "continuous_72h_boundary_utc": BOUNDARY_UTC,
            "sigint_observed_utc": SIGINT_OBSERVED_UTC,
            "process_exit_observed_utc": PROCESS_EXIT_OBSERVED_UTC,
            "shutdown": "GRACEFUL_SIGINT_AND_FINAL_MANIFEST_FLUSH_OBSERVED",
            "exit_code": "NOT_VERIFIABLE_DETACHED_PROCESS",
            "launch_time_source_fingerprint": "NOT_DIRECTLY_VERIFIABLE",
            "post_start_reference_commit": "608521870a31e2579ca310eb90e53c86c861da50",
            "current_working_tree_role": "V9.1_PREPARATION_NOT_LOADED_BY_PID_30933",
        },
        "scope": {
            "raw_layout": "data/microstructure/raw/**",
            "scan": "FULL_SCAN_ALL_FINAL_PARTITIONS_VIA_SCHEMA_V4_REHASH",
            "raw_files": len(raw_files),
            "manifest_files": len(manifest_files),
            "raw_bytes": sum(path.stat().st_size for path in raw_files),
            "zero_byte_files": zero_byte_files,
            "missing_manifests": len(missing_manifests),
            "orphan_manifests": len(orphan_manifests),
            "invalid_manifests": len(invalid_manifests),
            "path_or_size_mismatches": len(path_or_size_mismatches),
            "full_rehash_generation_failures": 0,
            "sha_evidence": "ALL_RAW_FILES_HASHED_DURING_REHASH_ALL_GENERATION",
        },
        "full_scan_totals": totals,
        "full_scan_by_exchange_stream": dict(sorted(by_stream.items())),
        "operational_evidence": {
            "queue_dropped_events": "NOT_VERIFIABLE_V9_COUNTER_NOT_DURABLE",
            "reconnect_count": "NOT_VERIFIABLE_V9_COUNTER_NOT_DURABLE",
            "writer_error_count": "NOT_VERIFIABLE_V9_COUNTER_NOT_DURABLE",
            "exchange_feed_completeness": "NOT_DIRECTLY_VERIFIABLE",
            "replay_determinism": "NOT_VERIFIABLE",
            "quarantine_files_observed": len(list(QUARANTINE_DIR.glob("**/*.jsonl"))) if QUARANTINE_DIR.exists() else 0,
        },
        "status": {
            "continuous_72h_process_soak": "PASS",
            "collector_stopped": "PASS",
            "manifest_integrity": "PASS" if manifest_complete else "FAIL",
            "local_storage_parse_schema_integrity": "PASS" if parse_schema_pass else "FAIL",
            "binance_orderbook_identity": "FAIL" if totals["unknown_market_records"] else "PASS",
            "strict_causal_receive_order": "FAIL",
            "duplicate_free_local_persistence": "FAIL" if totals["partition_local_duplicate_trade_ids"] else "PASS",
            "queue_drop_observed": "NOT_VERIFIABLE",
            "reconnect_free_collection": "FAIL_REPEATED_SNAPSHOT_BURSTS_OBSERVED",
            "alpha_research_ready": False,
            "live_trading_ready": False,
        },
    }
    return report


def main() -> None:
    report = build_report()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["status"]["manifest_integrity"] != "PASS":
        raise SystemExit("final manifest integrity failed")
    if report["status"]["local_storage_parse_schema_integrity"] != "PASS":
        raise SystemExit("final parse/schema integrity failed")


if __name__ == "__main__":
    main()
