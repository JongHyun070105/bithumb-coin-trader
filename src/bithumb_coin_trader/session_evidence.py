from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
import uuid

from bithumb_coin_trader.evidence_hashing import canonical_sha256

__all__ = [
    "FeedIdentity",
    "HeartbeatPolicy",
    "WriterHealthSnapshot",
    "SessionSegment",
    "SessionEvidenceTracker",
    "hash_subscriptions",
    "normalize_feed_str",
]


def normalize_feed_str(feed_str: str) -> str:
    """Normalize a feed string like 'bithumb/orderbook/KRW-BTC'."""
    parts = feed_str.split("/")
    if len(parts) == 3:
        exchange = parts[0].lower()
        stream = parts[1].lower()
        market = parts[2]
        if exchange in ("bithumb", "upbit"):
            market = market.upper()
        elif exchange == "binance":
            market = market.lower()
        return f"{exchange}/{stream}/{market}"
    return feed_str


def hash_subscriptions(feeds: Sequence[str]) -> str:
    normalized = sorted(set(normalize_feed_str(f) for f in feeds))
    return canonical_sha256({"feeds": normalized})


@dataclass(frozen=True, order=True)
class FeedIdentity:
    exchange: str
    stream: str
    market: str

    def __post_init__(self) -> None:
        # Normalize market: Bithumb and Upbit are uppercase (e.g. KRW-BTC), Binance is lowercase (e.g. btcusdt)
        exch = self.exchange.lower()
        mkt = self.market
        if exch in ("bithumb", "upbit"):
            mkt = mkt.upper()
        elif exch == "binance":
            mkt = mkt.lower()
        object.__setattr__(self, "market", mkt)
        object.__setattr__(self, "exchange", exch)
        object.__setattr__(self, "stream", self.stream.lower())

    @property
    def canonical(self) -> str:
        return f"{self.exchange}/{self.stream}/{self.market}"

    @property
    def canonical_str(self) -> str:
        return self.canonical


@dataclass(frozen=True)
class HeartbeatPolicy:
    heartbeat_probe_interval_seconds: float = 10
    heartbeat_timeout_seconds: float = 10
    max_allowed_heartbeat_gap_seconds: Mapping[str, int] = field(
        default_factory=lambda: {
            "bithumb": 30,
            "binance": 30,
            "upbit": 30,
        }
    )

    def __post_init__(self) -> None:
        if self.max_allowed_heartbeat_gap_seconds is None:
            object.__setattr__(
                self,
                "max_allowed_heartbeat_gap_seconds",
                {
                    "bithumb": 30,
                    "binance": 30,
                    "upbit": 30,
                },
            )


@dataclass(frozen=True)
class WriterHealthSnapshot:
    writer_error_count: int = 0
    queue_dropped_events: int = 0
    unpersisted_event_count: int = 0
    fatal_writer_error_type: str | None = None
    conflicting_duplicate_frames: int = 0


@dataclass(frozen=True)
class SessionSegment:
    exchange: str
    session_id: str
    connected_at_utc: str
    disconnected_at_utc: str | None
    requested_feeds: tuple[str, ...]
    requested_subscription_sha256: str
    confirmation_method: str | None
    confirmed_at_utc: str | None
    confirmed_feeds: tuple[str, ...]
    confirmed_subscription_sha256: str | None
    response_evidence_sha256: str | None
    heartbeat_observations_utc: tuple[str, ...]
    maximum_heartbeat_gap_seconds: float | None
    disconnect_reason: str | None
    reconnect_successor_id: str | None
    collector_epoch: str
    collector_run_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "exchange": self.exchange,
            "session_id": self.session_id,
            "connected_at_utc": self.connected_at_utc,
            "disconnected_at_utc": self.disconnected_at_utc,
            "requested_feeds": list(self.requested_feeds),
            "requested_subscription_sha256": self.requested_subscription_sha256,
            "confirmation_method": self.confirmation_method,
            "confirmed_at_utc": self.confirmed_at_utc,
            "confirmed_feeds": list(self.confirmed_feeds),
            "confirmed_subscription_sha256": self.confirmed_subscription_sha256,
            "response_evidence_sha256": self.response_evidence_sha256,
            "heartbeat_observations_utc": list(self.heartbeat_observations_utc),
            "maximum_heartbeat_gap_seconds": self.maximum_heartbeat_gap_seconds,
            "disconnect_reason": self.disconnect_reason,
            "reconnect_successor_id": self.reconnect_successor_id,
            "collector_epoch": self.collector_epoch,
            "collector_run_id": self.collector_run_id,
        }


