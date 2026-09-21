"""Local fault injection for the Bithumb connection owner; no network or cloud calls."""

from __future__ import annotations

import asyncio
from datetime import datetime
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
from bithumb_coin_trader.session_evidence import FeedIdentity, HeartbeatPolicy


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
