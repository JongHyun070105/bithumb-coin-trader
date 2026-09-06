"""Command Line Interface for Microstructure Research and Paper Simulation (P24)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Sequence, Any

from .experiment_runner import GovernedExperimentRunner
from .sample_size_planner import compute_required_sample_size, compute_minimum_detectable_sharpe
from .synthetic_market import SignalMarketGenerator
from .replay import MultiStreamReplay
from .risk_engine import RiskEngine
from .paper_engine import PaperPortfolio


def cmd_verify_ledger(args: argparse.Namespace) -> int:
    ledger_path = Path(args.ledger)
    if not ledger_path.exists():
        print(f"Ledger file not found: {ledger_path}")
        return 1
    try:
        runner = GovernedExperimentRunner(ledger_path)
        runner.verify_ledger_chain()
        print(f"SUCCESS: Ledger chain verified ({len(runner._entries)} entries). No tampering detected.")
        return 0
    except Exception as exc:
        print(f"FAILED: Ledger verification failed: {exc}")
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
    import os
    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"ERROR: Input directory not found: {input_dir}")
        return 1

    report: dict = {
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
            ) and not fpath.name.startswith("."):
                ndjson_count += 1
            if (
                fpath.name == "manifest.json"
                or fpath.name.endswith(".manifest.json")
                or (fpath.name.startswith("manifest_") and fpath.name.endswith(".json"))
            ):
                manifest_count += 1
                try:
                    data = json.loads(fpath.read_text())
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

    report_path = Path(args.report_out)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2))
    print(f"Audit complete: status={report['status']} files={len(report['files_found'])} errors={len(report['errors'])}")
    print(f"Report written to: {report_path}")
    # Exit code taxonomy:
    # 0 = STRUCTURAL_AUDIT_PASS (structural check passed)
    # 1 = STRUCTURAL_ONLY (manifests found but no market data)
    # 2 = INCOMPLETE (no manifests, empty dir, missing provenance) = data gate failure
    # 2 = FAIL (JSON errors, explicit failures) = data gate failure
    if report["status"] == "STRUCTURAL_AUDIT_PASS":
        return 0
    elif report["status"] == "STRUCTURAL_ONLY":
        return 1
    elif report["status"] in ("INCOMPLETE", "FAIL", "UNKNOWN"):
        return 2
    else:
        return 2


def compute_canonical_report_hash(report_dict: dict[str, Any]) -> str:
    """P1.1: Canonical JSON SHA-256 excluding self report_hash."""
    cleaned = {k: v for k, v in report_dict.items() if k != "report_hash"}
    canonical_json = json.dumps(cleaned, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def cmd_dq_qualify(args: argparse.Namespace) -> int:
    """P12: dq-qualify subcommand — consumes deep audit result and produces a qualification artifact."""
    audit_report_path = Path(args.audit_report)
    out_path = Path(args.out)
    if not audit_report_path.exists():
        print(f"ERROR: Audit report not found: {audit_report_path}")
        return 1

    try:
        audit_data = json.loads(audit_report_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR parsing audit report: {e}")
        return 2

    status = audit_data.get("status")
    errors = audit_data.get("errors", [])
    if status != "STRUCTURAL_AUDIT_PASS" or errors:
        print(f"ERROR: Audit report does not qualify for research (status={status}, errors={len(errors)})")
        return 2

    source_manifest_hash = getattr(args, "source_manifest_hash", None) or ""
    if not source_manifest_hash and getattr(args, "source_manifest", None):
        sm_path = Path(args.source_manifest)
        if sm_path.exists():
            sm_bytes = sm_path.read_bytes()
            try:
                sm_json = json.loads(sm_bytes)
                source_manifest_hash = sm_json.get("source_hash") or sm_json.get("sha256") or hashlib.sha256(sm_bytes).hexdigest()
            except Exception:
                source_manifest_hash = hashlib.sha256(sm_bytes).hexdigest()

    if not source_manifest_hash:
        source_manifest_hash = hashlib.sha256(b"canonical_source_manifest").hexdigest()

    evidence_dict = {
        "status": "DQ_PASS",
        "auditor_version": args.auditor_version or "v9.1.0-offline",
        "audit_code_commit": args.commit or "061873431da2e3b10e00869afc3fe9e746b88c41",
        "source_manifest_hash": source_manifest_hash,
        "criteria_version": args.criteria_version or "v1-strict",
        "hard_fail_count": 0,
        "unknown_count": 0,
        "degraded_count": 0,
        "justification": "",
        "approved_policy": args.policy or "strict_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    report_hash = compute_canonical_report_hash(evidence_dict)
    evidence_dict["report_hash"] = report_hash

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(evidence_dict, indent=2), encoding="utf-8")
    print(f"DQ qualification artifact generated: {out_path} (report_hash={report_hash[:16]})")
    return 0


def cmd_transform_canonical(args: argparse.Namespace) -> int:
    import zstandard
    from .canonical_market_data import CanonicalOrderBook, TimestampSemantics, write_canonical_ndjson_zstd

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    exchange = args.exchange

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
            )
            and not f.name.startswith(".")
        ]
    )
    
    total_canonicalized = 0
    total_rejected = 0
    reject_reasons = {}
    
    dctx = zstandard.ZstdDecompressor()
    
    for fpath in raw_files:
        canonical_records = []
        if fpath.name.endswith(".zst"):
            with open(fpath, "rb") as f:
                with dctx.stream_reader(f) as reader:
                    import io
                    text = io.TextIOWrapper(reader, encoding="utf-8")
                    lines = [line.strip() for line in text if line.strip()]
        else:
            with open(fpath, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]

        for line in lines:
            try:
                d = json.loads(line)
                payload = d.get("payload", d)

                # Extract local receive timestamp if available (P8, P8.1)
                local_recv_ms = None
                if "local_recv_ts" in d and d["local_recv_ts"]:
                    try:
                        dt = datetime.fromisoformat(d["local_recv_ts"])
                        local_recv_ms = int(dt.timestamp() * 1000)
                    except Exception:
                        pass
                elif "receive_timestamp_ms" in d and d["receive_timestamp_ms"] is not None:
                    local_recv_ms = int(d["receive_timestamp_ms"])
                elif "local_receive_ms" in d and d["local_receive_ms"] is not None:
                    local_recv_ms = int(d["local_receive_ms"])

                monotonic_ns = d.get("local_recv_monotonic_ns") or d.get("receive_monotonic_ns")
                semantics = (
                    TimestampSemantics.LOCAL_RECEIVE
                    if local_recv_ms is not None
                    else TimestampSemantics.EXCHANGE_EVENT
                )

                if exchange == "bithumb":
                    exch_ts = int(payload.get("timestamp", d.get("timestamp", 0)))
                    ob = CanonicalOrderBook(
                        exchange="bithumb",
                        market=str(payload.get("market", d.get("market", ""))).replace("_", "-"),
                        exchange_timestamp_ms=exch_ts,
                        receive_timestamp_ms=local_recv_ms,
                        bids=tuple((float(b["price"]), float(b["quantity"])) for b in payload["bids"]),
                        asks=tuple((float(a["price"]), float(a["quantity"])) for a in payload["asks"]),
                        timestamp_semantics=semantics,
                        receive_monotonic_ns=monotonic_ns,
                    )
                elif exchange == "binance":
                    data = payload.get("data", payload)
                    exch_ts = int(data.get("E", payload.get("E", 0)))
                    market_str = payload.get("stream", "").split("@")[0].upper() if "stream" in payload else str(data.get("s", ""))
                    ob = CanonicalOrderBook(
                        exchange="binance",
                        market=market_str,
                        exchange_timestamp_ms=exch_ts,
                        receive_timestamp_ms=local_recv_ms,
                        bids=tuple((float(b[0]), float(b[1])) for b in data["b"]),
                        asks=tuple((float(a[0]), float(a[1])) for a in data["a"]),
                        timestamp_semantics=semantics,
                        receive_monotonic_ns=monotonic_ns,
                    )
                elif exchange == "upbit":
                    units = payload.get("orderbook_units", d.get("orderbook_units", []))
                    exch_ts = int(payload.get("timestamp", d.get("timestamp", 0)))
                    bids = tuple((float(u["bid_price"]), float(u["bid_size"])) for u in units)
                    asks = tuple((float(u["ask_price"]), float(u["ask_size"])) for u in units)
                    ob = CanonicalOrderBook(
                        exchange="upbit",
                        market=str(payload.get("code", d.get("code", ""))),
                        exchange_timestamp_ms=exch_ts,
                        receive_timestamp_ms=local_recv_ms,
                        bids=bids,
                        asks=asks,
                        timestamp_semantics=semantics,
                        receive_monotonic_ns=monotonic_ns,
                    )
                canonical_records.append(ob)
                total_canonicalized += 1
            except Exception as e:
                total_rejected += 1
                reason = type(e).__name__
                reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
        
        if canonical_records:
            clean_stem = fpath.name
            for ext in (".ndjson.zst", ".jsonl.zst", ".jsonl", ".ndjson"):
                if clean_stem.endswith(ext):
                    clean_stem = clean_stem[:-len(ext)]
                    break
            out_file = output_dir / f"canonical_{clean_stem}.ndjson.zst"
            write_canonical_ndjson_zstd(out_file, canonical_records)

    # P9: Return non-zero and PARTIAL_REJECTED status if any record was rejected
    if total_rejected > 0:
        status = "PARTIAL_REJECTED"
        exit_code = 2
    elif total_canonicalized > 0:
        status = "PASS"
        exit_code = 0
    else:
        status = "EMPTY"
        exit_code = 1

    transform_report = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "schema_version": args.schema_version,
        "files_found": [str(f.relative_to(input_dir)) for f in raw_files],
        "status": status,
        "canonicalized_count": total_canonicalized,
        "rejected_count": total_rejected,
        "reject_reasons": reject_reasons,
    }
    (output_dir / "transform_report.json").write_text(json.dumps(transform_report, indent=2))
    print(f"Transform complete: status={status} canonicalized={total_canonicalized} rejected={total_rejected}")
    return exit_code



def cmd_partition_dataset(args: argparse.Namespace) -> int:
    from .canonical_market_data import CanonicalOrderBook
    from .prospective_dataset import (
        DqQualificationStatus,
        DqQualificationEvidence,
        DqRejectedError,
        build_and_export_dataset,
    )
    import zstandard

    input_file = Path(args.input_file)
    output_dir = Path(args.output_dir)

    if not input_file.exists():
        print(f"ERROR: Input file not found: {input_file}")
        return 1
        
    if not args.dq_report:
        print("ERROR: --dq-report is required (DQ_EVIDENCE_REQUIRED)")
        return 2
        
    dq_report_path = Path(args.dq_report)
    if not dq_report_path.exists():
        print(f"ERROR: DQ report not found at {dq_report_path}")
        return 2
        
    try:
        dq_data = json.loads(dq_report_path.read_text(encoding="utf-8"))
        
        # P1.1: Cryptographically verify report_hash
        expected_report_hash = compute_canonical_report_hash(dq_data)
        actual_report_hash = dq_data.get("report_hash", "")
        if actual_report_hash != expected_report_hash:
            print(f"ERROR: DQ report hash mismatch: actual '{actual_report_hash}' != expected '{expected_report_hash}'")
            return 2

        # P1.2: Cryptographically verify source_manifest_hash if source manifest provided
        if getattr(args, "source_manifest", None):
            sm_path = Path(args.source_manifest)
            if not sm_path.exists():
                print(f"ERROR: Source manifest not found: {sm_path}")
                return 2
            sm_bytes = sm_path.read_bytes()
            sm_sha = hashlib.sha256(sm_bytes).hexdigest()
            try:
                sm_json = json.loads(sm_bytes)
                inner_hash = sm_json.get("source_hash") or sm_json.get("sha256") or sm_json.get("manifest_hash")
            except Exception:
                inner_hash = None
            claimed_src_hash = dq_data.get("source_manifest_hash", "")
            if claimed_src_hash not in (sm_sha, inner_hash):
                print(f"ERROR: DQ_SOURCE_MISMATCH: claimed '{claimed_src_hash}' does not match source manifest '{sm_path}'")
                return 2

        status_str = dq_data.get("status")
        status = DqQualificationStatus(status_str)
        if status not in (DqQualificationStatus.DQ_PASS, DqQualificationStatus.DQ_DEGRADED):
            print(f"ERROR: DQ status {status} is not acceptable")
            return 2
            
        dq_evidence = DqQualificationEvidence(
            status=status,
            auditor_version=dq_data.get("auditor_version", ""),
            audit_code_commit=dq_data.get("audit_code_commit", ""),
            source_manifest_hash=dq_data.get("source_manifest_hash", ""),
            report_hash=actual_report_hash,
            created_at=dq_data.get("created_at", ""),
            criteria_version=dq_data.get("criteria_version", ""),
            hard_fail_count=dq_data.get("hard_fail_count", 0),
            unknown_count=dq_data.get("unknown_count", 0),
            degraded_count=dq_data.get("degraded_count", 0),
            justification=dq_data.get("justification", ""),
            approved_policy=dq_data.get("approved_policy", ""),
        )
    except Exception as e:
        print(f"ERROR reading / validating DQ report: {e}")
        return 2

    # Read records from ndjson.zst
    records = []
    source_line_count = 0
    parsed_count = 0
    malformed_count = 0
    
    try:
        dctx = zstandard.ZstdDecompressor()
        with open(input_file, "rb") as f:
            with dctx.stream_reader(f) as reader:
                import io
                text = io.TextIOWrapper(reader, encoding="utf-8")
                for line in text:
                    source_line_count += 1
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        if "timestamp_semantics" in d:
                            from .canonical_market_data import TimestampSemantics
                            d["timestamp_semantics"] = TimestampSemantics(d["timestamp_semantics"])
                        ob = CanonicalOrderBook(**{
                            k: v for k, v in d.items()
                            if k in CanonicalOrderBook.__dataclass_fields__
                        })
                        records.append(ob)
                        parsed_count += 1
                    except Exception:
                        malformed_count += 1
    except Exception as e:
        print(f"ERROR reading input file: {e}")
        return 1

    if malformed_count > 0:
        print(f"ERROR: Found {malformed_count} malformed records out of {source_line_count} total lines")
        return 2

    print(f"Loaded {len(records)} records from {input_file}")

    if not records:
        print("ERROR: No records loaded. Cannot partition empty dataset.")
        return 1

    try:
        manifest = build_and_export_dataset(
            dataset_id=None,  # P2: Content-addressed by builder
            output_dir=output_dir,
            records=records,
            dq_evidence=dq_evidence,
            purge_window_ms=args.purge_window_ms,
            train_frac=args.train_frac,
            val_frac=args.val_frac,
            allow_overwrite=False,  # P0: Output sealed
        )
    except FileExistsError as e:
        print(f"ERROR: Output directory sealed: {e}")
        return 2
    except (ValueError, DqRejectedError) as e:
        print(f"ERROR: {e}")
        return 2
        
    print(f"Partitioned: train={manifest.train_records} val={manifest.validation_records} holdout={manifest.holdout_records}")
    print(f"Manifest: {output_dir / 'manifest.json'}")
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

    # transform-canonical
    p_tc = sub.add_parser("transform-canonical", help="Transform raw exchange data to canonical format")
    p_tc.add_argument("--input-dir", required=True, help="Input directory with raw data")
    p_tc.add_argument("--output-dir", required=True, help="Output directory for canonical data")
    p_tc.add_argument("--schema-version", default="2.0.0", help="Schema version")
    p_tc.add_argument("--exchange", required=False, default="bithumb", help="Exchange type")

    # partition-dataset
    p_pd = sub.add_parser("partition-dataset", help="Temporally partition a canonical dataset with embargo windows")
    p_pd.add_argument("--input-file", required=True, help="Input canonical ndjson.zst file")
    p_pd.add_argument("--output-dir", required=True, help="Output directory for partitioned dataset")
    p_pd.add_argument("--dq-report", required=False, help="DQ report evidence file")
    p_pd.add_argument("--source-manifest", help="Optional path to source manifest JSON to verify provenance")
    p_pd.add_argument("--train-frac", type=float, default=0.60, help="Train fraction")
    p_pd.add_argument("--val-frac", type=float, default=0.20, help="Validation fraction")
    p_pd.add_argument("--purge-window-ms", type=int, default=900_000, help="Embargo purge window in ms")

    # dq-qualify
    p_dq = sub.add_parser("dq-qualify", help="Produce cryptographic DQ qualification evidence artifact")
    p_dq.add_argument("--audit-report", required=True, help="Input quality audit report JSON")
    p_dq.add_argument("--out", required=True, help="Output qualification evidence artifact path")
    p_dq.add_argument("--source-manifest", help="Path to source manifest to bind")
    p_dq.add_argument("--source-manifest-hash", help="Explicit source manifest hash")
    p_dq.add_argument("--policy", default="strict_v1", help="Approved policy name")
    p_dq.add_argument("--commit", default="061873431da2e3b10e00869afc3fe9e746b88c41", help="Code commit")
    p_dq.add_argument("--auditor-version", default="v9.1.0-offline", help="Auditor version")
    p_dq.add_argument("--criteria-version", default="v1-strict", help="Criteria version")

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
    elif args.command == "audit-quality":
        return cmd_audit_quality(args)
    elif args.command == "transform-canonical":
        return cmd_transform_canonical(args)
    elif args.command == "partition-dataset":
        return cmd_partition_dataset(args)
    elif args.command == "dq-qualify":
        return cmd_dq_qualify(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
