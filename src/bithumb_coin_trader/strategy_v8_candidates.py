"""Strategy V8 Candidate Families: 4 Market-Wide Cross-Sectional Intraday Families."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Any, Callable, Mapping, Sequence

from .models import Candle
from .v8_ranking_engine import V8CrossSectionalRankingEngine, V8RankingResult


class V8StrategyBase:
    """Base class for V8 Intraday Cross-Sectional Strategy Families."""

    name: str = "v8_base"
    family: str = "base"

    def compute_target_weights(
        self,
        timestamp: datetime,
        universe: Sequence[str],
        candles_15m_by_market: Mapping[str, Sequence[Candle]],
        candles_1h_by_market: Mapping[str, Sequence[Candle]],
        ranking_engine: V8CrossSectionalRankingEngine,
    ) -> dict[str, float]:
        raise NotImplementedError


# =============================================================================
# Family 1: Cross-Sectional Momentum Rotation
# =============================================================================
class V8CrossSectionalMomentumStrategy(V8StrategyBase):
    """Family 1: Selects top 2 assets by 1H multi-factor composite score and enters on 15m EMA20."""

    name = "v8_cross_sectional_momentum"
    family = "momentum_rotation"

    def __init__(self, top_k: int = 2, per_asset_target: float = 0.15, ema_span: int = 20) -> None:
        self.top_k = top_k
        self.per_asset_target = per_asset_target
        self.ema_span = ema_span

    def compute_target_weights(
        self,
        timestamp: datetime,
        universe: Sequence[str],
        candles_15m_by_market: Mapping[str, Sequence[Candle]],
        candles_1h_by_market: Mapping[str, Sequence[Candle]],
        ranking_engine: V8CrossSectionalRankingEngine,
    ) -> dict[str, float]:
        targets = {m: 0.0 for m in candles_15m_by_market}
        # Evaluate 1H cross-sectional ranking
        ranking_res = ranking_engine.evaluate_universe(timestamp, universe, candles_1h_by_market)
        top_candidates = ranking_res.top_n(self.top_k)

        for m in top_candidates:
            c15_list = candles_15m_by_market.get(m, [])
            if len(c15_list) >= self.ema_span + 2:
                closes = [c.close for c in c15_list]
                ema = self._calc_ema(closes, self.ema_span)
                if closes[-1] > ema[-1]:
                    targets[m] = self.per_asset_target

        return targets

    def _calc_ema(self, values: Sequence[float], span: int) -> list[float]:
        alpha = 2.0 / (span + 1.0)
        ema = [values[0]]
        for v in values[1:]:
            ema.append(alpha * v + (1.0 - alpha) * ema[-1])
        return ema


# =============================================================================
# Family 2: Volatility Contraction Breakout
# =============================================================================
class V8VolatilityBreakoutStrategy(V8StrategyBase):
    """Family 2: 1H Bollinger Band Squeeze + 15m Volume Breakout on Top 3 Ranked Assets."""

    name = "v8_volatility_breakout"
    family = "volatility_breakout"

    def __init__(self, top_k: int = 3, per_asset_target: float = 0.10, bb_period: int = 20) -> None:
        self.top_k = top_k
        self.per_asset_target = per_asset_target
        self.bb_period = bb_period

    def compute_target_weights(
        self,
        timestamp: datetime,
        universe: Sequence[str],
        candles_15m_by_market: Mapping[str, Sequence[Candle]],
        candles_1h_by_market: Mapping[str, Sequence[Candle]],
        ranking_engine: V8CrossSectionalRankingEngine,
    ) -> dict[str, float]:
        targets = {m: 0.0 for m in candles_15m_by_market}
        ranking_res = ranking_engine.evaluate_universe(timestamp, universe, candles_1h_by_market)
        top_candidates = ranking_res.top_n(self.top_k)

        for m in top_candidates:
            c1h = candles_1h_by_market.get(m, [])
            c15 = candles_15m_by_market.get(m, [])
            if len(c1h) < self.bb_period + 2 or len(c15) < 20:
                continue

            # 1H Bollinger Band Squeeze check
            closes_1h = [c.close for c in c1h]
            mid = mean(closes_1h[-self.bb_period:])
            std = (sum((x - mid) ** 2 for x in closes_1h[-self.bb_period:]) / self.bb_period) ** 0.5
            upper_1h = mid + 2.0 * std
            lower_1h = mid - 2.0 * std
            bandwidth = (upper_1h - lower_1h) / mid if mid > 0 else 1.0

            # 15m Breakout + Volume spike
            v15 = [c.volume for c in c15]
            avg_v15 = mean(v15[-20:])
            if c15[-1].close > upper_1h and c15[-1].volume > 1.5 * avg_v15:
                targets[m] = self.per_asset_target
            elif c15[-1].close > mid:
                targets[m] = self.per_asset_target * 0.5  # Partial continuation

        return targets


# =============================================================================
# Family 3: Market-Neutral-ish Relative Strength
# =============================================================================
class V8MarketRelativeStrengthStrategy(V8StrategyBase):
    """Family 3: BTC Macro Trend Filter (BTC > SMA50) + Top 1 Highest Relative Strength Asset."""

    name = "v8_market_relative_strength"
    family = "market_relative_strength"

    def __init__(self, per_asset_target: float = 0.15, btc_filter_period: int = 50) -> None:
        self.per_asset_target = per_asset_target
        self.btc_filter_period = btc_filter_period

    def compute_target_weights(
        self,
        timestamp: datetime,
        universe: Sequence[str],
        candles_15m_by_market: Mapping[str, Sequence[Candle]],
        candles_1h_by_market: Mapping[str, Sequence[Candle]],
        ranking_engine: V8CrossSectionalRankingEngine | None = None,
    ) -> dict[str, float]:
        targets = {m: 0.0 for m in candles_15m_by_market}

        # 1. BTC Macro Trend Filter (SMA)
        btc_candles = candles_1h_by_market.get("KRW-BTC", [])
        if len(btc_candles) < self.btc_filter_period:
            return targets

        # Fast SMA
        btc_recent = [c.close for c in btc_candles[-self.btc_filter_period:]]
        btc_sma = mean(btc_recent)
        if btc_candles[-1].close <= btc_sma:
            return targets  # Market weak: 100% Cash Defense

        # 2. Fast 14-bar Relative Strength vs BTC
        p_btc_0 = btc_candles[-14].close if len(btc_candles) >= 14 else btc_candles[0].close
        p_btc_1 = btc_candles[-1].close
        btc_mom = (p_btc_1 - p_btc_0) / p_btc_0 if p_btc_0 > 0 else 0.0

        rs_scores: dict[str, float] = {}
        for m in universe:
            if m == "KRW-BTC":
                continue
            c_list = candles_1h_by_market.get(m, [])
            if len(c_list) >= 14:
                p0 = c_list[-14].close
                p1 = c_list[-1].close
                mom = (p1 - p0) / p0 if p0 > 0 else 0.0
                rs_scores[m] = mom - btc_mom

        if rs_scores:
            best_market = max(rs_scores, key=rs_scores.get)
            if rs_scores[best_market] > 0.0:  # Must have positive relative alpha
                targets[best_market] = self.per_asset_target

        return targets


# =============================================================================
# Family 4: Trend-Aligned Short-Term Reversal
# =============================================================================
class V8TrendAlignedReversalStrategy(V8StrategyBase):
    """Family 4: 1H Trend (EMA20 > EMA50) + 15m RSI(14) Pullback Rebound on Top 3 Assets."""

    name = "v8_trend_aligned_reversal"
    family = "trend_aligned_reversal"

    def __init__(self, top_k: int = 3, per_asset_target: float = 0.10, rsi_period: int = 14) -> None:
        self.top_k = top_k
        self.per_asset_target = per_asset_target
        self.rsi_period = rsi_period

    def compute_target_weights(
        self,
        timestamp: datetime,
        universe: Sequence[str],
        candles_15m_by_market: Mapping[str, Sequence[Candle]],
        candles_1h_by_market: Mapping[str, Sequence[Candle]],
        ranking_engine: V8CrossSectionalRankingEngine,
    ) -> dict[str, float]:
        targets = {m: 0.0 for m in candles_15m_by_market}
        ranking_res = ranking_engine.evaluate_universe(timestamp, universe, candles_1h_by_market)
        top_candidates = ranking_res.top_n(self.top_k)

        for m in top_candidates:
            c1h = candles_1h_by_market.get(m, [])
            c15 = candles_15m_by_market.get(m, [])
            if len(c1h) < 55 or len(c15) < self.rsi_period + 5:
                continue

            # 1H Trend: EMA20 > EMA50
            closes_1h = [c.close for c in c1h]
            ema20 = self._calc_ema(closes_1h, 20)[-1]
            ema50 = self._calc_ema(closes_1h, 50)[-1]
            if ema20 <= ema50:
                continue  # Skip downtrends

            # 15m RSI(14) Pullback + Bullish Rebound
            closes_15 = [c.close for c in c15]
            rsi = self._calc_rsi(closes_15, self.rsi_period)
            is_bullish_bar = c15[-1].close > c15[-1].open

            if rsi < 40 and is_bullish_bar:
                targets[m] = self.per_asset_target
            elif rsi < 60:
                targets[m] = self.per_asset_target * 0.5

        return targets

    def _calc_ema(self, values: Sequence[float], span: int) -> list[float]:
        alpha = 2.0 / (span + 1.0)
        ema = [values[0]]
        for v in values[1:]:
            ema.append(alpha * v + (1.0 - alpha) * ema[-1])
        return ema

    def _calc_rsi(self, closes: Sequence[float], period: int) -> float:
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(0.0, d) for d in deltas[-period:]]
        losses = [max(0.0, -d) for d in deltas[-period:]]
        avg_gain = mean(gains) if gains else 0.0
        avg_loss = mean(losses) if losses else 0.0
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))


def v8_strategy_factories() -> dict[str, Callable[[], V8StrategyBase]]:
    return {
        "v8_cross_sectional_momentum": lambda: V8CrossSectionalMomentumStrategy(),
        "v8_volatility_breakout": lambda: V8VolatilityBreakoutStrategy(),
        "v8_market_relative_strength": lambda: V8MarketRelativeStrengthStrategy(),
        "v8_trend_aligned_reversal": lambda: V8TrendAlignedReversalStrategy(),
    }
