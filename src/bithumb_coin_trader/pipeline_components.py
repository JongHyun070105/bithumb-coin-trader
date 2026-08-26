"""True 4-Level Pipeline Components for Cross-Sectional Intraday Strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any, Mapping, Sequence

from .market_registry import get_market_metadata
from .models import Candle


@dataclass(frozen=True, slots=True)
class PipelineStepResult:
    timestamp: datetime
    universe_membership: tuple[str, ...]
    ranking_scores: dict[str, float]
    ranking_order: tuple[str, ...]
    target_weights: dict[str, float]

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "universe_membership": list(self.universe_membership),
            "ranking_scores": {k: round(v, 6) for k, v in sorted(self.ranking_scores.items())},
            "ranking_order": list(self.ranking_order),
            "target_weights": {k: round(v, 6) for k, v in sorted(self.target_weights.items())},
        }


class RealPipelineEngine:
    """Deterministic 4-level pipeline engine."""

    def __init__(
        self,
        *,
        top_universe_n: int = 5,
        top_select_k: int = 2,
        per_asset_target: float = 0.15,
        lookback_bars: int = 14,
        min_listing_days: int = 30,
    ) -> None:
        self.top_universe_n = top_universe_n
        self.top_select_k = top_select_k
        self.per_asset_target = per_asset_target
        self.lookback_bars = lookback_bars
        self.min_listing_days = min_listing_days

    def generate_step(
        self,
        timestamp: datetime,
        historical_candles_by_market: Mapping[str, Sequence[Candle]],
    ) -> PipelineStepResult:
        # Step 1: Universe Selection (point-in-time tradable and rolling volume)
        eligible_volumes: dict[str, float] = {}
        for m, c_list in historical_candles_by_market.items():
            if not c_list:
                continue
            meta = get_market_metadata(m)
            if not meta.is_eligible_for_new_entry(timestamp, min_listing_days=self.min_listing_days):
                continue
            # Calculate 30-bar rolling notional volume
            recent = c_list[-min(len(c_list), 30):]
            vol_krw = sum(c.volume * c.close for c in recent)
            eligible_volumes[m] = vol_krw

        sorted_by_vol = sorted(eligible_volumes.keys(), key=lambda m: eligible_volumes[m], reverse=True)
        universe = tuple(sorted_by_vol[: self.top_universe_n])

        # Step 2: Cross-Sectional Momentum Ranking
        scores: dict[str, float] = {}
        for m in universe:
            c_list = historical_candles_by_market[m]
            if len(c_list) >= self.lookback_bars:
                p_now = c_list[-1].close
                p_past = c_list[-self.lookback_bars].close
                mom = (p_now - p_past) / p_past if p_past > 0 else 0.0
                scores[m] = mom
            else:
                scores[m] = 0.0

        ranking_order = tuple(sorted(scores.keys(), key=lambda m: scores[m], reverse=True))

        # Step 3: Portfolio Target Weight Generation
        target_weights: dict[str, float] = {m: 0.0 for m in historical_candles_by_market}
        selected = ranking_order[: self.top_select_k]
        for m in selected:
            target_weights[m] = self.per_asset_target

        return PipelineStepResult(
            timestamp=timestamp,
            universe_membership=universe,
            ranking_scores=scores,
            ranking_order=ranking_order,
            target_weights=target_weights,
        )
