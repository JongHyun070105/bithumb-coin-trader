"""Microstructure Research & Paper CLI Interface.

Commands:
- verify-ledger: Validate cryptographic hash-chain of the experiment ledger
- power-plan: Output required sample size for given target Sharpe, alpha, power, autocorrelation
- run-synthetic-sim: Generate synthetic orderbook stream and run deterministic microstructure replay
- audit-quality: Validate raw soak archive directory data quality
- structural-audit: Fast structural-only validation (cannot qualify for research datasets)
- deep-dq-audit: Authoritative deep DQ and integrity audit
- build-epoch-manifest: Construct sealed epoch root manifest
- dq-qualify: Produce cryptographic DQ qualification evidence artifact
- transform-canonical: Transform raw exchange microstructure data into canonical NDJSON
- partition-dataset: Temporally partition canonical market data with purge windows
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
import uuid

import zstandard

_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from .canonical_market_data import (
    CanonicalDataValidationError,
    CanonicalOrderBook,
    CanonicalTicker,
    CanonicalTrade,
    raw_record_to_canonical,
)
from .experiment_runner import (
    ExperimentGatingError,
    ExperimentLedger,
)
from .sample_size_planner import compute_required_sample_size
from .replay import MultiStreamReplay
from .synthetic_market import SignalMarketGenerator


def _stream_file_sha256(path: Path) -> tuple[str, int, int]:
    """Stream SHA256 of a file and return (sha256_hex, total_bytes, line_count)."""
    hasher = hashlib.sha256()
    total_bytes = 0
    lines = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            total_bytes += len(chunk)
            lines += chunk.count(b"\n")
            hasher.update(chunk)
    return hasher.hexdigest(), total_bytes, lines


def _file_sha256(path: Path) -> str:
    return _stream_file_sha256(path)[0]


def _detect_git_head() -> str:
    try:
        import subprocess
        out = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL)
        commit = out.decode("utf-8").strip()
        if commit:
            return commit
    except Exception:
        pass
    raise RuntimeError("CANNOT_RESOLVE_GIT_HEAD: Valid Git repository HEAD commit required for software provenance")


def compute_canonical_report_hash(report_dict: dict[str, Any]) -> str:
    """Canonical JSON SHA-256 excluding self report_hash and qualification_sha256."""
    cleaned = {k: v for k, v in report_dict.items() if k not in ("report_hash", "qualification_sha256")}
    canonical_json = json.dumps(cleaned, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def cmd_verify_ledger(args: argparse.Namespace) -> int:
    ledger_path = Path(args.ledger)
    if not ledger_path.exists():
        print(f"ERROR: Ledger file not found: {ledger_path}")
        return 1

    from .experiment_runner import GovernedExperimentRunner
    runner = GovernedExperimentRunner(ledger_file=ledger_path)
    try:
        valid = runner.verify_ledger_chain()
        if valid:
            print(f"SUCCESS: Ledger chain verified. Length: {len(runner._entries)}")
            return 0
        else:
            print(f"FAIL: Cryptographic hash-chain validation failed for ledger at {ledger_path}.")
            return 2
    except ExperimentGatingError as e:
        print(f"TAMPER DETECTED: {e}")
        return 2


def cmd_power_plan(args: argparse.Namespace) -> int:
    n = compute_required_sample_size(
        target_sharpe_per_period=args.sharpe,
        alpha=args.alpha,
        power=args.power,
        autocorrelation_rho=args.rho,
    )
    print(f"Required observations: {n:,} (alpha={args.alpha}, power={args.power}, rho={args.rho})")
    return 0


def cmd_run_synthetic_sim(args: argparse.Namespace) -> int:
    gen = SignalMarketGenerator(initial_price=100_000_000.0, seed=42)
    books, signals = gen.generate_signal_orderbooks(count=args.count)
    replay = MultiStreamReplay([iter(books)])
    events = list(replay)
    print(f"Generated and replayed {len(events)} synthetic microstructure events successfully.")
    return 0


def cmd_audit_quality(args: argparse.Namespace) -> int:
    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"ERROR: Input directory not found: {input_dir}")
        return 1

    report: dict[str, Any] = {
        "audit_type": "structural_only",
        "input_dir": str(input_dir),
        "files_found": [],
        "manifest_files": [],
        "errors": [],
        "status": "UNKNOWN",
    }

    ndjson_count = 0
    manifest_count = 0

    for fpath in sorted(input_dir.rglob("*")):
        if fpath.is_file():
            report["files_found"].append(str(fpath.relative_to(input_dir)))
            if (
                fpath.name.endswith(".ndjson.zst")
                or fpath.name.endswith(".jsonl.zst")
                or fpath.name.endswith(".jsonl")
                or fpath.name.endswith(".ndjson")
                or fpath.name.endswith(".zst")
            ) and not fpath.name.startswith("."):
                ndjson_count += 1
            if (
                fpath.name == "manifest.json"
                or fpath.name.endswith(".manifest.json")
                or (fpath.name.startswith("manifest_") and fpath.name.endswith(".json"))
            ):
                manifest_count += 1
                try:
                    data = json.loads(fpath.read_text(encoding="utf-8"))
                    report["manifest_files"].append({
                        "path": str(fpath.relative_to(input_dir)),
                        "valid_json": True,
                        "keys": list(data.keys()),
                    })
                    if not data:
                        report["errors"].append(f"Manifest missing required fields at {fpath}")
                except json.JSONDecodeError as e:
                    report["errors"].append(f"Invalid manifest JSON at {fpath}: {e}")

    if not report["files_found"]:
        report["status"] = "INCOMPLETE"
    elif manifest_count == 0:
        report["status"] = "STRUCTURAL_ONLY"
    elif ndjson_count == 0:
        report["status"] = "INCOMPLETE"
    elif report["errors"]:
        report["status"] = "FAIL"
    else:
        report["status"] = "STRUCTURAL_AUDIT_PASS"

    out_path = Path(args.report_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Audit complete: status={report['status']} files={len(report['files_found'])} errors={len(report['errors'])}")

    if report["status"] == "STRUCTURAL_AUDIT_PASS":
        return 0
    elif report["status"] == "STRUCTURAL_ONLY":
        return 1
    elif report["status"] in ("INCOMPLETE", "FAIL", "UNKNOWN"):
        return 2
    else:
        return 2


def cmd_dq_qualify(args: argparse.Namespace) -> int:
    """Produce cryptographic DQ qualification evidence artifact from authoritative deep audit."""
    from scripts.build_epoch_manifest import verify_epoch_manifest

    audit_report_path = Path(args.audit_report)
    out_path = Path(args.out)
    if not audit_report_path.exists():
        print(f"ERROR: Audit report not found: {audit_report_path}")
        return 1

    try:
        report_bytes = audit_report_path.read_bytes()
        audit_data = json.loads(report_bytes.decode("utf-8"))
    except Exception as e:
        print(f"ERROR parsing audit report: {e}")
        return 2

    # P5: Structural-only audits cannot qualify research datasets
    audit_type = audit_data.get("audit_type", "")
    status = audit_data.get("status", "")
    if audit_type != "authoritative_deep_dq":
        print(f"ERROR: STRUCTURAL_ONLY_NOT_QUALIFIABLE: only authoritative_deep_dq audits can qualify research datasets (got {audit_type})")
        return 2

    errors = audit_data.get("errors", [])
    blockers = audit_data.get("blockers", [])
    if status != "DQ_PASS_ELIGIBLE" or errors or blockers:
        print(f"ERROR: Audit report does not qualify for research (status={status}, errors={len(errors)}, blockers={len(blockers)})")
        return 2

    # P4: String-only source manifest hash is NOT permitted
    sm_arg = getattr(args, "source_manifest", None) or getattr(args, "epoch_manifest", None)
    if not sm_arg:
        if getattr(args, "source_manifest_hash", None):
            print("ERROR: HASH_ONLY_QUALIFICATION_NOT_PERMITTED: --epoch-manifest / --source-manifest file is required for official qualification")
            return 2
        print("ERROR: Source manifest (--source-manifest / --epoch-manifest) is required for qualification")
        return 2

    sm_path = Path(sm_arg)
    if not sm_path.exists():
        print(f"ERROR: Source manifest not found: {sm_path}")
        return 2

    sm_file_sha = hashlib.sha256(sm_path.read_bytes()).hexdigest()
    try:
        sm_raw = json.loads(sm_path.read_text(encoding="utf-8"))
        if "epoch_manifest_sha256" in sm_raw or getattr(args, "epoch_manifest", None):
            sm_json = verify_epoch_manifest(sm_path)
            epoch_manifest_sha = sm_json.get("epoch_manifest_sha256", "")
            # Check required provenance fields
            for prov_field in ("collector_epoch", "collector_run_id", "runtime_commit", "runtime_fingerprint"):
                val = sm_json.get(prov_field)
                if not val or val == "unknown":
                    print(f"ERROR: UNKNOWN_ROOT_PROVENANCE: {prov_field} missing or unknown in epoch manifest")
                    return 2
        else:
            sm_json = sm_raw
            epoch_manifest_sha = sm_file_sha
    except Exception as e:
        print(f"ERROR: Invalid epoch manifest {sm_path}: {e}")
        return 2

    # P9: Dynamic commit
    commit_sha = args.commit if (getattr(args, "commit", None) and args.commit != "HEAD") else _detect_git_head()
    audit_report_sha256 = hashlib.sha256(report_bytes).hexdigest()

    # P4.1: Strict DQ state semantics
    hard_fail_count = len(blockers) + len(errors)
    if status != "DQ_PASS_ELIGIBLE":
        hard_fail_count += 1
    unknown_count = 0
    degraded_count = 0
    for w in audit_data.get("warnings", []):
        w_str = str(w)
        if w_str.startswith("INFO:"):
            continue
        elif w_str.startswith("UNKNOWN:"):
            unknown_count += 1
        else:
            degraded_count += 1

    if hard_fail_count > 0:
        qual_status = "DQ_FAIL"
    elif degraded_count > 0 or unknown_count > 0:
        qual_status = "DQ_DEGRADED"
    else:
        qual_status = "DQ_PASS"

    if qual_status != "DQ_PASS" and getattr(args, "strict", False):
        print(f"ERROR: Audit report has non-pass status: {qual_status} (degraded={degraded_count}, unknown={unknown_count}, hard_fail={hard_fail_count})")
        return 2

    evidence_dict = {
        "status": qual_status,
        "auditor_version": getattr(args, "auditor_version", None) or "v9.1.0-offline",
        "auditor_commit": commit_sha,
        "audit_code_commit": commit_sha,
        "source_manifest_hash": epoch_manifest_sha,
        "source_manifest_file_sha256": sm_file_sha,
        "epoch_manifest_sha256": epoch_manifest_sha,
        "criteria_version": getattr(args, "criteria_version", None) or "v1-strict",
        "hard_fail_count": hard_fail_count,
        "unknown_count": unknown_count,
        "degraded_count": degraded_count,
        "justification": "",
        "approved_policy": getattr(args, "policy", None) or "strict_v1",
        "audit_report_sha256": audit_report_sha256,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    report_hash = compute_canonical_report_hash(evidence_dict)
    evidence_dict["report_hash"] = report_hash
    evidence_dict["qualification_sha256"] = report_hash

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(evidence_dict, indent=2), encoding="utf-8")
    print(f"DQ qualification artifact generated: {out_path} (report_hash={report_hash[:16]})")
    return 0


def cmd_transform_canonical(args: argparse.Namespace) -> int:
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    exchange = getattr(args, "exchange", "bithumb")
    stream_filter = getattr(args, "stream", None)
    market_filter = getattr(args, "market", None)
    schema_version = getattr(args, "schema_version", "2.0.0")

    if not input_dir.exists():
        print(f"ERROR: Input directory not found: {input_dir}")
        return 1

    if exchange not in ("bithumb", "binance", "upbit"):
        print(f"ERROR: Unsupported exchange {exchange}")
        return 3

    output_dir.mkdir(parents=True, exist_ok=True)
    raw_files = sorted(
        [
            f for f in input_dir.rglob("*")
            if f.is_file()
            and (
                f.name.endswith(".ndjson.zst")
                or f.name.endswith(".jsonl.zst")
                or f.name.endswith(".jsonl")
                or f.name.endswith(".ndjson")
                or f.name.endswith(".zst")
            )
            and not f.name.startswith(".")
            and not f.name.endswith(".manifest.json")
        ]
    )

    em_arg = getattr(args, "epoch_manifest", None) or getattr(args, "source_manifest", None)
    em_sha = None
    files_to_process: list[Path] = []

    if em_arg:
        em_p = Path(em_arg)
        if not em_p.exists():
            print(f"ERROR: Epoch manifest not found: {em_p}")
            return 2
        try:
            from scripts.build_epoch_manifest import verify_epoch_manifest
            em_data = verify_epoch_manifest(em_p)
            em_sha = em_data.get("epoch_manifest_sha256")
        except Exception as e:
            print(f"ERROR: Invalid epoch manifest {em_p}: {e}")
            return 2

        root_partitions = em_data.get("partitions", [])
        root_known_rel_paths: set[str] = set()
        root_known_names: set[str] = set()

        for p_meta in root_partitions:
            p_rel = p_meta.get("partition_path", "")
            raw_sha_expected = p_meta.get("raw_sha256")
            p_clean = p_rel.removeprefix("raw/").removeprefix("/")
            root_known_rel_paths.add(p_clean)
            root_known_rel_paths.add(p_rel)
            root_known_names.add(Path(p_rel).name)

            # Locate raw file
            cand = input_dir / p_clean
            if not cand.exists():
                cand = input_dir / Path(p_rel).name
            if not cand.exists() and em_p.parent.parent:
                cand = em_p.parent.parent / p_rel
            if not cand.exists():
                print(f"ERROR: MISSING_RAW_FILE: Raw partition missing for root manifest: {p_rel}")
                return 2

            actual_sha, _, _ = _stream_file_sha256(cand)
            if raw_sha_expected and actual_sha != raw_sha_expected:
                print(f"ERROR: SOURCE_RAW_HASH_MISMATCH: {cand.name} actual '{actual_sha}' != expected '{raw_sha_expected}'")
                return 2

            # Check if this partition matches the requested exchange / stream / market
            p_exch = p_meta.get("exchange", "").lower()
            p_strm = p_meta.get("stream", "").lower()
            p_mkt = p_meta.get("market", "").upper()
            if exchange and p_exch != exchange.lower():
                continue
            if stream_filter and p_strm != stream_filter.lower():
                continue
            if market_filter and p_mkt != market_filter.upper():
                continue

            files_to_process.append(cand)

        # P8.1: Check for unsealed raw files in input_dir
        for fpath in raw_files:
            rel_f = str(fpath.relative_to(input_dir))
            if rel_f not in root_known_rel_paths and fpath.name not in root_known_names and f"raw/{rel_f}" not in root_known_rel_paths:
                print(f"ERROR: UNSEALED_SOURCE_PARTITION: unsealed file found in input_dir: {fpath.name} ({rel_f})")
                return 2
    else:
        files_to_process = list(raw_files)

    total_canonicalized = 0
    total_rejected = 0
    reject_reasons: dict[str, int] = {}
    source_lines_total = 0
    blank_lines = 0
    parse_failures = 0
    skipped_exchange = 0
    skipped_stream = 0
    skipped_market = 0
    eligible_records = 0

    dctx = zstandard.ZstdDecompressor()
    cctx = zstandard.ZstdCompressor(level=3)
    canonicalizer_commit = _detect_git_head()

    new_partition_entries: list[dict[str, Any]] = []

    for fpath in files_to_process:
        clean_stem = fpath.name
        for ext in (".ndjson.zst", ".jsonl.zst", ".jsonl", ".ndjson", ".zst"):
            if clean_stem.endswith(ext):
                clean_stem = clean_stem[:-len(ext)]
                break

        # Generate unique non-colliding partition name
        rel_parts = fpath.relative_to(input_dir).parts
        if len(rel_parts) > 1:
            clean_name = "_".join([p.replace("=", "_") for p in rel_parts])
            for ext in (".ndjson.zst", ".jsonl.zst", ".jsonl", ".ndjson", ".zst"):
                if clean_name.endswith(ext):
                    clean_name = clean_name[:-len(ext)]
                    break
        else:
            clean_name = clean_stem

        out_file = output_dir / f"canonical_{clean_name}.ndjson.zst"
        tmp_out = out_file.with_suffix(".tmp")
        file_canonicalized = 0
        file_rejected = 0
        file_min_ts: int | None = None
        file_max_ts: int | None = None
        detected_market: str = ""
        detected_stream: str = ""

        def iter_lines():
            if fpath.name.endswith(".zst"):
                with open(fpath, "rb") as fh:
                    with dctx.stream_reader(fh) as reader:
                        text = io.TextIOWrapper(reader, encoding="utf-8")
                        for line in text:
                            yield line
            else:
                with open(fpath, "r", encoding="utf-8") as fh:
                    for line in fh:
                        yield line

        with open(tmp_out, "wb") as out_fh:
            with cctx.stream_writer(out_fh) as writer:
                for line in iter_lines():
                    source_lines_total += 1
                    line = line.strip()
                    if not line:
                        blank_lines += 1
                        continue
                    try:
                        rec_dict = json.loads(line)
                    except Exception as parse_err:
                        parse_failures += 1
                        total_rejected += 1
                        file_rejected += 1
                        reason = type(parse_err).__name__
                        reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
                        continue

                    rec_exch = rec_dict.get("exchange")
                    if exchange and rec_exch and rec_exch.lower() != exchange.lower():
                        skipped_exchange += 1
                        continue

                    if "exchange" not in rec_dict and exchange:
                        rec_dict["exchange"] = exchange

                    rec_strm = rec_dict.get("stream")
                    if stream_filter and rec_strm != stream_filter:
                        skipped_stream += 1
                        continue

                    rec_mkt = rec_dict.get("market")
                    if market_filter and rec_mkt and rec_mkt.upper() != market_filter.upper():
                        skipped_market += 1
                        continue

                    eligible_records += 1
                    try:
                        canonical_obj = raw_record_to_canonical(rec_dict)
                        rec_data = canonical_obj.to_dict()
                        out_line = json.dumps(rec_data) + "\n"
                        writer.write(out_line.encode("utf-8"))
                        file_canonicalized += 1
                        total_canonicalized += 1

                        recv_ts = rec_data.get("receive_timestamp_ms")
                        if recv_ts is not None:
                            if file_min_ts is None or recv_ts < file_min_ts:
                                file_min_ts = recv_ts
                            if file_max_ts is None or recv_ts > file_max_ts:
                                file_max_ts = recv_ts
                        detected_market = rec_data.get("market", "")
                        detected_stream = rec_data.get("stream", "")
                    except Exception as err:
                        total_rejected += 1
                        file_rejected += 1
                        reason = type(err).__name__
                        reject_reasons[reason] = reject_reasons.get(reason, 0) + 1

        if file_canonicalized > 0:
            os.replace(tmp_out, out_file)
            src_sha, _, _ = _stream_file_sha256(fpath)
            canon_sha, _, _ = _stream_file_sha256(out_file)
            new_partition_entries.append({
                "canonical_file": out_file.name,
                "canonical_file_sha256": canon_sha,
                "source_file": str(fpath.relative_to(input_dir)),
                "source_file_sha256": src_sha,
                "exchange": exchange,
                "market": detected_market or market_filter or "UNKNOWN",
                "stream": detected_stream or stream_filter or "UNKNOWN",
                "canonical_count": file_canonicalized,
                "rejected_count": file_rejected,
                "min_timestamp_ms": file_min_ts or 0,
                "max_timestamp_ms": file_max_ts or 0,
                "canonical_schema_version": schema_version,
                "canonicalizer_commit": canonicalizer_commit,
            })
        else:
            if tmp_out.exists():
                tmp_out.unlink()

    status = "PASS" if total_rejected == 0 and total_canonicalized > 0 else ("PARTIAL_REJECTED" if total_canonicalized > 0 else "EMPTY")
    exit_code = 0 if status == "PASS" else (2 if status == "PARTIAL_REJECTED" else 1)

    source_nonblank = source_lines_total - blank_lines
    transform_report = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "schema_version": schema_version,
        "files_found": [str(f.relative_to(input_dir)) for f in raw_files],
        "status": status,
        "source_lines": source_lines_total,
        "blank_lines": blank_lines,
        "source_nonblank": source_nonblank,
        "parse_failures": parse_failures,
        "skipped_exchange": skipped_exchange,
        "skipped_stream": skipped_stream,
        "skipped_market": skipped_market,
        "eligible_records": eligible_records,
        "canonicalized_count": total_canonicalized,
        "rejected_count": total_rejected,
        "reject_reasons": reject_reasons,
    }
    report_fname = f"transform_report_{exchange}_{stream_filter}.json" if stream_filter else "transform_report.json"
    (output_dir / report_fname).write_text(json.dumps(transform_report, indent=2))
    (output_dir / "transform_report.json").write_text(json.dumps(transform_report, indent=2))

    # P14: Record DQ qualification artifact SHA if provided
    dq_qual_arg = getattr(args, "dq_qualification", None) or getattr(args, "dq_report", None)
    dq_qual_sha = None
    if dq_qual_arg:
        dq_qual_p = Path(dq_qual_arg)
        if not dq_qual_p.exists():
            print(f"ERROR: DQ qualification artifact not found: {dq_qual_p}")
            return 2
        try:
            dq_qual_data = json.loads(dq_qual_p.read_text(encoding="utf-8"))
            expected_dq_hash = compute_canonical_report_hash(dq_qual_data)
            if dq_qual_data.get("report_hash") != expected_dq_hash:
                print("ERROR: Corrupt DQ qualification report hash")
                return 2
            dq_qual_sha = dq_qual_data.get("qualification_sha256") or _file_sha256(dq_qual_p)
            if em_sha and dq_qual_data.get("epoch_manifest_sha256") and dq_qual_data.get("epoch_manifest_sha256") != em_sha:
                print(f"ERROR: EVIDENCE_CHAIN_MISMATCH: dq qualification epoch_manifest_sha256 '{dq_qual_data.get('epoch_manifest_sha256')}' != '{em_sha}'")
                return 2
        except Exception as e:
            print(f"ERROR: Invalid DQ qualification artifact: {e}")
            return 2

    # P17: Canonical Manifest Root
    canonical_manifest_path = output_dir / "canonical_manifest.json"
    existing_parts: dict[str, dict[str, Any]] = {}
    if canonical_manifest_path.exists():
        try:
            old_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
            for p in old_manifest.get("partitions", []):
                existing_parts[p["canonical_file"]] = p
        except Exception:
            pass

    for p in new_partition_entries:
        existing_parts[p["canonical_file"]] = p

    merged_parts = sorted(existing_parts.values(), key=lambda x: (x.get("min_timestamp_ms", 0), x.get("canonical_file", "")))
    cm_dict = {
        "schema_version": "2.1.0",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "canonicalizer_commit": canonicalizer_commit,
        "partitions_count": len(merged_parts),
        "partitions": merged_parts,
    }
    if em_sha:
        cm_dict["source_epoch_manifest_sha256"] = em_sha
    elif "old_manifest" in locals() and old_manifest.get("source_epoch_manifest_sha256"):
        cm_dict["source_epoch_manifest_sha256"] = old_manifest.get("source_epoch_manifest_sha256")

    if dq_qual_sha:
        cm_dict["dq_qualification_sha256"] = dq_qual_sha
    elif "old_manifest" in locals() and old_manifest.get("dq_qualification_sha256"):
        cm_dict["dq_qualification_sha256"] = old_manifest.get("dq_qualification_sha256")

    canonical_json = json.dumps(cm_dict, sort_keys=True, separators=(",", ":"))
    cm_dict["canonical_manifest_sha256"] = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    canonical_manifest_path.write_text(json.dumps(cm_dict, indent=2), encoding="utf-8")

    print(f"Transform complete: status={status} canonicalized={total_canonicalized} rejected={total_rejected}")
    return exit_code


def cmd_partition_dataset(args: argparse.Namespace) -> int:
    """Manifest-driven multi-file two-pass bounded-memory dataset partitioner."""
    from .prospective_dataset import (
        DqQualificationStatus,
        DqQualificationEvidence,
        _extract_clock_ts,
    )

    if getattr(args, "input_file", None):
        in_p = Path(args.input_file)
        if not in_p.exists():
            print(f"ERROR: Input file not found: {in_p}")
            return 1

    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        print(f"ERROR: Output directory sealed: {output_dir} already exists and is non-empty")
        return 2

    dq_report_str = getattr(args, "dq_report", None) or getattr(args, "qualification_evidence", None)
    if not dq_report_str:
        print("ERROR: --dq-report is required (DQ_EVIDENCE_REQUIRED)")
        return 2

    dq_report_path = Path(dq_report_str)
    if not dq_report_path.exists():
        print(f"ERROR: DQ report not found at {dq_report_path}")
        return 2

    try:
        dq_data = json.loads(dq_report_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR reading DQ report: {e}")
        return 2

    is_official = bool(getattr(args, "canonical_manifest", None))

    if is_official:
        # P5: Zero legacy official bypasses
        if dq_data.get("approved_policy") == "strict_phase4" or dq_data.get("auditor_version") == "1.0.0":
            print("ERROR: LEGACY_QUALIFICATION_REJECTED: strict_phase4 or auditor_version 1.0.0 is strictly rejected in official partition-dataset")
            return 2

        # P6: Deep audit report is strictly required
        deep_audit_arg = getattr(args, "deep_audit_report", None)
        if not deep_audit_arg:
            print("ERROR: MISSING_DEEP_AUDIT_REPORT: --deep-audit-report is strictly required in official partition-dataset")
            return 2
        deep_p = Path(deep_audit_arg)
        if not deep_p.exists():
            print(f"ERROR: MISSING_DEEP_AUDIT_REPORT: Deep audit report not found at {deep_p}")
            return 2
        actual_deep_sha = _file_sha256(deep_p)
        expected_deep_sha = dq_data.get("audit_report_sha256") or dq_data.get("report_hash")
        if expected_deep_sha and actual_deep_sha != expected_deep_sha:
            print(f"ERROR: AUDIT_REPORT_HASH_MISMATCH / DEEP_AUDIT_REPORT_MISMATCH: actual '{actual_deep_sha}' != qualification '{expected_deep_sha}'")
            return 2

        # P7: Source root manifest required and verified
        sm_arg = getattr(args, "source_manifest", None) or getattr(args, "epoch_manifest", None)
        if not sm_arg:
            print("ERROR: --source-manifest / --epoch-manifest is required for partition-dataset")
            return 2

        sm_path = Path(sm_arg)
        if not sm_path.exists():
            print(f"ERROR: Source manifest not found: {sm_path}")
            return 2

        sm_sha = _file_sha256(sm_path)
        try:
            from scripts.build_epoch_manifest import verify_epoch_manifest
            sm_json = verify_epoch_manifest(sm_path)
            epoch_manifest_sha = sm_json.get("epoch_manifest_sha256")
            collector_epoch = sm_json.get("collector_epoch")
            collector_run_id = sm_json.get("collector_run_id")
            runtime_commit = sm_json.get("runtime_commit")
            runtime_fingerprint = sm_json.get("runtime_fingerprint")
        except Exception as e:
            print(f"ERROR verifying epoch manifest: {e}")
            return 2

        # P7: Validate non-synthetic provenance derived from epoch root
        for p_field, p_val in [
            ("collector_epoch", collector_epoch),
            ("collector_run_id", collector_run_id),
            ("runtime_commit", runtime_commit),
            ("runtime_fingerprint", runtime_fingerprint),
        ]:
            if not p_val or str(p_val).lower() in ("unknown", "synthetic", "offline"):
                print(f"ERROR: INVALID_ROOT_PROVENANCE: {p_field} must be derived and non-synthetic from epoch manifest (got '{p_val}')")
                return 2

        if getattr(args, "source_epoch_id", None) and args.source_epoch_id != collector_epoch:
            print(f"ERROR: COLLECTOR_EPOCH_MISMATCH: claimed '{args.source_epoch_id}' != manifest '{collector_epoch}'")
            return 2
        if getattr(args, "source_run_id", None) and args.source_run_id != collector_run_id:
            print(f"ERROR: COLLECTOR_RUN_ID_MISMATCH: claimed '{args.source_run_id}' != manifest '{collector_run_id}'")
            return 2

        # Verify DQ report canonical hash and source manifest binding
        expected_report_hash = compute_canonical_report_hash(dq_data)
        actual_report_hash = dq_data.get("report_hash", "")
        if actual_report_hash != expected_report_hash:
            print(f"ERROR: DQ report hash mismatch: actual '{actual_report_hash}' != expected '{expected_report_hash}'")
            return 2

        claimed_src_hash = dq_data.get("source_manifest_hash", "")
        claimed_file_sha = dq_data.get("source_manifest_file_sha256")
        claimed_epoch_sha = dq_data.get("epoch_manifest_sha256", "")
        if claimed_file_sha and sm_sha != claimed_file_sha:
            print(f"ERROR: DQ_SOURCE_MISMATCH: claimed source file sha '{claimed_file_sha}' != actual '{sm_sha}'")
            return 2
        if claimed_src_hash and claimed_src_hash not in (sm_sha, epoch_manifest_sha):
            print(f"ERROR: DQ_SOURCE_MISMATCH: claimed '{claimed_src_hash}' does not match source manifest '{sm_path}'")
            return 2

        # P4.1: In official mode, only DQ_PASS is permitted (DQ_DEGRADED is rejected)
        status_str = dq_data.get("status")
        try:
            status = DqQualificationStatus(status_str)
        except Exception:
            status = None
        if status != DqQualificationStatus.DQ_PASS:
            print(f"ERROR: DQ status '{status_str}' is not acceptable in official partition-dataset (only DQ_PASS permitted)")
            return 2
    else:
        # Ad-hoc / input_file mode
        actual_deep_sha = dq_data.get("audit_report_sha256", "")
        if getattr(args, "deep_audit_report", None):
            deep_p = Path(args.deep_audit_report)
            if not deep_p.exists():
                print(f"ERROR: Deep audit report not found at {deep_p}")
                return 2
            actual_deep_sha = _file_sha256(deep_p)
            expected_deep_sha = dq_data.get("audit_report_sha256") or dq_data.get("report_hash")
            if expected_deep_sha and actual_deep_sha != expected_deep_sha:
                print(f"ERROR: AUDIT_REPORT_HASH_MISMATCH / DEEP_AUDIT_REPORT_MISMATCH: actual '{actual_deep_sha}' != qualification '{expected_deep_sha}'")
                return 2

        sm_arg = getattr(args, "source_manifest", None) or getattr(args, "epoch_manifest", None)
        sm_sha = ""
        epoch_manifest_sha = ""
        if sm_arg:
            sm_path = Path(sm_arg)
            if sm_path.exists():
                sm_sha = _file_sha256(sm_path)
                try:
                    sm_raw = json.loads(sm_path.read_text(encoding="utf-8"))
                    epoch_manifest_sha = sm_raw.get("epoch_manifest_sha256") or sm_sha
                except Exception:
                    epoch_manifest_sha = sm_sha
        if not epoch_manifest_sha:
            epoch_manifest_sha = dq_data.get("epoch_manifest_sha256", dq_data.get("source_manifest_hash", ""))
        if sm_arg and sm_sha:
            claimed_src_hash = dq_data.get("source_manifest_hash", "")
            claimed_file_sha = dq_data.get("source_manifest_file_sha256")
            if claimed_file_sha and sm_sha != claimed_file_sha:
                print(f"ERROR: DQ_SOURCE_MISMATCH: claimed source file sha '{claimed_file_sha}' != actual '{sm_sha}'")
                return 2
            if claimed_src_hash and claimed_src_hash not in (sm_sha, epoch_manifest_sha):
                print(f"ERROR: DQ_SOURCE_MISMATCH: claimed '{claimed_src_hash}' does not match source manifest '{sm_path}'")
                return 2
        collector_epoch = getattr(args, "source_epoch_id", None) or dq_data.get("collector_epoch", "synthetic")
        collector_run_id = getattr(args, "source_run_id", None) or dq_data.get("collector_run_id", "offline")
        runtime_commit = dq_data.get("runtime_commit", _detect_git_head())
        runtime_fingerprint = dq_data.get("runtime_fingerprint", "fp-default")
        claimed_src_hash = dq_data.get("source_manifest_hash", "")

        expected_report_hash = compute_canonical_report_hash(dq_data)
        actual_report_hash = dq_data.get("report_hash", "")
        if actual_report_hash != expected_report_hash:
            print(f"ERROR: DQ report hash mismatch: actual '{actual_report_hash}' != expected '{expected_report_hash}'")
            return 2

        status_str = dq_data.get("status")
        try:
            status = DqQualificationStatus(status_str)
        except Exception:
            status = None
        if status not in (DqQualificationStatus.DQ_PASS, DqQualificationStatus.DQ_DEGRADED):
            print(f"ERROR: DQ status '{status_str}' is not acceptable")
            return 2
        for k in ("audit_code_commit", "source_manifest_hash", "criteria_version"):
            if dq_data.get(k) == "unknown":
                print(f"ERROR: DQ report field {k} cannot be 'unknown'")
                return 2

    # Provenance commits
    deep_dq_auditor_commit = dq_data.get("auditor_commit") or dq_data.get("audit_code_commit") or "unknown"
    dataset_builder_commit = getattr(args, "builder_commit", None) or _detect_git_head()
    canonicalizer_commit = getattr(args, "canonicalizer_commit", None)
    actual_dq_sha = dq_data.get("qualification_sha256") or _file_sha256(dq_report_path)

    input_files: list[Path] = []
    cm_data: dict[str, Any] = {}
    if getattr(args, "canonical_manifest", None):
        cm_path = Path(args.canonical_manifest)
        if not cm_path.exists():
            print(f"ERROR: Canonical manifest not found: {cm_path}")
            return 2
        try:
            cm_data = json.loads(cm_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"ERROR: Corrupt canonical manifest: {e}")
            return 2

        # P15: Evidence chain consistency check
        cm_source_epoch_sha = cm_data.get("source_epoch_manifest_sha256")
        dq_epoch_sha = dq_data.get("epoch_manifest_sha256") or dq_data.get("source_manifest_hash")
        actual_epoch_sha = epoch_manifest_sha

        if not cm_source_epoch_sha or not dq_epoch_sha or cm_source_epoch_sha != actual_epoch_sha or dq_epoch_sha != actual_epoch_sha:
            print(f"ERROR: EVIDENCE_CHAIN_MISMATCH: canonical source root '{cm_source_epoch_sha}', DQ root '{dq_epoch_sha}', and actual root '{actual_epoch_sha}' do not match")
            return 2

        if cm_data.get("dq_qualification_sha256") and cm_data.get("dq_qualification_sha256") != actual_dq_sha:
            print(f"ERROR: EVIDENCE_CHAIN_MISMATCH: canonical dq_qualification_sha256 '{cm_data.get('dq_qualification_sha256')}' != actual DQ report sha '{actual_dq_sha}'")
            return 2

        # P17 / Table 5.2 #12: Verify internal canonical_manifest_sha256
        claimed_cm_sha = cm_data.get("canonical_manifest_sha256")
        if claimed_cm_sha:
            cm_copy = {k: v for k, v in cm_data.items() if k != "canonical_manifest_sha256"}
            calc_sha1 = hashlib.sha256(json.dumps(cm_copy, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            calc_sha2 = hashlib.sha256(json.dumps(cm_copy, sort_keys=True).encode("utf-8")).hexdigest()
            if claimed_cm_sha not in (calc_sha1, calc_sha2):
                print(f"ERROR: CANONICAL_MANIFEST_HASH_MISMATCH: actual '{calc_sha1}' != claimed '{claimed_cm_sha}'")
                return 2

        if not canonicalizer_commit:
            canonicalizer_commit = cm_data.get("canonicalizer_commit")

        parts = cm_data.get("partitions", [])

        # Table 5.2 #11: Verify canonical partition file hashes
        for p in parts:
            p_file = cm_path.parent / p["canonical_file"]
            if not p_file.exists():
                print(f"ERROR: Canonical partition file missing: {p_file}")
                return 2
            expected_cf_sha = p.get("canonical_file_sha256")
            if expected_cf_sha:
                actual_cf_sha = _file_sha256(p_file)
                if actual_cf_sha != expected_cf_sha:
                    print(f"ERROR: CANONICAL_PARTITION_HASH_MISMATCH: {p_file.name} actual '{actual_cf_sha}' != manifest '{expected_cf_sha}'")
                    return 2

        if getattr(args, "exchange", None):
            parts = [p for p in parts if p.get("exchange", "").lower() == args.exchange.lower()]
        if getattr(args, "market", None):
            parts = [p for p in parts if p.get("market", "").upper() == args.market.upper()]
        if getattr(args, "stream", None):
            parts = [p for p in parts if p.get("stream", "").lower() == args.stream.lower()]

        if not parts:
            print("ERROR: No matching partitions found in canonical manifest for given filters")
            return 2

        # P15: Single series enforcement
        distinct_series = {(p.get("exchange", "").lower(), p.get("market", "").upper(), p.get("stream", "").lower()) for p in parts}
        if len(distinct_series) > 1:
            print(f"ERROR: AMBIGUOUS_RESEARCH_SERIES: Partitions span multiple series ({distinct_series}). Explicit --exchange, --market, --stream filters required.")
            return 2

        parts.sort(key=lambda x: (x.get("min_timestamp_ms", 0), x.get("canonical_file", "")))
        for p in parts:
            input_files.append(cm_path.parent / p["canonical_file"])
    elif getattr(args, "input_file", None):
        in_p = Path(args.input_file)
        if not in_p.exists():
            print(f"ERROR: Input file not found: {in_p}")
            return 2
        input_files.append(in_p)
        cm_data = {"canonical_manifest_sha256": _file_sha256(in_p)}
    else:
        print("ERROR: Either --canonical-manifest or --input-file must be provided")
        return 2

    if not canonicalizer_commit:
        canonicalizer_commit = "standalone-canonicalizer"

    clock_arg = getattr(args, "clock", "receive_wall_clock")
    train_frac = getattr(args, "train_frac", 0.60)
    val_frac = getattr(args, "val_frac", 0.20)
    purge_window_ms = getattr(args, "purge_window_ms", 900_000)

    # -------------------------------------------------------------------------
    # TWO-PASS BOUNDED-MEMORY STREAMING PARTITIONER
    # PASS 1: Count total records, check temporal order, derive split boundaries
    # -------------------------------------------------------------------------
    dctx = zstandard.ZstdDecompressor()
    cctx = zstandard.ZstdCompressor(level=3)

    total_n = 0
    for fpath in input_files:
        with open(fpath, "rb") as fh:
            with dctx.stream_reader(fh) as reader:
                text_io = io.TextIOWrapper(reader, encoding="utf-8")
                for line in text_io:
                    if line.strip():
                        total_n += 1

    if total_n == 0:
        print("ERROR: No records loaded. Cannot partition empty dataset.")
        return 1

    train_end_idx = int(total_n * train_frac)
    val_target_count = int(total_n * val_frac)

    prev_ts: int | None = None
    train_end_ts: int | None = None
    val_start_ts: int | None = None
    val_start_idx: int | None = None
    val_end_ts: int | None = None
    val_end_idx: int | None = None
    holdout_start_ts: int | None = None
    holdout_start_idx: int | None = None

    first_record_sample: dict[str, Any] = {}
    curr_idx = 0

    for fpath in input_files:
        with open(fpath, "rb") as fh:
            with dctx.stream_reader(fh) as reader:
                text_io = io.TextIOWrapper(reader, encoding="utf-8")
                for line in text_io:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if not first_record_sample:
                        first_record_sample = rec

                    ts = _extract_clock_ts(rec, clock_arg)
                    if prev_ts is not None and ts < prev_ts:
                        print(
                            f"ERROR: Clock reversal detected at record {curr_idx}: "
                            f"prev_ts={prev_ts} > curr_ts={ts} ({clock_arg})"
                        )
                        return 2
                    prev_ts = ts

                    # Track boundaries
                    if curr_idx == train_end_idx - 1:
                        train_end_ts = ts
                        val_start_ts = train_end_ts + purge_window_ms

                    if curr_idx >= train_end_idx and val_start_idx is None:
                        if val_start_ts is not None and ts >= val_start_ts:
                            val_start_idx = curr_idx
                            val_end_idx = min(total_n, val_start_idx + val_target_count)

                    if val_end_idx is not None and curr_idx == val_end_idx - 1:
                        val_end_ts = ts
                        holdout_start_ts = val_end_ts + purge_window_ms

                    if val_end_idx is not None and curr_idx >= val_end_idx and holdout_start_idx is None:
                        if holdout_start_ts is not None and ts >= holdout_start_ts:
                            holdout_start_idx = curr_idx

                    curr_idx += 1

    if val_start_idx is None:
        val_start_idx = total_n
    if val_end_idx is None:
        val_end_idx = total_n
    if holdout_start_idx is None:
        holdout_start_idx = total_n

    train_records_count = train_end_idx
    embargo1_dropped = val_start_idx - train_end_idx
    val_records_count = val_end_idx - val_start_idx
    embargo2_dropped = holdout_start_idx - val_end_idx
    holdout_records_count = total_n - holdout_start_idx

    # -------------------------------------------------------------------------
    # PASS 2: Stream records to staged outputs (train, validation, holdout)
    # -------------------------------------------------------------------------
    staging_id = uuid.uuid4().hex
    staging_dir = output_dir.parent / f"{output_dir.name}.building.{staging_id}"
    staging_dir.mkdir(parents=True, exist_ok=True)

    train_path = staging_dir / "train.ndjson.zst"
    val_path = staging_dir / "validation.ndjson.zst"
    holdout_path = staging_dir / "holdout.ndjson.zst"

    start_ts_by_role: dict[str, int] = {}
    end_ts_by_role: dict[str, int] = {}

    try:
        with open(train_path, "wb") as t_fh, open(val_path, "wb") as v_fh, open(holdout_path, "wb") as h_fh:
            with cctx.stream_writer(t_fh) as t_writer, cctx.stream_writer(v_fh) as v_writer, cctx.stream_writer(h_fh) as h_writer:
                idx = 0
                for fpath in input_files:
                    with open(fpath, "rb") as fh:
                        with dctx.stream_reader(fh) as reader:
                            text_io = io.TextIOWrapper(reader, encoding="utf-8")
                            for line in text_io:
                                line = line.strip()
                                if not line:
                                    continue
                                line_bytes = line.encode("utf-8") + b"\n"
                                rec = json.loads(line)
                                ts = _extract_clock_ts(rec, clock_arg)

                                if idx < train_end_idx:
                                    t_writer.write(line_bytes)
                                    if "TRAIN" not in start_ts_by_role:
                                        start_ts_by_role["TRAIN"] = ts
                                    end_ts_by_role["TRAIN"] = ts
                                elif idx < val_start_idx:
                                    pass  # embargo 1
                                elif idx < val_end_idx:
                                    v_writer.write(line_bytes)
                                    if "VALIDATION" not in start_ts_by_role:
                                        start_ts_by_role["VALIDATION"] = ts
                                    end_ts_by_role["VALIDATION"] = ts
                                elif idx < holdout_start_idx:
                                    pass  # embargo 2
                                else:
                                    h_writer.write(line_bytes)
                                    if "HOLDOUT" not in start_ts_by_role:
                                        start_ts_by_role["HOLDOUT"] = ts
                                    end_ts_by_role["HOLDOUT"] = ts

                                idx += 1

        train_sha = _file_sha256(train_path)
        val_sha = _file_sha256(val_path)
        holdout_sha = _file_sha256(holdout_path)

        partition_meta = {
            "TRAIN": {
                "role": "TRAIN",
                "record_count": train_records_count,
                "start_receive_ms": start_ts_by_role.get("TRAIN", 0),
                "end_receive_ms": end_ts_by_role.get("TRAIN", 0),
                "sha256": train_sha,
                "file_name": "train.ndjson.zst",
            },
            "VALIDATION": {
                "role": "VALIDATION",
                "record_count": val_records_count,
                "start_receive_ms": start_ts_by_role.get("VALIDATION", 0),
                "end_receive_ms": end_ts_by_role.get("VALIDATION", 0),
                "sha256": val_sha,
                "file_name": "validation.ndjson.zst",
            },
            "HOLDOUT": {
                "role": "HOLDOUT",
                "record_count": holdout_records_count,
                "start_receive_ms": start_ts_by_role.get("HOLDOUT", 0),
                "end_receive_ms": end_ts_by_role.get("HOLDOUT", 0),
                "sha256": holdout_sha,
                "file_name": "holdout.ndjson.zst",
            },
        }

        content_hashes = [f"TRAIN:{train_sha}", f"VALIDATION:{val_sha}", f"HOLDOUT:{holdout_sha}"]
        canonical_content_hash = hashlib.sha256(";".join(sorted(content_hashes)).encode("utf-8")).hexdigest()

        partition_config_str = json.dumps({
            "purge_window_ms": purge_window_ms,
            "train_frac": train_frac,
            "val_frac": val_frac,
            "partition_clock": clock_arg,
        }, sort_keys=True)
        partition_config_hash = hashlib.sha256(partition_config_str.encode("utf-8")).hexdigest()

        canon_schema_ver = first_record_sample.get("schema_version", "2.0.0")

        id_source = json.dumps({
            "canonical_content_hash": canonical_content_hash,
            "canonical_schema_version": canon_schema_ver,
            "canonicalizer_commit": canonicalizer_commit,
            "dataset_builder_commit": dataset_builder_commit,
            "dq_criteria_version": dq_data.get("criteria_version", ""),
            "dq_report_hash": actual_report_hash,
            "partition_config_hash": partition_config_hash,
            "source_manifest_hash": claimed_src_hash,
        }, sort_keys=True)
        dataset_id = hashlib.sha256(id_source.encode("utf-8")).hexdigest()

        manifest_dict = {
            "dataset_id": dataset_id,
            "exchange": first_record_sample.get("exchange", getattr(args, "exchange", "unknown")),
            "market": first_record_sample.get("market", getattr(args, "market", "unknown")),
            "total_records": total_n,
            "source_record_count": total_n,
            "train_records": train_records_count,
            "validation_records": val_records_count,
            "holdout_records": holdout_records_count,
            "embargo1_dropped": embargo1_dropped,
            "embargo2_dropped": embargo2_dropped,
            "purge_window_ms": purge_window_ms,
            "dq_status": dq_data.get("status", "DQ_PASS"),
            "partitions": partition_meta,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            # P16: 10 official provenance fields
            "source_epoch_id": collector_epoch,
            "source_run_id": collector_run_id,
            "source_runtime_commit": runtime_commit,
            "source_runtime_fingerprint": runtime_fingerprint,
            "epoch_manifest_sha256": epoch_manifest_sha,
            "deep_dq_report_sha256": actual_deep_sha,
            "dq_qualification_sha256": actual_dq_sha,
            "canonical_manifest_sha256": cm_data.get("canonical_manifest_sha256", ""),
            "canonicalizer_commit": canonicalizer_commit,
            "dataset_builder_commit": dataset_builder_commit,
            # Supporting metadata
            "deep_dq_auditor_commit": deep_dq_auditor_commit,
            "source_manifest_hash": epoch_manifest_sha,
            "source_manifest_sha256": epoch_manifest_sha,
            "dq_report_hash": actual_report_hash,
            "dq_criteria_version": dq_data.get("criteria_version", ""),
            "canonical_schema_version": canon_schema_ver,
            "partition_config_hash": partition_config_hash,
        }

        # P16: Official dataset metadata validation (no synthetic, offline, or unknown)
        if is_official:
            for req_prov in (
                "source_epoch_id", "source_run_id", "source_runtime_commit", "source_runtime_fingerprint",
                "epoch_manifest_sha256", "deep_dq_report_sha256", "dq_qualification_sha256",
                "canonical_manifest_sha256", "canonicalizer_commit", "dataset_builder_commit"
            ):
                prov_val = manifest_dict.get(req_prov)
                if not prov_val or str(prov_val).lower() in ("unknown", "synthetic", "offline"):
                    print(f"ERROR: INVALID_DATASET_PROVENANCE: {req_prov} has invalid value '{prov_val}' in official mode")
                    return 2

        manifest_path = staging_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest_dict, indent=2), encoding="utf-8")

        staging_dir.rename(output_dir)
        print(f"Partitioned: train={train_records_count} val={val_records_count} holdout={holdout_records_count}")
        print(f"Manifest: {output_dir / 'manifest.json'}")
        return 0

    except Exception as e:
        import shutil
        shutil.rmtree(staging_dir, ignore_errors=True)
        print(f"ERROR during dataset partitioning: {e}")
        return 2


def cmd_build_epoch_manifest(args: argparse.Namespace) -> int:
    from scripts.build_epoch_manifest import build_epoch_manifest
    out_p = Path(args.output) if args.output else (Path(args.epoch_dir) / "manifests" / "epoch_manifest.json")
    contract_p = Path(args.contract) if args.contract else None
    res = build_epoch_manifest(
        epoch_dir=Path(args.epoch_dir),
        contract_path=contract_p,
        output_path=out_p,
        strict=args.strict,
    )
    if args.strict and not res.get("sealed_complete"):
        print(f"ERROR: Epoch manifest incomplete: {res.get('missing_items')}")
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Microstructure Research & Paper CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # verify-ledger
    p_vl = sub.add_parser("verify-ledger", help="Verify cryptographic hash-chain of experiment ledger")
    p_vl.add_argument("--ledger", default="evidence/research/governed_experiment_ledger.json")

    # power-plan
    p_pp = sub.add_parser("power-plan", help="Compute sample size requirements")
    p_pp.add_argument("--sharpe", type=float, default=0.05, help="Target Sharpe per observation")
    p_pp.add_argument("--alpha", type=float, default=0.01, help="Significance level")
    p_pp.add_argument("--power", type=float, default=0.80, help="Statistical power (1-beta)")
    p_pp.add_argument("--rho", type=float, default=0.20, help="Autocorrelation coefficient")

    # run-synthetic-sim
    p_sim = sub.add_parser("run-synthetic-sim", help="Run deterministic synthetic market simulation")
    p_sim.add_argument("--count", type=int, default=100, help="Number of orderbook events")

    # audit-quality
    p_aq = sub.add_parser("audit-quality", help="Run data quality audit on raw soak archive directory")
    p_aq.add_argument("--input-dir", required=True, help="Directory containing raw soak archives")
    p_aq.add_argument("--report-out", default="reports/data_quality_report.json", help="Output report path")

    # structural-audit (P1 alias)
    p_sa = sub.add_parser("structural-audit", help="Run structural audit on raw soak archive directory")
    p_sa.add_argument("--input-dir", required=True, help="Directory containing raw soak archives")
    p_sa.add_argument("--report-out", default="reports/structural_audit_report.json", help="Output report path")

    # deep-dq-audit (P1 authoritative)
    p_dda = sub.add_parser("deep-dq-audit", help="Run authoritative deep DQ audit on 72h soak epoch")
    p_dda.add_argument("--epoch-dir", required=True, help="Directory of 72h soak epoch")
    p_dda.add_argument("--report-out", default="reports/deep_dq_report.json", help="Output report path")
    p_dda.add_argument("--contract", "--epoch-contract", default=None, help="Optional run contract path")
    p_dda.add_argument("--epoch-manifest", default=None, help="Epoch manifest path for root binding")

    # build-epoch-manifest
    p_bem = sub.add_parser("build-epoch-manifest", help="Build sealed epoch root manifest")
    p_bem.add_argument("--epoch-dir", required=True, help="Directory of 72h soak epoch")
    p_bem.add_argument("--contract", "--epoch-contract", default=None, help="Run contract path")
    p_bem.add_argument("--output", "-o", default=None, help="Output epoch_manifest.json path")
    p_bem.add_argument("--strict", action="store_true", default=False, help="Fail if incomplete")

    # transform-canonical
    p_tc = sub.add_parser("transform-canonical", help="Transform raw exchange data to canonical format")
    p_tc.add_argument("--input-dir", required=True, help="Input directory with raw data")
    p_tc.add_argument("--output-dir", required=True, help="Output directory for canonical data")
    p_tc.add_argument("--schema-version", default="2.0.0", help="Schema version")
    p_tc.add_argument("--exchange", required=False, default="bithumb", help="Exchange type")
    p_tc.add_argument("--stream", required=False, help="Filter by stream (orderbook, trade, ticker)")
    p_tc.add_argument("--market", required=False, help="Filter by market (e.g. KRW-BTC)")
    p_tc.add_argument("--epoch-manifest", "--source-manifest", help="Path to epoch root manifest JSON for TOCTOU verification")
    p_tc.add_argument("--dq-qualification", "--dq-report", help="Path to DQ qualification evidence artifact")

    # partition-dataset
    p_pd = sub.add_parser("partition-dataset", help="Temporally partition a canonical dataset with embargo windows")
    p_pd.add_argument("--input-file", help="Input canonical ndjson.zst file")
    p_pd.add_argument("--canonical-manifest", help="Input canonical manifest JSON containing multi-file partitions")
    p_pd.add_argument("--output-dir", required=True, help="Output directory for partitioned dataset")
    p_pd.add_argument("--dq-report", required=False, help="DQ report evidence file")
    p_pd.add_argument("--source-manifest", "--epoch-manifest", help="Path to source or epoch root manifest JSON to verify provenance")
    p_pd.add_argument("--deep-audit-report", help="Path to deep audit report to verify against qualification artifact")
    p_pd.add_argument("--exchange", help="Filter exchange (e.g. bithumb)")
    p_pd.add_argument("--market", help="Filter market (e.g. KRW-BTC)")
    p_pd.add_argument("--stream", help="Filter stream (e.g. orderbook)")
    p_pd.add_argument("--train-frac", type=float, default=0.60, help="Train fraction")
    p_pd.add_argument("--val-frac", type=float, default=0.20, help="Validation fraction")
    p_pd.add_argument("--source-epoch-id", default=None, help="Source epoch ID")
    p_pd.add_argument("--source-run-id", default=None, help="Source run ID")
    p_pd.add_argument("--clock", default="receive_wall_clock", help="Partition clock domain")
    p_pd.add_argument("--dataset-name", default=None, help="Optional dataset name")
    p_pd.add_argument("--qualification-evidence", dest="dq_report", help="Alias for --dq-report")
    p_pd.add_argument("--purge-window-ms", type=int, default=900_000, help="Embargo purge window in ms")
    p_pd.add_argument("--builder-commit", default=None, help="Explicit builder commit")
    p_pd.add_argument("--canonicalizer-commit", default=None, help="Explicit canonicalizer commit")

    # dq-qualify
    p_dq = sub.add_parser("dq-qualify", help="Produce cryptographic DQ qualification evidence artifact")
    p_dq.add_argument("--audit-report", required=True, help="Input quality audit report JSON")
    p_dq.add_argument("--out", required=True, help="Output qualification evidence artifact path")
    p_dq.add_argument("--source-manifest", "--epoch-manifest", help="Path to source or epoch manifest to bind")
    p_dq.add_argument("--source-manifest-hash", help="Explicit source manifest hash")
    p_dq.add_argument("--policy", default="strict_v1", help="Approved policy name")
    p_dq.add_argument("--commit", default="HEAD", help="Code commit")
    p_dq.add_argument("--auditor-version", default="v9.1.0-offline", help="Auditor version")
    p_dq.add_argument("--criteria-version", default="v1-strict", help="Criteria version")
    p_dq.add_argument("--strict", action="store_true", default=False, help="Enforce strict DQ criteria")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "verify-ledger":
        return cmd_verify_ledger(args)
    elif args.command == "power-plan":
        return cmd_power_plan(args)
    elif args.command == "run-synthetic-sim":
        return cmd_run_synthetic_sim(args)
    elif args.command in ("audit-quality", "structural-audit"):
        return cmd_audit_quality(args)
    elif args.command == "deep-dq-audit":
        from scripts.audit_72h_soak import SoakAuditor72H
        epoch_dir = Path(args.epoch_dir)
        contract_p = Path(args.contract) if getattr(args, "contract", None) else None
        epoch_manifest_p = Path(args.epoch_manifest) if getattr(args, "epoch_manifest", None) else None
        auditor = SoakAuditor72H(
            epoch_dir,
            contract_path=contract_p,
            epoch_manifest_path=epoch_manifest_p,
            mode="official",
            strict=True,
        )
        report = auditor.audit()
        out_path = Path(args.report_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Deep DQ audit complete: status={report['status']}")
        if report["status"] != "DQ_PASS_ELIGIBLE":
            for b in report.get("blockers", []):
                print(f"BLOCKER: {b}", file=sys.stderr)
            for e in report.get("errors", []):
                print(f"ERROR: {e}", file=sys.stderr)
        return 0 if report["status"] == "DQ_PASS_ELIGIBLE" else 2
    elif args.command == "build-epoch-manifest":
        return cmd_build_epoch_manifest(args)
    elif args.command == "transform-canonical":
        return cmd_transform_canonical(args)
    elif args.command == "partition-dataset":
        return cmd_partition_dataset(args)
    elif args.command == "dq-qualify":
        return cmd_dq_qualify(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
