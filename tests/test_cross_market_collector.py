from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from bithumb_coin_trader.cross_market_collector import (
    MultiExchangeMicrostructureCollector,
    parse_binance_message,
)
from bithumb_coin_trader.microstructure_storage import RawMicrostructureStorage


class CrossMarketCollectorTests(unittest.TestCase):
    def test_storage_persists_monotonic_receive_clock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = RawMicrostructureStorage(Path(tmp) / "raw")
            now = datetime.now(timezone.utc)
            path = storage.append_raw_record(
                "bithumb",
                "trade",
                "KRW-BTC",
                {},
                now,
                now,
                local_receive_monotonic_ns=123456,
                collector_run_id="run-a",
            )
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["local_recv_monotonic_ns"], 123456)
            manifest = storage.generate_partition_manifest(path)
            self.assertEqual(manifest.schema_mismatch_count, 0)
            self.assertEqual(manifest.non_finite_numeric_count, 0)
            self.assertFalse((storage.manifest_dir / f"manifest_{path.stem}.json.tmp").exists())

    def test_manifest_counts_monotonic_reversal_and_offset_outlier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = RawMicrostructureStorage(Path(tmp) / "raw")
            exchange_ts = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc)
            local_ts = datetime(2026, 8, 26, 0, 1, 1, tzinfo=timezone.utc)
            path = storage.append_raw_record(
                "bithumb", "trade", "KRW-BTC", {}, local_ts, exchange_ts, 200, "run-a"
            )
            storage.append_raw_record(
                "bithumb", "trade", "KRW-BTC", {}, local_ts, exchange_ts, 100, "run-a"
            )
            storage.append_raw_record(
                "bithumb", "trade", "KRW-BTC", {}, local_ts, exchange_ts, None, None
            )
            manifest = storage.generate_partition_manifest(path)
            self.assertEqual(manifest.monotonic_reversal_count, 1)
            self.assertEqual(manifest.monotonic_missing_count, 1)
            self.assertEqual(manifest.latency_parseable_observation_count, 3)
            self.assertEqual(manifest.latency_out_of_range_count, 3)
            self.assertEqual(manifest.latency_observation_count, 0)

    def test_writer_failure_stops_collector_and_accounts_for_unpersisted_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            collector = MultiExchangeMicrostructureCollector(
                ["KRW-BTC"], storage_base_dir=Path(tmp) / "raw", enable_binance=False, enable_upbit=False
            )
            now = datetime.now(timezone.utc)

            async def exercise() -> None:
                collector.is_running = True
                await collector._enqueue("bithumb", "trade", "KRW-BTC", {}, now, now, 1)
                await collector._enqueue("bithumb", "trade", "KRW-BTC", {}, now, now, 2)
                with patch.object(collector.storage, "append_raw_record", side_effect=OSError("disk full")):
                    await collector._writer_worker()
                collector._discard_unpersisted_queue()

            with patch("bithumb_coin_trader.cross_market_collector.logger.critical"):
                asyncio.run(exercise())
            self.assertFalse(collector.is_running)
            self.assertEqual(collector.metrics["bithumb"].writer_errors, 1)
            self.assertEqual(collector._write_queue.qsize(), 0)
            self.assertIsInstance(collector._fatal_writer_error, OSError)
            self.assertEqual(collector._unpersisted_event_count, 2)

    def test_run_collector_propagates_fatal_writer_error_after_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            collector = MultiExchangeMicrostructureCollector(
                ["KRW-BTC"], storage_base_dir=Path(tmp) / "raw", enable_binance=False, enable_upbit=False
            )
            now = datetime.now(timezone.utc)

            async def failing_loop() -> None:
                await collector._enqueue("bithumb", "trade", "KRW-BTC", {}, now, now, 1)
                await collector._enqueue("bithumb", "trade", "KRW-BTC", {}, now, now, 2)
                while collector.is_running:
                    await asyncio.sleep(0)

            async def exercise() -> None:
                with (
                    patch.object(collector, "_bithumb_loop", side_effect=failing_loop),
                    patch.object(collector, "_binance_loop", side_effect=failing_loop),
                    patch.object(collector, "_upbit_loop", side_effect=failing_loop),
                    patch.object(collector.storage, "append_raw_record", side_effect=OSError("disk full")),
                    patch("bithumb_coin_trader.cross_market_collector.logger.critical"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "unpersisted_events=6"):
                        await collector.run_collector()

            asyncio.run(exercise())
            self.assertEqual(collector._write_queue.qsize(), 0)
            self.assertEqual(collector._unpersisted_event_count, 6)
            payload = json.loads(collector._metrics_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["writer_fail_closed"])
            self.assertEqual(payload["fatal_writer_error_type"], "OSError")
            self.assertEqual(payload["unpersisted_event_count"], 6)

    def test_writer_failure_cancels_producer_blocked_on_full_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            collector = MultiExchangeMicrostructureCollector(
                ["KRW-BTC"], storage_base_dir=Path(tmp) / "raw", enable_binance=False, enable_upbit=False
            )
            collector._write_queue = asyncio.Queue(maxsize=1)
            now = datetime.now(timezone.utc)
            collector._write_queue.put_nowait(
                ("bithumb", "trade", "KRW-BTC", {}, now, now, 1, collector._collector_run_id)
            )

            async def blocked_producer() -> None:
                await collector._enqueue("bithumb", "trade", "KRW-BTC", {}, now, now, 2)
                await collector._enqueue("bithumb", "trade", "KRW-BTC", {}, now, now, 3)

            async def idle_producer() -> None:
                while collector.is_running:
                    await asyncio.sleep(0)

            async def exercise() -> None:
                with (
                    patch.object(collector, "_bithumb_loop", side_effect=blocked_producer),
                    patch.object(collector, "_binance_loop", side_effect=idle_producer),
                    patch.object(collector, "_upbit_loop", side_effect=idle_producer),
                    patch.object(collector.storage, "append_raw_record", side_effect=OSError("disk full")),
                    patch("bithumb_coin_trader.cross_market_collector.logger.critical"),
                ):
                    with self.assertRaises(RuntimeError):
                        await asyncio.wait_for(collector.run_collector(), timeout=2.0)

            asyncio.run(exercise())
            self.assertEqual(collector._write_queue.qsize(), 0)
            self.assertGreaterEqual(collector._unpersisted_event_count, 1)

    def test_metrics_snapshot_persists_operational_counters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            collector = MultiExchangeMicrostructureCollector(
                ["KRW-BTC"],
                storage_base_dir=Path(tmp) / "raw",
                enable_binance=False,
                enable_upbit=False,
            )
            collector.metrics["bithumb"].writer_errors = 2
            active_partition = collector.storage.base_dir / "2026-09-02" / "bithumb" / "trade" / "active.jsonl"
            collector._active_partition_files.add(active_partition)
            collector._persist_metrics()
            payload = json.loads(collector._metrics_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["exchanges"]["bithumb"]["writer_errors"], 2)
            self.assertEqual(payload["queue_maxsize"], 50_000)
            self.assertEqual(payload["process_id"], os.getpid())
            self.assertEqual(payload["collector_run_id"], collector._collector_run_id)
            self.assertFalse(payload["writer_fail_closed"])
            self.assertEqual(payload["unpersisted_event_count"], 0)
            self.assertEqual(
                payload["active_partition_files"],
                ["2026-09-02/bithumb/trade/active.jsonl"],
            )
            self.assertFalse(collector._metrics_path.with_suffix(".json.tmp").exists())

    def test_explicit_short_smoke_provenance_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            commit = "a" * 40
            fingerprint = "b" * 64
            collector = MultiExchangeMicrostructureCollector(
                ["KRW-BTC"],
                storage_base_dir=Path(tmp) / "raw",
                enable_binance=False,
                enable_upbit=False,
                environment_id="aws-apne2-research",
                collector_epoch="aws-short-smoke-test",
                collector_run_id="aws-short-smoke-run-test",
                collector_config_fingerprint=fingerprint,
                collector_git_commit=commit,
            )
            collector._persist_metrics()
            payload = json.loads(collector._metrics_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["environment_id"], "aws-apne2-research")
            self.assertEqual(payload["collector_epoch"], "aws-short-smoke-test")
            self.assertEqual(payload["collector_run_id"], "aws-short-smoke-run-test")
            self.assertEqual(payload["collector_config_fingerprint"], fingerprint)
            self.assertEqual(payload["collector_git_commit"], commit)

            now = datetime.now(timezone.utc)
            raw = collector.storage.append_raw_record(
                "bithumb",
                "trade",
                "KRW-BTC",
                {},
                now,
                now,
                local_receive_monotonic_ns=1,
                collector_run_id=collector._collector_run_id,
            )
            manifest = collector.storage.generate_partition_manifest(raw)
            self.assertEqual(manifest.git_commit, commit)

    def test_throughput_rate_uses_collector_uptime_not_connection_uptime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            collector = MultiExchangeMicrostructureCollector(
                ["KRW-BTC"], storage_base_dir=Path(tmp) / "raw", enable_binance=False, enable_upbit=False
            )
            metric = collector.metrics["bithumb"]
            metric.collector_started_at = time.time() - 100
            metric.connected_at = time.time() - 1
            metric.total_messages_received = 100
            self.assertLess(metric.to_dict()["msg_per_sec"], 1.1)

    def test_combined_binance_depth_preserves_stream_symbol(self) -> None:
        stream, market, payload, exchange_ts = parse_binance_message(
            json.dumps(
                {
                    "stream": "xrpusdt@depth20@100ms",
                    "data": {"lastUpdateId": 123, "bids": [], "asks": []},
                }
            )
        )
        self.assertEqual(stream, "orderbook")
        self.assertEqual(market, "XRPUSDT")
        self.assertEqual(payload["lastUpdateId"], 123)
        self.assertIsNone(exchange_ts)

    def test_binance_trade_uses_payload_symbol_and_event_time(self) -> None:
        stream, market, _, exchange_ts = parse_binance_message(
            json.dumps(
                {
                    "stream": "btcusdt@trade",
                    "data": {"e": "trade", "s": "BTCUSDT", "E": 1_700_000_000_000},
                }
            )
        )
        self.assertEqual(stream, "trade")
        self.assertEqual(market, "BTCUSDT")
        self.assertEqual(exchange_ts, datetime.fromtimestamp(1_700_000_000, tz=timezone.utc))

    def test_full_queue_applies_backpressure_without_drop_counter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            collector = MultiExchangeMicrostructureCollector(
                ["KRW-BTC"],
                storage_base_dir=Path(tmp) / "raw",
                enable_binance=False,
                enable_upbit=False,
            )
            collector._write_queue = asyncio.Queue(maxsize=1)
            now = datetime.now(timezone.utc)

            async def exercise() -> None:
                collector._write_queue.put_nowait(
                    ("bithumb", "trade", "KRW-BTC", {}, now, now, 1, "test-run")
                )
                enqueue = asyncio.create_task(collector._enqueue("bithumb", "trade", "KRW-BTC", {}, now, now))
                await asyncio.sleep(0)
                self.assertFalse(enqueue.done())
                collector._write_queue.get_nowait()
                collector._write_queue.task_done()
                await enqueue

            with patch("bithumb_coin_trader.cross_market_collector.logger.warning"):
                asyncio.run(exercise())
            metric = collector.metrics["bithumb"]
            self.assertEqual(metric.queue_backpressure_events, 1)
            self.assertEqual(metric.queue_dropped_events, 0)


if __name__ == "__main__":
    unittest.main()
