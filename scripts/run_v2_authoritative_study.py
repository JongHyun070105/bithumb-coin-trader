#!/usr/bin/env python3
"""Authoritative V2 30H Microstructure Research Pipeline.

Processes the authoritative V2 dataset (aws-validation-30h-20260912-6576f63).
DEV hours only (18/30). Skips tickers. Checkpointed per market.

Usage:
    PYTHONUNBUFFERED=1 .venv/bin/python scripts/run_v2_authoritative_study.py
"""

from __future__ import annotations

import json, math, resource, sys, time
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
CKPT_DIR = REPORT_DIR / "checkpoints"
DS = "aws-validation-30h-20260912-6576f63"

MARKETS = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-ADA", "KRW-SOL",
    "KRW-DOGE", "KRW-AVAX", "KRW-LINK", "KRW-DOT", "KRW-ETC",
    "KRW-SHIB", "KRW-NEAR", "KRW-APT", "KRW-AXS", "KRW-MANA",
    "KRW-SUI", "KRW-TRX", "KRW-XLM", "KRW-BCH", "KRW-SAND",
]

DEV_HOURS = {("2026-09-12", f"{d:02d}") for d in range(11, 24)} | \
            {("2026-09-13", f"{d:02d}") for d in range(0, 5)}

HORIZONS = [1, 5, 10, 30]
LATENCIES = [0.0, 100.0, 250.0, 500.0]
FEES = {"promotional": 0.0, "normal": 0.0025}
SIZE_KRW = 100_000
SIG_Q = 0.90


@dataclass
class Baseline:
    market: str; ob: int = 0; trade: int = 0
    dur_s: float = 0.0; spread_bps: float = 0.0; rate: float = 0.0

@dataclass
class Pred:
    feat: str; hz: int; mkt: str; n: int
    ic: float | None; hr: float | None; cls: str
    def to_dict(self): return asdict(self)

@dataclass
class Exec:
    lat: float; fee: str; rate: float; sig: int; att: int; trips: int
    net: float; mean_bps: float | None; win: float | None
    def to_dict(self): return asdict(self)


def _rss(): return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576

def _hour(fname):
    stem = fname
    for ext in (".jsonl.zst", ".jsonl"):
        if stem.endswith(ext): stem = stem[:-len(ext)]; break
    p = stem.split("_")
    return (p[3], p[4]) if len(p) >= 5 else None

def _pct(v, p):
    if not v: return 0.0
    s = sorted(v); k = (len(s)-1)*p/100; f, c = int(k), min(int(k)+1, len(s)-1)
    return s[f]*(c-k)+s[c]*(k-f) if f != c else s[f]

def _bps(e, x): return (x-e)/e*10000 if e > 0 else 0.0


