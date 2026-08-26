"""Frozen strategy candidates for Strategy V5 research lane.

V5 separates alpha generation (when to buy/sell) from risk management (how much to buy),
and evaluates:
- Champion: V4 Adaptive Donchian (30% fixed)
- Challenger A: Regime-Adaptive Donchian (0%~50% dynamic allocation)
- Challenger B: BTC/ETH/XRP Cross-Asset Dual Momentum (Absolute Gate -> Relative Rank)
- Challenger C: Trend Pullback Entry with Fixed / VolTarget / Kelly sizing variants
"""

from __future__ import annotations

from dataclasses import dataclass
from math import log, sqrt
from statistics import mean, pstdev
from typing import Callable, Sequence

from .daily_strategy_candidates import KST, SUNDAY, _validate_daily_candles
from .indicators import wilder_rsi
from .models import Candle, Signal
from .strategy_v3_candidates import TargetWeightCandidate
from .strategy_v4_candidates import V4AdaptiveDonchianAtrStrategy


@dataclass(frozen=True, slots=True)
class V5RegimeAdaptiveDonchianStrategy:
    """Challenger A: Donchian 60/30 alpha engine with 4-state regime allocation.

    Market States:
    - Bull (Trend positive & Normal Vol): 40%
    - Neutral/Chop (Trend positive & Moderate Vol): 20%
    - Bear (Trend negative): 0%
    - Crash (Extreme Vol >= 0.90): 0%
    """

    name: str = "v5_regime_adaptive_donchian"
    entry_days: int = 60
    exit_days: int = 30
    atr_days: int = 20
    atr_multiplier: float = 3.0
    trend_sma_days: int = 200
    momentum_days: int = 90
    vol_days: int = 30
    bull_vol_cap: float = 0.60
    crash_vol_threshold: float = 0.90
    bull_weight: float = 0.40
    neutral_weight: float = 0.20

    def __post_init__(self) -> None:
        if (self.entry_days, self.exit_days, self.atr_days, self.atr_multiplier) != (60, 30, 20, 3.0):
            raise ValueError("V5 Regime Adaptive Donchian core alpha parameters are frozen")

    @property
    def required_history_bars(self) -> int:
        return max(self.trend_sma_days, self.momentum_days, self.vol_days, self.entry_days, self.exit_days) + 1

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        _validate_daily_candles(candles)
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        
        targets: list[float] = []
        current_weight = 0.0
        trailing_stop: float | None = None
        in_alpha_position = False

        for index, candle in enumerate(candles):
            if index < self.required_history_bars - 1:
                targets.append(0.0)
                continue

            if candle.timestamp.astimezone(KST).weekday() == SUNDAY:
                # 1. Calculate Alpha Signals (Donchian 60/30 + ATR20 Trailing Stop)
                tr_list = [
                    max(
                        highs[i] - lows[i],
                        abs(highs[i] - closes[i - 1]),
                        abs(lows[i] - closes[i - 1]),
                    )
                    for i in range(index - self.atr_days + 1, index + 1)
                ]
                atr = sum(tr_list) / self.atr_days
                entry_high = max(highs[index - self.entry_days : index])
                exit_low = min(lows[index - self.exit_days : index])
                close = closes[index]

                if not in_alpha_position:
                    if close > entry_high:
                        in_alpha_position = True
                        trailing_stop = close - (self.atr_multiplier * atr)
                else:
                    if trailing_stop is not None and close < trailing_stop:
                        in_alpha_position = False
                        trailing_stop = None
                    elif close < exit_low:
                        in_alpha_position = False
                        trailing_stop = None
                    else:
                        new_stop = close - (self.atr_multiplier * atr)
                        if trailing_stop is None or new_stop > trailing_stop:
                            trailing_stop = new_stop

                # 2. Calculate Market State & Dynamic Allocation
                if not in_alpha_position:
                    current_weight = 0.0
                else:
                    # Trend State
                    sma200 = sum(closes[index - self.trend_sma_days + 1 : index + 1]) / self.trend_sma_days
                    trend_positive = (close > sma200) and (close > closes[index - self.momentum_days])

                    # Volatility State (30d realized annualized)
                    log_rets = [
                        log(closes[i] / closes[i - 1])
                        for i in range(index - self.vol_days + 1, index + 1)
                    ]
                    realized_vol = pstdev(log_rets) * sqrt(365.0)

                    if not trend_positive or realized_vol >= self.crash_vol_threshold:
                        # Bear or Crash -> 0%
                        current_weight = 0.0
                    elif realized_vol < self.bull_vol_cap:
                        # Bull -> 40%
                        current_weight = self.bull_weight
                    else:
                        # Neutral/Chop -> 20%
                        current_weight = self.neutral_weight

            targets.append(current_weight)

        return targets


