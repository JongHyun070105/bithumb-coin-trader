"""Exploratory Research Runner.

Runs hypotheses H1-H5 against registered datasets with full
DQ awareness, feature computation, and evaluation.

ALL results from Old72H/V2/Fresh45 are DEVELOPMENT / EXPLORATORY ONLY.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Sequence

from .adapters import iter_raw_jsonl_streaming
from .canonical_events import CanonicalEvent, EventKind
from .dq import DQCatalog, CoverageState, FeedSlotCoverage
from .evaluation import (
    compute_information_coefficient,
    compute_hit_rate,
    compute_quantile_returns,
    create_chronological_folds,
)
from .features import FeatureEngine, FeatureVector
from .hypotheses import Hypothesis, HypothesisRegistry, HypothesisStatus
from .labels import LabelEngine, LabelVector
from .manifests import create_manifest, ResearchManifest
from .registry import DatasetRegistry, DatasetRole


def run_exploratory_single_market(
    raw_root: Path,
    dataset_id: str,
    dataset_role: str,
    exchange: str,
    market: str,
    hypothesis: Hypothesis,
    max_events: int = 500_000,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    """Run exploratory analysis for a single hypothesis on a single market.

    Streams events, computes features and labels, evaluates hypothesis.
    Returns result dict.
    """
    print(f"\n{'='*60}")
    print(f"Hypothesis {hypothesis.hypothesis_id}: {hypothesis.description}")
    print(f"Dataset: {dataset_id} ({dataset_role})")
    print(f"Exchange: {exchange}, Market: {market}")
    print(f"{'='*60}")

    # Initialize engines
    feature_engine = FeatureEngine(market=market, exchange=exchange)
    label_engine = LabelEngine(tolerance_s=5.0)

    # Stream events
    feature_vectors: list[FeatureVector] = []
    label_vectors: list[LabelVector] = []
    event_count = 0
    ob_count = 0
    trade_count = 0

    feeds = ["orderbook", "trade"]
    if any("ticker" in f for f in hypothesis.required_feeds):
        feeds.append("ticker")

    print(f"Streaming events from {raw_root}...")

    for event in iter_raw_jsonl_streaming(
        raw_root,
        dataset_id=dataset_id,
        exchanges=[exchange],
        feeds=feeds,
        markets=[market],
    ):
        event_count += 1
        if event_count > max_events:
            print(f"  Reached {max_events} event limit")
            break

        if event_count % 100_000 == 0:
            print(f"  Processed {event_count:,} events, "
                  f"{len(feature_vectors)} feature vectors")

        # Update label engine with mid prices from orderbook
        if event.event_kind == EventKind.ORDERBOOK:
            ob_count += 1
            bids = event.payload.get("bids", [])
            asks = event.payload.get("asks", [])
            if bids and asks:
                best_bid = float(bids[0][0]) if isinstance(bids[0], (list, tuple)) else float(bids[0])
                best_ask = float(asks[0][0]) if isinstance(asks[0], (list, tuple)) else float(asks[0])
                mid = (best_bid + best_ask) / 2.0
                label_engine.add_mid_observation(event.ordering_timestamp_ns, mid)

        # Compute features
        fv = feature_engine.process_event(event)
        if fv is not None and fv.mid_price is not None:
            feature_vectors.append(fv)

            # Compute label
            label = label_engine.compute_label(
                event.ordering_timestamp_ns, exchange, market
            )
            label_vectors.append(label)

        if event.event_kind == EventKind.TRADE:
            trade_count += 1

    print(f"\nTotal events: {event_count:,}")
    print(f"Orderbook events: {ob_count:,}")
    print(f"Trade events: {trade_count:,}")
    print(f"Feature vectors: {len(feature_vectors):,}")

    if len(feature_vectors) < 100:
        print("ERROR: Insufficient feature vectors for analysis")
        return {
            "hypothesis_id": hypothesis.hypothesis_id,
            "dataset_id": dataset_id,
            "status": "INSUFFICIENT_DATA",
            "feature_count": len(feature_vectors),
        }

    # Evaluate each relevant feature against target
    results: dict[str, Any] = {
        "hypothesis_id": hypothesis.hypothesis_id,
        "dataset_id": dataset_id,
        "dataset_role": dataset_role,
        "exchange": exchange,
        "market": market,
        "event_count": event_count,
        "feature_vector_count": len(feature_vectors),
        "feature_evaluations": {},
    }

    target_horizon = hypothesis.target_horizon_s
    target_key = f"mid_return_{target_horizon}s"

    for feat_name in hypothesis.feature_names:
        # Get feature values
        feat_values = []
        target_values = []

        for i, fv in enumerate(feature_vectors):
            val = getattr(fv, feat_name, None)
            if i < len(label_vectors):
                target = getattr(label_vectors[i], target_key, None)
            else:
                target = None

            if val is not None and target is not None and math.isfinite(val) and math.isfinite(target):
                feat_values.append(val)
                target_values.append(target)

        if len(feat_values) < 50:
            results["feature_evaluations"][feat_name] = {
                "status": "INSUFFICIENT_DATA",
                "sample_count": len(feat_values),
            }
            continue

        # Compute IC
        ic, n = compute_information_coefficient(feat_values, target_values)

        # Hit rate
        hr, hr_n = compute_hit_rate(feat_values, target_values, hypothesis.expected_sign)

        # Quantile returns
        qr = compute_quantile_returns(feat_values, target_values, n_quantiles=5)

        # Unconditional mean return
        mean_ret = sum(target_values) / len(target_values) if target_values else 0

        feat_eval = {
            "status": "COMPUTED",
            "sample_count": len(feat_values),
            "missing_labels": len(feature_vectors) - len(feat_values),
            "information_coefficient": ic,
            "hit_rate": hr,
            "unconditional_mean_return": mean_ret,
            "quantile_returns": qr,
            "feature_mean": sum(feat_values) / len(feat_values),
            "feature_std": math.sqrt(
                sum((f - sum(feat_values)/len(feat_values))**2 for f in feat_values) / len(feat_values)
            ),
        }

        # Classification
        if ic is not None and abs(ic) > 0.02 and hr is not None and hr > 0.52:
            feat_eval["classification"] = "EXPLORATORY_POSITIVE"
        elif ic is not None and abs(ic) < 0.005:
            feat_eval["classification"] = "EXPLORATORY_NEGATIVE"
        else:
            feat_eval["classification"] = "FRAGILE"

        results["feature_evaluations"][feat_name] = feat_eval

        print(f"\n  {feat_name}:")
        print(f"    Samples: {feat_eval['sample_count']:,}")
        print(f"    IC: {ic:.4f}" if ic is not None else "    IC: None")
        print(f"    Hit Rate: {hr:.3f}" if hr is not None else "    Hit Rate: None")
        print(f"    Classification: {feat_eval['classification']}")

        if qr:
            print(f"    Quantile Returns:")
            for q in qr:
                print(f"      Q{q['quantile']}: mean={q['target_mean']:.6f} "
                      f"(n={q['count']})")

    # Overall classification
    classifications = [
        v.get("classification") for v in results["feature_evaluations"].values()
        if v.get("status") == "COMPUTED"
    ]
    if any(c == "EXPLORATORY_POSITIVE" for c in classifications):
        results["overall_classification"] = "EXPLORATORY_POSITIVE"
    elif all(c == "EXPLORATORY_NEGATIVE" for c in classifications):
        results["overall_classification"] = "EXPLORATORY_NEGATIVE"
    else:
        results["overall_classification"] = "FRAGILE"

    print(f"\nOverall: {results['overall_classification']}")

    # Save report if directory provided
    if report_dir:
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"h{hypothesis.hypothesis_id}_{dataset_id}_{market}.json"
        report_path.write_text(json.dumps(results, indent=2, default=str))
        print(f"Report saved: {report_path}")

    return results


def run_exploratory_suite(
    raw_root: Path,
    dataset_id: str,
    dataset_role: str,
    registry: DatasetRegistry,
    report_dir: Path | None = None,
    markets: list[str] | None = None,
    max_events_per_market: int = 200_000,
) -> dict[str, Any]:
    """Run the full exploratory suite on a dataset.

    Tests H1-H5 on available markets.
    """
    if report_dir is None:
        report_dir = Path(f"research-artifacts/{dataset_id}")

    hyp_registry = HypothesisRegistry()
    from .hypotheses import register_default_hypotheses
    register_default_hypotheses(hyp_registry)

    if markets is None:
        markets = ["KRW-BTC"]

    suite_results: dict[str, Any] = {
        "dataset_id": dataset_id,
        "dataset_role": dataset_role,
        "markets": markets,
        "hypothesis_results": {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "classification": "UNTESTED",
    }

    for hyp in hyp_registry.list_hypotheses():
        for market in markets:
            key = f"{hyp.hypothesis_id}_{market}"
            try:
                result = run_exploratory_single_market(
                    raw_root=raw_root,
                    dataset_id=dataset_id,
                    dataset_role=dataset_role,
                    exchange="bithumb",
                    market=market,
                    hypothesis=hyp,
                    max_events=max_events_per_market,
                    report_dir=report_dir,
                )
                suite_results["hypothesis_results"][key] = result
            except Exception as e:
                print(f"ERROR running {hyp.hypothesis_id} on {market}: {e}")
                suite_results["hypothesis_results"][key] = {
                    "hypothesis_id": hyp.hypothesis_id,
                    "market": market,
                    "status": "ERROR",
                    "error": str(e),
                }

    # Overall classification
    classifications = [
        r.get("overall_classification")
        for r in suite_results["hypothesis_results"].values()
        if isinstance(r, dict) and "overall_classification" in r
    ]
    if any(c == "EXPLORATORY_POSITIVE" for c in classifications):
        suite_results["classification"] = "EXPLORATORY_POSITIVE"
    elif all(c == "EXPLORATORY_NEGATIVE" for c in classifications):
        suite_results["classification"] = "EXPLORATORY_NEGATIVE"
    else:
        suite_results["classification"] = "FRAGILE"

    # Save suite report
    suite_path = report_dir / "exploratory_suite.json"
    suite_path.parent.mkdir(parents=True, exist_ok=True)
    suite_path.write_text(json.dumps(suite_results, indent=2, default=str))
    print(f"\n{'='*60}")
    print(f"Suite complete. Overall: {suite_results['classification']}")
    print(f"Report: {suite_path}")

    return suite_results
