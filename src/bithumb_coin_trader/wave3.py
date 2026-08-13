"""Research-only Wave 3 candidate selection and out-of-sample evaluation.

This module deliberately imports only the candle, strategy, research, and
backtest surfaces.  It never touches credentials, exchange clients, account
state, or order execution.  All strategy decisions are made on completed
candle closes and the project backtester fills them at the following open.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import timedelta, timezone
from hashlib import sha256
import json
from math import exp, fsum, log
from random import Random
from statistics import fmean
from typing import Any, Callable, Mapping, Sequence

from .backtest import BacktestResult, Backtester
from .config import TradingSettings
from .models import Candle, Signal
from .research import (
    CandidateComparisonReport,
    ProjectResearchReport,
    ResearchError,
    WalkForwardFold,
    compare_candidate_factories,
    registered_candidate_factories,
)


KST = timezone(timedelta(hours=9))
WAVE3_HISTORICAL_COUNT = 40_000
WAVE3_CANDIDATE_NAMES = (
    "trading_range_daily_50_band_1pct",
    "trading_range_daily_50_no_band",
    "trend_daily_sma50_200_adx14_25",
    "trend_daily_macd12_26_9_pvo12_26",
    "ensemble_daily_3_of_5",
)
PREVIOUS_BEST_CANDIDATE = (
    "mean_reversion_1h_bb20_rsi30_reentry_4h_sma50_uptrend"
)


CandidateFactory = Callable[[], Any]


_COMPLETED_DAILY_WRAPPER = {
    "type": "CompletedIntervalStrategy",
    "source_minutes": 30,
    "target_minutes": 1_440,
    "boundary_timezone": "Asia/Seoul",
    "signal_observed_at": "completed_target_close",
    "execution_eligible_at": "next_source_open",
}

_WAVE3_FROZEN_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "name": "trading_range_daily_50_band_1pct",
        "family": "trading_range_breakout",
        "factory": "trading_range_daily_50_band_1pct_strategy",
        "wrapper": _COMPLETED_DAILY_WRAPPER,
        "strategy_type": "TradingRangeBreakoutStrategy",
        "strategy_name": "trading_range_50_100bps",
        "parameters": {
            "lookback_period": 50,
            "entry_band_fraction": 0.01,
            "exit_band_fraction": 0.01,
        },
    },
    {
        "name": "trading_range_daily_50_no_band",
        "family": "trading_range_breakout",
        "factory": "trading_range_daily_50_no_band_strategy",
        "wrapper": _COMPLETED_DAILY_WRAPPER,
        "strategy_type": "TradingRangeBreakoutStrategy",
        "strategy_name": "trading_range_50_0bps",
        "parameters": {
            "lookback_period": 50,
            "entry_band_fraction": 0.0,
            "exit_band_fraction": 0.0,
        },
    },
    {
        "name": "trend_daily_sma50_200_adx14_25",
        "family": "directional_trend_strength",
        "factory": "trend_daily_sma50_200_adx14_25_strategy",
        "wrapper": _COMPLETED_DAILY_WRAPPER,
        "strategy_type": "DailySmaAdxTrendStrategy",
        "strategy_name": "daily_sma50_200_adx14_25",
        "parameters": {
            "fast_period": 50,
            "slow_period": 200,
            "directional_period": 14,
            "adx_threshold": 25.0,
        },
    },
    {
        "name": "trend_daily_macd12_26_9_pvo12_26",
        "family": "price_volume_momentum",
        "factory": "trend_daily_macd12_26_9_pvo12_26_strategy",
        "wrapper": _COMPLETED_DAILY_WRAPPER,
        "strategy_type": "DailyMacdPvoTrendStrategy",
        "strategy_name": "daily_macd12_26_9_pvo12_26",
        "parameters": {
            "macd_fast_period": 12,
            "macd_slow_period": 26,
            "macd_signal_period": 9,
            "pvo_fast_period": 12,
            "pvo_slow_period": 26,
        },
    },
    {
        "name": "ensemble_daily_3_of_5",
        "family": "equal_vote_ensemble",
        "factory": "ensemble_daily_3_of_5_strategy",
        "wrapper": _COMPLETED_DAILY_WRAPPER,
        "strategy_type": "MajorityVoteLongStrategy",
        "strategy_name": "ensemble_daily_3_of_5",
        "parameters": {"minimum_votes": 3},
        "constituents": [
            {
                "strategy_type": "TimeSeriesMomentumStrategy",
                "strategy_name": "tsmom_365",
                "parameters": {"lookback_period": 365},
            },
            {
                "strategy_type": "TradingRangeBreakoutStrategy",
                "strategy_name": "trading_range_50_100bps",
                "parameters": {
                    "lookback_period": 50,
                    "entry_band_fraction": 0.01,
                    "exit_band_fraction": 0.01,
                },
            },
            {
                "strategy_type": "DailySmaTrendStrategy",
                "strategy_name": "daily_sma50_above_sma200",
                "parameters": {"fast_period": 50, "slow_period": 200},
            },
            {
                "strategy_type": "DailySmaAdxTrendStrategy",
                "strategy_name": "daily_sma50_200_adx14_25",
                "parameters": {
                    "fast_period": 50,
                    "slow_period": 200,
                    "directional_period": 14,
                    "adx_threshold": 25.0,
                },
            },
            {
                "strategy_type": "DailyMacdPvoTrendStrategy",
                "strategy_name": "daily_macd12_26_9_pvo12_26",
                "parameters": {
                    "macd_fast_period": 12,
                    "macd_slow_period": 26,
                    "macd_signal_period": 9,
                    "pvo_fast_period": 12,
                    "pvo_slow_period": 26,
                },
            },
        ],
    },
)


@dataclass(frozen=True, slots=True)
class NestedWalkForwardConfig:
    """Frozen Wave 3 nested walk-forward geometry.

    The defaults consume only the first 40,000 observations.  Eight rolling
    outer test folds use all observations available before that fold, beginning
    with 19,200. Candidate qualification uses six expanding inner folds anchored
    at the end of each outer training prefix. The first outer fold therefore
    begins its inner training at 12,000 observations; later folds begin with all
    additional history that had become available before their outer test.
    """

    historical_count: int = WAVE3_HISTORICAL_COUNT
    outer_train_size: int = 19_200
    outer_test_size: int = 2_400
    outer_fold_count: int = 8
    inner_initial_train_size: int = 12_000
    inner_test_size: int = 1_200
    inner_fold_count: int = 6
    minimum_profitable_stress_folds: int = 4

    def __post_init__(self) -> None:
        values = (
            self.historical_count,
            self.outer_train_size,
            self.outer_test_size,
            self.outer_fold_count,
            self.inner_initial_train_size,
            self.inner_test_size,
            self.inner_fold_count,
            self.minimum_profitable_stress_folds,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in values
        ):
            raise ValueError("nested walk-forward sizes must be positive integers")
        if self.outer_required_count > self.historical_count:
            raise ValueError("outer folds exceed the historical sample")
        if self.inner_required_count > self.outer_train_size:
            raise ValueError("inner folds exceed an outer training window")
        if self.minimum_profitable_stress_folds > self.inner_fold_count:
            raise ValueError("stress-fold threshold exceeds inner fold count")

    @property
    def outer_required_count(self) -> int:
        return self.outer_train_size + self.outer_test_size * self.outer_fold_count

    @property
    def inner_required_count(self) -> int:
        return (
            self.inner_initial_train_size
            + self.inner_test_size * self.inner_fold_count
        )

    def outer_boundaries(self) -> tuple[tuple[int, int, int, int], ...]:
        return tuple(
            (
                0,
                fold * self.outer_test_size + self.outer_train_size,
                fold * self.outer_test_size + self.outer_train_size,
                fold * self.outer_test_size
                + self.outer_train_size
                + self.outer_test_size,
            )
            for fold in range(self.outer_fold_count)
        )

    def inner_boundaries(
        self, observation_count: int | None = None
    ) -> tuple[tuple[int, int, int, int], ...]:
        selected_count = (
            self.outer_train_size
            if observation_count is None
            else observation_count
        )
        if (
            isinstance(selected_count, bool)
            or not isinstance(selected_count, int)
            or selected_count < self.inner_required_count
        ):
            raise ValueError("inner folds require a complete outer training prefix")
        initial_train_size = selected_count - (
            self.inner_test_size * self.inner_fold_count
        )
        return tuple(
            (
                0,
                initial_train_size + fold * self.inner_test_size,
                initial_train_size + fold * self.inner_test_size,
                initial_train_size + (fold + 1) * self.inner_test_size,
            )
            for fold in range(self.inner_fold_count)
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "historical_count": self.historical_count,
            "outer_train_size": self.outer_train_size,
            "outer_test_size": self.outer_test_size,
            "outer_fold_count": self.outer_fold_count,
            "inner_initial_train_size": self.inner_initial_train_size,
            "inner_test_size": self.inner_test_size,
            "inner_fold_count": self.inner_fold_count,
            "minimum_profitable_stress_folds": (
                self.minimum_profitable_stress_folds
            ),
        }


@dataclass(frozen=True, slots=True)
class CandidateInnerScore:
    candidate_name: str
    base_compounded_return: float
    base_maximum_drawdown: float
    stress_compounded_return: float
    stress_maximum_drawdown: float
    base_fold_returns: tuple[float, ...]
    stress_fold_returns: tuple[float, ...]
    profitable_stress_fold_count: int
    qualifies: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_name": self.candidate_name,
            "base_compounded_return": self.base_compounded_return,
            "base_maximum_drawdown": self.base_maximum_drawdown,
            "stress_compounded_return": self.stress_compounded_return,
            "stress_maximum_drawdown": self.stress_maximum_drawdown,
            "base_fold_returns": list(self.base_fold_returns),
            "stress_fold_returns": list(self.stress_fold_returns),
            "profitable_stress_fold_count": self.profitable_stress_fold_count,
            "qualifies": self.qualifies,
        }


@dataclass(frozen=True, slots=True)
class SelectionDecision:
    fold: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    selected_candidate: str | None
    candidate_scores: tuple[CandidateInnerScore, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "fold": self.fold,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
            "selected_candidate": self.selected_candidate,
            "candidate_scores": [score.as_dict() for score in self.candidate_scores],
        }


@dataclass(frozen=True, slots=True)
class NestedOosResult:
    config: NestedWalkForwardConfig
    decisions: tuple[SelectionDecision, ...]
    base: ProjectResearchReport
    stress: ProjectResearchReport

    def as_dict(self) -> dict[str, Any]:
        return {
            "config": self.config.as_dict(),
            "decisions": [decision.as_dict() for decision in self.decisions],
            "base": project_report_as_dict(self.base),
            "double_cost_stress": project_report_as_dict(self.stress),
        }


@dataclass(frozen=True, slots=True)
class MovingBlockBootstrapResult:
    observation_count: int
    block_days: int
    iterations: int
    seed: int
    point_estimate: float
    lower_95: float
    median: float
    upper_95: float
    probability_positive: float

    def as_dict(self) -> dict[str, int | float]:
        return {
            "observation_count": self.observation_count,
            "block_days": self.block_days,
            "iterations": self.iterations,
            "seed": self.seed,
            "point_estimate": self.point_estimate,
            "lower_95": self.lower_95,
            "median": self.median,
            "upper_95": self.upper_95,
            "probability_positive": self.probability_positive,
        }


def wave3_candidate_factories() -> dict[str, CandidateFactory]:
    """Return the five fixed, zero-argument Wave 3 strategy factories."""

    factories = _wave3_candidate_factories_unchecked()
    assert_wave3_candidate_factories_match_manifest(factories)
    return factories


def _wave3_candidate_factories_unchecked() -> dict[str, CandidateFactory]:
    """Build the registry without recursively invoking its manifest check."""

    from .strategy import (
        ensemble_daily_3_of_5_strategy,
        trading_range_daily_50_band_1pct_strategy,
        trading_range_daily_50_no_band_strategy,
        trend_daily_macd12_26_9_pvo12_26_strategy,
        trend_daily_sma50_200_adx14_25_strategy,
    )

    return {
        "trading_range_daily_50_band_1pct": (
            trading_range_daily_50_band_1pct_strategy
        ),
        "trading_range_daily_50_no_band": trading_range_daily_50_no_band_strategy,
        "trend_daily_sma50_200_adx14_25": (
            trend_daily_sma50_200_adx14_25_strategy
        ),
        "trend_daily_macd12_26_9_pvo12_26": (
            trend_daily_macd12_26_9_pvo12_26_strategy
        ),
        "ensemble_daily_3_of_5": ensemble_daily_3_of_5_strategy,
    }


def _dataclass_parameters(strategy: Any) -> dict[str, Any]:
    parameters = getattr(strategy, "parameters", None)
    if parameters is None:
        return {}
    if not is_dataclass(parameters) or isinstance(parameters, type):
        raise ResearchError(
            f"{type(strategy).__name__} parameters are not a dataclass instance"
        )
    return {
        field.name: getattr(parameters, field.name)
        for field in fields(parameters)
    }


def _runtime_inner_definition(strategy: Any) -> dict[str, Any]:
    definition = {
        "strategy_type": type(strategy).__name__,
        "strategy_name": str(getattr(strategy, "name", "")),
    }
    constituents = getattr(strategy, "strategies", None)
    if constituents is None:
        definition["parameters"] = _dataclass_parameters(strategy)
        return definition
    minimum_votes = getattr(strategy, "minimum_votes", None)
    definition["parameters"] = {"minimum_votes": minimum_votes}
    definition["constituents"] = [
        _runtime_inner_definition(constituent) for constituent in constituents
    ]
    return definition


def _runtime_candidate_definition(
    name: str,
    family: str,
    factory: CandidateFactory,
) -> dict[str, Any]:
    strategy = factory()
    wrapper = {
        "type": type(strategy).__name__,
        "source_minutes": getattr(strategy, "source_minutes", None),
        "target_minutes": getattr(strategy, "target_minutes", None),
        "boundary_timezone": "Asia/Seoul",
        "signal_observed_at": "completed_target_close",
        "execution_eligible_at": "next_source_open",
    }
    return {
        "name": str(getattr(strategy, "name", "")),
        "family": family,
        "factory": factory.__name__,
        "wrapper": wrapper,
        **_runtime_inner_definition(getattr(strategy, "inner", None)),
    }


def assert_wave3_candidate_factories_match_manifest(
    factories: Mapping[str, CandidateFactory] | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> None:
    """Fail closed when a runtime factory drifts from the frozen definition.

    The comparison covers factory names, completed-daily wrapper geometry,
    concrete inner strategy types and names, every dataclass parameter, and
    the ensemble vote threshold plus ordered constituent definitions.
    """

    selected_factories = dict(
        factories if factories is not None else _wave3_candidate_factories_unchecked()
    )
    selected_manifest = manifest or wave3_candidate_manifest()
    candidates = selected_manifest.get("candidates")
    if not isinstance(candidates, list):
        raise ResearchError("candidate manifest must contain a candidate list")
    expected_names = tuple(candidate.get("name") for candidate in candidates)
    if tuple(selected_factories) != expected_names:
        raise ResearchError("runtime candidate registry differs from the manifest")
    runtime_candidates = [
        _runtime_candidate_definition(
            name,
            str(candidate.get("family", "")),
            selected_factories[name],
        )
        for name, candidate in zip(expected_names, candidates, strict=True)
    ]
    if runtime_candidates != candidates:
        raise ResearchError("runtime candidate definitions differ from the manifest")


def assert_wave3_cost_settings_match_manifest(
    base_settings: TradingSettings,
    stress_settings: TradingSettings,
    manifest: Mapping[str, Any] | None = None,
) -> None:
    """Fail closed when artifact runtime costs drift from the frozen contract."""

    selected_manifest = manifest or wave3_candidate_manifest()
    expected = selected_manifest.get("cost_contract")
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
    if actual != expected:
        raise ResearchError("runtime cost settings differ from the manifest")


def wave3_candidate_manifest(
    config: NestedWalkForwardConfig | None = None,
) -> dict[str, Any]:
    """Return the exact candidate payload frozen before the final experiment."""

    selected_config = config or NestedWalkForwardConfig()
    return {
        "schema_version": 3,
        "frozen_at": "2026-08-13T12:00:00+00:00",
        "candidate_limit": 12,
        "candidates": json.loads(json.dumps(_WAVE3_FROZEN_CANDIDATES)),
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
            "inner_uses_all_outer_history": True,
            "minimum_positive_inner_stress_folds": (
                selected_config.minimum_profitable_stress_folds
            ),
            "requires_positive_base_return": True,
            "requires_positive_stress_return": True,
            "ranking": [
                "highest_stress_return",
                "lowest_stress_maximum_drawdown",
                "lexicographic_name",
            ],
            "fallback": "cash",
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
            "seed": 20_260_813,
        },
        "implementation_identity": {
            "candidate_registry": (
                "bithumb_coin_trader.wave3.wave3_candidate_factories"
            ),
            "nested_selector": (
                "bithumb_coin_trader.wave3.select_nested_candidate"
            ),
            "backtester": "bithumb_coin_trader.backtest.Backtester",
        },
        "historical_prefix_sha256": (
            "dc3537c862bc54efebfd215807e2ab57da66396ebfbfcf3d5a243327b9817248"
        ),
        "posthoc_shadow_starts_after": "2026-08-12T11:00:00+00:00",
        "posthoc_shadow_prospective": False,
        "posthoc_shadow_evidence_class": "observable_before_manifest_freeze",
    }


def wave3_candidate_manifest_hash(
    manifest: Mapping[str, Any] | None = None,
) -> str:
    """SHA-256 of canonical JSON, independent of mapping insertion order."""

    payload: Mapping[str, Any] = manifest or wave3_candidate_manifest()
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def historical_prefix(
    candles: Sequence[Candle],
    *,
    count: int = WAVE3_HISTORICAL_COUNT,
) -> tuple[Candle, ...]:
    """Freeze research to the first ``count`` candles, ignoring any tail."""

    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("historical count must be a positive integer")
    if len(candles) < count:
        raise ResearchError(f"expected at least {count} historical candles")
    sample = tuple(candles[:count])
    if any(
        sample[index].timestamp <= sample[index - 1].timestamp
        for index in range(1, len(sample))
    ):
        raise ResearchError("historical candles must be strictly chronological")
    return sample


def select_nested_candidate(
    outer_train: Sequence[Candle],
    *,
    candidate_factories: Mapping[str, CandidateFactory],
    config: NestedWalkForwardConfig,
    base_settings: TradingSettings,
    stress_settings: TradingSettings,
    fold: int,
    train_start: int,
    train_end: int,
    test_start: int,
    test_end: int,
) -> SelectionDecision:
    """Select using one outer training window and no outer-test observations."""

    _validate_candidate_factories(candidate_factories)
    if (
        len(outer_train) < config.outer_train_size
        or train_start != 0
        or train_end != len(outer_train)
    ):
        raise ResearchError("outer selector requires the full expanding training prefix")
    scores = tuple(
        _score_inner_candidate(
            outer_train,
            candidate_name=name,
            candidate_factory=factory,
            config=config,
            base_settings=base_settings,
            stress_settings=stress_settings,
        )
        for name, factory in candidate_factories.items()
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
    return SelectionDecision(
        fold=fold,
        train_start=train_start,
        train_end=train_end,
        test_start=test_start,
        test_end=test_end,
        selected_candidate=selected,
        candidate_scores=scores,
    )


def build_nested_selections(
    candles: Sequence[Candle],
    *,
    candidate_factories: Mapping[str, CandidateFactory],
    config: NestedWalkForwardConfig,
    base_settings: TradingSettings,
    stress_settings: TradingSettings,
) -> tuple[SelectionDecision, ...]:
    """Build all decisions before any outer OOS execution is scored."""

    sample = historical_prefix(candles, count=config.historical_count)
    decisions: list[SelectionDecision] = []
    for fold, (train_start, train_end, test_start, test_end) in enumerate(
        config.outer_boundaries()
    ):
        decisions.append(
            select_nested_candidate(
                sample[:train_end],
                candidate_factories=candidate_factories,
                config=config,
                base_settings=base_settings,
                stress_settings=stress_settings,
                fold=fold,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
    return tuple(decisions)


def execute_nested_outer_oos(
    candles: Sequence[Candle],
    *,
    decisions: Sequence[SelectionDecision],
    candidate_factories: Mapping[str, CandidateFactory],
    config: NestedWalkForwardConfig,
    base_settings: TradingSettings,
    stress_settings: TradingSettings,
) -> NestedOosResult:
    """Execute all outer folds once, preserving positions and switch costs."""

    sample = historical_prefix(candles, count=config.historical_count)
    _validate_candidate_factories(candidate_factories)
    _validate_decisions(decisions, config, candidate_factories)

    execution_candles: list[Candle] = []
    execution_signals: list[Signal] = []
    for decision in decisions:
        train = sample[decision.train_start : decision.train_end]
        test = sample[decision.test_start : decision.test_end]
        window_candles = [train[-1], *test]
        if decision.selected_candidate is None:
            window_signals = [Signal.FLAT] * len(window_candles)
        else:
            strategy = candidate_factories[decision.selected_candidate]()
            combined = (*train, *test)
            generated = strategy.generate(combined)
            if len(generated) != len(combined):
                raise ResearchError("candidate returned the wrong signal count")
            window_signals = [
                Signal(signal) for signal in generated[len(train) - 1 :]
            ]

        if not execution_candles:
            execution_candles.extend(window_candles)
            execution_signals.extend(window_signals)
            continue
        if execution_candles[-1].timestamp != window_candles[0].timestamp:
            raise ResearchError("outer OOS execution windows must be contiguous")
        # The newly selected strategy owns the close immediately before its
        # fold.  Any state change fills at the first OOS open and pays costs.
        execution_signals[-1] = window_signals[0]
        execution_candles.extend(window_candles[1:])
        execution_signals.extend(window_signals[1:])

    base_continuous = Backtester(base_settings, allow_short=False).run(
        execution_candles, execution_signals
    )
    stress_continuous = Backtester(stress_settings, allow_short=False).run(
        execution_candles, execution_signals
    )
    return NestedOosResult(
        config=config,
        decisions=tuple(decisions),
        base=_continuous_project_report(
            base_continuous,
            execution_candles,
            decisions,
            settings=base_settings,
            candidate_name="wave3_nested_selector",
        ),
        stress=_continuous_project_report(
            stress_continuous,
            execution_candles,
            decisions,
            settings=stress_settings,
            candidate_name="wave3_nested_selector",
        ),
    )


def run_nested_continuous_oos(
    candles: Sequence[Candle],
    *,
    candidate_factories: Mapping[str, CandidateFactory],
    config: NestedWalkForwardConfig,
    base_settings: TradingSettings | None = None,
    stress_settings: TradingSettings | None = None,
) -> NestedOosResult:
    """Select in nested training folds, then run one continuous outer OOS path."""

    selected_base = base_settings or TradingSettings()
    selected_stress = stress_settings or TradingSettings(
        fee_rate=0.005, slippage_bps=10
    )
    decisions = build_nested_selections(
        candles,
        candidate_factories=candidate_factories,
        config=config,
        base_settings=selected_base,
        stress_settings=selected_stress,
    )
    return execute_nested_outer_oos(
        candles,
        decisions=decisions,
        candidate_factories=candidate_factories,
        config=config,
        base_settings=selected_base,
        stress_settings=selected_stress,
    )


def run_wave3_nested_research(
    candles: Sequence[Candle],
    *,
    base_settings: TradingSettings | None = None,
    stress_settings: TradingSettings | None = None,
) -> NestedOosResult:
    """Run the exact pre-registered Wave 3 nested experiment."""

    return run_nested_continuous_oos(
        candles,
        candidate_factories=wave3_candidate_factories(),
        config=NestedWalkForwardConfig(),
        base_settings=base_settings,
        stress_settings=stress_settings,
    )


def fixed_wave3_comparison(
    candles: Sequence[Candle],
    *,
    settings: TradingSettings | None = None,
) -> CandidateComparisonReport:
    """Diagnose all five fixed candidates on identical outer folds."""

    sample = historical_prefix(candles)
    return compare_candidate_factories(
        sample,
        candidate_factories=wave3_candidate_factories(),
        train_size=19_200,
        test_size=2_400,
        settings=settings,
        expanding=True,
    )


def fixed_control_comparison(
    candles: Sequence[Candle],
    *,
    settings: TradingSettings | None = None,
) -> CandidateComparisonReport:
    """Evaluate the previous study winner and a long-only buy/hold control."""

    class _AlwaysLong:
        def generate(self, observations: Sequence[Candle], **_: object) -> list[Signal]:
            return [Signal.LONG] * len(observations)

    registry = registered_candidate_factories()
    sample = historical_prefix(candles)
    return compare_candidate_factories(
        sample,
        candidate_factories={
            PREVIOUS_BEST_CANDIDATE: registry[PREVIOUS_BEST_CANDIDATE],
            "buy_and_hold_long": _AlwaysLong,
        },
        train_size=19_200,
        test_size=2_400,
        settings=settings,
        expanding=True,
    )


def deterministic_daily_moving_block_bootstrap(
    candidate_equity_curve: Sequence[float],
    control_equity_curve: Sequence[float],
    execution_candles: Sequence[Candle],
    *,
    block_days: int = 7,
    iterations: int = 5_000,
    seed: int = 20_260_813,
) -> MovingBlockBootstrapResult:
    """Bootstrap KST daily log-return differences with moving blocks.

    The returned distribution is the compounded candidate-minus-control log
    return.  ``Random(seed)`` makes the evidence exactly reproducible.
    """

    for name, value in (("block_days", block_days), ("iterations", iterations)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if not (
        len(candidate_equity_curve)
        == len(control_equity_curve)
        == len(execution_candles)
    ):
        raise ValueError("equity curves and candles must align")
    if len(execution_candles) < 2:
        raise ValueError("bootstrap requires at least two observations")

    candidate_daily = _daily_end_equities(candidate_equity_curve, execution_candles)
    control_daily = _daily_end_equities(control_equity_curve, execution_candles)
    if tuple(candidate_daily) != tuple(control_daily):
        raise ValueError("candidate and control daily dates must align")
    dates = tuple(candidate_daily)
    if len(dates) < 2:
        raise ValueError("bootstrap requires at least two KST daily closes")
    differences = tuple(
        log(candidate_daily[dates[index]] / candidate_daily[dates[index - 1]])
        - log(control_daily[dates[index]] / control_daily[dates[index - 1]])
        for index in range(1, len(dates))
    )
    actual_block = min(block_days, len(differences))
    block_starts = len(differences) - actual_block + 1
    random = Random(seed)
    samples: list[float] = []
    for _ in range(iterations):
        draw: list[float] = []
        while len(draw) < len(differences):
            start = random.randrange(block_starts)
            draw.extend(differences[start : start + actual_block])
        samples.append(exp(fsum(draw[: len(differences)])) - 1.0)
    samples.sort()
    return MovingBlockBootstrapResult(
        observation_count=len(differences),
        block_days=actual_block,
        iterations=iterations,
        seed=seed,
        point_estimate=exp(fsum(differences)) - 1.0,
        lower_95=_quantile(samples, 0.025),
        median=_quantile(samples, 0.5),
        upper_95=_quantile(samples, 0.975),
        probability_positive=sum(value > 0 for value in samples) / iterations,
    )


def project_report_as_dict(report: ProjectResearchReport) -> dict[str, Any]:
    """Return compact JSON-safe metrics and fold evidence."""

    return {
        "candidate_name": report.candidate_name,
        "compounded_return": report.compounded_return,
        "maximum_drawdown": report.maximum_drawdown,
        "mean_sharpe": report.mean_sharpe,
        "trade_count": report.trade_count,
        "weighted_win_rate": report.weighted_win_rate,
        "profitable_folds": sum(
            fold.result.total_return > 0 for fold in report.folds
        ),
        "fold_count": len(report.folds),
        "folds": [
            {
                "fold": fold.fold + 1,
                "train": [fold.train_start, fold.train_end],
                "test": [fold.test_start, fold.test_end],
                "initial_equity_krw": fold.result.initial_equity,
                "final_equity_krw": fold.result.final_equity,
                "total_return": fold.result.total_return,
                "max_drawdown": fold.result.max_drawdown,
                "sharpe": fold.result.sharpe,
                "trade_count": fold.result.trade_count,
                "win_rate": fold.result.win_rate,
                "exposure": fold.result.exposure,
            }
            for fold in report.folds
        ],
        "oos_equity_curve": list(report.oos_equity_curve),
    }


def _score_inner_candidate(
    outer_train: Sequence[Candle],
    *,
    candidate_name: str,
    candidate_factory: CandidateFactory,
    config: NestedWalkForwardConfig,
    base_settings: TradingSettings,
    stress_settings: TradingSettings,
) -> CandidateInnerScore:
    execution_candles: list[Candle] = []
    execution_signals: list[Signal] = []
    inner_boundaries = config.inner_boundaries(len(outer_train))
    for _, train_end, test_start, test_end in inner_boundaries:
        prefix = outer_train[:test_end]
        generated = candidate_factory().generate(prefix)
        if len(generated) != len(prefix):
            raise ResearchError("candidate returned the wrong signal count")
        fold_candles = [prefix[test_start - 1], *prefix[test_start:test_end]]
        fold_signals = [
            Signal(signal) for signal in generated[test_start - 1 : test_end]
        ]
        if not execution_candles:
            execution_candles.extend(fold_candles)
            execution_signals.extend(fold_signals)
        else:
            if execution_candles[-1].timestamp != fold_candles[0].timestamp:
                raise ResearchError("inner validation folds must be contiguous")
            execution_signals[-1] = fold_signals[0]
            execution_candles.extend(fold_candles[1:])
            execution_signals.extend(fold_signals[1:])

    base = Backtester(base_settings, allow_short=False).run(
        execution_candles, execution_signals
    )
    stress = Backtester(stress_settings, allow_short=False).run(
        execution_candles, execution_signals
    )
    base_folds = _slice_inner_results(
        base, execution_candles, config, settings=base_settings
    )
    stress_folds = _slice_inner_results(
        stress, execution_candles, config, settings=stress_settings
    )
    profitable_stress = sum(result.total_return > 0 for result in stress_folds)
    qualifies = (
        base.total_return > 0
        and stress.total_return > 0
        and profitable_stress >= config.minimum_profitable_stress_folds
    )
    return CandidateInnerScore(
        candidate_name=candidate_name,
        base_compounded_return=base.total_return,
        base_maximum_drawdown=base.max_drawdown,
        stress_compounded_return=stress.total_return,
        stress_maximum_drawdown=stress.max_drawdown,
        base_fold_returns=tuple(result.total_return for result in base_folds),
        stress_fold_returns=tuple(result.total_return for result in stress_folds),
        profitable_stress_fold_count=profitable_stress,
        qualifies=qualifies,
    )


def _slice_inner_results(
    result: BacktestResult,
    execution_candles: Sequence[Candle],
    config: NestedWalkForwardConfig,
    *,
    settings: TradingSettings,
) -> tuple[BacktestResult, ...]:
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
    result: BacktestResult,
    execution_candles: Sequence[Candle],
    decisions: Sequence[SelectionDecision],
    *,
    settings: TradingSettings,
    candidate_name: str,
) -> ProjectResearchReport:
    backtester = Backtester(settings, allow_short=False)
    test_size = decisions[0].test_end - decisions[0].test_start
    folds: list[WalkForwardFold[BacktestResult]] = []
    for decision in decisions:
        start = decision.fold * test_size
        end = start + test_size
        folds.append(
            WalkForwardFold(
                fold=decision.fold,
                train_start=decision.train_start,
                train_end=decision.train_end,
                test_start=decision.test_start,
                test_end=decision.test_end,
                result=backtester.slice_result(
                    result, execution_candles, start=start, end=end
                ),
            )
        )
    trade_count = sum(fold.result.trade_count for fold in folds)
    weighted_wins = sum(
        fold.result.win_rate * fold.result.trade_count for fold in folds
    )
    return ProjectResearchReport(
        folds=tuple(folds),
        compounded_return=result.total_return,
        maximum_drawdown=result.max_drawdown,
        mean_sharpe=fmean(fold.result.sharpe for fold in folds),
        trade_count=trade_count,
        weighted_win_rate=weighted_wins / trade_count if trade_count else 0.0,
        oos_equity_curve=result.equity_curve,
        candidate_name=candidate_name,
    )


def _validate_candidate_factories(
    candidate_factories: Mapping[str, CandidateFactory],
) -> None:
    if not candidate_factories:
        raise ResearchError("at least one candidate is required")
    if any(
        not name or not callable(factory)
        for name, factory in candidate_factories.items()
    ):
        raise ResearchError("candidate names must be non-empty and factories callable")


def _validate_decisions(
    decisions: Sequence[SelectionDecision],
    config: NestedWalkForwardConfig,
    candidate_factories: Mapping[str, CandidateFactory],
) -> None:
    expected = config.outer_boundaries()
    if len(decisions) != len(expected):
        raise ResearchError("selection decision count does not match outer folds")
    for fold, (decision, boundary) in enumerate(zip(decisions, expected, strict=True)):
        if decision.fold != fold or (
            decision.train_start,
            decision.train_end,
            decision.test_start,
            decision.test_end,
        ) != boundary:
            raise ResearchError("selection decision boundaries do not match config")
        if (
            decision.selected_candidate is not None
            and decision.selected_candidate not in candidate_factories
        ):
            raise ResearchError("selection decision names an unknown candidate")


def _daily_end_equities(
    curve: Sequence[float], candles: Sequence[Candle]
) -> dict[str, float]:
    daily: dict[str, float] = {}
    for candle, value in zip(candles, curve, strict=True):
        if value <= 0:
            raise ValueError("bootstrap equity must stay positive")
        daily[candle.timestamp.astimezone(KST).date().isoformat()] = float(value)
    return daily


def _quantile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("quantile requires observations")
    position = (len(sorted_values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight
