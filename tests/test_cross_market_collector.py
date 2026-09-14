from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, cast
import unittest
from unittest.mock import patch

import websockets

from bithumb_coin_trader.cross_market_collector import (
    MultiExchangeMicrostructureCollector,
    binance_list_subscriptions_request,
    build_binance_combined_url,
    parse_binance_message,
    upbit_list_subscriptions_request,
)
from bithumb_coin_trader.feed_hour_coverage import load_frozen_journal
from bithumb_coin_trader.microstructure_storage import RawMicrostructureStorage
from bithumb_coin_trader.session_evidence import FeedIdentity, HeartbeatPolicy


class CrossMarketCollectorTests(unittest.TestCase):
    def test_binance_combined_stream_uses_official_443_endpoint(self) -> None:
        self.assertEqual(
            build_binance_combined_url(["btcusdt", "ethusdt"]),
            "wss://stream.binance.com:443/stream?streams="
            "btcusdt@trade/ethusdt@trade/btcusdt@depth20@100ms/ethusdt@depth20@100ms",
        )

    @staticmethod
    def _write_one(
        collector: MultiExchangeMicrostructureCollector,
        exchange: str,
        stream: str,
        market: str,
        timestamp: datetime,
    ) -> None:
        async def exercise() -> None:
            collector.is_running = True
            collector._accepting_partition_writes = True
            await collector._enqueue(exchange, stream, market, {}, timestamp, timestamp, 1)
            collector.is_running = False
            await collector._writer_worker()

        asyncio.run(exercise())

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
            now = datetime(2026, 9, 2, 9, 15, tzinfo=timezone.utc)
            collector = MultiExchangeMicrostructureCollector(
                ["KRW-BTC"],
                storage_base_dir=Path(tmp) / "raw",
                enable_binance=False,
                enable_upbit=False,
                utc_now=lambda: now,
            )
            collector.metrics["bithumb"].writer_errors = 2
            self._write_one(collector, "bithumb", "trade", "KRW-BTC", now)
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
                ["2026-09-02/bithumb/trade/bithumb_trade_krw-btc_2026-09-02_09.jsonl"],
            )
            self.assertFalse(collector._metrics_path.with_suffix(".json.tmp").exists())

    def test_active_partitions_rotate_for_multiple_feeds_and_idle_feeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current = [datetime(2026, 9, 2, 9, 59, tzinfo=timezone.utc)]
            collector = MultiExchangeMicrostructureCollector(
                ["KRW-BTC"],
                binance_symbols=["btcusdt"],
                storage_base_dir=Path(tmp) / "raw",
                utc_now=lambda: current[0],
            )
            self._write_one(collector, "bithumb", "trade", "KRW-BTC", current[0])
            self._write_one(collector, "binance", "trade", "BTCUSDT", current[0])
            hour_nine = collector._current_active_partition_files()
            self.assertEqual(len(hour_nine), 2)
            self.assertTrue(all(path.name.endswith("_09.jsonl") for path in hour_nine))

            current[0] += timedelta(minutes=2)
            self.assertEqual(collector._current_active_partition_files(), set())
            self._write_one(collector, "bithumb", "trade", "KRW-BTC", current[0])
            hour_ten = collector._current_active_partition_files()
            self.assertEqual(len(hour_ten), 1)
            self.assertTrue(next(iter(hour_ten)).name.endswith("_10.jsonl"))
            self.assertNotIn("binance", next(iter(hour_ten)).as_posix())
            self.assertEqual(len(collector._all_touched_partition_files), 3)

    def test_shutdown_drain_persists_empty_active_set_and_keeps_all_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime(2026, 9, 2, 9, 20, tzinfo=timezone.utc)
            collector = MultiExchangeMicrostructureCollector(
                ["KRW-BTC"],
                storage_base_dir=Path(tmp) / "raw",
                enable_binance=False,
                enable_upbit=False,
                utc_now=lambda: now,
            )

            async def one_event_then_wait() -> None:
                await collector._enqueue("bithumb", "trade", "KRW-BTC", {}, now, now, 1)
                while collector.is_running:
                    await asyncio.sleep(0)

            async def idle() -> None:
                while collector.is_running:
                    await asyncio.sleep(0)

            async def exercise() -> None:
                with (
                    patch.object(collector, "_bithumb_loop", side_effect=one_event_then_wait),
                    patch.object(collector, "_binance_loop", side_effect=idle),
                    patch.object(collector, "_upbit_loop", side_effect=idle),
                ):
                    await collector.run_collector(max_duration_seconds=0.01)

            asyncio.run(exercise())
            payload = json.loads(collector._metrics_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["active_partition_files"], [])
            self.assertEqual(collector._current_active_partition_files(), set())
            self.assertEqual(len(collector.generate_all_manifests()), 1)

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

    def test_upbit_list_subscriptions_confirms_same_owner(self) -> None:
        async def exercise() -> None:
            fake_ws = FakeWebSocket()
            fake_ws.queue_json({
                "method": "LIST_SUBSCRIPTIONS",
                "result": [
                    {"type": "orderbook", "codes": ["KRW-BTC"]},
                    {"type": "trade", "codes": ["KRW-BTC"]},
                ],
                "ticket": "t",
            })
            with tempfile.TemporaryDirectory() as tmp:
                collector = MultiExchangeMicrostructureCollector(
                    ["KRW-BTC"],
                    upbit_markets=["KRW-BTC"],
                    storage_base_dir=Path(tmp) / "raw",
                    enable_binance=False,
                )
                session_id = collector.session_evidence.open_session(
                    "upbit", ["upbit/orderbook/KRW-BTC", "upbit/trade/KRW-BTC"], "2026-09-14T12:00:00Z"
                )
                await collector._confirm_upbit_subscriptions(fake_ws, session_id, "t")
                self.assertEqual(
                    json.loads(fake_ws.sent[-1]),
                    [{"ticket": "t"}, {"method": "LIST_SUBSCRIPTIONS"}, {"format": "DEFAULT"}],
                )
                self.assertTrue(collector.session_evidence.is_confirmed(session_id))

        asyncio.run(exercise())

    def test_binance_requires_exact_list_response(self) -> None:
        async def exercise() -> None:
            fake_ws = FakeWebSocket()
            fake_ws.queue_json({"result": ["btcusdt@trade"], "id": 7})
            with tempfile.TemporaryDirectory() as tmp:
                collector = MultiExchangeMicrostructureCollector(
                    ["KRW-BTC"],
                    binance_symbols=["btcusdt"],
                    storage_base_dir=Path(tmp) / "raw",
                    enable_upbit=False,
                )
                session_id = collector.session_evidence.open_session(
                    "binance", ["binance/trade/btcusdt", "binance/orderbook/btcusdt"], "2026-09-14T12:00:00Z"
                )
                with self.assertRaisesRegex(ValueError, "SUBSCRIPTION_SET_MISMATCH"):
                    await collector._confirm_binance_subscriptions(fake_ws, session_id, 7)

                self.assertEqual(
                    json.loads(fake_ws.sent[-1]),
                    {"method": "LIST_SUBSCRIPTIONS", "id": 7},
                )

        asyncio.run(exercise())

    def test_bithumb_snapshot_confirms_feeds(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                collector = MultiExchangeMicrostructureCollector(
                    ["KRW-BTC"],
                    storage_base_dir=Path(tmp) / "raw",
                    enable_binance=False,
                    enable_upbit=False,
                )
                session_id = collector.session_evidence.open_session(
                    "bithumb",
                    ["bithumb/orderbook/KRW-BTC", "bithumb/trade/KRW-BTC", "bithumb/ticker/KRW-BTC"],
                    "2026-09-14T12:00:00Z",
                )
                snapshot_frame = {
                    "type": "orderbook",
                    "code": "KRW-BTC",
                    "stream_type": "SNAPSHOT",
                    "timestamp": 1726315200000,
                    "orderbook_units": [],
                }
                collector._confirm_bithumb_feed(session_id, "orderbook", "KRW-BTC", snapshot_frame)
                self.assertTrue(collector.session_evidence.is_confirmed(session_id))
                seg = collector.session_evidence.segments_for(
                    FeedIdentity("bithumb", "orderbook", "KRW-BTC"),
                    "2026-09-14T12:00:00Z",
                    "2026-09-14T13:00:00Z",
                )[0]
                self.assertIn("bithumb/orderbook/KRW-BTC", seg.confirmed_feeds)

        asyncio.run(exercise())

    def test_append_failure_keeps_count_zero(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                collector = MultiExchangeMicrostructureCollector(
                    ["KRW-BTC"],
                    storage_base_dir=Path(tmp) / "raw",
                    enable_binance=False,
                    enable_upbit=False,
                )
                now = datetime(2026, 9, 14, 12, 10, tzinfo=timezone.utc)
                collector._utc_now = lambda: now
                event = ("bithumb", "trade", "KRW-BTC", {}, now, now, 1, collector._collector_run_id)
                feed = FeedIdentity("bithumb", "trade", "KRW-BTC")
                cohort = "2026-09-14_12"

                with patch.object(collector.storage, "append_raw_record", side_effect=OSError("disk full")):
                    await collector._writer_worker_once(event)

                self.assertEqual(cast(Any, collector.coverage_tracker).event_count(feed, cohort), 0)

        asyncio.run(exercise())

    def test_reconnect_invalidates_session_and_creates_new_segment(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                collector = MultiExchangeMicrostructureCollector(
                    ["KRW-BTC"],
                    storage_base_dir=Path(tmp) / "raw",
                    enable_binance=False,
                    enable_upbit=False,
                )
                feed = FeedIdentity("bithumb", "trade", "KRW-BTC")
                t1 = "2026-09-14T12:00:00Z"
                t2 = "2026-09-14T12:10:00Z"
                t3 = "2026-09-14T12:11:00Z"

                s1 = collector.session_evidence.open_session("bithumb", [feed.canonical], t1)
                collector.session_evidence.confirm(s1, [feed.canonical], "SNAPSHOT", t1)
                collector.session_evidence.close_session(s1, t2, "CONNECTION_RESET")

                s2 = collector.session_evidence.open_session("bithumb", [feed.canonical], t3)
                collector.session_evidence._sessions[s1].reconnect_successor_id = s2

                segs = collector.session_evidence.segments_for(feed, "2026-09-14T12:00:00Z", "2026-09-14T13:00:00Z")
                self.assertEqual(len(segs), 2)
                self.assertEqual(segs[0].session_id, s1)
                self.assertEqual(segs[0].disconnect_reason, "CONNECTION_RESET")
                self.assertEqual(segs[0].reconnect_successor_id, s2)
                self.assertEqual(segs[1].session_id, s2)
                self.assertFalse(collector.session_evidence.is_confirmed(s2))

        asyncio.run(exercise())

    def test_heartbeat_timeout_marks_disconnect(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                collector = MultiExchangeMicrostructureCollector(
                    ["KRW-BTC"],
                    storage_base_dir=Path(tmp) / "raw",
                    enable_binance=False,
                    enable_upbit=False,
                )
                collector.heartbeat_policy = HeartbeatPolicy(
                    heartbeat_probe_interval_seconds=0,
                    heartbeat_timeout_seconds=0,
                )
                fake_ws = FakeWebSocket()
                async def timing_out_ping() -> asyncio.Future[None]:
                    fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
                    return fut
                fake_ws.ping = timing_out_ping  # type: ignore

                sid = collector.session_evidence.open_session("bithumb", ["bithumb/trade/KRW-BTC"], "2026-09-14T12:00:00Z")
                collector.is_running = True
                await collector._heartbeat_loop(fake_ws, "bithumb", sid)

                sess = collector.session_evidence._sessions[sid]
                self.assertEqual(sess.disconnect_reason, "HEARTBEAT_TIMEOUT")
                self.assertIsNotNone(sess.disconnected_at_utc)
                self.assertTrue(fake_ws.closed)

        asyncio.run(exercise())

    def test_bithumb_feed_confirmation_preserves_initial_timestamp(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                collector = MultiExchangeMicrostructureCollector(
                    ["KRW-BTC"],
                    storage_base_dir=Path(tmp) / "raw",
                    enable_binance=False,
                    enable_upbit=False,
                )
                session_id = collector.session_evidence.open_session(
                    "bithumb",
                    ["bithumb/orderbook/KRW-BTC", "bithumb/trade/KRW-BTC", "bithumb/ticker/KRW-BTC"],
                    "2026-09-14T12:00:00Z",
                )
                # Initial confirmations
                collector._confirm_bithumb_feed(session_id, "orderbook", "KRW-BTC", {})
                collector._confirm_bithumb_feed(session_id, "trade", "KRW-BTC", {})
                collector._confirm_bithumb_feed(session_id, "ticker", "KRW-BTC", {})

                sess = collector.session_evidence._sessions[session_id]
                initial_confirmed_at = sess.confirmed_at_utc
                self.assertIsNotNone(initial_confirmed_at)

                # Subsequent messages arriving later should not modify confirmed_at_utc
                with patch.object(collector, "_utc_now", return_value=datetime(2026, 9, 14, 12, 59, 59, tzinfo=timezone.utc)):
                    collector._confirm_bithumb_feed(session_id, "trade", "KRW-BTC", {})
                    collector._confirm_bithumb_feed(session_id, "orderbook", "KRW-BTC", {})
                    collector._confirm_bithumb_feed(session_id, "ticker", "KRW-BTC", {})

                self.assertEqual(sess.confirmed_at_utc, initial_confirmed_at)

        asyncio.run(exercise())

    def test_collector_persists_frozen_journals_on_boundary_and_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current_time = datetime(2026, 9, 14, 12, 10, 0, tzinfo=timezone.utc)
            collector = MultiExchangeMicrostructureCollector(
                ["KRW-BTC"],
                storage_base_dir=Path(tmp) / "raw",
                enable_binance=False,
                enable_upbit=False,
                utc_now=lambda: current_time,
            )
            self.assertTrue(hasattr(collector, "journals_dir"))

            # Write record in hour 12
            self._write_one(collector, "bithumb", "trade", "KRW-BTC", current_time)

            # Boundary crossing in hour 13 triggers freeze_completed
            current_time = datetime(2026, 9, 14, 13, 5, 0, tzinfo=timezone.utc)
            self._write_one(collector, "bithumb", "trade", "KRW-BTC", current_time)

            # Verify boundary journal file exists
            journal_12 = collector.journals_dir / "journal_2026-09-14_12.json"
            self.assertTrue(journal_12.exists())
            loaded_12 = load_frozen_journal(journal_12)
            self.assertGreaterEqual(len(loaded_12), 1)

            # Shutdown freeze
            collector.finalize_all()
            journal_13 = collector.journals_dir / "journal_2026-09-14_13.json"
            self.assertTrue(journal_13.exists())
            loaded_13 = load_frozen_journal(journal_13)
            self.assertGreaterEqual(len(loaded_13), 1)

            # Verify get_frozen_observations
            frozen_all = collector.get_frozen_observations()
            self.assertGreaterEqual(len(frozen_all), len(loaded_12) + len(loaded_13))

    def test_upbit_requires_exact_list_response(self) -> None:
        async def exercise() -> None:
            fake_ws = FakeWebSocket()
            fake_ws.queue_json({
                "method": "LIST_SUBSCRIPTIONS",
                "result": [{"type": "trade", "codes": ["KRW-BTC"]}],  # missing orderbook
                "ticket": "t",
            })
            with tempfile.TemporaryDirectory() as tmp:
                collector = MultiExchangeMicrostructureCollector(
                    ["KRW-BTC"],
                    upbit_markets=["KRW-BTC"],
                    storage_base_dir=Path(tmp) / "raw",
                    enable_binance=False,
                )
                session_id = collector.session_evidence.open_session(
                    "upbit", ["upbit/orderbook/KRW-BTC", "upbit/trade/KRW-BTC"], "2026-09-14T12:00:00Z"
                )
                with self.assertRaisesRegex(ValueError, "SUBSCRIPTION_SET_MISMATCH"):
                    await collector._confirm_upbit_subscriptions(fake_ws, session_id, "t")

        asyncio.run(exercise())

    def test_stale_stream_timeout_closes_session(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                collector = MultiExchangeMicrostructureCollector(
                    ["KRW-BTC"],
                    storage_base_dir=Path(tmp) / "raw",
                    enable_binance=False,
                    enable_upbit=False,
                )
                fake_ws = FakeWebSocket()
                async def timing_out_recv() -> str | bytes:
                    collector.is_running = False
                    raise asyncio.TimeoutError()
                fake_ws.recv = timing_out_recv  # type: ignore

                with patch("websockets.connect", return_value=fake_ws):
                    collector.is_running = True
                    await collector._bithumb_loop()

                sessions = list(collector.session_evidence._sessions.values())
                self.assertGreaterEqual(len(sessions), 1)
                sess = sessions[0]
                self.assertEqual(sess.disconnect_reason, "connection_stale_30s")
                self.assertIsNotNone(sess.disconnected_at_utc)

        asyncio.run(exercise())

    def test_binance_confirmation_processes_interim_frames(self) -> None:
        async def exercise() -> None:
            interim_trade = {
                "stream": "btcusdt@trade",
                "data": {
                    "e": "trade",
                    "E": 1726315200000,
                    "s": "BTCUSDT",
                    "t": 12345,
                    "p": "50000.00",
                    "q": "0.1",
                    "T": 1726315200000,
                    "m": True,
                    "M": True,
                },
            }
            resp = {"result": ["btcusdt@trade", "btcusdt@depth20@100ms"], "id": 7}
            fake_ws = FakeWebSocket()
            fake_ws.queue_json(interim_trade)
            fake_ws.queue_json(resp)

            with tempfile.TemporaryDirectory() as tmp:
                collector = MultiExchangeMicrostructureCollector(
                    ["KRW-BTC"],
                    binance_symbols=["btcusdt"],
                    storage_base_dir=Path(tmp) / "raw",
                    enable_upbit=False,
                )
                session_id = collector.session_evidence.open_session(
                    "binance", ["binance/trade/btcusdt", "binance/orderbook/btcusdt"], "2026-09-14T12:00:00Z"
                )
                await collector._confirm_binance_subscriptions(fake_ws, session_id, 7)
                self.assertTrue(collector.session_evidence.is_confirmed(session_id))
                self.assertEqual(collector._write_queue.qsize(), 1)
                item = collector._write_queue.get_nowait()
                self.assertEqual(item[0], "binance")
                self.assertEqual(item[1], "trade")
                self.assertEqual(item[2], "BTCUSDT")

        asyncio.run(exercise())

    def test_upbit_confirmation_processes_interim_frames(self) -> None:
        async def exercise() -> None:
            interim_trade = {
                "type": "trade",
                "code": "KRW-BTC",
                "trade_price": 50000000.0,
                "trade_volume": 0.01,
                "ask_bid": "BID",
                "prev_closing_price": 49000000.0,
                "change": "RISE",
                "change_price": 1000000.0,
                "trade_date_utc": "2026-09-14",
                "trade_time_utc": "12:00:00",
                "trade_timestamp": 1726315200000,
                "timestamp": 1726315200000,
                "sequential_id": 12345,
                "stream_type": "REALTIME",
            }
            resp = {
                "ticket": "t",
                "result": [
                    {"type": "orderbook", "codes": ["KRW-BTC"]},
                    {"type": "trade", "codes": ["KRW-BTC"]},
                ],
            }
            fake_ws = FakeWebSocket()
            fake_ws.queue_json(interim_trade)
            fake_ws.queue_json(resp)

            with tempfile.TemporaryDirectory() as tmp:
                collector = MultiExchangeMicrostructureCollector(
                    ["KRW-BTC"],
                    upbit_markets=["KRW-BTC"],
                    storage_base_dir=Path(tmp) / "raw",
                    enable_binance=False,
                )
                session_id = collector.session_evidence.open_session(
                    "upbit", ["upbit/orderbook/KRW-BTC", "upbit/trade/KRW-BTC"], "2026-09-14T12:00:00Z"
                )
                await collector._confirm_upbit_subscriptions(fake_ws, session_id, "t")
                self.assertTrue(collector.session_evidence.is_confirmed(session_id))
                self.assertEqual(collector._write_queue.qsize(), 1)
                item = collector._write_queue.get_nowait()
                self.assertEqual(item[0], "upbit")
                self.assertEqual(item[1], "trade")
                self.assertEqual(item[2], "KRW-BTC")

        asyncio.run(exercise())


class FakeWebSocket:
    def __init__(self, incoming: list[str | bytes | dict[str, Any]] | None = None) -> None:
        self.incoming: asyncio.Queue[str | bytes] = asyncio.Queue()
        if incoming:
            for item in incoming:
                if isinstance(item, dict):
                    self.queue_json(item)
                else:
                    self.incoming.put_nowait(item)
        self.sent: list[str] = []
        self.closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None

    def queue_json(self, data: Any) -> None:
        self.incoming.put_nowait(json.dumps(data))

    async def send(self, data: str | bytes) -> None:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        self.sent.append(data)

    async def recv(self) -> str | bytes:
        if self.closed:
            raise websockets.exceptions.ConnectionClosed(None, None)
        return await self.incoming.get()

    async def ping(self) -> asyncio.Future[None]:
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        fut.set_result(None)
        return fut

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason

    async def __aenter__(self) -> FakeWebSocket:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()


if __name__ == "__main__":
    unittest.main()
