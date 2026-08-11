from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .indicators import ema, rolling_volatility
from .models import Candle, Signal


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
        if any(candles[index].timestamp >= candles[index + 1].timestamp for index in range(len(candles) - 1)):
            raise ValueError("candles must be strictly chronological")
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
