#!/usr/bin/env python3
"""V2 Microstructure Profitability Research — Full Pipeline.

Runs H1/H2/H3 predictive and cost-aware execution studies on the
local_microstructure_aug2026 dataset (6h, KRW-BTC, UNATTRIBUTED).

V2 30h S3 data was inaccessible (expired AWS credentials).  This study
uses the local development dataset as a proxy.  Results are labeled
DEVELOPMENT / EXPLORATORY ONLY.

Usage:
    .venv/bin/python scripts/run_v2_microstructure_study.py
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# Ensure src is on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bithumb_coin_trader.research_infra.adapters import iter_raw_jsonl_streaming, adapt_raw_record
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
DATASET_ID = "local_microstructure_aug2026"
MARKET = "KRW-BTC"
EXCHANGE = "bithumb"
RAW_ROOT = ROOT / "data" / "microstructure" / "raw"
REPORT_DIR = ROOT / "research-artifacts" / "v2_study"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# Latency scenarios (ms)
LATENCY_SCENARIOS = [0.0, 100.0, 250.0, 500.0]
# Fee scenarios
FEE_SCENARIOS = {
    "promotional": {"fee_rate": 0.0, "description": "Zero-fee promotional"},
    "normal": {"fee_rate": 0.0025, "description": "Normal 0.25% taker fee"},
}
# Horizons for predictive analysis
HORIZONS_S = [1, 5, 10, 30]
# Signal quantile threshold for execution (top 10% positive)
SIGNAL_QUANTILE = 0.90
# Max events to process (keep manageable for 74GB dataset)
# Need enough to cover both orderbook and trade events
MAX_EVENTS = 100_000
# Execution notional
POSITION_SIZE_KRW = 100_000


# ── Data structures ───────────────────────────────────────────
@dataclass
class PredictiveResult:
    feature: str
    horizon_s: int
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
    partial_entries: int
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


@dataclass
class HypothesisStudy:
    hypothesis_id: str
    description: str
    features: list[str]
    target_horizon_s: int
    predictive_results: list[PredictiveResult] = field(default_factory=list)
    execution_results: list[ExecutionResult] = field(default_factory=list)
    classification: str = "UNTESTED"


# ── Utility ────────────────────────────────────────────────────
def _percentile(values: list[float], p: float) -> float:
    """Compute p-th percentile (0-100) from sorted or unsorted values."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)


def _bps(entry_price: float, exit_price: float) -> float:
    """Return in basis points."""
    if entry_price <= 0:
        return 0.0
    return (exit_price - entry_price) / entry_price * 10_000.0


