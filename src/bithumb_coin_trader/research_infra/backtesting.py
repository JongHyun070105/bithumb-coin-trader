"""Authoritative single-market candle backtest interface for new research.

Strategies provide point-in-time target weights. This adapter binds a mandatory
explicit spot cost scenario to the portfolio/accounting engine. Specialized
signal, multi-asset and order-book simulators remain available for their
historical or data-specific use cases, but new single-market spot experiments
should enter through this interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..config import TradingSettings
from ..models import Candle
from ..rebalance_backtest import RebalanceBacktestResult, RebalanceBacktester
from .costs import SpotCostScenario


@dataclass(frozen=True, slots=True)
class SpotResearchRun:
    engine_id: str
    result: RebalanceBacktestResult
    cost_scenario: SpotCostScenario

    @property
    def execution_complete(self) -> bool:
        return not self.result.unsupported_execution_semantics


class SpotResearchBacktester:
    """Run one long-only spot target-weight strategy with explicit costs."""

    ENGINE_ID = "rebalance_backtester.v1"

    def __init__(self, settings: TradingSettings | None = None) -> None:
        self._engine = RebalanceBacktester(settings)

    def run(
        self,
        candles: Sequence[Candle],
        target_weights: Sequence[float],
        *,
        cost_scenario: SpotCostScenario,
        validate_daily: bool = False,
    ) -> SpotResearchRun:
        result = self._engine.run(
            candles,
            target_weights,
            validate_daily=validate_daily,
            cost_scenario=cost_scenario,
        )
        return SpotResearchRun(
            engine_id=self.ENGINE_ID,
            result=result,
            cost_scenario=cost_scenario,
        )
