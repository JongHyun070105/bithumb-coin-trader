"""Data-Quality Flags and Microstructure Anomaly Detection (P10 - P10.5).

Provides:
- Bitmask quality flags for market data integrity.
- In-depth scanning for clock regressions, receive gaps, crossed books, zero depth,
  extreme spread, duplicate timestamps, and non-finite values.
- Filtering utilities to extract clean research partitions.
"""

from __future__ import annotations

from enum import IntFlag
import math
from typing import Iterator, Sequence, Union

from .canonical_market_data import (
    CanonicalOrderBook,
    CanonicalTrade,
    CanonicalTicker,
)


class DataQualityFlag(IntFlag):
    CLEAN = 0
    CLOCK_JUMP_BACKWARD = 1 << 0
    LARGE_RECEIVE_GAP = 1 << 1    # Gap > threshold (default 5000ms)
    CROSSED_BOOK = 1 << 2
    ZERO_DEPTH = 1 << 3
    EXTREME_SPREAD = 1 << 4      # Spread > threshold (default 100 bps)
    DUPLICATE_TIMESTAMP = 1 << 5
    NON_FINITE_VALUE = 1 << 6
    FUTURE_EXCHANGE_TIME = 1 << 7


def scan_orderbook_quality(
    ob: CanonicalOrderBook,
    prev_receive_ms: int | None = None,
    max_gap_ms: int = 5000,
    max_spread_bps: float = 100.0,
) -> DataQualityFlag:
    """Scans an individual orderbook snapshot against quality criteria."""
    flags = DataQualityFlag.CLEAN

    # Clock jump backwards
    if prev_receive_ms is not None:
        if ob.receive_timestamp_ms < prev_receive_ms:
            flags |= DataQualityFlag.CLOCK_JUMP_BACKWARD
        elif ob.receive_timestamp_ms == prev_receive_ms:
            flags |= DataQualityFlag.DUPLICATE_TIMESTAMP
        elif (ob.receive_timestamp_ms - prev_receive_ms) > max_gap_ms:
            flags |= DataQualityFlag.LARGE_RECEIVE_GAP

    # Future exchange time check (> 10s into the future)
    if ob.exchange_timestamp_ms > ob.receive_timestamp_ms + 10_000:
        flags |= DataQualityFlag.FUTURE_EXCHANGE_TIME

    # Zero depth check
    if not ob.bids or not ob.asks:
        flags |= DataQualityFlag.ZERO_DEPTH
        return flags

    best_bid, bid_qty = ob.bids[0]
    best_ask, ask_qty = ob.asks[0]

    # Non-finite check
    if not math.isfinite(best_bid) or not math.isfinite(best_ask) or not math.isfinite(bid_qty) or not math.isfinite(ask_qty):
        flags |= DataQualityFlag.NON_FINITE_VALUE
        return flags

    # Crossed or locked book
    if best_bid >= best_ask:
        flags |= DataQualityFlag.CROSSED_BOOK

    # Extreme spread
    if ob.mid_price > 0:
        spread_bps = (ob.spread / ob.mid_price) * 10_000.0
        if spread_bps > max_spread_bps:
            flags |= DataQualityFlag.EXTREME_SPREAD

    return flags


def filter_clean_orderbooks(
    orderbooks: Sequence[CanonicalOrderBook],
    max_gap_ms: int = 5000,
    max_spread_bps: float = 100.0,
    allowed_flags: DataQualityFlag = DataQualityFlag.CLEAN,
) -> list[CanonicalOrderBook]:
    """Filters an orderbook stream, retaining only records conforming to allowed_flags."""
    clean: list[CanonicalOrderBook] = []
    prev_ms: int | None = None
    for ob in orderbooks:
        flags = scan_orderbook_quality(
            ob,
            prev_receive_ms=prev_ms,
            max_gap_ms=max_gap_ms,
            max_spread_bps=max_spread_bps,
        )
        if (flags & ~allowed_flags) == DataQualityFlag.CLEAN:
            clean.append(ob)
        prev_ms = ob.receive_timestamp_ms
    return clean
