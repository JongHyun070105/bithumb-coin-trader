#!/usr/bin/env python3
"""Full-resolution V2 DEV study with REAL future-book execution.

Processes BTC, ETH, XRP on DEV hours (18h) with:
- NO event subsampling
- Real future orderbook for entry/exit
- Latency-aware book selection
- Proper execution failure tracking

Usage:
    PYTHONUNBUFFERED=1 .venv/bin/python scripts/run_v2_fullres_study.py
"""

from __future__ import annotations

import bisect
import json
import math
import resource
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bithumb_coin_trader.research_infra.adapters import iter_raw_jsonl_file
from bithumb_coin_trader.research_infra.canonical_events import CanonicalEvent, EventKind
from bithumb_coin_trader.research_infra.features import FeatureEngine, FeatureVector
from bithumb_coin_trader.research_infra.labels import LabelEngine, LabelVector
from bithumb_coin_trader.research_infra.evaluation import (
    compute_information_coefficient, compute_spearman_rank_ic,
    compute_hit_rate, compute_quantile_returns,
)
from bithumb_coin_trader.research_infra.execution import (
    ResearchExecutionSimulator, ExecutionAssumptions,
)

V2_DATA = ROOT / "data" / "research" / "v2"
REPORT_DIR = ROOT / "research-artifacts" / "v2-authoritative"
DS = "aws-validation-30h-20260912-6576f63"

MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]
DEV_HOURS = {("2026-09-12", f"{d:02d}") for d in range(11, 24)} | \
            {("2026-09-13", f"{d:02d}") for d in range(0, 5)}

HORIZONS = [1, 5, 10, 30]
LATENCIES = [0.0, 100.0, 250.0, 500.0]
FEES = {"promotional": 0.0, "normal": 0.0025}
SIZE_KRW = 100_000
SIG_Q = 0.90
MAX_EXEC_DELAY_MS = 5000  # Max allowed staleness for execution book


@dataclass
class Pred:
    feat: str; hz: int; mkt: str; n: int
    ic: float | None; sic: float | None; hr: float | None
    qr: list; cls: str
    def to_dict(self): return asdict(self)


@dataclass
class ExecResult:
    lat: float; fee: str; rate: float
    sig: int; attempts: int; no_entry: int; stale_entry: int
    partial_entry: int; no_exit: int; stale_exit: int
    partial_exit: int; residuals: int; complete: int
    gross: float; fees_total: float; net: float
    mean_bps: float | None; median_bps: float | None
    win_rate: float | None
    def to_dict(self): return asdict(self)


def _rss(): return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576

def _hour(fn):
    stem = fn
    for ext in (".jsonl.zst", ".jsonl"):
        if stem.endswith(ext): stem = stem[:-len(ext)]; break
    p = stem.split("_")
    return (p[3], p[4]) if len(p) >= 5 else None

def _pct(v, p):
    if not v: return 0.0
    s = sorted(v); k = (len(s)-1)*p/100; f, c = int(k), min(int(k)+1, len(s)-1)
    return s[f]*(c-k)+s[c]*(k-f) if f != c else s[f]

def _bps(e, x): return (x-e)/e*10000 if e > 0 else 0.0


class OrderbookBuffer:
    """Buffers orderbook snapshots for real execution lookup.

    Stores (timestamp_ns, bids_tuple, asks_tuple) sorted by timestamp.
    Supports efficient bisect lookup for first-valid-book-after-target.
    """
    def __init__(self):
        self._timestamps: list[int] = []
        self._bids: list[tuple] = []
        self._asks: list[tuple] = []

    def add(self, ts: int, bids: tuple, asks: tuple):
        self._timestamps.append(ts)
        self._bids.append(bids)
        self._asks.append(asks)

    def find_book_after(self, target_ns: int, max_delay_ns: int = MAX_EXEC_DELAY_MS * 1_000_000):
        """Find first orderbook at or after target_ns within max_delay."""
        idx = bisect.bisect_left(self._timestamps, target_ns)
        if idx >= len(self._timestamps):
            return None
        ts = self._timestamps[idx]
        if ts - target_ns > max_delay_ns:
            return None
        return (ts, self._bids[idx], self._asks[idx])

    def __len__(self):
        return len(self._timestamps)