# ── Phase1: Stream features + labels ──────────────────────────
def stream_features_and_labels(
    max_events: int = MAX_EVENTS,
) -> tuple[list[FeatureVector], list[LabelVector]]:
    """Stream canonical events and build feature/label vectors.

    Uses direct file access to interleave orderbook and trade events
    chronologically for KRW-BTC only.
    """
    import bisect as _bisect

    print(f"Streaming events from {RAW_ROOT} (max={max_events:,})...")
    t0 = time.time()

    fe = FeatureEngine(market=MARKET, exchange=EXCHANGE)
    le = LabelEngine(tolerance_s=60.0)  # 60s tolerance to handle data gaps

    features: list[FeatureVector] = []
    labels: list[LabelVector] = []

    # Collect all KRW-BTC event files (orderbook + trade only)
    # Limit to first available date for manageable processing
    ob_files: list[Path] = []
    trade_files: list[Path] = []
    dates_processed = 0
    for date_dir in sorted(RAW_ROOT.iterdir()):
        if not date_dir.is_dir():
            continue
        if date_dir.name.startswith("."):
            continue
        dates_processed += 1
        if dates_processed > 1:  # Process 1 day only (~6h of data)
            break
        print(f"  Processing date: {date_dir.name}")
        exchange_dir = date_dir / EXCHANGE
        if not exchange_dir.exists():
            continue
        ob_dir = exchange_dir / "orderbook"
        if ob_dir.exists():
            for f in sorted(ob_dir.iterdir()):
                if f.name.endswith(".jsonl") and "krw-btc" in f.name:
                    ob_files.append(f)
        trade_dir = exchange_dir / "trade"
        if trade_dir.exists():
            for f in sorted(trade_dir.iterdir()):
                if f.name.endswith(".jsonl") and "krw-btc" in f.name:
                    trade_files.append(f)

    print(f"  Found {len(ob_files)} orderbook files, {len(trade_files)} trade files")

    # Load all events into memory (sorted by timestamp) for interleaving
    all_events: list[tuple[int, CanonicalEvent]] = []

    def _load_file(path: Path) -> list[tuple[int, CanonicalEvent]]:
        events = []
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event = adapt_raw_record(
                        record,
                        dataset_id=DATASET_ID,
                        source_file=str(path),
                    )
                    if event is not None:
                        events.append((event.ordering_timestamp_ns, event))
        except Exception:
            pass
        return events

    for f in ob_files:
        all_events.extend(_load_file(f))
    for f in trade_files:
        all_events.extend(_load_file(f))

    # Sort by timestamp for chronological interleaving
    all_events.sort(key=lambda x: x[0])
    print(f"  Loaded {len(all_events):,} total events ({time.time()-t0:.1f}s)")

    count = 0
    for _, event in all_events:
        count += 1
        if count > max_events:
            break
        if count % 200_000 == 0:
            elapsed = time.time() - t0
            print(f"  {count:,} events ({elapsed:.1f}s)")

        # Feed mid to label engine
        if event.event_kind == EventKind.ORDERBOOK:
            bids = event.payload.get("bids", [])
            asks = event.payload.get("asks", [])
            if bids and asks:
                mid = (float(bids[0][0]) + float(asks[0][0])) / 2.0
                le.add_mid_observation(event.ordering_timestamp_ns, mid)

        fv = fe.process_event(event)
        if fv is not None and fv.mid_price is not None:
            features.append(fv)

    # Backfill labels: now that all mids are loaded, compute labels for
    # every feature vector EXCEPT the last horizon_s seconds (no future data).
    print(f"  Backfilling labels for {len(features):,} features...")
    for fv in features:
        lbl = le.compute_label(fv.timestamp_ns, EXCHANGE, MARKET)
        labels.append(lbl)

    elapsed = time.time() - t0
    print(f"  Done: {len(features):,} feature vectors in {elapsed:.1f}s")
    return features, labels


# ── Phase2: Predictive analysis ───────────────────────────────
def run_predictive_analysis(
    features: list[FeatureVector],
    labels: list[LabelVector],
    feature_names: list[str],
    horizon_s: int,
    expected_sign: str = "positive",
) -> list[PredictiveResult]:
    """Run IC, hit rate, quantile returns for each feature."""
    target_attr = f"mid_return_{horizon_s}s"
    results = []

    for fname in feature_names:
        feat_vals = [getattr(fv, fname, None) for fv in features]
        target_vals = [getattr(lv, target_attr, None) for lv in labels]

        # Filter to paired non-None
        paired_f, paired_t = [], []
        for f, t in zip(feat_vals, target_vals):
            if f is not None and t is not None and math.isfinite(f) and math.isfinite(t):
                paired_f.append(f)
                paired_t.append(t)

        n = len(paired_f)
        if n < 50:
            results.append(PredictiveResult(
                feature=fname, horizon_s=horizon_s, n=n,
                pearson_ic=None, spearman_ic=None, hit_rate=None,
                quantile_returns=[], classification="INSUFFICIENT_DATA",
            ))
            continue

        pearson_ic, _ = compute_information_coefficient(feat_vals, target_vals)
        spearman_ic, _ = compute_spearman_rank_ic(feat_vals, target_vals)
        hr, _ = compute_hit_rate(feat_vals, target_vals, expected_sign=expected_sign)
        qr = compute_quantile_returns(feat_vals, target_vals, n_quantiles=5)

        # Classification
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
            feature=fname, horizon_s=horizon_s, n=n,
            pearson_ic=pearson_ic, spearman_ic=spearman_ic, hit_rate=hr,
            quantile_returns=qr, classification=cls,
        ))

    return results


