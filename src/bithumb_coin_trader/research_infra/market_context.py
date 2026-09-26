"""Public market context interface and strict as-of no-lookahead joiner.

Enforces:
- Section 14: Clear schema and interface for external market context data
  (orderbook top-of-book, bbo, depth, 1m/5m OHLCV bars)
- Section 15: Strict as-of join invariant where market state at trade time t
  MUST strictly satisfy: t_market <= t (strictly no future data leakage)
- Fail-closed behavior on timestamp violations or forward lookahead
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterator, Protocol, Sequence


@dataclass(frozen=True)
class TopOfBookSnapshot:
    timestamp: datetime
    symbol: str
    best_bid_price: Decimal
    best_bid_qty: Decimal
    best_ask_price: Decimal
    best_ask_qty: Decimal

    @property
    def mid_price(self) -> Decimal:
        return (self.best_bid_price + self.best_ask_price) / Decimal("2")

    @property
    def spread(self) -> Decimal:
        return self.best_ask_price - self.best_bid_price

    @property
    def spread_bps(self) -> Decimal:
        mid = self.mid_price
        if mid == Decimal("0"):
            return Decimal("0")
        return (self.spread / mid) * Decimal("10000")


@dataclass(frozen=True)
class MarketBar:
    timestamp: datetime  # Bar close time
    symbol: str
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    turnover: Decimal


class MarketContextFeed(Protocol):
    """Abstract protocol for reading external public market context."""

    def iter_orderbook_snapshots(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Iterator[TopOfBookSnapshot]:
        ...

    def iter_market_bars(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Iterator[MarketBar]:
        ...


class LookaheadViolationError(ValueError):
    """Raised when an as-of join attempts to use future market state."""


class StrictAsOfJoiner:
    """Performs deterministic, strictly non-anticipating as-of joins between trade events and market state."""

    @staticmethod
    def join_trades_with_market_bbo(
        trade_events: Sequence[Any],  # Objects with .timestamp and .symbol
        market_snapshots: Sequence[TopOfBookSnapshot],
        max_lookback_seconds: float = 60.0,
    ) -> list[dict[str, Any]]:
        """
        Joins each trade event with the most recent market snapshot occurring AT OR BEFORE trade.timestamp.
        Guarantees:
        1. t_market <= t_trade
        2. If t_market > t_trade, raises LookaheadViolationError immediately.
        3. If t_trade - t_market > max_lookback_seconds, flags state as STALE.
        """
        # Ensure market snapshots are strictly ordered by timestamp
        for i in range(1, len(market_snapshots)):
            if market_snapshots[i].timestamp < market_snapshots[i - 1].timestamp:
                raise ValueError(
                    f"Market snapshots out of chronological order at index {i}: "
                    f"{market_snapshots[i].timestamp} < {market_snapshots[i-1].timestamp}"
                )

        joined: list[dict[str, Any]] = []
        m_idx = 0
        m_len = len(market_snapshots)

        for trade in sorted(trade_events, key=lambda x: x.timestamp):
            t_time = trade.timestamp
            symbol = trade.symbol

            # Advance market index to the latest snapshot where snapshot.timestamp <= t_time
            matching_snapshot: TopOfBookSnapshot | None = None
            while m_idx < m_len and market_snapshots[m_idx].timestamp <= t_time:
                matching_snapshot = market_snapshots[m_idx]
                m_idx += 1

            # Rewind m_idx by 1 if we advanced past the latest valid snapshot
            if m_idx > 0 and matching_snapshot is not None:
                # Keep pointer at the current valid snapshot for subsequent trades
                m_idx -= 1

            if matching_snapshot is not None:
                # Explicit No-Lookahead Assert
                if matching_snapshot.timestamp > t_time:
                    raise LookaheadViolationError(
                        f"CRITICAL: Future market snapshot leaked into trade join! "
                        f"Market TS: {matching_snapshot.timestamp} > Trade TS: {t_time}"
                    )

                latency_seconds = (t_time - matching_snapshot.timestamp).total_seconds()
                is_stale = latency_seconds > max_lookback_seconds

                joined.append({
                    "trade": trade,
                    "market_snapshot": matching_snapshot,
                    "market_mid": float(matching_snapshot.mid_price),
                    "market_spread_bps": float(matching_snapshot.spread_bps),
                    "as_of_latency_seconds": latency_seconds,
                    "is_stale": is_stale,
                })
            else:
                # No preceding market data available
                joined.append({
                    "trade": trade,
                    "market_snapshot": None,
                    "market_mid": None,
                    "market_spread_bps": None,
                    "as_of_latency_seconds": None,
                    "is_stale": True,
                })

        return joined
