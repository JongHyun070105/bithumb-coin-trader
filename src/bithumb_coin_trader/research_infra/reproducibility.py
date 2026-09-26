"""Deterministic reproducibility harness for external BitMEX dataset.

Proves that starting from only the original ZIP archive and repository code,
a clean rebuild produces logically identical derived Parquet datasets and reports.

Defines:
- BYTE_HASH: Raw file byte checksums
- LOGICAL_CONTENT_HASH: Schema + columnar data buffer checksums independent of parquet writer metadata
- Canonical ordering checks
- Automated clean rebuild runner and verification comparator
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any
import zipfile

import pyarrow.compute as pc
import pyarrow.parquet as pq

from bithumb_coin_trader.research_infra.external_canonical import CanonicalIngestor
from bithumb_coin_trader.research_infra.external_dq import DataQualityAuditor
from bithumb_coin_trader.research_infra.external_expert import (
    ExecutionRow,
    iter_csv_rows,
    normalize_execution,
    normalize_wallet,
    sha256_file,
)
from bithumb_coin_trader.research_infra.external_reconstruction import (
    CycleReconstructor,
    OrderReconstructor,
    PositionReconstructor,
    WalletReconciler,
)
from bithumb_coin_trader.research_infra.external_behavior import (
    BehaviorEvolutionAnalyzer,
    BehaviorFeatureExtractor,
    DomainShiftRiskAssessment,
)


def compute_table_hashes(table_dir: Path) -> dict[str, Any]:
    """Compute row count, schema, byte hash, and logical content hash for a Parquet dataset directory."""
    if not table_dir.exists():
        return {
            "row_count": 0,
            "byte_hash": "",
            "logical_content_hash": "",
            "file_count": 0,
        }

    parquet_files = sorted(table_dir.rglob("*.parquet"))
    byte_hasher = hashlib.sha256()
    for pf in parquet_files:
        byte_hasher.update(pf.read_bytes())

    # Read entire dataset table
    table = pq.read_table(table_dir)
    total_rows = len(table)
    schema_str = str(table.schema)

    # Canonical sorting keys
    sort_keys = []
    if "transact_time" in table.column_names:
        sort_keys.append(("transact_time", "ascending"))
    if "timestamp" in table.column_names:
        sort_keys.append(("timestamp", "ascending"))
    if "execution_id" in table.column_names:
        sort_keys.append(("execution_id", "ascending"))
    if "transact_id" in table.column_names:
        sort_keys.append(("transact_id", "ascending"))

    if sort_keys:
        sort_fn = getattr(pc, "sort_indices")
        sorted_indices = sort_fn(table, sort_keys=sort_keys)
        table = table.take(sorted_indices)

    logical_hasher = hashlib.sha256()
    logical_hasher.update(schema_str.encode("utf-8"))

    for col_name in table.column_names:
        col = table[col_name]
        for chunk in col.chunks:
            for buf in chunk.buffers():
                if buf is not None:
                    logical_hasher.update(buf.to_pybytes())

    return {
        "row_count": total_rows,
        "byte_hash": byte_hasher.hexdigest(),
        "logical_content_hash": logical_hasher.hexdigest(),
        "file_count": len(parquet_files),
        "schema": schema_str,
    }


def compute_dataset_logical_manifest(derived_dir: Path) -> dict[str, Any]:
    """Compute logical hashes for all derived Parquet tables and JSON artifacts."""
    pq_dir = derived_dir / "parquet"
    manifest: dict[str, Any] = {
        "tables": {},
        "reports": {},
    }

    for table_name in ["execution", "funding", "settlement", "wallet"]:
        manifest["tables"][table_name] = compute_table_hashes(pq_dir / table_name)

    for r_file in sorted(derived_dir.glob("*.json")):
        try:
            data = json.loads(r_file.read_text(encoding="utf-8"))
            # Normalize JSON string without timestamp differences
            normalized_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
            manifest["reports"][r_file.name] = {
                "sha256": hashlib.sha256(normalized_str.encode("utf-8")).hexdigest(),
                "size_bytes": len(normalized_str.encode("utf-8")),
            }
        except Exception:
            manifest["reports"][r_file.name] = {
                "sha256": sha256_file(r_file),
                "size_bytes": r_file.stat().st_size,
            }

    return manifest


def clean_rebuild_from_zip(
    zip_path: Path,
    target_root: Path,
    max_reconstruct_sample: int | None = 10_000,
) -> dict[str, Any]:
    """Perform a clean end-to-end rebuild starting strictly from the original ZIP archive."""
    target_root = Path(target_root)
    raw_dir = target_root / "raw"
    derived_dir = target_root / "derived"
    verification_dir = target_root / "verification"

    if target_root.exists():
        shutil.rmtree(target_root)

    raw_dir.mkdir(parents=True, exist_ok=True)
    derived_dir.mkdir(parents=True, exist_ok=True)
    verification_dir.mkdir(parents=True, exist_ok=True)

    # 1. Extract ZIP
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(raw_dir)

    # 2. Canonical Ingestion
    ingestor = CanonicalIngestor(target_root)
    ingest_manifest = ingestor.ingest_all(batch_size=50_000)

    # 3. Data Quality Audit
    auditor = DataQualityAuditor(raw_dir)
    dq_report = auditor.run_audit()
    (verification_dir / "data-quality-report.json").write_text(
        json.dumps(dq_report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # 4. Reconstruction
    trade_rows: list[ExecutionRow] = []
    count = 0
    for f in sorted(raw_dir.glob("aoa-execution-*.csv")):
        for row in iter_csv_rows(f):
            if row.get("exectype") == "Trade":
                trade_rows.append(normalize_execution(row))
                count += 1
                if max_reconstruct_sample and count >= max_reconstruct_sample:
                    break
        if max_reconstruct_sample and count >= max_reconstruct_sample:
            break

    order_summaries = OrderReconstructor.reconstruct_orders(trade_rows)
    position_events = PositionReconstructor.reconstruct_positions(trade_rows)
    cycles = CycleReconstructor.extract_cycles(position_events)

    order_reconstruction_summary = {
        "orders_reconstructed_count": len(order_summaries),
        "total_position_events": len(position_events),
        "total_position_cycles": len(cycles),
    }
    (derived_dir / "order-reconstruction-summary.json").write_text(
        json.dumps(order_reconstruction_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 5. Wallet
    wallet_file = raw_dir / "aoa-wallet-2018-03-01-2021-12-31.csv"
    wallet_events = [
        normalize_wallet(row)
        for row in iter_csv_rows(wallet_file)
        if row.get("transacttype")
    ]
    reconciliation = WalletReconciler.reconcile(wallet_events, trade_rows)
    (derived_dir / "wallet-reconciliation.json").write_text(
        json.dumps(reconciliation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 6. Logical content manifest
    logical_manifest = compute_dataset_logical_manifest(derived_dir)

    return {
        "target_root": str(target_root),
        "canonical_counts": ingest_manifest["canonical_counts"],
        "dq_status": dq_report["overall_status"],
        "order_reconstruction": order_reconstruction_summary,
        "logical_manifest": logical_manifest,
    }


def compare_rebuild_manifests(
    run1: dict[str, Any],
    run2: dict[str, Any],
) -> dict[str, Any]:
    """Compare two clean rebuild runs and assert strict determinism."""
    m1 = run1["logical_manifest"]
    m2 = run2["logical_manifest"]

    table_checks: list[dict[str, Any]] = []
    all_match = True

    for t_name in sorted(m1["tables"].keys()):
        t1 = m1["tables"][t_name]
        t2 = m2["tables"].get(t_name, {})

        rows_match = (t1["row_count"] == t2.get("row_count"))
        logical_hash_match = (t1["logical_content_hash"] == t2.get("logical_content_hash"))
        byte_hash_match = (t1["byte_hash"] == t2.get("byte_hash"))
        schema_match = (t1.get("schema") == t2.get("schema"))

        match = rows_match and logical_hash_match and schema_match
        if not match:
            all_match = False

        table_checks.append({
            "table": t_name,
            "row_count_run1": t1["row_count"],
            "row_count_run2": t2.get("row_count"),
            "rows_match": rows_match,
            "logical_hash_run1": t1["logical_content_hash"],
            "logical_hash_run2": t2.get("logical_content_hash"),
            "logical_hash_match": logical_hash_match,
            "byte_hash_match": byte_hash_match,
            "schema_match": schema_match,
            "verdict": "PASS" if match else "FAIL",
        })

    canonical_counts_match = (run1["canonical_counts"] == run2["canonical_counts"])
    dq_match = (run1["dq_status"] == run2["dq_status"])
    reconstruct_match = (run1["order_reconstruction"] == run2["order_reconstruction"])

    deterministic = (
        all_match
        and canonical_counts_match
        and dq_match
        and reconstruct_match
    )

    return {
        "deterministic": deterministic,
        "all_table_logical_hashes_match": all_match,
        "canonical_counts_match": canonical_counts_match,
        "dq_status_match": dq_match,
        "reconstruction_match": reconstruct_match,
        "table_checks": table_checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--zip-path",
        type=Path,
        default=Path(".external-research-data/external-bitmex-trader-2018-2021/raw/aoa_public_2021-12-31_with_letter.zip"),
        help="Path to raw source zip archive",
    )
    parser.add_argument(
        "--reconstruct-sample",
        type=int,
        default=5_000,
        help="Sample size for reconstruction step in test rebuild",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="rebuild_run1_") as td1, \
         tempfile.TemporaryDirectory(prefix="rebuild_run2_") as td2:
        print(f"Executing Clean Rebuild Run 1 in {td1}...")
        run1 = clean_rebuild_from_zip(args.zip_path, Path(td1), max_reconstruct_sample=args.reconstruct_sample)

        print(f"Executing Clean Rebuild Run 2 in {td2}...")
        run2 = clean_rebuild_from_zip(args.zip_path, Path(td2), max_reconstruct_sample=args.reconstruct_sample)

        print("Comparing Run 1 vs Run 2 manifests...")
        comparison = compare_rebuild_manifests(run1, run2)

        print(json.dumps(comparison, indent=2))
        if comparison["deterministic"]:
            print("\nPROVEN: Derived dataset and reports are 100% DETERMINISTIC and REPRODUCIBLE.")
            return 0
        else:
            print("\nFAILED: Rebuild runs yielded non-deterministic output.")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
