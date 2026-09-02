"""Publish one fail-closed batch of durable collector metrics to CloudWatch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from bithumb_coin_trader.collector_metrics_publisher import CollectorMetricPublisher


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data" / "microstructure"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment-id", required=True)
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--metrics-path", type=Path, default=DATA_ROOT / "collector_metrics.json")
    parser.add_argument("--state-path", type=Path, default=DATA_ROOT / "metric-publisher-state.json")
    parser.add_argument("--storage-path", type=Path, default=DATA_ROOT)
    parser.add_argument("--ops-log", type=Path, default=DATA_ROOT / "metric-publisher-ops.jsonl")
    args = parser.parse_args(argv)

    import boto3

    publisher = CollectorMetricPublisher(
        client=boto3.client("cloudwatch", region_name=args.region),
        environment_id=args.environment_id,
        metrics_path=args.metrics_path,
        state_path=args.state_path,
        storage_path=args.storage_path,
        ops_log_path=args.ops_log,
    )
    result = publisher.publish_once()
    print(json.dumps({
        "published": result.published,
        "metric_names": result.metric_names,
        "reason": result.reason,
        "attempts": result.attempts,
    }, indent=2))
    return 0 if result.published else 2


if __name__ == "__main__":
    raise SystemExit(main())