def stream_market(mkt, ob_buffer: OrderbookBuffer):
    """Stream DEV events, populate ob_buffer, return features+labels."""
    ml = mkt.lower().replace("-", "-")
    fe = FeatureEngine(market=mkt, exchange="bithumb")
    le = LabelEngine(tolerance_s=60.0)
    ob_count = trade_count = 0

    ob_files, tr_files = [], []
    for dd in sorted(V2_DATA.iterdir()):
        if not dd.is_dir() or dd.name.startswith("."): continue
        ed = dd / "bithumb"
        if not ed.exists(): continue
        for fd in sorted(ed.iterdir()):
            if not fd.is_dir() or fd.name == "ticker": continue
            feed = fd.name
            for f in sorted(fd.iterdir()):
                fn = f.name
                if not (fn.endswith(".jsonl.zst") or fn.endswith(".jsonl")): continue
                if ml not in fn.lower(): continue
                h = _hour(fn)
                if not h or h not in DEV_HOURS: continue
                (ob_files if feed == "orderbook" else tr_files).append(f)

    # Process OB files: build features + populate buffer
    fvs = []
    for f in ob_files:
        for ev in iter_raw_jsonl_file(f, dataset_id=DS):
            ob_count += 1
            bids = ev.payload.get("bids", [])
            asks = ev.payload.get("asks", [])
            if bids and asks:
                bid_t = tuple((float(p), float(s)) for p, s in bids)
                ask_t = tuple((float(p), float(s)) for p, s in asks)
                ob_buffer.add(ev.ordering_timestamp_ns, bid_t, ask_t)
                mid = (float(bids[0][0]) + float(asks[0][0])) / 2.0
                le.add_mid_observation(ev.ordering_timestamp_ns, mid)
            fv = fe.process_event(ev)
            if fv and fv.mid_price: fvs.append(fv)

    for f in tr_files:
        for ev in iter_raw_jsonl_file(f, dataset_id=DS):
            trade_count += 1
            fv = fe.process_event(ev)
            if fv and fv.mid_price: fvs.append(fv)

    if not fvs: return [], [], ob_count, trade_count
    labels = [le.compute_label(fv.timestamp_ns, "bithumb", mkt) for fv in fvs]
    return fvs, labels, ob_count, trade_count


def run_predictive(fvs, labels, feats, hz, mkt):
    tgt = f"mid_return_{hz}s"
    res = []
    for fn in feats:
        fv_vals = [getattr(f, fn, None) for f in fvs]
        tv_vals = [getattr(l, tgt, None) for l in labels]
        pf, pt = [], []
        for a, b in zip(fv_vals, tv_vals):
            if a is not None and b is not None and math.isfinite(a) and math.isfinite(b):
                pf.append(a); pt.append(b)
        n = len(pf)
        if n < 50:
            res.append(Pred(fn, hz, mkt, n, None, None, None, [], "INSUFFICIENT")); continue
        ic, _ = compute_information_coefficient(fv_vals, tv_vals)
        sic, _ = compute_spearman_rank_ic(fv_vals, tv_vals)
        hr, _ = compute_hit_rate(fv_vals, tv_vals, expected_sign="positive")
        qr = compute_quantile_returns(fv_vals, tv_vals, n_quantiles=5)
        cls = "FRAGILE"
        if ic is not None and hr is not None:
            if abs(ic) > 0.02 and hr > 0.52: cls = "EXPLORATORY_POSITIVE"
            elif abs(ic) < 0.005: cls = "EXPLORATORY_NEGATIVE"
        res.append(Pred(fn, hz, mkt, n, ic, sic, hr, qr, cls))
    return res