@dataclass(frozen=True, slots=True)
class V5CrossAssetDualMomentumStrategy:
    """Challenger B: Cross-Asset Dual Momentum across BTC, ETH, and XRP.

    Step 1: Absolute Momentum Filter (close > close[90d]) for all 3 assets
    Step 2: Eligible assets ranked by Risk-Adjusted Momentum (Return_90d / Vol_30d)
    Step 3: Top-ranked asset allocated 30%, or Cash if none eligible.
    """

    name: str = "v5_cross_asset_dual_momentum"
    momentum_days: int = 90
    vol_days: int = 30
    target_weight: float = 0.30
    asset_name: str = "KRW-BTC"

    @property
    def required_history_bars(self) -> int:
        return max(self.momentum_days, self.vol_days) + 1

    def generate_multi_asset(
        self,
        universe_candles: dict[str, Sequence[Candle]],
    ) -> dict[str, list[float]]:
        """Generate synchronized portfolio target weights across the universe."""
        assets = sorted(universe_candles)
        lengths = {len(candles) for candles in universe_candles.values()}
        if len(lengths) != 1:
            raise ValueError("All universe assets must have identical candle length")
        length = next(iter(lengths))

        closes_by_asset = {asset: [c.close for c in universe_candles[asset]] for asset in assets}
        weights_by_asset: dict[str, list[float]] = {asset: [] for asset in assets}
        current_selection: str | None = None

        sample_asset = assets[0]
        candles_ref = universe_candles[sample_asset]

        for index in range(length):
            candle = candles_ref[index]
            if index < self.required_history_bars - 1:
                for asset in assets:
                    weights_by_asset[asset].append(0.0)
                continue

            if candle.timestamp.astimezone(KST).weekday() == SUNDAY:
                eligible_scores: dict[str, float] = {}

                for asset in assets:
                    closes = closes_by_asset[asset]
                    cur_close = closes[index]
                    past_close = closes[index - self.momentum_days]
                    abs_mom = (cur_close / past_close) - 1.0

                    if abs_mom > 0:  # Absolute Momentum Gate Passed
                        log_rets = [
                            log(closes[i] / closes[i - 1])
                            for i in range(index - self.vol_days + 1, index + 1)
                        ]
                        vol = pstdev(log_rets) * sqrt(365.0)
                        risk_adj_mom = abs_mom / vol if vol > 0 else 0.0
                        eligible_scores[asset] = risk_adj_mom

                if eligible_scores:
                    # Select 1st rank
                    current_selection = max(eligible_scores, key=lambda a: (eligible_scores[a], a))
                else:
                    current_selection = None

            for asset in assets:
                is_selected = (asset == current_selection)
                weights_by_asset[asset].append(self.target_weight if is_selected else 0.0)

        return weights_by_asset

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        """Default single-asset interface for BTC research framework."""
        _validate_daily_candles(candles)
        closes = [c.close for c in candles]
        targets: list[float] = []
        current_weight = 0.0

        for index, candle in enumerate(candles):
            if index < self.required_history_bars - 1:
                targets.append(0.0)
                continue

            if candle.timestamp.astimezone(KST).weekday() == SUNDAY:
                cur_close = closes[index]
                past_close = closes[index - self.momentum_days]
                abs_mom = (cur_close / past_close) - 1.0

                if abs_mom > 0:
                    current_weight = self.target_weight
                else:
                    current_weight = 0.0

            targets.append(current_weight)

        return targets


