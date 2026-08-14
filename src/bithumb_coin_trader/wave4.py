"""Research-only Wave 4 BTC hypotheses.

This module is deliberately isolated from exchange clients, credentials, account
state, paper execution, and live execution.  It accepts only public OHLCV
``Candle`` objects and produces LONG/FLAT signals for the offline backtester.
All source data must be 30-minute KRW-BTC candles; decisions use completed
KST daily candles and the backtester fills on the following source-bar open.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
import json
from math import log, sqrt
from statistics import median, stdev
from typing import Any, Callable, Mapping, Sequence

from .backtest import Backtester
from .models import Candle, Signal
from .research import (
    CandidateComparisonReport,
    ProjectResearchReport,
    ResearchError,
    WalkForwardFold,
    walk_forward,
)
from .wave3 import (
    CandidateInnerScore,
    NestedOosResult,
    NestedWalkForwardConfig,
    SelectionDecision,
    historical_prefix,
)


KST = timezone(timedelta(hours=9))
SOURCE_MINUTES = 30
SOURCE_DELTA = timedelta(minutes=SOURCE_MINUTES)
SLOTS_PER_KST_DAY = 48
WAVE4_CANDIDATE_NAMES = (
    "daily_tsmom_84",
    "daily_tsmom_84_rv20_median_gate",
    "intraday_volume_clock_first_last_momentum",
)

CandidateBuilder = Callable[[Sequence[Candle]], "Wave4Strategy"]
# Wave 4 deliberately retains the pre-existing nested geometry, while using
# train-aware builders instead of Wave 3's zero-argument factories.
Wave4NestedConfig = NestedWalkForwardConfig


@dataclass(frozen=True, slots=True)
class DailyPoint:
    """One complete, contiguous KST daily candle derived from source bars."""

    day: date
    closes_at: datetime
    close: float


class Wave4Strategy:
    """Minimal long/flat surface used by the Wave 4 research runner."""

    name: str

    def generate(self, candles: Sequence[Candle]) -> list[Signal]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class DailyTsmom84Strategy(Wave4Strategy):
    """Long after a completed KST close exceeds the close 84 days earlier."""

    lookback_days: int = 84
    name: str = "daily_tsmom_84"

    def __post_init__(self) -> None:
        if self.lookback_days != 84:
            raise ValueError("Wave 4 TSMOM horizon is frozen at 84 KST days")

    def generate(self, candles: Sequence[Candle]) -> list[Signal]:
        runs = _complete_daily_runs(candles)
        daily_signals: dict[datetime, Signal] = {}
        for run in runs:
            for index, point in enumerate(run):
                daily_signals[point.closes_at] = (
                    Signal.LONG
                    if index >= self.lookback_days
                    and point.close > run[index - self.lookback_days].close
                    else Signal.FLAT
                )
        return _map_completed_daily_signals(candles, daily_signals)


@dataclass(frozen=True, slots=True)
class DailyTsmom84Rv20MedianGateStrategy(Wave4Strategy):
    """TSMOM-84 with a non-levered realized-volatility regime gate."""

    momentum_days: int = 84
    realized_volatility_days: int = 20
    reference_rv_days: int = 252
    annualization_days: int = 365
    name: str = "daily_tsmom_84_rv20_median_gate"

    def __post_init__(self) -> None:
        if (
            self.momentum_days,
            self.realized_volatility_days,
            self.reference_rv_days,
            self.annualization_days,
        ) != (84, 20, 252, 365):
            raise ValueError("Wave 4 volatility-gate parameters are frozen")

    def generate(self, candles: Sequence[Candle]) -> list[Signal]:
        runs = _complete_daily_runs(candles)
        daily_signals: dict[datetime, Signal] = {}
        for run in runs:
            returns = [
                None
                if index == 0
                else log(point.close / run[index - 1].close)
                for index, point in enumerate(run)
            ]
            realized_volatility: list[float | None] = [None] * len(run)
            for index in range(self.realized_volatility_days, len(run)):
                values = returns[
                    index - self.realized_volatility_days + 1 : index + 1
                ]
                if any(value is None for value in values):
                    continue
                realized_volatility[index] = sqrt(self.annualization_days) * stdev(
                    value for value in values if value is not None
                )
            for index, point in enumerate(run):
                current = realized_volatility[index]
                reference = realized_volatility[
                    max(0, index - self.reference_rv_days) : index
                ]
                valid_reference = [value for value in reference if value is not None]
                long_momentum = (
                    index >= self.momentum_days
                    and point.close > run[index - self.momentum_days].close
                )
                daily_signals[point.closes_at] = (
                    Signal.LONG
                    if long_momentum
                    and current is not None
                    and len(valid_reference) == self.reference_rv_days
                    and current <= median(valid_reference)
                    else Signal.FLAT
                )
        return _map_completed_daily_signals(candles, daily_signals)


@dataclass(frozen=True, slots=True)
class VolumeClockFirstLastMomentumStrategy(Wave4Strategy):
    """Frozen-volume-clock first/last half-hour momentum test.

    ``anchor_slot`` is fitted from training volume only.  ``generate`` never
    ranks or otherwise reads volume, so changing OOS volume cannot change the
    learned anchor or the OOS signal schedule.
    """

    anchor_slot: int
    name: str = "intraday_volume_clock_first_last_momentum"

    def __post_init__(self) -> None:
        if (
            isinstance(self.anchor_slot, bool)
            or not isinstance(self.anchor_slot, int)
            or not 0 <= self.anchor_slot < SLOTS_PER_KST_DAY
        ):
            raise ValueError("volume-clock anchor must be a KST half-hour slot")

    def generate(self, candles: Sequence[Candle]) -> list[Signal]:
        _validate_source_candles(candles)
        signals = [Signal.FLAT] * len(candles)
        complete_dates = {point.day for run in _complete_daily_runs(candles) for point in run}
        for source_index, candle in enumerate(candles):
            local = candle.timestamp.astimezone(KST)
            if _slot(local) != self.anchor_slot or local.date() not in complete_dates:
                continue
            entry_index = source_index + 47
            if entry_index >= len(candles):
                continue
            entry_local = candles[entry_index].timestamp.astimezone(KST)
            if entry_local.date() not in complete_dates:
                continue
            window = candles[source_index : entry_index + 1]
            if len(window) != 48 or not _is_contiguous(window):
                continue
            if candle.close <= candle.open:
                continue
            # A close signal on entry_index - 1 is filled at entry_index open.
            signals[entry_index - 1] = Signal.LONG
            signals[entry_index] = Signal.FLAT
        return signals


def fit_volume_clock(candles: Sequence[Candle]) -> VolumeClockFirstLastMomentumStrategy:
    """Fit the sole volume-derived parameter from a training prefix only."""

    _validate_source_candles(candles)
    complete_days = _complete_kst_days(candles)
    if not complete_days:
        raise ResearchError("volume-clock fitting requires a complete KST day")
    means = [
        sum(day[slot].volume for day in complete_days) / len(complete_days)
        for slot in range(SLOTS_PER_KST_DAY)
    ]
    anchor = max(range(SLOTS_PER_KST_DAY), key=lambda slot: (means[slot], -slot))
    return VolumeClockFirstLastMomentumStrategy(anchor_slot=anchor)


def daily_tsmom_84_builder(_: Sequence[Candle]) -> DailyTsmom84Strategy:
    return DailyTsmom84Strategy()


def daily_tsmom_84_rv20_median_gate_builder(
    _: Sequence[Candle],
) -> DailyTsmom84Rv20MedianGateStrategy:
    return DailyTsmom84Rv20MedianGateStrategy()


def wave4_candidate_builders() -> dict[str, CandidateBuilder]:
    """Frozen train-aware builders; only volume-clock fitting reads train volume."""

    return {
        "daily_tsmom_84": daily_tsmom_84_builder,
        "daily_tsmom_84_rv20_median_gate": daily_tsmom_84_rv20_median_gate_builder,
        "intraday_volume_clock_first_last_momentum": fit_volume_clock,
    }


def wave4_candidate_manifest(
    config: Wave4NestedConfig | None = None,
) -> dict[str, Any]:
    """Canonical, research-only candidate contract frozen before evaluation."""

    selected_config = config or Wave4NestedConfig()
    return {
        "schema_version": 4,
        "frozen_at": "2026-08-14T11:18:00+00:00",
        "status": "RESEARCH_ONLY",
        "market": "KRW-BTC",
        "historical_prefix_sha256": (
            "dc3537c862bc54efebfd215807e2ab57da66396ebfbfcf3d5a243327b9817248"
        ),
        "historical_data_reused": True,
        "forward_sample_after_freeze_30m": 0,
        "execution": {
            "source_minutes": SOURCE_MINUTES,
            "signal_observed_at": "completed_source_or_kst_day_close",
            "execution_eligible_at": "next_source_open",
            "allow_short": False,
            "gap_policy": "reset_flat_until_next_complete_kst_daily_close",
        },
        "candidates": [
            {
                "name": "daily_tsmom_84",
                "family": "time_series_momentum_adjacent_horizon",
                "parameters": {"lookback_kst_days": 84},
                "independent_confirmation_family": "tsmom",
            },
            {
                "name": "daily_tsmom_84_rv20_median_gate",
                "family": "time_series_momentum_volatility_regime_overlay",
                "parameters": {
                    "lookback_kst_days": 84,
                    "rv_log_return_days": 20,
                    "rv_reference_prior_values": 252,
                    "annualization_days": 365,
                    "comparison": "current_rv20_lte_prior_rv20_median",
                },
                "independent_confirmation_family": "tsmom",
            },
            {
                "name": "intraday_volume_clock_first_last_momentum",
                "family": "train_fitted_intraday_volume_clock_momentum",
                "parameters": {
                    "fit": "mean_base_volume_by_complete_kst_30m_slot",
                    "anchor_tie_break": "earliest_slot",
                    "entry": "anchor_plus_47_source_bar_open",
                    "holding_source_bars": 1,
                    "cycle_gap_policy": "skip",
                },
                "independent_confirmation_family": "intraday_volume_clock",
            },
        ],
        "candidate_limit": 3,
        "nested_selection": {
            "outer_initial_train_30m": selected_config.outer_train_size,
            "outer_test_30m": selected_config.outer_test_size,
            "outer_fold_count": selected_config.outer_fold_count,
            "outer_expanding_prefix": True,
            "inner_initial_train_30m_first_outer": (
                selected_config.inner_initial_train_size
            ),
            "inner_test_30m": selected_config.inner_test_size,
            "inner_fold_count": selected_config.inner_fold_count,
            "inner_expanding_prefix": True,
            "inner_anchor": "outer_train_end",
            "minimum_positive_inner_stress_folds": (
                selected_config.minimum_profitable_stress_folds
            ),
        },
        "cost_contract": {
            "allow_short": False,
            "base": {"fee_rate": 0.0025, "slippage_bps": 5.0},
            "double_cost_stress": {"fee_rate": 0.005, "slippage_bps": 10.0},
        },
        "bootstrap": {
            "method": "kst_daily_moving_block",
            "block_days": 7,
            "iterations": 5_000,
            "seed": 20_260_814,
        },
        "rejection_gates": [
            "nested_base_return_gt_previous_best_0.01019286",
            "nested_double_cost_stress_return_gt_0",
            "nested_maximum_drawdown_lte_0.10",
            "positive_outer_folds_gte_5_of_8",
            "positive_stress_quarters_gte_3_of_4",
            "seven_day_block_bootstrap_excess_return_lower_95_gt_0",
            "non_final_closed_trades_gte_12",
            "single_trade_positive_pnl_lte_0.50_and_gap_sensitivity_required",
        ],
        "implementation_identity": {
            "candidate_builders": "bithumb_coin_trader.wave4.wave4_candidate_builders",
            "nested_runner": "bithumb_coin_trader.wave4.run_wave4_nested_research",
            "backtester": "bithumb_coin_trader.backtest.Backtester",
        },
        "promotion": "forbidden_without_new_forward_evidence",
    }


def wave4_candidate_manifest_hash(
    manifest: Mapping[str, Any] | None = None,
) -> str:
    payload = manifest or wave4_candidate_manifest()
    return sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def assert_wave4_candidate_builders_match_manifest(
    builders: Mapping[str, CandidateBuilder] | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> None:
    selected = builders or wave4_candidate_builders()
    selected_manifest = manifest or wave4_candidate_manifest()
    candidates = selected_manifest.get("candidates")
    if not isinstance(candidates, list):
        raise ResearchError("Wave 4 manifest candidates must be a list")
    if tuple(selected) != tuple(item.get("name") for item in candidates):
        raise ResearchError("Wave 4 runtime builder registry differs from manifest")
    if tuple(selected) != WAVE4_CANDIDATE_NAMES:
        raise ResearchError("Wave 4 candidate names are not frozen")


def assert_wave4_cost_settings_match_manifest(
    base_settings: Any,
    stress_settings: Any,
    manifest: Mapping[str, Any] | None = None,
) -> None:
    selected_manifest = manifest or wave4_candidate_manifest()
    actual = {
        "allow_short": False,
        "base": {
            "fee_rate": base_settings.fee_rate,
            "slippage_bps": base_settings.slippage_bps,
        },
        "double_cost_stress": {
            "fee_rate": stress_settings.fee_rate,
            "slippage_bps": stress_settings.slippage_bps,
        },
    }
    if actual != selected_manifest.get("cost_contract"):
        raise ResearchError("Wave 4 runtime cost settings differ from manifest")


def assert_wave4_config_matches_manifest(
    config: Wave4NestedConfig,
    manifest: Mapping[str, Any] | None = None,
) -> None:
    selected_manifest = manifest or wave4_candidate_manifest(config)
    expected = selected_manifest.get("nested_selection")
    actual = {
        "outer_initial_train_30m": config.outer_train_size,
        "outer_test_30m": config.outer_test_size,
        "outer_fold_count": config.outer_fold_count,
        "outer_expanding_prefix": True,
        "inner_initial_train_30m_first_outer": config.inner_initial_train_size,
        "inner_test_30m": config.inner_test_size,
        "inner_fold_count": config.inner_fold_count,
        "inner_expanding_prefix": True,
        "inner_anchor": "outer_train_end",
        "minimum_positive_inner_stress_folds": config.minimum_profitable_stress_folds,
    }
    if actual != expected:
        raise ResearchError("Wave 4 runtime nested config differs from manifest")


def compare_wave4_candidates(
    candles: Sequence[Candle],
    *,
    settings: Any = None,
    config: Wave4NestedConfig | None = None,
    train_size: int | None = None,
    test_size: int | None = None,
    expanding: bool = True,
    candidate_builders: Mapping[str, CandidateBuilder] | None = None,
) -> CandidateComparisonReport:
    """Compare train-aware builders without ever fitting on a test fold."""

    selected_config = config or Wave4NestedConfig()
    sample = historical_prefix(candles, count=selected_config.historical_count)
    _validate_source_candles(sample)
    selected_train_size = train_size or selected_config.outer_train_size
    selected_test_size = test_size or selected_config.outer_test_size
    builders = dict(candidate_builders or wave4_candidate_builders())
    assert_wave4_candidate_builders_match_manifest(builders)
    reports = tuple(
        _run_train_aware_candidate(
            sample,
            candidate_name=name,
            builder=builder,
            train_size=selected_train_size,
            test_size=selected_test_size,
            settings=settings,
            expanding=expanding,
        )
        for name, builder in builders.items()
    )
    boundaries = tuple(
        (fold.train_start, fold.train_end, fold.test_start, fold.test_end)
        for fold in reports[0].folds
    )
    if any(
        tuple((fold.train_start, fold.train_end, fold.test_start, fold.test_end) for fold in report.folds)
        != boundaries
        for report in reports[1:]
    ):
        raise ResearchError("Wave 4 candidate fold boundaries unexpectedly differ")
    return CandidateComparisonReport(
        candidates=reports,
        candidate_count=len(reports),
        fold_boundaries=boundaries,
    )


def run_wave4_nested_research(
    candles: Sequence[Candle],
    *,
    base_settings: Any = None,
    stress_settings: Any = None,
    config: Wave4NestedConfig | None = None,
) -> NestedOosResult:
    """Run train-aware, nested Wave 4 selection without OOS fitting leakage."""

    from .config import TradingSettings

    selected_config = config or Wave4NestedConfig()
    selected_base = base_settings or TradingSettings()
    selected_stress = stress_settings or TradingSettings(
        fee_rate=0.005, slippage_bps=10
    )
    assert_wave4_config_matches_manifest(selected_config)
    assert_wave4_cost_settings_match_manifest(selected_base, selected_stress)
    sample = historical_prefix(candles, count=selected_config.historical_count)
    _validate_source_candles(sample)
    builders = wave4_candidate_builders()
    decisions = _build_nested_selections(
        sample,
        builders=builders,
        config=selected_config,
        base_settings=selected_base,
        stress_settings=selected_stress,
    )
    return _execute_nested_outer_oos(
        sample,
        decisions=decisions,
        builders=builders,
        config=selected_config,
        base_settings=selected_base,
        stress_settings=selected_stress,
    )


def _run_train_aware_candidate(
    candles: Sequence[Candle],
    *,
    candidate_name: str,
    builder: CandidateBuilder,
    train_size: int,
    test_size: int,
    settings: Any,
    expanding: bool,
) -> ProjectResearchReport:
    def build(train: Sequence[Candle]) -> tuple[Wave4Strategy, tuple[Candle, ...]]:
        return builder(tuple(train)), tuple(train)

    def prepare_execution(
        prepared: tuple[Wave4Strategy, tuple[Candle, ...]], test: Sequence[Candle]
    ) -> tuple[tuple[Candle, ...], tuple[Signal, ...]]:
        strategy, train = prepared
        combined = (*train, *test)
        signals = strategy.generate(combined)
        if len(signals) != len(combined) or any(signal is Signal.SHORT for signal in signals):
            raise ResearchError("Wave 4 strategies must return aligned LONG/FLAT signals")
        execution_candles = (train[-1], *test)
        execution_signals = tuple(signals[len(train) - 1 :])
        return execution_candles, execution_signals

    prepared = walk_forward(
        candles,
        train_size=train_size,
        test_size=test_size,
        step_size=test_size,
        strategy_factory=build,
        backtest=prepare_execution,
        expanding=expanding,
    )
    if not prepared:
        raise ResearchError("not enough candles for one complete Wave 4 fold")
    execution_candles = list(prepared[0].result[0])
    execution_signals = list(prepared[0].result[1])
    for fold in prepared[1:]:
        window_candles, window_signals = fold.result
        if execution_candles[-1].timestamp != window_candles[0].timestamp:
            raise ResearchError("Wave 4 OOS fold windows must be contiguous")
        # The next train-only fitted strategy owns the final pre-boundary close.
        # A changing target pays one genuine switch cost at the next OOS open;
        # an unchanged target is carried without a synthetic liquidation.
        execution_signals[-1] = window_signals[0]
        execution_candles.extend(window_candles[1:])
        execution_signals.extend(window_signals[1:])
    continuous = Backtester(settings, allow_short=False).run(
        execution_candles, execution_signals
    )
    folds = tuple(
        WalkForwardFold(
            fold=fold.fold,
            train_start=fold.train_start,
            train_end=fold.train_end,
            test_start=fold.test_start,
            test_end=fold.test_end,
            result=Backtester(settings, allow_short=False).slice_result(
                continuous,
                execution_candles,
                start=fold.fold * test_size,
                end=(fold.fold + 1) * test_size,
            ),
        )
        for fold in prepared
    )
    trade_count = sum(fold.result.trade_count for fold in folds)
    weighted_wins = sum(fold.result.win_rate * fold.result.trade_count for fold in folds)
    return ProjectResearchReport(
        folds=tuple(folds),
        compounded_return=continuous.total_return,
        maximum_drawdown=continuous.max_drawdown,
        mean_sharpe=sum(fold.result.sharpe for fold in folds) / len(folds),
        trade_count=trade_count,
        weighted_win_rate=weighted_wins / trade_count if trade_count else 0.0,
        oos_equity_curve=continuous.equity_curve,
        candidate_name=candidate_name,
    )


def _build_nested_selections(
    sample: Sequence[Candle],
    *,
    builders: Mapping[str, CandidateBuilder],
    config: Wave4NestedConfig,
    base_settings: Any,
    stress_settings: Any,
) -> tuple[SelectionDecision, ...]:
    decisions: list[SelectionDecision] = []
    for fold, (train_start, train_end, test_start, test_end) in enumerate(
        config.outer_boundaries()
    ):
        outer_train = sample[:train_end]
        scores = tuple(
            _score_train_aware_inner_candidate(
                outer_train,
                candidate_name=name,
                builder=builder,
                config=config,
                base_settings=base_settings,
                stress_settings=stress_settings,
            )
            for name, builder in builders.items()
        )
        qualified = [score for score in scores if score.qualifies]
        selected = (
            sorted(
                qualified,
                key=lambda score: (
                    -score.stress_compounded_return,
                    score.stress_maximum_drawdown,
                    score.candidate_name,
                ),
            )[0].candidate_name
            if qualified
            else None
        )
        decisions.append(
            SelectionDecision(
                fold=fold,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                selected_candidate=selected,
                candidate_scores=scores,
            )
        )
    return tuple(decisions)


def _score_train_aware_inner_candidate(
    outer_train: Sequence[Candle],
    *,
    candidate_name: str,
    builder: CandidateBuilder,
    config: Wave4NestedConfig,
    base_settings: Any,
    stress_settings: Any,
) -> CandidateInnerScore:
    execution_candles: list[Candle] = []
    execution_signals: list[Signal] = []
    for _, train_end, test_start, test_end in config.inner_boundaries(len(outer_train)):
        train = outer_train[:train_end]
        test = outer_train[test_start:test_end]
        strategy = builder(train)
        generated = strategy.generate((*train, *test))
        if len(generated) != len(train) + len(test):
            raise ResearchError("Wave 4 candidate returned the wrong signal count")
        fold_candles = [train[-1], *test]
        fold_signals = [Signal(signal) for signal in generated[len(train) - 1 :]]
        if any(signal is Signal.SHORT for signal in fold_signals):
            raise ResearchError("Wave 4 candidate emitted a short signal")
        if execution_candles:
            if execution_candles[-1].timestamp != fold_candles[0].timestamp:
                raise ResearchError("Wave 4 inner folds must be contiguous")
            execution_signals[-1] = fold_signals[0]
            execution_candles.extend(fold_candles[1:])
            execution_signals.extend(fold_signals[1:])
        else:
            execution_candles.extend(fold_candles)
            execution_signals.extend(fold_signals)
    base = Backtester(base_settings, allow_short=False).run(
        execution_candles, execution_signals
    )
    stress = Backtester(stress_settings, allow_short=False).run(
        execution_candles, execution_signals
    )
    base_folds = _slice_inner_results(base, execution_candles, config, base_settings)
    stress_folds = _slice_inner_results(
        stress, execution_candles, config, stress_settings
    )
    profitable_stress = sum(result.total_return > 0 for result in stress_folds)
    return CandidateInnerScore(
        candidate_name=candidate_name,
        base_compounded_return=base.total_return,
        base_maximum_drawdown=base.max_drawdown,
        stress_compounded_return=stress.total_return,
        stress_maximum_drawdown=stress.max_drawdown,
        base_fold_returns=tuple(result.total_return for result in base_folds),
        stress_fold_returns=tuple(result.total_return for result in stress_folds),
        profitable_stress_fold_count=profitable_stress,
        qualifies=(
            base.total_return > 0
            and stress.total_return > 0
            and profitable_stress >= config.minimum_profitable_stress_folds
        ),
    )


def _execute_nested_outer_oos(
    sample: Sequence[Candle],
    *,
    decisions: Sequence[SelectionDecision],
    builders: Mapping[str, CandidateBuilder],
    config: Wave4NestedConfig,
    base_settings: Any,
    stress_settings: Any,
) -> NestedOosResult:
    execution_candles: list[Candle] = []
    execution_signals: list[Signal] = []
    for decision in decisions:
        train = sample[decision.train_start : decision.train_end]
        test = sample[decision.test_start : decision.test_end]
        window_candles = [train[-1], *test]
        if decision.selected_candidate is None:
            window_signals = [Signal.FLAT] * len(window_candles)
        else:
            strategy = builders[decision.selected_candidate](train)
            generated = strategy.generate((*train, *test))
            window_signals = [
                Signal(signal) for signal in generated[len(train) - 1 :]
            ]
            if len(window_signals) != len(window_candles) or any(
                signal is Signal.SHORT for signal in window_signals
            ):
                raise ResearchError("Wave 4 outer candidate emitted invalid signals")
        if execution_candles:
            if execution_candles[-1].timestamp != window_candles[0].timestamp:
                raise ResearchError("Wave 4 outer folds must be contiguous")
            execution_signals[-1] = window_signals[0]
            execution_candles.extend(window_candles[1:])
            execution_signals.extend(window_signals[1:])
        else:
            execution_candles.extend(window_candles)
            execution_signals.extend(window_signals)
    base = Backtester(base_settings, allow_short=False).run(
        execution_candles, execution_signals
    )
    stress = Backtester(stress_settings, allow_short=False).run(
        execution_candles, execution_signals
    )
    return NestedOosResult(
        config=config,
        decisions=tuple(decisions),
        base=_continuous_project_report(
            base, execution_candles, decisions, base_settings, "wave4_nested_selector"
        ),
        stress=_continuous_project_report(
            stress,
            execution_candles,
            decisions,
            stress_settings,
            "wave4_nested_selector",
        ),
    )


def _slice_inner_results(
    result: Any,
    execution_candles: Sequence[Candle],
    config: Wave4NestedConfig,
    settings: Any,
) -> tuple[Any, ...]:
    backtester = Backtester(settings, allow_short=False)
    return tuple(
        backtester.slice_result(
            result,
            execution_candles,
            start=fold * config.inner_test_size,
            end=(fold + 1) * config.inner_test_size,
        )
        for fold in range(config.inner_fold_count)
    )


def _continuous_project_report(
    result: Any,
    execution_candles: Sequence[Candle],
    decisions: Sequence[SelectionDecision],
    settings: Any,
    candidate_name: str,
) -> ProjectResearchReport:
    backtester = Backtester(settings, allow_short=False)
    test_size = decisions[0].test_end - decisions[0].test_start
    folds = tuple(
        WalkForwardFold(
            fold=decision.fold,
            train_start=decision.train_start,
            train_end=decision.train_end,
            test_start=decision.test_start,
            test_end=decision.test_end,
            result=backtester.slice_result(
                result,
                execution_candles,
                start=decision.fold * test_size,
                end=(decision.fold + 1) * test_size,
            ),
        )
        for decision in decisions
    )
    trades = sum(fold.result.trade_count for fold in folds)
    weighted_wins = sum(fold.result.win_rate * fold.result.trade_count for fold in folds)
    return ProjectResearchReport(
        folds=folds,
        compounded_return=result.total_return,
        maximum_drawdown=result.max_drawdown,
        mean_sharpe=sum(fold.result.sharpe for fold in folds) / len(folds),
        trade_count=trades,
        weighted_win_rate=weighted_wins / trades if trades else 0.0,
        oos_equity_curve=result.equity_curve,
        candidate_name=candidate_name,
    )


def _complete_daily_runs(candles: Sequence[Candle]) -> list[list[DailyPoint]]:
    _validate_source_candles(candles)
    complete = _complete_kst_days(candles)
    points = [
        DailyPoint(
            day=day[0].timestamp.astimezone(KST).date(),
            closes_at=day[-1].timestamp + SOURCE_DELTA,
            close=day[-1].close,
        )
        for day in complete
    ]
    runs: list[list[DailyPoint]] = []
    for point in points:
        if not runs or point.day != runs[-1][-1].day + timedelta(days=1):
            runs.append([point])
        else:
            runs[-1].append(point)
    return runs


def _complete_kst_days(candles: Sequence[Candle]) -> list[tuple[Candle, ...]]:
    _validate_source_candles(candles)
    buckets: dict[date, list[Candle]] = {}
    for candle in candles:
        buckets.setdefault(candle.timestamp.astimezone(KST).date(), []).append(candle)
    complete: list[tuple[Candle, ...]] = []
    for day, bucket in sorted(buckets.items()):
        ordered = tuple(bucket)
        if len(ordered) != SLOTS_PER_KST_DAY or not _is_contiguous(ordered):
            continue
        if all(
            candle.timestamp.astimezone(KST)
            == datetime.combine(day, time(0), tzinfo=KST)
            + timedelta(minutes=SOURCE_MINUTES * index)
            for index, candle in enumerate(ordered)
        ):
            complete.append(ordered)
    return complete


def _map_completed_daily_signals(
    candles: Sequence[Candle], daily_signals: Mapping[datetime, Signal]
) -> list[Signal]:
    _validate_source_candles(candles)
    mapped: list[Signal] = []
    current = Signal.FLAT
    previous: Candle | None = None
    for candle in candles:
        if previous is not None and candle.timestamp - previous.timestamp != SOURCE_DELTA:
            # A data gap invalidates the carried state until a later complete
            # daily close contributes a new signal.
            current = Signal.FLAT
        current = Signal(daily_signals.get(candle.timestamp + SOURCE_DELTA, current))
        mapped.append(current)
        previous = candle
    return mapped


def _validate_source_candles(candles: Sequence[Candle]) -> None:
    previous: Candle | None = None
    for candle in candles:
        if candle.market != "KRW-BTC":
            raise ValueError("Wave 4 research is frozen to KRW-BTC")
        local = candle.timestamp.astimezone(KST)
        if local.second or local.microsecond or local.minute not in {0, 30}:
            raise ValueError("Wave 4 source candles must use a 30-minute cadence")
        if previous is not None:
            delta = candle.timestamp - previous.timestamp
            if delta <= timedelta(0):
                raise ValueError("Wave 4 candles must be strictly chronological")
            minutes = delta.total_seconds() / 60
            if minutes % SOURCE_MINUTES:
                raise ValueError("Wave 4 source candles must use a 30-minute cadence")
        previous = candle


def _is_contiguous(candles: Sequence[Candle]) -> bool:
    return all(
        candles[index].timestamp - candles[index - 1].timestamp == SOURCE_DELTA
        for index in range(1, len(candles))
    )


def _slot(local: datetime) -> int:
    return local.hour * 2 + local.minute // SOURCE_MINUTES


def _stitch_equity_curves(curves: Sequence[Sequence[float]]) -> tuple[float, ...]:
    if not curves or not curves[0] or curves[0][0] <= 0:
        raise ResearchError("Wave 4 fold curves must start positive")
    stitched = [float(curves[0][0])]
    for curve in curves:
        if not curve or curve[0] <= 0:
            raise ResearchError("Wave 4 fold curves must start positive")
        scale = stitched[-1] / curve[0]
        stitched.extend(float(value) * scale for value in curve[1:])
    return tuple(stitched)


def _maximum_drawdown(curve: Sequence[float]) -> float:
    peak = curve[0]
    drawdown = 0.0
    for value in curve:
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak)
    return drawdown
