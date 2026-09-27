"""Governed train-only walk-forward execution for target-weight strategies."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
import hashlib
import json
import math
from statistics import mean, median, pstdev
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence

from ..config import TradingSettings
from ..models import Candle
from .backtesting import SpotResearchBacktester
from .costs import SpotCostScenario
from .evaluation import ChronologicalFold, create_chronological_folds
from .result_schema import ExperimentResult, ResultStatus


class WalkForwardGovernanceError(ValueError):
    """Raised when a strategy violates the train-only/frozen-model contract."""


class FittedTargetWeightStrategy(Protocol):
    def parameters(self) -> Mapping[str, Any]: ...

    def target_weight(self, point_in_time_history: Sequence[Candle]) -> float: ...


class TrainOnlyTargetWeightStrategy(Protocol):
    def fit(self, training_candles: Sequence[Candle]) -> FittedTargetWeightStrategy: ...


@dataclass(frozen=True, slots=True)
class WalkForwardFoldRun:
    fold_id: int
    train_start_utc: str
    train_end_utc: str
    validation_start_utc: str
    validation_end_utc: str
    train_samples: int
    validation_samples: int
    frozen_parameter_sha256: str
    result: ExperimentResult

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["result"] = self.result.to_dict()
        return payload


@dataclass(frozen=True, slots=True)
class WalkForwardReport:
    dataset_id: str
    dataset_sha256: str
    code_revision: str
    candidate_family: str
    strategy_id: str
    window_mode: str
    n_folds: int
    purge_s: float
    embargo_s: float
    seed: int
    folds: tuple[WalkForwardFoldRun, ...]
    aggregates: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "dataset_id": self.dataset_id,
            "dataset_sha256": self.dataset_sha256,
            "code_revision": self.code_revision,
            "candidate_family": self.candidate_family,
            "strategy_id": self.strategy_id,
            "window_mode": self.window_mode,
            "n_folds": self.n_folds,
            "purge_s": self.purge_s,
            "embargo_s": self.embargo_s,
            "seed": self.seed,
            "folds": [fold.to_dict() for fold in self.folds],
            "aggregates": self.aggregates,
        }


def fold_run_from_dict(value: Mapping[str, Any]) -> WalkForwardFoldRun:
    """Load one previously written fold record for idempotent batch resume."""
    fields = {
        "fold_id", "train_start_utc", "train_end_utc", "validation_start_utc",
        "validation_end_utc", "train_samples", "validation_samples",
        "frozen_parameter_sha256", "result",
    }
    if set(value) != fields or not isinstance(value.get("result"), Mapping):
        raise ValueError("invalid persisted walk-forward fold record")
    result = ExperimentResult.from_dict(value["result"])
    return WalkForwardFoldRun(
        fold_id=value["fold_id"],
        train_start_utc=value["train_start_utc"],
        train_end_utc=value["train_end_utc"],
        validation_start_utc=value["validation_start_utc"],
        validation_end_utc=value["validation_end_utc"],
        train_samples=value["train_samples"],
        validation_samples=value["validation_samples"],
        frozen_parameter_sha256=value["frozen_parameter_sha256"],
        result=result,
    )


def run_walk_forward(
    candles: Sequence[Candle],
    strategy_factory: Callable[[int], TrainOnlyTargetWeightStrategy],
    cost_scenarios: Sequence[SpotCostScenario],
    *,
    dataset_id: str,
    dataset_sha256: str,
    code_revision: str,
    candidate_family: str,
    strategy_id: str,
    strategy_config: Mapping[str, Any],
    feature_config: Mapping[str, Any],
    n_folds: int,
    window_mode: Literal["ROLLING", "EXPANDING"],
    purge_s: float,
    embargo_s: float,
    seed: int,
    settings: TradingSettings | None = None,
    resume_completed: Mapping[tuple[int, str], WalkForwardFoldRun] | None = None,
    on_fold_complete: Callable[[WalkForwardFoldRun], None] | None = None,
) -> WalkForwardReport:
    """Fit on each fold's training candles and predict one causal prefix at a time.

    Validation candles and their returns are never passed to ``fit``. Every
    predictor call receives only candles observable through that bar's close.
    The fitted model's declared parameter manifest is hashed before prediction
    and rechecked after every call; mutating fitted parameters during evaluation
    fails the run.
    """
    _validate_identity_inputs(
        dataset_id=dataset_id,
        dataset_sha256=dataset_sha256,
        code_revision=code_revision,
        candidate_family=candidate_family,
        strategy_id=strategy_id,
        seed=seed,
        purge_s=purge_s,
        embargo_s=embargo_s,
    )
    if not candles or any(
        candles[index].timestamp <= candles[index - 1].timestamp
        for index in range(1, len(candles))
    ):
        raise WalkForwardGovernanceError("walk-forward candles must be non-empty and strictly chronological")
    if window_mode not in {"ROLLING", "EXPANDING"}:
        raise WalkForwardGovernanceError("window_mode must be ROLLING or EXPANDING")
    if not cost_scenarios:
        raise WalkForwardGovernanceError("at least one explicit cost scenario is required")
    if len({scenario.name for scenario in cost_scenarios}) != len(cost_scenarios):
        raise WalkForwardGovernanceError("cost scenario names must be unique")

    _canonical_json(dict(strategy_config), "strategy_config")
    _canonical_json(dict(feature_config), "feature_config")
    timestamps_ns = [int(candle.timestamp.timestamp() * 1_000_000_000) for candle in candles]
    folds = create_chronological_folds(
        timestamps_ns,
        n_folds=n_folds,
        embargo_s=embargo_s,
        expanding=window_mode == "EXPANDING",
        purge_s=purge_s,
    )
    if not folds:
        raise WalkForwardGovernanceError("fold configuration produced no valid train/validation folds")

    cached_runs = dict(resume_completed or {})
    expected_cache_keys = {
        (fold.fold_id, scenario.name)
        for fold in folds
        for scenario in cost_scenarios
    }
    unexpected_cache_keys = set(cached_runs) - expected_cache_keys
    if unexpected_cache_keys:
        raise WalkForwardGovernanceError(
            "resume cache contains fold/cost keys outside the current experiment: "
            + ", ".join(f"{fold}/{scenario}" for fold, scenario in sorted(unexpected_cache_keys))
        )
    fold_runs: list[WalkForwardFoldRun] = []
    for fold in folds:
        cached_for_fold = {
            scenario.name: cached_runs[(fold.fold_id, scenario.name)]
            for scenario in cost_scenarios
            if (fold.fold_id, scenario.name) in cached_runs
        }
        _validate_cached_runs(
            cached_for_fold,
            fold=fold,
            candles=candles,
            dataset_id=dataset_id,
            code_revision=code_revision,
            candidate_family=candidate_family,
            strategy_id=strategy_id,
            expected_scenarios={scenario.name for scenario in cost_scenarios},
        )
        computed = _run_fold(
            candles=candles,
            strategy_factory=strategy_factory,
            fold=fold,
            cost_scenarios=tuple(
                scenario for scenario in cost_scenarios
                if scenario.name not in cached_for_fold
            ),
            dataset_id=dataset_id,
            dataset_sha256=dataset_sha256,
            code_revision=code_revision,
            candidate_family=candidate_family,
            strategy_id=strategy_id,
            strategy_config=dict(strategy_config),
            feature_config=dict(feature_config),
            window_mode=window_mode,
            purge_s=purge_s,
            embargo_s=embargo_s,
            seed=seed,
            settings=settings,
        )
        computed_by_scenario = {run.result.cost_scenario: run for run in computed}
        for scenario in cost_scenarios:
            cached = cached_for_fold.get(scenario.name)
            if cached is not None:
                fold_runs.append(cached)
            else:
                completed = computed_by_scenario[scenario.name]
                fold_runs.append(completed)
                if on_fold_complete is not None:
                    on_fold_complete(completed)
    return WalkForwardReport(
        dataset_id=dataset_id,
        dataset_sha256=dataset_sha256,
        code_revision=code_revision,
        candidate_family=candidate_family,
        strategy_id=strategy_id,
        window_mode=window_mode,
        n_folds=len(folds),
        purge_s=purge_s,
        embargo_s=embargo_s,
        seed=seed,
        folds=tuple(fold_runs),
        aggregates=_aggregate(fold_runs),
    )


def _run_fold(
    *,
    candles: Sequence[Candle],
    strategy_factory: Callable[[int], TrainOnlyTargetWeightStrategy],
    fold: ChronologicalFold,
    cost_scenarios: Sequence[SpotCostScenario],
    dataset_id: str,
    dataset_sha256: str,
    code_revision: str,
    candidate_family: str,
    strategy_id: str,
    strategy_config: dict[str, Any],
    feature_config: dict[str, Any],
    window_mode: str,
    purge_s: float,
    embargo_s: float,
    seed: int,
    settings: TradingSettings | None,
) -> list[WalkForwardFoldRun]:
    if not cost_scenarios:
        return []
    training = tuple(
        candle for candle in candles
        if fold.train_start_ns <= int(candle.timestamp.timestamp() * 1e9) <= fold.train_end_ns
    )
    validation_indices = [
        index for index, candle in enumerate(candles)
        if fold.test_start_ns <= int(candle.timestamp.timestamp() * 1e9) <= fold.test_end_ns
    ]
    if not training or not validation_indices:
        raise WalkForwardGovernanceError(f"fold {fold.fold_id} has an empty train or validation partition")
    validation = tuple(candles[index] for index in validation_indices)
    if training[-1].timestamp >= validation[0].timestamp:
        raise WalkForwardGovernanceError(f"fold {fold.fold_id} train/validation boundary overlaps")
    minimum_gap_ns = int((purge_s + embargo_s) * 1_000_000_000)
    actual_gap_ns = int((validation[0].timestamp - training[-1].timestamp).total_seconds() * 1_000_000_000)
    if actual_gap_ns < minimum_gap_ns:
        raise WalkForwardGovernanceError(f"fold {fold.fold_id} does not enforce purge plus embargo")

    model = strategy_factory(seed).fit(training)
    frozen_parameters = dict(model.parameters())
    parameters_json = _canonical_json(frozen_parameters, "fitted parameter manifest")
    parameter_hash = hashlib.sha256(parameters_json.encode("utf-8")).hexdigest()

    first_validation_index = validation_indices[0]
    warmup_index = first_validation_index - 1
    if warmup_index < 0:
        raise WalkForwardGovernanceError(f"fold {fold.fold_id} lacks a pre-validation candle for next-open fills")
    evaluation_candles = [candles[warmup_index], *validation]
    weights: list[float] = []
    for candle_index in [warmup_index, *validation_indices]:
        history = tuple(candles[: candle_index + 1])
        if history[-1].timestamp != candles[candle_index].timestamp:
            raise WalkForwardGovernanceError("prediction history contains data after its decision candle")
        weight = model.target_weight(history)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or not 0.0 <= weight <= 1.0:
            raise WalkForwardGovernanceError("strategy prediction must be a finite target weight in [0, 1]")
        if _canonical_json(dict(model.parameters()), "fitted parameter manifest") != parameters_json:
            raise WalkForwardGovernanceError(
                f"fold {fold.fold_id} fitted parameters changed during validation prediction"
            )
        weights.append(float(weight))

    runs: list[WalkForwardFoldRun] = []
    for scenario in cost_scenarios:
        spot_run = SpotResearchBacktester(settings).run(
            evaluation_candles,
            weights,
            cost_scenario=scenario,
        )
        metrics = _metrics(spot_run.result, evaluation_candles, validation)
        unsupported = spot_run.result.unsupported_execution_semantics
        result_identity = {
            "dataset_sha256": dataset_sha256,
            "code_revision": code_revision,
            "candidate_family": candidate_family,
            "strategy_id": strategy_id,
            "strategy_config": strategy_config,
            "feature_config": feature_config,
            "fitted_parameters": frozen_parameters,
            "fitted_parameter_sha256": parameter_hash,
            "cost_scenario": scenario.to_dict(),
            "fold": asdict(fold),
            "window_mode": window_mode,
            "purge_s": purge_s,
            "embargo_s": embargo_s,
            "seed": seed,
        }
        experiment_id = "exp_" + hashlib.sha256(
            _canonical_json(result_identity, "experiment identity").encode("utf-8")
        ).hexdigest()
        result = ExperimentResult(
            experiment_id=experiment_id,
            candidate_family=candidate_family,
            strategy_id=strategy_id,
            dataset_id=dataset_id,
            code_revision=code_revision,
            period={
                "start_utc": validation[0].timestamp.isoformat(),
                "end_utc": validation[-1].timestamp.isoformat(),
            },
            fold=fold.fold_id,
            cost_scenario=scenario.name,
            status=ResultStatus.UNSUPPORTED if unsupported else ResultStatus.COMPLETED,
            failure_reason=";".join(unsupported) if unsupported else None,
            **metrics,
        )
        runs.append(WalkForwardFoldRun(
            fold_id=fold.fold_id,
            train_start_utc=training[0].timestamp.isoformat(),
            train_end_utc=training[-1].timestamp.isoformat(),
            validation_start_utc=validation[0].timestamp.isoformat(),
            validation_end_utc=validation[-1].timestamp.isoformat(),
            train_samples=len(training),
            validation_samples=len(validation),
            frozen_parameter_sha256=parameter_hash,
            result=result,
        ))
    return runs


def _validate_cached_runs(
    cached: Mapping[str, WalkForwardFoldRun],
    *,
    fold: ChronologicalFold,
    candles: Sequence[Candle],
    dataset_id: str,
    code_revision: str,
    candidate_family: str,
    strategy_id: str,
    expected_scenarios: set[str],
) -> None:
    if set(cached) - expected_scenarios:
        raise WalkForwardGovernanceError("resume cache contains an unexpected cost scenario")
    training = [
        candle for candle in candles
        if fold.train_start_ns <= int(candle.timestamp.timestamp() * 1e9) <= fold.train_end_ns
    ]
    validation = [
        candle for candle in candles
        if fold.test_start_ns <= int(candle.timestamp.timestamp() * 1e9) <= fold.test_end_ns
    ]
    if not training or not validation:
        raise WalkForwardGovernanceError("persisted fold no longer matches current data boundaries")
    for scenario_name, run in cached.items():
        if (
            run.fold_id != fold.fold_id
            or run.train_start_utc != training[0].timestamp.isoformat()
            or run.train_end_utc != training[-1].timestamp.isoformat()
            or run.validation_start_utc != validation[0].timestamp.isoformat()
            or run.validation_end_utc != validation[-1].timestamp.isoformat()
            or run.train_samples != len(training)
            or run.validation_samples != len(validation)
            or run.result.fold != fold.fold_id
            or run.result.cost_scenario != scenario_name
            or run.result.dataset_id != dataset_id
            or run.result.code_revision != code_revision
            or run.result.candidate_family != candidate_family
            or run.result.strategy_id != strategy_id
        ):
            raise WalkForwardGovernanceError(
                f"persisted fold {fold.fold_id}/{scenario_name} does not match current inputs"
            )


def _metrics(result: Any, execution_candles: Sequence[Candle], validation: Sequence[Candle]) -> dict[str, Any]:
    equity = result.equity_curve
    returns = [equity[index] / equity[index - 1] - 1.0 for index in range(1, len(equity)) if equity[index - 1] > 0]
    intervals = [
        (execution_candles[index].timestamp - execution_candles[index - 1].timestamp).total_seconds()
        for index in range(1, len(execution_candles))
    ]
    typical_seconds = median(intervals) if intervals else 0.0
    periods_per_year = 365.25 * 24 * 60 * 60 / typical_seconds if typical_seconds > 0 else 0.0
    volatility = pstdev(returns) if len(returns) > 1 else 0.0
    sharpe = mean(returns) / volatility * math.sqrt(periods_per_year) if volatility > 0 else None
    downside = [min(0.0, value) for value in returns]
    downside_deviation = math.sqrt(sum(value * value for value in downside) / len(downside)) if downside else 0.0
    sortino = mean(returns) / downside_deviation * math.sqrt(periods_per_year) if downside_deviation > 0 else None
    days = (validation[-1].timestamp - validation[0].timestamp).total_seconds() / (365.25 * 86400.0)
    cagr = (result.final_equity / result.initial_equity) ** (1.0 / days) - 1.0 if days > 0 and result.final_equity > 0 else None
    calmar = cagr / result.max_drawdown if cagr is not None and result.max_drawdown > 0 else None
    trade_count = _round_trip_count(result.fills)
    worst_day, worst_week = _worst_periods(result.equity_curve, validation)
    return {
        "gross_return": None,
        "net_return": result.total_return,
        "cagr": cagr,
        "mdd": result.max_drawdown,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "profit_factor": None,
        "expectancy": None,
        "win_rate": None,
        "trade_count": trade_count,
        "turnover": result.turnover,
        "exposure": result.exposure,
        "fees": result.total_fees,
        "slippage_cost": sum(fill.slippage_cost for fill in result.fills),
        "worst_day": worst_day,
        "worst_week": worst_week,
    }


def _round_trip_count(fills: Sequence[Any]) -> int:
    quantity = 0.0
    completed = 0
    for fill in fills:
        if fill.side == "buy":
            quantity += fill.quantity
        elif fill.side == "sell":
            quantity = max(0.0, quantity - fill.quantity)
            if quantity <= 1e-8:
                completed += 1
    return completed


def _worst_periods(
    equity_curve: Sequence[float], validation: Sequence[Candle]
) -> tuple[float | None, float | None]:
    def grouped_returns(key_fn: Callable[[datetime], object]) -> list[float]:
        values: dict[object, tuple[float, float]] = {}
        previous_equity = equity_curve[0]
        for index, candle in enumerate(validation):
            bucket = key_fn(candle.timestamp)
            end_equity = equity_curve[index + 1]
            if bucket in values:
                start_equity = values[bucket][0]
                values[bucket] = (start_equity, end_equity)
            else:
                values[bucket] = (previous_equity, end_equity)
            previous_equity = end_equity
        return [end / start - 1.0 for start, end in values.values() if start > 0]

    daily = grouped_returns(lambda value: value.date())
    weekly = grouped_returns(lambda value: value.isocalendar()[:2])
    return min(daily) if daily else None, min(weekly) if weekly else None


def _aggregate(folds: Sequence[WalkForwardFoldRun]) -> dict[str, dict[str, Any]]:
    by_scenario: dict[str, list[ExperimentResult]] = defaultdict(list)
    for fold in folds:
        by_scenario[fold.result.cost_scenario].append(fold.result)
    return {
        name: {
            "fold_count": len(results),
            "completed_fold_count": sum(result.status is ResultStatus.COMPLETED for result in results),
            "unsupported_fold_count": sum(result.status is ResultStatus.UNSUPPORTED for result in results),
            "mean_net_return": mean([result.net_return for result in results if result.net_return is not None]),
            "worst_mdd": max([result.mdd for result in results if result.mdd is not None], default=0.0),
            "mean_sharpe": mean([result.sharpe for result in results if result.sharpe is not None]) if any(result.sharpe is not None for result in results) else None,
            "fees": sum(result.fees or 0.0 for result in results),
            "slippage_cost": sum(result.slippage_cost or 0.0 for result in results),
            "status": "UNSUPPORTED" if any(result.status is ResultStatus.UNSUPPORTED for result in results) else "COMPLETE",
        }
        for name, results in sorted(by_scenario.items())
    }


def _validate_identity_inputs(
    *,
    dataset_id: str,
    dataset_sha256: str,
    code_revision: str,
    candidate_family: str,
    strategy_id: str,
    seed: int,
    purge_s: float,
    embargo_s: float,
) -> None:
    for name, value in (
        ("dataset_id", dataset_id),
        ("candidate_family", candidate_family),
        ("strategy_id", strategy_id),
    ):
        if not isinstance(value, str) or not value.strip():
            raise WalkForwardGovernanceError(f"{name} must be non-empty")
    if not _is_hex_hash(dataset_sha256, {64}):
        raise WalkForwardGovernanceError("dataset_sha256 must be a SHA-256 hex digest")
    if not _is_hex_hash(code_revision, {40, 64}):
        raise WalkForwardGovernanceError("code_revision must be a Git SHA hex digest")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise WalkForwardGovernanceError("seed must be a non-negative integer")
    if not math.isfinite(purge_s) or purge_s < 0 or not math.isfinite(embargo_s) or embargo_s < 0:
        raise WalkForwardGovernanceError("purge_s and embargo_s must be finite non-negative seconds")


def _is_hex_hash(value: str, lengths: set[int]) -> bool:
    return isinstance(value, str) and len(value) in lengths and all(
        char in "0123456789abcdefABCDEF" for char in value
    )


def _canonical_json(value: Mapping[str, Any], label: str) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WalkForwardGovernanceError(f"{label} must be finite JSON data") from exc
