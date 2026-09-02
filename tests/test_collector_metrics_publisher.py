from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from bithumb_coin_trader.collector_metrics_publisher import (
    CollectorMetricPublisher,
    METRIC_DIMENSION,
    METRIC_NAMESPACE,
)


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


class FakeCloudWatch:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls = []

    def put_metric_data(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures:
            raise TimeoutError("injected CloudWatch failure")
        return {}


class ThrottlingCloudWatch:
    def __init__(self) -> None:
        self.calls = []

    def put_metric_data(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise RuntimeError("ThrottlingException")
        return {}


class CollectorMetricPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.metrics = self.root / "collector_metrics.json"
        self.state = self.root / "publisher-state.json"
        self.ops = self.root / "ops.jsonl"
        self.client = FakeCloudWatch()
        self.publisher = self._publisher(self.client)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _publisher(self, client, process_checker=lambda _: True, sleep=lambda _: None):
        return CollectorMetricPublisher(
            client=client,
            environment_id="aws-apne2-research",
            metrics_path=self.metrics,
            state_path=self.state,
            storage_path=self.root,
            ops_log_path=self.ops,
            process_checker=process_checker,
            sleep=sleep,
        )

    def _snapshot(
        self,
        writer_errors: int = 0,
        queue_drops: int = 0,
        unpersisted: int = 0,
        written_at: datetime = NOW,
        run_id: str = "run-a",
    ) -> None:
        payload = {
            "schema_version": 1,
            "collector_run_id": run_id,
            "collector_started_at": (NOW - timedelta(minutes=5)).isoformat(),
            "process_id": 123,
            "written_at": written_at.isoformat(),
            "unpersisted_event_count": unpersisted,
            "exchanges": {
                "bithumb": {"writer_errors": writer_errors, "queue_dropped_events": queue_drops},
                "binance": {"writer_errors": 0, "queue_dropped_events": 0},
                "upbit": {"writer_errors": 0, "queue_dropped_events": 0},
            },
        }
        self.metrics.write_text(json.dumps(payload), encoding="utf-8")

    def test_first_snapshot_omits_unknown_interval_counters(self) -> None:
        self._snapshot()
        result = self.publisher.publish_once(NOW)
        self.assertTrue(result.published)
        self.assertEqual(result.metric_names, ["DiskUsedPercent"])
        self.assertEqual(len(self.client.calls), 1)

    def test_second_snapshot_publishes_exact_batch_and_zero_values(self) -> None:
        self._snapshot()
        self.publisher.publish_once(NOW)
        self._snapshot(written_at=NOW + timedelta(seconds=5))
        result = self.publisher.publish_once(NOW + timedelta(seconds=5))
        self.assertEqual(set(result.metric_names), {"WriterErrors", "QueueDrops", "DiskUsedPercent"})
        request = self.client.calls[-1]
        self.assertEqual(request["Namespace"], METRIC_NAMESPACE)
        by_name = {item["MetricName"]: item for item in request["MetricData"]}
        self.assertEqual(by_name["WriterErrors"]["Value"], 0)
        self.assertEqual(by_name["QueueDrops"]["Value"], 0)
        for item in by_name.values():
            self.assertEqual(item["Dimensions"], [{"Name": METRIC_DIMENSION, "Value": "aws-apne2-research"}])

    def test_interval_deltas_include_writer_unpersisted_and_actual_drops(self) -> None:
        self._snapshot(writer_errors=1, queue_drops=2, unpersisted=3)
        self.publisher.publish_once(NOW)
        self._snapshot(writer_errors=2, queue_drops=5, unpersisted=7, written_at=NOW + timedelta(seconds=5))
        self.publisher.publish_once(NOW + timedelta(seconds=5))
        by_name = {item["MetricName"]: item for item in self.client.calls[-1]["MetricData"]}
        self.assertEqual(by_name["WriterErrors"]["Value"], 5)
        self.assertEqual(by_name["QueueDrops"]["Value"], 3)

    def test_missing_stale_or_dead_source_never_publishes_false_green(self) -> None:
        result = self.publisher.publish_once(NOW)
        self.assertFalse(result.published)
        self._snapshot(written_at=NOW - timedelta(minutes=1))
        result = self.publisher.publish_once(NOW)
        self.assertFalse(result.published)
        self._snapshot()
        dead = self._publisher(self.client, process_checker=lambda _: False)
        result = dead.publish_once(NOW)
        self.assertFalse(result.published)
        self.assertEqual(self.client.calls, [])

    def test_run_change_resets_baseline_without_zero_counter_claim(self) -> None:
        self._snapshot(run_id="run-a")
        self.publisher.publish_once(NOW)
        self._snapshot(run_id="run-b", written_at=NOW + timedelta(seconds=5))
        result = self.publisher.publish_once(NOW + timedelta(seconds=5))
        self.assertEqual(result.metric_names, ["DiskUsedPercent"])

    def test_counter_decrease_omits_counters(self) -> None:
        self._snapshot(writer_errors=5, queue_drops=5)
        self.publisher.publish_once(NOW)
        self._snapshot(writer_errors=1, queue_drops=1, written_at=NOW + timedelta(seconds=5))
        result = self.publisher.publish_once(NOW + timedelta(seconds=5))
        self.assertEqual(result.metric_names, ["DiskUsedPercent"])
        self.assertIn("metric_counter_reset", self.ops.read_text(encoding="utf-8"))

    def test_cloudwatch_exception_has_bounded_retry_and_durable_log(self) -> None:
        client = FakeCloudWatch(failures=10)
        publisher = self._publisher(client)
        self._snapshot()
        result = publisher.publish_once(NOW)
        self.assertFalse(result.published)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(self.ops.read_text(encoding="utf-8").count("cloudwatch_publish_failed"), 3)

    def test_throttling_then_success_is_bounded(self) -> None:
        client = ThrottlingCloudWatch()
        publisher = self._publisher(client)
        self._snapshot()
        result = publisher.publish_once(NOW)
        self.assertTrue(result.published)
        self.assertEqual(result.attempts, 2)

    def test_malformed_or_negative_counters_do_not_publish(self) -> None:
        self._snapshot(writer_errors=-1)
        result = self.publisher.publish_once(NOW)
        self.assertFalse(result.published)
        self.assertEqual(self.client.calls, [])

    def test_fractional_or_boolean_counters_do_not_publish(self) -> None:
        self._snapshot()
        payload = json.loads(self.metrics.read_text(encoding="utf-8"))
        for invalid in (1.5, True, "1"):
            payload["unpersisted_event_count"] = invalid
            self.metrics.write_text(json.dumps(payload), encoding="utf-8")
            self.assertFalse(self.publisher.publish_once(NOW).published)
        self.assertEqual(self.client.calls, [])

    def test_ops_log_failure_does_not_mask_invalid_source(self) -> None:
        self.ops.mkdir()
        result = self.publisher.publish_once(NOW)
        self.assertFalse(result.published)
        self.assertEqual(result.reason, "durable metrics snapshot missing")


if __name__ == "__main__":
    unittest.main()
