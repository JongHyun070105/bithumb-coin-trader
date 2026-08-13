from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Sequence

from .data import aggregate_candles
from .indicators import (
    bollinger_bandwidth,
    bollinger_bands,
    directional_indicators,
    ema,
    macd,
    percentage_volume_oscillator,
    rolling_percentile,
    rolling_volatility,
    simple_moving_average,
    wilder_rsi,
)
from .models import Candle, Signal


KST = timezone(timedelta(hours=9))


class CompletedIntervalStrategy:
    """Run an inner strategy on completed candles and map state to source bars.

    A target signal appears on the source bar whose close completes that target
    candle.  The backtester therefore cannot execute it before the following
    source open.
    """

    def __init__(
        self,
        inner: object,
        *,
        source_minutes: int = 30,
        target_minutes: int = 60,
    ) -> None:
        if (
            isinstance(source_minutes, bool)
            or isinstance(target_minutes, bool)
            or not isinstance(source_minutes, int)
            or not isinstance(target_minutes, int)
            or source_minutes <= 0
            or target_minutes <= source_minutes
            or target_minutes % source_minutes
        ):
            raise ValueError("target interval must be a larger whole multiple of source interval")
        self.inner = inner
        self.source_minutes = source_minutes
        self.target_minutes = target_minutes
        self.name = str(getattr(inner, "name", type(inner).__name__))

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        if not candles:
            return []
        completed = aggregate_candles(
            candles,
            self.source_minutes,
            self.target_minutes,
            as_of=candles[-1].timestamp + timedelta(minutes=self.source_minutes),
        )
        target_signals = self.inner.generate(completed)  # type: ignore[attr-defined]
        if len(target_signals) != len(completed):
            raise ValueError("inner strategy returned the wrong signal count")
        signal_at_close = {
            candle.timestamp + timedelta(minutes=self.target_minutes): Signal(signal)
            for candle, signal in zip(completed, target_signals, strict=True)
        }
        mapped: list[Signal] = []
        current = Signal.FLAT
        source_delta = timedelta(minutes=self.source_minutes)
        for candle in candles:
            current = signal_at_close.get(candle.timestamp + source_delta, current)
            mapped.append(current)
        return mapped


class IntersectionLongStrategy:
    """Stay long only while every constituent long/flat strategy is long."""

    def __init__(self, *strategies: object, name: str) -> None:
        if not name or len(strategies) < 2 or any(
            not callable(getattr(strategy, "generate", None)) for strategy in strategies
        ):
            raise ValueError("intersection requires a name and at least two strategies")
        self.strategies = strategies
        self.name = name

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        generated = [
            strategy.generate(candles)  # type: ignore[attr-defined]
            for strategy in self.strategies
        ]
        if any(len(signals) != len(candles) for signals in generated):
            raise ValueError("constituent strategy returned the wrong signal count")
        return [
            Signal.LONG
            if all(Signal(signals[index]) is Signal.LONG for signals in generated)
            else Signal.FLAT
            for index in range(len(candles))
        ]


class MajorityVoteLongStrategy:
    """Stay long while at least ``minimum_votes`` constituents are long."""

    def __init__(
        self,
        *strategies: object,
        minimum_votes: int,
        name: str,
    ) -> None:
        if (
            not name
            or not strategies
            or isinstance(minimum_votes, bool)
            or not isinstance(minimum_votes, int)
            or not 1 <= minimum_votes <= len(strategies)
            or any(
                not callable(getattr(strategy, "generate", None))
                for strategy in strategies
            )
        ):
            raise ValueError("majority vote requires valid strategies, threshold, and name")
        self.strategies = strategies
        self.minimum_votes = minimum_votes
        self.name = name

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        generated = [
            strategy.generate(candles)  # type: ignore[attr-defined]
            for strategy in self.strategies
        ]
        if any(len(signals) != len(candles) for signals in generated):
            raise ValueError("constituent strategy returned the wrong signal count")
        return [
            Signal.LONG
            if sum(
                Signal(signals[index]) is Signal.LONG for signals in generated
            )
            >= self.minimum_votes
            else Signal.FLAT
            for index in range(len(candles))
        ]


