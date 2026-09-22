"""Local fault injection for the Bithumb connection owner; no network or cloud calls."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import time
from typing import Any
from unittest.mock import patch

import pytest
from websockets.exceptions import ConnectionClosedError

from bithumb_coin_trader.cross_market_collector import (
    MultiExchangeMicrostructureCollector,
    _ReconnectDecision,
)
from bithumb_coin_trader.bithumb_redundancy import BithumbRedundancyFilter
from bithumb_coin_trader.feed_hour_coverage import FrozenFeedHourObservation, materialize_feed_hour_coverage
from bithumb_coin_trader.session_evidence import (
    FeedIdentity, HeartbeatPolicy, SessionSegment, WriterHealthSnapshot,
)


class FaultTransport:
    def __init__(self) -> None:
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True

    def get_extra_info(self, _: str) -> None:
        return None


class FaultSocket:
    def __init__(self, frames: list[str] | None = None, error: Exception | None = None) -> None:
        self.frames = list(frames or [])
        self.error = error or TimeoutError()
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self.close_calls = 0
        self.sent: list[str] = []
        self.on_exhausted: Any = None
        self.hang_close = False
        self.hang_recv = False
        self.close_error: Exception | None = None
        self.transport = FaultTransport()

    async def __aenter__(self) -> FaultSocket:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        if self.frames:
            return self.frames.pop(0)
        if self.on_exhausted is not None:
            self.on_exhausted()
        if self.hang_recv:
            await asyncio.Future()
        raise self.error

    async def ping(self) -> asyncio.Future[None]:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        future.set_result(None)
        return future

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error
        if self.hang_close:
            await asyncio.Future()


def make_collector(tmp: str) -> MultiExchangeMicrostructureCollector:
    return MultiExchangeMicrostructureCollector(
        ["KRW-BTC"], storage_base_dir=Path(tmp) / "raw",
        enable_binance=False, enable_upbit=False,
    )


def run_one_socket(socket: FaultSocket) -> tuple[MultiExchangeMicrostructureCollector, float]:
    async def run(tmp: str) -> tuple[MultiExchangeMicrostructureCollector, float]:
        collector = make_collector(tmp)
        socket.on_exhausted = lambda: setattr(collector, "is_running", False)
        start = time.monotonic()
        with patch("websockets.connect", return_value=socket):
            collector.is_running = True
            await collector._bithumb_loop()
        return collector, time.monotonic() - start

    with tempfile.TemporaryDirectory() as tmp:
        return asyncio.run(run(tmp))


def test_replay_fixture_distinguishes_four_historical_intervals() -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures/bithumb_gap_20260919.json").read_text())
    parse = datetime.fromisoformat
    receive_gap = (parse(fixture["first_receive_utc"]) - parse(fixture["last_receive_utc"])).total_seconds()
    monotonic_gap = (
        fixture["first_receive_monotonic_ns"] - fixture["last_receive_monotonic_ns"]
    ) / 1_000_000_000
    persistence_gap = (parse(fixture["first_write_utc"]) - parse(fixture["last_write_utc"])).total_seconds()
    disconnected = (
        parse(fixture["session_reconnected_utc_second"].replace("Z", "+00:00"))
        - parse(fixture["session_disconnected_utc_second"].replace("Z", "+00:00"))
    ).total_seconds()
    assert receive_gap == pytest.approx(40.396224)
    assert monotonic_gap == pytest.approx(40.396223469)
    assert persistence_gap == pytest.approx(40.396373)
    assert disconnected == 10
    assert fixture["binance_btc_orderbook_count_122427_to_122506"] == 390
    assert fixture["upbit_btc_orderbook_count_122427_to_122506"] == 246
    assert fixture["unknown_stages"]  # no invented exact reconnect timeline
    replay = fixture["replay_model"]
    assert replay["historical_old"]["logical_feed_gap_seconds"] == pytest.approx(receive_gap)
    assert replay["historical_old"]["teardown_latency_seconds"] is None
    assert replay["pr13_single_socket"]["single_connection_failure_causes_logical_gap"] is True
    assert replay["active_active_synthetic"]["logical_feed_gap_seconds"] == 0
    assert replay["active_active_synthetic"]["redundant_frames_recovered"] == 40
    assert replay["active_active_synthetic"]["dual_connection_failure_causes_logical_gap"] is True


def test_reconnect_decision_is_first_wins_even_when_detectors_race() -> None:
    decision = _ReconnectDecision()
    assert decision.request("HEARTBEAT_TIMEOUT", "HEARTBEAT")
    assert not decision.request("connection_stale_30s", "FRAME_STALE")
    assert decision.reason == "HEARTBEAT_TIMEOUT"
    assert decision.source == "HEARTBEAT"
    assert decision.event.is_set()


def test_normal_pong_and_active_frames_do_not_request_reconnect() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            collector = make_collector(tmp)
            socket = FaultSocket()
            decision = _ReconnectDecision()
            collector.heartbeat_policy = HeartbeatPolicy(0, 0.01)
            sid = collector.session_evidence.open_session("bithumb", ["bithumb/orderbook/KRW-BTC"], "2026-09-19T12:00:00Z")
            pongs: list[float] = []
            collector.is_running = True
            def record_pong(value: float) -> None:
                pongs.append(value)
                collector.is_running = False
            await collector._heartbeat_loop(socket, "bithumb", sid, decision, lambda: time.monotonic(), on_pong=record_pong)
            assert not decision.event.is_set()
            assert pongs
    asyncio.run(scenario())


def test_pong_timeout_with_fresh_frames_keeps_session() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            collector = make_collector(tmp)
            socket = FaultSocket()
            decision = _ReconnectDecision()
            collector.heartbeat_policy = HeartbeatPolicy(0, 0.001)
            sid = collector.session_evidence.open_session("bithumb", ["bithumb/orderbook/KRW-BTC"], "2026-09-19T12:00:00Z")
            async def unresolved_ping() -> asyncio.Future[None]:
                future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
                collector.is_running = False
                return future
            socket.ping = unresolved_ping  # type: ignore[method-assign]
            collector.is_running = True
            await collector._heartbeat_loop(socket, "bithumb", sid, decision, lambda: time.monotonic())
            assert not decision.event.is_set()
            assert collector.session_evidence._sessions[sid].disconnected_at_utc is None
    asyncio.run(scenario())


def test_pong_and_frames_stop_requests_one_reconnect() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            collector = make_collector(tmp)
            socket = FaultSocket()
            decision = _ReconnectDecision()
            collector.heartbeat_policy = HeartbeatPolicy(0, 0.001)
            sid = collector.session_evidence.open_session("bithumb", ["bithumb/orderbook/KRW-BTC"], "2026-09-19T12:00:00Z")
            async def unresolved_ping() -> asyncio.Future[None]:
                return asyncio.get_running_loop().create_future()
            socket.ping = unresolved_ping  # type: ignore[method-assign]
            collector.is_running = True
            await collector._heartbeat_loop(socket, "bithumb", sid, decision, lambda: time.monotonic() - 60)
            assert decision.reason == "HEARTBEAT_TIMEOUT"
            assert decision.event.is_set()
            assert socket.close_calls == 0  # owner closes it
    asyncio.run(scenario())


def test_ping_exception_requests_reconnect() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            collector = make_collector(tmp)
            socket = FaultSocket()
            decision = _ReconnectDecision()
            collector.heartbeat_policy = HeartbeatPolicy(0, 1)
            sid = collector.session_evidence.open_session("bithumb", ["bithumb/orderbook/KRW-BTC"], "2026-09-19T12:00:00Z")
            async def failed_ping() -> asyncio.Future[None]:
                raise OSError("ping write failed")
            socket.ping = failed_ping  # type: ignore[method-assign]
            collector.is_running = True
            await collector._heartbeat_loop(socket, "bithumb", sid, decision, lambda: None)
            assert decision.reason == "HEARTBEAT_EXCEPTION"
            assert decision.source == "HEARTBEAT"
    asyncio.run(scenario())


@pytest.mark.parametrize("error,expected", [
    (TimeoutError(), "connection_stale_30s"),
    (OSError("tcp reset"), "SOCKET_EXCEPTION"),
    (ConnectionClosedError(None, None), "SERVER_CLOSE"),
])
def test_receive_failures_have_one_owner_and_diagnostic(error: Exception, expected: str) -> None:
    socket = FaultSocket(error=error)
    socket.close_code = 1011
    socket.close_reason = "server unavailable"
    collector, _ = run_one_socket(socket)
    metric = collector.metrics["bithumb"]
    assert metric.disconnect_count == 1
    assert metric.last_reconnect_reason == expected
    assert metric.last_connection_diagnostic is not None
    assert metric.last_connection_diagnostic["trigger_source"] == "RECV"
    assert metric.last_connection_diagnostic["close_code"] == 1011
    assert metric.last_connection_diagnostic["close_reason"] == "server unavailable"
    assert metric.last_connection_diagnostic["decision_to_teardown_seconds"] is not None
    assert metric.last_connection_diagnostic["reconnect_requested_utc"]
    assert socket.close_calls == 1
    assert list(collector.session_evidence._sessions.values())[0].disconnect_reason == expected


def test_hanging_close_is_bounded() -> None:
    socket = FaultSocket()
    socket.hang_close = True
    collector, elapsed = run_one_socket(socket)
    assert elapsed < 3.0
    diagnostic = collector.metrics["bithumb"].last_connection_diagnostic
    assert diagnostic is not None
    assert diagnostic["close_timed_out"] is True


def test_heartbeat_interrupts_blocked_receive_with_one_teardown() -> None:
    async def scenario(tmp: str) -> MultiExchangeMicrostructureCollector:
        collector = make_collector(tmp)
        collector.heartbeat_policy = HeartbeatPolicy(0, 0.001)
        socket = FaultSocket()
        socket.hang_recv = True
        async def no_pong() -> asyncio.Future[None]:
            return asyncio.get_running_loop().create_future()
        socket.ping = no_pong  # type: ignore[method-assign]
        original_close = socket.close
        async def close_and_stop() -> None:
            collector.is_running = False
            await original_close()
        socket.close = close_and_stop  # type: ignore[method-assign]
        with patch("websockets.connect", return_value=socket):
            collector.is_running = True
            await asyncio.wait_for(collector._bithumb_loop(), timeout=0.5)
        assert socket.close_calls == 1
        return collector
    with tempfile.TemporaryDirectory() as tmp:
        collector = asyncio.run(scenario(tmp))
        metric = collector.metrics["bithumb"]
        assert metric.disconnect_count == 1
        assert metric.last_connection_diagnostic is not None
        assert metric.last_connection_diagnostic["trigger_source"] == "HEARTBEAT"


def test_frame_delivered_with_reconnect_signal_is_not_discarded() -> None:
    async def scenario(tmp: str) -> MultiExchangeMicrostructureCollector:
        collector = make_collector(tmp)
        socket = FaultSocket()
        decision = _ReconnectDecision()
        frame = json.dumps({"type": "orderbook", "code": "KRW-BTC", "timestamp": 1789820682})
        async def simultaneous_recv() -> str:
            decision.request("HEARTBEAT_TIMEOUT", "HEARTBEAT")
            collector.is_running = False
            return frame
        socket.recv = simultaneous_recv  # type: ignore[method-assign]
        with patch("websockets.connect", return_value=socket), patch(
            "bithumb_coin_trader.cross_market_collector._ReconnectDecision", return_value=decision
        ):
            collector.is_running = True
            await collector._bithumb_loop()
        return collector
    with tempfile.TemporaryDirectory() as tmp:
        collector = asyncio.run(scenario(tmp))
        assert collector.metrics["bithumb"].disconnect_count == 1
        assert collector.metrics["bithumb"].orderbook_messages == 1
        assert collector._write_queue.qsize() == 1


def test_failed_first_connect_then_second_succeeds() -> None:
    class FailedConnect:
        async def __aenter__(self) -> Any:
            raise OSError("dial failed")

    async def scenario(tmp: str) -> tuple[MultiExchangeMicrostructureCollector, int]:
        collector = make_collector(tmp)
        socket = FaultSocket()
        socket.on_exhausted = lambda: setattr(collector, "is_running", False)
        calls = 0
        def connect(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            return FailedConnect() if calls == 1 else socket
        with patch("websockets.connect", side_effect=connect), patch("random.uniform", return_value=0.0):
            collector.is_running = True
            await asyncio.wait_for(collector._bithumb_loop(), timeout=2.0)
        return collector, calls
    with tempfile.TemporaryDirectory() as tmp:
        collector, calls = asyncio.run(scenario(tmp))
        assert calls == 2
        assert collector.metrics["bithumb"].disconnect_count == 2
        assert collector.metrics["bithumb"].reconnect_count == 1
        assert len(collector.session_evidence._sessions) == 1  # no phantom session on failed dial


def test_event_loop_lag_sample_is_separate_from_remote_silence() -> None:
    async def scenario(tmp: str) -> MultiExchangeMicrostructureCollector:
        collector = make_collector(tmp)
        collector.is_running = True
        task = asyncio.create_task(collector._loop_lag_worker())
        await asyncio.sleep(0.99)
        time.sleep(0.04)
        await asyncio.sleep(0.05)
        collector.is_running = False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return collector
    with tempfile.TemporaryDirectory() as tmp:
        collector = asyncio.run(scenario(tmp))
        assert collector.metrics["bithumb"].max_event_loop_lag_seconds >= 0.02
        assert collector.metrics["bithumb"].disconnect_count == 0


@pytest.mark.parametrize("outage_seconds", [1, 3, 5, 10, 15, 30])
def test_unavailable_server_has_irrecoverable_gap_lower_bound(outage_seconds: int) -> None:
    """Deterministic best-case retry model; excludes unknown handshake/frame delay."""
    decision = _ReconnectDecision()
    assert decision.request("SOCKET_EXCEPTION", "CONNECT")
    assert not decision.request("HEARTBEAT_TIMEOUT", "HEARTBEAT")
    attempts = [0]
    delay = 1
    while attempts[-1] < outage_seconds:
        attempts.append(attempts[-1] + delay)
        delay = min(30, delay * 2)
    earliest_first_frame = attempts[-1]
    assert earliest_first_frame >= outage_seconds
    assert len(attempts) == [2, 3, 4, 5, 5, 6][[1, 3, 5, 10, 15, 30].index(outage_seconds)]
    assert (earliest_first_frame > 30) == (outage_seconds == 30)
    assert decision.source == "CONNECT"


def test_partial_subscription_confirmation_and_other_exchange_control() -> None:
    frame = json.dumps({"type": "orderbook", "code": "KRW-BTC", "timestamp": 1789820682})
    socket = FaultSocket(frames=[frame])
    async def run(tmp: str) -> MultiExchangeMicrostructureCollector:
        collector = make_collector(tmp)
        collector.last_websocket_activity["binance"] = "2026-09-19T12:24:55+00:00"
        collector.last_websocket_activity["upbit"] = "2026-09-19T12:24:55+00:00"
        socket.on_exhausted = lambda: setattr(collector, "is_running", False)
        with patch("websockets.connect", return_value=socket):
            collector.is_running = True
            await collector._bithumb_loop()
        return collector
    with tempfile.TemporaryDirectory() as tmp:
        collector = asyncio.run(run(tmp))
        session = list(collector.session_evidence._sessions.values())[0]
        assert session.confirmed_feeds == ("bithumb/orderbook/KRW-BTC",)
        diagnostic = collector.metrics["bithumb"].last_connection_diagnostic
        assert diagnostic is not None
        assert diagnostic["first_frame_seconds"] is not None
        assert diagnostic["last_frame_age_seconds_at_decision"] is not None
        assert diagnostic["other_exchange_activity"]["binance"] == "2026-09-19T12:24:55+00:00"
        assert collector.metrics["binance"].disconnect_count == 0


def test_reconnect_diagnostic_is_persisted_as_json() -> None:
    async def scenario(tmp: str) -> dict[str, Any]:
        collector = make_collector(tmp)
        socket = FaultSocket(error=OSError("reset"))
        socket.on_exhausted = lambda: setattr(collector, "is_running", False)
        with patch("websockets.connect", return_value=socket):
            collector.is_running = True
            await collector._bithumb_loop()
        collector._persist_metrics()
        return json.loads(collector._metrics_path.read_text())
    with tempfile.TemporaryDirectory() as tmp:
        payload = asyncio.run(scenario(tmp))
        diagnostic = payload["exchanges"]["bithumb"]["last_connection_diagnostic"]
        assert diagnostic["schema_version"] == 1
        assert diagnostic["reconnect_reason"] == "SOCKET_EXCEPTION"
        assert diagnostic["connection_id"]
        assert payload["exchanges"]["bithumb"]["disconnect_count"] == 1


def test_queue_pressure_is_measured_without_exchange_blame() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            collector = make_collector(tmp)
            collector._write_queue = asyncio.Queue(maxsize=1)
            collector._write_queue.put_nowait(("blocked",))  # type: ignore[arg-type]
            now = datetime.now().astimezone()
            task = asyncio.create_task(collector._enqueue("bithumb", "orderbook", "KRW-BTC", {}, now, None))
            await asyncio.sleep(0)
            assert collector.metrics["bithumb"].queue_backpressure_events == 1
            assert collector.metrics["bithumb"].disconnect_count == 0
            collector._write_queue.get_nowait()
            await task
    asyncio.run(scenario())


def test_cohort_boundary_session_gap_is_preserved() -> None:
    from bithumb_coin_trader.session_evidence import SessionEvidenceTracker
    tracker = SessionEvidenceTracker("epoch", "run")
    feed = FeedIdentity("bithumb", "orderbook", "KRW-BTC")
    sid = tracker.open_session("bithumb", [feed.canonical], "2026-09-19T12:59:50Z")
    tracker.record_heartbeat(sid, "2026-09-19T12:59:55Z")
    tracker.close_session(sid, "2026-09-19T13:00:05Z", "FRAME_STALE")
    assert tracker.segments_for(feed, "2026-09-19T12:00:00Z", "2026-09-19T13:00:00Z")[0].disconnect_reason == "FRAME_STALE"
    assert tracker.segments_for(feed, "2026-09-19T13:00:00Z", "2026-09-19T14:00:00Z")


def _segment(
    session_id: str,
    connected: str,
    disconnected: str | None,
    heartbeats: tuple[str, ...],
) -> SessionSegment:
    feed = "bithumb/orderbook/KRW-BTC"
    return SessionSegment(
        exchange="bithumb", session_id=session_id, connected_at_utc=connected,
        disconnected_at_utc=disconnected, requested_feeds=(feed,),
        requested_subscription_sha256="r", confirmation_method="STREAM_SNAPSHOT",
        confirmed_at_utc=connected, confirmed_feeds=(feed,),
        confirmed_subscription_sha256="c", response_evidence_sha256="e",
        heartbeat_observations_utc=heartbeats, maximum_heartbeat_gap_seconds=None,
        disconnect_reason="INJECTED" if disconnected else None, reconnect_successor_id=None,
        collector_epoch="epoch", collector_run_id="run",
    )


def _heartbeats(start_second: int, end_second: int, step: int = 20) -> tuple[str, ...]:
    base = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
    values = range(start_second, end_second + 1, step)
    return tuple((base + timedelta(seconds=value)).strftime("%Y-%m-%dT%H:%M:%SZ") for value in values)


def _coverage(segments: tuple[SessionSegment, ...], *, conflicts: int = 0) -> str:
    observation = FrozenFeedHourObservation(
        feed=FeedIdentity("bithumb", "orderbook", "KRW-BTC"),
        cohort_utc="2026-09-19_12", interval_start_utc="2026-09-19T12:00:00Z",
        interval_end_utc="2026-09-19T13:00:00Z", cohort_qualification="QUALIFYING_FULL_HOUR",
        observation_start_utc="2026-09-19T12:00:00Z", observation_end_utc="2026-09-19T13:00:00Z",
        event_count=0, first_event_timestamp=None, last_event_timestamp=None,
        session_segments=segments, disconnect_count=sum(s.disconnected_at_utc is not None for s in segments),
        reconnect_count=0, health=WriterHealthSnapshot(conflicting_duplicate_frames=conflicts),
        logical_redundancy_enabled=True,
    )
    return materialize_feed_hour_coverage(
        observation, HeartbeatPolicy(max_allowed_heartbeat_gap_seconds={"bithumb": 30}), None,
        closed_at_utc="2026-09-19T13:00:01Z",
    ).coverage_state


def test_reconnect_evidence_uses_injected_utc_and_monotonic_clocks() -> None:
    frozen = datetime(2026, 9, 19, 12, 24, 56, 123456, tzinfo=timezone.utc)
    decision = _ReconnectDecision(utc_now=lambda: frozen, monotonic_now=lambda: 42.5)
    assert decision.request("FRAME_STALE", "FRAME_STALE")
    assert decision.requested_at_utc == frozen.isoformat()
    assert decision.requested_at_monotonic == 42.5


def test_close_exception_is_recorded_and_transport_is_aborted() -> None:
    socket = FaultSocket(error=OSError("receive reset"))
    socket.close_error = OSError("close failed")
    collector, _ = run_one_socket(socket)
    diagnostic = collector.metrics["bithumb"].last_connection_diagnostic
    assert diagnostic is not None
    assert diagnostic["exception_type"] == "OSError"  # original receive failure retained
    assert diagnostic["close_exception_type"] == "OSError"
    assert diagnostic["transport_aborted"] is True
    assert socket.transport.aborted is True


def test_redundancy_cache_is_bounded_and_conflicts_fail_coverage() -> None:
    cache = BithumbRedundancyFilter(max_entries=2, retention_seconds=10)
    a = {"type": "trade", "code": "KRW-BTC", "sequential_id": 7, "trade_price": 100}
    assert cache.observe("trade", "KRW-BTC", a, "A", 0).disposition == "canonical"
    assert cache.observe("trade", "KRW-BTC", a, "B", 1).disposition == "duplicate"
    conflict = dict(a, trade_price=101)
    assert cache.observe("trade", "KRW-BTC", conflict, "B", 2).disposition == "conflict"
    cache.observe("trade", "KRW-BTC", dict(a, sequential_id=8), "A", 3)
    cache.observe("trade", "KRW-BTC", dict(a, sequential_id=9), "A", 4)
    assert cache.size == 2
    assert cache.evicted == 1
    assert _coverage((_segment("B", "2026-09-19T12:00:00Z", None, _heartbeats(0, 3600)),), conflicts=1) == "FAILED"


def test_trade_source_timestamp_drift_is_not_a_conflict() -> None:
    cache = BithumbRedundancyFilter()
    primary = {
        "type": "trade", "code": "KRW-BTC", "sequential_id": 7,
        "trade_timestamp": 1_790_049_004_651, "timestamp": 1_790_049_004_912,
        "trade_price": 100,
    }
    secondary = dict(primary, timestamp=1_790_049_004_910)

    assert cache.observe("trade", "KRW-BTC", primary, "primary", 0).disposition == "canonical"
    timestamp_drift = cache.observe("trade", "KRW-BTC", secondary, "secondary", 1)
    assert timestamp_drift.disposition == "duplicate"
    assert timestamp_drift.equivalence == "trade_timestamp_ignored"
    assert timestamp_drift.payload_sha256 != timestamp_drift.canonical_sha256

    changed_trade = dict(secondary, trade_price=101)
    assert cache.observe("trade", "KRW-BTC", changed_trade, "secondary", 2).disposition == "conflict"


def test_cancelled_canonical_claim_can_be_reclaimed_and_is_accounted() -> None:
    cache = BithumbRedundancyFilter()
    payload = {"type": "trade", "code": "KRW-BTC", "sequential_id": 7}
    first = cache.observe("trade", "KRW-BTC", payload, "A", 1)
    assert cache.release_canonical(first)
    assert cache.observe("trade", "KRW-BTC", payload, "B", 2).disposition == "canonical"

    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            collector = make_collector(tmp)
            collector._write_queue = asyncio.Queue(maxsize=1)
            collector._write_queue.put_nowait(("occupied",))  # type: ignore[arg-type]
            task = asyncio.create_task(collector._enqueue(
                "bithumb", "trade", "KRW-BTC", payload,
                datetime.now(timezone.utc), None,
            ))
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert collector._unpersisted_event_count == 1
    asyncio.run(scenario())


def test_two_physical_loops_persist_one_canonical_and_one_provenance_record() -> None:
    async def scenario(tmp: str) -> MultiExchangeMicrostructureCollector:
        collector = make_collector(tmp)
        frame = json.dumps({
            "type": "orderbook", "code": "KRW-BTC", "timestamp": 1789820682000000,
            "orderbook_units": [{"ask_price": 1, "bid_price": 0.9}],
        })
        sockets = [FaultSocket([frame]), FaultSocket([frame])]
        for socket in sockets:
            socket.hang_recv = True
        with patch("websockets.connect", side_effect=sockets):
            collector.is_running = True
            tasks = [
                asyncio.create_task(collector._bithumb_loop("primary")),
                asyncio.create_task(collector._bithumb_loop("secondary")),
            ]
            await asyncio.wait_for(
                _wait_until(lambda: collector.metrics["bithumb"].total_messages_received == 2),
                timeout=2.0,
            )
            collector.is_running = False
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            while not collector._write_queue.empty():
                item = collector._write_queue.get_nowait()
                await collector._process_writer_item(item)
                collector._write_queue.task_done()
        return collector

    async def _wait_until(predicate: Any) -> None:
        while not predicate():
            await asyncio.sleep(0.001)

    with tempfile.TemporaryDirectory() as tmp:
        collector = asyncio.run(scenario(tmp))
        metric = collector.metrics["bithumb"]
        assert metric.redundant_frames_received == 1
        assert metric.deduplicated_frames == 1
        raw_files = list((Path(tmp) / "raw").rglob("*.jsonl"))
        assert len(raw_files) == 1
        record = json.loads(raw_files[0].read_text().strip())
        assert record["source_connection_id"]
        provenance = list((Path(tmp) / "quarantine").rglob("bithumb_redundancy_*.jsonl"))
        assert len(provenance) == 1
        assert json.loads(provenance[0].read_text())["disposition"] == "EXACT_DUPLICATE"
        assert collector.websocket_sessions["bithumb"] == "DISCONNECTED"


@pytest.mark.parametrize(
    "case,expected_gap,expected_duplicate,expected_conflict",
    [
        (25, False, False, False), (26, False, False, False),
        (27, False, False, False), (28, True, False, False),
        (29, True, False, False), (30, False, True, False),
        (31, False, True, False), (32, False, False, True),
        (33, False, True, False), (34, False, False, False),
        (35, False, False, False), (36, False, False, False),
        (37, True, False, False), (38, True, False, False),
        (39, False, False, False), (40, False, True, False),
        (41, False, False, False), (42, True, False, False),
        (43, False, False, False), (44, False, False, False),
        (45, False, True, False), (46, False, True, False),
        (47, True, False, False), (48, False, False, False),
    ],
    ids=[
        "25-primary-silent-secondary-healthy", "26-secondary-silent-primary-healthy",
        "27-primary-close-secondary-receives", "28-both-fail", "29-staggered-dual-failure",
        "30-identical-trade", "31-identical-orderbook", "32-conflicting-identity",
        "33-overlapping-sessions", "34-one-reconnect-storm", "35-concurrent-reconnects",
        "36-utc-hour-boundary", "37-after-grace-boundary", "38-event-loop-stall",
        "39-other-exchanges-unaffected", "40-queue-pressure", "41-one-normal-close",
        "42-both-normal-close", "43-dns-connect-failure", "44-tls-connect-timeout",
        "45-out-of-order-duplicates", "46-stale-copy-after-reconnect",
        "47-both-sources-absent", "48-one-source-complete",
    ],
)
def test_extended_fault_matrix_25_to_48(
    case: int, expected_gap: bool, expected_duplicate: bool, expected_conflict: bool,
) -> None:
    """Deterministic model using production dedup and logical coverage rules."""
    full = _segment("A", "2026-09-19T12:00:00Z", None, _heartbeats(0, 3600))
    first = _segment("A", "2026-09-19T12:00:00Z", "2026-09-19T12:20:00Z", _heartbeats(0, 1200))
    second = _segment("B", "2026-09-19T12:00:00Z", None, _heartbeats(0, 3600))
    delayed = _segment("B", "2026-09-19T12:20:40Z", None, _heartbeats(1240, 3600))
    if case in {28, 29, 37, 38, 42, 47}:
        segments = (first, delayed)
    elif case in {25, 26, 27, 36, 39, 41, 48}:
        segments = (first, second)
    else:
        segments = (full,)
    assert (_coverage(segments) == "FAILED") is expected_gap

    cache = BithumbRedundancyFilter(max_entries=16, retention_seconds=180)
    stream = "trade" if case in {30, 32, 45, 46} else "orderbook"
    payload = {"type": stream, "code": "KRW-BTC", "timestamp": 10, "value": 1}
    if stream == "trade":
        payload["sequential_id"] = 10
    first_result = cache.observe(stream, "KRW-BTC", payload, "A", 1)
    second_payload = dict(payload)
    if expected_conflict:
        second_payload["value"] = 2
    second_result = cache.observe(stream, "KRW-BTC", second_payload, "B", 2)
    assert first_result.disposition == "canonical"
    if expected_duplicate:
        assert second_result.disposition == "duplicate"
    elif expected_conflict:
        assert second_result.disposition == "conflict"
    else:
        # Non-dedup cases exercise coverage; give the second frame a new native identity.
        fresh = dict(payload)
        fresh["timestamp"] = 11
        if stream == "trade":
            fresh["sequential_id"] = 11
        assert cache.observe(stream, "KRW-BTC", fresh, "B", 3).disposition == "canonical"

    if case in {34, 35}:
        # Both physical owners share a serialized 250 ms dial gate: <=4 attempts/s.
        attempts = [index * 0.25 for index in range(8)]
        assert all(right - left >= 0.25 for left, right in zip(attempts, attempts[1:]))
    if case in {43, 44}:
        decision = _ReconnectDecision(utc_now=lambda: datetime(2026, 9, 19, tzinfo=timezone.utc))
        assert decision.request("CONNECT_TIMEOUT" if case == 44 else "SOCKET_EXCEPTION", "CONNECT")
        assert decision.source == "CONNECT"
