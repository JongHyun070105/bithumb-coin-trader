"""Research-only KST session, volume, and VWAP proxy candidates.

The strategies in this module consume completed KRW-BTC 30-minute OHLCV
candles only.  Every decision is made from the current or earlier candle, so a
backtester can execute it no earlier than the next candle open.  Missing source
candles reset both indicator and position state to avoid carrying information
across a gap.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import timedelta, timezone
from math import log, sqrt
from statistics import fmean, pstdev
from typing import Callable, Sequence

from .models import Candle, Signal


KST = timezone(timedelta(hours=9))
SOURCE_DELTA = timedelta(minutes=30)
CandidateFactory = Callable[[], object]


@dataclass(frozen=True, slots=True)
class SessionVolumeVwapStrategy:
    """A frozen family of selective LONG/FLAT intraday candidates."""

    name: str
    mode: str
    fast_ema_period: int
    slow_ema_period: int
    rolling_vwap_period: int
    volume_period: int
    maximum_holding_bars: int
    relative_volume_threshold: float
    entry_hours_kst: frozenset[int]

    def __post_init__(self) -> None:
        if self.mode not in {"momentum", "reclaim", "breakout", "expansion"}:
            raise ValueError("unknown session/VWAP strategy mode")
        if not self.name or not self.entry_hours_kst:
            raise ValueError("strategy name and KST entry hours are required")
        periods = (
            self.fast_ema_period,
            self.slow_ema_period,
            self.rolling_vwap_period,
            self.volume_period,
            self.maximum_holding_bars,
        )
        if any(isinstance(value, bool) or value <= 0 for value in periods):
            raise ValueError("strategy periods must be positive integers")
        if self.fast_ema_period >= self.slow_ema_period:
            raise ValueError("fast EMA period must be shorter than slow EMA period")
        if self.relative_volume_threshold <= 0:
            raise ValueError("relative-volume threshold must be positive")
        if any(hour < 0 or hour > 23 for hour in self.entry_hours_kst):
            raise ValueError("KST hours must be between zero and 23")

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        signals = [Signal.FLAT] * len(candles)
        if not candles:
            return signals

        position = Signal.FLAT
        held = 0
        entry_price = 0.0
        entry_pending = False
        fast_ema: float | None = None
        slow_ema: float | None = None
        fast_alpha = 2.0 / (self.fast_ema_period + 1.0)
        slow_alpha = 2.0 / (self.slow_ema_period + 1.0)
        history_size = max(96, self.slow_ema_period + 1, self.rolling_vwap_period + 1)
        closes: deque[float] = deque(maxlen=history_size)
        highs: deque[float] = deque(maxlen=history_size)
        lows: deque[float] = deque(maxlen=history_size)
        volumes: deque[float] = deque(maxlen=history_size)
        typicals: deque[float] = deque(maxlen=history_size)
        ranges: deque[float] = deque(maxlen=history_size)
        day_key: tuple[int, int, int] | None = None
        daily_value = 0.0
        daily_volume = 0.0
        previous_daily_vwap: float | None = None
        previous_close: float | None = None

        for index, candle in enumerate(candles):
            if index and candle.timestamp - candles[index - 1].timestamp != SOURCE_DELTA:
                position = Signal.FLAT
                held = 0
                entry_price = 0.0
                entry_pending = False
                fast_ema = None
                slow_ema = None
                closes.clear()
                highs.clear()
                lows.clear()
                volumes.clear()
                typicals.clear()
                ranges.clear()
                day_key = None
                daily_value = 0.0
                daily_volume = 0.0
                previous_daily_vwap = None
                previous_close = None

            local = candle.timestamp.astimezone(KST)
            current_day = (local.year, local.month, local.day)
            if current_day != day_key:
                day_key = current_day
                daily_value = 0.0
                daily_volume = 0.0
                previous_daily_vwap = None

            typical = (candle.high + candle.low + candle.close) / 3.0
            daily_value += typical * candle.volume
            daily_volume += candle.volume
            daily_vwap = daily_value / daily_volume if daily_volume > 0 else candle.close

            fast_ema = (
                candle.close
                if fast_ema is None
                else fast_alpha * candle.close + (1.0 - fast_alpha) * fast_ema
            )
            slow_ema = (
                candle.close
                if slow_ema is None
                else slow_alpha * candle.close + (1.0 - slow_alpha) * slow_ema
            )

            closes.append(candle.close)
            highs.append(candle.high)
            lows.append(candle.low)
            volumes.append(candle.volume)
            typicals.append(typical)
            ranges.append((candle.high - candle.low) / candle.close)

            close_values = list(closes)
            high_values = list(highs)
            low_values = list(lows)
            volume_values = list(volumes)
            typical_values = list(typicals)
            range_values = list(ranges)
            window = min(self.rolling_vwap_period, len(typical_values))
            rolling_values = typical_values[-window:]
            rolling_volumes = volume_values[-window:]
            rolling_volume = sum(rolling_volumes)
            rolling_vwap = (
                sum(value * volume for value, volume in zip(rolling_values, rolling_volumes))
                / rolling_volume
                if rolling_volume > 0
                else candle.close
            )
            prior_volumes = volume_values[:-1][-self.volume_period :]
            prior_volume_average = fmean(prior_volumes) if prior_volumes else 0.0
            relative_volume = (
                candle.volume / prior_volume_average
                if prior_volume_average > 0
                else 0.0
            )
            prior_high_24 = max(high_values[:-1][-24:], default=float("inf"))
            prior_low_12 = min(low_values[:-1][-12:], default=0.0)
            prior_ranges = range_values[:-1][-24:]
            prior_range_average = fmean(prior_ranges) if prior_ranges else 0.0
            range_expansion = (
                range_values[-1] / prior_range_average
                if prior_range_average > 0
                else 0.0
            )
            volatility_ratio = 1.0
            if self.mode in {"momentum", "expansion"}:
                short_volatility = _realized_volatility(close_values[-13:])
                long_volatility = _realized_volatility(close_values[-73:])
                volatility_ratio = (
                    short_volatility / long_volatility if long_volatility > 0 else 1.0
                )
            warmed_up = len(closes) >= max(
                self.slow_ema_period,
                self.rolling_vwap_period,
                self.volume_period + 1,
                24,
            )
            bullish_trend = fast_ema > slow_ema and candle.close > slow_ema
            above_value = candle.close > rolling_vwap and candle.close > daily_vwap
            in_entry_session = local.hour in self.entry_hours_kst

            if position is Signal.LONG:
                if entry_pending:
                    entry_price = candle.open
                    entry_pending = False
                held += 1
                hard_stop = candle.close <= entry_price * 0.985
                take_profit = candle.close >= entry_price * 1.018
                mode_exit = {
                    "momentum": candle.close < rolling_vwap * 0.995 or fast_ema < slow_ema,
                    "reclaim": candle.close < daily_vwap * 0.995 or take_profit,
                    "breakout": candle.close < rolling_vwap * 0.995 or candle.close < prior_low_12,
                    "expansion": candle.close < rolling_vwap * 0.995 or volatility_ratio > 2.2,
                }[self.mode]
                if hard_stop or mode_exit or held >= self.maximum_holding_bars:
                    position = Signal.FLAT
                    held = 0
                    entry_price = 0.0
                    entry_pending = False
            elif warmed_up and in_entry_session:
                common = relative_volume >= self.relative_volume_threshold
                entry = False
                if self.mode == "momentum":
                    entry = (
                        common
                        and bullish_trend
                        and above_value
                        and 0.45 <= volatility_ratio <= 2.0
                    )
                elif self.mode == "reclaim":
                    entry = (
                        common
                        and bullish_trend
                        and previous_close is not None
                        and previous_daily_vwap is not None
                        and previous_close <= previous_daily_vwap
                        and candle.close > daily_vwap
                        and candle.close > rolling_vwap
                    )
                elif self.mode == "breakout":
                    entry = (
                        common
                        and bullish_trend
                        and above_value
                        and candle.close > prior_high_24
                    )
                elif self.mode == "expansion":
                    entry = (
                        common
                        and bullish_trend
                        and above_value
                        and volatility_ratio < 0.9
                        and range_expansion >= 1.2
                        and candle.close > candle.open
                    )
                if entry:
                    position = Signal.LONG
                    held = 0
                    entry_price = 0.0
                    entry_pending = True

            signals[index] = position
            previous_close = candle.close
            previous_daily_vwap = daily_vwap

        return signals


def _realized_volatility(closes: Sequence[float]) -> float:
    if len(closes) < 3:
        return 0.0
    returns = [log(current / previous) for previous, current in zip(closes, closes[1:])]
    return pstdev(returns) * sqrt(48.0 * 365.0) if len(returns) > 1 else 0.0


def _validate_candles(candles: Sequence[Candle]) -> None:
    if any(candle.market != "KRW-BTC" for candle in candles):
        raise ValueError("session/VWAP candidates require KRW-BTC candles")
    if any(
        current.timestamp <= previous.timestamp
        for previous, current in zip(candles, candles[1:])
    ):
        raise ValueError("candles must be strictly chronological")


def candidate_factories() -> dict[str, CandidateFactory]:
    """Return frozen candidate factories for the chronological research runner."""

    return {
        "kst_vwap_momentum": lambda: SessionVolumeVwapStrategy(
            name="kst_vwap_momentum",
            mode="momentum",
            fast_ema_period=16,
            slow_ema_period=48,
            rolling_vwap_period=48,
            volume_period=48,
            maximum_holding_bars=24,
            relative_volume_threshold=1.05,
            entry_hours_kst=frozenset((*range(8, 16), *range(20, 24), 0)),
        ),
        "kst_vwap_reclaim": lambda: SessionVolumeVwapStrategy(
            name="kst_vwap_reclaim",
            mode="reclaim",
            fast_ema_period=12,
            slow_ema_period=48,
            rolling_vwap_period=36,
            volume_period=36,
            maximum_holding_bars=12,
            relative_volume_threshold=0.90,
            entry_hours_kst=frozenset((*range(7, 17), *range(20, 24), 0, 1)),
        ),
        "kst_relative_volume_breakout": lambda: SessionVolumeVwapStrategy(
            name="kst_relative_volume_breakout",
            mode="breakout",
            fast_ema_period=16,
            slow_ema_period=64,
            rolling_vwap_period=48,
            volume_period=48,
            maximum_holding_bars=20,
            relative_volume_threshold=1.15,
            entry_hours_kst=frozenset((*range(8, 17), *range(20, 24), 0)),
        ),
        "kst_low_vol_vwap_expansion": lambda: SessionVolumeVwapStrategy(
            name="kst_low_vol_vwap_expansion",
            mode="expansion",
            fast_ema_period=12,
            slow_ema_period=48,
            rolling_vwap_period=48,
            volume_period=36,
            maximum_holding_bars=16,
            relative_volume_threshold=1.00,
            entry_hours_kst=frozenset((*range(8, 16), *range(20, 24), 0, 1)),
        ),
    }