def run_execution(fvs, labels, ob_buffer: OrderbookBuffer, feat, hz):
    """Real future-book execution with latency scenarios.

    Optimized: pre-selects signals once, pre-computes book indices per latency,
    reuses entry/exit across fee scenarios.
    """
    fmid = f"future_mid_{hz}s"
    results = []

    # Pre-select signals ONCE
    sig_data = []  # (fv_idx, future_mid, signal_ts, best_bid, best_ask, best_bid_size, best_ask_size)
    for i, (fv, lv) in enumerate(zip(fvs, labels)):
        f = getattr(fv, feat, None)
        if f is None: continue
        m = getattr(lv, fmid, None)
        if m is None or m <= 0: continue
        if not fv.best_bid or not fv.best_ask: continue
        if fv.best_bid <= 0 or fv.best_ask <= 0 or fv.best_bid >= fv.best_ask: continue
        if not math.isfinite(f): continue
        tgt = getattr(lv, f"mid_return_{hz}s", None)
        if tgt is None or not math.isfinite(tgt): continue
        sig_data.append((i, m, fv.timestamp_ns, fv.best_bid, fv.best_ask,
                          fv.best_bid_size or 1.0, fv.best_ask_size or 1.0, f))

    if len(sig_data) < 100: return []

    # Select top SIG_Q signals by feature value
    feat_vals = sorted(s[7] for s in sig_data)
    thr = feat_vals[int(len(feat_vals) * SIG_Q)]
    sig_data = [s for s in sig_data if s[7] >= thr]
    if len(sig_data) < 10: return []

    n_sigs = len(sig_data)
    ob_ts = ob_buffer._timestamps

    for lat in LATENCIES:
        lat_ns = int(lat * 1_000_000)
        hz_ns = int(hz * 1_000_000_000)
        max_delay_ns = MAX_EXEC_DELAY_MS * 1_000_000

        # Pre-compute entry/exit book indices for ALL signals at this latency
        entry_indices = []
        exit_indices = []
        for _, _, sig_ts, _, _, _, _, _ in sig_data:
            entry_target = sig_ts + lat_ns
            eidx = bisect.bisect_left(ob_ts, entry_target)
            entry_indices.append(eidx if eidx < len(ob_ts) else -1)

            exit_target = sig_ts + hz_ns + lat_ns
            xidx = bisect.bisect_left(ob_ts, exit_target)
            exit_indices.append(xidx if xidx < len(ob_ts) else -1)

        for fn, fr in FEES.items():
            a = ExecutionAssumptions(
                fee_regime=fn, fee_rate=fr, additional_impact_bps=0.0,
                latency_ms=lat, position_size_krw=SIZE_KRW,
                max_depth_levels=5, partial_fills_enabled=True)
            sim = ResearchExecutionSimulator(assumptions=a)

            attempts = no_entry = stale_entry = 0
            no_exit = stale_exit = complete = 0
            rpbs = []

            for si_idx, (fv_idx, m, sig_ts, bb, ba, bbs, bas, _) in enumerate(sig_data):
                eidx = entry_indices[si_idx]
                if eidx < 0:
                    no_entry += 1; continue

                entry_ts = ob_ts[eidx]
                if entry_ts < sig_ts:
                    stale_entry += 1; continue
                if entry_ts - (sig_ts + lat_ns) > max_delay_ns:
                    stale_entry += 1; continue

                entry_bids = ob_buffer._bids[eidx]
                entry_asks = ob_buffer._asks[eidx]
                entry_mid = (entry_bids[0][0] + entry_asks[0][0]) / 2.0
                if entry_mid <= 0:
                    no_entry += 1; continue

                entry_ev = CanonicalEvent(
                    dataset_id=DS, source_run_id=None, collector_epoch=None,
                    source_file=None, source_file_offset=None,
                    exchange="bithumb", market=fvs[fv_idx].market,
                    event_kind=EventKind.ORDERBOOK,
                    exchange_timestamp_ms=entry_ts // 1_000_000,
                    local_recv_timestamp_ms=entry_ts // 1_000_000,
                    local_write_timestamp_ms=entry_ts // 1_000_000,
                    ordering_timestamp_ns=entry_ts,
                    exchange_timestamp_role="LOCAL_WRITE",
                    payload={"bids": entry_bids, "asks": entry_asks, "is_snapshot": True})

                buy = sim.execute_signal("BUY", entry_ev, entry_mid)
                if not buy:
                    no_entry += 1; continue
                attempts += 1

                xidx = exit_indices[si_idx]
                if xidx < 0:
                    no_exit += 1; continue

                exit_ts = ob_ts[xidx]
                exit_target = sig_ts + hz_ns + lat_ns
                if exit_ts - exit_target > max_delay_ns:
                    stale_exit += 1; continue

                exit_bids = ob_buffer._bids[xidx]
                exit_asks = ob_buffer._asks[xidx]
                exit_mid = (exit_bids[0][0] + exit_asks[0][0]) / 2.0
                if exit_mid <= 0:
                    no_exit += 1; continue

                exit_ev = CanonicalEvent(
                    dataset_id=DS, source_run_id=None, collector_epoch=None,
                    source_file=None, source_file_offset=None,
                    exchange="bithumb", market=fvs[fv_idx].market,
                    event_kind=EventKind.ORDERBOOK,
                    exchange_timestamp_ms=exit_ts // 1_000_000,
                    local_recv_timestamp_ms=exit_ts // 1_000_000,
                    local_write_timestamp_ms=exit_ts // 1_000_000,
                    ordering_timestamp_ns=exit_ts,
                    exchange_timestamp_role="LOCAL_WRITE",
                    payload={"bids": exit_bids, "asks": exit_asks, "is_snapshot": True})

                sell = sim.execute_signal("SELL", exit_ev, exit_mid)
                if not sell:
                    no_exit += 1; continue

                complete += 1
                rpbs.append(_bps(buy.fill_price, sell.fill_price))

            pnl = sim.get_pnl_summary()
            mb = sum(rpbs) / len(rpbs) if rpbs else None
            med = _pct(rpbs, 50) if rpbs else None
            w = sum(1 for r in rpbs if r > 0) / len(rpbs) if rpbs else None
            results.append(ExecResult(
                lat, fn, fr, n_sigs, attempts, no_entry, stale_entry,
                0, no_exit, stale_exit, 0, 0,
                complete, pnl["gross_pnl"], pnl["total_fees"], pnl["net_pnl"],
                mb, med, w))
    return results


