"""Research-only volatility and volume breakout candidates.

All decisions use completed 30-minute candles.  The shared backtester is
responsible for applying a decision at the following candle open.  Strategies
are deliberately LONG/FLAT, never pyramid, and reset to FLAT across data gaps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from statistics import fmean, pstdev
from typing import Callable, Sequence

from .models import Candle, Signal


SOURCE_DELTA = timedelta(minutes=30)
CandidateFactory = Callable[[], object]


@dataclass(frozen=True, slots=True)
class VolatilityBreakoutParameters:
    family: str
    lookback: int
    volume_lookback: int
    relative_volume: float
    atr_period: int = 14
    trend_4h_period: int = 8
    exit_lookback: int = 96
    maximum_holding_bars: int = 336
    squeeze_lookback: int = 96
    squeeze_quantile: float = 0.25
    atr_expansion: float = 1.5

    def __post_init__(self) -> None:
        if self.family not in {
            "bollinger_squeeze",
            "keltner_squeeze",
            "atr_expansion",
            "volume_breakout",
        }:
            raise ValueError("unsupported volatility candidate family")
        integers = (
            self.lookback,
            self.volume_lookback,
            self.atr_period,
            self.trend_4h_period,
            self.exit_lookback,
            self.maximum_holding_bars,
            self.squeeze_lookback,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 2
            for value in integers
        ):
            raise ValueError("candidate periods must be integers of at least two")
        if self.relative_volume <= 0 or self.atr_expansion <= 0:
            raise ValueError("volume and ATR thresholds must be positive")
        if not 0 < self.squeeze_quantile < 1:
            raise ValueError("squeeze quantile must be between zero and one")


class VolatilityBreakoutStrategy:
    """Selective close-confirmed breakout with volume and 4h trend filters."""

    def __init__(self, parameters: VolatilityBreakoutParameters, *, name: str) -> None:
        if not name:
            raise ValueError("strategy name is required")
        self.parameters = parameters
        self.name = name

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        signals = [Signal.FLAT] * len(candles)
        if not candles:
            return signals

        position = Signal.FLAT
        held = 0
        segment: list[Candle] = []
        close_values: list[float] = []
        completed_4h_closes: list[float] = []
        four_hour_uptrend = False
        widths: list[float] = []

        for index, candle in enumerate(candles):
            if index and candle.timestamp - candles[index - 1].timestamp != SOURCE_DELTA:
                position = Signal.FLAT
                held = 0
                segment = []
                close_values = []
                completed_4h_closes = []
                four_hour_uptrend = False
                widths = []

            segment.append(candle)
            close_values.append(candle.close)
            if _completes_four_hour_bucket(segment):
                completed_4h_closes.append(candle.close)
                period = self.parameters.trend_4h_period
                if len(completed_4h_closes) >= period:
                    trend_average = fmean(completed_4h_closes[-period:])
                    four_hour_uptrend = candle.close > trend_average

            current_width = _bollinger_width(close_values, self.parameters.lookback)
            if current_width is not None:
                widths.append(current_width)

            if position is Signal.LONG:
                held += 1
                exit_average = _mean_last(close_values, self.parameters.exit_lookback)
                if (
                    held >= self.parameters.maximum_holding_bars
                    or exit_average is None
                    or candle.close < exit_average
                ):
                    position = Signal.FLAT
                    held = 0
            elif self._entry_allowed(
                segment,
                widths,
                four_hour_uptrend=four_hour_uptrend,
            ):
                position = Signal.LONG
                held = 0

            signals[index] = position

        return signals

    def _entry_allowed(
        self,
        candles: Sequence[Candle],
        widths: Sequence[float],
        *,
        four_hour_uptrend: bool,
    ) -> bool:
        parameters = self.parameters
        required = max(
            parameters.lookback + 1,
            parameters.volume_lookback + 1,
            parameters.atr_period + 1,
        )
        if not four_hour_uptrend or len(candles) < required:
            return False

        current = candles[-1]
        prior_high = max(
            item.high for item in candles[-parameters.lookback - 1 : -1]
        )
        prior_volume = fmean(
            item.volume for item in candles[-parameters.volume_lookback - 1 : -1]
        )
        if prior_volume <= 0 or current.volume / prior_volume < parameters.relative_volume:
            return False

        prior_atr = _average_true_range(
            candles[-parameters.atr_period - 2 : -1], parameters.atr_period
        )
        if prior_atr is None or prior_atr <= 0:
            return False

        if parameters.family == "volume_breakout":
            return current.close > prior_high

        true_range = max(
            current.high - current.low,
            abs(current.high - candles[-2].close),
            abs(current.low - candles[-2].close),
        )
        if parameters.family == "atr_expansion":
            return (
                current.close > prior_high
                and true_range >= parameters.atr_expansion * prior_atr
            )

        if current.close <= prior_high or len(widths) < parameters.squeeze_lookback + 1:
            return False
        prior_widths = widths[-parameters.squeeze_lookback - 1 : -1]
        squeeze_cutoff = _quantile(prior_widths, parameters.squeeze_quantile)
        prior_width = widths[-2]
        if parameters.family == "bollinger_squeeze":
            return prior_width <= squeeze_cutoff

        prior_mean = fmean(
            item.close for item in candles[-parameters.lookback - 1 : -1]
        )
        if prior_mean is None:
            return False
        # Keltner-like compression: the prior two-standard-deviation envelope
        # must fit inside a 1.5 ATR channel around the same rolling mean.
        return prior_width * prior_mean <= 3.0 * prior_atr


def candidate_factories() -> dict[str, CandidateFactory]:
    """Return frozen, independently instantiable volatility candidates."""

    definitions = (
        (
            "vol_bb20_squeeze96_q25_rvol125_4h20",
            VolatilityBreakoutParameters(
                "bollinger_squeeze", 20, 24, 1.25, trend_4h_period=20
            ),
        ),
        (
            "vol_keltner20_squeeze_rvol120_4h20",
            VolatilityBreakoutParameters(
                "keltner_squeeze", 20, 24, 1.20, trend_4h_period=20
            ),
        ),
        (
            "vol_atr14_expansion150_breakout20_rvol120_4h20",
            VolatilityBreakoutParameters(
                "atr_expansion", 20, 24, 1.20, trend_4h_period=20
            ),
        ),
        (
            "vol_breakout24_rvol160_4h16",
            VolatilityBreakoutParameters(
                "volume_breakout", 24, 24, 1.60, trend_4h_period=16
            ),
        ),
    )
    return {
        name: (
            lambda parameters=parameters, name=name: VolatilityBreakoutStrategy(
                parameters, name=name
            )
        )
        for name, parameters in definitions
    }


def _validate_candles(candles: Sequence[Candle]) -> None:
    if any(
        candles[index].timestamp >= candles[index + 1].timestamp
        for index in range(len(candles) - 1)
    ):
        raise ValueError("candles must be strictly chronological")
    markets = {candle.market for candle in candles}
    if len(markets) > 1:
        raise ValueError("candles must contain exactly one market")


def _completes_four_hour_bucket(candles: Sequence[Candle]) -> bool:
    if len(candles) < 8:
        return False
    bucket = candles[-8:]
    if any(
        bucket[index].timestamp + SOURCE_DELTA != bucket[index + 1].timestamp
        for index in range(7)
    ):
        return False
    close_time = bucket[-1].timestamp + SOURCE_DELTA
    if (close_time.hour * 60 + close_time.minute) % 240:
        return False
    return bucket[0].timestamp + timedelta(hours=4) == close_time


def _mean_last(values: Sequence[float], period: int) -> float | None:
    return fmean(values[-period:]) if len(values) >= period else None


def _bollinger_width(values: Sequence[float], period: int) -> float | None:
    if len(values) < period:
        return None
    window = values[-period:]
    average = fmean(window)
    if average <= 0:
        return None
    return 4.0 * pstdev(window) / average


def _average_true_range(candles: Sequence[Candle], period: int) -> float | None:
    if len(candles) < period + 1:
        return None
    ranges = []
    for previous, current in zip(candles[-period - 1 : -1], candles[-period:], strict=True):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return fmean(ranges)


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    index = probability * (len(ordered) - 1)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction
