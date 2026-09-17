"""Frozen Cross-Exchange Confirmation Execution Experiment.

Evaluates top predictive cross-exchange signals with REAL future-orderbook
depth-walking execution using ResearchExecutionSimulator on Bithumb orderbook snapshots.

This directly confirms whether the theoretical predictive lead (X1) translates
into profitable taker execution when accounting for:
- Real orderbook depth walking (bid/ask queues)
- Execution latency (100ms, 250ms, 500ms)
- Bithumb taker fee (4 bps each way)
- Half-spread crossing costs

Outputs:
- research-artifacts/cross-exchange/reports/CROSS_EXCHANGE_CONFIRMATION_EXECUTION.json
"""

from __future__ import annotations

import bisect
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bithumb_coin_trader.research_infra.adapters import iter_raw_jsonl_file
from bithumb_coin_trader.research_infra.canonical_events import CanonicalEvent, EventKind
from bithumb_coin_trader.research_infra.execution import (
    ExecutionAssumptions,
    ResearchExecutionSimulator,
    SimulatedTrade,
)

V2_DATA = ROOT / "data" / "research" / "v2"
REPORT_DIR = ROOT / "research-artifacts" / "cross-exchange" / "reports"
DS = "aws-validation-30h-20260912-6576f63"
DEV_HOURS = [f"2026-09-12_{h:02d}" for h in range(11, 17)]


def load_canonical_orderbook_events(exchange: str, market_symbol: str) -> list[CanonicalEvent]:
    """Load canonical orderbook events for an exchange and market."""
    date_dir = V2_DATA / "2026-09-12" / exchange / "orderbook"
    if not date_dir.exists():
        return []

    files = [
        date_dir / f"{exchange}_orderbook_{market_symbol.lower()}_{h}.jsonl.zst"
        for h in DEV_HOURS
    ]

    events: list[CanonicalEvent] = []
    for f in files:
        if f.exists():
            for ev in iter_raw_jsonl_file(f, dataset_id=DS):
                if ev.event_kind == EventKind.ORDERBOOK:
                    events.append(ev)

    events.sort(key=lambda e: e.ordering_timestamp_ns)
    return events


