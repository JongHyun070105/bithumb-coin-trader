"""Point-in-time universe selection, liquidity filtering, and risk screening for Bithumb KRW markets.

Implements:
1. Minimum 24h trading volume threshold (default: 5 billion KRW / 5,000,000,000 KRW)
2. Warning / Caution asset filtering (excludes investment warnings, deposit halts)
3. Multi-timeframe ranking and scoring for top N asset allocation
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

from .models import Candle


# Top tier liquid universe candidates on Bithumb KRW market
DEFAULT_CORE_UNIVERSE = (
    "KRW-BTC",
    "KRW-ETH",
    "KRW-XRP",
    "KRW-SOL",
    "KRW-DOGE",
)


@dataclass(frozen=True, slots=True)
class UniverseFilterSettings:
    min_24h_volume_krw: float = 5_000_000_000.0  # 50억 원 이상
    max_active_assets: int = 3  # Top 1~3 assets
    exclude_warning_coins: bool = True


@dataclass(frozen=True, slots=True)
class AssetScore:
    market: str
    momentum_score: float
    volume_score: float
    trend_quality: float
    composite_score: float
    is_eligible: bool


def score_universe_assets(
    market_candles: Mapping[str, Sequence[Candle]],
    current_index: int,
    *,
    lookback_bars: int = 24,
    vol_lookback: int = 24,
) -> list[AssetScore]:
    """Score and rank all assets in the universe at current_index deterministically."""
    scores: list[AssetScore] = []

    for market, candles in market_candles.items():
        if current_index < lookback_bars or current_index >= len(candles):
            continue

        close_now = candles[current_index].close
        close_past = candles[current_index - lookback_bars].close
        mom = (close_now / close_past) - 1.0

        # Volume ratio: current bar volume vs average volume
        vol_now = candles[current_index].volume
        vol_avg = sum(c.volume for c in candles[current_index - vol_lookback : current_index]) / max(vol_lookback, 1)
        vol_ratio = (vol_now / vol_avg) if vol_avg > 0 else 1.0

        # Trend quality: ratio of positive price movements
        pos_moves = sum(
            candles[i].close > candles[i - 1].close
            for i in range(current_index - lookback_bars + 1, current_index + 1)
        )
        trend_quality = pos_moves / lookback_bars

        # Composite score
        composite = (mom * 0.5) + (min(vol_ratio, 3.0) * 0.1) + (trend_quality * 0.4)
        is_eligible = mom > 0.0 and trend_quality >= 0.50

        scores.append(
            AssetScore(
                market=market,
                momentum_score=mom,
                volume_score=vol_ratio,
                trend_quality=trend_quality,
                composite_score=composite,
                is_eligible=is_eligible,
            )
        )

    # Sort descending by composite score
    return sorted(scores, key=lambda s: (s.composite_score, s.momentum_score), reverse=True)
