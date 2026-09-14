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
import re
import time
from typing import Any, Callable, Mapping, Sequence
import uuid

import websockets

from bithumb_coin_trader.feed_hour_coverage import (
    DataArtifactBinding,
    FeedHourCoverage,
    FeedHourCoverageTracker,
    FrozenFeedHourObservation,
    materialize_feed_hour_coverage,
    save_feed_hour_coverage,
)
from bithumb_coin_trader.incremental_finalizer import (
    ArtifactBinding,
    FinalizationEntry,
    FinalizationEvidenceError,
    FinalizationIdentity,
    FinalizationProgressStore,
    FinalizationState,
    FinalizationSummary,
    IncrementalManifestFinalizer,
)
from bithumb_coin_trader.session_evidence import (
    FeedIdentity,
    HeartbeatPolicy,
    SessionEvidenceTracker,
    WriterHealthSnapshot,
    normalize_feed_str,
)
from .microstructure_storage import RawMicrostructureStorage

logger = logging.getLogger("bithumb_coin_trader.cross_market_collector")

BITHUMB_WS_URL = "wss://ws-api.bithumb.com/websocket/v1"
BINANCE_WS_URL = "wss://stream.binance.com:443/ws"
UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"

SEALED_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
LOWER_HEX_40 = re.compile(r"^[0-9a-f]{40}$")


def _feed_hour_event_count(self: FeedHourCoverageTracker, feed: FeedIdentity, cohort: str) -> int:
    stats = self._cohort_feed_stats.get((cohort, feed))
    return stats["event_count"] if stats is not None else 0


FeedHourCoverageTracker.event_count = _feed_hour_event_count  # type: ignore[attr-defined]


def upbit_list_subscriptions_request(ticket: str) -> list[dict[str, str]]:
    return [
        {"ticket": ticket},
        {"method": "LIST_SUBSCRIPTIONS"},
        {"format": "DEFAULT"},
    ]


def binance_list_subscriptions_request(request_id: int) -> dict[str, str | int]:
    return {"method": "LIST_SUBSCRIPTIONS", "id": request_id}


def build_binance_combined_url(symbols: Sequence[str]) -> str:
    normalized = [symbol.lower() for symbol in symbols]
    streams = [f"{symbol}@trade" for symbol in normalized] + [
        f"{symbol}@depth20@100ms" for symbol in normalized
    ]
    return f"{BINANCE_WS_URL.rsplit('/ws', 1)[0]}/stream?streams={'/'.join(streams)}"


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


def parse_bithumb_message(message: bytes | str) -> tuple[str, str, dict[str, Any], datetime | None]:
    """Pure deterministic parser for Bithumb WebSocket messages."""
    raw_bytes = message if isinstance(message, bytes) else message.encode("utf-8")
    data = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Bithumb message must be a JSON object")
    stream = data.get("type", "unknown").lower()
    market = data.get("code", "unknown")

    exch_ts: datetime | None = None
    if "trade_timestamp" in data:
        exch_ts = datetime.fromtimestamp(data["trade_timestamp"] / 1000.0, tz=timezone.utc)
    elif "timestamp" in data:
        raw_val = data["timestamp"]
        if raw_val > 1e14:  # microseconds
            exch_ts = datetime.fromtimestamp(raw_val / 1_000_000.0, tz=timezone.utc)
        elif raw_val > 1e11:  # milliseconds
            exch_ts = datetime.fromtimestamp(raw_val / 1000.0, tz=timezone.utc)
        else:
            exch_ts = datetime.fromtimestamp(float(raw_val), tz=timezone.utc)

    return stream, market, data, exch_ts


