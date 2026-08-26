"""Frozen daily target-weight candidates for the V4 research lane."""

from __future__ import annotations

from dataclasses import dataclass
from math import log, sqrt
from statistics import pstdev
from typing import Callable, Sequence

from .daily_strategy_candidates import _validate_daily_candles
from .models import Candle
from .strategy_v3_candidates import TargetWeightCandidate

@dataclass(frozen=True, slots=True)
class V4TrendVolatilityRegimeStrategy:
    """Regime-based trend: allocates weight based on 90-day volatility."""

    name: str = "v4_trend_volatility_regime"
    volatility_days: int = 90
    momentum_days: int = 126
    
    @property
    def required_history_bars(self) -> int:
        return max(self.volatility_days + 1, self.momentum_days + 1)

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        _validate_daily_candles(candles)
        closes = [candle.close for candle in candles]
        targets: list[float] = []
        current_weight = 0.0

        for index, candle in enumerate(candles):
            if index < self.required_history_bars - 1:
                targets.append(0.0)
                continue

            if candle.timestamp.weekday() == 6:
                returns = [
                    log(closes[i] / closes[i - 1])
                    for i in range(index - self.volatility_days + 1, index + 1)
                ]
                annualized_vol = pstdev(returns) * sqrt(365.0)

                sma126 = sum(closes[index - self.momentum_days + 1 : index + 1]) / self.momentum_days
                momentum_positive = (
                    closes[index] > closes[index - self.momentum_days] and 
                    closes[index] > sma126
                )

                if annualized_vol >= 1.00:
                    current_weight = 0.0
                elif momentum_positive:
                    if annualized_vol < 0.60:
                        current_weight = 0.40
                    else:
                        current_weight = 0.20
                else:
                    current_weight = 0.0

            targets.append(current_weight)
            
        return targets


@dataclass(frozen=True, slots=True)
class V4AdaptiveDonchianAtrStrategy:
    """Donchian channel breakout with ATR trailing stop."""

    name: str = "v4_adaptive_donchian_atr"
    entry_days: int = 60
    exit_days: int = 30
    atr_days: int = 20
    atr_multiplier: float = 3.0
    target_weight: float = 0.30
    
    @property
    def required_history_bars(self) -> int:
        return max(self.entry_days + 1, self.exit_days + 1, self.atr_days + 1)

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        _validate_daily_candles(candles)
        targets: list[float] = []
        current_weight = 0.0
        trailing_stop: float | None = None
        in_position = False

        for index, candle in enumerate(candles):
            if index < self.required_history_bars - 1:
                targets.append(0.0)
                continue

            if candle.timestamp.weekday() == 6:
                # Calculate ATR20
                tr_list = []
                for i in range(index - self.atr_days + 1, index + 1):
                    tr = max(
                        candles[i].high - candles[i].low,
                        abs(candles[i].high - candles[i-1].close),
                        abs(candles[i].low - candles[i-1].close)
                    )
                    tr_list.append(tr)
                atr = sum(tr_list) / self.atr_days

                entry_high = max(c.high for c in candles[index - self.entry_days : index])
                exit_low = min(c.low for c in candles[index - self.exit_days : index])
                
                close = candle.close

                if not in_position:
                    if close > entry_high:
                        in_position = True
                        current_weight = self.target_weight
                        trailing_stop = close - (self.atr_multiplier * atr)
                else:
                    if trailing_stop is not None and close < trailing_stop:
                        in_position = False
                        current_weight = 0.0
                        trailing_stop = None
                    elif close < exit_low:
                        in_position = False
                        current_weight = 0.0
                        trailing_stop = None
                    else:
                        new_stop = close - (self.atr_multiplier * atr)
                        if trailing_stop is None or new_stop > trailing_stop:
                            trailing_stop = new_stop

            targets.append(current_weight)

        return targets