def run_confirmation_execution() -> list[dict[str, Any]]:
    """Run real depth-walking confirmation execution on top predictive setups."""
    print("=" * 70)
    print("RUNNING CROSS-EXCHANGE REAL CONFIRMATION EXECUTION EXPERIMENT")
    print("=" * 70)

    # Focus on top predictive markets identified in X1
    target_markets = ["BTC", "SOL"]
    test_lags_ms = [500, 1000]
    test_horizons_s = [1.0, 5.0, 10.0]
    test_latencies_ms = [100.0, 250.0, 500.0]
    position_size_krw = 100_000.0
    taker_fee_rate = 0.0004  # 4 bps taker fee

    results: list[dict[str, Any]] = []

    for mkt in target_markets:
        print(f"\n[1/2] Loading orderbook event streams for {mkt}...")
        bithumb_sym = f"krw-{mkt.lower()}"
        binance_sym = f"{mkt.lower()}usdt"
        bithumb_events = load_canonical_orderbook_events("bithumb", bithumb_sym)
        binance_events = load_canonical_orderbook_events("binance", binance_sym)

        if not bithumb_events or not binance_events:
            print(f"  Missing data for {mkt}, skipping.")
            continue

        print(f"  Bithumb events: {len(bithumb_events):,}, Binance events: {len(binance_events):,}")

        # Extract Binance availability timestamps and mid prices
        bn_avail: list[int] = []
        bn_mids: list[float] = []
        for ev in binance_events:
            p = ev.payload
            bids = p.get("bids", [])
            asks = p.get("asks", [])
            if bids and asks:
                bb = float(bids[0][0])
                ba = float(asks[0][0])
                if bb > 0 and ba > 0:
                    bn_avail.append(ev.causal_availability_ns)
                    bn_mids.append((bb + ba) / 2.0)

        for lag_ms in test_lags_ms:
            lag_ns = lag_ms * 1_000_000

            for hz_s in test_horizons_s:
                hz_ns = int(hz_s * 1_000_000_000)

                for lat_ms in test_latencies_ms:
                    # Configure execution simulator
                    assumptions = ExecutionAssumptions(
                        fee_regime="bithumb_taker_4bps",
                        fee_rate=taker_fee_rate,
                        additional_impact_bps=0.0,
                        latency_ms=lat_ms,
                        position_size_krw=position_size_krw,
                        max_depth_levels=5,
                        passive_fills_enabled=False,
                        partial_fills_enabled=True,
                        description=f"Real taker execution with {lat_ms}ms latency on {mkt}",
                    )
                    sim = ResearchExecutionSimulator(assumptions)

                    # Threshold for trade signal (Binance return in bps)
                    signal_threshold_bps = 2.0

                    in_trade = False
                    exit_due_ts = 0
                    signals_triggered = 0

                    # Sample through Bithumb events at 50-event intervals (~5 seconds)
                    step = 50
                    for i in range(0, len(bithumb_events), step):
                        ev = bithumb_events[i]
                        t_decision = ev.ordering_timestamp_ns

                        if in_trade:
                            # Check if holding horizon has elapsed
                            if t_decision >= exit_due_ts:
                                # Execute SELL exit with latency
                                future_window = bithumb_events[i : min(i + 200, len(bithumb_events))]
                                trade = sim.execute_signal_with_latency("SELL", ev, future_window)
                                if trade is not None:
                                    in_trade = False
                                    exit_due_ts = 0
                            continue

                        # Check Binance signal with strict backward as-of availability
                        idx_curr = bisect.bisect_right(bn_avail, t_decision) - 1
                        if idx_curr < 0:
                            continue

                        idx_prev = bisect.bisect_right(bn_avail, t_decision - lag_ns) - 1
                        if idx_prev < 0 or idx_prev >= idx_curr:
                            continue

                        p_curr = bn_mids[idx_curr]
                        p_prev = bn_mids[idx_prev]
                        if p_prev <= 0:
                            continue

                        bn_ret_bps = ((p_curr - p_prev) / p_prev) * 10_000.0

                        if bn_ret_bps >= signal_threshold_bps:
                            signals_triggered += 1
                            future_window = bithumb_events[i : min(i + 200, len(bithumb_events))]
                            trade = sim.execute_signal_with_latency("BUY", ev, future_window)
                            if trade is not None:
                                in_trade = True
                                exit_due_ts = trade.timestamp_ns + hz_ns

                    # If still in trade at the end, force close
                    if in_trade and len(bithumb_events) > 0:
                        last_ev = bithumb_events[-1]
                        sim.execute_signal_with_latency("SELL", last_ev, [last_ev])

                    summary = sim.get_pnl_summary()
                    trades = sim.get_trades()
                    n_round_trips = summary.get("round_trips", 0)
                    gross_pnl = summary.get("gross_pnl", 0.0)
                    net_pnl = summary.get("net_pnl", 0.0)
                    total_fees = summary.get("total_fees", 0.0)

                    # Compute average bps per round trip
                    turnover = n_round_trips * position_size_krw
                    gross_bps = (gross_pnl / turnover * 10_000.0) if turnover > 0 else 0.0
                    net_bps = (net_pnl / turnover * 10_000.0) if turnover > 0 else 0.0

                    # Count winning round trips
                    wins = 0
                    if len(trades) >= 2:
                        for idx_t in range(0, len(trades) - 1, 2):
                            buy_t = trades[idx_t]
                            sell_t = trades[idx_t + 1]
                            rt_gross = (sell_t.fill_price - buy_t.fill_price) * min(buy_t.fill_quantity, sell_t.fill_quantity)
                            rt_fees = buy_t.fee_krw + sell_t.fee_krw
                            if rt_gross - rt_fees > 0:
                                wins += 1

                    win_rate = (wins / n_round_trips) if n_round_trips > 0 else 0.0

                    verdict = (
                        "PROFITABLE_TAKER_CONFIRMED"
                        if net_bps > 0 and n_round_trips >= 10
                        else ("COST_KILLED_BY_SPREAD_AND_FEES" if net_bps < 0 else "INSUFFICIENT_TRADES")
                    )

                    scenario_res = {
                        "market": mkt,
                        "signal_exchange": "binance",
                        "lookback_lag_ms": lag_ms,
                        "holding_horizon_s": hz_s,
                        "execution_latency_ms": lat_ms,
                        "signals_triggered": signals_triggered,
                        "round_trips": n_round_trips,
                        "winning_round_trips": wins,
                        "win_rate": round(win_rate, 4),
                        "gross_pnl_krw": round(gross_pnl, 2),
                        "gross_bps": round(gross_bps, 2),
                        "total_fees_krw": round(total_fees, 2),
                        "net_pnl_krw": round(net_pnl, 2),
                        "net_bps": round(net_bps, 2),
                        "verdict": verdict,
                        "execution_model": "REAL_DEPTH_WALKING_ORDERBOOK_SIMULATION",
                    }
                    results.append(scenario_res)
                    print(
                        f"  [{mkt}] Lag={lag_ms}ms Hz={hz_s}s Lat={lat_ms}ms -> "
                        f"RTs={n_round_trips}, Net={net_bps:+.2f} bps, WinRate={win_rate*100:.1f}%, Verdict={verdict}"
                    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = REPORT_DIR / "CROSS_EXCHANGE_CONFIRMATION_EXECUTION.json"
    out_file.write_text(json.dumps(results, indent=2))
    print(f"\nSaved confirmation execution results to {out_file}")
    return results


if __name__ == "__main__":
    run_confirmation_execution()
