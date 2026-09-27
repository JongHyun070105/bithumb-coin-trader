"""Machine-readable cost sensitivity runs over the authoritative candle engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from math import sqrt
from statistics import median, pstdev
from typing import Any, Sequence

from ..backtest import BacktestResult, Backtester
from ..config import TradingSettings
from ..models import Candle, Signal
from ..rebalance_backtest import RebalanceBacktestResult
from .backtesting import SpotResearchBacktester
from .costs import SpotCostScenario


@dataclass(frozen=True, slots=True)
class CostSensitivityRow:
    scenario: dict[str, Any]
    net_return: float
    max_drawdown: float
    sharpe: float
    turnover: float
    fees: float
    slippage_cost: float
    trade_count: int
    unsupported_execution_semantics: tuple[str, ...]
    engine_id: str = "signal_backtester.legacy"

    @property
    def execution_complete(self) -> bool:
        return not self.unsupported_execution_semantics

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "execution_complete": self.execution_complete}


@dataclass(frozen=True, slots=True)
class CostSensitivityReport:
    rows: tuple[CostSensitivityRow, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "rows": [row.to_dict() for row in self.rows],
        }


def run_cost_sensitivity(
    candles: Sequence[Candle],
    signals: Sequence[Signal],
    scenarios: Sequence[SpotCostScenario],
    *,
    settings: TradingSettings | None = None,
    expected_interval: timedelta | None = None,
    target_allocations: Sequence[float] | None = None,
    max_scenarios: int = 64,
) -> CostSensitivityReport:
    """Run a bounded cost grid and retain unsupported model semantics per row."""
    if not scenarios:
        raise ValueError("at least one cost scenario is required")
    if len(scenarios) > max_scenarios:
        raise ValueError(f"cost grid exceeds the {max_scenarios}-scenario limit")
    names = [scenario.name for scenario in scenarios]
    if len(names) != len(set(names)):
        raise ValueError("cost scenario names must be unique within one report")

    rows: list[CostSensitivityRow] = []
    for scenario in scenarios:
        result: BacktestResult = Backtester(
            settings, expected_interval=expected_interval
        ).run(
            candles,
            signals,
            target_allocations=target_allocations,
            cost_scenario=scenario,
        )
        rows.append(CostSensitivityRow(
            scenario=scenario.to_dict(),
            net_return=result.total_return,
            max_drawdown=result.max_drawdown,
            sharpe=result.sharpe,
            turnover=result.turnover,
            fees=result.total_fees,
            slippage_cost=result.total_slippage_cost,
            trade_count=result.trade_count,
            unsupported_execution_semantics=result.unsupported_execution_semantics,
            engine_id="signal_backtester.legacy",
        ))
    return CostSensitivityReport(tuple(rows))


def run_target_weight_cost_sensitivity(
    candles: Sequence[Candle],
    target_weights: Sequence[float],
    scenarios: Sequence[SpotCostScenario],
    *,
    settings: TradingSettings | None = None,
    validate_daily: bool = False,
    max_scenarios: int = 64,
) -> CostSensitivityReport:
    """Run a bounded cost grid through the authoritative target-weight path."""
    if not scenarios:
        raise ValueError("at least one cost scenario is required")
    if len(scenarios) > max_scenarios:
        raise ValueError(f"cost grid exceeds the {max_scenarios}-scenario limit")
    names = [scenario.name for scenario in scenarios]
    if len(names) != len(set(names)):
        raise ValueError("cost scenario names must be unique within one report")

    rows: list[CostSensitivityRow] = []
    runner = SpotResearchBacktester(settings)
    for scenario in scenarios:
        run = runner.run(
            candles,
            target_weights,
            cost_scenario=scenario,
            validate_daily=validate_daily,
        )
        result: RebalanceBacktestResult = run.result
        returns = [
            result.equity_curve[index] / result.equity_curve[index - 1] - 1.0
            for index in range(1, len(result.equity_curve))
            if result.equity_curve[index - 1] > 0
        ]
        volatility = pstdev(returns) if len(returns) > 1 else 0.0
        intervals = [
            (candles[index].timestamp - candles[index - 1].timestamp).total_seconds()
            for index in range(1, len(candles))
        ]
        typical_seconds = median(intervals)
        periods_per_year = 365.25 * 24 * 60 * 60 / typical_seconds
        sharpe = (
            sum(returns) / len(returns) / volatility * sqrt(periods_per_year)
            if returns and volatility > 0
            else 0.0
        )
        rows.append(CostSensitivityRow(
            scenario=scenario.to_dict(),
            net_return=result.total_return,
            max_drawdown=result.max_drawdown,
            sharpe=sharpe,
            turnover=result.turnover,
            fees=result.total_fees,
            slippage_cost=sum(fill.slippage_cost for fill in result.fills),
            trade_count=sum(fill.side == "sell" for fill in result.fills),
            unsupported_execution_semantics=result.unsupported_execution_semantics,
            engine_id=run.engine_id,
        ))
    return CostSensitivityReport(tuple(rows))