# ── Phase3: Execution simulation ──────────────────────────────
def _make_ob_event(fv: FeatureVector, ts_ns: int | None = None) -> CanonicalEvent | None:
    """Create an orderbook event from a FeatureVector. Returns None if prices invalid."""
    if fv.best_bid is None or fv.best_ask is None:
        return None
    if fv.best_bid <= 0 or fv.best_ask <= 0 or fv.best_bid >= fv.best_ask:
        return None
    ts = ts_ns or fv.timestamp_ns
    return CanonicalEvent(
        dataset_id=DATASET_ID, source_run_id=None, collector_epoch=None,
        source_file=None, source_file_offset=None,
        exchange=EXCHANGE, market=MARKET,
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


def run_execution_study(
    features: list[FeatureVector],
    labels: list[LabelVector],
    signal_feature: str,
    horizon_s: int,
) -> list[ExecutionResult]:
    """Run long-only execution with top-10% positive signal.

    Uses the future_mid from labels for exit pricing (authoritative).
    Constructs synthetic orderbook events from FeatureVector bid/ask.
    """
    target_attr = f"mid_return_{horizon_s}s"
    future_mid_attr = f"future_mid_{horizon_s}s"
    results = []

    # Build signal: top 10% positive feature values WITH valid prices + labels
    paired: list[tuple[int, float, float]] = []
    for i, (fv, lv) in enumerate(zip(features, labels)):
        feat_val = getattr(fv, signal_feature, None)
        target_val = getattr(lv, target_attr, None)
        future_mid = getattr(lv, future_mid_attr, None)
        if (feat_val is not None and target_val is not None
                and future_mid is not None and fv.best_bid is not None
                and fv.best_ask is not None and fv.best_bid > 0
                and fv.best_ask > 0 and fv.best_bid < fv.best_ask):
            if math.isfinite(feat_val) and math.isfinite(target_val):
                paired.append((i, feat_val, target_val))

    if len(paired) < 100:
        return results

    # Determine threshold for top 10%
    feat_sorted = sorted(p[1] for p in paired)
    threshold_idx = int(len(feat_sorted) * SIGNAL_QUANTILE)
    threshold = feat_sorted[min(threshold_idx, len(feat_sorted) - 1)]

    # Signal indices (positive signal → BUY)
    signal_indices = [p[0] for p in paired if p[1] >= threshold]
    n_signals = len(signal_indices)
    if n_signals < 10:
        return results

    for latency_ms in LATENCY_SCENARIOS:
        for fee_name, fee_cfg in FEE_SCENARIOS.items():
            assumptions = ExecutionAssumptions(
                fee_regime=fee_name,
                fee_rate=fee_cfg["fee_rate"],
                additional_impact_bps=0.0,
                latency_ms=latency_ms,
                position_size_krw=POSITION_SIZE_KRW,
                max_depth_levels=5,
                partial_fills_enabled=True,
                description=f"{fee_cfg['description']}, {latency_ms}ms latency",
            )
            sim = ResearchExecutionSimulator(assumptions=assumptions)

            entry_attempts = 0
            completed = 0
            no_book = 0
            round_trip_bps: list[float] = []

            for sig_idx in signal_indices:
                fv = features[sig_idx]
                lv = labels[sig_idx]
                future_mid = getattr(lv, future_mid_attr, None)
                if future_mid is None or future_mid <= 0:
                    continue

                # Build entry event
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

                # Exit using future_mid from label (authoritative future price)
                # Construct exit event around the future mid with same spread
                spread = (fv.best_ask or 0) - (fv.best_bid or 0)
                half_spread = spread / 2.0
                exit_bid = future_mid - half_spread
                exit_ask = future_mid + half_spread
                exit_ts = fv.timestamp_ns + horizon_s * 1_000_000_000
                exit_event = CanonicalEvent(
                    dataset_id=DATASET_ID, source_run_id=None, collector_epoch=None,
                    source_file=None, source_file_offset=None,
                    exchange=EXCHANGE, market=MARKET,
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
                    rt_bps = _bps(buy_trade.fill_price, sell_trade.fill_price)
                    round_trip_bps.append(rt_bps)

            pnl = sim.get_pnl_summary()
            mean_bps = (sum(round_trip_bps) / len(round_trip_bps)
                        if round_trip_bps else None)
            median_bps = (_percentile(round_trip_bps, 50)
                          if round_trip_bps else None)
            wins = sum(1 for r in round_trip_bps if r > 0)
            win_rate = wins / len(round_trip_bps) if round_trip_bps else None

            results.append(ExecutionResult(
                latency_ms=latency_ms,
                fee_scenario=fee_name,
                fee_rate=fee_cfg["fee_rate"],
                signals=n_signals,
                entry_attempts=entry_attempts,
                completed_round_trips=completed,
                partial_entries=0,
                no_book_rejects=no_book,
                fill_rate=completed / entry_attempts if entry_attempts > 0 else 0.0,
                gross_pnl=pnl["gross_pnl"],
                total_fees=pnl["total_fees"],
                net_pnl=pnl["net_pnl"],
                mean_net_bps=mean_bps,
                median_net_bps=median_bps,
                win_rate=win_rate,
            ))

    return results


# ── Main ───────────────────────────────────────────────────────
def main() -> None:
    print("=" * 70)
    print("V2 MICROSTRUCTURE PROFITABILITY RESEARCH")
    print("Dataset: local_microstructure_aug2026 (6h, KRW-BTC)")
    print("Role: DEVELOPMENT / EXPLORATORY ONLY")
    print("V2 30h S3 data: INACCESSIBLE (expired AWS credentials)")
    print("V4 touched: NO")
    print("=" * 70)

    # Phase1: Stream
    features, labels = stream_features_and_labels(max_events=MAX_EVENTS)

    if not features:
        print("ERROR: No features generated. Check data path.")
        sys.exit(1)

    # Summary stats
    print(f"\nDataset summary:")
    print(f"  Feature vectors: {len(features):,}")
    t_start = features[0].timestamp_ns / 1e9
    t_end = features[-1].timestamp_ns / 1e9
    duration_h = (t_end - t_start) / 3600
    print(f"  Duration: {duration_h:.1f} hours")
    print(f"  Mid price range: {min(f.mid_price for f in features if f.mid_price):,.0f}"
          f" - {max(f.mid_price for f in features if f.mid_price):,.0f}")

    # Spread stats
    spreads = [f.spread_bps for f in features if f.spread_bps is not None and f.spread_bps > 0]
    if spreads:
        print(f"  Median spread: {sorted(spreads)[len(spreads)//2]:.1f} bps")

    # ── H1: Orderbook imbalance ────────────────────────────────
    print("\n" + "=" * 70)
    print("H1: ORDERBOOK IMBALANCE → DIRECTION PREDICTION")
    print("=" * 70)
    h1 = HypothesisStudy(
        hypothesis_id="H1",
        description="Orderbook imbalance predicts short-horizon direction",
        features=["depth_imbalance_l1", "depth_imbalance_l5", "qi_l1", "qi_l5"],
        target_horizon_s=5,
    )

    for horizon in HORIZONS_S:
        print(f"\n--- H1: horizon={horizon}s ---")
        results = run_predictive_analysis(
            features, labels, h1.features, horizon, expected_sign="positive",
        )
        h1.predictive_results.extend(results)
        for r in results:
            ic_str = f"{r.pearson_ic:.4f}" if r.pearson_ic is not None else "N/A"
            hr_str = f"{r.hit_rate:.3f}" if r.hit_rate is not None else "N/A"
            print(f"  {r.feature}: IC={ic_str}, HR={hr_str}, n={r.n}, cls={r.classification}")

    # Execution for best H1 feature
    print("\n--- H1 Execution (depth_imbalance_l1, top-10% positive → BUY) ---")
    h1.execution_results = run_execution_study(
        features, labels, "depth_imbalance_l1", 5,
    )
    for er in h1.execution_results:
        print(f"  {er.latency_ms}ms/{er.fee_scenario}: "
              f"signals={er.signals}, completed={er.completed_round_trips}, "
              f"net_pnl={er.net_pnl:,.0f} KRW, "
              f"mean_bps={er.mean_net_bps:.1f}" if er.mean_net_bps is not None
              else f"  {er.latency_ms}ms/{er.fee_scenario}: "
                   f"signals={er.signals}, completed={er.completed_round_trips}, "
                   f"net_pnl={er.net_pnl:,.0f} KRW, mean_bps=N/A")

    # ── H2: Trade imbalance (ATI) ──────────────────────────────
    print("\n" + "=" * 70)
    print("H2: AGGRESSIVE TRADE IMBALANCE → CONTINUATION")
    print("=" * 70)
    h2 = HypothesisStudy(
        hypothesis_id="H2",
        description="Signed trade flow predicts short-horizon continuation",
        features=["ati_5s", "ati_30s", "ati_60s", "signed_volume_30s"],
        target_horizon_s=5,
    )

    for horizon in HORIZONS_S:
        print(f"\n--- H2: horizon={horizon}s ---")
        results = run_predictive_analysis(
            features, labels, h2.features, horizon, expected_sign="positive",
        )
        h2.predictive_results.extend(results)
        for r in results:
            ic_str = f"{r.pearson_ic:.4f}" if r.pearson_ic is not None else "N/A"
            hr_str = f"{r.hit_rate:.3f}" if r.hit_rate is not None else "N/A"
            print(f"  {r.feature}: IC={ic_str}, HR={hr_str}, n={r.n}, cls={r.classification}")

    # Execution for best H2 feature
    print("\n--- H2 Execution (ati_5s, top-10% positive → BUY) ---")
    h2.execution_results = run_execution_study(
        features, labels, "ati_5s", 5,
    )
    for er in h2.execution_results:
        mean_str = f"{er.mean_net_bps:.1f}" if er.mean_net_bps is not None else "N/A"
        wr_str = f"{er.win_rate:.3f}" if er.win_rate is not None else "N/A"
        print(f"  {er.latency_ms}ms/{er.fee_scenario}: "
              f"signals={er.signals}, completed={er.completed_round_trips}, "
              f"net_pnl={er.net_pnl:,.0f} KRW, mean_bps={mean_str}, win_rate={wr_str}")

    # ── H3: Microprice displacement ────────────────────────────
    print("\n" + "=" * 70)
    print("H3: MICROPRICE DISPLACEMENT → MOVEMENT")
    print("=" * 70)
    h3 = HypothesisStudy(
        hypothesis_id="H3",
        description="Microprice displacement predicts short-horizon movement",
        features=["microprice_bias_bps", "microprice_displacement"],
        target_horizon_s=5,
    )

    for horizon in HORIZONS_S:
        print(f"\n--- H3: horizon={horizon}s ---")
        results = run_predictive_analysis(
            features, labels, h3.features, horizon, expected_sign="positive",
        )
        h3.predictive_results.extend(results)
        for r in results:
            ic_str = f"{r.pearson_ic:.4f}" if r.pearson_ic is not None else "N/A"
            hr_str = f"{r.hit_rate:.3f}" if r.hit_rate is not None else "N/A"
            print(f"  {r.feature}: IC={ic_str}, HR={hr_str}, n={r.n}, cls={r.classification}")

    # Execution for H3
    print("\n--- H3 Execution (microprice_bias_bps, top-10% positive → BUY) ---")
    h3.execution_results = run_execution_study(
        features, labels, "microprice_bias_bps", 5,
    )
    for er in h3.execution_results:
        mean_str = f"{er.mean_net_bps:.1f}" if er.mean_net_bps is not None else "N/A"
        wr_str = f"{er.win_rate:.3f}" if er.win_rate is not None else "N/A"
        print(f"  {er.latency_ms}ms/{er.fee_scenario}: "
              f"signals={er.signals}, completed={er.completed_round_trips}, "
              f"net_pnl={er.net_pnl:,.0f} KRW, mean_bps={mean_str}, win_rate={wr_str}")

    # ── Trial Ledger ───────────────────────────────────────────
    print("\n" + "=" * 70)
    print("TRIAL LEDGER")
    print("=" * 70)
    total_variants = sum(
        len(h.predictive_results) + len(h.execution_results)
        for h in [h1, h2, h3]
    )
    print(f"  Total variants tested: {total_variants}")
    print(f"  Predictive tests: {sum(len(h.predictive_results) for h in [h1, h2, h3])}")
    print(f"  Execution tests: {sum(len(h.execution_results) for h in [h1, h2, h3])}")

    # ── Candidate ranking ──────────────────────────────────────
    print("\n" + "=" * 70)
    print("CANDIDATE RANKING")
    print("=" * 70)
    all_results: list[tuple[str, PredictiveResult]] = []
    for h in [h1, h2, h3]:
        for r in h.predictive_results:
            all_results.append((h.hypothesis_id, r))

    # Rank by |IC|
    ranked = sorted(
        all_results,
        key=lambda x: abs(x[1].pearson_ic) if x[1].pearson_ic is not None else 0,
        reverse=True,
    )
    for i, (hyp_id, r) in enumerate(ranked[:10]):
        ic_str = f"{r.pearson_ic:.4f}" if r.pearson_ic is not None else "N/A"
        print(f"  {i+1}. [{hyp_id}] {r.feature} h={r.horizon_s}s: "
              f"IC={ic_str}, cls={r.classification}")

    # ── Classification ─────────────────────────────────────────
    for h in [h1, h2, h3]:
        classifications = [r.classification for r in h.predictive_results]
        if any(c == "EXPLORATORY_POSITIVE" for c in classifications):
            h.classification = "EXPLORATORY_POSITIVE"
        elif all(c in ("EXPLORATORY_NEGATIVE", "INSUFFICIENT_DATA") for c in classifications):
            h.classification = "EXPLORATORY_NEGATIVE"
        else:
            h.classification = "FRAGILE"

    # ── Execution summary ──────────────────────────────────────
    print("\n" + "=" * 70)
    print("EXECUTION COST-AWARE SUMMARY")
    print("=" * 70)
    for h in [h1, h2, h3]:
        print(f"\n  {h.hypothesis_id} ({h.description}):")
        if not h.execution_results:
            print("    No execution results")
            continue
        for er in h.execution_results:
            mean_str = f"{er.mean_net_bps:.1f}" if er.mean_net_bps is not None else "N/A"
            wr_str = f"{er.win_rate:.3f}" if er.win_rate is not None else "N/A"
            print(f"    {er.latency_ms:>5.0f}ms / {er.fee_scenario:<12}: "
                  f"trips={er.completed_round_trips:>4}, "
                  f"gross={er.gross_pnl:>10,.0f}, fees={er.total_fees:>8,.0f}, "
                  f"net={er.net_pnl:>10,.0f} KRW, "
                  f"mean_bps={mean_str:>7}, win={wr_str}")

    # ── Save JSON report ───────────────────────────────────────
    report = {
        "study": "V2 Microstructure Profitability Research",
        "dataset": DATASET_ID,
        "market": MARKET,
        "duration_hours": duration_h,
        "feature_vectors": len(features),
        "v2_30h_s3_access": "INACCESSIBLE (expired AWS credentials)",
        "v4_touched": False,
        "scientific_state": "DEVELOPMENT / EXPLORATORY ONLY",
        "alpha": "UNPROVEN",
        "hypotheses": {
            h.hypothesis_id: {
                "description": h.description,
                "classification": h.classification,
                "predictive_results": [r.to_dict() for r in h.predictive_results],
                "execution_results": [r.to_dict() for r in h.execution_results],
            }
            for h in [h1, h2, h3]
        },
        "trial_ledger": {
            "total_variants": total_variants,
            "predictive_tests": sum(len(h.predictive_results) for h in [h1, h2, h3]),
            "execution_tests": sum(len(h.execution_results) for h in [h1, h2, h3]),
        },
        "latency_scenarios": LATENCY_SCENARIOS,
        "fee_scenarios": {k: v["fee_rate"] for k, v in FEE_SCENARIOS.items()},
    }

    json_path = REPORT_DIR / "v2_microstructure_study_results.json"
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nJSON report saved to: {json_path}")

    # ── Final summary ──────────────────────────────────────────
    print("\n" + "=" * 70)
    print("FINAL CLASSIFICATION")
    print("=" * 70)
    for h in [h1, h2, h3]:
        print(f"  {h.hypothesis_id}: {h.classification}")

    any_surviving = any(h.classification == "EXPLORATORY_POSITIVE" for h in [h1, h2, h3])
    if any_surviving:
        print("\n  RESULT: EXPLORATORY_POSITIVE candidate(s) found.")
        print("  Classification: CANDIDATE_FOR_PROSPECTIVE_RESEARCH (development only)")
    else:
        print("\n  RESULT: NO NET-PROFITABLE CANDIDATE in development data.")
        print("  Recommend: larger dataset, longer study, or different signal families.")

    print("\n  ALPHA: UNPROVEN")
    print("  V4: QUARANTINED, NOT TOUCHED")
    print("=" * 70)


if __name__ == "__main__":
    main()
