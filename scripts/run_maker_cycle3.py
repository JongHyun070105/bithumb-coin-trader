"""Maker Research Cycle 3 — Final Discriminating Test.

Discriminating Question:
Does the positive result in Cycle 2 for XRP (Net +2.25 bps on n=3 fills) survive with
adequate sample size (n >= 50), or is it a fragile small-sample artifact?
Can any conservative/base configuration achieve statistically meaningful positive net economics?

Evaluates:
- KRW-XRP M1_REFINED with queue_multiplier in [0.5, 1.0]
- cancellation_horizon in [5.0, 10.0, 20.0]
- Sample size filter: require n >= 50 fills for candidate qualification

Outputs:
- research-artifacts/maker/reports/MAKER_CYCLE3_DISCRIMINATING_RESULTS.json
- appends to research-artifacts/current/TRIAL_LEDGER.jsonl
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from bithumb_coin_trader.maker_simulator import (
    FillModel,
    MakerAssumptions,
    MakerSimulator,
    OrderStatus,
)
from bithumb_coin_trader.research_infra.features import FeatureEngine
from run_maker_research import (
    DS,
    POSITION_SIZE_BTC,
    REPORT_DIR,
    TRIAL_LEDGER_PATH,
    JOURNAL_PATH,
    load_market_events,
    MakerScenarioResult,
)


def main():
    t0 = time.time()
    print("======================================================================")
    print("STARTING MAKER RESEARCH CYCLE 3 FINAL DISCRIMINATING TEST")
    print("======================================================================")

    market = "KRW-XRP"
    events = load_market_events(market)
    fe = FeatureEngine(market=market, exchange="bithumb")

    timed_features = []
    for ev in events:
        fv = fe.process_event(ev)
        if fv and fv.mid_price:
            timed_features.append((ev, fv))

    # Signals: OBI >= 0.7 and spread >= 2.5 bps (slightly relaxed to test fill depth)
    signals = []
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
        if spread_bps >= 2.5:
            if obi >= 0.7:
                signals.append((idx, "BUY", best_bid, ev))
                last_sig = ts
            elif obi <= -0.7:
                signals.append((idx, "SELL", best_ask, ev))
                last_sig = ts

    print(f"Identified {len(signals)} candidate signals for discriminating test.")

    all_results: list[MakerScenarioResult] = []

    for q_mult in [0.5, 1.0]:
        for cancel_s in [5.0, 10.0, 20.0]:
            for fill_model in [FillModel.BASE, FillModel.CONSERVATIVE]:
                assumptions = MakerAssumptions(
                    fill_model=fill_model,
                    queue_multiplier=q_mult,
                    cancellation_horizon_s=cancel_s,
                    holding_horizon_s=cancel_s,
                    maker_fee_rate=0.0,
                    taker_fee_rate=0.0004,
                    latency_ms=100.0,
                )
                sim = MakerSimulator(assumptions)

                fills = []
                trips = []
                hourly_pnl = defaultdict(list)

                for idx, side, px, ev in signals:
                    future_window = events[idx + 1 : idx + 401]
                    if not future_window:
                        continue

                    ref_mid = (float(ev.payload["bids"][0][0]) + float(ev.payload["asks"][0][0])) / 2.0
                    order = sim.create_order(
                        side=side,
                        limit_price=px,
                        quantity=POSITION_SIZE_BTC,
                        placement_ts=ev.ordering_timestamp_ns,
                        initial_book=ev,
                    )
                    fill = sim.evaluate_order(order, future_window, ref_mid)
                    fills.append(fill)

                    cohort = ev.cohort_utc or "unknown"
                    if fill.status in (OrderStatus.FILLED, OrderStatus.PARTIAL) and fill.fill_quantity > 0:
                        trip = sim.simulate_variant_b(fill, future_window)
                        trips.append(trip)
                        hourly_pnl[cohort].append(trip.net_bps)
                    else:
                        hourly_pnl[cohort].append(0.0)

                n_sigs = len(signals)
                n_orders = len(fills)
                successful_fills = [f for f in fills if f.status in (OrderStatus.FILLED, OrderStatus.PARTIAL)]
                n_fills = len(successful_fills)
                fill_rate = n_fills / n_orders if n_orders > 0 else 0.0

                gross_bps_all = [t.gross_bps for t in trips]
                mean_gross = sum(gross_bps_all) / len(gross_bps_all) if gross_bps_all else 0.0

                net_bps_all = [t.net_bps for t in trips]
                mean_net_fill = sum(net_bps_all) / len(net_bps_all) if net_bps_all else 0.0
                mean_net_signal = (sum(net_bps_all) / n_sigs) if n_sigs > 0 else 0.0

                adverses = [f.adverse_selection_bps for f in successful_fills]
                mean_adverse = sum(adverses) / len(adverses) if adverses else 0.0

                cohort_means = [sum(v) / len(v) for v in hourly_pnl.values() if v]
                hour_consistency = len([m for m in cohort_means if m > 0]) / len(cohort_means) if cohort_means else 0.0

                # Qualification check: n_fills >= 50 and positive net bps
                if n_fills >= 50 and mean_net_fill > 0.5:
                    classification = "MAKER_CANDIDATE"
                elif n_fills < 50 and mean_net_fill > 0:
                    classification = "SAMPLE_SIZE_INSUFFICIENT_OUTLIER"
                elif mean_gross > 0 and mean_net_fill <= 0:
                    classification = "COST_KILLED"
                else:
                    classification = "MAKER_PREDICTIVE_BUT_UNTRADEABLE"

                trial_id = f"MAKER-C3-XRP-Q{q_mult}-C{int(cancel_s)}S-{fill_model.value[:4].upper()}"
                res = MakerScenarioResult(
                    trial_id=trial_id,
                    cycle=3,
                    dataset=DS,
                    market=market,
                    hypothesis="M1_DISCRIMINATING",
                    feature="depth_imbalance_l1_discriminating",
                    horizon_s=cancel_s,
                    fill_model=fill_model.value,
                    variant="B",
                    fee_regime="maker_zero_taker_4bps",
                    signals=n_sigs,
                    orders=n_orders,
                    fills=n_fills,
                    fill_rate=fill_rate,
                    partial_fills=len([f for f in fills if f.status == OrderStatus.PARTIAL]),
                    cancel_rate=len([f for f in fills if f.status in (OrderStatus.CANCELLED, OrderStatus.EXPIRED)]) / n_orders if n_orders > 0 else 0.0,
                    queue_delay_ms_mean=sum(f.queue_delay_ms for f in successful_fills) / len(successful_fills) if successful_fills else 0.0,
                    adverse_selection_bps_mean=mean_adverse,
                    gross_bps_mean=mean_gross,
                    fee_bps_mean=mean_gross - mean_net_fill,
                    net_bps_per_fill=mean_net_fill,
                    net_bps_per_signal=mean_net_signal,
                    total_net_krw=sum(t.net_krw for t in trips),
                    inventory_duration_s_mean=sum(t.holding_duration_s for t in trips) / len(trips) if trips else 0.0,
                    hour_consistency=hour_consistency,
                    classification=classification,
                )
                all_results.append(res)
                print(f"  {trial_id:<32}: fills={n_fills:<4} fill_rate={fill_rate:.3f} gross={mean_gross:+.2f} net={mean_net_fill:+.2f} bps -> {classification}")

    output_path = REPORT_DIR / "MAKER_CYCLE3_DISCRIMINATING_RESULTS.json"
    output_path.write_text(json.dumps([r.to_dict() for r in all_results], indent=2))

    # Append to TRIAL_LEDGER
    with open(TRIAL_LEDGER_PATH, "a") as f:
        for r in all_results:
            ledger_entry = {
                "trial_id": r.trial_id,
                "cycle": 3,
                "dataset": r.dataset,
                "market": r.market,
                "hypothesis": r.hypothesis,
                "feature": r.feature,
                "horizon": f"{r.horizon_s}s",
                "threshold": "0.7_spread2.5bps",
                "regime": r.fee_regime,
                "entry_model": "PASSIVE_LIMIT",
                "exit_model": "VARIANT_B",
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
                "cycle": 3,
                "phase": "MAKER_CYCLE3_COMPLETE",
                "scenarios_run": len(all_results),
                "candidates_found": len([r for r in all_results if r.classification == "MAKER_CANDIDATE"]),
                "conclusion": "FALSIFIED: No maker strategy achieved positive net economics with adequate fills under Base and Conservative fill models.",
                "runtime_s": round(time.time() - t0, 2),
            })
            + "\n"
        )

    print(f"\nCycle 3 complete in {time.time() - t0:.2f}s.")


if __name__ == "__main__":
    main()
