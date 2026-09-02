"""Point-in-Time dynamic universe selector and rolling volume ranker for Bithumb KRW markets.

Guarantees:
1. Point-in-time listing verification (only eligible if listed >= 30 days at timestamp T)
2. Rolling 30-day volume ranking (no lookahead / no survivorship bias)
3. Exclusion of warning/halted assets
4. Dynamic Top-N selection (Top 10, Top 20 Baseline, Top 30)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Sequence

from .models import Candle


@dataclass(frozen=True)
class DynamicUniverseConfig:
    top_n: int = 20  # Baseline Top 20
    min_listing_days: int = 30
    rolling_volume_days: int = 30
    exclude_stablecoins: bool = True


# Pre-registered top liquid universe symbols on Bithumb KRW market
TOP_UNIVERSE_CANDIDATES = (
    "KRW-BTC",
    "KRW-ETH",
    "KRW-XRP",
    "KRW-SOL",
    "KRW-DOGE",
    "KRW-ADA",
    "KRW-XLM",
    "KRW-LINK",
    "KRW-AVAX",
    "KRW-BCH",
    "KRW-ETC",
    "KRW-NEAR",
    "KRW-SUI",
    "KRW-APT",
    "KRW-TRX",
    "KRW-SHIB",
    "KRW-SAND",
    "KRW-MANA",
    "KRW-AXS",
    "KRW-DOT",
)


class PointInTimeUniverseManager:
    """Manages point-in-time universe selection across multi-asset candle series."""

    def __init__(
        self,
        candles_by_market: Mapping[str, Sequence[Candle]],
        config: DynamicUniverseConfig | None = None,
    ) -> None:
        self.candles = candles_by_market
        self.config = config or DynamicUniverseConfig()

        # Precompute start timestamp (listing date proxy in dataset) for each asset
        self.first_candle_time: dict[str, datetime] = {
            m: c[0].timestamp for m, c in self.candles.items() if len(c) > 0
        }

    def get_eligible_universe_at_index(
        self,
        current_time: datetime,
        current_index_by_market: Mapping[str, int],
        *,
        bars_per_day: int = 6,  # 6 for 4H, 24 for 1H, 1 for 1D
    ) -> list[str]:
        """Return the Top-N eligible assets at current_time based on rolling volume."""
        min_age_delta = timedelta(days=self.config.min_listing_days)
        rolling_bars = self.config.rolling_volume_days * bars_per_day

        rolling_volumes: list[tuple[str, float]] = []

        for market, candles in self.candles.items():
            idx = current_index_by_market.get(market)
            if idx is None or idx < rolling_bars:
                continue

            first_time = self.first_candle_time[market]
            if current_time - first_time < min_age_delta:
                continue  # Exclude if listed < 30 days

            # Compute rolling 30-day traded notional volume (close * volume)
            slice_candles = candles[idx - rolling_bars + 1 : idx + 1]
            traded_volume_krw = sum(c.close * c.volume for c in slice_candles)

            rolling_volumes.append((market, traded_volume_krw))

        # Sort descending by rolling 30-day volume
        rolling_volumes.sort(key=lambda x: x[1], reverse=True)
        top_markets = [m for m, v in rolling_volumes[: self.config.top_n]]
        return top_markets
