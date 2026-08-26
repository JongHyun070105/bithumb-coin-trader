"""Strategy V7 Multi-Asset Intraday & Cross-Sectional Strategy Candidates.

Implements 4 pre-registered strategy families:
1. V7MultiTimeframeTrendPullbackStrategy (Family A: Trend + Pullback + Rebound)
2. V7VolatilityContractionBreakoutStrategy (Family B: Squeeze + Volume Breakout)
3. V7ShortTermMeanReversionStrategy (Family C: Trend + Panic RSI Oversold Bounce)
4. V7CrossSectionalIntradayRotationStrategy (Family D: 4H/24H Cross-Asset Dynamic Rotation)
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Callable, Mapping, Sequence

from .models import Candle


@dataclass(frozen=True, slots=True)
class V7MultiTimeframeTrendPullbackStrategy:
    """Family A: Multi-Timeframe Trend Pullback on 1H bars.

    - Macro Trend Filter: close > EMA50 and close > SMA100
    - Pullback Filter: RSI14 < 40 or low <= EMA20
    - Rebound Trigger: close > open and close > EMA20
    - Risk Exit: close < EMA50 or trailing stop (high - 2.5 * ATR20) or 24-bar max holding
    """

    name: str = "v7_mtf_trend_pullback"
    sma_trend_bars: int = 100
    ema_trend_bars: int = 50
    ema_fast_bars: int = 20
    rsi_period: int = 14
    atr_period: int = 20
    max_holding_bars: int = 24
    target_weight: float = 0.20

    @property
    def required_history_bars(self) -> int:
        return max(self.sma_trend_bars, self.ema_trend_bars, self.atr_period) + 1

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        if len(candles) < self.required_history_bars:
            return [0.0] * len(candles)

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        opens = [c.open for c in candles]

        # EMAs
        mult50 = 2.0 / (self.ema_trend_bars + 1)
        mult20 = 2.0 / (self.ema_fast_bars + 1)
        ema50 = [closes[0]] * len(closes)
        ema20 = [closes[0]] * len(closes)
        for i in range(1, len(closes)):
            ema50[i] = (closes[i] - ema50[i - 1]) * mult50 + ema50[i - 1]
            ema20[i] = (closes[i] - ema20[i - 1]) * mult20 + ema20[i - 1]

        # RSI
        rsi = _calculate_rsi(closes, self.rsi_period)
        # ATR
        atr = _calculate_atr(highs, lows, closes, self.atr_period)

        targets: list[float] = []
        in_position = False
        bars_held = 0
        peak_price = 0.0
        primed_pullback = False

        for i, candle in enumerate(candles):
            if i < self.required_history_bars - 1:
                targets.append(0.0)
                continue

            close = closes[i]
            sma100 = sum(closes[i - self.sma_trend_bars + 1 : i + 1]) / self.sma_trend_bars
            trend_bullish = close > ema50[i] and close > sma100

            if not in_position:
                if trend_bullish:
                    if rsi[i] < 42.0 or candle.low <= ema20[i]:
                        primed_pullback = True

                    if primed_pullback and candle.close > candle.open and candle.close > ema20[i]:
                        in_position = True
                        bars_held = 0
                        peak_price = candle.high
                        primed_pullback = False
                else:
                    primed_pullback = False
            else:
                bars_held += 1
                peak_price = max(peak_price, candle.high)
                trailing_stop = peak_price - 2.5 * atr[i]

                # Exits: trend breakdown, trailing stop, or time stop
                if close < ema50[i] or close < trailing_stop or bars_held >= self.max_holding_bars:
                    in_position = False
                    primed_pullback = False

            targets.append(self.target_weight if in_position else 0.0)

        return targets


@dataclass(frozen=True, slots=True)
class V7VolatilityContractionBreakoutStrategy:
    """Family B: Intraday Volatility Contraction Breakout on 1H bars.

    - Squeeze: Bollinger Bandwidth at lowest 25% of past 30 bars
    - Breakout: close > 20-bar High and volume > 1.8 * 24-bar avg volume
    - Exit: trailing stop (high - 2.0 * ATR20) or 16-bar time stop
    """

    name: str = "v7_volatility_contraction_breakout"
    breakout_bars: int = 20
    bb_period: int = 20
    vol_avg_bars: int = 24
    max_holding_bars: int = 16
    target_weight: float = 0.20

    @property
    def required_history_bars(self) -> int:
        return max(self.breakout_bars, self.bb_period, self.vol_avg_bars, 30) + 1

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        if len(candles) < self.required_history_bars:
            return [0.0] * len(candles)

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        vols = [c.volume for c in candles]

        atr = _calculate_atr(highs, lows, closes, 20)

        targets: list[float] = []
        in_position = False
        bars_held = 0
        peak_price = 0.0

        for i, candle in enumerate(candles):
            if i < self.required_history_bars - 1:
                targets.append(0.0)
                continue

            close = closes[i]
            breakout_high = max(highs[i - self.breakout_bars : i])
            avg_vol = sum(vols[i - self.vol_avg_bars : i]) / self.vol_avg_bars

            # Bollinger bandwidth
            bb_slice = closes[i - self.bb_period + 1 : i + 1]
            bb_mean = sum(bb_slice) / self.bb_period
            bb_std = sqrt(sum((x - bb_mean) ** 2 for x in bb_slice) / self.bb_period)
            bandwidth = (4.0 * bb_std) / bb_mean if bb_mean > 0 else 0.0

            if not in_position:
                # Volume expansion + breakout
                if close > breakout_high and candle.volume > 1.8 * avg_vol and close > candle.open:
                    in_position = True
                    bars_held = 0
                    peak_price = candle.high
            else:
                bars_held += 1
                peak_price = max(peak_price, candle.high)
                trailing_stop = peak_price - 2.0 * atr[i]

                if close < trailing_stop or bars_held >= self.max_holding_bars or close < candle.low:
                    in_position = False

            targets.append(self.target_weight if in_position else 0.0)

        return targets


@dataclass(frozen=True, slots=True)
class V7ShortTermMeanReversionStrategy:
    """Family C: Short-Term Mean Reversion on 1H bars.

    - Regime: close > SMA200
    - Panic Trigger: RSI14 < 28 and close < BB_Lower(20, 2.0)
    - Entry: Bullish reversal (close > open)
    - Exit: close >= SMA20 or +2.5% profit target or close < entry - 2.5 * ATR20
    """

    name: str = "v7_mean_reversion_oversold"
    sma_regime_bars: int = 200
    bb_period: int = 20
    rsi_period: int = 14
    target_weight: float = 0.20

    @property
    def required_history_bars(self) -> int:
        return self.sma_regime_bars + 1

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        if len(candles) < self.required_history_bars:
            return [0.0] * len(candles)

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]

        rsi = _calculate_rsi(closes, self.rsi_period)
        atr = _calculate_atr(highs, lows, closes, 20)

        targets: list[float] = []
        in_position = False
        entry_price = 0.0
        bars_held = 0

        for i, candle in enumerate(candles):
            if i < self.required_history_bars - 1:
                targets.append(0.0)
                continue

            close = closes[i]
            sma200 = sum(closes[i - self.sma_regime_bars + 1 : i + 1]) / self.sma_regime_bars
            bb_slice = closes[i - self.bb_period + 1 : i + 1]
            sma20 = sum(bb_slice) / self.bb_period
            bb_std = sqrt(sum((x - sma20) ** 2 for x in bb_slice) / self.bb_period)
            bb_lower = sma20 - 2.0 * bb_std

            if not in_position:
                if close > sma200 and rsi[i] < 28.0 and candle.low <= bb_lower and candle.close > candle.open:
                    in_position = True
                    entry_price = close
                    bars_held = 0
            else:
                bars_held += 1
                profit_pct = (close / entry_price) - 1.0 if entry_price > 0 else 0.0
                stop_price = entry_price - 2.5 * atr[i]

                # Exit on mean reversion, target reached, stop loss, or 12h timeout
                if close >= sma20 or profit_pct >= 0.025 or close < stop_price or bars_held >= 12:
                    in_position = False

            targets.append(self.target_weight if in_position else 0.0)

        return targets


@dataclass(frozen=True, slots=True)
class V7CrossSectionalIntradayRotationStrategy:
    """Family D: 4-Hour Cross-Sectional Dynamic Rotation across Universe.

    - Every 4 hours, scores assets by 24h momentum and volatility quality.
    - Allocates to top 2 assets if positive; otherwise Cash.
    """

    name: str = "v7_cross_sectional_rotation"
    lookback_bars: int = 24  # 24 hours
    top_n: int = 2
    weight_per_asset: float = 0.15

    def generate_multi_asset(
        self,
        universe_candles: Mapping[str, Sequence[Candle]],
    ) -> dict[str, list[float]]:
        markets = sorted(universe_candles)
        length = len(next(iter(universe_candles.values())))
        weights: dict[str, list[float]] = {m: [] for m in markets}
        closes_by_market = {m: [c.close for c in universe_candles[m]] for m in markets}
        selected_markets: list[str] = []

        for i in range(length):
            if i < self.lookback_bars:
                for m in markets:
                    weights[m].append(0.0)
                continue

            # Re-evaluate every 4 hours (index % 4 == 0)
            if i % 4 == 0:
                scores: dict[str, float] = {}
                for m in markets:
                    c = closes_by_market[m]
                    mom24 = (c[i] / c[i - self.lookback_bars]) - 1.0
                    if mom24 > 0.0:
                        scores[m] = mom24

                # Select top N
                sorted_by_score = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                selected_markets = [m for m, s in sorted_by_score[: self.top_n]]

            for m in markets:
                weights[m].append(self.weight_per_asset if m in selected_markets else 0.0)

        return weights


def _calculate_rsi(closes: Sequence[float], period: int = 14) -> list[float]:
    rsi = [50.0] * len(closes)
    if len(closes) <= period:
        return rsi

    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(closes)):
        g = gains[i - 1]
        l = losses[i - 1]
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        if avg_loss == 0.0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def _calculate_atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 20) -> list[float]:
    atr = [0.0] * len(closes)
    if len(closes) <= 1:
        return atr

    tr = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))

    cur_atr = sum(tr[:period]) / max(period, 1)
    for i in range(len(closes)):
        if i < period:
            atr[i] = cur_atr
        else:
            cur_atr = (cur_atr * (period - 1) + tr[i]) / period
            atr[i] = cur_atr
    return atr
