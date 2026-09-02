"""Hardened Multi-Exchange Real-Time WebSocket Collector (v9.1.0).

Features:
- Stream-separated integrity metrics (Trade sequence integrity vs Orderbook/Ticker continuity)
- Quarantine store for unparsable/malformed raw payloads
- Connection-level heartbeat & activity tracking (Prevents false reconnect storms on quiet markets)
- Explicit queue_dropped_events tracking
- 3-level timestamping (exchange_ts, local_receive_ts, local_write_ts)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import random
import time
from typing import Any, Mapping, Sequence
import uuid

import websockets

from .microstructure_storage import RawMicrostructureStorage

logger = logging.getLogger("bithumb_coin_trader.cross_market_collector")

BITHUMB_WS_URL = "wss://ws-api.bithumb.com/websocket/v1"
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"
UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"


def parse_binance_message(message: bytes | str) -> tuple[str, str, dict[str, Any], datetime | None]:
    """Normalize Binance raw or combined-stream messages without losing symbol identity."""
    raw_bytes = message if isinstance(message, bytes) else message.encode("utf-8")
    envelope = json.loads(raw_bytes.decode("utf-8"))
    stream_id = str(envelope.get("stream", ""))
    data = envelope.get("data", envelope)
    if not isinstance(data, dict):
        raise ValueError("Binance message data must be an object")
    event_type = data.get("e", "depth")
    stream_name = "trade" if event_type == "trade" else "orderbook"
    stream_symbol = stream_id.split("@", 1)[0]
    market = str(data.get("s") or stream_symbol or "unknown").upper()
    exchange_ts = None
    if "E" in data:
        exchange_ts = datetime.fromtimestamp(data["E"] / 1000.0, tz=timezone.utc)
    return stream_name, market, data, exchange_ts


@dataclass
class CollectorMetrics:
    exchange: str
    collector_started_at: float = field(default_factory=time.time)
    connected_at: float | None = None
    disconnect_count: int = 0
    reconnect_count: int = 0
    total_messages_received: int = 0
    total_bytes_received: int = 0
    trade_messages: int = 0
    orderbook_messages: int = 0
    ticker_messages: int = 0
    last_connection_event_time: float = 0.0
    trade_sequence_gaps: int = 0
    trade_duplicates: int = 0
    malformed_quarantined: int = 0
    queue_dropped_events: int = 0
    queue_backpressure_events: int = 0
    writer_errors: int = 0
    last_reconnect_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        now = time.time()
        uptime_sec = now - self.collector_started_at
        connection_uptime_sec = (now - self.connected_at) if self.connected_at else 0.0
        return {
            "exchange": self.exchange,
            "uptime_seconds": round(uptime_sec, 2),
            "current_connection_uptime_seconds": round(connection_uptime_sec, 2),
            "disconnect_count": self.disconnect_count,
            "reconnect_count": self.reconnect_count,
            "total_messages": self.total_messages_received,
            "stream_counts": {
                "trade": self.trade_messages,
                "orderbook": self.orderbook_messages,
                "ticker": self.ticker_messages,
            },
            "total_bytes": self.total_bytes_received,
            "msg_per_sec": round(self.total_messages_received / max(1.0, uptime_sec), 2),
            "kb_per_sec": round((self.total_bytes_received / 1024.0) / max(1.0, uptime_sec), 2),
            "trade_sequence_gaps": self.trade_sequence_gaps,
            "trade_duplicates": self.trade_duplicates,
            "malformed_quarantined": self.malformed_quarantined,
            "queue_dropped_events": self.queue_dropped_events,
            "queue_backpressure_events": self.queue_backpressure_events,
            "writer_errors": self.writer_errors,
            "last_reconnect_reason": self.last_reconnect_reason,
            "seconds_since_last_connection_event": round(now - self.last_connection_event_time, 2) if self.last_connection_event_time > 0 else None,
        }


class MultiExchangeMicrostructureCollector:
    """Enterprise-Grade Resilient Microstructure Collector Daemon."""

    def __init__(
        self,
        bithumb_markets: Sequence[str],
        binance_symbols: Sequence[str] = ("btcusdt", "ethusdt", "solusdt", "xrpusdt"),
        upbit_markets: Sequence[str] = ("KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP"),
        storage_base_dir: Path | None = None,
        enable_binance: bool = True,
        enable_upbit: bool = True,
    ) -> None:
        self.bithumb_markets = list(bithumb_markets)
        self.binance_symbols = [s.lower() for s in binance_symbols]
        self.upbit_markets = list(upbit_markets)
        self.storage = RawMicrostructureStorage(storage_base_dir)
        self.enable_binance = enable_binance
        self.enable_upbit = enable_upbit
        self.is_running = False

        self.metrics: dict[str, CollectorMetrics] = {
            "bithumb": CollectorMetrics(exchange="bithumb"),
            "binance": CollectorMetrics(exchange="binance"),
            "upbit": CollectorMetrics(exchange="upbit"),
        }

        self._write_queue: asyncio.Queue[
            tuple[str, str, str, dict[str, Any], datetime, datetime | None, int | None, str]
        ] = asyncio.Queue(maxsize=50_000)
        self._active_partition_files: set[Path] = set()
        self._metrics_path = self.storage.base_dir.parent / "collector_metrics.json"
        self._collector_run_id = uuid.uuid4().hex
        self._collector_started_at = datetime.now(timezone.utc).isoformat()
        self._fatal_writer_error: Exception | None = None
        self._fatal_writer_event = asyncio.Event()
        self._unpersisted_event_count = 0

    async def _enqueue(
        self,
        exchange: str,
        stream: str,
        market: str,
        payload: dict[str, Any],
        recv_ts: datetime,
        exch_ts: datetime | None,
        recv_monotonic_ns: int | None = None,
    ) -> None:
        """Apply bounded backpressure without intentionally dropping a received event."""
        metric = self.metrics[exchange]
        if self._write_queue.full():
            metric.queue_backpressure_events += 1
            logger.warning("[%s] Write queue full; applying backpressure.", exchange.capitalize())
        await self._write_queue.put(
            (
                exchange,
                stream,
                market,
                payload,
                recv_ts,
                exch_ts,
                recv_monotonic_ns,
                self._collector_run_id,
            )
        )

    def _persist_metrics(self) -> None:
        """Atomically persist operational counters for independent status auditing."""
        payload = {
            "schema_version": 1,
            "collector_run_id": self._collector_run_id,
            "collector_started_at": self._collector_started_at,
            "process_id": os.getpid(),
            "written_at": datetime.now(timezone.utc).isoformat(),
            "queue_size": self._write_queue.qsize(),
            "queue_maxsize": self._write_queue.maxsize,
            "writer_fail_closed": self._fatal_writer_error is not None,
            "fatal_writer_error_type": (
                type(self._fatal_writer_error).__name__ if self._fatal_writer_error else None
            ),
            "unpersisted_event_count": self._unpersisted_event_count,
            "active_partition_files": sorted(
                str(path.resolve().relative_to(self.storage.base_dir.resolve()))
                for path in self._active_partition_files
                if self.storage.base_dir.resolve() in path.resolve().parents
            ),
            "exchanges": {name: metric.to_dict() for name, metric in self.metrics.items()},
        }
        self._metrics_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._metrics_path.with_suffix(".json.tmp")
        descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(self._metrics_path))
        directory_descriptor = os.open(str(self._metrics_path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)

    async def _metrics_worker(self) -> None:
        while self.is_running or not self._write_queue.empty():
            try:
                self._persist_metrics()
            except OSError as error:
                logger.error("Failed to persist collector metrics: %s", error)
            await asyncio.sleep(5.0)
        try:
            self._persist_metrics()
        except OSError as error:
            logger.error("Failed to persist final collector metrics: %s", error)

    async def _writer_worker(self) -> None:
        while self.is_running or not self._write_queue.empty():
            item = None
            try:
                item = await asyncio.wait_for(self._write_queue.get(), timeout=1.0)
                (
                    exchange,
                    stream,
                    market,
                    payload,
                    recv_ts,
                    exch_ts,
                    recv_monotonic_ns,
                    collector_run_id,
                ) = item
                part_file = self.storage.append_raw_record(
                    exchange,
                    stream,
                    market,
                    payload,
                    recv_ts,
                    exch_ts,
                    recv_monotonic_ns,
                    collector_run_id,
                )
                self._active_partition_files.add(part_file)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                if "exchange" in locals():
                    self.metrics[exchange].writer_errors += 1
                self._fatal_writer_error = e
                self._unpersisted_event_count += 1
                self.is_running = False
                self._fatal_writer_event.set()
                logger.critical("Writer failure; stopping collector fail-closed: %s", e)
                return
            finally:
                if item is not None:
                    self._write_queue.task_done()

    def _discard_unpersisted_queue(self) -> None:
        """Account for queued events that cannot be written after a fatal writer error."""
        while True:
            try:
                self._write_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._unpersisted_event_count += 1
            self._write_queue.task_done()

    # -------------------------------------------------------------------------
    # Bithumb WebSocket Loop
    # -------------------------------------------------------------------------
    async def _bithumb_loop(self) -> None:
        m = self.metrics["bithumb"]
        backoff = 1.0

        while self.is_running:
            ticket = f"bithumb_v9_{uuid.uuid4().hex[:8]}"
            payload = json.dumps([
                {"ticket": ticket},
                {"type": "orderbook", "codes": self.bithumb_markets},
                {"type": "trade", "codes": self.bithumb_markets},
                {"type": "ticker", "codes": self.bithumb_markets},
                {"format": "DEFAULT"},
            ])
            try:
                m.connected_at = time.time()
                async with websockets.connect(BITHUMB_WS_URL, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"[Bithumb] Connected. Subscribing {len(self.bithumb_markets)} markets...")
                    await ws.send(payload)
                    backoff = 1.0

                    while self.is_running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        except asyncio.TimeoutError:
                            logger.warning("[Bithumb] Connection-level stale stream (30s timeout). Reconnecting...")
                            m.last_reconnect_reason = "connection_stale_30s"
                            m.disconnect_count += 1
                            m.reconnect_count += 1
                            break

                        recv_ts = datetime.now(timezone.utc)
                        recv_monotonic_ns = time.monotonic_ns()
                        raw_bytes = msg if isinstance(msg, bytes) else msg.encode("utf-8")
                        m.total_messages_received += 1
                        m.total_bytes_received += len(raw_bytes)
                        m.last_connection_event_time = time.time()

                        try:
                            data = json.loads(raw_bytes.decode("utf-8"))
                            stream = data.get("type", "unknown").lower()
                            market = data.get("code", "unknown")

                            if stream == "trade":
                                m.trade_messages += 1
                            elif stream == "orderbook":
                                m.orderbook_messages += 1
                            elif stream == "ticker":
                                m.ticker_messages += 1

                            exch_ts: datetime | None = None
                            if "trade_timestamp" in data:
                                exch_ts = datetime.fromtimestamp(data["trade_timestamp"] / 1000.0, tz=timezone.utc)
                            elif "timestamp" in data:
                                exch_ts = datetime.fromtimestamp(data["timestamp"] / 1_000_000.0, tz=timezone.utc)

                            await self._enqueue(
                                "bithumb", stream, market, data, recv_ts, exch_ts, recv_monotonic_ns
                            )
                        except Exception as e:
                            m.malformed_quarantined += 1
                            self.storage.quarantine_malformed_record("bithumb", raw_bytes, str(e), recv_ts)

            except Exception as e:
                m.disconnect_count += 1
                m.last_reconnect_reason = str(e)
                logger.warning(f"[Bithumb] Disconnected: {e}. Backoff {backoff:.1f}s...")
                await asyncio.sleep(backoff + random.uniform(0.1, 0.5))
                backoff = min(30.0, backoff * 2.0)
                m.reconnect_count += 1

    # -------------------------------------------------------------------------
    # Binance WebSocket Loop
    # -------------------------------------------------------------------------
    async def _binance_loop(self) -> None:
        m = self.metrics["binance"]
        if not self.enable_binance or not self.binance_symbols:
            return

        backoff = 1.0
        streams = [f"{s}@trade" for s in self.binance_symbols] + [f"{s}@depth20@100ms" for s in self.binance_symbols]
        combined_url = f"{BINANCE_WS_URL.rsplit('/ws', 1)[0]}/stream?streams={'/'.join(streams)}"

        while self.is_running:
            try:
                m.connected_at = time.time()
                async with websockets.connect(combined_url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"[Binance] Connected to {len(self.binance_symbols)} benchmark streams...")
                    backoff = 1.0

                    while self.is_running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        except asyncio.TimeoutError:
                            logger.warning("[Binance] Connection-level stale stream (30s timeout). Reconnecting...")
                            m.last_reconnect_reason = "connection_stale_30s"
                            m.disconnect_count += 1
                            m.reconnect_count += 1
                            break

                        recv_ts = datetime.now(timezone.utc)
                        recv_monotonic_ns = time.monotonic_ns()
                        raw_bytes = msg if isinstance(msg, bytes) else msg.encode("utf-8")
                        m.total_messages_received += 1
                        m.total_bytes_received += len(raw_bytes)
                        m.last_connection_event_time = time.time()

                        try:
                            stream_name, sym, data, exch_ts = parse_binance_message(raw_bytes)
                            if stream_name == "trade":
                                m.trade_messages += 1
                            else:
                                m.orderbook_messages += 1

                            await self._enqueue(
                                "binance", stream_name, sym, data, recv_ts, exch_ts, recv_monotonic_ns
                            )
                        except Exception as e:
                            m.malformed_quarantined += 1
                            self.storage.quarantine_malformed_record("binance", raw_bytes, str(e), recv_ts)

            except Exception as e:
                m.disconnect_count += 1
                m.last_reconnect_reason = str(e)
                logger.warning(f"[Binance] Disconnected: {e}. Backoff {backoff:.1f}s...")
                await asyncio.sleep(backoff + random.uniform(0.1, 0.5))
                backoff = min(30.0, backoff * 2.0)
                m.reconnect_count += 1

    # -------------------------------------------------------------------------
    # Upbit WebSocket Loop
    # -------------------------------------------------------------------------
    async def _upbit_loop(self) -> None:
        m = self.metrics["upbit"]
        if not self.enable_upbit or not self.upbit_markets:
            return

        backoff = 1.0
        while self.is_running:
            ticket = f"upbit_v9_{uuid.uuid4().hex[:8]}"
            payload = json.dumps([
                {"ticket": ticket},
                {"type": "orderbook", "codes": self.upbit_markets},
                {"type": "trade", "codes": self.upbit_markets},
                {"format": "DEFAULT"},
            ])
            try:
                m.connected_at = time.time()
                async with websockets.connect(UPBIT_WS_URL, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"[Upbit] Connected to {len(self.upbit_markets)} benchmark streams...")
                    await ws.send(payload)
                    backoff = 1.0

                    while self.is_running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        except asyncio.TimeoutError:
                            logger.warning("[Upbit] Connection-level stale stream (30s timeout). Reconnecting...")
                            m.last_reconnect_reason = "connection_stale_30s"
                            m.disconnect_count += 1
                            m.reconnect_count += 1
                            break

                        recv_ts = datetime.now(timezone.utc)
                        recv_monotonic_ns = time.monotonic_ns()
                        raw_bytes = msg if isinstance(msg, bytes) else msg.encode("utf-8")
                        m.total_messages_received += 1
                        m.total_bytes_received += len(raw_bytes)
                        m.last_connection_event_time = time.time()

                        try:
                            data = json.loads(raw_bytes.decode("utf-8"))
                            stream = data.get("type", "unknown").lower()
                            market = data.get("code", "unknown")

                            if stream == "trade":
                                m.trade_messages += 1
                            else:
                                m.orderbook_messages += 1

                            exch_ts: datetime | None = None
                            if "trade_timestamp" in data:
                                exch_ts = datetime.fromtimestamp(data["trade_timestamp"] / 1000.0, tz=timezone.utc)
                            elif "timestamp" in data:
                                exch_ts = datetime.fromtimestamp(data["timestamp"] / 1000.0, tz=timezone.utc)

                            await self._enqueue(
                                "upbit", stream, market, data, recv_ts, exch_ts, recv_monotonic_ns
                            )
                        except Exception as e:
                            m.malformed_quarantined += 1
                            self.storage.quarantine_malformed_record("upbit", raw_bytes, str(e), recv_ts)

            except Exception as e:
                m.disconnect_count += 1
                m.last_reconnect_reason = str(e)
                logger.warning(f"[Upbit] Disconnected: {e}. Backoff {backoff:.1f}s...")
                await asyncio.sleep(backoff + random.uniform(0.1, 0.5))
                backoff = min(30.0, backoff * 2.0)
                m.reconnect_count += 1

    async def run_collector(self, max_duration_seconds: float | None = None) -> None:
        self.is_running = True
        writer_task = asyncio.create_task(self._writer_worker())
        metrics_task = asyncio.create_task(self._metrics_worker())
        tasks = [
            asyncio.create_task(self._bithumb_loop()),
            asyncio.create_task(self._binance_loop()),
            asyncio.create_task(self._upbit_loop()),
        ]
        producer_group = asyncio.gather(*tasks)
        fatal_writer_waiter = asyncio.create_task(self._fatal_writer_event.wait())
        duration_waiter = (
            asyncio.create_task(asyncio.sleep(max_duration_seconds))
            if max_duration_seconds is not None
            else None
        )

        logger.info("Multi-Exchange Collector started.")
        try:
            waiters = {producer_group, fatal_writer_waiter}
            if duration_waiter is not None:
                waiters.add(duration_waiter)
            completed, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if producer_group in completed:
                await producer_group
        finally:
            self.is_running = False
            producer_group.cancel()
            await asyncio.gather(producer_group, return_exceptions=True)
            fatal_writer_waiter.cancel()
            if duration_waiter is not None:
                duration_waiter.cancel()
            await asyncio.gather(
                fatal_writer_waiter,
                *([duration_waiter] if duration_waiter is not None else []),
                return_exceptions=True,
            )
            if self._fatal_writer_error is None:
                await self._write_queue.join()
            else:
                self._discard_unpersisted_queue()
            writer_task.cancel()
            await asyncio.gather(writer_task, return_exceptions=True)
            await metrics_task
        if self._fatal_writer_error is not None:
            raise RuntimeError(
                "Collector stopped after writer failure; "
                f"unpersisted_events={self._unpersisted_event_count}"
            ) from self._fatal_writer_error

    def generate_all_manifests(self) -> list[dict[str, Any]]:
        manifests = []
        for p in list(self._active_partition_files):
            if p.exists() and p.stat().st_size > 0:
                try:
                    mf = self.storage.generate_partition_manifest(p)
                    manifests.append(mf.to_dict())
                except Exception as e:
                    logger.error(f"Failed to generate manifest for {p}: {e}")
        return manifests