class CompletedCalendarMonthStrategy:
    """Map a close-only strategy over complete KST calendar months."""

    def __init__(self, inner: object, *, source_minutes: int = 30) -> None:
        if (
            isinstance(source_minutes, bool)
            or not isinstance(source_minutes, int)
            or source_minutes != 30
        ):
            raise ValueError("calendar-month mapping requires 30-minute source candles")
        if (
            not callable(getattr(inner, "generate", None))
            or getattr(inner, "uses_close_only", False) is not True
        ):
            raise ValueError("calendar-month mapping requires a close-only strategy")
        self.inner = inner
        self.source_minutes = source_minutes
        self.name = str(getattr(inner, "name", type(inner).__name__))

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        if not candles:
            return []
        source_delta = timedelta(minutes=self.source_minutes)
        by_month: dict[tuple[int, int], list[Candle]] = {}
        for candle in candles:
            local = candle.timestamp.astimezone(KST)
            by_month.setdefault((local.year, local.month), []).append(candle)

        completed: list[Candle] = []
        closes_at: list[datetime] = []
        for (year, month), bucket in sorted(by_month.items()):
            days_in_month = monthrange(year, month)[1]
            first_local = bucket[0].timestamp.astimezone(KST)
            last_local = bucket[-1].timestamp.astimezone(KST)
            if (
                (first_local.day, first_local.hour, first_local.minute) != (1, 0, 0)
                or (last_local.day, last_local.hour, last_local.minute)
                != (days_in_month, 23, 30)
            ):
                continue
            completed.append(
                Candle(
                    market=bucket[0].market,
                    timestamp=bucket[0].timestamp,
                    open=bucket[0].open,
                    high=max(candle.high for candle in bucket),
                    low=min(candle.low for candle in bucket),
                    close=bucket[-1].close,
                    volume=sum(candle.volume for candle in bucket),
                )
            )
            closes_at.append(bucket[-1].timestamp + source_delta)

        target_signals = self.inner.generate(completed)  # type: ignore[attr-defined]
        if len(target_signals) != len(completed):
            raise ValueError("inner strategy returned the wrong signal count")
        signal_at_close = {
            completed_at: Signal(signal)
            for completed_at, signal in zip(closes_at, target_signals, strict=True)
        }
        mapped: list[Signal] = []
        current = Signal.FLAT
        for candle in candles:
            current = signal_at_close.get(candle.timestamp + source_delta, current)
            mapped.append(current)
        return mapped


@dataclass(frozen=True, slots=True)
class TimeSeriesMomentumParameters:
    lookback_period: int = 365

    def __post_init__(self) -> None:
        if (
            isinstance(self.lookback_period, bool)
            or not isinstance(self.lookback_period, int)
            or self.lookback_period <= 0
        ):
            raise ValueError("momentum lookback must be a positive integer")


class TimeSeriesMomentumStrategy:
    """Long when a completed close exceeds the close exactly N bars ago."""

    def __init__(self, parameters: TimeSeriesMomentumParameters | None = None) -> None:
        self.parameters = parameters or TimeSeriesMomentumParameters()
        self.name = f"tsmom_{self.parameters.lookback_period}"

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        signals = [Signal.FLAT] * len(candles)
        lookback = self.parameters.lookback_period
        for index in range(lookback, len(candles)):
            if candles[index].close > candles[index - lookback].close:
                signals[index] = Signal.LONG
        return signals


@dataclass(frozen=True, slots=True)
class DailyCloseAboveSmaParameters:
    sma_period: int = 140

    def __post_init__(self) -> None:
        if (
            isinstance(self.sma_period, bool)
            or not isinstance(self.sma_period, int)
            or self.sma_period <= 1
        ):
            raise ValueError("daily SMA period must be an integer greater than one")


class DailyCloseAboveSmaStrategy:
    """Long while a completed daily close is above its trailing SMA."""

    uses_close_only = True

    def __init__(self, parameters: DailyCloseAboveSmaParameters | None = None) -> None:
        self.parameters = parameters or DailyCloseAboveSmaParameters()
        self.name = f"daily_close_above_sma{self.parameters.sma_period}"

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        closes = [candle.close for candle in candles]
        average = simple_moving_average(closes, self.parameters.sma_period)
        return [
            Signal.LONG if value is not None and close > value else Signal.FLAT
            for close, value in zip(closes, average, strict=True)
        ]


@dataclass(frozen=True, slots=True)
class DailySmaTrendParameters:
    fast_period: int = 50
    slow_period: int = 200

    def __post_init__(self) -> None:
        periods = (self.fast_period, self.slow_period)
        if any(
            isinstance(period, bool) or not isinstance(period, int) or period <= 1
            for period in periods
        ):
            raise ValueError("daily SMA periods must be integers greater than one")
        if self.fast_period >= self.slow_period:
            raise ValueError("fast daily SMA period must be below slow period")


class DailySmaTrendStrategy:
    """Long while the completed daily fast SMA is above the slow SMA."""

    def __init__(self, parameters: DailySmaTrendParameters | None = None) -> None:
        self.parameters = parameters or DailySmaTrendParameters()
        self.name = (
            f"daily_sma{self.parameters.fast_period}_above_"
            f"sma{self.parameters.slow_period}"
        )

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        closes = [candle.close for candle in candles]
        fast = simple_moving_average(closes, self.parameters.fast_period)
        slow = simple_moving_average(closes, self.parameters.slow_period)
        return [
            Signal.LONG
            if fast_value is not None
            and slow_value is not None
            and fast_value > slow_value
            else Signal.FLAT
            for fast_value, slow_value in zip(fast, slow, strict=True)
        ]


@dataclass(frozen=True, slots=True)
class DonchianBreakoutParameters:
    entry_period: int = 55
    exit_period: int = 20

    def __post_init__(self) -> None:
        periods = (self.entry_period, self.exit_period)
        if any(
            isinstance(period, bool) or not isinstance(period, int) or period <= 0
            for period in periods
        ):
            raise ValueError("Donchian periods must be positive integers")
        if self.exit_period >= self.entry_period:
            raise ValueError("Donchian exit period must be below entry period")