@dataclass(frozen=True, slots=True)
class V4KamaTrendStrategy:
    """Kaufman Adaptive Moving Average trend."""

    name: str = "v4_kama_trend"
    kama_n: int = 10
    kama_fast: int = 2
    kama_slow: int = 30
    momentum_days: int = 63
    target_weight: float = 0.30
    
    @property
    def required_history_bars(self) -> int:
        return max(self.kama_n + 1, self.momentum_days + 1)

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        _validate_daily_candles(candles)
        closes = [candle.close for candle in candles]
        targets: list[float] = []
        current_weight = 0.0

        fast_sc = 2.0 / (self.kama_fast + 1)
        slow_sc = 2.0 / (self.kama_slow + 1)

        kama: list[float] = []

        for index, candle in enumerate(candles):
            if index < self.kama_n:
                if index == self.kama_n - 1:
                    kama.append(closes[index])
                else:
                    kama.append(0.0)
            else:
                change = abs(closes[index] - closes[index - self.kama_n])
                volatility = sum(abs(closes[i] - closes[i - 1]) for i in range(index - self.kama_n + 1, index + 1))
                er = change / volatility if volatility != 0 else 0.0
                sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
                
                prev_kama = kama[index - 1]
                new_kama = prev_kama + sc * (closes[index] - prev_kama)
                kama.append(new_kama)

            if index < self.required_history_bars - 1:
                targets.append(0.0)
                continue

            if candle.timestamp.weekday() == 6:
                if closes[index] > kama[index] and closes[index] > closes[index - self.momentum_days]:
                    current_weight = self.target_weight
                else:
                    current_weight = 0.0

            targets.append(current_weight)
            
        return targets


@dataclass(frozen=True, slots=True)
class V4TripleMomentumFilterStrategy:
    """Triple momentum filter: 30, 90, 252 days."""

    name: str = "v4_triple_momentum_filter"
    periods: tuple[int, ...] = (30, 90, 252)
    weight_per_period: float = 0.10
    
    @property
    def required_history_bars(self) -> int:
        return max(self.periods) + 1

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        _validate_daily_candles(candles)
        closes = [candle.close for candle in candles]
        targets: list[float] = []
        current_weight = 0.0

        for index, candle in enumerate(candles):
            if index < self.required_history_bars - 1:
                targets.append(0.0)
                continue

            if candle.timestamp.weekday() == 6:
                count = 0
                for period in self.periods:
                    if closes[index] > closes[index - period]:
                        count += 1
                current_weight = count * self.weight_per_period

            targets.append(current_weight)

        return targets


@dataclass(frozen=True, slots=True)
class V4AdxKamaConfluenceStrategy:
    """ADX 추세강도 + KAMA 적응형 이평선 합류 전략.
    
    경제적 근거: ADX > 25이면서 +DI > -DI인 구간은 추세 강도와 방향이
    모두 확인된 상태. KAMA는 횡보장에서 평평해져 가짜 돌파를 필터링.
    """
    name: str = "v4_adx_kama_confluence"
    kama_n: int = 10
    adx_period: int = 14
    adx_entry: float = 25.0
    adx_exit: float = 20.0
    target_weight: float = 0.30

    @property
    def required_history_bars(self) -> int:
        return max(self.kama_n, self.adx_period * 2) + 1

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        _validate_daily_candles(candles)
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        
        # KAMA 계산 (전체 구간)
        fast_sc = 2.0 / (2 + 1)  # fast EMA period=2
        slow_sc = 2.0 / (30 + 1)  # slow EMA period=30
        kama_vals: list[float] = [closes[0]] * len(closes)
        for i in range(1, len(closes)):
            if i < self.kama_n:
                kama_vals[i] = closes[i]
                continue
            change = abs(closes[i] - closes[i - self.kama_n])
            vol = sum(abs(closes[j] - closes[j-1]) for j in range(i - self.kama_n + 1, i + 1))
            er = change / vol if vol > 0 else 0.0
            sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
            kama_vals[i] = kama_vals[i-1] + sc * (closes[i] - kama_vals[i-1])
        
        # ADX 계산 (indicators.py의 directional_indicators 사용)
        from bithumb_coin_trader.indicators import directional_indicators
        plus_di, minus_di, adx = directional_indicators(highs, lows, closes, period=self.adx_period)
        
        current_weight = 0.0
        targets: list[float] = []
        
        for index, candle in enumerate(candles):
            if index < self.required_history_bars - 1:
                targets.append(0.0)
                continue
            if candle.timestamp.weekday() == 6:  # Sunday KST
                p_di = plus_di[index]
                m_di = minus_di[index]
                cur_adx = adx[index]
                if p_di is None or m_di is None or cur_adx is None:
                    targets.append(current_weight)
                    continue
                
                in_long = current_weight > 0
                if not in_long:
                    if closes[index] > kama_vals[index] and cur_adx > self.adx_entry and p_di > m_di:
                        current_weight = self.target_weight
                else:
                    if closes[index] < kama_vals[index] or cur_adx < self.adx_exit or m_di > p_di:
                        current_weight = 0.0
            targets.append(current_weight)
        return targets


