#!/usr/bin/env python3
"""Authoritative V2 30H Microstructure Research Pipeline.

Processes the authoritative V2 dataset (aws-validation-30h-20260912-6576f63)
from S3-downloaded zstd-compressed JSONL files.

Usage:
    .venv/bin/python scripts/run_v2_authoritative_study.py
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bithumb_coin_trader.research_infra.adapters import iter_raw_jsonl_file
from bithumb_coin_trader.research_infra.canonical_events import (
    CanonicalEvent, EventKind,
)
from bithumb_coin_trader.research_infra.features import FeatureEngine, FeatureVector
from bithumb_coin_trader.research_infra.labels import LabelEngine, LabelVector
from bithumb_coin_trader.research_infra.evaluation import (
    compute_information_coefficient,
    compute_spearman_rank_ic,
    compute_hit_rate,
    compute_quantile_returns,
)
from bithumb_coin_trader.research_infra.execution import (
    ResearchExecutionSimulator,
    ExecutionAssumptions,
    SimulatedTrade,
)

# ── Constants ──────────────────────────────────────────────────
V2_DATA_ROOT = ROOT / "data" / "research" / "v2"
REPORT_DIR = ROOT / "research-artifacts" / "v2-authoritative"
DATASET_ID = "aws-validation-30h-20260912-6576f63"
EXCHANGE = "bithumb"

# Bithumb markets (from V2 feed universe)
BITHUMB_MARKETS = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-ADA", "KRW-SOL",
    "KRW-DOGE", "KRW-AVAX", "KRW-LINK", "KRW-DOT", "KRW-ETC",
    "KRW-SHIB", "KRW-NEAR", "KRW-APT", "KRW-AXS", "KRW-MANA",
    "KRW-SUI", "KRW-TRX", "KRW-XLM", "KRW-BCH", "KRW-SAND",
]

# Chronological split hours (first 18 = DEV)
DEV_HOURS = set()
for d in range(11, 24):  # 2026-09-12_11 through _23
    DEV_HOURS.add(("2026-09-12", f"{d:02d}"))
for d in range(0, 5):  # 2026-09-13_00 through _04
    DEV_HOURS.add(("2026-09-13", f"{d:02d}"))

VAL_HOURS = set()
for d in range(5, 11):  # 2026-09-13_05 through _10
    VAL_HOURS.add(("2026-09-13", f"{d:02d}"))

TEST_HOURS = set()
for d in range(11, 17):  # 2026-09-13_11 through _16
    TEST_HOURS.add(("2026-09-13", f"{d:02d}"))

# Research parameters
HORIZONS_S = [1, 5, 10, 30]
LATENCY_SCENARIOS = [0.0, 100.0, 250.0, 500.0]
FEE_SCENARIOS = {
    "promotional": {"fee_rate": 0.0, "description": "Zero-fee promotional"},
    "normal": {"fee_rate": 0.0025, "description": "Normal 0.25% taker fee"},
}
POSITION_SIZE_KRW = 100_000
SIGNAL_QUANTILE = 0.90


@dataclass
class MarketBaseline:
    market: str
    orderbook_events: int = 0
    trade_events: int = 0
    ticker_events: int = 0
    duration_s: float = 0.0
    median_spread_bps: float = 0.0
    events_per_sec: float = 0.0


@dataclass
class PredictiveResult:
    feature: str
    horizon_s: int
    market: str
    n: int
    pearson_ic: float | None
    spearman_ic: float | None
    hit_rate: float | None
    quantile_returns: list[dict[str, Any]]
    classification: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionResult:
    latency_ms: float
    fee_scenario: str
    fee_rate: float
    signals: int
    entry_attempts: int
    completed_round_trips: int
    no_book_rejects: int
    fill_rate: float
    gross_pnl: float
    total_fees: float
    net_pnl: float
    mean_net_bps: float | None
    median_net_bps: float | None
    win_rate: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Helpers ────────────────────────────────────────────────────
def _hour_from_path(path: Path) -> tuple[str, str]:
    """Extract (date, hour) from a V2 file path."""
    parts = path.name.replace(".jsonl.zst", "").replace(".jsonl", "").split("_")
    if len(parts) >= 5:
        return (f"{parts[3]}", parts[4])
    return ("", "")


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)


def _bps(entry: float, exit: float) -> float:
    if entry <= 0:
        return 0.0
    return (exit - entry) / entry * 10_000.0


# ── Phase 1: Stream one market ────────────────────────────────
def stream_market(
    market: str,
    max_events: int | None = None,
) -> tuple[list[FeatureVector], list[LabelVector], MarketBaseline]:
    """Stream V2 events for one Bithumb market, return features and labels.

    Processes files in chronological order (date/hour/feed) without loading
    all events into memory. Events within each file are in order.
    Cross-file ordering uses file path as proxy (sufficient for partitioned data).
    """
    market_lower = market.lower().replace("-", "-")
    fe = FeatureEngine(market=market, exchange=EXCHANGE)
    le = LabelEngine(tolerance_s=60.0)
    baseline = MarketBaseline(market=market)

    # Collect and sort files
    files: list[Path] = []
    for date_dir in sorted(V2_DATA_ROOT.iterdir()):
        if not date_dir.is_dir() or date_dir.name.startswith("."):
            continue
        exchange_dir = date_dir / EXCHANGE
        if not exchange_dir.exists():
            continue
        for feed_dir in sorted(exchange_dir.iterdir()):
            if not feed_dir.is_dir():
                continue
            for f in sorted(feed_dir.iterdir()):
                if not (f.name.endswith(".jsonl.zst") or f.name.endswith(".jsonl")):
                    continue
                if market_lower not in f.name.lower():
                    continue
                files.append(f)

    # Phase 1a: Stream events through feature engine, collecting mids
    # Process orderbook files first (they define mid prices), then trades
    ob_files = [f for f in files if "orderbook" in str(f)]
    trade_files = [f for f in files if "trade" in str(f)]
    ticker_files = [f for f in files if "ticker" in str(f)]

    features: list[FeatureVector] = []
    ob_count = 0
    trade_count = 0

    # Process orderbook files first to build mid history
    for f in ob_files:
        for event in iter_raw_jsonl_file(f, dataset_id=DATASET_ID):
            ob_count += 1
            if max_events and ob_count > max_events:
                break
            baseline.orderbook_events += 1
            bids = event.payload.get("bids", [])
            asks = event.payload.get("asks", [])
            if bids and asks:
                mid = (float(bids[0][0]) + float(asks[0][0])) / 2.0
                le.add_mid_observation(event.ordering_timestamp_ns, mid)
            fv = fe.process_event(event)
            if fv is not None and fv.mid_price is not None:
                features.append(fv)
        if max_events and ob_count > max_events:
            break

    # Then trade files (for H2 features) - process all, no separate limit
    for f in trade_files:
        for event in iter_raw_jsonl_file(f, dataset_id=DATASET_ID):
            trade_count += 1
            baseline.trade_events += 1
            fv = fe.process_event(event)
            if fv is not None and fv.mid_price is not None:
                features.append(fv)

    # Ticker files (minimal)
    for f in ticker_files:
        for event in iter_raw_jsonl_file(f, dataset_id=DATASET_ID):
            baseline.ticker_events += 1
            fv = fe.process_event(event)
            if fv is not None and fv.mid_price is not None:
                features.append(fv)

    if not features:
        return [], [], baseline

    # Phase 1b: Backfill labels (all mids now loaded)
    labels = [le.compute_label(fv.timestamp_ns, EXCHANGE, market)
              for fv in features]

    # Baseline stats
    spreads = [f.spread_bps for f in features
               if f.spread_bps is not None and f.spread_bps > 0]
    if spreads:
        baseline.median_spread_bps = sorted(spreads)[len(spreads) // 2]
    total = baseline.orderbook_events + baseline.trade_events
    timestamps = [fv.timestamp_ns for fv in features]
    if len(timestamps) >= 2:
        baseline.duration_s = (timestamps[-1] - timestamps[0]) / 1e9
        if baseline.duration_s > 0:
            baseline.events_per_sec = total / baseline.duration_s

    return features, labels, baseline


# ── Phase 2: Predictive analysis ──────────────────────────────
def run_predictive(
    features: list[FeatureVector],
    labels: list[LabelVector],
    feature_names: list[str],
    horizon_s: int,
    market: str,
    expected_sign: str = "positive",
) -> list[PredictiveResult]:
    target_attr = f"mid_return_{horizon_s}s"
    results = []
    for fname in feature_names:
        feat_vals = [getattr(fv, fname, None) for fv in features]
        target_vals = [getattr(lv, target_attr, None) for lv in labels]
        paired_f, paired_t = [], []
        for f, t in zip(feat_vals, target_vals):
            if (f is not None and t is not None
                    and math.isfinite(f) and math.isfinite(t)):
                paired_f.append(f)
                paired_t.append(t)
        n = len(paired_f)
        if n < 50:
            results.append(PredictiveResult(
                feature=fname, horizon_s=horizon_s, market=market, n=n,
                pearson_ic=None, spearman_ic=None, hit_rate=None,
                quantile_returns=[], classification="INSUFFICIENT_DATA"))
            continue

        pearson_ic, _ = compute_information_coefficient(feat_vals, target_vals)
        spearman_ic, _ = compute_spearman_rank_ic(feat_vals, target_vals)
        hr, _ = compute_hit_rate(feat_vals, target_vals, expected_sign=expected_sign)
        qr = compute_quantile_returns(feat_vals, target_vals, n_quantiles=5)

        if pearson_ic is not None and hr is not None:
            if abs(pearson_ic) > 0.02 and hr > 0.52:
                cls = "EXPLORATORY_POSITIVE"
            elif abs(pearson_ic) < 0.005:
                cls = "EXPLORATORY_NEGATIVE"
            else:
                cls = "FRAGILE"
        else:
            cls = "INSUFFICIENT_DATA"
        results.append(PredictiveResult(
            feature=fname, horizon_s=horizon_s, market=market, n=n,
            pearson_ic=pearson_ic, spearman_ic=spearman_ic, hit_rate=hr,
            quantile_returns=qr, classification=cls))
    return results


# ── Phase 3: Execution simulation ─────────────────────────────
def _make_ob_event(fv: FeatureVector, ts_ns: int | None = None) -> CanonicalEvent | None:
    if fv.best_bid is None or fv.best_ask is None:
        return None
    if fv.best_bid <= 0 or fv.best_ask <= 0 or fv.best_bid >= fv.best_ask:
        return None
    ts = ts_ns or fv.timestamp_ns
    return CanonicalEvent(
        dataset_id=DATASET_ID, source_run_id=None, collector_epoch=None,
        source_file=None, source_file_offset=None,
        exchange=EXCHANGE, market=fv.market,
        event_kind=EventKind.ORDERBOOK,
        exchange_timestamp_ms=ts // 1_000_000,
        local_recv_timestamp_ms=ts // 1_000_000,
        local_write_timestamp_ms=ts // 1_000_000,
        ordering_timestamp_ns=ts,
        exchange_timestamp_role="LOCAL_WRITE",
        payload={
            "bids": [[fv.best_bid, fv.best_bid_size or 1.0],
                     [fv.best_bid - 10_000, 2.0]],
            "asks": [[fv.best_ask, fv.best_ask_size or 1.0],
                     [fv.best_ask + 10_000, 2.0]],
            "is_snapshot": True,
        },
    )


def run_execution(
    features: list[FeatureVector],
    labels: list[LabelVector],
    signal_feature: str,
    horizon_s: int,
) -> list[ExecutionResult]:
    target_attr = f"mid_return_{horizon_s}s"
    future_mid_attr = f"future_mid_{horizon_s}s"
    results = []

    paired = []
    for i, (fv, lv) in enumerate(zip(features, labels)):
        feat_val = getattr(fv, signal_feature, None)
        target_val = getattr(lv, target_attr, None)
        future_mid = getattr(lv, future_mid_attr, None)
        if (feat_val is not None and target_val is not None
                and future_mid is not None
                and fv.best_bid is not None and fv.best_ask is not None
                and fv.best_bid > 0 and fv.best_ask > 0
                and fv.best_bid < fv.best_ask):
            if math.isfinite(feat_val) and math.isfinite(target_val):
                paired.append((i, feat_val, target_val))

    if len(paired) < 100:
        return results

    feat_sorted = sorted(p[1] for p in paired)
    threshold = feat_sorted[int(len(feat_sorted) * SIGNAL_QUANTILE)]
    signal_indices = [p[0] for p in paired if p[1] >= threshold]
    n_signals = len(signal_indices)
    if n_signals < 10:
        return results

    for latency_ms in LATENCY_SCENARIOS:
        for fee_name, fee_cfg in FEE_SCENARIOS.items():
            assumptions = ExecutionAssumptions(
                fee_regime=fee_name, fee_rate=fee_cfg["fee_rate"],
                additional_impact_bps=0.0, latency_ms=latency_ms,
                position_size_krw=POSITION_SIZE_KRW, max_depth_levels=5,
                partial_fills_enabled=True,
                description=f"{fee_cfg['description']}, {latency_ms}ms latency",
            )
            sim = ResearchExecutionSimulator(assumptions=assumptions)
            entry_attempts = completed = no_book = 0
            round_trip_bps: list[float] = []

            for sig_idx in signal_indices:
                fv = features[sig_idx]
                lv = labels[sig_idx]
                future_mid = getattr(lv, future_mid_attr, None)
                if future_mid is None or future_mid <= 0:
                    continue
                signal_event = _make_ob_event(fv)
                if signal_event is None:
                    no_book += 1
                    continue
                buy_trade = sim.execute_signal("BUY", signal_event,
                                               fv.mid_price or 0.0)
                if buy_trade is None:
                    no_book += 1
                    continue
                entry_attempts += 1

                # Exit using future mid from label
                spread = (fv.best_ask or 0) - (fv.best_bid or 0)
                half_spread = spread / 2.0
                exit_bid = future_mid - half_spread
                exit_ask = future_mid + half_spread
                exit_ts = fv.timestamp_ns + horizon_s * 1_000_000_000
                exit_event = CanonicalEvent(
                    dataset_id=DATASET_ID, source_run_id=None, collector_epoch=None,
                    source_file=None, source_file_offset=None,
                    exchange=EXCHANGE, market=fv.market,
                    event_kind=EventKind.ORDERBOOK,
                    exchange_timestamp_ms=exit_ts // 1_000_000,
                    local_recv_timestamp_ms=exit_ts // 1_000_000,
                    local_write_timestamp_ms=exit_ts // 1_000_000,
                    ordering_timestamp_ns=exit_ts,
                    exchange_timestamp_role="LOCAL_WRITE",
                    payload={
                        "bids": [[exit_bid, 1.0], [exit_bid - 10_000, 2.0]],
                        "asks": [[exit_ask, 1.0], [exit_ask + 10_000, 2.0]],
                        "is_snapshot": True,
                    },
                )
                sell_trade = sim.execute_signal("SELL", exit_event, future_mid)
                if sell_trade:
                    completed += 1
                    round_trip_bps.append(_bps(buy_trade.fill_price,
                                                sell_trade.fill_price))

            pnl = sim.get_pnl_summary()
            mean_bps = (sum(round_trip_bps) / len(round_trip_bps)
                        if round_trip_bps else None)
            median_bps = _percentile(round_trip_bps, 50) if round_trip_bps else None
            wins = sum(1 for r in round_trip_bps if r > 0)
            win_rate = wins / len(round_trip_bps) if round_trip_bps else None

            results.append(ExecutionResult(
                latency_ms=latency_ms, fee_scenario=fee_name,
                fee_rate=fee_cfg["fee_rate"], signals=n_signals,
                entry_attempts=entry_attempts, completed_round_trips=completed,
                no_book_rejects=no_book,
                fill_rate=completed / entry_attempts if entry_attempts > 0 else 0.0,
                gross_pnl=pnl["gross_pnl"], total_fees=pnl["total_fees"],
                net_pnl=pnl["net_pnl"], mean_net_bps=mean_bps,
                median_net_bps=median_bps, win_rate=win_rate,
            ))
    return results


# ── Main ───────────────────────────────────────────────────────
def main() -> None:
    print("=" * 70)
    print("AUTHORITATIVE V2 30H MICROSTRUCTURE PROFITABILITY RESEARCH")
    print(f"Dataset: {DATASET_ID}")
    print(f"Data: {V2_DATA_ROOT}")
    print("Role: DEVELOPMENT / EXPLORATORY ONLY")
    print("V4: NOT USED, NOT TOUCHED")
    print("ALPHA: UNPROVEN")
    print("=" * 70)

    # Hypothesis definitions
    h1_features = ["depth_imbalance_l1", "depth_imbalance_l5", "qi_l1", "qi_l5"]
    h2_features = ["ati_5s", "ati_30s", "ati_60s", "signed_volume_30s"]
    h3_features = ["microprice_bias_bps", "microprice_displacement"]

    all_baselines: list[MarketBaseline] = []
    all_predictive: list[PredictiveResult] = []
    all_execution: list[ExecutionResult] = []
    trial_id = 0

    # Process each Bithumb market
    for market in BITHUMB_MARKETS:
        print(f"\n{'='*60}")
        print(f"Processing {market}...")
        t0 = time.time()

        features, labels, baseline = stream_market(market)
        all_baselines.append(baseline)
        elapsed = time.time() - t0

        total_events = baseline.orderbook_events + baseline.trade_events
        if total_events < 1000:
            print(f"  SKIP: insufficient events ({total_events})")
            continue

        print(f"  Events: {total_events:,} (OB={baseline.orderbook_events:,}, "
              f"trade={baseline.trade_events:,})")
        print(f"  Duration: {baseline.duration_s/3600:.1f}h, "
              f"spread={baseline.median_spread_bps:.1f}bps, "
              f"rate={baseline.events_per_sec:.1f}/s")
        print(f"  Features: {len(features):,} vectors ({elapsed:.1f}s)")

        # DEV split: filter to DEV hours only
        dev_features = []
        dev_labels = []
        for fv, lv in zip(features, labels):
            ts_s = fv.timestamp_ns / 1_000_000_000
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(ts_s, tz=timezone.utc)
            date_str = dt.strftime("%Y-%m-%d")
            hour_str = dt.strftime("%H")
            if (date_str, hour_str) in DEV_HOURS:
                dev_features.append(fv)
                dev_labels.append(lv)

        print(f"  DEV features: {len(dev_features):,}")

        if len(dev_features) < 100:
            print(f"  SKIP DEV: insufficient DEV features")
            continue

        # H1: Orderbook imbalance
        print(f"\n  --- H1: Orderbook Imbalance ---")
        for horizon in HORIZONS_S:
            results = run_predictive(dev_features, dev_labels,
                                     h1_features, horizon, market)
            all_predictive.extend(results)
            trial_id += len(results)
            for r in results:
                ic = f"{r.pearson_ic:.4f}" if r.pearson_ic is not None else "N/A"
                hr = f"{r.hit_rate:.3f}" if r.hit_rate is not None else "N/A"
                print(f"    {r.feature} h={horizon}s: IC={ic} HR={hr} n={r.n} [{r.classification}]")

        # H2: Trade imbalance
        print(f"\n  --- H2: Trade Imbalance (ATI) ---")
        for horizon in HORIZONS_S:
            results = run_predictive(dev_features, dev_labels,
                                     h2_features, horizon, market)
            all_predictive.extend(results)
            trial_id += len(results)
            for r in results:
                ic = f"{r.pearson_ic:.4f}" if r.pearson_ic is not None else "N/A"
                hr = f"{r.hit_rate:.3f}" if r.hit_rate is not None else "N/A"
                print(f"    {r.feature} h={horizon}s: IC={ic} HR={hr} n={r.n} [{r.classification}]")

        # H3: Microprice
        print(f"\n  --- H3: Microprice Displacement ---")
        for horizon in HORIZONS_S:
            results = run_predictive(dev_features, dev_labels,
                                     h3_features, horizon, market)
            all_predictive.extend(results)
            trial_id += len(results)
            for r in results:
                ic = f"{r.pearson_ic:.4f}" if r.pearson_ic is not None else "N/A"
                hr = f"{r.hit_rate:.3f}" if r.hit_rate is not None else "N/A"
                print(f"    {r.feature} h={horizon}s: IC={ic} HR={hr} n={r.n} [{r.classification}]")

        # Execution for best feature per hypothesis (first feature as proxy)
        print(f"\n  --- Execution ---")
        for hyp_name, best_feat in [("H1", h1_features[0]), ("H2", h2_features[0]),
                                      ("H3", h3_features[0])]:
            exec_results = run_execution(dev_features, dev_labels,
                                          best_feat, 5)
            all_execution.extend(exec_results)
            if exec_results:
                for er in exec_results[:2]:  # Show first 2
                    mean = f"{er.mean_net_bps:.1f}" if er.mean_net_bps is not None else "N/A"
                    wr = f"{er.win_rate:.3f}" if er.win_rate is not None else "N/A"
                    print(f"    {hyp_name}/{best_feat} {er.latency_ms}ms/{er.fee_scenario}: "
                          f"trips={er.completed_round_trips}, net={er.net_pnl:,.0f}, "
                          f"mean_bps={mean}, win={wr}")

    # ── Summary ────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("MARKET BASELINE SUMMARY")
    print("=" * 70)
    for b in sorted(all_baselines, key=lambda x: x.orderbook_events, reverse=True):
        if b.orderbook_events > 0:
            print(f"  {b.market:12s}: OB={b.orderbook_events:>8,} "
                  f"trade={b.trade_events:>6,} "
                  f"spread={b.median_spread_bps:>5.1f}bps "
                  f"rate={b.events_per_sec:>6.1f}/s")

    print("\n" + "=" * 70)
    print("PREDICTIVE RESULTS RANKING (top 20 by |IC|)")
    print("=" * 70)
    ranked = sorted(all_predictive,
                    key=lambda x: abs(x.pearson_ic) if x.pearson_ic else 0,
                    reverse=True)
    for i, r in enumerate(ranked[:20]):
        ic = f"{r.pearson_ic:.4f}" if r.pearson_ic is not None else "N/A"
        hr = f"{r.hit_rate:.3f}" if r.hit_rate is not None else "N/A"
        print(f"  {i+1:2d}. [{r.market}] {r.feature} h={r.horizon}s: "
              f"IC={ic} HR={hr} n={r.n} [{r.classification}]")

    print("\n" + "=" * 70)
    print("EXECUTION SUMMARY")
    print("=" * 70)
    for er in all_execution:
        mean = f"{er.mean_net_bps:.1f}" if er.mean_net_bps is not None else "N/A"
        wr = f"{er.win_rate:.3f}" if er.win_rate is not None else "N/A"
        print(f"  {er.latency_ms:>5.0f}ms/{er.fee_scenario:<12}: "
              f"trips={er.completed_round_trips:>5}, "
              f"gross={er.gross_pnl:>10,.0f}, fees={er.total_fees:>8,.0f}, "
              f"net={er.net_pnl:>10,.0f} KRW, mean={mean:>7}, win={wr}")

    # ── Save results ───────────────────────────────────────────
    report = {
        "study": "AUTHORITATIVE V2 30H MICROSTRUCTURE PROFITABILITY RESEARCH",
        "dataset": DATASET_ID,
        "role": "DEVELOPMENT / EXPLORATORY ONLY",
        "alpha": "UNPROVEN",
        "v4_touched": False,
        "v4_data_used": False,
        "market_baselines": [asdict(b) for b in all_baselines],
        "predictive_results": [r.to_dict() for r in all_predictive],
        "execution_results": [r.to_dict() for r in all_execution],
        "trial_count": trial_id,
        "split": "DEV only (first 18h)",
        "latency_scenarios": LATENCY_SCENARIOS,
        "fee_scenarios": {k: v["fee_rate"] for k, v in FEE_SCENARIOS.items()},
    }
    out_path = REPORT_DIR / "reports" / "V2_DEV_RESULTS.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nResults saved to: {out_path}")

    # Classification
    pos_predictive = sum(1 for r in all_predictive if r.classification == "EXPLORATORY_POSITIVE")
    neg_predictive = sum(1 for r in all_predictive if r.classification in ("EXPLORATORY_NEGATIVE", "FRAGILE"))
    exec_positive = sum(1 for er in all_execution if er.net_pnl > 0)
    print(f"\nPredictive: {pos_predictive} EXPLORATORY_POSITIVE, {neg_predictive} other")
    print(f"Execution: {exec_positive}/{len(all_execution)} profitable scenarios")
    print("\nALPHA: UNPROVEN")
    print("V4: NOT TOUCHED")


if __name__ == "__main__":
    main()
