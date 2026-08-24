"""Causal, research-only meta-label candidates for 30-minute KRW-BTC.

The candidates in this module deliberately learn slowly and abstain often.
At candle ``t`` they may only train on an example whose complete forward
holding period ends at ``t``.  Predictions and calibration thresholds are
therefore based exclusively on information already observable at the signal
close.  Execution remains the backtester's responsibility at the next open.

These strategies do not self-certify a win rate.  In particular, a high
historical precision from a tiny number of trades must still be rejected by
the research harness's independent minimum-30-OOS-trades gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import exp, isfinite, log, sqrt
from typing import Callable, Sequence

from .models import Candle, Signal


SOURCE_DELTA = timedelta(minutes=30)
FeatureVector = tuple[float, ...]
CandidateFactory = Callable[[], object]


@dataclass(frozen=True, slots=True)
class OnlineMetaParameters:
    setup: str
    outcome_horizon: int
    warmup_bars: int = 256
    maximum_holding_bars: int = 16
    take_profit_fraction: float = 0.018
    stop_loss_fraction: float = 0.010
    minimum_forward_return: float = 0.008
    target_calibration_precision: float = 0.68
    learning_rate: float = 0.035
    l2_penalty: float = 0.0005

    def __post_init__(self) -> None:
        if self.setup not in {"pullback", "momentum", "hybrid"}:
            raise ValueError("setup must be pullback, momentum, or hybrid")
        integers = (
            self.outcome_horizon,
            self.warmup_bars,
            self.maximum_holding_bars,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in integers
        ):
            raise ValueError("bar counts must be positive integers")
        fractions = (
            self.take_profit_fraction,
            self.stop_loss_fraction,
            self.minimum_forward_return,
            self.learning_rate,
            self.l2_penalty,
        )
        if not all(isfinite(value) and value > 0 for value in fractions):
            raise ValueError("fractions and learning controls must be positive")
        if not (
            isfinite(self.target_calibration_precision)
            and 0.5 < self.target_calibration_precision < 1.0
        ):
            raise ValueError("target precision must be between 0.5 and one")


class CausalOnlineMetaStrategy:
    """Online logistic meta-filter around a causal rule-based setup.

    The model is reset after a source-data gap.  A prediction made at index
    ``s`` is cached, and its label is not revealed until index
    ``s + outcome_horizon``.  The adaptive probability cutoff is calibrated
    from those matured, pre-update predictions only.
    """

    def __init__(self, parameters: OnlineMetaParameters, *, name: str) -> None:
        if not name:
            raise ValueError("strategy name cannot be empty")
        self.parameters = parameters
        self.name = name

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        if not candles:
            return []

        signals = [Signal.FLAT] * len(candles)
        segment_start = 0
        weights = [0.0] * 8
        training_count = 0
        cached: dict[int, tuple[FeatureVector, float, bool]] = {}
        calibration: list[tuple[float, int]] = []
        position = Signal.FLAT
        entry_pending = False
        entry_price: float | None = None
        held_bars = 0

        for index, candle in enumerate(candles):
            if index and candle.timestamp - candles[index - 1].timestamp != SOURCE_DELTA:
                segment_start = index
                weights = [0.0] * 8
                training_count = 0
                cached.clear()
                calibration.clear()
                position = Signal.FLAT
                entry_pending = False
                entry_price = None
                held_bars = 0

            matured_index = index - self.parameters.outcome_horizon
            matured = cached.pop(matured_index, None)
            if matured is not None and matured_index + 1 < len(candles):
                features, old_probability, was_setup = matured
                entry = candles[matured_index + 1].open
                forward_return = candle.close / entry - 1.0
                label = int(forward_return >= self.parameters.minimum_forward_return)
                if was_setup:
                    calibration.append((old_probability, label))
                    if len(calibration) > 512:
                        del calibration[:-512]
                _online_logistic_update(
                    weights,
                    features,
                    label,
                    learning_rate=self.parameters.learning_rate,
                    l2_penalty=self.parameters.l2_penalty,
                )
                training_count += 1

            if position is Signal.LONG:
                if entry_pending:
                    entry_price = candle.open
                    entry_pending = False
                held_bars += 1
                assert entry_price is not None
                if (
                    candle.close >= entry_price * (1.0 + self.parameters.take_profit_fraction)
                    or candle.close <= entry_price * (1.0 - self.parameters.stop_loss_fraction)
                    or held_bars >= self.parameters.maximum_holding_bars
                ):
                    position = Signal.FLAT
                    entry_price = None
                    held_bars = 0

            features = _features(candles, index, segment_start)
            if features is None:
                signals[index] = position
                continue
            setup = _is_setup(self.parameters.setup, features)
            probability = _probability(weights, features)
            cached[index] = (features, probability, setup)
            cutoff = _causal_cutoff(calibration, self.parameters)

            if (
                position is Signal.FLAT
                and training_count >= self.parameters.warmup_bars
                and setup
                and cutoff is not None
                and probability >= cutoff
            ):
                position = Signal.LONG
                entry_pending = True
                held_bars = 0
            signals[index] = position

        return signals


def _features(
    candles: Sequence[Candle], index: int, segment_start: int
) -> FeatureVector | None:
    if index - segment_start < 96:
        return None
    closes = [candles[item].close for item in range(index - 96, index + 1)]
    one_bar_returns = [
        log(closes[item] / closes[item - 1]) for item in range(1, len(closes))
    ]
    recent = one_bar_returns[-48:]
    mean = sum(recent) / len(recent)
    variance = sum((value - mean) ** 2 for value in recent) / len(recent)
    deviation = max(sqrt(variance), 1e-8)
    r1 = one_bar_returns[-1]
    r4 = log(closes[-1] / closes[-5])
    trend48 = log(closes[-1] / closes[-49])
    trend96 = log(closes[-1] / closes[0])
    zscore = (r1 - mean) / deviation
    volumes = [candles[item].volume for item in range(index - 48, index)]
    average_volume = max(sum(volumes) / len(volumes), 1e-12)
    volume_log_ratio = log(max(candles[index].volume, 1e-12) / average_volume)
    range_fraction = (candles[index].high - candles[index].low) / candles[index].close
    # Fixed economic scales avoid fitting preprocessing statistics on a fold.
    return (
        1.0,
        _clip(r1 / 0.01),
        _clip(r4 / 0.025),
        _clip(trend48 / 0.08),
        _clip(trend96 / 0.12),
        _clip(zscore / 3.0),
        _clip(volume_log_ratio / 2.0),
        _clip(range_fraction / 0.04),
    )


def _is_setup(setup: str, features: FeatureVector) -> bool:
    _, r1, r4, trend48, trend96, zscore, volume, candle_range = features
    pullback = (
        trend48 > 0.04
        and trend96 > 0.03
        and r4 < -0.06
        and zscore < -0.12
        and candle_range < 0.85
    )
    momentum = (
        trend48 > 0.08
        and trend96 > 0.05
        and r1 > 0.04
        and r4 > 0.06
        and zscore > 0.08
        and volume > -0.35
    )
    if setup == "pullback":
        return pullback
    if setup == "momentum":
        return momentum
    return pullback or momentum


def _probability(weights: list[float], features: FeatureVector) -> float:
    score = max(-30.0, min(30.0, sum(w * x for w, x in zip(weights, features))))
    return 1.0 / (1.0 + exp(-score))


def _online_logistic_update(
    weights: list[float],
    features: FeatureVector,
    label: int,
    *,
    learning_rate: float,
    l2_penalty: float,
) -> None:
    error = float(label) - _probability(weights, features)
    for index, feature in enumerate(features):
        penalty = 0.0 if index == 0 else l2_penalty * weights[index]
        weights[index] += learning_rate * (error * feature - penalty)


def _causal_cutoff(
    calibration: Sequence[tuple[float, int]], parameters: OnlineMetaParameters
) -> float | None:
    # Requiring 24 historical opportunities per cutoff prevents a handful of
    # lucky labels from relaxing the filter. Final acceptance still requires
    # at least 30 independent OOS closed trades in the outer validator.
    for cutoff in (0.60, 0.65, 0.70, 0.75, 0.80):
        selected = [label for probability, label in calibration if probability >= cutoff]
        if (
            len(selected) >= 24
            and sum(selected) / len(selected) >= parameters.target_calibration_precision
        ):
            return cutoff
    return None


def _clip(value: float) -> float:
    return max(-3.0, min(3.0, value))


def _validate_candles(candles: Sequence[Candle]) -> None:
    if not candles:
        return
    market = candles[0].market
    if market != "KRW-BTC" or any(candle.market != market for candle in candles):
        raise ValueError("meta candidates require exactly one KRW-BTC market")
    if any(
        candles[index].timestamp <= candles[index - 1].timestamp
        for index in range(1, len(candles))
    ):
        raise ValueError("candles must be chronological")


def candidate_factories() -> dict[str, CandidateFactory]:
    return {
        "meta_online_pullback_h16": lambda: CausalOnlineMetaStrategy(
            OnlineMetaParameters(setup="pullback", outcome_horizon=16),
            name="meta_online_pullback_h16",
        ),
        "meta_online_momentum_h12": lambda: CausalOnlineMetaStrategy(
            OnlineMetaParameters(
                setup="momentum",
                outcome_horizon=12,
                maximum_holding_bars=12,
                take_profit_fraction=0.016,
            ),
            name="meta_online_momentum_h12",
        ),
        "meta_online_hybrid_h16": lambda: CausalOnlineMetaStrategy(
            OnlineMetaParameters(
                setup="hybrid",
                outcome_horizon=16,
                target_calibration_precision=0.70,
            ),
            name="meta_online_hybrid_h16",
        ),
    }
