"""Frozen profit-first candidates for completed KRW-BTC 30-minute candles.

These candidates are deliberately LONG/FLAT and research-only.  A state change
is decided from a completed candle and is therefore eligible for execution only
at the next candle open by the shared backtester.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from math import isfinite, sqrt
from statistics import median, pstdev
from typing import Any, Callable, Sequence

from .indicators import average_true_range, ema
from .models import Candle, Signal


SOURCE_DELTA = timedelta(minutes=30)
KST = timezone(timedelta(hours=9))
CandidateFactory = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class FourHourBar:
    source_index: int
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class DonchianRetestCandidate:
    name: str
    breakout_bars: int = 55
    exit_bars: int = 20
    retest_source_bars: int = 6
    maximum_holding_source_bars: int = 320
    atr_period: int = 14
    trail_atr: float = 3.0

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        signals = [Signal.FLAT] * len(candles)
        for start, end in _contiguous_segments(candles):
            signals[start:end] = self._generate_segment(candles[start:end])
        return signals

    def _generate_segment(self, candles: Sequence[Candle]) -> list[Signal]:
        signals = [Signal.FLAT] * len(candles)
        bars = _completed_four_hour_bars(candles)
        by_source_index = {bar.source_index: index for index, bar in enumerate(bars)}
        atr = average_true_range(
            [candle.high for candle in candles],
            [candle.low for candle in candles],
            [candle.close for candle in candles],
            self.atr_period,
        )
        position = Signal.FLAT
        armed_line: float | None = None
        armed_from = -1
        armed_until = -1
        peak = 0.0
        held = 0

        for index, candle in enumerate(candles):
            bar_index = by_source_index.get(index)
            if position is Signal.LONG:
                held += 1
                peak = max(peak, candle.close)
                atr_value = atr[index]
                trailing_exit = (
                    atr_value is not None
                    and candle.close <= peak - self.trail_atr * atr_value
                )
                channel_exit = False
                if bar_index is not None and bar_index >= self.exit_bars:
                    channel_exit = bars[bar_index].close < min(
                        bar.low for bar in bars[bar_index - self.exit_bars : bar_index]
                    )
                if trailing_exit or channel_exit or held >= self.maximum_holding_source_bars:
                    position = Signal.FLAT
                    held = 0
                    peak = 0.0
            else:
                if bar_index is not None and bar_index >= self.breakout_bars:
                    prior_high = max(
                        bar.high
                        for bar in bars[bar_index - self.breakout_bars : bar_index]
                    )
                    if bars[bar_index].close > prior_high:
                        armed_line = prior_high
                        armed_from = index + 1
                        armed_until = index + self.retest_source_bars
                if armed_line is not None:
                    atr_value = atr[index]
                    if index > armed_until or (
                        atr_value is not None
                        and candle.close < armed_line - 0.75 * atr_value
                    ):
                        armed_line = None
                    elif atr_value is not None and index >= max(48, armed_from):
                        average_volume = sum(
                            item.volume for item in candles[index - 48 : index]
                        ) / 48
                        candle_range = candle.high - candle.low
                        close_location = (
                            (candle.close - candle.low) / candle_range
                            if candle_range > 0
                            else 0.0
                        )
                        retested = candle.low <= armed_line + 0.5 * atr_value
                        if (
                            retested
                            and candle.close >= armed_line
                            and close_location >= 0.70
                            and candle.volume >= average_volume
                        ):
                            position = Signal.LONG
                            peak = candle.close
                            held = 0
                            armed_line = None
            signals[index] = position
        return signals


@dataclass(frozen=True, slots=True)
class DualMomentumCandidate:
    name: str
    fast_bars: int = 42
    slow_bars: int = 168
    ema_bars: int = 50
    ema_slope_bars: int = 10
    volatility_history_bars: int = 540
    volatility_percentile: float = 0.80

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        signals = [Signal.FLAT] * len(candles)
        for start, end in _contiguous_segments(candles):
            signals[start:end] = self._generate_segment(candles[start:end])
        return signals

    def _generate_segment(self, candles: Sequence[Candle]) -> list[Signal]:
        signals = [Signal.FLAT] * len(candles)
        bars = _completed_four_hour_bars(candles)
        closes = [bar.close for bar in bars]
        trend = ema(closes, self.ema_bars)
        realized: list[float | None] = [None] * len(bars)
        returns = [0.0] + [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes))]
        for index in range(12, len(bars)):
            realized[index] = pstdev(returns[index - 11 : index + 1]) * sqrt(6 * 365)

        state = Signal.FLAT
        previous_source = -1
        warmup = max(
            self.slow_bars,
            self.ema_bars + self.ema_slope_bars,
            self.volatility_history_bars + 12,
        )
        for bar_index, bar in enumerate(bars):
            signals[previous_source + 1 : bar.source_index] = [state] * (
                bar.source_index - previous_source - 1
            )
            trend_value = trend[bar_index]
            if bar_index >= warmup and trend_value is not None:
                fast_return = closes[bar_index] / closes[bar_index - self.fast_bars] - 1
                slow_return = closes[bar_index] / closes[bar_index - self.slow_bars] - 1
                past_volatility = sorted(
                    value
                    for value in realized[
                        bar_index - self.volatility_history_bars : bar_index
                    ]
                    if value is not None
                )
                cutoff_index = min(
                    len(past_volatility) - 1,
                    int((len(past_volatility) - 1) * self.volatility_percentile),
                )
                realized_value = realized[bar_index]
                volatility_ok = bool(
                    past_volatility
                    and realized_value is not None
                    and realized_value <= past_volatility[cutoff_index]
                )
                slope_value = trend[bar_index - self.ema_slope_bars]
                entry = (
                    fast_return > 0
                    and slow_return > 0
                    and bar.close > trend_value
                    and slope_value is not None
                    and trend_value > slope_value
                    and volatility_ok
                )
                exit_now = fast_return <= 0 or bar.close < trend_value
                if state is Signal.LONG and exit_now:
                    state = Signal.FLAT
                elif state is Signal.FLAT and entry:
                    state = Signal.LONG
            signals[bar.source_index] = state
            previous_source = bar.source_index
        if previous_source + 1 < len(candles):
            signals[previous_source + 1 :] = [state] * (len(candles) - previous_source - 1)
        return signals


@dataclass(frozen=True, slots=True)
class ExtremeDropReboundCandidate:
    name: str
    drop_fraction: float
    volume_multiple: float = 1.5
    target_fraction: float = 0.022
    stop_fraction: float = 0.015
    maximum_holding_bars: int = 24

    def __post_init__(self) -> None:
        values = (
            self.drop_fraction,
            self.volume_multiple,
            self.target_fraction,
            self.stop_fraction,
        )
        if not all(isfinite(value) and value > 0 for value in values):
            raise ValueError("candidate thresholds must be finite and positive")

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        signals = [Signal.FLAT] * len(candles)
        for start, end in _contiguous_segments(candles):
            signals[start:end] = self._generate_segment(candles[start:end])
        return signals

    def _generate_segment(self, candles: Sequence[Candle]) -> list[Signal]:
        signals = [Signal.FLAT] * len(candles)
        position = Signal.FLAT
        entry_pending = False
        entry_price = 0.0
        held = 0
        returns = [0.0] + [
            candles[index].close / candles[index - 1].close - 1
            for index in range(1, len(candles))
        ]
        for index, candle in enumerate(candles):
            if position is Signal.LONG:
                if entry_pending:
                    entry_price = candle.open
                    entry_pending = False
                held += 1
                if (
                    candle.high >= entry_price * (1 + self.target_fraction)
                    or candle.low <= entry_price * (1 - self.stop_fraction)
                    or held >= self.maximum_holding_bars
                ):
                    position = Signal.FLAT
                    held = 0
            elif index >= 20:
                volume_floor = median(item.volume for item in candles[index - 20 : index])
                repeated_collapse = sum(
                    value <= -self.drop_fraction for value in returns[max(1, index - 4) : index]
                ) >= 1
                if (
                    returns[index] <= -self.drop_fraction
                    and candle.volume >= volume_floor * self.volume_multiple
                    and not repeated_collapse
                ):
                    position = Signal.LONG
                    entry_pending = True
                    held = 0
            signals[index] = position
        return signals


def candidate_factories() -> dict[str, CandidateFactory]:
    configurations: tuple[object, ...] = (
        DonchianRetestCandidate(name="profit_donchian_4h_55_retest6"),
        DualMomentumCandidate(name="profit_dual_momentum_4h_7d_28d"),
        ExtremeDropReboundCandidate(
            name="profit_extreme_drop_16bp_rebound12h",
            drop_fraction=0.016,
        ),
        ExtremeDropReboundCandidate(
            name="profit_extreme_drop_20bp_rebound12h",
            drop_fraction=0.020,
        ),
    )
    return {
        candidate.name: (lambda candidate=candidate: candidate)
        for candidate in configurations
    }


def _completed_four_hour_bars(candles: Sequence[Candle]) -> list[FourHourBar]:
    bars: list[FourHourBar] = []
    index = 0
    while index < len(candles):
        stamp = candles[index].timestamp.astimezone(KST)
        start = stamp.replace(hour=(stamp.hour // 4) * 4, minute=0, second=0, microsecond=0)
        end = index
        while end + 1 < len(candles):
            candidate = candles[end + 1].timestamp.astimezone(KST)
            candidate_start = candidate.replace(
                hour=(candidate.hour // 4) * 4,
                minute=0,
                second=0,
                microsecond=0,
            )
            if candidate_start != start:
                break
            end += 1
        bucket = candles[index : end + 1]
        expected = [start + offset * SOURCE_DELTA for offset in range(8)]
        if len(bucket) == 8 and [item.timestamp.astimezone(KST) for item in bucket] == expected:
            bars.append(
                FourHourBar(
                    source_index=end,
                    high=max(item.high for item in bucket),
                    low=min(item.low for item in bucket),
                    close=bucket[-1].close,
                    volume=sum(item.volume for item in bucket),
                )
            )
        index = end + 1
    return bars


def _validate_candles(candles: Sequence[Candle]) -> None:
    if any(
        candles[index].timestamp >= candles[index + 1].timestamp
        for index in range(len(candles) - 1)
    ):
        raise ValueError("candles must be strictly chronological")
    if candles and {candle.market for candle in candles} != {"KRW-BTC"}:
        raise ValueError("opportunity candidates require KRW-BTC")


def _contiguous_segments(candles: Sequence[Candle]) -> list[tuple[int, int]]:
    if not candles:
        return []
    starts = [0]
    starts.extend(
        index
        for index in range(1, len(candles))
        if candles[index].timestamp - candles[index - 1].timestamp != SOURCE_DELTA
    )
    return [
        (start, starts[index + 1] if index + 1 < len(starts) else len(candles))
        for index, start in enumerate(starts)
    ]
