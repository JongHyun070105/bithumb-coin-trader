#!/usr/bin/env python3
"""Sealed 72-Hour Epoch Evidence Root Manifest Builder.

Constructs a cryptographically bound root manifest (epoch_manifest.json)
representing all raw partitions, partition manifests, archive receipts,
terminal full-scan reports, and runtime seals across the entire soak run.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

try:
    from scripts.audit_72h_soak import (
        EXPECTED_BITHUMB_20,
        EXPECTED_BINANCE_4,
        EXPECTED_UPBIT_4,
        SoakAuditor72H,
        parse_partition_path,
        _stream_file_sha256,
        derive_expected_raw_cohorts,
        derive_expected_archive_cohorts,
        derive_expected_fullscan_cohorts,
    )
except ModuleNotFoundError:
    from audit_72h_soak import (
        EXPECTED_BITHUMB_20,
        EXPECTED_BINANCE_4,
        EXPECTED_UPBIT_4,
        SoakAuditor72H,
        parse_partition_path,
        _stream_file_sha256,
        derive_expected_raw_cohorts,
        derive_expected_archive_cohorts,
        derive_expected_fullscan_cohorts,
    )


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_epoch_manifest(
    epoch_dir: Path,
    contract_path: Path | None = None,
    output_path: Path | None = None,
    strict: bool = True,
    mode: str = "lenient",
) -> dict[str, Any]:
    epoch_dir = Path(epoch_dir)
    strict = strict or (mode == "official")
    raw_dir = epoch_dir / "raw"
    manifests_dir = epoch_dir / "manifests"
    receipts_dir = epoch_dir / "archive-receipts"
    if not receipts_dir.exists():
        receipts_dir = epoch_dir / "receipts"

    # 1. Load run contract / runtime seal
    if contract_path is None or not contract_path.exists():
        candidates = [
            epoch_dir / "epoch_contract.json",
            epoch_dir / "runtime_seal.json",
            epoch_dir / "aws-72h-soak.runtime.json",
        ]
        for c in candidates:
            if c.exists():
                contract_path = c
                break

    contract_data: dict[str, Any] = {}
    if contract_path and contract_path.exists():
        try:
            contract_data = json.loads(contract_path.read_text(encoding="utf-8"))
        except Exception as e:
            if strict:
                raise ValueError(f"CORRUPT_RUN_CONTRACT: {contract_path}: {e}")
    elif strict:
        raise ValueError("NO_RUN_CONTRACT: Run contract required for epoch manifest build")

    collector_epoch = contract_data.get("collector_epoch") or epoch_dir.name
    collector_run_id = contract_data.get("collector_run_id") or "unknown"
    runtime_commit = (
        contract_data.get("runtime_software_commit")
        or contract_data.get("runtime_code_commit")
        or "unknown"
    )
    runtime_fingerprint = (
        contract_data.get("runtime_config_fingerprint")
        or contract_data.get("runtime_fingerprint")
        or "unknown"
    )
    raw_schema_version = contract_data.get("raw_schema_version") or "2.0.0"
    start_time_utc = contract_data.get("start_time_utc")
    expected_end_time_utc = contract_data.get("expected_end_time_utc")
    duration_seconds = contract_data.get("duration_seconds", 0)

    # Cross-validation with runtime_seal.json / runtime.json and launch-provenance.json
    runtime_seal_p = None
    for p in [epoch_dir / "runtime_seal.json", epoch_dir / "runtime.json", epoch_dir / "aws-72h-soak.runtime.json"]:
        if p.exists():
            runtime_seal_p = p
            break

    launch_prov_p = None
    for p in [epoch_dir / "launch-provenance.json", epoch_dir / "aws-72h-soak.launch-provenance.json"]:
        if p.exists():
            launch_prov_p = p
            break

    if strict:
        if runtime_seal_p:
            try:
                seal_data = json.loads(runtime_seal_p.read_text(encoding="utf-8"))
                seal_commit = seal_data.get("runtime_software_commit") or seal_data.get("runtime_code_commit") or seal_data.get("runtime_commit") or seal_data.get("software_commit")
                if seal_commit and runtime_commit != "unknown" and runtime_commit != seal_commit:
                    raise ValueError(f"RUNTIME_COMMIT_MISMATCH: contract commit '{runtime_commit}' != runtime seal '{seal_commit}'")
                seal_fp = seal_data.get("runtime_config_fingerprint") or seal_data.get("runtime_fingerprint") or seal_data.get("fingerprint")
                if seal_fp and runtime_fingerprint != "unknown" and runtime_fingerprint != seal_fp:
                    raise ValueError(f"RUNTIME_FINGERPRINT_MISMATCH: contract fingerprint '{runtime_fingerprint}' != runtime seal '{seal_fp}'")
            except ValueError:
                raise
            except Exception as e:
                raise ValueError(f"CORRUPT_RUNTIME_SEAL: {e}")

        if launch_prov_p:
            try:
                prov_data = json.loads(launch_prov_p.read_text(encoding="utf-8"))
                prov_fp = prov_data.get("fingerprint") or prov_data.get("runtime_config_fingerprint")
                prov_run = prov_data.get("collector_run_id")
                prov_epoch = prov_data.get("collector_epoch")
                prov_commit = prov_data.get("software_commit") or prov_data.get("runtime_software_commit")
                if prov_commit and runtime_commit != "unknown" and runtime_commit != prov_commit:
                    raise ValueError(f"RUNTIME_COMMIT_MISMATCH: contract commit '{runtime_commit}' != launch provenance '{prov_commit}'")
                if prov_fp and runtime_fingerprint != "unknown" and runtime_fingerprint != prov_fp:
                    raise ValueError(f"RUNTIME_FINGERPRINT_MISMATCH: contract fingerprint '{runtime_fingerprint}' != launch provenance '{prov_fp}'")
                if prov_run and collector_run_id != "unknown" and collector_run_id != prov_run:
                    raise ValueError(f"COLLECTOR_RUN_ID_MISMATCH: contract run_id '{collector_run_id}' != launch provenance '{prov_run}'")
                if prov_epoch and collector_epoch != "unknown" and collector_epoch != prov_epoch:
                    raise ValueError(f"COLLECTOR_EPOCH_MISMATCH: contract epoch '{collector_epoch}' != launch provenance '{prov_epoch}'")
            except ValueError:
                raise
            except Exception as e:
                raise ValueError(f"CORRUPT_LAUNCH_PROVENANCE: {e}")

    # 2. Derive expected cohorts
    expected_raw_cohorts: list[str] = []
    expected_archive_cohorts: list[str] = []
    fullscan_spec: dict[str, Any] = {"hourly_fullscan_cohorts": [], "terminal_fullscan_required": False}
    if start_time_utc and (expected_end_time_utc or duration_seconds):
        try:
            start_dt = datetime.fromisoformat(start_time_utc)
            if expected_end_time_utc:
                end_dt = datetime.fromisoformat(expected_end_time_utc)
            else:
                from datetime import timedelta
                end_dt = start_dt + timedelta(seconds=duration_seconds)
            expected_raw_cohorts = derive_expected_raw_cohorts(start_dt, end_dt)
            expected_archive_cohorts = derive_expected_archive_cohorts(start_dt, end_dt, grace_seconds=600)
            fullscan_spec = derive_expected_fullscan_cohorts(start_dt, end_dt)
        except Exception as e:
            if strict:
                raise ValueError(f"CANNOT_DERIVE_COHORTS: {e}")

    # 3. Discover all partition manifests and raw files
    partition_entries: list[dict[str, Any]] = []
    found_feeds_by_cohort: dict[str, set[str]] = {}

    manifest_files: list[Path] = []
    if manifests_dir.exists():
        for pat in ("**/manifest_*.json", "**/*.manifest.json"):
            manifest_files.extend(list(manifests_dir.glob(pat)))
    manifest_files = sorted(set(manifest_files))

    raw_files: list[Path] = []
    if raw_dir.exists():
        for pat in ("**/*.jsonl", "**/*.zst", "**/*.ndjson"):
            raw_files.extend(list(raw_dir.glob(pat)))
    raw_files = sorted(set(raw_files))

    raw_file_map: dict[str, Path] = {f.name: f for f in raw_files}

    for mf in manifest_files:
        try:
            m_data = json.loads(mf.read_text(encoding="utf-8"))
        except Exception as e:
            if strict:
                raise ValueError(f"CORRUPT_MANIFEST: {mf.name}: {e}")
            continue
        rel_p = m_data.get("partition_path", "")
        parsed = parse_partition_path(rel_p, manifest_meta=m_data)
        if not parsed:
            continue
        exch, strm, mkt, hour = parsed
        if hour not in found_feeds_by_cohort:
            found_feeds_by_cohort[hour] = set()
        found_feeds_by_cohort[hour].add(f"{exch}/{mkt}/{strm}")

        mf_sha = _file_sha256(mf)
        raw_sha = m_data.get("sha256", "")
        rec_count = m_data.get("record_count", 0)
        byte_count = m_data.get("bytes", 0)

        # Match raw file
        raw_p = raw_dir / rel_p.removeprefix("raw/").removeprefix("/")
        if not raw_p.exists() and Path(rel_p).name in raw_file_map:
            raw_p = raw_file_map[Path(rel_p).name]

        if not raw_p.exists():
            if strict:
                raise ValueError(f"MISSING_RAW_FILE: Raw partition missing for manifest {mf.name}: {rel_p}")
            continue

        actual_sha, actual_bytes, actual_records = _stream_file_sha256(raw_p)
        if raw_sha and actual_sha != raw_sha:
            if strict:
                raise ValueError(f"RAW_PARTITION_HASH_MISMATCH: {raw_p.name} actual '{actual_sha}' != manifest '{raw_sha}'")

        raw_sha = actual_sha
        rec_count = actual_records
        byte_count = actual_bytes

        partition_entries.append({
            "exchange": exch,
            "market": mkt,
            "stream": strm,
            "hour_cohort": hour,
            "partition_path": str(raw_p.relative_to(epoch_dir)) if raw_p.exists() else rel_p,
            "raw_sha256": raw_sha,
            "record_count": rec_count,
            "bytes": byte_count,
            "manifest_file": str(mf.relative_to(epoch_dir)),
            "manifest_sha256": mf_sha,
        })

    default_hour = expected_raw_cohorts[0] if expected_raw_cohorts else "unknown"
    for rf in (list(receipts_dir.glob("**/*.archive-receipt.json")) if receipts_dir.exists() else []):
        try:
            rd = json.loads(rf.read_text(encoding="utf-8"))
            default_hour = rd.get("hour_cohort") or rd.get("cohort") or rf.name.split(".")[0]
            break
        except Exception:
            pass

    # If no partition manifests, index directly from raw files
    if not partition_entries and raw_files:
        for rf in raw_files:
            rel = rf.relative_to(raw_dir if raw_dir.exists() else epoch_dir)
            parsed = parse_partition_path(rel)
            if not parsed:
                continue
            exch, strm, mkt, hour = parsed
            if hour == "unknown":
                hour = default_hour
            if hour not in found_feeds_by_cohort:
                found_feeds_by_cohort[hour] = set()
            found_feeds_by_cohort[hour].add(f"{exch}/{mkt}/{strm}")

            raw_sha, byte_count, rec_count = _stream_file_sha256(rf)
            partition_entries.append({
                "exchange": exch,
                "market": mkt,
                "stream": strm,
                "hour_cohort": hour,
                "partition_path": str(rf.relative_to(epoch_dir)),
                "raw_sha256": raw_sha,
                "record_count": rec_count,
                "bytes": byte_count,
                "manifest_file": "",
                "manifest_sha256": "",
            })

    partition_entries.sort(key=lambda x: (x["hour_cohort"], x["exchange"], x["market"], x["stream"]))

    # 4. Discover archive receipts
    receipt_entries: list[dict[str, Any]] = []
    receipt_files: list[Path] = []
    if receipts_dir.exists():
        receipt_files = sorted(set(list(receipts_dir.glob("**/*.archive-receipt.json")) + list(receipts_dir.glob("**/receipt_*.json"))))
    for rf in receipt_files:
        try:
            r_data = json.loads(rf.read_text(encoding="utf-8"))
            r_sha = _file_sha256(rf)
            cohort = r_data.get("hour_cohort") or rf.name.split(".")[0]
            receipt_entries.append({
                "hour_cohort": cohort,
                "file_name": rf.name,
                "receipt_sha256": r_sha,
                "status": r_data.get("status") or r_data.get("state") or "UNKNOWN",
                "restore_verified": bool(r_data.get("restore_verified")),
                "file_count": r_data.get("file_count", 0),
            })
        except Exception:
            pass
    receipt_entries.sort(key=lambda x: x["hour_cohort"])

    # 5. Discover full scan reports
    fullscan_entries: list[dict[str, Any]] = []
    fullscan_files: list[Path] = []
    if receipts_dir.exists():
        fullscan_files = sorted(set(list(receipts_dir.glob("**/full_scan_*_report.json"))))
    for fs in fullscan_files:
        try:
            fs_data = json.loads(fs.read_text(encoding="utf-8"))
            fs_sha = _file_sha256(fs)
            fullscan_entries.append({
                "file_name": fs.name,
                "fullscan_sha256": fs_sha,
                "status": fs_data.get("status", "UNKNOWN"),
                "total_records": fs_data.get("total_records", 0),
            })
        except Exception:
            pass
    fullscan_entries.sort(key=lambda x: x["file_name"])

    # 6. Runtime seal & Launch provenance
    runtime_seal_sha = ""
    launch_prov_sha = ""
    for seal_p in [epoch_dir / "runtime_seal.json", contract_path]:
        if seal_p and seal_p.exists():
            runtime_seal_sha = _file_sha256(seal_p)
            break
    launch_p = epoch_dir / "launch-provenance.json"
    if not launch_p.exists():
        launch_p = epoch_dir / "aws-72h-soak.launch-provenance.json"
    if launch_p.exists():
        launch_prov_sha = _file_sha256(launch_p)

    # 7. Check completeness against 76-feed universe and cohorts
    missing_items: list[str] = []
    feed_universe = SoakAuditor72H.get_expected_feed_universe()

    cohorts_to_check = expected_raw_cohorts or sorted(found_feeds_by_cohort.keys())
    for ch in cohorts_to_check:
        ch_feeds = found_feeds_by_cohort.get(ch, set())
        for exch, strm, mkt in feed_universe:
            k = f"{exch}/{mkt}/{strm}"
            alt_upper = f"{exch}/{mkt.upper()}/{strm}"
            alt_lower = f"{exch}/{mkt.lower()}/{strm}"
            if k not in ch_feeds and alt_upper not in ch_feeds and alt_lower not in ch_feeds:
                missing_items.append(f"MISSING_FEED:{ch}:{k}")

    # Check receipt for expected archive cohorts
    archive_cohorts_to_check = expected_archive_cohorts
    if not archive_cohorts_to_check and (len(cohorts_to_check) > 1 or contract_data.get("require_receipts", False)):
        archive_cohorts_to_check = cohorts_to_check

    if archive_cohorts_to_check:
        for ch in archive_cohorts_to_check:
            norm_ch = re.sub(r"[-_]", "", ch)
            has_receipt = any(
                (r["hour_cohort"] == ch or re.sub(r"[-_]", "", r["hour_cohort"]) == norm_ch or norm_ch in re.sub(r"[-_]", "", r["file_name"]))
                and r["restore_verified"]
                for r in receipt_entries
            )
            if not has_receipt:
                missing_items.append(f"MISSING_RECEIPT:{ch}")

    if contract_data.get("require_fullscan", False) or fullscan_spec["terminal_fullscan_required"]:
        if not fullscan_entries or not any(fs["status"] == "PASS" for fs in fullscan_entries):
            missing_items.append("MISSING_FULLSCAN_REPORT")

    if mode == "official" and not launch_prov_sha:
        missing_items.append("MISSING_LAUNCH_PROVENANCE")

    is_complete = len(missing_items) == 0 and len(partition_entries) > 0
    status = "SEALED_COMPLETE" if is_complete else "INCOMPLETE"

    manifest_dict: dict[str, Any] = {
        "schema_version": "2.1.0",
        "collector_epoch": collector_epoch,
        "collector_run_id": collector_run_id,
        "runtime_commit": runtime_commit,
        "runtime_fingerprint": runtime_fingerprint,
        "raw_schema_version": raw_schema_version,
        "start_time_utc": start_time_utc,
        "expected_end_time_utc": expected_end_time_utc,
        "duration_seconds": duration_seconds,
        "status": status,
        "sealed_complete": is_complete,
        "missing_items": missing_items,
        "expected_hour_cohorts": expected_raw_cohorts,
        "expected_archive_cohorts": expected_archive_cohorts,
        "runtime_seal_sha256": runtime_seal_sha,
        "launch_provenance_sha256": launch_prov_sha,
        "partitions_count": len(partition_entries),
        "archive_receipts_count": len(receipt_entries),
        "fullscan_reports_count": len(fullscan_entries),
        "archive_receipts": receipt_entries,
        "fullscan_reports": fullscan_entries,
        "partitions": partition_entries,
    }

    # Compute deterministic root SHA256
    canonical_json = json.dumps(manifest_dict, sort_keys=True, separators=(",", ":"))
    root_sha = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    manifest_dict["epoch_manifest_sha256"] = root_sha

    if output_path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(manifest_dict, indent=2), encoding="utf-8")
        print(f"Wrote epoch manifest to {out_p} (status={status}, sha256={root_sha})")

    return manifest_dict


def main() -> int:
    parser = argparse.ArgumentParser(description="Build epoch root manifest.")
    parser.add_argument("--epoch-dir", required=True, type=Path, help="Path to soak epoch directory")
    parser.add_argument("--contract", "--epoch-contract", type=Path, default=None, help="Run contract path")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Output epoch_manifest.json path")
    parser.add_argument("--strict", action="store_true", default=False, help="Fail if incomplete")
    parser.add_argument("--mode", choices=["official", "lenient"], default="official", help="Mode (default: official)")
    args = parser.parse_args()

    out_p = args.output or (args.epoch_dir / "manifests" / "epoch_manifest.json")
    try:
        res = build_epoch_manifest(
            epoch_dir=args.epoch_dir,
            contract_path=args.contract,
            output_path=out_p,
            strict=args.strict,
            mode=args.mode,
        )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if (args.strict or args.mode == "official") and not res.get("sealed_complete"):
        print(f"ERROR: Epoch is incomplete: {res.get('missing_items')}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
