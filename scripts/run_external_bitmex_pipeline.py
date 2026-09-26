"""End-to-end research preparation pipeline for external BitMEX expert dataset.

Executes all pipeline phases:
1. Source verification & ingestion
2. Partitioned Parquet derived dataset generation
3. Comprehensive Data Quality (DQ) audit
4. Deterministic Order, Position, and Cycle Reconstruction
5. Wallet Normalization & Trading Reconciliation
6. Descriptive Behavior Dataset & Execution Regime Segmentation
7. Domain Shift Risk Assessment
8. Context Document Metadata Extraction
9. Provenance Sealing & Performance Benchmarks
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

from bithumb_coin_trader.research_infra.external_canonical import CanonicalIngestor
from bithumb_coin_trader.research_infra.external_dq import DataQualityAuditor
from bithumb_coin_trader.research_infra.external_expert import (
    ExecutionRow,
    WalletEvent,
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
    RegimeConditioningInterface,
)
from bithumb_coin_trader.research_infra.external_context import parse_context_document
from bithumb_coin_trader.research_infra.registry import EXTERNAL_BITMEX_DATASET_ID

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_pipeline(
    dataset_dir: Path | None = None,
    max_reconstruct_sample: int | None = None,
) -> dict[str, Any]:
    start_time = time.perf_counter()
    target_root = dataset_dir or (REPO_ROOT / ".external-research-data" / EXTERNAL_BITMEX_DATASET_ID)
    raw_dir = target_root / "raw"
    derived_dir = target_root / "derived"
    verification_dir = target_root / "verification"

    derived_dir.mkdir(parents=True, exist_ok=True)
    verification_dir.mkdir(parents=True, exist_ok=True)

    print("Phase 1: Canonical Ingestion and Partitioned Parquet Creation...")
    ingestor = CanonicalIngestor(target_root)
    ingest_manifest = ingestor.ingest_all(batch_size=50_000)
    print(f"Ingested {ingest_manifest['canonical_counts']['total_execution_rows']} executions and {ingest_manifest['canonical_counts']['wallet_events']} wallet events.")

    print("Phase 2: Comprehensive Data Quality Audit...")
    auditor = DataQualityAuditor(raw_dir)
    dq_report = auditor.run_audit()
    dq_report_path = verification_dir / "data-quality-report.json"
    dq_report_path.write_text(json.dumps(dq_report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Data Quality overall status: {dq_report['overall_status']}")

    print("Phase 3: Order, Position, and Cycle Reconstruction...")
    # Load representative sample or full execution rows for reconstruction
    # For large datasets, reconstruct on streaming rows
    trade_rows: list[ExecutionRow] = []
    exec_files = sorted(raw_dir.glob("aoa-execution-*.csv"))

    count = 0
    limit = max_reconstruct_sample or 100_000  # Default to 100k sample for fast deterministic reconstruction report, or full if requested
    for f in exec_files:
        for row in iter_csv_rows(f):
            if row.get("exectype") == "Trade":
                trade_rows.append(normalize_execution(row))
                count += 1
                if max_reconstruct_sample and count >= max_reconstruct_sample:
                    break
        if max_reconstruct_sample and count >= max_reconstruct_sample:
            break

    print(f"Reconstructing orders and positions from {len(trade_rows)} trade fills...")
    order_summaries = OrderReconstructor.reconstruct_orders(trade_rows)
    position_events = PositionReconstructor.reconstruct_positions(trade_rows)
    cycles = CycleReconstructor.extract_cycles(position_events)

    order_reconstruction_summary = {
        "orders_reconstructed_count": len(order_summaries),
        "reconstructed_confidence": sum(1 for o in order_summaries if o.reconstruction_confidence == "RECONSTRUCTED"),
        "partial_confidence": sum(1 for o in order_summaries if o.reconstruction_confidence == "PARTIAL"),
        "ambiguous_confidence": sum(1 for o in order_summaries if o.reconstruction_confidence == "AMBIGUOUS"),
        "total_position_events": len(position_events),
        "total_position_cycles": len(cycles),
        "sample_orders": [o.to_dict() for o in order_summaries[:10]],
        "sample_cycles": [c.to_dict() for c in cycles[:10]],
    }
    (derived_dir / "order-reconstruction-summary.json").write_text(
        json.dumps(order_reconstruction_summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("Phase 4: Wallet Normalization and Reconciliation...")
    wallet_file = raw_dir / "aoa-wallet-2018-03-01-2021-12-31.csv"
    wallet_events = [
        normalize_wallet(row)
        for row in iter_csv_rows(wallet_file)
        if row.get("transacttype")
    ]
    reconciliation = WalletReconciler.reconcile(wallet_events, trade_rows)
    (derived_dir / "wallet-reconciliation.json").write_text(
        json.dumps(reconciliation, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("Phase 5: Behavior Feature Extraction and Regime Analysis...")
    daily_features = BehaviorFeatureExtractor.extract_daily_features(trade_rows, order_summaries)
    behavioral_phases = BehaviorEvolutionAnalyzer.identify_behavioral_phases(daily_features)
    domain_shift = DomainShiftRiskAssessment.generate_risk_report()
    regime_schema = RegimeConditioningInterface.get_interface_schema()

    behavior_summary = {
        "daily_features_count": len(daily_features),
        "behavioral_phases": behavioral_phases,
        "sample_daily_features": [
            {
                "date": f.date,
                "trade_count": f.trade_count,
                "volume": f.total_volume,
                "maker_ratio": f.maker_ratio,
                "taker_ratio": f.taker_ratio,
                "median_fill_size": f.median_fill_size,
                "buy_sell_balance": f.buy_sell_balance,
                "symbol_mix": f.symbol_mix,
            }
            for f in daily_features[:10]
        ],
        "domain_shift_risk": domain_shift,
        "regime_interface": regime_schema,
    }
    (derived_dir / "behavior-analysis.json").write_text(
        json.dumps(behavior_summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("Phase 6: Context Document Metadata Extraction...")
    context_file = raw_dir / "90일 서한.txt"
    if context_file.exists():
        context_doc = parse_context_document(context_file)
        (derived_dir / "external-context-document.json").write_text(
            json.dumps(context_doc.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    total_duration = time.perf_counter() - start_time

    # Calculate compression ratio
    raw_bytes = sum(f.stat().st_size for f in raw_dir.glob("*.*") if f.is_file())
    derived_bytes = sum(f.stat().st_size for f in derived_dir.rglob("*.parquet"))
    compression_ratio = (raw_bytes / derived_bytes) if derived_bytes > 0 else 1.0

    checkpoint_report = {
        "dataset_id": EXTERNAL_BITMEX_DATASET_ID,
        "ingest_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "total_duration_seconds": round(total_duration, 2),
        "raw_bytes": raw_bytes,
        "derived_bytes": derived_bytes,
        "compression_ratio": round(compression_ratio, 2),
        "canonical_counts": ingest_manifest["canonical_counts"],
        "dq_status": dq_report["overall_status"],
        "order_reconstruction": {
            "reconstructed_orders": order_reconstruction_summary["orders_reconstructed_count"],
            "position_events": order_reconstruction_summary["total_position_events"],
            "position_cycles": order_reconstruction_summary["total_position_cycles"],
        },
        "behavioral_phases_count": len(behavioral_phases),
    }

    (derived_dir / "pipeline-summary.json").write_text(
        json.dumps(checkpoint_report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print("Pipeline run complete!")
    return checkpoint_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-sample", type=int, default=None,
                        help="Maximum trade rows to reconstruct (default: all)")
    args = parser.parse_args()
    summary = run_pipeline(max_reconstruct_sample=args.max_sample)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