def parse_upbit_message(message: bytes | str) -> tuple[str, str, dict[str, Any], datetime | None]:
    """Pure deterministic parser for Upbit WebSocket messages."""
    raw_bytes = message if isinstance(message, bytes) else message.encode("utf-8")
    data = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Upbit message must be a JSON object")
    stream = data.get("type", "unknown").lower()
    market = data.get("code", "unknown")

    exch_ts: datetime | None = None
    if "trade_timestamp" in data:
        exch_ts = datetime.fromtimestamp(data["trade_timestamp"] / 1000.0, tz=timezone.utc)
    elif "timestamp" in data:
        exch_ts = datetime.fromtimestamp(data["timestamp"] / 1000.0, tz=timezone.utc)

    return stream, market, data, exch_ts


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
        environment_id: str = "NOT-SEALED",
        collector_epoch: str = "NOT-SEALED",
        collector_run_id: str | None = None,
        collector_config_fingerprint: str = "NOT-SEALED",
        collector_git_commit: str = "HEAD",
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        run_id = collector_run_id or uuid.uuid4().hex
        if not SEALED_IDENTIFIER.fullmatch(environment_id):
            raise ValueError("environment_id must be a non-empty safe identifier")
        if not SEALED_IDENTIFIER.fullmatch(collector_epoch):
            raise ValueError("collector_epoch must be a non-empty safe identifier")
        if not SEALED_IDENTIFIER.fullmatch(run_id):
            raise ValueError("collector_run_id must be a non-empty safe identifier")
        if collector_config_fingerprint != "NOT-SEALED" and not LOWER_HEX_64.fullmatch(
            collector_config_fingerprint
        ):
            raise ValueError("collector_config_fingerprint must be NOT-SEALED or lowercase SHA-256")
        if collector_git_commit != "HEAD" and not LOWER_HEX_40.fullmatch(collector_git_commit):
            raise ValueError("collector_git_commit must be HEAD or an exact lowercase commit")
        self.bithumb_markets = list(bithumb_markets)
        self.binance_symbols = [s.lower() for s in binance_symbols]
        self.upbit_markets = list(upbit_markets)
        self.storage = RawMicrostructureStorage(storage_base_dir, git_commit=collector_git_commit)
        self.enable_binance = enable_binance
        self.enable_upbit = enable_upbit
        self.environment_id = environment_id
        self.collector_epoch = collector_epoch
        self.collector_config_fingerprint = collector_config_fingerprint
        self.collector_git_commit = collector_git_commit
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self.is_running = False

        self.metrics: dict[str, CollectorMetrics] = {
            "bithumb": CollectorMetrics(exchange="bithumb"),
            "binance": CollectorMetrics(exchange="binance"),
            "upbit": CollectorMetrics(exchange="upbit"),
        }

        self._write_queue: asyncio.Queue[
            tuple[str, str, str, dict[str, Any], datetime, datetime | None, int | None, str]
        ] = asyncio.Queue(maxsize=50_000)
        self._latest_partition_by_feed: dict[tuple[str, str, str], tuple[Path, str]] = {}
        self._all_touched_partition_files: set[Path] = set()
        self._accepting_partition_writes = False
        self._metrics_path = self.storage.base_dir.parent / "collector_metrics.json"
        self._collector_run_id = run_id
        self._collector_started_at = self._utc_now().isoformat()
        self._fatal_writer_error: Exception | None = None
        self._fatal_writer_event = asyncio.Event()
        self._unpersisted_event_count = 0

        all_feeds: list[FeedIdentity] = []
        for mkt in self.bithumb_markets:
            all_feeds.append(FeedIdentity("bithumb", "orderbook", mkt))
            all_feeds.append(FeedIdentity("bithumb", "trade", mkt))
            all_feeds.append(FeedIdentity("bithumb", "ticker", mkt))
        if self.enable_binance:
            for sym in self.binance_symbols:
                all_feeds.append(FeedIdentity("binance", "trade", sym))
                all_feeds.append(FeedIdentity("binance", "orderbook", sym))
        if self.enable_upbit:
            for mkt in self.upbit_markets:
                all_feeds.append(FeedIdentity("upbit", "orderbook", mkt))
                all_feeds.append(FeedIdentity("upbit", "trade", mkt))
        self.configured_feeds: tuple[FeedIdentity, ...] = tuple(all_feeds)

        self.session_evidence = SessionEvidenceTracker(epoch=self.collector_epoch, run_id=self._collector_run_id)
        self.heartbeat_policy = HeartbeatPolicy(heartbeat_probe_interval_seconds=10, heartbeat_timeout_seconds=10)
        self.coverage_tracker = FeedHourCoverageTracker(
            feeds=self.configured_feeds,
            epoch=self.collector_epoch,
            run_id=self._collector_run_id,
            actual_start_utc=self._utc_now(),
        )
        finalizer_store_root = self.storage.base_dir.parent / "finalization-progress"
        self.finalizer_store = FinalizationProgressStore(finalizer_store_root)
        receipt_root = self.storage.base_dir.parent / "archive-receipts"
        self.finalizer = IncrementalManifestFinalizer(
            store=self.finalizer_store,
            storage=self.storage,
            receipt_root=receipt_root,
        )
        self._registered_partition_entry_ids: set[str] = set()
        self._current_writer_cohort: str | None = None
        self._bithumb_confirmed_by_session: dict[str, set[str]] = {}

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
            "environment_id": self.environment_id,
            "collector_epoch": self.collector_epoch,
            "collector_run_id": self._collector_run_id,
            "collector_config_fingerprint": self.collector_config_fingerprint,
            "collector_git_commit": self.collector_git_commit,
            "collector_started_at": self._collector_started_at,
            "process_id": os.getpid(),
            "written_at": self._utc_now().isoformat(),
            "queue_size": self._write_queue.qsize(),
            "queue_maxsize": self._write_queue.maxsize,
            "writer_fail_closed": self._fatal_writer_error is not None,
            "fatal_writer_error_type": (
                type(self._fatal_writer_error).__name__ if self._fatal_writer_error else None
            ),
            "unpersisted_event_count": self._unpersisted_event_count,
            "active_partition_files": sorted(
                str(path.resolve().relative_to(self.storage.base_dir.resolve()))
                for path in self._current_active_partition_files()
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

    def _get_writer_health_snapshot(self) -> WriterHealthSnapshot:
        total_writer_errors = sum(m.writer_errors for m in self.metrics.values())
        total_queue_dropped = sum(m.queue_dropped_events for m in self.metrics.values())
        return WriterHealthSnapshot(
            writer_error_count=total_writer_errors,
            queue_dropped_events=total_queue_dropped,
            unpersisted_event_count=self._unpersisted_event_count,
            fatal_writer_error_type=(
                type(self._fatal_writer_error).__name__ if self._fatal_writer_error is not None else None
            ),
        )

    async def _process_writer_item(
        self,
        item: tuple[str, str, str, dict[str, Any], datetime, datetime | None, int | None, str],
    ) -> None:
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
        write_ts = self._utc_now()
        if write_ts.tzinfo is None:
            write_ts = write_ts.replace(tzinfo=timezone.utc)
        else:
            write_ts = write_ts.astimezone(timezone.utc)

        cohort_utc = write_ts.strftime("%Y-%m-%d_%H")

        # Cohort boundary fence: when writer detects cohort crossing, freeze and finalize
        if self._current_writer_cohort is not None and cohort_utc != self._current_writer_cohort:
            boundary_dt = write_ts.replace(minute=0, second=0, microsecond=0)
            health = self._get_writer_health_snapshot()
            self.coverage_tracker.freeze_completed(boundary_dt, self.session_evidence, health)
            self.finalizer.finalize_pending()

        self._current_writer_cohort = cohort_utc

        # Register partition as PENDING with FinalizationProgressStore before first write
        feed_id = FeedIdentity(exchange=exchange, stream=stream, market=market)
        clean_market = market.replace("/", "-").replace(":", "-").lower()
        dt_str = write_ts.strftime("%Y-%m-%d")
        hour_str = write_ts.strftime("%H")
        raw_rel = f"{dt_str}/{exchange.lower()}/{stream.lower()}/{exchange.lower()}_{stream.lower()}_{clean_market}_{dt_str}_{hour_str}.jsonl"

        identity = FinalizationIdentity(
            environment_id=self.environment_id,
            collector_epoch=self.collector_epoch,
            collector_run_id=self._collector_run_id,
            cohort=cohort_utc,
            exchange=exchange.lower(),
            stream=stream.lower(),
            market=feed_id.market,
            feed_identity=feed_id.canonical,
            raw_relative_path=raw_rel,
        )
        if identity.entry_id not in self._registered_partition_entry_ids:
            self.finalizer_store.register_pending(identity)
            self._registered_partition_entry_ids.add(identity.entry_id)

        # Write RAW record
        part_file = self.storage.append_raw_record(
            exchange,
            stream,
            market,
            payload,
            recv_ts,
            exch_ts,
            recv_monotonic_ns,
            collector_run_id,
            write_ts=write_ts,
        )

        # POST-APPEND: record persisted event in FeedHourCoverageTracker only AFTER append succeeds
        self.coverage_tracker.record_persisted_event(feed_id, write_ts)

        feed_key = (exchange.lower(), stream.lower(), market.lower())
        hour_key = write_ts.strftime("%Y-%m-%d/%H")
        self._latest_partition_by_feed[feed_key] = (part_file, hour_key)
        self._all_touched_partition_files.add(part_file)

    async def _writer_worker_once(
        self,
        item: tuple[str, str, str, dict[str, Any], datetime, datetime | None, int | None, str],
    ) -> None:
        try:
            await self._process_writer_item(item)
        except Exception as e:
            exchange = item[0]
            if exchange in self.metrics:
                self.metrics[exchange].writer_errors += 1
            self._fatal_writer_error = e
            self._unpersisted_event_count += 1
            self.is_running = False
            self._fatal_writer_event.set()
            logger.critical("Writer failure; stopping collector fail-closed: %s", e)

    async def _writer_worker(self) -> None:
        while self.is_running or not self._write_queue.empty():
            item = None
            try:
                item = await asyncio.wait_for(self._write_queue.get(), timeout=1.0)
                await self._process_writer_item(item)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                if item is not None and len(item) > 0 and item[0] in self.metrics:
                    self.metrics[item[0]].writer_errors += 1
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

    def _current_active_partition_files(self) -> set[Path]:
        """Return current-hour paths that could receive another queued append."""
        if not self._accepting_partition_writes:
            return set()
        current_hour = self._utc_now().astimezone(timezone.utc).strftime("%Y-%m-%d/%H")
        return {
            path
            for path, hour_key in self._latest_partition_by_feed.values()
            if hour_key == current_hour
        }

    # -------------------------------------------------------------------------
    # Confirmation & Heartbeat Helpers
    # -------------------------------------------------------------------------
    def _confirm_bithumb_feed(
        self,
        session_id: str,
        stream: str,
        market: str,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        feed_str = normalize_feed_str(f"bithumb/{stream}/{market}")
        confirmed_set = self._bithumb_confirmed_by_session.setdefault(session_id, set())
        confirmed_set.add(feed_str)
        now_utc = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.session_evidence.confirm(
            session_id=session_id,
            confirmed=sorted(confirmed_set),
            method="STREAM_SNAPSHOT",
            confirmed_at_utc=now_utc,
            response=data,
        )

    async def _confirm_binance_subscriptions(
        self,
        ws: Any,
        session_id: str,
        request_id: int,
    ) -> None:
        req = binance_list_subscriptions_request(request_id)
        await ws.send(json.dumps(req))
        msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
        raw_bytes = msg if isinstance(msg, bytes) else msg.encode("utf-8")
        data = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("SUBSCRIPTION_SET_MISMATCH")

        expected_streams: set[str] = set()
        for sym in self.binance_symbols:
            s_low = sym.lower()
            expected_streams.add(f"{s_low}@trade")
            expected_streams.add(f"{s_low}@depth20@100ms")

        result = data.get("result")
        if not isinstance(result, list):
            raise ValueError("SUBSCRIPTION_SET_MISMATCH")

        actual_streams = set(str(s) for s in result)
        if actual_streams != expected_streams:
            raise ValueError("SUBSCRIPTION_SET_MISMATCH")

        confirmed_feeds: list[str] = []
        for sym in self.binance_symbols:
            s_low = sym.lower()
            confirmed_feeds.append(f"binance/trade/{s_low}")
            confirmed_feeds.append(f"binance/orderbook/{s_low}")

        now_utc = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.session_evidence.confirm(
            session_id=session_id,
            confirmed=confirmed_feeds,
            method="LIST_SUBSCRIPTIONS",
            confirmed_at_utc=now_utc,
            response=data,
        )

    async def _confirm_upbit_subscriptions(
        self,
        ws: Any,
        session_id: str,
        ticket: str,
    ) -> None:
        req = upbit_list_subscriptions_request(ticket)
        await ws.send(json.dumps(req))
        msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
        raw_bytes = msg if isinstance(msg, bytes) else msg.encode("utf-8")
        data = json.loads(raw_bytes.decode("utf-8"))
        confirmed_feeds: list[str] = []
        for mkt in self.upbit_markets:
            confirmed_feeds.append(f"upbit/orderbook/{mkt.upper()}")
            confirmed_feeds.append(f"upbit/trade/{mkt.upper()}")
        now_utc = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.session_evidence.confirm(
            session_id=session_id,
            confirmed=confirmed_feeds,
            method="LIST_SUBSCRIPTIONS",
            confirmed_at_utc=now_utc,
            response=data if isinstance(data, dict) else {"result": data},
        )

    async def _heartbeat_loop(self, ws: Any, exchange: str, session_id: str) -> None:
        probe_interval = self.heartbeat_policy.heartbeat_probe_interval_seconds
        timeout = self.heartbeat_policy.heartbeat_timeout_seconds
        try:
            while self.is_running:
                if probe_interval > 0:
                    await asyncio.sleep(probe_interval)
                if not self.is_running:
                    break
                try:
                    pong_waiter = await ws.ping()
                    timeout_val = float(timeout) if timeout > 0 else 0.001
                    await asyncio.wait_for(pong_waiter, timeout=timeout_val)
                    now_utc = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
                    self.session_evidence.record_heartbeat(session_id, now_utc, kind="PING_PONG")
                except (asyncio.TimeoutError, TimeoutError):
                    now_utc = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
                    logger.warning("[%s] Heartbeat timeout on session %s", exchange.capitalize(), session_id)
                    sess = self.session_evidence._sessions.get(session_id)
                    if sess is not None and sess.disconnected_at_utc is None:
                        self.session_evidence.close_session(session_id, now_utc, reason="HEARTBEAT_TIMEOUT")
                    self.metrics[exchange].last_reconnect_reason = "HEARTBEAT_TIMEOUT"
                    self.metrics[exchange].disconnect_count += 1
                    try:
                        await ws.close()
                    except Exception:
                        pass
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("[%s] Heartbeat loop exception: %s", exchange.capitalize(), e)

    # -------------------------------------------------------------------------
    # Bithumb WebSocket Loop
    # -------------------------------------------------------------------------
    async def _bithumb_loop(self) -> None:
        m = self.metrics["bithumb"]
        backoff = 1.0
        session_id: str | None = None

        requested_feeds = [
            f"bithumb/{stream}/{mkt.upper()}"
            for mkt in self.bithumb_markets
            for stream in ("orderbook", "trade", "ticker")
        ]

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
                now_str = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
                prev_session_id = session_id
                session_id = self.session_evidence.open_session("bithumb", requested_feeds, now_str)
                if prev_session_id is not None:
                    prev_sess = self.session_evidence._sessions.get(prev_session_id)
                    if prev_sess is not None and prev_sess.reconnect_successor_id is None:
                        prev_sess.reconnect_successor_id = session_id

                async with websockets.connect(BITHUMB_WS_URL, ping_interval=None) as ws:
                    logger.info(f"[Bithumb] Connected. Subscribing {len(self.bithumb_markets)} markets...")
                    await ws.send(payload)
                    backoff = 1.0
                    hb_task = asyncio.create_task(self._heartbeat_loop(ws, "bithumb", session_id))

                    try:
                        while self.is_running:
                            try:
                                msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                            except asyncio.TimeoutError:
                                logger.warning("[Bithumb] Connection-level stale stream (30s timeout). Reconnecting...")
                                m.last_reconnect_reason = "connection_stale_30s"
                                m.disconnect_count += 1
                                m.reconnect_count += 1
                                break

                            recv_ts = self._utc_now()
                            recv_monotonic_ns = time.monotonic_ns()
                            raw_bytes = msg if isinstance(msg, bytes) else msg.encode("utf-8")
                            m.total_messages_received += 1
                            m.total_bytes_received += len(raw_bytes)
                            m.last_connection_event_time = time.time()

                            try:
                                stream, market, data, exch_ts = parse_bithumb_message(raw_bytes)
                                if stream == "trade":
                                    m.trade_messages += 1
                                elif stream == "orderbook":
                                    m.orderbook_messages += 1
                                elif stream == "ticker":
                                    m.ticker_messages += 1

                                self.session_evidence.record_heartbeat(
                                    session_id, recv_ts.strftime("%Y-%m-%dT%H:%M:%SZ"), kind="FRAME"
                                )
                                self._confirm_bithumb_feed(session_id, stream, market, data)

                                await self._enqueue(
                                    "bithumb", stream, market, data, recv_ts, exch_ts, recv_monotonic_ns
                                )
                            except Exception as e:
                                m.malformed_quarantined += 1
                                self.storage.quarantine_malformed_record("bithumb", raw_bytes, str(e), recv_ts)
                    finally:
                        hb_task.cancel()
                        await asyncio.gather(hb_task, return_exceptions=True)

            except Exception as e:
                m.disconnect_count += 1
                m.last_reconnect_reason = str(e)
                if session_id is not None:
                    sess = self.session_evidence._sessions.get(session_id)
                    if sess is not None and sess.disconnected_at_utc is None:
                        disc_ts = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
                        self.session_evidence.close_session(session_id, disc_ts, reason=str(e))
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
        combined_url = build_binance_combined_url(self.binance_symbols)
        session_id: str | None = None
        req_counter = 1

        requested_feeds = [
            f"binance/{stream}/{sym.lower()}"
            for sym in self.binance_symbols
            for stream in ("trade", "orderbook")
        ]

        while self.is_running:
            try:
                m.connected_at = time.time()
                now_str = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
                prev_session_id = session_id
                session_id = self.session_evidence.open_session("binance", requested_feeds, now_str)
                if prev_session_id is not None:
                    prev_sess = self.session_evidence._sessions.get(prev_session_id)
                    if prev_sess is not None and prev_sess.reconnect_successor_id is None:
                        prev_sess.reconnect_successor_id = session_id

                async with websockets.connect(combined_url, ping_interval=None) as ws:
                    logger.info(f"[Binance] Connected to {len(self.binance_symbols)} benchmark streams...")
                    backoff = 1.0

                    req_id = req_counter
                    req_counter += 1
                    await self._confirm_binance_subscriptions(ws, session_id, req_id)

                    hb_task = asyncio.create_task(self._heartbeat_loop(ws, "binance", session_id))
                    try:
                        while self.is_running:
                            try:
                                msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                            except asyncio.TimeoutError:
                                logger.warning("[Binance] Connection-level stale stream (30s timeout). Reconnecting...")
                                m.last_reconnect_reason = "connection_stale_30s"
                                m.disconnect_count += 1
                                m.reconnect_count += 1
                                break

                            recv_ts = self._utc_now()
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

                                self.session_evidence.record_heartbeat(
                                    session_id, recv_ts.strftime("%Y-%m-%dT%H:%M:%SZ"), kind="FRAME"
                                )

                                await self._enqueue(
                                    "binance", stream_name, sym, data, recv_ts, exch_ts, recv_monotonic_ns
                                )
                            except Exception as e:
                                m.malformed_quarantined += 1
                                self.storage.quarantine_malformed_record("binance", raw_bytes, str(e), recv_ts)
                    finally:
                        hb_task.cancel()
                        await asyncio.gather(hb_task, return_exceptions=True)

            except Exception as e:
                m.disconnect_count += 1
                m.last_reconnect_reason = str(e)
                if session_id is not None:
                    sess = self.session_evidence._sessions.get(session_id)
                    if sess is not None and sess.disconnected_at_utc is None:
                        disc_ts = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
                        self.session_evidence.close_session(session_id, disc_ts, reason=str(e))
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
        session_id: str | None = None

        requested_feeds = [
            f"upbit/{stream}/{mkt.upper()}"
            for mkt in self.upbit_markets
            for stream in ("orderbook", "trade")
        ]

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
                now_str = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
                prev_session_id = session_id
                session_id = self.session_evidence.open_session("upbit", requested_feeds, now_str)
                if prev_session_id is not None:
                    prev_sess = self.session_evidence._sessions.get(prev_session_id)
                    if prev_sess is not None and prev_sess.reconnect_successor_id is None:
                        prev_sess.reconnect_successor_id = session_id

                async with websockets.connect(UPBIT_WS_URL, ping_interval=None) as ws:
                    logger.info(f"[Upbit] Connected to {len(self.upbit_markets)} benchmark streams...")
                    await ws.send(payload)
                    backoff = 1.0

                    list_ticket = f"upbit_list_{ticket}"
                    await self._confirm_upbit_subscriptions(ws, session_id, list_ticket)

                    hb_task = asyncio.create_task(self._heartbeat_loop(ws, "upbit", session_id))
                    try:
                        while self.is_running:
                            try:
                                msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                            except asyncio.TimeoutError:
                                logger.warning("[Upbit] Connection-level stale stream (30s timeout). Reconnecting...")
                                m.last_reconnect_reason = "connection_stale_30s"
                                m.disconnect_count += 1
                                m.reconnect_count += 1
                                break

                            recv_ts = self._utc_now()
                            recv_monotonic_ns = time.monotonic_ns()
                            raw_bytes = msg if isinstance(msg, bytes) else msg.encode("utf-8")
                            m.total_messages_received += 1
                            m.total_bytes_received += len(raw_bytes)
                            m.last_connection_event_time = time.time()

                            try:
                                stream, market, data, exch_ts = parse_upbit_message(raw_bytes)
                                if stream == "trade":
                                    m.trade_messages += 1
                                else:
                                    m.orderbook_messages += 1

                                self.session_evidence.record_heartbeat(
                                    session_id, recv_ts.strftime("%Y-%m-%dT%H:%M:%SZ"), kind="FRAME"
                                )

                                await self._enqueue(
                                    "upbit", stream, market, data, recv_ts, exch_ts, recv_monotonic_ns
                                )
                            except Exception as e:
                                m.malformed_quarantined += 1
                                self.storage.quarantine_malformed_record("upbit", raw_bytes, str(e), recv_ts)
                    finally:
                        hb_task.cancel()
                        await asyncio.gather(hb_task, return_exceptions=True)

            except Exception as e:
                m.disconnect_count += 1
                m.last_reconnect_reason = str(e)
                if session_id is not None:
                    sess = self.session_evidence._sessions.get(session_id)
                    if sess is not None and sess.disconnected_at_utc is None:
                        disc_ts = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
                        self.session_evidence.close_session(session_id, disc_ts, reason=str(e))
                logger.warning(f"[Upbit] Disconnected: {e}. Backoff {backoff:.1f}s...")
                await asyncio.sleep(backoff + random.uniform(0.1, 0.5))
                backoff = min(30.0, backoff * 2.0)
                m.reconnect_count += 1

    async def run_collector(self, max_duration_seconds: float | None = None) -> None:
        self.is_running = True
        self._accepting_partition_writes = True
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
            self._accepting_partition_writes = False
            writer_task.cancel()
            await asyncio.gather(writer_task, return_exceptions=True)
            metrics_task.cancel()
            await asyncio.gather(metrics_task, return_exceptions=True)

            # Close any open sessions upon shutdown
            now_str = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
            for sid, sess in self.session_evidence._sessions.items():
                if sess.disconnected_at_utc is None:
                    self.session_evidence.close_session(sid, now_str, reason="COLLECTOR_SHUTDOWN")

            # Freeze shutdown tails and finalize pending
            try:
                health = self._get_writer_health_snapshot()
                self.coverage_tracker.freeze_shutdown(self._utc_now(), self.session_evidence, health)
                self.finalizer.finalize_pending()
            except Exception as exc:
                logger.error("Error during shutdown tail freeze/finalization: %s", exc)

            try:
                self._persist_metrics()
            except OSError as error:
                logger.error("Failed to persist final collector metrics: %s", error)
        if self._fatal_writer_error is not None:
            raise RuntimeError(
                "Collector stopped after writer failure; "
                f"unpersisted_events={self._unpersisted_event_count}"
            ) from self._fatal_writer_error

    def finalize_all(self) -> FinalizationSummary:
        health = self._get_writer_health_snapshot()
        self.coverage_tracker.freeze_shutdown(self._utc_now(), self.session_evidence, health)
        return self.finalizer.finalize_pending()

    def generate_all_manifests(self) -> list[dict[str, Any]]:
        self.finalize_all()
        manifests = []
        for p in list(self._all_touched_partition_files):
            if p.exists() and p.stat().st_size > 0:
                stem = p.stem
                mf_path = self.storage.manifest_dir / f"manifest_{stem}.json"
                if mf_path.exists():
                    try:
                        manifests.append(json.loads(mf_path.read_text(encoding="utf-8")))
                    except Exception as e:
                        logger.error(f"Failed to read manifest {mf_path}: {e}")
                else:
                    try:
                        mf = self.storage.generate_partition_manifest(p)
                        manifests.append(mf.to_dict())
                    except Exception as e:
                        logger.error(f"Failed to generate manifest for {p}: {e}")
        return manifests