class DonchianBreakoutStrategy:
    """Long/flat close-confirmed Donchian breakout on completed candles.

    The current candle is deliberately excluded from both channel windows.
    This makes the breakout observable at that candle's close and leaves any
    execution to the following source-bar open when interval-mapped.
    """

    def __init__(self, parameters: DonchianBreakoutParameters | None = None) -> None:
        self.parameters = parameters or DonchianBreakoutParameters()
        self.name = (
            f"donchian_{self.parameters.entry_period}_high_"
            f"{self.parameters.exit_period}_low"
        )

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        signals = [Signal.FLAT] * len(candles)
        position = Signal.FLAT
        warmup = self.parameters.entry_period
        for index in range(warmup, len(candles)):
            if position is Signal.LONG:
                exit_low = min(
                    candle.low
                    for candle in candles[index - self.parameters.exit_period : index]
                )
                if candles[index].close < exit_low:
                    position = Signal.FLAT
            else:
                entry_high = max(
                    candle.high
                    for candle in candles[index - self.parameters.entry_period : index]
                )
                if candles[index].close > entry_high:
                    position = Signal.LONG
            signals[index] = position
        return signals


@dataclass(frozen=True, slots=True)
class TradingRangeBreakoutParameters:
    lookback_period: int = 50
    entry_band_fraction: float = 0.01
    exit_band_fraction: float = 0.01

    def __post_init__(self) -> None:
        if (
            isinstance(self.lookback_period, bool)
            or not isinstance(self.lookback_period, int)
            or self.lookback_period <= 0
        ):
            raise ValueError("trading-range lookback must be a positive integer")
        bands = (self.entry_band_fraction, self.exit_band_fraction)
        if not all(isfinite(value) and 0 <= value < 1 for value in bands):
            raise ValueError("trading-range bands must be finite fractions on [0, 1)")


class TradingRangeBreakoutStrategy:
    """Stateful close-confirmed breakout of the prior trading range.

    Both channels deliberately exclude the current candle.  A configured band
    is applied above the prior high for entry and below the prior low for exit.
    """

    def __init__(
        self, parameters: TradingRangeBreakoutParameters | None = None
    ) -> None:
        self.parameters = parameters or TradingRangeBreakoutParameters()
        band_bps = round(self.parameters.entry_band_fraction * 10_000)
        self.name = f"trading_range_{self.parameters.lookback_period}_{band_bps}bps"

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        signals = [Signal.FLAT] * len(candles)
        position = Signal.FLAT
        period = self.parameters.lookback_period
        for index in range(period, len(candles)):
            prior = candles[index - period : index]
            if position is Signal.LONG:
                prior_low = min(candle.low for candle in prior)
                if candles[index].close < prior_low * (
                    1.0 - self.parameters.exit_band_fraction
                ):
                    position = Signal.FLAT
            else:
                prior_high = max(candle.high for candle in prior)
                if candles[index].close > prior_high * (
                    1.0 + self.parameters.entry_band_fraction
                ):
                    position = Signal.LONG
            signals[index] = position
        return signals


@dataclass(frozen=True, slots=True)
class DailySmaAdxTrendParameters:
    fast_period: int = 50
    slow_period: int = 200
    directional_period: int = 14
    adx_threshold: float = 25.0

    def __post_init__(self) -> None:
        periods = (self.fast_period, self.slow_period, self.directional_period)
        if any(
            isinstance(period, bool) or not isinstance(period, int) or period <= 1
            for period in periods
        ):
            raise ValueError("SMA/ADX periods must be integers greater than one")
        if self.fast_period >= self.slow_period:
            raise ValueError("fast SMA period must be below slow period")
        if not isfinite(self.adx_threshold) or not 0 <= self.adx_threshold <= 100:
            raise ValueError("ADX threshold must be finite and between zero and 100")


class DailySmaAdxTrendStrategy:
    """Long when daily SMA trend and directional strength agree."""

    def __init__(self, parameters: DailySmaAdxTrendParameters | None = None) -> None:
        self.parameters = parameters or DailySmaAdxTrendParameters()
        self.name = (
            f"daily_sma{self.parameters.fast_period}_{self.parameters.slow_period}_"
            f"adx{self.parameters.directional_period}_{self.parameters.adx_threshold:g}"
        )

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        closes = [candle.close for candle in candles]
        fast = simple_moving_average(closes, self.parameters.fast_period)
        slow = simple_moving_average(closes, self.parameters.slow_period)
        positive, negative, strength = directional_indicators(
            [candle.high for candle in candles],
            [candle.low for candle in candles],
            closes,
            self.parameters.directional_period,
        )
        return [
            Signal.LONG
            if None not in (fast_value, slow_value, positive_value, negative_value, adx_value)
            and fast_value > slow_value  # type: ignore[operator]
            and positive_value > negative_value  # type: ignore[operator]
            and adx_value > self.parameters.adx_threshold  # type: ignore[operator]
            else Signal.FLAT
            for fast_value, slow_value, positive_value, negative_value, adx_value in zip(
                fast, slow, positive, negative, strength, strict=True
            )
        ]