@dataclass(frozen=True, slots=True)
class V5TrendPullbackStrategy:
    """Challenger C: Trend Pullback Entry with configurable sizing.

    Alpha logic:
    - Trend: close > SMA200 & close > close[60d]
    - Pullback: RSI14 < 35 OR close < EMA20
    - Re-entry confirmation: close > high[1d] OR RSI14 cross above 40
    - Exit: close < SMA200 OR close < low[30d]
    """

    name: str = "v5_trend_pullback_fixed30"
    sizing_mode: str = "fixed30"  # "fixed30", "voltarget25", "kelly025"
    sma_days: int = 200
    momentum_days: int = 60
    rsi_period: int = 14
    ema_days: int = 20
    exit_low_days: int = 30
    fixed_weight: float = 0.30
    vol_target: float = 0.25
    max_weight: float = 0.40

    @property
    def required_history_bars(self) -> int:
        return max(self.sma_days, self.momentum_days, self.rsi_period * 2, self.ema_days, self.exit_low_days) + 1

    def generate(self, candles: Sequence[Candle]) -> list[float]:
        _validate_daily_candles(candles)
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]

        # Calculate EMA20
        multiplier = 2.0 / (self.ema_days + 1)
        ema_vals = [closes[0]] * len(closes)
        for i in range(1, len(closes)):
            ema_vals[i] = (closes[i] - ema_vals[i - 1]) * multiplier + ema_vals[i - 1]

        # Calculate RSI14
        rsi_series = wilder_rsi(closes, period=self.rsi_period)

        targets: list[float] = []
        in_position = False
        pullback_detected = False
        current_weight = 0.0

        for index, candle in enumerate(candles):
            if index < self.required_history_bars - 1:
                targets.append(0.0)
                continue

            if candle.timestamp.astimezone(KST).weekday() == SUNDAY:
                close = closes[index]
                sma200 = sum(closes[index - self.sma_days + 1 : index + 1]) / self.sma_days
                trend_ok = (close > sma200) and (close > closes[index - self.momentum_days])
                exit_low = min(lows[index - self.exit_low_days : index])
                rsi_val = rsi_series[index]
                prev_rsi = rsi_series[index - 1] if index > 0 else 50.0

                if not in_position:
                    if trend_ok:
                        # Check pullback condition
                        is_pullback = (rsi_val is not None and rsi_val < 35.0) or (close < ema_vals[index])
                        if is_pullback:
                            pullback_detected = True

                        # Check re-entry trigger if pullback was primed
                        if pullback_detected:
                            re_enter = (close > highs[index - 1]) or (
                                rsi_val is not None and prev_rsi is not None and prev_rsi < 40.0 and rsi_val >= 40.0
                            )
                            if re_enter:
                                in_position = True
                                pullback_detected = False
                else:
                    # Exit condition
                    if close < sma200 or close < exit_low:
                        in_position = False
                        pullback_detected = False

                # Calculate Sizing
                if not in_position:
                    current_weight = 0.0
                else:
                    if self.sizing_mode == "fixed30":
                        current_weight = self.fixed_weight
                    elif self.sizing_mode == "voltarget25":
                        log_rets = [
                            log(closes[i] / closes[i - 1])
                            for i in range(index - 29, index + 1)
                        ]
                        cur_vol = pstdev(log_rets) * sqrt(365.0)
                        weight = self.vol_target / cur_vol if cur_vol > 0 else self.fixed_weight
                        current_weight = min(self.max_weight, max(0.10, weight))
                    elif self.sizing_mode == "kelly025":
                        # 0.25 Fractional Kelly based on trend following historical edge (winrate 45%, R 2.5)
                        w = 0.45
                        r = 2.5
                        full_kelly = w - (1 - w) / r  # 0.45 - 0.55/2.5 = 0.45 - 0.22 = 0.23
                        current_weight = max(0.0, min(self.max_weight, full_kelly * 0.5 + 0.15))
                    else:
                        current_weight = self.fixed_weight

            targets.append(current_weight)

        return targets


def strategy_v5_candidate_factories() -> dict[str, Callable[[], TargetWeightCandidate]]:
    """Return frozen V5 candidate factories (Champion + Challengers)."""
    factories: tuple[Callable[[], TargetWeightCandidate], ...] = (
        V4AdaptiveDonchianAtrStrategy,  # Champion
        V5RegimeAdaptiveDonchianStrategy,  # Challenger A
        V5CrossAssetDualMomentumStrategy,  # Challenger B
        lambda: V5TrendPullbackStrategy(name="v5_trend_pullback_fixed30", sizing_mode="fixed30"),
        lambda: V5TrendPullbackStrategy(name="v5_trend_pullback_voltarget25", sizing_mode="voltarget25"),
        lambda: V5TrendPullbackStrategy(name="v5_trend_pullback_kelly025", sizing_mode="kelly025"),
    )
    return {factory().name: factory for factory in factories}
