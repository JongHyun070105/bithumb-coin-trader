"""Selective, research-only trend candidates for completed 30-minute candles.

The strategies in this module deliberately trade LONG/FLAT only.  Every
decision uses the current completed candle and older observations, leaving
execution to the next candle open in the research backtester.  Missing source
candles split the input into independent segments so neither position nor
indicator state leaks across a data gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import isfinite
from typing import Callable, Sequence

from .indicators import average_true_range, directional_indicators, ema
from .models import Candle, Signal


SOURCE_DELTA = timedelta(minutes=30)
CandidateFactory = Callable[[], object]


@dataclass(frozen=True, slots=True)
class SelectiveTrendCandidate:
    """Trend continuation candidate with explicit risk and time exits."""

    name: str
    fast_period: int
    slow_period: int
    adx_period: int
    adx_threshold: float
    entry_lookback: int
    breakout_buffer_atr: float
    require_pullback: bool
    volume_period: int
    volume_multiplier: float
    stop_atr: float
    trail_atr: float
    maximum_holding_bars: int

    def __post_init__(self) -> None:
        periods = (
            self.fast_period,
            self.slow_period,
            self.adx_period,
            self.entry_lookback,
            self.volume_period,
            self.maximum_holding_bars,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 1
            for value in periods
        ):
            raise ValueError("candidate periods must be integers greater than one")
        if self.fast_period >= self.slow_period:
            raise ValueError("fast EMA period must be below slow EMA period")
        finite_values = (
            self.adx_threshold,
            self.breakout_buffer_atr,
            self.volume_multiplier,
            self.stop_atr,
            self.trail_atr,
        )
        if not all(isfinite(value) and value >= 0.0 for value in finite_values):
            raise ValueError("candidate thresholds must be finite and non-negative")
        if self.stop_atr == 0.0 or self.trail_atr == 0.0:
            raise ValueError("ATR exits must be positive")

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        signals = [Signal.FLAT] * len(candles)
        for start, end in _contiguous_segments(candles):
            segment = candles[start:end]
            segment_signals = self._generate_segment(segment)
            signals[start:end] = segment_signals
        return signals

    def _generate_segment(self, candles: Sequence[Candle]) -> list[Signal]:
        closes = [candle.close for candle in candles]
        highs = [candle.high for candle in candles]
        lows = [candle.low for candle in candles]
        volumes = [candle.volume for candle in candles]
        fast = ema(closes, self.fast_period)
        slow = ema(closes, self.slow_period)
        atr = average_true_range(highs, lows, closes, self.adx_period)
        positive, negative, strength = directional_indicators(
            highs, lows, closes, self.adx_period
        )

        signals = [Signal.FLAT] * len(candles)
        position = Signal.FLAT
        entry_price = 0.0
        entry_pending = False
        peak_close = 0.0
        entry_atr = 0.0
        held = 0
        warmup = max(
            self.slow_period,
            self.entry_lookback + 1,
            self.volume_period,
            self.adx_period * 2,
        )

        for index in range(warmup, len(candles)):
            values = (
                fast[index],
                slow[index],
                atr[index],
                positive[index],
                negative[index],
                strength[index],
            )
            if any(value is None for value in values):
                continue
            fast_value, slow_value, atr_value, positive_value, negative_value, adx_value = (
                float(value) for value in values
            )
            close = closes[index]

            if position is Signal.LONG:
                if entry_pending:
                    entry_price = candles[index].open
                    peak_close = entry_price
                    entry_pending = False
                held += 1
                peak_close = max(peak_close, close)
                hard_stop = entry_price - self.stop_atr * entry_atr
                trailing_stop = peak_close - self.trail_atr * atr_value
                trend_failed = close < slow_value or positive_value <= negative_value
                if (
                    close <= max(hard_stop, trailing_stop)
                    or trend_failed
                    or held >= self.maximum_holding_bars
                ):
                    position = Signal.FLAT
            elif self._entry_allowed(
                index=index,
                candles=candles,
                fast=fast,
                slow=slow,
                volumes=volumes,
                fast_value=fast_value,
                slow_value=slow_value,
                atr_value=atr_value,
                positive_value=positive_value,
                negative_value=negative_value,
                adx_value=adx_value,
            ):
                position = Signal.LONG
                entry_price = 0.0
                entry_pending = True
                peak_close = 0.0
                entry_atr = atr_value
                held = 0

            signals[index] = position
        return signals

    def _entry_allowed(
        self,
        *,
        index: int,
        candles: Sequence[Candle],
        fast: Sequence[float | None],
        slow: Sequence[float | None],
        volumes: Sequence[float],
        fast_value: float,
        slow_value: float,
        atr_value: float,
        positive_value: float,
        negative_value: float,
        adx_value: float,
    ) -> bool:
        previous_fast = fast[index - 1]
        previous_slow = slow[index - 1]
        if previous_fast is None or previous_slow is None or atr_value <= 0.0:
            return False

        close = candles[index].close
        prior_high = max(
            candle.high for candle in candles[index - self.entry_lookback : index]
        )
        average_volume = sum(volumes[index - self.volume_period : index]) / self.volume_period
        trend = (
            fast_value > slow_value
            and fast_value > float(previous_fast)
            and slow_value >= float(previous_slow)
            and positive_value > negative_value
            and adx_value >= self.adx_threshold
        )
        volume_confirmed = volumes[index] >= average_volume * self.volume_multiplier
        if not trend or not volume_confirmed:
            return False

        if self.require_pullback:
            previous_close = candles[index - 1].close
            return (
                previous_close <= float(previous_fast)
                and previous_close > float(previous_slow)
                and close > fast_value
                and close < prior_high + self.breakout_buffer_atr * atr_value
            )
        return close >= prior_high + self.breakout_buffer_atr * atr_value


def candidate_factories() -> dict[str, CandidateFactory]:
    """Return a fixed, auditable set of trend candidate factories."""

    configurations = (
        dict(
            name="trend_ema48_192_adx22_pullback",
            fast_period=48,
            slow_period=192,
            adx_period=14,
            adx_threshold=22.0,
            entry_lookback=48,
            breakout_buffer_atr=0.35,
            require_pullback=True,
            volume_period=48,
            volume_multiplier=0.9,
            stop_atr=2.0,
            trail_atr=3.0,
            maximum_holding_bars=192,
        ),
        dict(
            name="trend_donchian96_adx25_volume",
            fast_period=48,
            slow_period=192,
            adx_period=14,
            adx_threshold=25.0,
            entry_lookback=96,
            breakout_buffer_atr=0.10,
            require_pullback=False,
            volume_period=48,
            volume_multiplier=1.15,
            stop_atr=2.25,
            trail_atr=3.5,
            maximum_holding_bars=240,
        ),
        dict(
            name="trend_donchian48_adx30_selective",
            fast_period=24,
            slow_period=144,
            adx_period=20,
            adx_threshold=30.0,
            entry_lookback=48,
            breakout_buffer_atr=0.20,
            require_pullback=False,
            volume_period=48,
            volume_multiplier=1.25,
            stop_atr=1.75,
            trail_atr=2.75,
            maximum_holding_bars=144,
        ),
        dict(
            name="trend_ema24_96_adx20_pullback",
            fast_period=24,
            slow_period=96,
            adx_period=14,
            adx_threshold=20.0,
            entry_lookback=32,
            breakout_buffer_atr=0.50,
            require_pullback=True,
            volume_period=32,
            volume_multiplier=0.85,
            stop_atr=1.75,
            trail_atr=2.5,
            maximum_holding_bars=120,
        ),
    )
    return {
        str(configuration["name"]): (
            lambda configuration=configuration: SelectiveTrendCandidate(**configuration)
        )
        for configuration in configurations
    }


def _validate_candles(candles: Sequence[Candle]) -> None:
    if not candles:
        raise ValueError("trend candidates require candles")
    if any(
        candle.timestamp.second
        or candle.timestamp.microsecond
        or candle.timestamp.minute % 30
        for candle in candles
    ):
        raise ValueError("trend candidates require aligned 30-minute candles")
    if any(
        candles[index].timestamp <= candles[index - 1].timestamp
        for index in range(1, len(candles))
    ):
        raise ValueError("candles must be strictly chronological")
    if len({candle.market for candle in candles}) != 1:
        raise ValueError("trend candidate input must contain exactly one market")


def _contiguous_segments(candles: Sequence[Candle]) -> tuple[tuple[int, int], ...]:
    boundaries: list[tuple[int, int]] = []
    start = 0
    for index in range(1, len(candles)):
        if candles[index].timestamp - candles[index - 1].timestamp != SOURCE_DELTA:
            boundaries.append((start, index))
            start = index
    boundaries.append((start, len(candles)))
    return tuple(boundaries)