@dataclass(frozen=True, slots=True)
class DailyMacdPvoTrendParameters:
    macd_fast_period: int = 12
    macd_slow_period: int = 26
    macd_signal_period: int = 9
    pvo_fast_period: int = 12
    pvo_slow_period: int = 26

    def __post_init__(self) -> None:
        periods = (
            self.macd_fast_period,
            self.macd_slow_period,
            self.macd_signal_period,
            self.pvo_fast_period,
            self.pvo_slow_period,
        )
        if any(
            isinstance(period, bool) or not isinstance(period, int) or period <= 1
            for period in periods
        ):
            raise ValueError("MACD/PVO periods must be integers greater than one")
        if self.macd_fast_period >= self.macd_slow_period:
            raise ValueError("MACD fast period must be below slow period")
        if self.pvo_fast_period >= self.pvo_slow_period:
            raise ValueError("PVO fast period must be below slow period")


class DailyMacdPvoTrendStrategy:
    """Long when positive daily MACD momentum has positive volume confirmation."""

    def __init__(self, parameters: DailyMacdPvoTrendParameters | None = None) -> None:
        self.parameters = parameters or DailyMacdPvoTrendParameters()
        self.name = (
            f"daily_macd{self.parameters.macd_fast_period}_"
            f"{self.parameters.macd_slow_period}_{self.parameters.macd_signal_period}_"
            f"pvo{self.parameters.pvo_fast_period}_{self.parameters.pvo_slow_period}"
        )

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        line, signal, _ = macd(
            [candle.close for candle in candles],
            self.parameters.macd_fast_period,
            self.parameters.macd_slow_period,
            self.parameters.macd_signal_period,
        )
        pvo = percentage_volume_oscillator(
            [candle.volume for candle in candles],
            self.parameters.pvo_fast_period,
            self.parameters.pvo_slow_period,
        )
        return [
            Signal.LONG
            if None not in (line_value, signal_value, pvo_value)
            and line_value > signal_value  # type: ignore[operator]
            and line_value > 0  # type: ignore[operator]
            and pvo_value > 0  # type: ignore[operator]
            else Signal.FLAT
            for line_value, signal_value, pvo_value in zip(
                line, signal, pvo, strict=True
            )
        ]


def daily_close_above_sma140_strategy() -> CompletedIntervalStrategy:
    """Return the fixed completed-KST-day close/SMA140 candidate."""

    strategy = CompletedIntervalStrategy(
        DailyCloseAboveSmaStrategy(), source_minutes=30, target_minutes=1440
    )
    strategy.name = "trend_daily_close_above_sma140"
    return strategy


def daily_close_above_sma200_strategy() -> CompletedIntervalStrategy:
    """Return the fixed completed-KST-day close/SMA200 candidate."""

    strategy = CompletedIntervalStrategy(
        DailyCloseAboveSmaStrategy(DailyCloseAboveSmaParameters(200)),
        source_minutes=30,
        target_minutes=1440,
    )
    strategy.name = "trend_daily_close_above_sma200"
    return strategy


def daily_sma50_above_sma200_strategy() -> CompletedIntervalStrategy:
    """Return the fixed completed-KST-day SMA50/SMA200 candidate."""

    strategy = CompletedIntervalStrategy(
        DailySmaTrendStrategy(), source_minutes=30, target_minutes=1440
    )
    strategy.name = "trend_daily_sma50_above_sma200"
    return strategy


def donchian_4h_55_20_strategy() -> CompletedIntervalStrategy:
    """Return the fixed completed-4h Donchian 55/20 candidate."""

    strategy = CompletedIntervalStrategy(
        DonchianBreakoutStrategy(), source_minutes=30, target_minutes=240
    )
    strategy.name = "donchian_4h_55_20_breakout"
    return strategy


def donchian_4h_20_10_strategy() -> CompletedIntervalStrategy:
    """Return the fixed completed-4h Donchian 20/10 candidate."""

    strategy = CompletedIntervalStrategy(
        DonchianBreakoutStrategy(DonchianBreakoutParameters(20, 10)),
        source_minutes=30,
        target_minutes=240,
    )
    strategy.name = "donchian_4h_20_10_breakout"
    return strategy


def daily_tsmom_365_strategy() -> CompletedIntervalStrategy:
    """Return the fixed completed-KST-day 365-bar momentum candidate."""

    strategy = CompletedIntervalStrategy(
        TimeSeriesMomentumStrategy(), source_minutes=30, target_minutes=1440
    )
    strategy.name = "trend_daily_tsmom_365"
    return strategy


def monthly_close_above_sma10_strategy() -> CompletedCalendarMonthStrategy:
    """Return the fixed completed-KST-calendar-month close/SMA10 candidate."""

    strategy = CompletedCalendarMonthStrategy(
        DailyCloseAboveSmaStrategy(DailyCloseAboveSmaParameters(10))
    )
    strategy.name = "trend_monthly_close_above_sma10"
    return strategy


