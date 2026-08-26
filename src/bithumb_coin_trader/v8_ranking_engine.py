"""Multi-Dimensional Cross-Sectional Ranking Engine for Strategy V8.

Computes normalized cross-sectional factors across the Point-in-Time Universe:
1. Short-to-Medium Term Momentum (14-bar return)
2. Relative Strength vs Benchmark (BTC alpha)
3. Volume Expansion Ratio (recent volume vs 20-bar SMA)
4. Trend Quality (EMA alignment and direction)
5. Volatility Risk Penalty (ATR normalized by price)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence

from .market_registry import get_market_metadata
from .models import Candle


@dataclass(frozen=True, slots=True)
class V8AssetFactorScores:
    market: str
    momentum: float
    relative_strength_vs_btc: float
    volume_expansion: float
    trend_quality: float
    volatility_risk: float
    composite_score: float

    def to_dict(self) -> dict[str, float]:
        return {
            "momentum": round(self.momentum, 6),
            "rs_vs_btc": round(self.relative_strength_vs_btc, 6),
            "vol_expansion": round(self.volume_expansion, 6),
            "trend_quality": round(self.trend_quality, 6),
            "vol_risk": round(self.volatility_risk, 6),
            "composite": round(self.composite_score, 6),
        }


@dataclass(frozen=True, slots=True)
class V8RankingResult:
    timestamp: datetime
    universe_markets: tuple[str, ...]
    asset_scores: dict[str, V8AssetFactorScores]
    ranked_markets: tuple[str, ...]  # Sorted best to worst

    def top_n(self, n: int) -> tuple[str, ...]:
        return self.ranked_markets[:n]


class V8CrossSectionalRankingEngine:
    """Deterministic Multi-Factor Cross-Sectional Ranking Engine."""

    def __init__(
        self,
        *,
        momentum_lookback: int = 14,
        volume_lookback: int = 20,
        trend_ema_span: int = 20,
        weight_momentum: float = 0.35,
        weight_relative_strength: float = 0.25,
        weight_volume_expansion: float = 0.20,
        weight_trend_quality: float = 0.20,
        penalty_volatility_risk: float = 0.10,
    ) -> None:
        self.momentum_lookback = momentum_lookback
        self.volume_lookback = volume_lookback
        self.trend_ema_span = trend_ema_span
        self.w_mom = weight_momentum
        self.w_rs = weight_relative_strength
        self.w_vol = weight_volume_expansion
        self.w_trend = weight_trend_quality
        self.p_vol_risk = penalty_volatility_risk

    def evaluate_universe(
        self,
        timestamp: datetime,
        universe: Sequence[str],
        historical_candles_by_market: Mapping[str, Sequence[Candle]],
    ) -> V8RankingResult:
        if not universe:
            return V8RankingResult(timestamp, (), {}, ())

        # 1. Benchmark (BTC) momentum for relative strength
        btc_candles = historical_candles_by_market.get("KRW-BTC", [])
        btc_mom = 0.0
        if len(btc_candles) >= self.momentum_lookback:
            p0 = btc_candles[-self.momentum_lookback].close
            p1 = btc_candles[-1].close
            btc_mom = (p1 - p0) / p0 if p0 > 0 else 0.0

        raw_factors: dict[str, dict[str, float]] = {}

        for m in universe:
            c_list = historical_candles_by_market.get(m, [])
            if len(c_list) < max(self.momentum_lookback, self.volume_lookback) + 5:
                continue

            closes = [c.close for c in c_list]
            volumes = [c.volume for c in c_list]
            highs = [c.high for c in c_list]
            lows = [c.low for c in c_list]

            # 1. Momentum
            p_past = closes[-self.momentum_lookback]
            p_now = closes[-1]
            mom = (p_now - p_past) / p_past if p_past > 0 else 0.0

            # 2. Relative Strength vs BTC
            rs = mom - btc_mom

            # 3. Volume Expansion
            recent_vol = mean(volumes[-3:])
            baseline_vol = mean(volumes[-self.volume_lookback:])
            vol_exp = (recent_vol / baseline_vol) if baseline_vol > 0 else 1.0

            # 4. Trend Quality (EMA20 position and slope)
            ema = self._calc_ema(closes, self.trend_ema_span)
            trend_q = ((p_now - ema[-1]) / ema[-1]) if ema[-1] > 0 else 0.0

            # 5. Volatility Risk (ATR normalized by price)
            atr = self._calc_atr(highs, lows, closes, 14)
            vol_risk = (atr / p_now) if p_now > 0 else 0.0

            raw_factors[m] = {
                "momentum": mom,
                "relative_strength": rs,
                "volume_expansion": vol_exp,
                "trend_quality": trend_q,
                "volatility_risk": vol_risk,
            }

        if not raw_factors:
            return V8RankingResult(timestamp, tuple(universe), {}, ())

        # Cross-sectional z-score standardization
        std_factors = self._standardize_factors(raw_factors)

        asset_scores: dict[str, V8AssetFactorScores] = {}
        for m, f in std_factors.items():
            comp = (
                self.w_mom * f["momentum"]
                + self.w_rs * f["relative_strength"]
                + self.w_vol * f["volume_expansion"]
                + self.w_trend * f["trend_quality"]
                - self.p_vol_risk * f["volatility_risk"]
            )
            raw = raw_factors[m]
            asset_scores[m] = V8AssetFactorScores(
                market=m,
                momentum=raw["momentum"],
                relative_strength_vs_btc=raw["relative_strength"],
                volume_expansion=raw["volume_expansion"],
                trend_quality=raw["trend_quality"],
                volatility_risk=raw["volatility_risk"],
                composite_score=comp,
            )

        ranked = tuple(sorted(asset_scores.keys(), key=lambda m: asset_scores[m].composite_score, reverse=True))

        return V8RankingResult(
            timestamp=timestamp,
            universe_markets=tuple(universe),
            asset_scores=asset_scores,
            ranked_markets=ranked,
        )

    def _calc_ema(self, values: Sequence[float], span: int) -> list[float]:
        alpha = 2.0 / (span + 1.0)
        ema = [values[0]]
        for v in values[1:]:
            ema.append(alpha * v + (1.0 - alpha) * ema[-1])
        return ema

    def _calc_atr(self, highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], span: int) -> float:
        trs = [highs[0] - lows[0]]
        for i in range(1, len(highs)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        return mean(trs[-span:])

    def _standardize_factors(self, raw: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
        keys = ["momentum", "relative_strength", "volume_expansion", "trend_quality", "volatility_risk"]
        std_res: dict[str, dict[str, float]] = {m: {} for m in raw}

        for k in keys:
            vals = [raw[m][k] for m in raw]
            m_val = mean(vals)
            s_val = pstdev(vals) if len(vals) > 1 else 0.0
            for m in raw:
                std_res[m][k] = (raw[m][k] - m_val) / s_val if s_val > 1e-8 else 0.0

        return std_res
