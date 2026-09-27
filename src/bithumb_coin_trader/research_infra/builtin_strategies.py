"""Small, explicit strategies used by the local research-batch CLI.

These are execution smoke/baseline definitions, not promoted candidates. More
complex repository strategies must be adapted behind the same train-only
target-weight contract before the CLI will execute them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import math
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..daily_strategy_candidates import DailyCandidate, daily_candidate_factories
from ..models import Candle, Signal
from ..strategy_v4_candidates import (
    V4AdaptiveDonchianAtrStrategy,
    V4AdxKamaConfluenceStrategy,
    V4KamaTrendStrategy,
    V4TripleMomentumFilterStrategy,
    V4TrendVolatilityRegimeStrategy,
    V4VolatilityAdjustedMomentumStrategy,
)
from ..strategy_v3_candidates import strategy_v3_candidate_factories
from ..strategy_v4b_candidates import V452WeekHighBreakoutStrategy, V4TrendQualityFilterStrategy
from ..strategy_v5_candidates import V5RegimeAdaptiveDonchianStrategy, V5TrendPullbackStrategy
from ..strategy_v6_candidates import V6DailyEmaPullbackStrategy, V6FastDonchianSwingStrategy
from .walk_forward_runner import TrainOnlyTargetWeightStrategy


class UnsupportedStrategyError(ValueError):
    """Raised when the batch CLI is asked to run an unregistered strategy."""


class _TargetWeightCandidate(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def required_history_bars(self) -> int: ...

    def generate(self, candles: Sequence[Candle]) -> list[float]: ...


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


@dataclass(frozen=True, slots=True)
class _TargetWeightCandidateAdapter:
    """Adapt an existing fixed single-market daily target-weight strategy."""

    candidate: _TargetWeightCandidate

    def fit(self, training_candles: Sequence[Candle]) -> "_TargetWeightCandidateAdapter":
        if len(training_candles) < self.candidate.required_history_bars:
            raise ValueError("training partition is shorter than the candidate's frozen history requirement")
        self.candidate.generate(training_candles)
        return self

    def parameters(self) -> Mapping[str, Any]:
        if not is_dataclass(self.candidate):
            raise UnsupportedStrategyError("target-weight candidate must expose a dataclass parameter manifest")
        return asdict(self.candidate)

    def target_weight(self, point_in_time_history: Sequence[Candle]) -> float:
        targets = self.candidate.generate(point_in_time_history)
        if not targets:
            raise ValueError("daily target-weight candidate emitted no observations")
        return _validated_weight(targets[-1])


@dataclass(frozen=True, slots=True)
class _CoreSatelliteAdapter:
    """Combine frozen daily core and satellite weights under explicit 70/30 ratios."""

    core: V4AdaptiveDonchianAtrStrategy
    satellite: V6FastDonchianSwingStrategy | V6DailyEmaPullbackStrategy
    core_ratio: float = 0.70
    satellite_ratio: float = 0.30

    def fit(self, training_candles: Sequence[Candle]) -> "_CoreSatelliteAdapter":
        required = max(self.core.required_history_bars, self.satellite.required_history_bars)
        if len(training_candles) < required:
            raise ValueError("training partition is shorter than the composite history requirement")
        self.core.generate(training_candles)
        self.satellite.generate(training_candles)
        return self

    def parameters(self) -> Mapping[str, Any]:
        return {
            "core": asdict(self.core),
            "satellite": asdict(self.satellite),
            "core_ratio": self.core_ratio,
            "satellite_ratio": self.satellite_ratio,
        }

    def target_weight(self, point_in_time_history: Sequence[Candle]) -> float:
        core_targets = self.core.generate(point_in_time_history)
        satellite_targets = self.satellite.generate(point_in_time_history)
        if not core_targets or not satellite_targets:
            raise ValueError("Core+Satellite candidate emitted no observations")
        combined = self.core_ratio * core_targets[-1] + self.satellite_ratio * satellite_targets[-1]
        return _validated_weight(combined)


_BASELINE_IDS = {"cash", "buy_and_hold", "randomized_placebo"}
_DAILY_CANDIDATE_IDS = set(daily_candidate_factories()) - {"daily_buy_hold_benchmark"}
_V3_CANDIDATE_FACTORIES = strategy_v3_candidate_factories()
_V3_CANDIDATE_IDS = set(_V3_CANDIDATE_FACTORIES)
_V4_CANDIDATE_IDS = {
    "v4_adaptive_donchian_atr",
    "v4_trend_volatility_regime",
    "v4_kama_trend",
    "v4_triple_momentum_filter",
    "v4_adx_kama_confluence",
    "v4_volatility_adjusted_momentum",
}
_V4B_CANDIDATE_IDS = {"v4_52week_high_breakout", "v4_trend_quality_filter"}
_V5_CANDIDATE_IDS = {
    "v5_regime_adaptive_donchian",
    "v5_trend_pullback_fixed30",
    "v5_trend_pullback_voltarget25",
    "v5_trend_pullback_kelly025",
}
_V6_CANDIDATE_IDS = {"v6_fast_donchian_swing", "v6_daily_ema_pullback"}
_CORE_SATELLITE_IDS = {
    "core70_satellite30_v6_fast_donchian",
    "core70_satellite30_v6_daily_ema_pullback",
}
_GOVERNED_CANDIDATE_IDS = (
    _DAILY_CANDIDATE_IDS
    | _V3_CANDIDATE_IDS
    | _V4_CANDIDATE_IDS
    | _V4B_CANDIDATE_IDS
    | _V5_CANDIDATE_IDS
    | _V6_CANDIDATE_IDS
    | _CORE_SATELLITE_IDS
)
_TARGET_WEIGHT_CANDIDATE_FACTORIES: dict[str, Callable[[], _TargetWeightCandidate]] = {
    **_V3_CANDIDATE_FACTORIES,
    "v4_adaptive_donchian_atr": V4AdaptiveDonchianAtrStrategy,
    "v4_trend_volatility_regime": V4TrendVolatilityRegimeStrategy,
    "v4_kama_trend": V4KamaTrendStrategy,
    "v4_triple_momentum_filter": V4TripleMomentumFilterStrategy,
    "v4_adx_kama_confluence": V4AdxKamaConfluenceStrategy,
    "v4_volatility_adjusted_momentum": V4VolatilityAdjustedMomentumStrategy,
    "v4_52week_high_breakout": V452WeekHighBreakoutStrategy,
    "v4_trend_quality_filter": V4TrendQualityFilterStrategy,
    "v5_regime_adaptive_donchian": V5RegimeAdaptiveDonchianStrategy,
    "v5_trend_pullback_fixed30": lambda: V5TrendPullbackStrategy(
        name="v5_trend_pullback_fixed30", sizing_mode="fixed30"
    ),
    "v5_trend_pullback_voltarget25": lambda: V5TrendPullbackStrategy(
        name="v5_trend_pullback_voltarget25", sizing_mode="voltarget25"
    ),
    "v5_trend_pullback_kelly025": lambda: V5TrendPullbackStrategy(
        name="v5_trend_pullback_kelly025", sizing_mode="kelly025"
    ),
    "v6_fast_donchian_swing": V6FastDonchianSwingStrategy,
    "v6_daily_ema_pullback": V6DailyEmaPullbackStrategy,
}


def registered_strategy_ids() -> tuple[str, ...]:
    return (
        "buy_and_hold", "cash", "randomized_placebo", "sma_trend",
        *sorted(_GOVERNED_CANDIDATE_IDS),
    )


def governed_candidate_strategy_ids() -> frozenset[str]:
    """Strategies wired through governed research, freeze, and local PAPER paths."""
    return frozenset(_GOVERNED_CANDIDATE_IDS)


def candidate_family_for_strategy(strategy_id: str) -> str:
    if strategy_id in _DAILY_CANDIDATE_IDS:
        return "daily_weekly_trend_and_momentum"
    if strategy_id in _V3_CANDIDATE_IDS:
        return "v3_daily_target_weight"
    if strategy_id in _V4_CANDIDATE_IDS:
        return "v4_v4b_regime_breakout_and_trend"
    if strategy_id in _V4B_CANDIDATE_IDS:
        return "v4_v4b_regime_breakout_and_trend"
    if strategy_id in _V5_CANDIDATE_IDS:
        return "v5_regime_dual_momentum_pullback"
    if strategy_id in _V6_CANDIDATE_IDS | _CORE_SATELLITE_IDS:
        return "v6_satellite_and_core_satellite"
    if strategy_id in _BASELINE_IDS:
        return "baseline_controls"
    if strategy_id == "sma_trend":
        return "builtin_sma_trend_example"
    raise UnsupportedStrategyError(f"strategy {strategy_id!r} has no governed candidate family")


def strategy_source_modules(strategy_id: str) -> tuple[str, ...]:
    """Return source modules that define this strategy and its governed adapter."""
    sources = ["research_infra/builtin_strategies.py"]
    if strategy_id in _DAILY_CANDIDATE_IDS:
        sources.append("daily_strategy_candidates.py")
    elif strategy_id in _V3_CANDIDATE_IDS:
        sources.extend(("daily_strategy_candidates.py", "strategy_v3_candidates.py"))
    elif strategy_id in _V4_CANDIDATE_IDS:
        sources.extend((
            "daily_strategy_candidates.py",
            "strategy_v3_candidates.py",
            "strategy_v4_candidates.py",
        ))
    elif strategy_id in _V4B_CANDIDATE_IDS:
        sources.extend(("daily_strategy_candidates.py", "strategy_v4b_candidates.py"))
    elif strategy_id in _V5_CANDIDATE_IDS:
        sources.extend((
            "daily_strategy_candidates.py",
            "indicators.py",
            "strategy_v3_candidates.py",
            "strategy_v4_candidates.py",
            "strategy_v5_candidates.py",
        ))
    elif strategy_id in _V6_CANDIDATE_IDS:
        sources.extend((
            "daily_strategy_candidates.py",
            "strategy_v3_candidates.py",
            "strategy_v4_candidates.py",
            "strategy_v6_candidates.py",
        ))
    elif strategy_id in _CORE_SATELLITE_IDS:
        sources.extend((
            "daily_strategy_candidates.py",
            "strategy_v3_candidates.py",
            "strategy_v4_candidates.py",
            "strategy_v6_candidates.py",
        ))
    elif strategy_id not in {"cash", "buy_and_hold", "randomized_placebo", "sma_trend"}:
        raise UnsupportedStrategyError(f"strategy {strategy_id!r} has no source binding")
    return tuple(sources)


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
    if strategy_id in _DAILY_CANDIDATE_IDS:
        if parameters:
            raise UnsupportedStrategyError(
                f"{strategy_id} uses its existing frozen implementation parameters and accepts no overrides"
            )
        return _DailyCandidateAdapter(strategy_id, daily_candidate_factories()[strategy_id]())
    if strategy_id in _TARGET_WEIGHT_CANDIDATE_FACTORIES or strategy_id in _CORE_SATELLITE_IDS:
        if parameters:
            raise UnsupportedStrategyError(
                f"{strategy_id} uses its existing frozen implementation parameters and accepts no overrides"
            )
        if strategy_id in _TARGET_WEIGHT_CANDIDATE_FACTORIES:
            return _TargetWeightCandidateAdapter(_TARGET_WEIGHT_CANDIDATE_FACTORIES[strategy_id]())
        if strategy_id == "core70_satellite30_v6_fast_donchian":
            satellite = V6FastDonchianSwingStrategy()
        else:
            satellite = V6DailyEmaPullbackStrategy()
        return _CoreSatelliteAdapter(V4AdaptiveDonchianAtrStrategy(), satellite)
    raise UnsupportedStrategyError(
        f"unknown strategy_id {strategy_id!r}; registered: {', '.join(registered_strategy_ids())}"
    )


def _validated_weight(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("daily strategy emitted a non-finite target weight")
    if not 0.0 <= value <= 1.0:
        raise ValueError("daily strategy emitted a target weight outside [0, 1]")
    return float(value)
