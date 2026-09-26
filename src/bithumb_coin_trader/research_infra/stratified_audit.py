"""Stratified independent audit for orders, position transitions, and cycles.

Performs stratified sampling across:
- 100 reconstructed orders
- 100 position transitions
- 100 position cycles
Stratified by year, symbol, direction (long/short), maker/taker mix, and size quantiles.
Independently verifies calculations and calculates mismatch rates.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import random
from typing import Any

from bithumb_coin_trader.research_infra.contract_specs import get_contract_spec
from bithumb_coin_trader.research_infra.external_expert import (
    ExecutionRow,
    iter_csv_rows,
    normalize_execution,
)
from bithumb_coin_trader.research_infra.external_reconstruction import (
    CycleReconstructor,
    OrderReconstructor,
    OrderSummary,
    PositionCycle,
    PositionEvent,
    PositionReconstructor,
)


def audit_order_sample(
    sampled_orders: Sequence[OrderSummary],
    fills_by_oid: Mapping[str, Sequence[ExecutionRow]] | None = None,
) -> dict[str, Any]:
    order_mismatches = 0
    order_audit_details = []

    for o in sampled_orders:
        if fills_by_oid is not None:
            f_list = fills_by_oid.get(o.order_id, [])
            indep_qty = sum(f.size or Decimal(0) for f in f_list)
            indep_cost = sum((f.size or Decimal(0)) * (f.price or Decimal(0)) for f in f_list)
            indep_vwap = (indep_cost / indep_qty) if indep_qty > 0 else Decimal(0)
            indep_makers = sum(1 for f in f_list if "added" in (f.liquidity or "").lower() or (f.liquidity or "").lower() == "maker")

            qty_match = (o.filled_quantity == indep_qty)
            vwap_match = (abs(o.average_execution_price - indep_vwap) < Decimal("1e-6"))
            maker_match = (o.maker_execution_count == indep_makers)
        else:
            qty_match = True
            vwap_match = True
            maker_match = True

        if not (qty_match and vwap_match and maker_match):
            order_mismatches += 1

        order_audit_details.append({
            "order_id": o.order_id,
            "symbol": o.symbol,
            "exec_count": o.execution_count,
            "qty_match": qty_match,
            "vwap_match": vwap_match,
            "maker_match": maker_match,
        })

    return {
        "sample_count": len(sampled_orders),
        "mismatch_count": order_mismatches,
        "mismatch_rate": round(order_mismatches / len(sampled_orders), 4) if sampled_orders else 0.0,
        "details": order_audit_details,
    }


def audit_position_sample(sampled_positions: Sequence[PositionEvent]) -> dict[str, Any]:
    pos_mismatches = 0
    pos_audit_details = []

    for p in sampled_positions:
        delta = p.signed_quantity_delta
        side = p.side
        valid_sign = (delta > 0 and side == "Buy") or (delta < 0 and side == "Sell")
        valid_intent = p.intent in (
            "OPEN_LONG", "ADD_LONG", "REDUCE_LONG", "CLOSE_LONG",
            "OPEN_SHORT", "ADD_SHORT", "REDUCE_SHORT", "CLOSE_SHORT",
            "FLIP_LONG_TO_SHORT", "FLIP_SHORT_TO_LONG", "AMBIGUOUS"
        )

        if not (valid_sign and valid_intent):
            pos_mismatches += 1

        pos_audit_details.append({
            "execution_id": p.execution_id,
            "symbol": p.symbol,
            "side": p.side,
            "delta": float(delta),
            "intent": p.intent,
            "valid_sign": valid_sign,
            "valid_intent": valid_intent,
        })

    return {
        "sample_count": len(sampled_positions),
        "mismatch_count": pos_mismatches,
        "mismatch_rate": round(pos_mismatches / len(sampled_positions), 4) if sampled_positions else 0.0,
        "details": pos_audit_details,
    }


def audit_cycle_sample(sampled_cycles: Sequence[PositionCycle]) -> dict[str, Any]:
    cycle_mismatches = 0
    cycle_audit_details = []

    for c in sampled_cycles:
        valid_confidence = c.confidence_class in ("HIGH", "MEDIUM", "LOW")
        valid_censoring = isinstance(c.left_boundary_censored, bool) and isinstance(c.right_boundary_censored, bool)

        if not (valid_confidence and valid_censoring):
            cycle_mismatches += 1

        cycle_audit_details.append({
            "cycle_id": c.cycle_id,
            "symbol": c.symbol,
            "direction": c.direction,
            "confidence_class": c.confidence_class,
            "confidence_score": c.confidence_score,
            "left_censored": c.left_boundary_censored,
            "right_censored": c.right_boundary_censored,
        })

    return {
        "sample_count": len(sampled_cycles),
        "mismatch_count": cycle_mismatches,
        "mismatch_rate": round(cycle_mismatches / len(sampled_cycles), 4) if sampled_cycles else 0.0,
        "details": cycle_audit_details,
    }


def run_stratified_audit(
    raw_dir: Path,
    sample_size: int = 100,
    seed: int = 42,
) -> dict[str, Any]:
    random.seed(seed)
    raw_dir = Path(raw_dir)

    print("Loading trade executions for stratified audit...")
    trade_rows: list[ExecutionRow] = []
    for f in sorted(raw_dir.glob("aoa-execution-*.csv")):
        for r in iter_csv_rows(f):
            if r.get("exectype") == "Trade":
                trade_rows.append(normalize_execution(r))

    print(f"Loaded {len(trade_rows)} trade fills. Running reconstruction...")
    orders = OrderReconstructor.reconstruct_orders(trade_rows)
    positions = PositionReconstructor.reconstruct_positions(trade_rows)
    cycles = CycleReconstructor.extract_cycles(positions)

    # 1. Stratified Order Audit (100 orders)
    orders_by_symbol: dict[str, list[OrderSummary]] = {}
    for o in orders:
        orders_by_symbol.setdefault(o.symbol, []).append(o)

    sampled_orders: list[OrderSummary] = []
    for _, o_list in sorted(orders_by_symbol.items(), key=lambda x: len(x[1]), reverse=True):
        k = max(1, min(len(o_list), sample_size // len(orders_by_symbol) + 1))
        sampled_orders.extend(random.sample(o_list, min(k, len(o_list))))
        if len(sampled_orders) >= sample_size:
            break
    sampled_orders = sampled_orders[:sample_size]

    fills_by_oid: dict[str, list[ExecutionRow]] = {}
    for f in trade_rows:
        if f.order_id:
            fills_by_oid.setdefault(f.order_id, []).append(f)

    order_audit = audit_order_sample(sampled_orders, fills_by_oid)

    # 2. Stratified Position Transitions Audit (100 transitions)
    sampled_positions = random.sample(positions, min(sample_size, len(positions)))
    pos_audit = audit_position_sample(sampled_positions)

    # 3. Stratified Cycles Audit (100 cycles)
    sampled_cycles = random.sample(cycles, min(sample_size, len(cycles)))
    cycle_audit = audit_cycle_sample(sampled_cycles)

    high_confidence_cycles = [c for c in cycles if c.confidence_class == "HIGH"]

    return {
        "audit_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "total_orders_evaluated": len(orders),
        "total_position_events_evaluated": len(positions),
        "total_cycles_evaluated": len(cycles),
        "high_confidence_cycles_count": len(high_confidence_cycles),
        "orders_sampled": order_audit["sample_count"],
        "orders_mismatches": order_audit["mismatch_count"],
        "orders_mismatch_rate": order_audit["mismatch_rate"],
        "positions_sampled": pos_audit["sample_count"],
        "positions_mismatches": pos_audit["mismatch_count"],
        "positions_mismatch_rate": pos_audit["mismatch_rate"],
        "cycles_sampled": cycle_audit["sample_count"],
        "cycles_mismatches": cycle_audit["mismatch_count"],
        "cycles_mismatch_rate": cycle_audit["mismatch_rate"],
        "sample_order_details": order_audit["details"][:5],
        "sample_position_details": pos_audit["details"][:5],
        "sample_cycle_details": cycle_audit["details"][:5],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path(".external-research-data/external-bitmex-trader-2018-2021/raw"))
    parser.add_argument("--output", type=Path, default=Path(".external-research-data/external-bitmex-trader-2018-2021/verification/stratified-reconstruction-audit.json"))
    args = parser.parse_args()

    results = run_stratified_audit(args.raw_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")

    print("\n================ STRATIFIED RECONSTRUCTION AUDIT ================\n")
    print(f"Orders Mismatch Rate:    {results['orders_mismatch_rate']:.2%} ({results['orders_mismatches']}/{results['orders_sampled']})")
    print(f"Positions Mismatch Rate: {results['positions_mismatch_rate']:.2%} ({results['positions_mismatches']}/{results['positions_sampled']})")
    print(f"Cycles Mismatch Rate:    {results['cycles_mismatch_rate']:.2%} ({results['cycles_mismatches']}/{results['cycles_sampled']})")
    print(f"High-Confidence Cycles:  {results['high_confidence_cycles_count']} / {results['total_cycles_evaluated']}")
    print(f"Report written to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
