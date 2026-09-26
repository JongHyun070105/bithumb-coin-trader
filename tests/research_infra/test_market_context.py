"""Unit tests for public market context data interface and strict as-of no-lookahead joiner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import pytest

from bithumb_coin_trader.research_infra.market_context import (
    LookaheadViolationError,
    StrictAsOfJoiner,
    TopOfBookSnapshot,
)


@dataclass(frozen=True)
class DummyTrade:
    timestamp: datetime
    symbol: str
    price: Decimal


def test_top_of_book_mid_and_spread() -> None:
    snap = TopOfBookSnapshot(
        timestamp=datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        symbol="XBTUSD",
        best_bid_price=Decimal("10000"),
        best_bid_qty=Decimal("10"),
        best_ask_price=Decimal("10001"),
        best_ask_qty=Decimal("10"),
    )
    assert snap.mid_price == Decimal("10000.5")
    assert snap.spread == Decimal("1")
    # spread_bps = 1 / 10000.5 * 10000 ~ 0.99995 bps
    assert round(snap.spread_bps, 2) == Decimal("1.00")


def test_strict_as_of_valid_join() -> None:
    snaps = [
        TopOfBookSnapshot(
            timestamp=datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            symbol="XBTUSD",
            best_bid_price=Decimal("10000"),
            best_bid_qty=Decimal("10"),
            best_ask_price=Decimal("10001"),
            best_ask_qty=Decimal("10"),
        ),
        TopOfBookSnapshot(
            timestamp=datetime(2020, 1, 1, 12, 0, 10, tzinfo=timezone.utc),
            symbol="XBTUSD",
            best_bid_price=Decimal("10005"),
            best_bid_qty=Decimal("10"),
            best_ask_price=Decimal("10006"),
            best_ask_qty=Decimal("10"),
        ),
    ]

    # Trade at 12:00:05 should join with snap[0] (latency 5s, not stale)
    trades = [
        DummyTrade(
            timestamp=datetime(2020, 1, 1, 12, 0, 5, tzinfo=timezone.utc),
            symbol="XBTUSD",
            price=Decimal("10000"),
        )
    ]

    joined = StrictAsOfJoiner.join_trades_with_market_bbo(trades, snaps, max_lookback_seconds=30.0)
    assert len(joined) == 1
    assert joined[0]["market_snapshot"] == snaps[0]
    assert joined[0]["as_of_latency_seconds"] == 5.0
    assert not joined[0]["is_stale"]


def test_strict_as_of_stale_detection() -> None:
    snaps = [
        TopOfBookSnapshot(
            timestamp=datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            symbol="XBTUSD",
            best_bid_price=Decimal("10000"),
            best_bid_qty=Decimal("10"),
            best_ask_price=Decimal("10001"),
            best_ask_qty=Decimal("10"),
        ),
    ]

    # Trade at 12:02:00 (120s later) exceeds max_lookback (60s) -> flagged as stale
    trades = [
        DummyTrade(
            timestamp=datetime(2020, 1, 1, 12, 2, 0, tzinfo=timezone.utc),
            symbol="XBTUSD",
            price=Decimal("10000"),
        )
    ]

    joined = StrictAsOfJoiner.join_trades_with_market_bbo(trades, snaps, max_lookback_seconds=60.0)
    assert len(joined) == 1
    assert joined[0]["is_stale"] is True
    assert joined[0]["as_of_latency_seconds"] == 120.0


def test_strict_as_of_lookahead_violation_error() -> None:
    # If someone tries to pass snaps where joiner would use future snapshot
    snap_future = TopOfBookSnapshot(
        timestamp=datetime(2020, 1, 1, 12, 1, 0, tzinfo=timezone.utc),
        symbol="XBTUSD",
        best_bid_price=Decimal("10000"),
        best_bid_qty=Decimal("10"),
        best_ask_price=Decimal("10001"),
        best_ask_qty=Decimal("10"),
    )
    # Trade occurred BEFORE the first snapshot
    trades = [
        DummyTrade(
            timestamp=datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            symbol="XBTUSD",
            price=Decimal("10000"),
        )
    ]

    joined = StrictAsOfJoiner.join_trades_with_market_bbo(trades, [snap_future])
    assert len(joined) == 1
    # Future snapshot must NOT be joined to trade
    assert joined[0]["market_snapshot"] is None
    assert joined[0]["is_stale"] is True
