"""Command Line Interface for Microstructure Research and Paper Simulation (P24)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

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
            if fpath.name.endswith(".ndjson.zst"):
                ndjson_count += 1
            if fpath.name == "manifest.json":
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
    return 0 if report["status"] == "STRUCTURAL_AUDIT_PASS" else 2


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
    raw_files = list(input_dir.rglob("*.ndjson.zst"))
    
    total_canonicalized = 0
    total_rejected = 0
    reject_reasons = {}
    
    dctx = zstandard.ZstdDecompressor()
    
    for fpath in raw_files:
        canonical_records = []
        with open(fpath, "rb") as f:
            with dctx.stream_reader(f) as reader:
                import io
                text = io.TextIOWrapper(reader, encoding="utf-8")
                for line in text:
                    line = line.strip()
                    if not line: continue
                    try:
                        d = json.loads(line)
                        if exchange == "bithumb":
                            ob = CanonicalOrderBook(
                                exchange="bithumb",
                                market=d["market"].replace("_", "-"),
                                exchange_timestamp_ms=d["timestamp"],
                                receive_timestamp_ms=d["timestamp"],
                                bids=tuple((float(b["price"]), float(b["quantity"])) for b in d["bids"]),
                                asks=tuple((float(a["price"]), float(a["quantity"])) for a in d["asks"]),
                                timestamp_semantics=TimestampSemantics.EXCHANGE_EVENT
                            )
                        elif exchange == "binance":
                            data = d["data"]
                            ob = CanonicalOrderBook(
                                exchange="binance",
                                market=d["stream"].split("@")[0].upper(),
                                exchange_timestamp_ms=data["E"],
                                receive_timestamp_ms=data["E"],
                                bids=tuple((float(b[0]), float(b[1])) for b in data["b"]),
                                asks=tuple((float(a[0]), float(a[1])) for a in data["a"]),
                                timestamp_semantics=TimestampSemantics.EXCHANGE_EVENT
                            )
                        elif exchange == "upbit":
                            units = d["orderbook_units"]
                            bids = tuple((float(u["bid_price"]), float(u["bid_size"])) for u in units)
                            asks = tuple((float(u["ask_price"]), float(u["ask_size"])) for u in units)
                            ob = CanonicalOrderBook(
                                exchange="upbit",
                                market=d["code"],
                                exchange_timestamp_ms=d["timestamp"],
                                receive_timestamp_ms=d["timestamp"],
                                bids=bids,
                                asks=asks,
                                timestamp_semantics=TimestampSemantics.EXCHANGE_EVENT
                            )
                        canonical_records.append(ob)
                        total_canonicalized += 1
                    except Exception as e:
                        total_rejected += 1
                        reason = type(e).__name__
                        reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
        
        if canonical_records:
            out_file = output_dir / f"canonical_{fpath.name}"
            write_canonical_ndjson_zstd(out_file, canonical_records)

    transform_report = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "schema_version": args.schema_version,
        "files_found": [str(f.relative_to(input_dir)) for f in raw_files],
        "status": "PASS",
        "canonicalized_count": total_canonicalized,
        "rejected_count": total_rejected,
        "reject_reasons": reject_reasons,
    }
    (output_dir / "transform_report.json").write_text(json.dumps(transform_report, indent=2))
    return 0



def cmd_partition_dataset(args: argparse.Namespace) -> int:
    from .canonical_market_data import CanonicalOrderBook
    from .prospective_dataset import (
        DqQualificationStatus,
        DqQualificationEvidence,
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
        dq_data = json.loads(dq_report_path.read_text())
        status_str = dq_data.get("status")
        hard_fail_count = dq_data.get("hard_fail_count", 0)
        justification = dq_data.get("justification", "")
        
        status = DqQualificationStatus(status_str)
        if status not in (DqQualificationStatus.DQ_PASS, DqQualificationStatus.DQ_DEGRADED):
            print(f"ERROR: DQ status {status} is not acceptable")
            return 2
            
        dq_evidence = DqQualificationEvidence(
            status=status,
            auditor_version=dq_data.get("auditor_version", "1.0.0"),
            audit_code_commit=dq_data.get("audit_code_commit", "unknown"),
            source_manifest_hash=dq_data.get("source_manifest_hash", "unknown"),
            report_hash=dq_data.get("report_hash", "unknown"),
            created_at=dq_data.get("created_at", "unknown"),
            criteria_version=dq_data.get("criteria_version", "unknown"),
            hard_fail_count=hard_fail_count,
            unknown_count=dq_data.get("unknown_count", 0),
            degraded_count=dq_data.get("degraded_count", 0),
            justification=justification,
            approved_policy=dq_data.get("approved_policy", "default")
        )
    except Exception as e:
        print(f"ERROR reading DQ report: {e}")
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

    dataset_id = input_file.stem.replace(".ndjson", "")
    try:
        manifest = build_and_export_dataset(
            dataset_id=dataset_id,
            output_dir=output_dir,
            records=records,
            dq_evidence=dq_evidence,
            purge_window_ms=args.purge_window_ms,
            train_frac=args.train_frac,
            val_frac=args.val_frac,
            allow_overwrite=True
        )
    except ValueError as e:
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
    p_pd.add_argument("--train-frac", type=float, default=0.60, help="Train fraction")
    p_pd.add_argument("--val-frac", type=float, default=0.20, help="Validation fraction")
    p_pd.add_argument("--purge-window-ms", type=int, default=900_000, help="Embargo purge window in ms")

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
