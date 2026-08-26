"""Frozen daily target-weight candidates for the V3 research lane."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import pstdev
from typing import Callable, Protocol, Sequence

from .daily_strategy_candidates import (
    WeeklyAbsoluteMomentumStrategy,
    WeeklyDonchianStrategy,
    WeeklySmaCrossStrategy,
    _validate_daily_candles,
)
from .models import Candle, Signal


E9_PERIODS = (5, 10, 20, 30, 60, 90, 150, 250, 360)


class TargetWeightCandidate(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def required_history_bars(self) -> int: ...

    def generate(self, candles: Sequence[Candle]) -> list[float]: ...


@dataclass(frozen=True, slots=True)
class E9DonchianVolatilityStrategy:
    """Nine equal-weight breakouts with monotone mid-channel stops."""

    name: str = "v3_e9_donchian_90d_vol25"
    periods: tuple[int, ...] = E9_PERIODS
    volatility_days: int = 90
    volatility_target: float = 0.25
    volatility_rebalance_threshold: float = 0.20

    def __post_init__(self) -> None:
        if (
            self.periods != E9_PERIODS
            or self.volatility_days != 90
            or self.volatility_target != 0.25
            or self.volatility_rebalance_threshold != 0.20
        ):
            raise ValueError("E9 parameters are frozen")

    @property
    def required_history_bars(self) -> int:
        return max(max(self.periods), self.volatility_days + 1)

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        _validate_daily_candles(candles)
        closes = [candle.close for candle in candles]
        active = [False] * len(self.periods)
        stops: list[float | None] = [None] * len(self.periods)
        previous_weight = 0.0
        previous_count = 0
        targets: list[float] = []

        for index, close in enumerate(closes):
            for model_index, period in enumerate(self.periods):
                if index + 1 < period:
                    continue
                window = closes[index - period + 1 : index + 1]
                upper = max(window)
                midpoint = (upper + min(window)) / 2.0
                active[model_index], stops[model_index] = self._next_model_state(
                    active=active[model_index],
                    prior_stop=stops[model_index],
                    close=close,
                    upper=upper,
                    midpoint=midpoint,
                )

            count = sum(active)
            scale = self._volatility_scale(closes, index)
            raw_weight = min(1.0, count / len(self.periods) * scale)
            if count == previous_count and abs(raw_weight - previous_weight) < 0.20:
                weight = previous_weight
            else:
                weight = raw_weight
            targets.append(weight)
            previous_weight = weight
            previous_count = count
        return targets

    @staticmethod
    def _next_model_state(
        *,
        active: bool,
        prior_stop: float | None,
        close: float,
        upper: float,
        midpoint: float,
    ) -> tuple[bool, float | None]:
        # The frozen paper rule gives a fresh channel breakout precedence over
        # the trailing-stop branch, including for an already active model.
        if close >= upper:
            return True, midpoint if not active else max(prior_stop or midpoint, midpoint)
        if active and prior_stop is not None and close <= prior_stop:
            return False, None
        if active:
            return True, max(prior_stop or midpoint, midpoint)
        return False, None

    def _volatility_scale(self, closes: Sequence[float], index: int) -> float:
        if index < self.volatility_days:
            return 0.0
        returns = [
            closes[position] / closes[position - 1] - 1.0
            for position in range(index - self.volatility_days + 1, index + 1)
        ]
        annualized = pstdev(returns) * sqrt(365.0)
        if annualized <= 0:
            return 1.0
        return min(1.0, self.volatility_target / annualized)


@dataclass(frozen=True, slots=True)
class EntryVolatilityAbsoluteMomentumStrategy:
    """V2 momentum state with an RV28 allocation frozen for each holding."""

    name: str = "v3_absolute_momentum_126_63_entry_vol20"
    volatility_days: int = 28
    volatility_target: float = 0.20
    maximum_weight: float = 0.50

    def __post_init__(self) -> None:
        if (
            self.volatility_days,
            self.volatility_target,
            self.maximum_weight,
        ) != (28, 0.20, 0.50):
            raise ValueError("entry-volatility momentum parameters are frozen")

    @property
    def required_history_bars(self) -> int:
        return WeeklyAbsoluteMomentumStrategy().required_history_bars

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        signals = WeeklyAbsoluteMomentumStrategy().generate(candles)
        closes = [candle.close for candle in candles]
        weight = 0.0
        targets: list[float] = []
        prior = Signal.FLAT
        for index, signal in enumerate(signals):
            if signal is Signal.LONG and prior is Signal.FLAT:
                returns = [
                    closes[position] / closes[position - 1] - 1.0
                    for position in range(index - self.volatility_days + 1, index + 1)
                ]
                annualized = pstdev(returns) * sqrt(365.0)
                weight = (
                    self.maximum_weight
                    if annualized <= 0
                    else min(self.maximum_weight, self.volatility_target / annualized)
                )
            elif signal is Signal.FLAT:
                weight = 0.0
            targets.append(weight)
            prior = signal
        return targets


@dataclass(frozen=True, slots=True)
class MajorityTrendStrategy:
    """Allocate 30% when at least two of three frozen V2 trends are LONG."""

    name: str = "v3_frozen_majority_2_of_3"
    required_votes: int = 2
    target_weight: float = 0.30

    def __post_init__(self) -> None:
        if (self.required_votes, self.target_weight) != (2, 0.30):
            raise ValueError("majority-trend parameters are frozen")

    @property
    def required_history_bars(self) -> int:
        return max(
            WeeklyAbsoluteMomentumStrategy().required_history_bars,
            WeeklySmaCrossStrategy().required_history_bars,
            WeeklyDonchianStrategy().required_history_bars,
        )

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        signal_sets = (
            WeeklyAbsoluteMomentumStrategy().generate(candles),
            WeeklySmaCrossStrategy().generate(candles),
            WeeklyDonchianStrategy().generate(candles),
        )
        return [
            self.target_weight
            if sum(signals[index] is Signal.LONG for signals in signal_sets)
            >= self.required_votes
            else 0.0
            for index in range(len(candles))
        ]


def strategy_v3_candidate_factories() -> dict[str, Callable[[], TargetWeightCandidate]]:
    factories: tuple[Callable[[], TargetWeightCandidate], ...] = (
        E9DonchianVolatilityStrategy,
        EntryVolatilityAbsoluteMomentumStrategy,
        MajorityTrendStrategy,
    )
    return {factory().name: factory for factory in factories}
