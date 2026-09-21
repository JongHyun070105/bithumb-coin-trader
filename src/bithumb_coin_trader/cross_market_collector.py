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
import gc
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
from websockets.exceptions import ConnectionClosed

from bithumb_coin_trader.collector_state_model import (
    CollectorHealth,
    ComponentHealthState,
    CurrentCohortHealth,
    EvidenceHealth,
    LastExceptionInfo,
    ResourceTelemetry,
    RuntimeHealthSnapshot,
    SupervisorHealth,
    WriterHealth,
    compute_exception_hash,
    utc_iso_now,
    write_health_snapshot_atomic,
)
from bithumb_coin_trader.feed_hour_coverage import (
    DataArtifactBinding,
    FeedHourCoverage,
    FeedHourCoverageTracker,
    FrozenFeedHourObservation,
    save_frozen_journal,
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
    last_connection_diagnostic: dict[str, Any] | None = None
    max_event_loop_lag_seconds: float = 0.0

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
            "last_connection_diagnostic": self.last_connection_diagnostic,
            "max_event_loop_lag_seconds": round(self.max_event_loop_lag_seconds, 6),
            "seconds_since_last_connection_event": round(now - self.last_connection_event_time, 2) if self.last_connection_event_time > 0 else None,
        }


@dataclass
class _ReconnectDecision:
    """One decision per physical Bithumb connection, shared by both detectors."""

    event: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str | None = None
    source: str | None = None
    requested_at_monotonic: float | None = None
    requested_at_utc: str | None = None

    def request(self, reason: str, source: str) -> bool:
        if self.event.is_set():
            return False
        self.reason = reason
        self.source = source
        self.requested_at_monotonic = time.monotonic()
        self.requested_at_utc = datetime.now(timezone.utc).isoformat()
        self.event.set()
        return True


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
        health_path: Path | str | None = None,
        runtime_dir: Path | str | None = None,
        health_interval_seconds: float = 10.0,
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
        self.health_interval_seconds = float(health_interval_seconds)

        if health_path is not None:
            self.health_path: Path = Path(health_path)
        elif runtime_dir is not None:
            self.health_path = Path(runtime_dir) / "health" / "latest.json"
        else:
            self.health_path = self.storage.base_dir.parent / "health" / "latest.json"

        self._last_loop_heartbeat: str | None = None
        self._last_canonical_event: str | None = None
        self.last_websocket_activity: dict[str, str | None] = {
            "bithumb": None,
            "binance": None,
            "upbit": None,
        }
        self.websocket_sessions: dict[str, str] = {
            "bithumb": "DISCONNECTED",
            "binance": "DISCONNECTED",
            "upbit": "DISCONNECTED",
        }
        self._last_dequeue_time: str | None = None
        self._last_local_raw_write: str | None = None

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
        self.heartbeat_policy = HeartbeatPolicy(heartbeat_probe_interval_seconds=10, heartbeat_timeout_seconds=25)
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
        self.journals_dir = self.storage.base_dir.parent / "coverage" / "journals"
        self._frozen_observations: list[FrozenFeedHourObservation] = []
        self._bithumb_confirmed_feeds: dict[str, set[str]] = {}
        self._bithumb_expected_feeds: set[str] = set(
            FeedIdentity("bithumb", stream, mkt).canonical
            for mkt in self.bithumb_markets
            for stream in ("orderbook", "trade", "ticker")
        )
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._finalization_lock = asyncio.Lock()

    def _trigger_background_finalization(self, cohort: str) -> asyncio.Task[None]:
        """Trigger incremental manifest finalization for completed cohort in background without blocking event loop."""
        async def _async_finalize() -> None:
            async with self._finalization_lock:
                loop = asyncio.get_running_loop()
                try:
                    summary = await loop.run_in_executor(
                        None,
                        self.finalizer.finalize_cohort,
                        cohort,
                    )
                    logger.info(
                        "Incremental manifest finalization completed in background for cohort %s: "
                        "recomputed=%d, reused=%d, failed=%d, pending=%d",
                        cohort,
                        summary.recomputed_count,
                        summary.reused_count,
                        summary.failed_count,
                        summary.pending_count,
                    )
                except Exception as exc:
                    logger.error("Background finalization failed for cohort %s: %s", cohort, exc, exc_info=True)

        task = asyncio.create_task(_async_finalize(), name=f"bg_finalizer_{cohort}")
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def drain_background_tasks(self, timeout: float = 120.0) -> None:
        """Wait for all active background finalization tasks to complete."""
        if not self._background_tasks:
            return
        logger.info("Draining %d active background finalization task(s)...", len(self._background_tasks))
        try:
            await asyncio.wait_for(
                asyncio.gather(*list(self._background_tasks), return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.error("Timed out waiting for background finalization tasks to complete after %.1fs", timeout)
        except Exception as exc:
            logger.error("Error while draining background finalization tasks: %s", exc)

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
        recv_iso = recv_ts.isoformat()
        self._last_canonical_event = recv_iso
        self.last_websocket_activity[exchange] = recv_iso
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

    def _get_latest_websocket_activity(self) -> str | None:
        valid_ts = [ts for ts in self.last_websocket_activity.values() if ts is not None]
        if not valid_ts:
            return None
        return max(valid_ts)

    def emit_health_snapshot(self) -> RuntimeHealthSnapshot:
        """Atomically emit a RuntimeHealthSnapshot to self.health_path."""
        now_dt = self._utc_now()
        now_iso = now_dt.isoformat()
        self._last_loop_heartbeat = now_iso

        # 1. Collector status
        if self._fatal_writer_error is not None:
            collector_status = ComponentHealthState.FAILED.value
        elif self.is_running:
            active_sessions = [
                self.websocket_sessions.get("bithumb", "DISCONNECTED"),
            ]
            if self.enable_binance:
                active_sessions.append(self.websocket_sessions.get("binance", "DISCONNECTED"))
            if self.enable_upbit:
                active_sessions.append(self.websocket_sessions.get("upbit", "DISCONNECTED"))

            if all(s == "CONNECTED" for s in active_sessions):
                collector_status = ComponentHealthState.HEALTHY.value
            elif any(s == "CONNECTED" for s in active_sessions):
                collector_status = ComponentHealthState.DEGRADED.value
            else:
                collector_status = ComponentHealthState.DEGRADED.value
        else:
            collector_status = ComponentHealthState.UNKNOWN.value

        total_reconnects = sum(m.reconnect_count for m in self.metrics.values())
        fatal_error_str = (
            f"{type(self._fatal_writer_error).__name__}: {self._fatal_writer_error}"
            if self._fatal_writer_error is not None
            else None
        )

        collector_health = CollectorHealth(
            status=collector_status,
            last_loop_heartbeat=self._last_loop_heartbeat,
            last_websocket_activity=self._get_latest_websocket_activity(),
            last_canonical_event=self._last_canonical_event,
            websocket_sessions=dict(self.websocket_sessions),
            reconnect_count=total_reconnects,
            fatal_error=fatal_error_str,
        )

        # 2. Writer status
        if self._fatal_writer_error is not None:
            writer_status = ComponentHealthState.FAILED.value
        elif self.is_running:
            if self._write_queue.full():
                writer_status = ComponentHealthState.DEGRADED.value
            else:
                writer_status = ComponentHealthState.HEALTHY.value
        else:
            writer_status = ComponentHealthState.UNKNOWN.value

        total_writer_errors = sum(m.writer_errors for m in self.metrics.values())
        writer_health = WriterHealth(
            status=writer_status,
            queue_depth=self._write_queue.qsize(),
            max_queue_depth=self._write_queue.maxsize,
            last_dequeue=self._last_dequeue_time,
            last_local_raw_write=self._last_local_raw_write,
            current_open_raw_count=len(self._current_active_partition_files()),
            unpersisted_count=self._unpersisted_event_count,
            writer_errors=total_writer_errors,
        )

        # 3. Evidence & Current cohort
        evidence_health = EvidenceHealth(
            status=ComponentHealthState.HEALTHY.value if self.is_running else ComponentHealthState.UNKNOWN.value,
            current_hour_expected_slots=len(self.configured_feeds),
            current_hour_terminal_slots=len(self._frozen_observations),
        )

        current_cohort = CurrentCohortHealth(
            utc_hour=now_dt.strftime("%Y-%m-%d_%H"),
            observed_feed_count=len(self._latest_partition_by_feed),
            expected_feed_count=len(self.configured_feeds),
        )

        # 4. Resources
        rss_bytes = 0
        try:
            import resource
            import sys
            ru = resource.getrusage(resource.RUSAGE_SELF)
            if sys.platform == "darwin":
                rss_bytes = int(ru.ru_maxrss)
            else:
                rss_bytes = int(ru.ru_maxrss * 1024)
        except Exception:
            pass

        disk_free = 0
        disk_used = 0
        try:
            import shutil
            usage = shutil.disk_usage(self.storage.base_dir)
            disk_free = int(usage.free)
            disk_used = int(usage.used)
        except Exception:
            pass

        fd_count = 0
        try:
            proc_fd_dir = f"/proc/{os.getpid()}/fd"
            if os.path.isdir(proc_fd_dir):
                fd_count = len(os.listdir(proc_fd_dir))
            else:
                import resource as _resource
                fd_count = _resource.getrlimit(_resource.RLIMIT_NOFILE)[0]
        except Exception:
            pass

        resources = ResourceTelemetry(
            rss_bytes=rss_bytes,
            fd_count=fd_count,
            disk_free_bytes=disk_free,
            disk_used_bytes=disk_used,
        )

        # 5. Last Exception
        last_exception = LastExceptionInfo()
        if self._fatal_writer_error is not None:
            last_exception = LastExceptionInfo(
                component="writer",
                type=type(self._fatal_writer_error).__name__,
                message_hash=compute_exception_hash(str(self._fatal_writer_error)),
                timestamp=now_iso,
            )

        snapshot = RuntimeHealthSnapshot(
            schema_version=1,
            epoch=self.collector_epoch,
            run_id=self._collector_run_id,
            software_sha=self.collector_git_commit,
            config_fingerprint=self.collector_config_fingerprint,
            observed_at=now_iso,
            supervisor=SupervisorHealth(
                pid=os.getpid(),
                process_start=self._collector_started_at,
                active_state="active" if self.is_running else "inactive",
            ),
            collector=collector_health,
            writer=writer_health,
            evidence=evidence_health,
            resources=resources,
            current_cohort=current_cohort,
            last_exception=last_exception,
        )

        if self.health_path is not None:
            write_health_snapshot_atomic(self.health_path, snapshot)

        return snapshot

    async def _health_worker(self) -> None:
        loop = asyncio.get_event_loop()
        while self.is_running or not self._write_queue.empty():
            try:
                await loop.run_in_executor(None, self.emit_health_snapshot)
            except Exception as error:
                logger.error("Failed to emit health snapshot: %s", error)
            # systemd watchdog notification (best-effort, no-op if not running under systemd)
            try:
                import systemd.daemon  # pyright: ignore[reportMissingImports]
                systemd.daemon.notify("WATCHDOG=1")
            except Exception:
                pass
            try:
                from bithumb_coin_trader.bounded_supervisor import sd_notify

                sd_notify("WATCHDOG=1")
            except Exception:
                pass
            try:
                await asyncio.sleep(self.health_interval_seconds)
            except asyncio.CancelledError:
                break
        try:
            self.emit_health_snapshot()
        except Exception as error:
            logger.error("Failed to emit final health snapshot: %s", error)

    async def _loop_lag_worker(self) -> None:
        """Sample process-wide scheduling lag without logging on every tick."""
        interval = 1.0
        expected = time.monotonic() + interval
        while self.is_running:
            await asyncio.sleep(max(0.0, expected - time.monotonic()))
            lag = max(0.0, time.monotonic() - expected)
            for metric in self.metrics.values():
                metric.max_event_loop_lag_seconds = max(metric.max_event_loop_lag_seconds, lag)
            expected = time.monotonic() + interval

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
        self._last_dequeue_time = write_ts.isoformat()

        cohort_utc = write_ts.strftime("%Y-%m-%d_%H")

        # Cohort boundary fence: when writer detects cohort crossing, freeze and finalize
        if self._current_writer_cohort is not None and cohort_utc != self._current_writer_cohort:
            completed_cohort = self._current_writer_cohort
            boundary_dt = write_ts.replace(minute=0, second=0, microsecond=0)
            health = self._get_writer_health_snapshot()
            obs_seq = self.coverage_tracker.freeze_completed(boundary_dt, self.session_evidence, health)
            if obs_seq:
                save_frozen_journal(obs_seq, self.journals_dir)
                self._frozen_observations.extend(obs_seq)
            self._trigger_background_finalization(completed_cohort)
            # Prune heartbeats older than boundary_dt to keep collector memory strictly bounded
            boundary_utc_str = boundary_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            self.session_evidence.prune_older_than(boundary_utc_str)
            gc.collect()

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
        self._last_local_raw_write = write_ts.isoformat()

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
        feed_str = f"bithumb/{stream.lower()}/{market.upper()}"
        if feed_str not in self._bithumb_expected_feeds:
            return
        if session_id not in self._bithumb_confirmed_feeds:
            self._bithumb_confirmed_feeds[session_id] = set()
        if feed_str in self._bithumb_confirmed_feeds[session_id]:
            return
        self._bithumb_confirmed_feeds[session_id].add(feed_str)
        now_utc = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.session_evidence.confirm(
            session_id=session_id,
            confirmed=sorted(self._bithumb_confirmed_feeds[session_id]),
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

        start_time = time.monotonic()
        timeout = 10.0
        confirmed_data: dict[str, Any] | None = None

        while (time.monotonic() - start_time) < timeout:
            remaining = max(0.1, timeout - (time.monotonic() - start_time))
            msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
            raw_bytes = msg if isinstance(msg, bytes) else msg.encode("utf-8")
            try:
                data = json.loads(raw_bytes.decode("utf-8"))
            except Exception:
                continue

            if isinstance(data, dict) and (
                data.get("id") == request_id
                or ("result" in data and "stream" not in data and "e" not in data)
            ):
                confirmed_data = data
                break

            # Interim market data frame
            try:
                stream_name, sym, d, exch_ts = parse_binance_message(raw_bytes)
                recv_ts = self._utc_now()
                recv_monotonic_ns = time.monotonic_ns()
                self.session_evidence.record_heartbeat(
                    session_id, recv_ts.strftime("%Y-%m-%dT%H:%M:%SZ"), kind="FRAME"
                )
                await self._enqueue(
                    "binance", stream_name, sym, d, recv_ts, exch_ts, recv_monotonic_ns
                )
            except Exception:
                pass

        if confirmed_data is None:
            raise TimeoutError("Binance subscription confirmation timed out")

        data = confirmed_data
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

        start_time = time.monotonic()
        timeout = 10.0
        confirmed_data: dict[str, Any] | None = None

        while (time.monotonic() - start_time) < timeout:
            remaining = max(0.1, timeout - (time.monotonic() - start_time))
            msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
            raw_bytes = msg if isinstance(msg, bytes) else msg.encode("utf-8")
            try:
                data = json.loads(raw_bytes.decode("utf-8"))
            except Exception:
                continue

            if isinstance(data, dict) and (
                data.get("ticket") == ticket
                or data.get("method") == "LIST_SUBSCRIPTIONS"
                or data.get("type") == "LIST_SUBSCRIPTIONS"
                or ("result" in data and data.get("type") not in ("trade", "orderbook", "ticker"))
            ):
                confirmed_data = data
                break

            # Interim market data frame
            try:
                stream, market, d, exch_ts = parse_upbit_message(raw_bytes)
                recv_ts = self._utc_now()
                recv_monotonic_ns = time.monotonic_ns()
                self.session_evidence.record_heartbeat(
                    session_id, recv_ts.strftime("%Y-%m-%dT%H:%M:%SZ"), kind="FRAME"
                )
                await self._enqueue(
                    "upbit", stream, market, d, recv_ts, exch_ts, recv_monotonic_ns
                )
            except Exception:
                pass

        if confirmed_data is None:
            raise TimeoutError("Upbit subscription confirmation timed out")

        data = confirmed_data
        result_items = data.get("result")
        if not isinstance(result_items, list):
            raise ValueError("SUBSCRIPTION_SET_MISMATCH: Upbit result is not a list")

        returned_feeds: set[str] = set()
        for item in result_items:
            if isinstance(item, dict):
                st = item.get("type", "").lower()
                codes = item.get("codes", [])
                if isinstance(codes, list):
                    for c in codes:
                        returned_feeds.add(f"upbit/{st}/{c.upper()}")

        expected_feeds = set(
            f"upbit/{st}/{mkt.upper()}"
            for mkt in self.upbit_markets
            for st in ("orderbook", "trade")
        )
        if returned_feeds != expected_feeds:
            raise ValueError("SUBSCRIPTION_SET_MISMATCH")

        now_utc = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.session_evidence.confirm(
            session_id=session_id,
            confirmed=sorted(expected_feeds),
            method="LIST_SUBSCRIPTIONS",
            confirmed_at_utc=now_utc,
            response=data,
        )

    async def _heartbeat_loop(
        self,
        ws: Any,
        exchange: str,
        session_id: str,
        reconnect_decision: _ReconnectDecision | None = None,
        last_frame_monotonic: Callable[[], float | None] | None = None,
        on_ping: Callable[[float], None] | None = None,
        on_pong: Callable[[float], None] | None = None,
    ) -> None:
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
                    if on_ping is not None:
                        on_ping(time.monotonic())
                    timeout_val = float(timeout) if timeout > 0 else 0.001
                    await asyncio.wait_for(pong_waiter, timeout=timeout_val)
                    if on_pong is not None:
                        on_pong(time.monotonic())
                    now_utc = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
                    self.last_websocket_activity[exchange] = self._utc_now().isoformat()
                    self.session_evidence.record_heartbeat(session_id, now_utc, kind="PING_PONG")
                except (asyncio.TimeoutError, TimeoutError):
                    now_ts = self._utc_now()
                    now_utc = now_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
                    is_active = False
                    if last_frame_monotonic is not None:
                        last_frame = last_frame_monotonic()
                        is_active = last_frame is not None and time.monotonic() - last_frame < timeout
                    else:
                        last_act_str = self.last_websocket_activity.get(exchange)
                        try:
                            if last_act_str:
                                last_act = datetime.fromisoformat(last_act_str)
                                is_active = (now_ts - last_act).total_seconds() < timeout
                        except Exception:
                            pass
                    if is_active:
                        logger.info(
                            "[%s] Heartbeat ping timed out but data frames are actively arriving; retaining session %s",
                            exchange.capitalize(),
                            session_id,
                        )
                        continue

                    logger.warning("[%s] Heartbeat timeout on session %s", exchange.capitalize(), session_id)
                    if reconnect_decision is not None:
                        reconnect_decision.request("HEARTBEAT_TIMEOUT", "HEARTBEAT")
                        break
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
            if reconnect_decision is not None:
                reconnect_decision.request("HEARTBEAT_EXCEPTION", "HEARTBEAT")

    # -------------------------------------------------------------------------
    # Bithumb WebSocket Loop
    # -------------------------------------------------------------------------
    async def _bithumb_loop(self) -> None:
        m = self.metrics["bithumb"]
        backoff = 1.0
        previous_session_id: str | None = None

        requested_feeds = [
            f"bithumb/{stream}/{mkt.upper()}"
            for mkt in self.bithumb_markets
            for stream in ("orderbook", "trade", "ticker")
        ]

        try:
            while self.is_running:
                self.websocket_sessions["bithumb"] = "RECONNECTING"
                connection_id = uuid.uuid4().hex
                attempt_started = time.monotonic()
                attempt_utc = self._utc_now().isoformat()
                decision = _ReconnectDecision()
                session_id: str | None = None
                connected_at: float | None = None
                subscribed_at: float | None = None
                first_frame_at: float | None = None
                last_frame_at: float | None = None
                last_ping_at: float | None = None
                last_pong_at: float | None = None
                close_elapsed: float | None = None
                close_timed_out = False
                exception_type: str | None = None
                exception_repr: str | None = None
                close_code: int | None = None
                close_reason: str | None = None
                peer: str | None = None
                ticket = f"bithumb_v9_{uuid.uuid4().hex[:8]}"
                payload = json.dumps([
                    {"ticket": ticket},
                    {"type": "orderbook", "codes": self.bithumb_markets},
                    {"type": "trade", "codes": self.bithumb_markets},
                    {"type": "ticker", "codes": self.bithumb_markets},
                    {"format": "DEFAULT"},
                ])
                connection = websockets.connect(BITHUMB_WS_URL, ping_interval=None, close_timeout=1.0)
                ws: Any = None
                hb_task: asyncio.Task[None] | None = None
                try:
                    ws = await connection.__aenter__()
                    connected_at = time.monotonic()
                    m.connected_at = time.time()
                    now_str = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
                    session_id = self.session_evidence.open_session("bithumb", requested_feeds, now_str)
                    if previous_session_id is not None:
                        prev_sess = self.session_evidence._sessions.get(previous_session_id)
                        if prev_sess is not None and prev_sess.reconnect_successor_id is None:
                            prev_sess.reconnect_successor_id = session_id
                    previous_session_id = session_id
                    transport = getattr(ws, "transport", None)
                    if transport is not None:
                        endpoint = transport.get_extra_info("peername")
                        peer = str(endpoint) if endpoint is not None else None

                    if self.is_running:
                        self.websocket_sessions["bithumb"] = "CONNECTED"
                        logger.info(f"[Bithumb] Connected. Subscribing {len(self.bithumb_markets)} markets...")
                        await ws.send(payload)
                        subscribed_at = time.monotonic()
                        backoff = 1.0
                        def set_ping(value: float) -> None:
                            nonlocal last_ping_at
                            last_ping_at = value

                        def set_pong(value: float) -> None:
                            nonlocal last_pong_at
                            last_pong_at = value

                        hb_task = asyncio.create_task(
                            self._heartbeat_loop(
                                ws, "bithumb", session_id, decision,
                                lambda: last_frame_at, set_ping, set_pong,
                            )
                        )
                        reconnect_task = asyncio.create_task(decision.event.wait())
                        try:
                            while self.is_running and not decision.event.is_set():
                                recv_task = asyncio.create_task(ws.recv())
                                try:
                                    done, _ = await asyncio.wait(
                                        {recv_task, reconnect_task}, timeout=30.0,
                                        return_when=asyncio.FIRST_COMPLETED,
                                    )
                                finally:
                                    if not recv_task.done():
                                        recv_task.cancel()
                                    await asyncio.gather(recv_task, return_exceptions=True)
                                if not done:
                                    decision.request("connection_stale_30s", "FRAME_STALE")
                                    logger.warning("[Bithumb] Connection-level stale stream (30s timeout). Reconnecting...")
                                    break
                                if decision.event.is_set() and recv_task not in done:
                                    break
                                msg = recv_task.result()
                                recv_ts = self._utc_now()
                                recv_monotonic_ns = time.monotonic_ns()
                                last_frame_at = recv_monotonic_ns / 1_000_000_000
                                if first_frame_at is None:
                                    first_frame_at = last_frame_at
                                self.last_websocket_activity["bithumb"] = recv_ts.isoformat()
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
                            reconnect_task.cancel()
                            await asyncio.gather(reconnect_task, return_exceptions=True)

                except Exception as e:
                    exception_type = type(e).__name__
                    exception_repr = repr(e)[:512]
                    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
                        reason = "connection_stale_30s"
                    elif isinstance(e, ConnectionClosed):
                        reason = "SERVER_CLOSE"
                    else:
                        reason = "SOCKET_EXCEPTION"
                    decision.request(reason, "RECV" if ws is not None else "CONNECT")
                    logger.warning("[Bithumb] Connection %s ended: %s", connection_id, exception_repr)
                finally:
                    self.websocket_sessions["bithumb"] = "DISCONNECTED"
                    if hb_task is not None:
                        hb_task.cancel()
                        await asyncio.gather(hb_task, return_exceptions=True)
                    if ws is not None:
                        close_code = getattr(ws, "close_code", None)
                        close_reason = getattr(ws, "close_reason", None)
                        close_started = time.monotonic()
                        try:
                            await asyncio.wait_for(connection.__aexit__(None, None, None), timeout=2.0)
                        except asyncio.TimeoutError:
                            close_timed_out = True
                            transport = getattr(ws, "transport", None)
                            if transport is not None:
                                transport.abort()
                        except Exception as e:
                            exception_type = exception_type or type(e).__name__
                            exception_repr = exception_repr or repr(e)[:512]
                        close_elapsed = time.monotonic() - close_started
                    if decision.event.is_set():
                        m.disconnect_count += 1
                        m.last_reconnect_reason = decision.reason or "UNKNOWN"
                        if self.is_running:
                            m.reconnect_count += 1
                    if session_id is not None:
                        sess = self.session_evidence._sessions.get(session_id)
                        if sess is not None and sess.disconnected_at_utc is None:
                            disc_ts = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
                            self.session_evidence.close_session(
                                session_id, disc_ts, reason=decision.reason or "COLLECTOR_SHUTDOWN"
                            )
                    observed_at = decision.requested_at_monotonic or time.monotonic()
                    diagnostic = {
                        "schema_version": 1,
                        "connection_id": connection_id,
                        "attempt_started_utc": attempt_utc,
                        "connect_seconds": connected_at - attempt_started if connected_at is not None else None,
                        "subscribe_seconds": subscribed_at - connected_at if subscribed_at is not None and connected_at is not None else None,
                        "first_frame_seconds": first_frame_at - subscribed_at if first_frame_at is not None and subscribed_at is not None else None,
                        "last_frame_age_seconds_at_decision": max(0.0, observed_at - last_frame_at) if last_frame_at is not None else None,
                        "last_ping_age_seconds_at_decision": max(0.0, observed_at - last_ping_at) if last_ping_at is not None else None,
                        "last_pong_age_seconds_at_decision": max(0.0, observed_at - last_pong_at) if last_pong_at is not None else None,
                        "reconnect_reason": decision.reason,
                        "trigger_source": decision.source,
                        "reconnect_requested_utc": decision.requested_at_utc,
                        "close_seconds": close_elapsed,
                        "decision_to_teardown_seconds": (
                            time.monotonic() - decision.requested_at_monotonic
                            if decision.requested_at_monotonic is not None else None
                        ),
                        "close_timed_out": close_timed_out,
                        "close_code": close_code,
                        "close_reason": close_reason,
                        "exception_type": exception_type,
                        "exception_repr": exception_repr,
                        "peer": peer,
                        "queue_depth": self._write_queue.qsize(),
                        "writer_errors": m.writer_errors,
                        "other_exchange_activity": {
                            key: self.last_websocket_activity[key] for key in ("binance", "upbit")
                        },
                        "max_event_loop_lag_seconds": m.max_event_loop_lag_seconds,
                    }
                    if decision.event.is_set():
                        m.last_connection_diagnostic = diagnostic
                        logger.warning("[Bithumb] reconnect diagnostic %s", json.dumps(diagnostic, sort_keys=True))
                if self.is_running and decision.event.is_set():
                    self.websocket_sessions["bithumb"] = "RECONNECTING"
                    await asyncio.sleep(backoff + random.uniform(0.1, 0.5))
                    backoff = min(30.0, backoff * 2.0)
        finally:
            self.websocket_sessions["bithumb"] = "DISCONNECTED"

    # -------------------------------------------------------------------------
    # Binance WebSocket Loop
    # -------------------------------------------------------------------------
    async def _binance_loop(self) -> None:
        m = self.metrics["binance"]
        if not self.enable_binance or not self.binance_symbols:
            self.websocket_sessions["binance"] = "DISCONNECTED"
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

        try:
            while self.is_running:
                self.websocket_sessions["binance"] = "RECONNECTING"
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
                        self.websocket_sessions["binance"] = "CONNECTED"
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
                                    self.websocket_sessions["binance"] = "DISCONNECTED"
                                    now_utc = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
                                    if session_id is not None:
                                        self.session_evidence.close_session(session_id, now_utc, reason="connection_stale_30s")
                                    break

                                recv_ts = self._utc_now()
                                recv_monotonic_ns = time.monotonic_ns()
                                self.last_websocket_activity["binance"] = recv_ts.isoformat()
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
                            self.websocket_sessions["binance"] = "DISCONNECTED"
                            hb_task.cancel()
                            await asyncio.gather(hb_task, return_exceptions=True)

                except Exception as e:
                    self.websocket_sessions["binance"] = "DISCONNECTED"
                    m.disconnect_count += 1
                    m.last_reconnect_reason = str(e)
                    if session_id is not None:
                        sess = self.session_evidence._sessions.get(session_id)
                        if sess is not None and sess.disconnected_at_utc is None:
                            disc_ts = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
                            self.session_evidence.close_session(session_id, disc_ts, reason=str(e))
                    logger.warning(f"[Binance] Disconnected: {e}. Backoff {backoff:.1f}s...")
                    self.websocket_sessions["binance"] = "RECONNECTING"
                    await asyncio.sleep(backoff + random.uniform(0.1, 0.5))
                    backoff = min(30.0, backoff * 2.0)
                    m.reconnect_count += 1
        finally:
            self.websocket_sessions["binance"] = "DISCONNECTED"

    # -------------------------------------------------------------------------
    # Upbit WebSocket Loop
    # -------------------------------------------------------------------------
    async def _upbit_loop(self) -> None:
        m = self.metrics["upbit"]
        if not self.enable_upbit or not self.upbit_markets:
            self.websocket_sessions["upbit"] = "DISCONNECTED"
            return

        backoff = 1.0
        session_id: str | None = None

        requested_feeds = [
            f"upbit/{stream}/{mkt.upper()}"
            for mkt in self.upbit_markets
            for stream in ("orderbook", "trade")
        ]

        try:
            while self.is_running:
                self.websocket_sessions["upbit"] = "RECONNECTING"
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
                        self.websocket_sessions["upbit"] = "CONNECTED"
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
                                    self.websocket_sessions["upbit"] = "DISCONNECTED"
                                    now_utc = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
                                    if session_id is not None:
                                        self.session_evidence.close_session(session_id, now_utc, reason="connection_stale_30s")
                                    break

                                recv_ts = self._utc_now()
                                recv_monotonic_ns = time.monotonic_ns()
                                self.last_websocket_activity["upbit"] = recv_ts.isoformat()
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
                            self.websocket_sessions["upbit"] = "DISCONNECTED"
                            hb_task.cancel()
                            await asyncio.gather(hb_task, return_exceptions=True)

                except Exception as e:
                    self.websocket_sessions["upbit"] = "DISCONNECTED"
                    m.disconnect_count += 1
                    m.last_reconnect_reason = str(e)
                    if session_id is not None:
                        sess = self.session_evidence._sessions.get(session_id)
                        if sess is not None and sess.disconnected_at_utc is None:
                            disc_ts = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
                            self.session_evidence.close_session(session_id, disc_ts, reason=str(e))
                    logger.warning(f"[Upbit] Disconnected: {e}. Backoff {backoff:.1f}s...")
                    self.websocket_sessions["upbit"] = "RECONNECTING"
                    await asyncio.sleep(backoff + random.uniform(0.1, 0.5))
                    backoff = min(30.0, backoff * 2.0)
                    m.reconnect_count += 1
        finally:
            self.websocket_sessions["upbit"] = "DISCONNECTED"

    async def run_collector(self, max_duration_seconds: float | None = None) -> None:
        # Notify systemd that the service is ready (required for Type=notify)
        try:
            import systemd.daemon  # pyright: ignore[reportMissingImports]
            systemd.daemon.notify("READY=1")
        except Exception:
            pass
        from bithumb_coin_trader.bounded_supervisor import sd_notify

        sd_notify("READY=1")
        self.is_running = True
        self._accepting_partition_writes = True
        writer_task = asyncio.create_task(self._writer_worker())
        metrics_task = asyncio.create_task(self._metrics_worker())
        health_task = asyncio.create_task(self._health_worker())
        lag_task = asyncio.create_task(self._loop_lag_worker())
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
            health_task.cancel()
            await asyncio.gather(health_task, return_exceptions=True)
            lag_task.cancel()
            await asyncio.gather(lag_task, return_exceptions=True)

            for exch in self.websocket_sessions:
                self.websocket_sessions[exch] = "DISCONNECTED"

            # Close any open sessions upon shutdown
            now_str = self._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
            for sid, sess in self.session_evidence._sessions.items():
                if sess.disconnected_at_utc is None:
                    self.session_evidence.close_session(sid, now_str, reason="COLLECTOR_SHUTDOWN")

            # Drain any background finalization tasks before tail shutdown finalization
            await self.drain_background_tasks()

            # Freeze shutdown tails and finalize pending
            try:
                health = self._get_writer_health_snapshot()
                obs_seq = self.coverage_tracker.freeze_shutdown(self._utc_now(), self.session_evidence, health)
                if obs_seq:
                    by_cohort: dict[str, list[FrozenFeedHourObservation]] = {}
                    for obs in obs_seq:
                        by_cohort.setdefault(obs.cohort_utc, []).append(obs)
                    for c_obs in by_cohort.values():
                        save_frozen_journal(c_obs, self.journals_dir)
                    self._frozen_observations.extend(obs_seq)
                self.finalizer.finalize_pending()
            except Exception as exc:
                logger.error("Error during shutdown tail freeze/finalization: %s", exc)

            try:
                self._persist_metrics()
            except OSError as error:
                logger.error("Failed to persist final collector metrics: %s", error)

            try:
                self.emit_health_snapshot()
            except Exception as error:
                logger.error("Failed to emit final health snapshot: %s", error)
        if self._fatal_writer_error is not None:
            raise RuntimeError(
                "Collector stopped after writer failure; "
                f"unpersisted_events={self._unpersisted_event_count}"
            ) from self._fatal_writer_error

    def finalize_all(self) -> FinalizationSummary:
        health = self._get_writer_health_snapshot()
        obs_seq = self.coverage_tracker.freeze_shutdown(self._utc_now(), self.session_evidence, health)
        if obs_seq:
            by_cohort: dict[str, list[FrozenFeedHourObservation]] = {}
            for obs in obs_seq:
                by_cohort.setdefault(obs.cohort_utc, []).append(obs)
            for c_obs in by_cohort.values():
                save_frozen_journal(c_obs, self.journals_dir)
            self._frozen_observations.extend(obs_seq)
        return self.finalizer.finalize_pending()

    def get_frozen_observations(self) -> tuple[FrozenFeedHourObservation, ...]:
        return tuple(self._frozen_observations)

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
