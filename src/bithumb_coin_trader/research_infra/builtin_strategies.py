"""Small, explicit strategies used by the local research-batch CLI.

These are execution smoke/baseline definitions, not promoted candidates. More
complex repository strategies must be adapted behind the same train-only
target-weight contract before the CLI will execute them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import math
from typing import Any, Mapping, Sequence

from ..daily_strategy_candidates import DailyCandidate, daily_candidate_factories
from ..models import Candle, Signal
from .walk_forward_runner import TrainOnlyTargetWeightStrategy


class UnsupportedStrategyError(ValueError):
    """Raised when the batch CLI is asked to run an unregistered strategy."""


@dataclass(frozen=True, slots=True)
class _ConstantWeight:
    weight: float

    def fit(self, training_candles: Sequence[Candle]) -> "_ConstantWeight":
        if not training_candles:
            raise ValueError("training partition cannot be empty")
        return self

    def parameters(self) -> Mapping[str, Any]:
        return {"target_weight": self.weight}

    def target_weight(self, point_in_time_history: Sequence[Candle]) -> float:
        if not point_in_time_history:
            raise ValueError("point-in-time history cannot be empty")
        return self.weight


@dataclass(frozen=True, slots=True)
class _SmaTrend:
    lookback_bars: int
    target_weight_value: float
    entry_return_threshold: float

    def fit(self, training_candles: Sequence[Candle]) -> "_SmaTrend":
        if len(training_candles) < self.lookback_bars:
            raise ValueError("training partition is shorter than the configured lookback")
        return self

    def parameters(self) -> Mapping[str, Any]:
        return {
            "lookback_bars": self.lookback_bars,
            "target_weight": self.target_weight_value,
            "entry_return_threshold": self.entry_return_threshold,
        }

    def target_weight(self, point_in_time_history: Sequence[Candle]) -> float:
        if len(point_in_time_history) < self.lookback_bars:
            return 0.0
        closes = [candle.close for candle in point_in_time_history[-self.lookback_bars :]]
        mean_close = sum(closes) / len(closes)
        momentum = closes[-1] / closes[0] - 1.0
        return (
            self.target_weight_value
            if closes[-1] > mean_close and momentum >= self.entry_return_threshold
            else 0.0
        )


@dataclass(frozen=True, slots=True)
class _RandomizedExposure:
    seed: int
    exposure_probability: float
    target_weight_value: float

    def fit(self, training_candles: Sequence[Candle]) -> "_RandomizedExposure":
        if not training_candles:
            raise ValueError("training partition cannot be empty")
        return self

    def parameters(self) -> Mapping[str, Any]:
        return {
            "seed": self.seed,
            "exposure_probability": self.exposure_probability,
            "target_weight": self.target_weight_value,
        }

    def target_weight(self, point_in_time_history: Sequence[Candle]) -> float:
        if not point_in_time_history:
            raise ValueError("point-in-time history cannot be empty")
        candle = point_in_time_history[-1]
        key = f"{self.seed}:{candle.market}:{candle.timestamp.isoformat()}".encode("utf-8")
        draw = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") / 2**64
        return self.target_weight_value if draw < self.exposure_probability else 0.0


@dataclass(frozen=True, slots=True)
class _DailyCandidateAdapter:
    """Expose an existing frozen daily candidate through the causal WF API."""

    strategy_id: str
    candidate: DailyCandidate

    def fit(self, training_candles: Sequence[Candle]) -> "_DailyCandidateAdapter":
        if len(training_candles) < self.candidate.required_history_bars:
            raise ValueError("training partition is shorter than the candidate's frozen history requirement")
        # Generate only over training data here to validate the source's declared
        # cadence and make the no-fit/frozen-parameter behavior explicit.
        self.candidate.generate(training_candles)
        return self

    def parameters(self) -> Mapping[str, Any]:
        if not is_dataclass(self.candidate):
            raise UnsupportedStrategyError("daily candidate must expose a dataclass parameter manifest")
        return asdict(self.candidate)

    def target_weight(self, point_in_time_history: Sequence[Candle]) -> float:
        if not point_in_time_history:
            raise ValueError("point-in-time history cannot be empty")
        signal = self.candidate.generate(point_in_time_history)[-1]
        if signal not in {Signal.FLAT, Signal.LONG}:
            raise ValueError("daily candidate emitted an unsupported signal")
        return 1.0 if signal is Signal.LONG else 0.0


def registered_strategy_ids() -> tuple[str, ...]:
    daily_candidate_ids = tuple(
        sorted(set(daily_candidate_factories()) - {"daily_buy_hold_benchmark"})
    )
    return ("buy_and_hold", "cash", "randomized_placebo", "sma_trend", *daily_candidate_ids)


def create_builtin_strategy(
    strategy_id: str,
    seed: int,
    parameters: Mapping[str, Any],
) -> TrainOnlyTargetWeightStrategy:
    """Build an allowlisted strategy; reject unknown code paths fail closed."""
    if strategy_id in {"cash", "buy_and_hold"}:
        if parameters:
            raise UnsupportedStrategyError(f"{strategy_id} does not accept parameters")
        return _ConstantWeight(0.0 if strategy_id == "cash" else 1.0)
    if strategy_id == "randomized_placebo":
        allowed = {"exposure_probability", "target_weight"}
        unknown = set(parameters) - allowed
        missing = allowed - parameters.keys()
        if unknown or missing:
            raise UnsupportedStrategyError(
                "randomized_placebo parameters mismatch; "
                f"missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        probability = parameters["exposure_probability"]
        weight = parameters["target_weight"]
        if (
            isinstance(probability, bool)
            or not isinstance(probability, (int, float))
            or not math.isfinite(probability)
            or not 0.0 <= probability <= 1.0
        ):
            raise UnsupportedStrategyError("exposure_probability must be in [0, 1]")
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
            or not 0.0 <= weight <= 1.0
        ):
            raise UnsupportedStrategyError("target_weight must be in [0, 1]")
        return _RandomizedExposure(seed, float(probability), float(weight))
    if strategy_id == "sma_trend":
        allowed = {"lookback_bars", "target_weight", "entry_return_threshold"}
        unknown = set(parameters) - allowed
        missing = allowed - parameters.keys()
        if unknown or missing:
            raise UnsupportedStrategyError(
                f"sma_trend parameters mismatch; missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        lookback = parameters["lookback_bars"]
        weight = parameters["target_weight"]
        threshold = parameters["entry_return_threshold"]
        if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 2:
            raise UnsupportedStrategyError("lookback_bars must be an integer of at least 2")
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
            or not 0.0 <= weight <= 1.0
        ):
            raise UnsupportedStrategyError("target_weight must be in [0, 1]")
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(threshold)
            or threshold < -1.0
            or threshold > 10.0
        ):
            raise UnsupportedStrategyError("entry_return_threshold must be in [-1, 10]")
        return _SmaTrend(lookback, float(weight), float(threshold))
    daily_factories = daily_candidate_factories()
    if strategy_id in set(daily_factories) - {"daily_buy_hold_benchmark"}:
        if parameters:
            raise UnsupportedStrategyError(
                f"{strategy_id} uses its existing frozen implementation parameters and accepts no overrides"
            )
        return _DailyCandidateAdapter(strategy_id, daily_factories[strategy_id]())
    raise UnsupportedStrategyError(
        f"unknown strategy_id {strategy_id!r}; registered: {', '.join(registered_strategy_ids())}"
    )
