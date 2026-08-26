"""Frozen satellite candidate strategies for Strategy V6 research lane.

Satellite candidates are designed for intermediate swing and momentum capture:
1. V6FastDonchianSwingStrategy (20d high breakout / 10d low exit)
2. V6DailyEmaPullbackStrategy (Trend + EMA20 pullback + bounce entry)
3. V6CrossAssetFastRotationStrategy (BTC/ETH/XRP 14d relative momentum rotation)
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Callable, Sequence

from .daily_strategy_candidates import KST, _validate_daily_candles
from .models import Candle, Signal
from .strategy_v3_candidates import TargetWeightCandidate
from .strategy_v4_candidates import V4AdaptiveDonchianAtrStrategy


@dataclass(frozen=True, slots=True)
class V6FastDonchianSwingStrategy:
    """Satellite S1: Fast Donchian Swing (20d entry / 10d exit).

    Evaluates daily to capture 2-4 week swing moves under 0% fee conditions.
    """

    name: str = "v6_fast_donchian_swing"
    entry_days: int = 20
    exit_days: int = 10
    target_weight: float = 0.30

    @property
    def required_history_bars(self) -> int:
        return max(self.entry_days, self.exit_days) + 1

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        _validate_daily_candles(candles)
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]

        targets: list[float] = []
        in_position = False

        for index, candle in enumerate(candles):
            if index < self.required_history_bars - 1:
                targets.append(0.0)
                continue

            entry_high = max(highs[index - self.entry_days : index])
            exit_low = min(lows[index - self.exit_days : index])
            close = closes[index]

            if not in_position:
                if close > entry_high:
                    in_position = True
            else:
                if close < exit_low:
                    in_position = False

            targets.append(self.target_weight if in_position else 0.0)

        return targets


@dataclass(frozen=True, slots=True)
class V6DailyEmaPullbackStrategy:
    """Satellite S2: Trend Pullback Swing on daily bars.

    - Trend Filter: close > SMA100
    - Pullback Trigger: low <= EMA20 (pullback into 20d exponential average)
    - Entry Confirmation: close > open (bullish reversal bar)
    - Exit: close < EMA50 OR close < 15d low
    """

    name: str = "v6_daily_ema_pullback"
    sma_trend_days: int = 100
    ema_pullback_days: int = 20
    ema_exit_days: int = 50
    exit_low_days: int = 15
    target_weight: float = 0.30

    @property
    def required_history_bars(self) -> int:
        return max(self.sma_trend_days, self.ema_pullback_days, self.ema_exit_days, self.exit_low_days) + 1

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        _validate_daily_candles(candles)
        closes = [c.close for c in candles]
        lows = [c.low for c in candles]

        # EMA20 and EMA50 series
        mult20 = 2.0 / (self.ema_pullback_days + 1)
        mult50 = 2.0 / (self.ema_exit_days + 1)
        ema20 = [closes[0]] * len(closes)
        ema50 = [closes[0]] * len(closes)
        for i in range(1, len(closes)):
            ema20[i] = (closes[i] - ema20[i - 1]) * mult20 + ema20[i - 1]
            ema50[i] = (closes[i] - ema50[i - 1]) * mult50 + ema50[i - 1]

        targets: list[float] = []
        in_position = False
        primed_pullback = False

        for index, candle in enumerate(candles):
            if index < self.required_history_bars - 1:
                targets.append(0.0)
                continue

            close = closes[index]
            sma100 = sum(closes[index - self.sma_trend_days + 1 : index + 1]) / self.sma_trend_days
            trend_ok = close > sma100
            exit_low = min(lows[index - self.exit_low_days : index])

            if not in_position:
                if trend_ok:
                    # Check pullback
                    if candle.low <= ema20[index]:
                        primed_pullback = True

                    if primed_pullback and candle.close > candle.open and candle.close > ema20[index]:
                        in_position = True
                        primed_pullback = False
                else:
                    primed_pullback = False
            else:
                # Exit
                if close < ema50[index] or close < exit_low or close < sma100:
                    in_position = False
                    primed_pullback = False

            targets.append(self.target_weight if in_position else 0.0)

        return targets


@dataclass(frozen=True, slots=True)
class V6CrossAssetFastRotationStrategy:
    """Satellite S3: 14-day Fast Rotation across BTC, ETH, and XRP.

    - Every Sunday & Wednesday, ranks eligible assets by 14-day momentum.
    - Top asset allocated 30% if positive; otherwise 100% Cash.
    """

    name: str = "v6_cross_asset_fast_rotation"
    momentum_days: int = 14
    target_weight: float = 0.30

    @property
    def required_history_bars(self) -> int:
        return self.momentum_days + 1

    def generate_multi_asset(
        self,
        universe_candles: dict[str, Sequence[Candle]],
    ) -> dict[str, list[float]]:
        assets = sorted(universe_candles)
        length = len(next(iter(universe_candles.values())))
        closes_by_asset = {a: [c.close for c in universe_candles[a]] for a in assets}
        weights: dict[str, list[float]] = {a: [] for a in assets}
        current_selection: str | None = None

        sample_candles = universe_candles[assets[0]]

        for index in range(length):
            candle = sample_candles[index]
            if index < self.required_history_bars - 1:
                for a in assets:
                    weights[a].append(0.0)
                continue

            weekday = candle.timestamp.astimezone(KST).weekday()
            # Evaluate every Sunday (6) and Wednesday (2)
            if weekday in (2, 6):
                scores: dict[str, float] = {}
                for a in assets:
                    c = closes_by_asset[a]
                    mom14 = (c[index] / c[index - self.momentum_days]) - 1.0
                    if mom14 > 0.0:
                        scores[a] = mom14

                if scores:
                    current_selection = max(scores, key=lambda a: (scores[a], a))
                else:
                    current_selection = None

            for a in assets:
                weights[a].append(self.target_weight if a == current_selection else 0.0)

        return weights

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        """Single asset fallback interface (BTC)."""
        _validate_daily_candles(candles)
        closes = [c.close for c in candles]
        targets: list[float] = []
        current_weight = 0.0

        for index, candle in enumerate(candles):
            if index < self.required_history_bars - 1:
                targets.append(0.0)
                continue

            weekday = candle.timestamp.astimezone(KST).weekday()
            if weekday in (2, 6):
                mom14 = (closes[index] / closes[index - self.momentum_days]) - 1.0
                current_weight = self.target_weight if mom14 > 0.0 else 0.0

            targets.append(current_weight)

        return targets


def strategy_v6_satellite_factories() -> dict[str, Callable[[], TargetWeightCandidate]]:
    """Return satellite candidate factories."""
    factories: tuple[Callable[[], TargetWeightCandidate], ...] = (
        V6FastDonchianSwingStrategy,
        V6DailyEmaPullbackStrategy,
        V6CrossAssetFastRotationStrategy,
    )
    return {factory().name: factory for factory in factories}
