"""Frozen daily target-weight candidates for V4b research."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Sequence

from bithumb_coin_trader.daily_strategy_candidates import KST, SUNDAY, _validate_daily_candles
from bithumb_coin_trader.models import Candle, Signal


@dataclass(frozen=True, slots=True)
class V452WeekHighBreakoutStrategy:
    """52주 신고점 돌파 전략. 공급 저항 해소 및 강한 상승 추세 진입을 포착."""

    name: str = "v4_52week_high_breakout"
    lookback_high: int = 365
    lookback_exit: int = 63
    target_weight: float = 0.30

    def __post_init__(self) -> None:
        if (self.lookback_high, self.lookback_exit, self.target_weight) != (365, 63, 0.30):
            raise ValueError("52주 신고점 돌파 전략 파라미터 동결 (공급 저항 해소 및 추세 반전 확인)")

    @property
    def required_history_bars(self) -> int:
        return max(self.lookback_high, self.lookback_exit) + 1

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        _validate_daily_candles(candles)
        closes = [candle.close for candle in candles]
        state = Signal.FLAT
        weights: list[float] = []

        for index, candle in enumerate(candles):
            if candle.timestamp.astimezone(KST).weekday() == SUNDAY:
                if index + 1 < self.required_history_bars:
                    state = Signal.FLAT
                else:
                    close = closes[index]
                    high_52w = max(closes[index - self.lookback_high : index])
                    if state is Signal.FLAT and close > high_52w:
                        state = Signal.LONG
                    elif state is Signal.LONG and close < closes[index - self.lookback_exit]:
                        state = Signal.FLAT
            
            weights.append(self.target_weight if state is Signal.LONG else 0.0)

        return weights


@dataclass(frozen=True, slots=True)
class V4TrendQualityFilterStrategy:
    """추세 품질 필터 전략. 노이즈 대비 방향성이 뚜렷한 구간(t-stat)에만 진입."""

    name: str = "v4_trend_quality_filter"
    trend_quality_days: int = 30
    momentum_days: int = 90
    target_weight: float = 0.30

    def __post_init__(self) -> None:
        if (self.trend_quality_days, self.momentum_days, self.target_weight) != (30, 90, 0.30):
            raise ValueError("추세 품질 필터 파라미터 동결 (노이즈 대비 방향성 우위 및 중기 추세 확인)")

    @property
    def required_history_bars(self) -> int:
        return max(self.trend_quality_days, self.momentum_days) + 1

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        _validate_daily_candles(candles)
        closes = [candle.close for candle in candles]
        state = Signal.FLAT
        weights: list[float] = []

        for index, candle in enumerate(candles):
            if candle.timestamp.astimezone(KST).weekday() == SUNDAY:
                if index + 1 < self.required_history_bars:
                    state = Signal.FLAT
                else:
                    close = closes[index]
                    returns = [
                        math.log(closes[j] / closes[j - 1])
                        for j in range(index - self.trend_quality_days + 1, index + 1)
                    ]
                    
                    std_r = pstdev(returns)
                    n = len(returns)
                    # t-통계량: mean / (std/sqrt(n)) — Sharpe 비율이 아니라 평균이 0과 얼마나 다른지 측정
                    # 30일 기준 임계값 1.0 → 약 연율화 Sharpe 1.83 수준에 해당 (sqrt(30) ≈ 5.48)
                    trend_quality = (mean(returns) / (std_r / (n ** 0.5))) if std_r > 0 else 0.0
                    
                    if state is Signal.FLAT and trend_quality > 1.0 and close > closes[index - self.momentum_days]:
                        state = Signal.LONG
                    elif state is Signal.LONG and (trend_quality < 0.0 or close < closes[index - self.momentum_days]):
                        state = Signal.FLAT

            weights.append(self.target_weight if state is Signal.LONG else 0.0)

        return weights


from typing import Callable


def strategy_v4b_candidate_factories() -> dict[str, Callable[[], object]]:
    factories = (
        V452WeekHighBreakoutStrategy,
        V4TrendQualityFilterStrategy,
    )
    return {factory().name: factory for factory in factories}
