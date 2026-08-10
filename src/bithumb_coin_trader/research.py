"""Reusable, side-effect-free walk-forward research orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean
from typing import Any, Callable, Generic, Sequence, TypeVar


Observation = TypeVar("Observation")
Strategy = TypeVar("Strategy")
Result = TypeVar("Result")


class ResearchError(ValueError):
    """Raised when a walk-forward experiment is configured incorrectly."""


@dataclass(frozen=True, slots=True)
class WalkForwardFold(Generic[Result]):
    fold: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    result: Result


@dataclass(frozen=True, slots=True)
class ProjectResearchReport:
    """Aggregate metrics across independent chronological test folds."""

    folds: tuple[WalkForwardFold[Any], ...]
    compounded_return: float
    maximum_drawdown: float
    mean_sharpe: float
    trade_count: int
    weighted_win_rate: float
    oos_equity_curve: tuple[float, ...]


StrategyFactory = Callable[[Sequence[Observation]], Strategy]
Backtest = Callable[[Strategy, Sequence[Observation]], Result]


def walk_forward(
    observations: Sequence[Observation],
    *,
    train_size: int,
    test_size: int,
    strategy_factory: StrategyFactory[Observation, Strategy],
    backtest: Backtest[Strategy, Observation, Result],
    step_size: int | None = None,
    expanding: bool = False,
) -> list[WalkForwardFold[Result]]:
    """Train on past observations and evaluate only on later observations.

    The callbacks keep this orchestration independent of any particular
    strategy/backtest implementation.  Project adapters can pass their APIs
    directly without this module importing live-trading code.
    """

    for name, value in (("train_size", train_size), ("test_size", test_size)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ResearchError(f"{name} must be a positive integer")
    if step_size is None:
        step_size = test_size
    if isinstance(step_size, bool) or not isinstance(step_size, int) or step_size <= 0:
        raise ResearchError("step_size must be a positive integer")
    if not callable(strategy_factory) or not callable(backtest):
        raise ResearchError("strategy_factory and backtest must be callable")

    folds: list[WalkForwardFold[Result]] = []
    test_start = train_size
    while test_start + test_size <= len(observations):
        train_start = 0 if expanding else test_start - train_size
        train_end = test_start
        test_end = test_start + test_size
        train = observations[train_start:train_end]
        test = observations[test_start:test_end]
        strategy = strategy_factory(train)
        result = backtest(strategy, test)
        folds.append(
            WalkForwardFold(
                fold=len(folds),
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                result=result,
            )
        )
        test_start += step_size
    return folds


def project_adapters(
    *,
    parameters: Any = None,
    settings: Any = None,
    allow_short: bool = False,
) -> tuple[Callable[..., Any], Callable[..., Any]]:
    """Load optional project strategy/backtest adapters only when requested.

    Keeping these imports inside the function lets the data and research layers
    remain usable while those modules are absent, and avoids importing any live
    execution surface during offline research.
    """

    try:
        from .backtest import Backtester
        from .strategy import TrendBreakoutStrategy
    except (ImportError, AttributeError) as exc:
        raise ResearchError(
            "project adapters require strategy.TrendBreakoutStrategy and backtest.Backtester"
        ) from exc

    def build_strategy(train: Sequence[Any]) -> tuple[Any, tuple[Any, ...]]:
        # Preserve history so indicators are warm at the test boundary without
        # exposing any later test observation to an earlier signal.
        return TrendBreakoutStrategy(parameters), tuple(train)

    def run_backtest(strategy_and_train: tuple[Any, tuple[Any, ...]], test: Sequence[Any]) -> Any:
        if len(test) < 2:
            raise ResearchError("project backtests require at least two test candles")
        strategy, train = strategy_and_train
        if not train:
            raise ResearchError("project backtests require training context")
        combined = (*train, *test)
        signals = strategy.generate(combined)
        # Prepend the final training candle as execution context. Backtester
        # consumes its close signal at index 0 and executes it at the first OOS
        # candle's open (index 1), while all marked returns remain OOS.
        execution_candles = (train[-1], *test)
        execution_signals = signals[len(train) - 1 :]
        return Backtester(settings, allow_short=allow_short).run(
            execution_candles,
            execution_signals,
        )

    return build_strategy, run_backtest


def run_chronological_research(
    candles: Sequence[Any],
    *,
    train_size: int = 400,
    test_size: int = 100,
    parameters: Any = None,
    settings: Any = None,
    allow_short: bool = False,
) -> ProjectResearchReport:
    """Run fixed-parameter project backtests over non-overlapping test folds."""

    strategy_factory, backtest = project_adapters(
        parameters=parameters,
        settings=settings,
        allow_short=allow_short,
    )
    folds = walk_forward(
        candles,
        train_size=train_size,
        test_size=test_size,
        step_size=test_size,
        strategy_factory=strategy_factory,
        backtest=backtest,
    )
    if not folds:
        raise ResearchError("not enough candles for one complete train/test fold")

    results = [fold.result for fold in folds]
    trade_count = sum(result.trade_count for result in results)
    weighted_wins = sum(result.win_rate * result.trade_count for result in results)
    oos_equity_curve = _stitch_equity_curves([result.equity_curve for result in results])
    return ProjectResearchReport(
        folds=tuple(folds),
        compounded_return=oos_equity_curve[-1] / oos_equity_curve[0] - 1.0,
        maximum_drawdown=_maximum_drawdown(oos_equity_curve),
        mean_sharpe=fmean(result.sharpe for result in results),
        trade_count=trade_count,
        weighted_win_rate=weighted_wins / trade_count if trade_count else 0.0,
        oos_equity_curve=oos_equity_curve,
    )


def _stitch_equity_curves(curves: Sequence[Sequence[float]]) -> tuple[float, ...]:
    if not curves or not curves[0] or curves[0][0] <= 0:
        raise ResearchError("fold equity curves must start with positive equity")
    stitched = [float(curves[0][0])]
    for curve in curves:
        if not curve or curve[0] <= 0:
            raise ResearchError("fold equity curves must start with positive equity")
        scale = stitched[-1] / curve[0]
        stitched.extend(float(value) * scale for value in curve[1:])
    return tuple(stitched)


def _maximum_drawdown(curve: Sequence[float]) -> float:
    peak = curve[0]
    maximum = 0.0
    for value in curve:
        peak = max(peak, value)
        maximum = max(maximum, (peak - value) / peak)
    return maximum