class _MutableSession:
    def __init__(
        self,
        session_id: str,
        exchange: str,
        requested_feeds: tuple[str, ...],
        requested_subscription_sha256: str,
        connected_at_utc: str,
        collector_epoch: str,
        collector_run_id: str,
    ) -> None:
        self.session_id = session_id
        self.exchange = exchange
        self.requested_feeds = requested_feeds
        self.requested_subscription_sha256 = requested_subscription_sha256
        self.connected_at_utc = connected_at_utc
        self.collector_epoch = collector_epoch
        self.collector_run_id = collector_run_id
        self.disconnected_at_utc: str | None = None
        self.confirmation_method: str | None = None
        self.confirmed_at_utc: str | None = None
        self.confirmed_feeds: tuple[str, ...] = ()
        self.confirmed_feed_at_utc: dict[str, str] = {}
        self.confirmed_subscription_sha256: str | None = None
        self.response_evidence_sha256: str | None = None
        self.heartbeat_observations_utc: list[str] = []
        self.disconnect_reason: str | None = None
        self.reconnect_successor_id: str | None = None

    def to_segment(
        self,
        interval_start_utc: str | None = None,
        interval_end_utc: str | None = None,
    ) -> SessionSegment:
        if interval_start_utc is not None and interval_end_utc is not None:
            hb_in_interval = [h for h in self.heartbeat_observations_utc if interval_start_utc <= h <= interval_end_utc]
            hb_tuples = tuple(hb_in_interval)
            if hb_in_interval:
                start_dt = datetime.fromisoformat(interval_start_utc.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(interval_end_utc.replace("Z", "+00:00"))
                conn_dt = datetime.fromisoformat(self.connected_at_utc.replace("Z", "+00:00"))
                eff_start = max(start_dt, conn_dt)
                if self.disconnected_at_utc is not None:
                    disc_dt = datetime.fromisoformat(self.disconnected_at_utc.replace("Z", "+00:00"))
                    eff_end = min(end_dt, disc_dt)
                else:
                    eff_end = end_dt

                dts = [datetime.fromisoformat(h.replace("Z", "+00:00")) for h in hb_in_interval]
                dts.sort()
                gaps = [(dts[0] - eff_start).total_seconds()]
                for i in range(len(dts) - 1):
                    gaps.append((dts[i + 1] - dts[i]).total_seconds())
                gaps.append((eff_end - dts[-1]).total_seconds())
                max_gap: float | None = max(gaps)
            else:
                max_gap = None
        else:
            hb_tuples = tuple(self.heartbeat_observations_utc)
            max_gap = None
            if len(hb_tuples) >= 2:
                gaps: list[float] = []
                dts = [datetime.fromisoformat(h.replace("Z", "+00:00")) for h in hb_tuples]
                dts.sort()
                for i in range(len(dts) - 1):
                    gaps.append((dts[i + 1] - dts[i]).total_seconds())
                if gaps:
                    max_gap = max(gaps)

        return SessionSegment(
            exchange=self.exchange,
            session_id=self.session_id,
            connected_at_utc=self.connected_at_utc,
            disconnected_at_utc=self.disconnected_at_utc,
            requested_feeds=self.requested_feeds,
            requested_subscription_sha256=self.requested_subscription_sha256,
            confirmation_method=self.confirmation_method,
            confirmed_at_utc=self.confirmed_at_utc,
            confirmed_feeds=self.confirmed_feeds,
            confirmed_subscription_sha256=self.confirmed_subscription_sha256,
            response_evidence_sha256=self.response_evidence_sha256,
            heartbeat_observations_utc=hb_tuples,
            maximum_heartbeat_gap_seconds=max_gap,
            disconnect_reason=self.disconnect_reason,
            reconnect_successor_id=self.reconnect_successor_id,
            collector_epoch=self.collector_epoch,
            collector_run_id=self.collector_run_id,
        )


class SessionEvidenceTracker:
    def __init__(self, epoch: str, run_id: str) -> None:
        self.epoch = epoch
        self.run_id = run_id
        self._sessions: dict[str, _MutableSession] = {}
        self._session_order: list[str] = []

    def open_session(
        self,
        exchange: str,
        requested: Sequence[str],
        connected_at_utc: str,
    ) -> str:
        exch = exchange.lower()
        normalized_requested = tuple(sorted(set(normalize_feed_str(f) for f in requested)))
        req_hash = hash_subscriptions(normalized_requested)
        session_id = uuid.uuid4().hex

        session = _MutableSession(
            session_id=session_id,
            exchange=exch,
            requested_feeds=normalized_requested,
            requested_subscription_sha256=req_hash,
            connected_at_utc=connected_at_utc,
            collector_epoch=self.epoch,
            collector_run_id=self.run_id,
        )
        self._sessions[session_id] = session
        self._session_order.append(session_id)
        return session_id

    def confirm(
        self,
        session_id: str,
        confirmed: Sequence[str],
        method: str,
        confirmed_at_utc: str,
        response: Mapping[str, object] | None = None,
    ) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Session {session_id} not found")

        normalized_confirmed = tuple(sorted(set(normalize_feed_str(f) for f in confirmed)))
        conf_hash = hash_subscriptions(normalized_confirmed)
        resp_hash = canonical_sha256(response) if response is not None else None

        session.confirmation_method = method
        session.confirmed_at_utc = confirmed_at_utc
        session.confirmed_feeds = normalized_confirmed
        for feed in normalized_confirmed:
            session.confirmed_feed_at_utc.setdefault(feed, confirmed_at_utc)
        session.confirmed_subscription_sha256 = conf_hash
        session.response_evidence_sha256 = resp_hash

    def record_heartbeat(
        self,
        session_id: str,
        observed_at_utc: str,
        kind: str = "frame",
    ) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Session {session_id} not found")
        # Keep second-resolution observations ordered even if ping and frame tasks race.
        observations = session.heartbeat_observations_utc
        index = bisect_left(observations, observed_at_utc)
        if index < len(observations) and observations[index] == observed_at_utc:
            return
        observations.insert(index, observed_at_utc)

    def prune_older_than(self, threshold_utc: str) -> int:
        """Prune heartbeats older than threshold_utc to bound memory, retaining 1 boundary point for gap calculations."""
        pruned_count = 0
        for session in self._sessions.values():
            hb = session.heartbeat_observations_utc
            if not hb:
                continue
            idx = 0
            while idx < len(hb) and hb[idx] < threshold_utc:
                idx += 1
            # Keep one element before threshold if available to preserve gap measurement across boundary
            keep_from = max(0, idx - 1) if idx > 0 else 0
            if keep_from > 0:
                session.heartbeat_observations_utc = hb[keep_from:]
                pruned_count += keep_from
        return pruned_count

    def close_session(
        self,
        session_id: str,
        disconnected_at_utc: str,
        reason: str,
        reconnect_successor_id: str | None = None,
    ) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Session {session_id} not found")
        session.disconnected_at_utc = disconnected_at_utc
        session.disconnect_reason = reason
        session.reconnect_successor_id = reconnect_successor_id

    def is_confirmed(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        return session.confirmed_at_utc is not None

    def segments_for(
        self,
        feed: FeedIdentity,
        interval_start_utc: str,
        interval_end_utc: str,
    ) -> tuple[SessionSegment, ...]:
        result: list[SessionSegment] = []
        for sid in self._session_order:
            sess = self._sessions[sid]
            if sess.exchange != feed.exchange:
                continue

            feed_matches = (
                feed.canonical in sess.requested_feeds
                or feed.canonical in sess.confirmed_feeds
                or feed.market in sess.requested_feeds
                or feed.market in sess.confirmed_feeds
            )
            if not feed_matches:
                continue

            if sess.connected_at_utc >= interval_end_utc:
                continue
            if sess.disconnected_at_utc is not None and sess.disconnected_at_utc <= interval_start_utc:
                continue

            segment = sess.to_segment(interval_start_utc, interval_end_utc)
            feed_confirmation = sess.confirmed_feed_at_utc.get(feed.canonical)
            if feed_confirmation is None:
                feed_confirmation = sess.confirmed_feed_at_utc.get(feed.market)
            if sess.confirmed_feed_at_utc:
                segment = replace(segment, confirmed_at_utc=feed_confirmation)
            result.append(segment)

        result.sort(key=lambda s: (s.connected_at_utc, s.session_id))
        return tuple(result)
