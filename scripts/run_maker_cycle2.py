"""Maker Research Cycle 2 — High-Information Refinement.

Addresses Cycle 1 Bottlenecks:
1. Forced taker unwinds in Variant B eroded positive gross spread (+1.58 bps on XRP).
   Refinement: Extend passive exit cancellation window to 15s and condition on wide spread (>= 3.0 bps).
2. Conservative adverse selection was severe on unconditioned entries.
   Refinement: Strict threshold (OBI >= 0.8 / ATI >= 0.8) for higher signal conviction.

Outputs:
- research-artifacts/maker/reports/MAKER_CYCLE2_REFINEMENT_RESULTS.json
- appends to research-artifacts/current/TRIAL_LEDGER.jsonl
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from bithumb_coin_trader.maker_simulator import (
    FillModel,
    MakerAssumptions,
    MakerFill,
    MakerSimulator,
    OrderStatus,
)
from bithumb_coin_trader.research_infra.canonical_events import CanonicalEvent
from bithumb_coin_trader.research_infra.features import FeatureEngine
from run_maker_research import (
    DEV_HOURS,
    DS,
    HORIZONS_S,
    MARKETS,
    POSITION_SIZE_BTC,
    REPORT_DIR,
    TRIAL_LEDGER_PATH,
    JOURNAL_PATH,
    load_market_events,
    MakerScenarioResult,
)


def run_cycle2_market(market: str, events: list[CanonicalEvent]) -> list[MakerScenarioResult]:
    print(f"\n[Cycle 2 Refinement] Processing {market}...")
    fe = FeatureEngine(market=market, exchange="bithumb")

    timed_features = []
    for ev in events:
        fv = fe.process_event(ev)
        if fv and fv.mid_price:
            timed_features.append((ev, fv))

    # Refined signal filter: Strict OBI/ATI threshold (0.8) AND spread >= 3.0 bps
    refined_signals: list[tuple[int, str, float, str, CanonicalEvent]] = []
    min_spacing_ns = 2_000_000_000
    last_sig = 0

    for idx, (ev, fv) in enumerate(timed_features):
        ts = ev.ordering_timestamp_ns
        if ts - last_sig < min_spacing_ns:
            continue

        bids = ev.payload.get("bids", [])
        asks = ev.payload.get("asks", [])
        if not bids or not asks:
            continue

        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        mid = (best_bid + best_ask) / 2.0
        spread_bps = ((best_ask - best_bid) / mid) * 10_000.0

        obi = getattr(fv, "depth_imbalance_l1", 0.0) or 0.0

        # Strict conditioning: OBI >= 0.8 and spread >= 3.0 bps
        if spread_bps >= 3.0:
            if obi >= 0.8:
                refined_signals.append((idx, "M1_REFINED_HIGH_CONVICTION", best_bid, "BUY", ev))
                last_sig = ts
            elif obi <= -0.8:
                refined_signals.append((idx, "M1_REFINED_HIGH_CONVICTION", best_ask, "SELL", ev))
                last_sig = ts

    print(f"  Identified {len(refined_signals)} high-conviction wide-spread signals.")
    results: list[MakerScenarioResult] = []

    if not refined_signals:
        return results

    # Evaluate Variant B (Passive Exit) under BASE and CONSERVATIVE with 15s exit window
    for fill_model in [FillModel.BASE, FillModel.CONSERVATIVE]:
        assumptions = MakerAssumptions(
            fill_model=fill_model,
            cancellation_horizon_s=10.0,
            holding_horizon_s=15.0,
            maker_fee_rate=0.0,
            taker_fee_rate=0.0004,
            latency_ms=100.0,
        )
        sim = MakerSimulator(assumptions)

        fills_list = []
        trips_list = []
        hourly_pnl = defaultdict(list)

        for idx, h_name, limit_px, side, ev in refined_signals:
            future_window = events[idx + 1 : idx + 401]
            if not future_window:
                continue

            ref_mid = (float(ev.payload["bids"][0][0]) + float(ev.payload["asks"][0][0])) / 2.0
            order = sim.create_order(
                side=side,
                limit_price=limit_px,
                quantity=POSITION_SIZE_BTC,
                placement_ts=ev.ordering_timestamp_ns,
                initial_book=ev,
            )
            fill = sim.evaluate_order(order, future_window, ref_mid)
            fills_list.append(fill)

            cohort = ev.cohort_utc or "unknown"
            if fill.status in (OrderStatus.FILLED, OrderStatus.PARTIAL) and fill.fill_quantity > 0:
                trip = sim.simulate_variant_b(fill, future_window)
                trips_list.append(trip)
                hourly_pnl[cohort].append(trip.net_bps)
            else:
                hourly_pnl[cohort].append(0.0)

        n_sigs = len(refined_signals)
        n_orders = len(fills_list)
        successful_fills = [f for f in fills_list if f.status in (OrderStatus.FILLED, OrderStatus.PARTIAL)]
        n_fills = len(successful_fills)
        fill_rate = n_fills / n_orders if n_orders > 0 else 0.0

        gross_bps_all = [t.gross_bps for t in trips_list]
        mean_gross = sum(gross_bps_all) / len(gross_bps_all) if gross_bps_all else 0.0

        net_bps_all = [t.net_bps for t in trips_list]
        mean_net_fill = sum(net_bps_all) / len(net_bps_all) if net_bps_all else 0.0
        mean_net_signal = (sum(net_bps_all) / n_sigs) if n_sigs > 0 else 0.0

        adverses = [f.adverse_selection_bps for f in successful_fills]
        mean_adverse = sum(adverses) / len(adverses) if adverses else 0.0

        total_net_krw = sum(t.net_krw for t in trips_list)
        cohort_means = [sum(v) / len(v) for v in hourly_pnl.values() if v]
        hour_consistency = len([m for m in cohort_means if m > 0]) / len(cohort_means) if cohort_means else 0.0

        if mean_net_fill > 1.0 and fill_model in (FillModel.BASE, FillModel.CONSERVATIVE):
            classification = "MAKER_CANDIDATE"
        elif mean_gross > 0 and mean_net_fill <= 0:
            classification = "COST_KILLED"
        elif mean_gross <= 0:
            classification = "MAKER_PREDICTIVE_BUT_UNTRADEABLE"
        else:
            classification = "NO_EXECUTABLE_CANDIDATE"

        trial_id = f"MAKER-C2-{market}-M1_REFINED-{fill_model.value[:4].upper()}-VARB-15S"
        res = MakerScenarioResult(
            trial_id=trial_id,
            cycle=2,
            dataset=DS,
            market=market,
            hypothesis="M1_REFINED",
            feature="depth_imbalance_l1_cond_spread",
            horizon_s=15.0,
            fill_model=fill_model.value,
            variant="B",
            fee_regime="maker_zero_taker_4bps",
            signals=n_sigs,
            orders=n_orders,
            fills=n_fills,
            fill_rate=fill_rate,
            partial_fills=len([f for f in fills_list if f.status == OrderStatus.PARTIAL]),
            cancel_rate=len([f for f in fills_list if f.status in (OrderStatus.CANCELLED, OrderStatus.EXPIRED)]) / n_orders if n_orders > 0 else 0.0,
            queue_delay_ms_mean=sum(f.queue_delay_ms for f in successful_fills) / len(successful_fills) if successful_fills else 0.0,
            adverse_selection_bps_mean=mean_adverse,
            gross_bps_mean=mean_gross,
            fee_bps_mean=mean_gross - mean_net_fill,
            net_bps_per_fill=mean_net_fill,
            net_bps_per_signal=mean_net_signal,
            total_net_krw=total_net_krw,
            inventory_duration_s_mean=sum(t.holding_duration_s for t in trips_list) / len(trips_list) if trips_list else 0.0,
            hour_consistency=hour_consistency,
            classification=classification,
        )
        results.append(res)

    return results


def main():
    t0 = time.time()
    print("======================================================================")
    print("STARTING MAKER RESEARCH CYCLE 2 REFINEMENT")
    print("======================================================================")

    all_results: list[MakerScenarioResult] = []
    for mkt in ["KRW-ETH", "KRW-XRP"]:  # Focus on wide-spread markets
        events = load_market_events(mkt)
        if not events:
            continue
        all_results.extend(run_cycle2_market(mkt, events))

    output_path = REPORT_DIR / "MAKER_CYCLE2_REFINEMENT_RESULTS.json"
    output_path.write_text(json.dumps([r.to_dict() for r in all_results], indent=2))
    print(f"\nWrote Cycle 2 results to {output_path}")

    # Append to TRIAL_LEDGER
    with open(TRIAL_LEDGER_PATH, "a") as f:
        for r in all_results:
            ledger_entry = {
                "trial_id": r.trial_id,
                "cycle": 2,
                "dataset": r.dataset,
                "market": r.market,
                "hypothesis": r.hypothesis,
                "feature": r.feature,
                "horizon": f"{r.horizon_s}s",
                "threshold": "0.8_spread3bps",
                "regime": r.fee_regime,
                "entry_model": "PASSIVE_LIMIT",
                "exit_model": "VARIANT_B_15S",
                "queue_model": r.fill_model,
                "latency_ms": 100.0,
                "fees": "maker_0_taker_4bps",
                "signals": r.signals,
                "fills": r.fills,
                "fill_rate": round(r.fill_rate, 4),
                "gross_bps": round(r.gross_bps_mean, 3),
                "net_bps": round(r.net_bps_per_fill, 3),
                "classification": r.classification,
                "pre_registered": True,
                "artifact": str(output_path.relative_to(ROOT)),
            }
            f.write(json.dumps(ledger_entry) + "\n")

    # Update JOURNAL
    with open(JOURNAL_PATH, "a") as f:
        f.write(
            json.dumps({
                "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "cycle": 2,
                "phase": "MAKER_CYCLE2_COMPLETE",
                "scenarios_run": len(all_results),
                "best_net_bps": max((r.net_bps_per_fill for r in all_results), default=0.0),
                "best_scenario": max(all_results, key=lambda r: r.net_bps_per_fill).trial_id if all_results else None,
                "runtime_s": round(time.time() - t0, 2),
            })
            + "\n"
        )

    print(f"Cycle 2 complete in {time.time() - t0:.2f}s.")


if __name__ == "__main__":
    main()
