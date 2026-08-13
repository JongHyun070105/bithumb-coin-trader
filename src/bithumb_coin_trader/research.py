"""Reusable, side-effect-free walk-forward research orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean
from typing import Any, Callable, Generic, Mapping, Sequence, TypeVar


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
    candidate_name: str = "trend_breakout"


@dataclass(frozen=True, slots=True)
class CandidateComparisonReport:
    """Like-for-like OOS results for fixed, pre-registered hypotheses."""

    candidates: tuple[ProjectResearchReport, ...]
    candidate_count: int
    fold_boundaries: tuple[tuple[int, int, int, int], ...]


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
    candidate_factory: Callable[[], Any] | None = None,
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
        strategy = (
            candidate_factory()
            if candidate_factory is not None
            else TrendBreakoutStrategy(parameters)
        )
        return strategy, tuple(train)

    def run_backtest(strategy_and_train: tuple[Any, tuple[Any, ...]], test: Sequence[Any]) -> Any:
        # Prepend the final training candle as execution context. Backtester
        # consumes its close signal at index 0 and executes it at the first OOS
        # candle's open (index 1), while all marked returns remain OOS.
        execution_candles, execution_signals = _prepare_execution_window(
            strategy_and_train,
            test,
        )
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
    candidate_name: str = "trend_breakout",
    candidate_factory: Callable[[], Any] | None = None,
    continuous_oos: bool = False,
    expanding: bool = False,
) -> ProjectResearchReport:
    """Run fixed-parameter project backtests over non-overlapping test folds.

    ``continuous_oos`` keeps execution state across contiguous test folds while
    still rebuilding the strategy from each fold's training window. The legacy
    independent-fold behavior remains the default for the daily baseline.
    """

    strategy_factory, backtest = project_adapters(
        parameters=parameters,
        settings=settings,
        allow_short=allow_short,
        candidate_factory=candidate_factory,
    )
    if continuous_oos:
        folds = _run_continuous_oos(
            candles,
            train_size=train_size,
            test_size=test_size,
            strategy_factory=strategy_factory,
            settings=settings,
            allow_short=allow_short,
            expanding=expanding,
        )
    else:
        folds = walk_forward(
            candles,
            train_size=train_size,
            test_size=test_size,
            step_size=test_size,
            strategy_factory=strategy_factory,
            backtest=backtest,
            expanding=expanding,
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
        candidate_name=candidate_name,
    )


def _run_continuous_oos(
    candles: Sequence[Any],
    *,
    train_size: int,
    test_size: int,
    strategy_factory: Callable[[Sequence[Any]], Any],
    settings: Any,
    allow_short: bool,
    expanding: bool = False,
) -> list[WalkForwardFold[Any]]:
    """Generate fold-specific signals, then execute all contiguous OOS once."""

    prepared = walk_forward(
        candles,
        train_size=train_size,
        test_size=test_size,
        step_size=test_size,
        strategy_factory=strategy_factory,
        backtest=_prepare_execution_window,
        expanding=expanding,
    )
    if not prepared:
        return []

    execution_candles = list(prepared[0].result[0])
    execution_signals = list(prepared[0].result[1])
    for fold in prepared[1:]:
        fold_candles, fold_signals = fold.result
        if execution_candles[-1].timestamp != fold_candles[0].timestamp:
            raise ResearchError("continuous OOS folds must be contiguous")
        # The retrained strategy owns the boundary-close signal that executes
        # at this fold's first OOS open.
        execution_signals[-1] = fold_signals[0]
        execution_candles.extend(fold_candles[1:])
        execution_signals.extend(fold_signals[1:])

    from .backtest import Backtester

    backtester = Backtester(settings, allow_short=allow_short)
    continuous = backtester.run(execution_candles, execution_signals)
    folds: list[WalkForwardFold[Any]] = []
    for prepared_fold in prepared:
        start = prepared_fold.fold * test_size
        end = start + test_size
        folds.append(
            WalkForwardFold(
                fold=prepared_fold.fold,
                train_start=prepared_fold.train_start,
                train_end=prepared_fold.train_end,
                test_start=prepared_fold.test_start,
                test_end=prepared_fold.test_end,
                result=backtester.slice_result(
                    continuous,
                    execution_candles,
                    start=start,
                    end=end,
                ),
            )
        )
    return folds


def _prepare_execution_window(
    strategy_and_train: tuple[Any, tuple[Any, ...]],
    test: Sequence[Any],
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    if len(test) < 2:
        raise ResearchError("project backtests require at least two test candles")
    strategy, train = strategy_and_train
    if not train:
        raise ResearchError("project backtests require training context")
    combined = (*train, *test)
    signals = strategy.generate(combined)
    return (train[-1], *test), tuple(signals[len(train) - 1 :])


def registered_candidate_factories() -> dict[str, Callable[[], Any]]:
    """Return the fixed hypothesis registry; factories never inspect OOS data."""

    from .strategy import (
        BollingerRsiReentryStrategy,
        BollingerRsiFourHourUptrendReentryStrategy,
        BollingerRsiUptrendReentryStrategy,
        BollingerSqueezeBreakoutStrategy,
        CompletedIntervalStrategy,
        DCBollingerRsiArmedReentryStrategy,
        daily_close_above_sma140_strategy,
        daily_close_above_sma200_strategy,
        daily_sma50_above_sma200_strategy,
        daily_tsmom_365_strategy,
        dc_with_4h_sma50_uptrend_strategy,
        dc_with_daily_sma140_uptrend_strategy,
        donchian_4h_20_10_strategy,
        donchian_4h_55_20_strategy,
        donchian_daily_20_10_strategy,
        donchian_daily_55_20_strategy,
        monthly_close_above_sma10_strategy,
    )

    hourly_classes = (
        BollingerRsiReentryStrategy,
        BollingerRsiFourHourUptrendReentryStrategy,
        BollingerRsiUptrendReentryStrategy,
        BollingerSqueezeBreakoutStrategy,
    )
    factories: dict[str, Callable[[], Any]] = {
        DCBollingerRsiArmedReentryStrategy.name: DCBollingerRsiArmedReentryStrategy,
        "trend_daily_close_above_sma140": daily_close_above_sma140_strategy,
        "trend_daily_close_above_sma200": daily_close_above_sma200_strategy,
        "trend_daily_sma50_above_sma200": daily_sma50_above_sma200_strategy,
        "donchian_4h_55_20_breakout": donchian_4h_55_20_strategy,
        "donchian_4h_20_10_breakout": donchian_4h_20_10_strategy,
        "trend_daily_tsmom_365": daily_tsmom_365_strategy,
        "trend_monthly_close_above_sma10": monthly_close_above_sma10_strategy,
        "donchian_daily_55_20_breakout": donchian_daily_55_20_strategy,
        "donchian_daily_20_10_breakout": donchian_daily_20_10_strategy,
        "dc_30m_bb20_rsi14_with_4h_sma50_uptrend": dc_with_4h_sma50_uptrend_strategy,
        "dc_30m_bb20_rsi14_with_daily_sma140_uptrend": dc_with_daily_sma140_uptrend_strategy,
    }
    for strategy_class in hourly_classes:
        factories[strategy_class.name] = (
            lambda strategy_class=strategy_class: CompletedIntervalStrategy(
                strategy_class()
            )
        )
    return factories


def compare_registered_candidates(
    candles: Sequence[Any],
    *,
    train_size: int = 400,
    test_size: int = 100,
    settings: Any = None,
    candidate_names: Sequence[str] | None = None,
    expanding: bool = False,
) -> CandidateComparisonReport:
    """Compare fixed candidates on identical folds, costs, and next-open fills.

    Candidate factories take no training or test observations.  Consequently
    this adapter cannot tune a hypothesis after observing an OOS fold.
    """

    registry = registered_candidate_factories()
    selected = tuple(registry if candidate_names is None else candidate_names)
    if not selected:
        raise ResearchError("at least one candidate must be selected")
    unknown = [name for name in selected if name not in registry]
    if unknown:
        raise ResearchError(f"unknown registered candidate: {unknown[0]}")
    return compare_candidate_factories(
        candles,
        candidate_factories={name: registry[name] for name in selected},
        train_size=train_size,
        test_size=test_size,
        settings=settings,
        expanding=expanding,
    )


def compare_candidate_factories(
    candles: Sequence[Any],
    *,
    candidate_factories: Mapping[str, Callable[[], Any]],
    train_size: int = 400,
    test_size: int = 100,
    settings: Any = None,
    expanding: bool = False,
) -> CandidateComparisonReport:
    """Compare zero-argument fixed factories with one fold/cost configuration."""

    if not candidate_factories:
        raise ResearchError("at least one candidate must be selected")
    if any(not name or not callable(factory) for name, factory in candidate_factories.items()):
        raise ResearchError("candidate names must be non-empty and factories callable")
    reports = tuple(
        run_chronological_research(
            candles,
            train_size=train_size,
            test_size=test_size,
            settings=settings,
            allow_short=False,
            candidate_name=name,
            candidate_factory=factory,
            continuous_oos=True,
            expanding=expanding,
        )
        for name, factory in candidate_factories.items()
    )
    boundaries = tuple(
        (fold.train_start, fold.train_end, fold.test_start, fold.test_end)
        for fold in reports[0].folds
    )
    if any(
        tuple(
            (fold.train_start, fold.train_end, fold.test_start, fold.test_end)
            for fold in report.folds
        )
        != boundaries
        for report in reports[1:]
    ):
        raise ResearchError("candidate fold boundaries unexpectedly differ")
    return CandidateComparisonReport(
        candidates=reports,
        candidate_count=len(reports),
        fold_boundaries=boundaries,
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
