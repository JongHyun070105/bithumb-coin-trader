"""Frozen LONG/FLAT candidates for completed Bithumb KRW-BTC daily candles.

Every decision is made only from a completed Sunday candle in Korea Standard
Time.  The signal is written on that Sunday candle, so the shared backtester
can execute a changed position no earlier than the following daily open.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from math import sqrt
from statistics import pstdev
from typing import Callable, Protocol, Sequence

from .models import Candle, Signal


KST = timezone(timedelta(hours=9))
DAILY_DELTA = timedelta(days=1)
SUNDAY = 6

WeeklyDecision = Callable[[int, Sequence[float], Signal], Signal]


class DailyCandidate(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def required_history_bars(self) -> int: ...

    def generate(self, candles: Sequence[Candle], **kwargs: object) -> list[Signal]: ...


@dataclass(frozen=True, slots=True)
class BuyHoldBenchmark:
    """Enter at the first eligible weekly decision and never exit."""

    name: str = "daily_buy_hold_benchmark"
    required_history_bars: int = 1

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        return _generate_weekly(candles, lambda _index, _closes, _state: Signal.LONG)


@dataclass(frozen=True, slots=True)
class WeeklyAbsoluteMomentumStrategy:
    """Enter on positive 126-day momentum above SMA126; exit on 63-day momentum."""

    name: str = "daily_weekly_absolute_momentum_126_63"
    entry_lookback_days: int = 126
    sma_days: int = 126
    exit_lookback_days: int = 63

    def __post_init__(self) -> None:
        if (self.entry_lookback_days, self.sma_days, self.exit_lookback_days) != (
            126,
            126,
            63,
        ):
            raise ValueError("daily absolute-momentum parameters are frozen")

    @property
    def required_history_bars(self) -> int:
        return max(self.entry_lookback_days + 1, self.sma_days, self.exit_lookback_days + 1)

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        def decide(index: int, closes: Sequence[float], state: Signal) -> Signal:
            if index + 1 < self.required_history_bars:
                return Signal.FLAT
            close = closes[index]
            entry = (
                close > closes[index - self.entry_lookback_days]
                and close > _mean(closes[index - self.sma_days + 1 : index + 1])
            )
            exit_now = close <= closes[index - self.exit_lookback_days]
            if state is Signal.LONG and exit_now:
                return Signal.FLAT
            if state is Signal.FLAT and entry:
                return Signal.LONG
            return state

        return _generate_weekly(candles, decide)


@dataclass(frozen=True, slots=True)
class WeeklySmaCrossStrategy:
    """Weekly-observed 50/200-day simple moving-average trend strategy."""

    name: str = "daily_weekly_sma_50_200"
    fast_days: int = 50
    slow_days: int = 200

    def __post_init__(self) -> None:
        if (self.fast_days, self.slow_days) != (50, 200):
            raise ValueError("daily SMA parameters are frozen")

    @property
    def required_history_bars(self) -> int:
        return self.slow_days

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        def decide(index: int, closes: Sequence[float], _state: Signal) -> Signal:
            if index + 1 < self.required_history_bars:
                return Signal.FLAT
            fast = _mean(closes[index - self.fast_days + 1 : index + 1])
            slow = _mean(closes[index - self.slow_days + 1 : index + 1])
            return Signal.LONG if fast > slow else Signal.FLAT

        return _generate_weekly(candles, decide)


@dataclass(frozen=True, slots=True)
class WeeklyDonchianStrategy:
    """Enter a prior-90-day high breakout and exit a prior-30-day low break."""

    name: str = "daily_weekly_donchian_90_30"
    entry_days: int = 90
    exit_days: int = 30

    def __post_init__(self) -> None:
        if (self.entry_days, self.exit_days) != (90, 30):
            raise ValueError("daily Donchian parameters are frozen")

    @property
    def required_history_bars(self) -> int:
        return max(self.entry_days, self.exit_days) + 1

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        highs = [candle.high for candle in candles]
        lows = [candle.low for candle in candles]

        def decide(index: int, closes: Sequence[float], state: Signal) -> Signal:
            if index + 1 < self.required_history_bars:
                return Signal.FLAT
            close = closes[index]
            if state is Signal.LONG and close < min(lows[index - self.exit_days : index]):
                return Signal.FLAT
            if state is Signal.FLAT and close > max(highs[index - self.entry_days : index]):
                return Signal.LONG
            return state

        return _generate_weekly(candles, decide)


@dataclass(frozen=True, slots=True)
class WeeklyDualMomentumStrategy:
    """Require positive 42/168-day momentum under a fixed 80% vol ceiling."""

    name: str = "daily_weekly_dual_momentum_42_168_vol80"
    fast_days: int = 42
    slow_days: int = 168
    volatility_days: int = 42
    annualized_volatility_cap: float = 0.80

    def __post_init__(self) -> None:
        if (
            self.fast_days,
            self.slow_days,
            self.volatility_days,
            self.annualized_volatility_cap,
        ) != (42, 168, 42, 0.80):
            raise ValueError("daily dual-momentum parameters are frozen")

    @property
    def required_history_bars(self) -> int:
        return max(self.slow_days, self.volatility_days) + 1

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        def decide(index: int, closes: Sequence[float], _state: Signal) -> Signal:
            if index + 1 < self.required_history_bars:
                return Signal.FLAT
            returns = [
                closes[position] / closes[position - 1] - 1.0
                for position in range(index - self.volatility_days + 1, index + 1)
            ]
            annualized_volatility = pstdev(returns) * sqrt(365.0)
            positive_momentum = (
                closes[index] > closes[index - self.fast_days]
                and closes[index] > closes[index - self.slow_days]
            )
            return (
                Signal.LONG
                if positive_momentum
                and annualized_volatility <= self.annualized_volatility_cap
                else Signal.FLAT
            )

        return _generate_weekly(candles, decide)


def daily_candidate_factories() -> dict[str, Callable[[], DailyCandidate]]:
    """Return a new frozen strategy instance for each registered candidate."""

    factories: tuple[Callable[[], DailyCandidate], ...] = (
        BuyHoldBenchmark,
        WeeklyAbsoluteMomentumStrategy,
        WeeklySmaCrossStrategy,
        WeeklyDonchianStrategy,
        WeeklyDualMomentumStrategy,
    )
    return {factory().name: factory for factory in factories}


def _generate_weekly(candles: Sequence[Candle], decide: WeeklyDecision) -> list[Signal]:
    _validate_daily_candles(candles)
    closes = [candle.close for candle in candles]
    signals: list[Signal] = []
    state = Signal.FLAT
    for index, candle in enumerate(candles):
        if candle.timestamp.astimezone(KST).weekday() == SUNDAY:
            state = decide(index, closes, state)
            if state not in (Signal.FLAT, Signal.LONG):
                raise ValueError("daily candidates may emit only LONG or FLAT")
        signals.append(state)
    return signals


def _validate_daily_candles(candles: Sequence[Candle]) -> None:
    for index, candle in enumerate(candles):
        if candle.market != "KRW-BTC":
            raise ValueError("daily candidates require market KRW-BTC")
        local = candle.timestamp.astimezone(KST)
        if (local.hour, local.minute, local.second, local.microsecond) != (0, 0, 0, 0):
            raise ValueError("daily candles must begin at KST midnight")
        if index and candle.timestamp - candles[index - 1].timestamp != DAILY_DELTA:
            raise ValueError("daily candles must be strictly chronological without gaps")


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)