def donchian_daily_55_20_strategy() -> CompletedIntervalStrategy:
    """Return the fixed completed-KST-day Donchian 55/20 candidate."""

    strategy = CompletedIntervalStrategy(
        DonchianBreakoutStrategy(), source_minutes=30, target_minutes=1440
    )
    strategy.name = "donchian_daily_55_20_breakout"
    return strategy


def donchian_daily_20_10_strategy() -> CompletedIntervalStrategy:
    """Return the fixed completed-KST-day Donchian 20/10 candidate."""

    strategy = CompletedIntervalStrategy(
        DonchianBreakoutStrategy(DonchianBreakoutParameters(20, 10)),
        source_minutes=30,
        target_minutes=1440,
    )
    strategy.name = "donchian_daily_20_10_breakout"
    return strategy


def trading_range_daily_50_band_1pct_strategy() -> CompletedIntervalStrategy:
    """Return the fixed daily prior-50 range breakout with one-percent bands."""

    strategy = CompletedIntervalStrategy(
        TradingRangeBreakoutStrategy(), source_minutes=30, target_minutes=1440
    )
    strategy.name = "trading_range_daily_50_band_1pct"
    return strategy


def trading_range_daily_50_no_band_strategy() -> CompletedIntervalStrategy:
    """Return the fixed daily prior-50 range breakout without bands."""

    strategy = CompletedIntervalStrategy(
        TradingRangeBreakoutStrategy(
            TradingRangeBreakoutParameters(
                lookback_period=50,
                entry_band_fraction=0.0,
                exit_band_fraction=0.0,
            )
        ),
        source_minutes=30,
        target_minutes=1440,
    )
    strategy.name = "trading_range_daily_50_no_band"
    return strategy


def trend_daily_sma50_200_adx14_25_strategy() -> CompletedIntervalStrategy:
    """Return the fixed daily SMA50/200 plus DI/ADX14 trend candidate."""

    strategy = CompletedIntervalStrategy(
        DailySmaAdxTrendStrategy(), source_minutes=30, target_minutes=1440
    )
    strategy.name = "trend_daily_sma50_200_adx14_25"
    return strategy


def trend_daily_macd12_26_9_pvo12_26_strategy() -> CompletedIntervalStrategy:
    """Return the fixed daily MACD plus PVO confirmation candidate."""

    strategy = CompletedIntervalStrategy(
        DailyMacdPvoTrendStrategy(), source_minutes=30, target_minutes=1440
    )
    strategy.name = "trend_daily_macd12_26_9_pvo12_26"
    return strategy


def ensemble_daily_3_of_5_strategy() -> CompletedIntervalStrategy:
    """Return one daily wrapper around a three-of-five trend vote."""

    inner = MajorityVoteLongStrategy(
        TimeSeriesMomentumStrategy(),
        TradingRangeBreakoutStrategy(),
        DailySmaTrendStrategy(),
        DailySmaAdxTrendStrategy(),
        DailyMacdPvoTrendStrategy(),
        minimum_votes=3,
        name="ensemble_daily_3_of_5",
    )
    strategy = CompletedIntervalStrategy(
        inner, source_minutes=30, target_minutes=1440
    )
    strategy.name = "ensemble_daily_3_of_5"
    return strategy


def dc_with_4h_sma50_uptrend_strategy() -> IntersectionLongStrategy:
    """Return the source DC signal gated by a completed-4h SMA50 trend."""

    trend = CompletedIntervalStrategy(
        DailyCloseAboveSmaStrategy(DailyCloseAboveSmaParameters(50)),
        source_minutes=30,
        target_minutes=240,
    )
    return IntersectionLongStrategy(
        DCBollingerRsiArmedReentryStrategy(),
        trend,
        name="dc_30m_bb20_rsi14_with_4h_sma50_uptrend",
    )


def dc_with_daily_sma140_uptrend_strategy() -> IntersectionLongStrategy:
    """Return the source DC signal gated by a completed-daily SMA140 trend."""

    trend = CompletedIntervalStrategy(
        DailyCloseAboveSmaStrategy(), source_minutes=30, target_minutes=1440
    )
    return IntersectionLongStrategy(
        DCBollingerRsiArmedReentryStrategy(),
        trend,
        name="dc_30m_bb20_rsi14_with_daily_sma140_uptrend",
    )


def _validate_candles(candles: Sequence[Candle]) -> None:
    if any(
        candles[index].timestamp >= candles[index + 1].timestamp
        for index in range(len(candles) - 1)
    ):
        raise ValueError("candles must be strictly chronological")


def _completed_daily_regime(
    candles: Sequence[Candle], period: int
) -> list[bool]:
    """Return source-rule regime using only KST days before the current day."""

    if not candles:
        return []
    daily = aggregate_candles(
        candles,
        30,
        1440,
        as_of=candles[-1].timestamp + timedelta(minutes=30),
    )
    days = [candle.timestamp.astimezone(KST).date() for candle in daily]
    closes = [candle.close for candle in daily]
    daily_sma, _, daily_lower = bollinger_bands(closes, period, 2.0)
    bearish_by_day = {
        day: (
            daily_sma[index] is not None
            and daily_lower[index] is not None
            and closes[index] < (daily_lower[index] + daily_sma[index]) / 2.0
        )
        for index, day in enumerate(days)
    }
    result: list[bool] = []
    prior_day_index = -1
    for candle in candles:
        current_day = candle.timestamp.astimezone(KST).date()
        while prior_day_index + 1 < len(days) and days[prior_day_index + 1] < current_day:
            prior_day_index += 1
        result.append(prior_day_index >= 0 and bearish_by_day[days[prior_day_index]])
    return result