def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 70, flush=True)
    print("FULL-RESOLUTION V2 DEV STUDY — BTC / ETH / XRP", flush=True)
    print("Real future-book execution | No subsampling", flush=True)
    print(f"Dataset: {DS} | V4: NOT TOUCHED | ALPHA: UNPROVEN", flush=True)
    print("=" * 70, flush=True)

    h1 = ["depth_imbalance_l1", "depth_imbalance_l5", "qi_l1", "qi_l3", "qi_l5"]
    h2 = ["ati_5s", "ati_30s", "ati_60s", "signed_volume_30s"]
    h3 = ["microprice_bias_bps", "microprice_displacement"]

    all_pred, all_exec = [], []
    t_total = time.time()

    for mi, mkt in enumerate(MARKETS):
        print(f"\n[{mi+1}/{len(MARKETS)}] {mkt}: streaming (FULL RESOLUTION)...", flush=True)
        t0 = time.time()
        ob_buf = OrderbookBuffer()
        fvs, labels, ob_n, tr_n = stream_market(mkt, ob_buf)
        elapsed = time.time() - t0
        print(f"  OB={ob_n:,} trade={tr_n:,} FVs={len(fvs):,} ob_buffer={len(ob_buf):,} "
              f"({elapsed:.1f}s) RSS={_rss():.0f}MB", flush=True)

        if len(fvs) < 100:
            print(f"  SKIP: insufficient features", flush=True)
            continue

        # Predictive
        for name, fl in [("H1", h1), ("H2", h2), ("H3", h3)]:
            print(f"  {name}:", flush=True)
            for hz in HORIZONS:
                rs = run_predictive(fvs, labels, fl, hz, mkt)
                all_pred.extend(rs)
                for r in rs:
                    ic = f"{r.ic:.4f}" if r.ic else "N/A"
                    hr_s = f"{r.hr:.3f}" if r.hr else "N/A"
                    print(f"    {r.feat} h={hz}s IC={ic} HR={hr_s} n={r.n:>8,} [{r.cls}]", flush=True)

        # Execution (real future-book)
        print(f"  Execution (real future-book):", flush=True)
        for name, ft in [("H1", h1[0]), ("H2", h2[0]), ("H3", h3[0])]:
            er = run_execution(fvs, labels, ob_buf, ft, 5)
            all_exec.extend(er)
            for r in er:
                mb = f"{r.mean_bps:.2f}" if r.mean_bps else "N/A"
                w = f"{r.win_rate:.3f}" if r.win_rate else "N/A"
                print(f"    {name}/{ft} {r.lat:.0f}ms/{r.fee}: "
                      f"complete={r.complete} net={r.net:>12,.0f} mean={mb} win={w} "
                      f"no_entry={r.no_entry} no_exit={r.no_exit}", flush=True)

    total = time.time() - t_total
    print(f"\n{'='*70}", flush=True)
    print(f"TOTAL: {total/60:.1f}min", flush=True)

    # Summary
    print(f"\nPREDICTIVE (top 20 by |IC|):", flush=True)
    for i, r in enumerate(sorted(all_pred, key=lambda x: abs(x.ic) if x.ic else 0, reverse=True)[:20]):
        ic = f"{r.ic:.4f}" if r.ic else "N/A"
        print(f"  {i+1:2d}. [{r.mkt}] {r.feat} h={r.hz}s IC={ic} n={r.n:>8,} [{r.cls}]", flush=True)

    print(f"\nEXECUTION SUMMARY:", flush=True)
    for r in all_exec:
        mb = f"{r.mean_bps:.2f}" if r.mean_bps else "N/A"
        med = f"{r.median_bps:.2f}" if r.median_bps else "N/A"
        w = f"{r.win_rate:.3f}" if r.win_rate else "N/A"
        print(f"  {r.lat:>5.0f}ms/{r.fee:<12}: complete={r.complete:>5} "
              f"net={r.net:>14,.0f} mean={mb:>10} med={med:>10} win={w} "
              f"no_ent={r.no_entry} no_ex={r.no_exit}", flush=True)

    # Save
    rpt = {
        "study": "V2 FULL-RESOLUTION DEV — BTC/ETH/XRP",
        "dataset": DS, "role": "DEVELOPMENT / EXPLORATORY ONLY",
        "alpha": "UNPROVEN", "v4_touched": False,
        "resolution": "FULL (no subsampling)",
        "execution": "REAL FUTURE-BOOK",
        "elapsed_s": total,
        "predictive": [r.to_dict() for r in all_pred],
        "execution": [r.to_dict() for r in all_exec],
    }
    out = REPORT_DIR / "reports" / "V2_FULLRES_DEV_RESULTS.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(rpt, f, indent=2, default=str)
    print(f"\nResults: {out}", flush=True)

    pos = sum(1 for r in all_pred if r.cls == "EXPLORATORY_POSITIVE")
    exec_pos = sum(1 for r in all_exec if r.net > 0)
    print(f"Predictive: {pos} POSITIVE | Execution: {exec_pos}/{len(all_exec)} profitable", flush=True)


if __name__ == "__main__":
    main()