def stream(mkt):
    """Stream DEV-hours orderbook+trade for one market. Skip tickers."""
    ml = mkt.lower().replace("-", "-")
    fe = FeatureEngine(market=mkt, exchange="bithumb")
    le = LabelEngine(tolerance_s=60.0)
    bl = Baseline(market=mkt)
    ob_f, tr_f = [], []
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
                (ob_f if feed == "orderbook" else tr_f).append(f)

    fvs = []
    ob_count = 0
    # Process OB files with subsampling: keep every SUB-th event for features
    # Feed every MID_SUB-th mid to label engine (sufficient for 30s horizon lookups)
    SUB = 15
    MID_SUB = 10
    for f in ob_f:
        for ev in iter_raw_jsonl_file(f, dataset_id=DS):
            ob_count += 1
            bl.ob += 1
            b, a = ev.payload.get("bids", []), ev.payload.get("asks", [])
            if b and a and ob_count % MID_SUB == 0:
                le.add_mid_observation(ev.ordering_timestamp_ns, (float(b[0][0])+float(a[0][0]))/2)
            if ob_count % SUB == 0:
                fv = fe.process_event(ev)
                if fv and fv.mid_price: fvs.append(fv)
    for f in tr_f:
        for ev in iter_raw_jsonl_file(f, dataset_id=DS):
            bl.trade += 1
            fv = fe.process_event(ev)
            if fv and fv.mid_price: fvs.append(fv)

    if not fvs: return [], [], bl
    labels = [le.compute_label(fv.timestamp_ns, "bithumb", mkt) for fv in fvs]
    sp = [f.spread_bps for f in fvs if f.spread_bps and f.spread_bps > 0]
    if sp: bl.spread_bps = sorted(sp)[len(sp)//2]
    if len(fvs) >= 2:
        bl.dur_s = (fvs[-1].timestamp_ns - fvs[0].timestamp_ns) / 1e9
        if bl.dur_s > 0: bl.rate = (bl.ob + bl.trade) / bl.dur_s
    return fvs, labels, bl


def predictive(fvs, labels, feats, hz, mkt):
    tgt = f"mid_return_{hz}s"
    res = []
    for fn in feats:
        fv = [getattr(f, fn, None) for f in fvs]
        tv = [getattr(l, tgt, None) for l in labels]
        pf, pt = [], []
        for a, b in zip(fv, tv):
            if a is not None and b is not None and math.isfinite(a) and math.isfinite(b):
                pf.append(a); pt.append(b)
        n = len(pf)
        if n < 50:
            res.append(Pred(fn, hz, mkt, n, None, None, "INSUFFICIENT")); continue
        ic, _ = compute_information_coefficient(fv, tv)
        hr, _ = compute_hit_rate(fv, tv, expected_sign="positive")
        cls = "FRAGILE"
        if ic is not None and hr is not None:
            if abs(ic) > 0.02 and hr > 0.52: cls = "EXPLORATORY_POSITIVE"
            elif abs(ic) < 0.005: cls = "EXPLORATORY_NEGATIVE"
        res.append(Pred(fn, hz, mkt, n, ic, hr, cls))
    return res


def execution(fvs, labels, feat, hz):
    tgt = f"mid_return_{hz}s"; fmid = f"future_mid_{hz}s"
    paired = []
    for i, (fv, lv) in enumerate(zip(fvs, labels)):
        f = getattr(fv, feat, None); t = getattr(lv, tgt, None); m = getattr(lv, fmid, None)
        if (f is not None and t is not None and m is not None
                and fv.best_bid and fv.best_ask and fv.best_bid > 0
                and fv.best_ask > 0 and fv.best_bid < fv.best_ask
                and math.isfinite(f) and math.isfinite(t)):
            paired.append((i, f))
    if len(paired) < 100: return []
    fs = sorted(p[1] for p in paired)
    thr = fs[int(len(fs)*SIG_Q)]
    sigs = [p[0] for p in paired if p[1] >= thr]
    if len(sigs) < 10: return []
    res = []
    for lat in LATENCIES:
        for fn, fr in FEES.items():
            a = ExecutionAssumptions(fee_regime=fn, fee_rate=fr, additional_impact_bps=0.0,
                latency_ms=lat, position_size_krw=SIZE_KRW, max_depth_levels=5, partial_fills_enabled=True)
            sim = ResearchExecutionSimulator(assumptions=a)
            att = comp = 0; rpbs = []
            for si in sigs:
                fv = fvs[si]; lv = labels[si]; m = getattr(lv, fmid, None)
                if not m or m <= 0: continue
                if not fv.best_bid or not fv.best_ask or fv.best_bid >= fv.best_ask: continue
                ts = fv.timestamp_ns
                # Use price-relative level offset (10% of price, min 1 tick)
                lvl_off = max(fv.best_bid * 0.1, 0.0001)
                sig_ev = CanonicalEvent(dataset_id=DS, source_run_id=None, collector_epoch=None,
                    source_file=None, source_file_offset=None, exchange="bithumb", market=fv.market,
                    event_kind=EventKind.ORDERBOOK, exchange_timestamp_ms=ts//1000000,
                    local_recv_timestamp_ms=ts//1000000, local_write_timestamp_ms=ts//1000000,
                    ordering_timestamp_ns=ts, exchange_timestamp_role="LOCAL_WRITE",
                    payload={"bids": [[fv.best_bid, fv.best_bid_size or 1], [fv.best_bid - lvl_off, 2]],
                             "asks": [[fv.best_ask, fv.best_ask_size or 1], [fv.best_ask + lvl_off, 2]],
                             "is_snapshot": True})
                buy = sim.execute_signal("BUY", sig_ev, fv.mid_price or 0)
                if not buy: continue
                att += 1
                sp = (fv.best_ask or 0) - (fv.best_bid or 0)
                ets = ts + hz * 1000000000
                # Price-relative offset for exit levels
                exit_lvl_off = max(m * 0.1, 0.0001)
                ex_ev = CanonicalEvent(dataset_id=DS, source_run_id=None, collector_epoch=None,
                    source_file=None, source_file_offset=None, exchange="bithumb", market=fv.market,
                    event_kind=EventKind.ORDERBOOK, exchange_timestamp_ms=ets//1000000,
                    local_recv_timestamp_ms=ets//1000000, local_write_timestamp_ms=ets//1000000,
                    ordering_timestamp_ns=ets, exchange_timestamp_role="LOCAL_WRITE",
                    payload={"bids": [[m-sp/2, 1], [max(m-sp/2 - exit_lvl_off, 0.0001), 2]],
                             "asks": [[m+sp/2, 1], [m+sp/2 + exit_lvl_off, 2]], "is_snapshot": True})
                sell = sim.execute_signal("SELL", ex_ev, m)
                if sell:
                    comp += 1; rpbs.append(_bps(buy.fill_price, sell.fill_price))
            pnl = sim.get_pnl_summary()
            mb = sum(rpbs)/len(rpbs) if rpbs else None
            w = sum(1 for r in rpbs if r > 0)/len(rpbs) if rpbs else None
            res.append(Exec(lat, fn, fr, len(sigs), att, comp, pnl["net_pnl"], mb, w))
    return res


def main():
    CKPT_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 70, flush=True)
    print("AUTHORITATIVE V2 MICROSTRUCTURE RESEARCH — DEV PHASE", flush=True)
    print(f"Dataset: {DS} | Markets: {len(MARKETS)} | V4: NOT TOUCHED", flush=True)
    print("=" * 70, flush=True)

    h1 = ["depth_imbalance_l1", "depth_imbalance_l5", "qi_l1", "qi_l5"]
    h2 = ["ati_5s", "ati_30s", "ati_60s", "signed_volume_30s"]
    h3 = ["microprice_bias_bps", "microprice_displacement"]

    all_bl, all_p, all_e = [], [], []
    times = []; t0 = time.time()

    for mi, mkt in enumerate(MARKETS):
        ck = CKPT_DIR / f"{mkt.replace('-','_')}.json"
        if ck.exists():
            with open(ck) as f: d = json.load(f)
            all_p.extend([Pred(**r) for r in d.get("pred", [])])
            all_e.extend([Exec(**r) for r in d.get("exec", [])])
            if d.get("bl"): all_bl.append(Baseline(**d["bl"]))
            times.append(d.get("t", 0))
            print(f"[{mi+1}/{len(MARKETS)}] {mkt}: checkpoint loaded", flush=True)
            continue

        print(f"\n[{mi+1}/{len(MARKETS)}] {mkt}...", flush=True)
        tc = time.time()
        fvs, labels, bl = stream(mkt)
        elapsed = time.time() - tc; times.append(elapsed)
        all_bl.append(bl)

        n = bl.ob + bl.trade
        if n < 1000:
            print(f"  SKIP ({n} events, {elapsed:.1f}s)", flush=True)
            with open(ck, "w") as f: json.dump({"mkt": mkt, "t": elapsed, "skip": True}, f)
            continue

        print(f"  OB={bl.ob:,} trade={bl.trade:,} spread={bl.spread_bps:.1f}bps "
              f"FVs={len(fvs):,} ({elapsed:.1f}s) RSS={_rss():.0f}MB", flush=True)

        cp, ce = [], []
        for name, fl in [("H1", h1), ("H2", h2), ("H3", h3)]:
            for hz in HORIZONS:
                rs = predictive(fvs, labels, fl, hz, mkt)
                all_p.extend(rs); cp.extend([r.to_dict() for r in rs])

        for name, ft in [("H1", h1[0]), ("H2", h2[0]), ("H3", h3[0])]:
            er = execution(fvs, labels, ft, 5)
            all_e.extend(er); ce.extend([r.to_dict() for r in er])

        with open(ck, "w") as f:
            json.dump({"mkt": mkt, "t": elapsed, "bl": asdict(bl), "pred": cp, "exec": ce}, f)

        avg = sum(times)/len(times); eta = avg * (len(MARKETS)-mi-1)
        print(f"  ETA: {eta/60:.0f}m", flush=True)

    # Summary
    total = time.time() - t0
    print(f"\n{'='*70}", flush=True)
    print(f"TOTAL: {total/60:.1f}min", flush=True)
    print(f"\nMARKET BASELINE:", flush=True)
    for b in sorted(all_bl, key=lambda x: x.ob, reverse=True):
        if b.ob > 0: print(f"  {b.market:12s} OB={b.ob:>8,} trade={b.trade:>6,} spread={b.spread_bps:.1f}bps", flush=True)

    print(f"\nTOP PREDICTIVE (by |IC|):", flush=True)
    for i, r in enumerate(sorted(all_p, key=lambda x: abs(x.ic) if x.ic else 0, reverse=True)[:30]):
        ic = f"{r.ic:.4f}" if r.ic else "N/A"
        print(f"  {i+1:2d}. [{r.mkt}] {r.feat} h={r.hz}s IC={ic} n={r.n:>6,} [{r.cls}]", flush=True)

    print(f"\nEXECUTION:", flush=True)
    for er in all_e:
        mb = f"{er.mean_bps:.1f}" if er.mean_bps else "N/A"
        w = f"{er.win:.3f}" if er.win else "N/A"
        print(f"  {er.lat:>5.0f}ms/{er.fee:<12}: trips={er.trips:>5} net={er.net:>12,.0f} mean={mb:>7} win={w}", flush=True)

    rpt = {"study": "V2 DEV", "dataset": DS, "elapsed_s": total,
           "baselines": [asdict(b) for b in all_bl],
           "predictive": [r.to_dict() for r in all_p],
           "execution": [r.to_dict() for r in all_e]}
    out = REPORT_DIR / "reports" / "V2_DEV_RESULTS.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f: json.dump(rpt, f, indent=2, default=str)
    print(f"\nResults: {out}", flush=True)
    pos = sum(1 for r in all_p if r.cls == "EXPLORATORY_POSITIVE")
    print(f"Predictive: {pos} POSITIVE | Execution: {sum(1 for e in all_e if e.net>0)}/{len(all_e)} profitable", flush=True)


if __name__ == "__main__":
    main()