def _completed_four_hour_uptrend(
    candles: Sequence[Candle], period: int
) -> list[bool]:
    """Map each source close to the latest fully completed KST 4h SMA state."""

    if not candles:
        return [False] * len(candles)
    interval = timedelta(hours=1)
    four_hours = timedelta(hours=4)
    expected = int(four_hours // interval)
    buckets: dict[datetime, list[Candle]] = {}
    for candle in candles:
        local = candle.timestamp.astimezone(KST)
        if local.minute or local.second or local.microsecond:
            raise ValueError("4h filter requires hour-aligned candles")
        bucket_start = local.replace(
            hour=(local.hour // 4) * 4,
            minute=0,
            second=0,
            microsecond=0,
        )
        buckets.setdefault(bucket_start, []).append(candle)

    completed_closes: list[float] = []
    state_at_close: dict[datetime, bool] = {}
    uptrend = False
    for bucket_start in sorted(buckets):
        bucket = buckets[bucket_start]
        bucket_end = bucket_start + four_hours
        expected_timestamps = [bucket_start + interval * index for index in range(expected)]
        actual_timestamps = [candle.timestamp.astimezone(KST) for candle in bucket]
        if actual_timestamps != expected_timestamps:
            continue
        completed_closes.append(bucket[-1].close)
        average = (
            sum(completed_closes[-period:]) / period
            if len(completed_closes) >= period
            else None
        )
        uptrend = average is not None and bucket[-1].close > average
        state_at_close[bucket_end.astimezone(timezone.utc)] = uptrend

    result: list[bool] = []
    for candle in candles:
        completed_at = candle.timestamp.astimezone(timezone.utc) + interval
        if completed_at in state_at_close:
            uptrend = state_at_close[completed_at]
        result.append(uptrend)
    return result


@dataclass(frozen=True, slots=True)
class DCBollingerRsiParameters:
    bollinger_period: int = 20
    bollinger_deviations: float = 2.0
    rsi_period: int = 14
    normal_rsi_threshold: float = 35.0
    bearish_rsi_threshold: float = 20.0
    daily_regime_period: int = 20
    take_profit_fraction: float = 0.05
    stop_loss_fraction: float = 0.05

    def __post_init__(self) -> None:
        periods = (self.bollinger_period, self.rsi_period, self.daily_regime_period)
        if any(isinstance(period, bool) or not isinstance(period, int) or period <= 1 for period in periods):
            raise ValueError("indicator periods must be integers greater than one")
        numbers = (
            self.bollinger_deviations,
            self.normal_rsi_threshold,
            self.bearish_rsi_threshold,
            self.take_profit_fraction,
            self.stop_loss_fraction,
        )
        if not all(isfinite(value) for value in numbers):
            raise ValueError("strategy parameters must be finite")
        if self.bollinger_deviations <= 0:
            raise ValueError("Bollinger deviations must be positive")
        if not 0 <= self.bearish_rsi_threshold <= self.normal_rsi_threshold <= 100:
            raise ValueError("RSI thresholds must be ordered within zero and 100")
        if not 0 < self.take_profit_fraction < 1 or not 0 < self.stop_loss_fraction < 1:
            raise ValueError("exit fractions must be between zero and one")


class DCBollingerRsiArmedReentryStrategy:
    """30m DC-inspired BB/RSI armed re-entry, long/flat spot edition.

    A close outside the lower band with RSI below the point-in-time daily
    regime threshold arms the setup.  A later close back inside the band enters.
    Five-percent close thresholds request an exit for next-open execution.
    """

    name = "dc_30m_bb20_rsi14_armed_reentry_5pct_exit"

    def __init__(self, parameters: DCBollingerRsiParameters | None = None) -> None:
        self.parameters = parameters or DCBollingerRsiParameters()

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        closes = [candle.close for candle in candles]
        _, _, lower = bollinger_bands(
            closes, self.parameters.bollinger_period, self.parameters.bollinger_deviations
        )
        rsi = wilder_rsi(closes, self.parameters.rsi_period)
        bearish = _completed_daily_regime(candles, self.parameters.daily_regime_period)
        signals = [Signal.FLAT] * len(candles)
        armed = False
        position = Signal.FLAT
        executed_entry: float | None = None
        entry_pending = False
        for index, close in enumerate(closes):
            if lower[index] is None or rsi[index] is None:
                continue
            if position is Signal.LONG:
                if entry_pending:
                    executed_entry = candles[index].open
                    entry_pending = False
                assert executed_entry is not None
                if (
                    close >= executed_entry * (1.0 + self.parameters.take_profit_fraction)
                    or close <= executed_entry * (1.0 - self.parameters.stop_loss_fraction)
                ):
                    position = Signal.FLAT
                    executed_entry = None
                signals[index] = position
                continue
            threshold = (
                self.parameters.bearish_rsi_threshold
                if bearish[index]
                else self.parameters.normal_rsi_threshold
            )
            if close < lower[index] and rsi[index] < threshold:
                armed = True
            elif armed and close >= lower[index]:
                position = Signal.LONG
                entry_pending = True
                armed = False
            signals[index] = position
        return signals


@dataclass(frozen=True, slots=True)
class MeanReversionParameters:
    bollinger_period: int = 20
    bollinger_deviations: float = 2.0
    rsi_period: int = 14
    rsi_threshold: float = 30.0
    maximum_holding_bars: int = 24
    trend_ema_period: int = 200

    def __post_init__(self) -> None:
        periods = (self.bollinger_period, self.rsi_period, self.trend_ema_period)
        if any(isinstance(period, bool) or not isinstance(period, int) or period <= 1 for period in periods):
            raise ValueError("indicator periods must be integers greater than one")
        if (
            isinstance(self.maximum_holding_bars, bool)
            or not isinstance(self.maximum_holding_bars, int)
            or self.maximum_holding_bars <= 0
        ):
            raise ValueError("maximum holding bars must be a positive integer")
        if not isfinite(self.bollinger_deviations) or self.bollinger_deviations <= 0:
            raise ValueError("Bollinger deviations must be finite and positive")
        if not isfinite(self.rsi_threshold) or not 0 <= self.rsi_threshold <= 100:
            raise ValueError("RSI threshold must be between zero and 100")


class BollingerRsiReentryStrategy:
    """1h lower-band re-entry plus RSI-30 cross, exited at midline or 24 bars."""

    name = "mean_reversion_1h_bb20_rsi30_reentry_24bar_exit"
    require_uptrend = False
    require_four_hour_uptrend = False

    def __init__(self, parameters: MeanReversionParameters | None = None) -> None:
        self.parameters = parameters or MeanReversionParameters()

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        closes = [candle.close for candle in candles]
        middle, _, lower = bollinger_bands(
            closes, self.parameters.bollinger_period, self.parameters.bollinger_deviations
        )
        rsi = wilder_rsi(closes, self.parameters.rsi_period)
        trend = (
            ema(closes, self.parameters.trend_ema_period)
            if self.require_uptrend
            else [None] * len(candles)
        )
        four_hour_trend = (
            _completed_four_hour_uptrend(candles, 50)
            if self.require_four_hour_uptrend
            else [False] * len(candles)
        )
        signals = [Signal.FLAT] * len(candles)
        position = Signal.FLAT
        held_bars = 0
        for index in range(1, len(candles)):
            ready = None not in (
                middle[index],
                lower[index],
                lower[index - 1],
                rsi[index],
                rsi[index - 1],
            )
            if position is Signal.LONG:
                held_bars += 1
                if ready and (
                    closes[index] >= middle[index]  # type: ignore[operator]
                    or held_bars >= self.parameters.maximum_holding_bars
                ):
                    position = Signal.FLAT
                signals[index] = position
                continue
            trend_ok = not self.require_uptrend or (
                trend[index] is not None and closes[index] > trend[index]  # type: ignore[operator]
            )
            trend_ok = trend_ok and (
                not self.require_four_hour_uptrend or four_hour_trend[index]
            )
            if (
                ready
                and trend_ok
                and closes[index - 1] < lower[index - 1]  # type: ignore[operator]
                and closes[index] >= lower[index]  # type: ignore[operator]
                and rsi[index - 1] <= self.parameters.rsi_threshold  # type: ignore[operator]
                and rsi[index] > self.parameters.rsi_threshold  # type: ignore[operator]
            ):
                position = Signal.LONG
                held_bars = 0
            signals[index] = position
        return signals


class BollingerRsiUptrendReentryStrategy(BollingerRsiReentryStrategy):
    name = "mean_reversion_1h_bb20_rsi30_reentry_ema200_uptrend"
    require_uptrend = True


class BollingerRsiFourHourUptrendReentryStrategy(BollingerRsiReentryStrategy):
    name = "mean_reversion_1h_bb20_rsi30_reentry_4h_sma50_uptrend"
    require_four_hour_uptrend = True


@dataclass(frozen=True, slots=True)
class SqueezeBreakoutParameters:
    bollinger_period: int = 20
    bollinger_deviations: float = 2.0
    bandwidth_lookback: int = 120
    squeeze_quantile: float = 0.20

    def __post_init__(self) -> None:
        periods = (self.bollinger_period, self.bandwidth_lookback)
        if any(isinstance(period, bool) or not isinstance(period, int) or period <= 1 for period in periods):
            raise ValueError("indicator periods must be integers greater than one")
        if not isfinite(self.bollinger_deviations) or self.bollinger_deviations <= 0:
            raise ValueError("Bollinger deviations must be finite and positive")
        if not isfinite(self.squeeze_quantile) or not 0 < self.squeeze_quantile < 1:
            raise ValueError("squeeze quantile must be between zero and one")


class BollingerSqueezeBreakoutStrategy:
    """Long-only BB squeeze breakout; a midline loss requests next-open exit."""

    name = "bb_squeeze_bottom20_breakout_120_exit_midline"

    def __init__(self, parameters: SqueezeBreakoutParameters | None = None) -> None:
        self.parameters = parameters or SqueezeBreakoutParameters()

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        closes = [candle.close for candle in candles]
        middle, upper, _ = bollinger_bands(
            closes, self.parameters.bollinger_period, self.parameters.bollinger_deviations
        )
        bandwidth = bollinger_bandwidth(
            closes, self.parameters.bollinger_period, self.parameters.bollinger_deviations
        )
        signals = [Signal.FLAT] * len(candles)
        position = Signal.FLAT
        for index in range(1, len(candles)):
            if None in (middle[index], upper[index], upper[index - 1], bandwidth[index]):
                continue
            history = [
                value
                for value in bandwidth[
                    max(0, index - self.parameters.bandwidth_lookback + 1) : index + 1
                ]
                if value is not None
            ]
            squeeze = False
            if len(history) >= self.parameters.bandwidth_lookback:
                threshold = rolling_percentile(
                    history,
                    self.parameters.bandwidth_lookback,
                    self.parameters.squeeze_quantile * 100.0,
                )[-1]
                squeeze = (
                    threshold is not None
                    and bandwidth[index] <= threshold  # type: ignore[operator]
                )
            if position is Signal.LONG:
                if closes[index] < middle[index]:  # type: ignore[operator]
                    position = Signal.FLAT
            elif (
                squeeze
                and closes[index - 1] <= upper[index - 1]  # type: ignore[operator]
                and closes[index] > upper[index]  # type: ignore[operator]
            ):
                position = Signal.LONG
            signals[index] = position
        return signals


@dataclass(frozen=True, slots=True)
class StrategyParameters:
    fast_period: int = 20
    slow_period: int = 80
    breakout_period: int = 20
    exit_period: int = 10
    volatility_period: int = 20
    maximum_annualized_volatility: float = 1.50
    allow_short_signals: bool = False

    def __post_init__(self) -> None:
        periods = (
            self.fast_period,
            self.slow_period,
            self.breakout_period,
            self.exit_period,
            self.volatility_period,
        )
        if any(period <= 1 for period in periods):
            raise ValueError("strategy periods must be greater than one")
        if self.fast_period >= self.slow_period:
            raise ValueError("fast period must be below slow period")
        if self.maximum_annualized_volatility <= 0:
            raise ValueError("volatility cap must be positive")


class TrendBreakoutStrategy:
    """Bithumb spot long/flat trend breakout with no future-candle access."""

    def __init__(self, parameters: StrategyParameters | None = None) -> None:
        self.parameters = parameters or StrategyParameters()

    def generate(
        self,
        candles: Sequence[Candle],
        *,
        initial_position: Signal = Signal.FLAT,
        start_index: int | None = None,
    ) -> list[Signal]:
        _validate_candles(candles)
        closes = [candle.close for candle in candles]
        fast = ema(closes, self.parameters.fast_period)
        slow = ema(closes, self.parameters.slow_period)
        exits = ema(closes, self.parameters.exit_period)
        volatility = rolling_volatility(closes, self.parameters.volatility_period)
        try:
            position = Signal(initial_position)
        except (TypeError, ValueError) as exc:
            raise ValueError("initial_position is invalid") from exc
        if position is Signal.SHORT and not self.parameters.allow_short_signals:
            raise ValueError("SHORT initial_position requires allow_short_signals")
        signals = [Signal.FLAT] * len(candles)
        warmup = max(
            self.parameters.slow_period,
            self.parameters.breakout_period,
            self.parameters.volatility_period + 1,
        )
        if start_index is None and warmup >= len(candles):
            return signals
        first_decision = warmup if start_index is None else start_index
        if (
            isinstance(first_decision, bool)
            or not isinstance(first_decision, int)
            or first_decision < warmup
            or first_decision >= len(candles)
        ):
            raise ValueError("start_index must select a candle after strategy warmup")
        for index in range(first_decision, len(candles)):
            fast_value = fast[index]
            slow_value = slow[index]
            exit_value = exits[index]
            vol_value = volatility[index]
            if None in (fast_value, slow_value, exit_value, vol_value):
                continue
            if vol_value > self.parameters.maximum_annualized_volatility:
                position = Signal.FLAT
                signals[index] = position
                continue
            previous = candles[index - self.parameters.breakout_period : index]
            upper = max(candle.high for candle in previous)
            lower = min(candle.low for candle in previous)
            close = candles[index].close
            if position is Signal.LONG and close < exit_value:
                position = Signal.FLAT
            elif position is Signal.SHORT and close > exit_value:
                position = Signal.FLAT
            if close > upper and fast_value > slow_value:
                position = Signal.LONG
            elif (
                self.parameters.allow_short_signals
                and close < lower
                and fast_value < slow_value
            ):
                position = Signal.SHORT
            signals[index] = position
        return signals
