from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from math import isfinite
import re


_MARKET_RE = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+$")


class Signal(IntEnum):
    SHORT = -1
    FLAT = 0
    LONG = 1


@dataclass(frozen=True, slots=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    market: str = "KRW-BTC"

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close, self.volume)
        if self.timestamp.tzinfo is None:
            raise ValueError("candle timestamp must be timezone-aware")
        if not _MARKET_RE.fullmatch(self.market):
            raise ValueError("market must look like 'KRW-BTC'")
        if not all(isfinite(value) for value in values):
            raise ValueError("candle values must be finite")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC values must be positive")
        if self.volume < 0:
            raise ValueError("volume cannot be negative")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("candle high/low do not contain open and close")
        if self.high < self.low:
            raise ValueError("candle high cannot be below low")