@dataclass(frozen=True, slots=True)
class V4VolatilityAdjustedMomentumStrategy:
    """변동성 조정 모멘텀: 변동성이 역사적 상위 25% 이상이면 진입 금지.
    
    경제적 근거: 고변동성 환경에서는 모멘텀 신호가 노이즈에 묻혀 예측력이
    급감. 변동성 조건부 진입으로 횡보·패닉 구간의 가짜 돌파를 차단.
    비중은 변동성 타게팅(연간 25%)으로 시장 변동성에 역비례 조절.
    """
    name: str = "v4_volatility_adjusted_momentum"
    momentum_days: int = 90
    vol_days: int = 21
    vol_history_days: int = 252
    vol_target: float = 0.25
    vol_percentile_cap: float = 0.75  # 상위 25% 고변동성 구간 진입 금지
    max_weight: float = 0.40

    @property
    def required_history_bars(self) -> int:
        return self.momentum_days + self.vol_history_days + 1

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        from math import log, sqrt
        _validate_daily_candles(candles)
        closes = [c.close for c in candles]
        
        # 미리 log 수익률 계산
        log_rets = [0.0] + [log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
        
        current_weight = 0.0
        targets: list[float] = []
        
        for index, candle in enumerate(candles):
            if index < self.required_history_bars - 1:
                targets.append(0.0)
                continue
            if candle.timestamp.weekday() == 6:  # Sunday KST
                # 90일 모멘텀
                momentum_positive = closes[index] > closes[index - self.momentum_days]
                
                # 21일 실현변동성 (연율화)
                vol_window = log_rets[index - self.vol_days + 1: index + 1]
                from statistics import pstdev
                cur_vol = pstdev(vol_window) * sqrt(365.0)
                
                # 252일 변동성 역사 기준 현재 변동성 백분위수
                hist_vols = []
                for j in range(index - self.vol_history_days + self.vol_days, index - self.vol_days + 2):
                    if j >= self.vol_days:
                        w = log_rets[j - self.vol_days + 1: j + 1]
                        hist_vols.append(pstdev(w) * sqrt(365.0))
                if hist_vols:
                    rank = sum(v <= cur_vol for v in hist_vols) / len(hist_vols)
                else:
                    rank = 0.5
                
                not_high_vol = rank < self.vol_percentile_cap
                
                if momentum_positive and not_high_vol:
                    # 변동성 타게팅
                    weight = min(self.max_weight, self.vol_target / cur_vol) if cur_vol > 0 else self.max_weight
                    current_weight = weight
                else:
                    current_weight = 0.0
            targets.append(current_weight)
        return targets


def strategy_v4_candidate_factories() -> dict[str, Callable[[], TargetWeightCandidate]]:
    factories: tuple[Callable[[], TargetWeightCandidate], ...] = (
        V4TrendVolatilityRegimeStrategy,
        V4AdaptiveDonchianAtrStrategy,
        V4KamaTrendStrategy,
        V4TripleMomentumFilterStrategy,
        V4AdxKamaConfluenceStrategy,
        V4VolatilityAdjustedMomentumStrategy,
    )
    return {factory().name: factory for factory in factories}
