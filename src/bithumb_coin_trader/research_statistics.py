"""Deterministic multiple-testing diagnostics for chronological research."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations
from math import e, isfinite, log, sqrt
import random
from statistics import NormalDist, mean, pstdev
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class RealityCheckResult:
    observed_best_mean: float
    p_value: float
    iterations: int
    mean_block_length: int


@dataclass(frozen=True, slots=True)
class DeflatedSharpeResult:
    observed_sharpe: float
    expected_maximum_sharpe: float
    probability: float
    trial_count: int


@dataclass(frozen=True, slots=True)
class PboResult:
    probability_backtest_overfitting: float
    split_count: int
    median_oos_rank_fraction: float


def stationary_bootstrap_indices(
    length: int,
    *,
    mean_block_length: int,
    iterations: int,
    seed: str,
) -> tuple[tuple[int, ...], ...]:
    if length < 2 or mean_block_length < 1 or iterations < 1 or not seed:
        raise ValueError("stationary bootstrap parameters are invalid")
    restart_probability = 1.0 / mean_block_length
    rng = random.Random(seed)
    samples: list[tuple[int, ...]] = []
    for _ in range(iterations):
        indices: list[int] = []
        current = rng.randrange(length)
        for index in range(length):
            if index == 0 or rng.random() < restart_probability:
                current = rng.randrange(length)
            else:
                current = (current + 1) % length
            indices.append(current)
        samples.append(tuple(indices))
    return tuple(samples)


def white_reality_check(
    excess_returns: Mapping[str, Sequence[float]],
    *,
    mean_block_length: int = 7,
    iterations: int = 2_000,
    seed: str = "strategy-v2-reality-check",
) -> RealityCheckResult:
    rows = _validated_matrix(excess_returns)
    observed = max(mean(values) for values in rows.values())
    centered = {
        name: tuple(value - mean(values) for value in values)
        for name, values in rows.items()
    }
    samples = stationary_bootstrap_indices(
        len(next(iter(rows.values()))),
        mean_block_length=mean_block_length,
        iterations=iterations,
        seed=seed,
    )
    exceedances = 0
    for indices in samples:
        maximum = max(
            mean(tuple(values[index] for index in indices))
            for values in centered.values()
        )
        if maximum >= observed:
            exceedances += 1
    return RealityCheckResult(
        observed_best_mean=observed,
        p_value=(exceedances + 1) / (iterations + 1),
        iterations=iterations,
        mean_block_length=mean_block_length,
    )


def deflated_sharpe_ratio(
    returns: Sequence[float],
    *,
    trial_sharpes: Sequence[float],
    trial_count: int,
) -> DeflatedSharpeResult:
    values = _validated_values(returns)
    if trial_count < 1 or len(trial_sharpes) < 1 or any(
        not isfinite(value) for value in trial_sharpes
    ):
        raise ValueError("trial Sharpe evidence is invalid")
    volatility = pstdev(values)
    observed = mean(values) / volatility if volatility > 0 else 0.0
    sharpe_dispersion = pstdev(trial_sharpes)
    if sharpe_dispersion == 0:
        sharpe_dispersion = 1 / sqrt(len(values) - 1)
    normal = NormalDist()
    if trial_count == 1:
        benchmark = 0.0
    else:
        euler_gamma = 0.5772156649015329
        benchmark = sharpe_dispersion * (
            (1 - euler_gamma) * normal.inv_cdf(1 - 1 / trial_count)
            + euler_gamma * normal.inv_cdf(1 - 1 / (trial_count * e))
        )
    standardized = (
        tuple((value - mean(values)) / volatility for value in values)
        if volatility > 0
        else tuple(0.0 for _ in values)
    )
    skewness = mean(tuple(value**3 for value in standardized))
    kurtosis = mean(tuple(value**4 for value in standardized))
    variance_term = 1 - skewness * observed + (kurtosis - 1) * observed**2 / 4
    denominator = sqrt(max(variance_term, 1e-12))
    probability = normal.cdf(
        (observed - benchmark) * sqrt(len(values) - 1) / denominator
    )
    return DeflatedSharpeResult(observed, benchmark, probability, trial_count)


def cscv_probability_backtest_overfitting(
    returns_by_candidate: Mapping[str, Sequence[float]],
    *,
    blocks: int = 8,
) -> PboResult:
    rows = _validated_matrix(returns_by_candidate)
    if blocks < 4 or blocks % 2 or len(rows) < 2:
        raise ValueError("CSCV requires an even block count and at least two candidates")
    length = len(next(iter(rows.values())))
    if length < blocks:
        raise ValueError("CSCV block count exceeds the observation count")
    boundaries = [round(index * length / blocks) for index in range(blocks + 1)]
    block_indices = [
        tuple(range(boundaries[index], boundaries[index + 1]))
        for index in range(blocks)
    ]
    below_median = 0
    ranks: list[float] = []
    split_count = 0
    names = sorted(rows)
    for selected in combinations(range(blocks), blocks // 2):
        selected_set = set(selected)
        train = tuple(
            item for block in selected for item in block_indices[block]
        )
        test = tuple(
            item
            for block in range(blocks)
            if block not in selected_set
            for item in block_indices[block]
        )
        winner = max(names, key=lambda name: (mean(tuple(rows[name][i] for i in train)), name))
        oos_scores = sorted(
            (mean(tuple(rows[name][i] for i in test)), name) for name in names
        )
        rank = next(index for index, (_, name) in enumerate(oos_scores) if name == winner)
        rank_fraction = (rank + 1) / len(names)
        ranks.append(rank_fraction)
        below_median += rank_fraction <= 0.5
        split_count += 1
    ordered = sorted(ranks)
    median_rank = ordered[len(ordered) // 2]
    return PboResult(below_median / split_count, split_count, median_rank)


def as_serializable(value: object) -> dict[str, object]:
    if not hasattr(value, "__dataclass_fields__"):
        raise TypeError("statistics result must be a dataclass")
    return asdict(value)  # type: ignore[arg-type]


def _validated_matrix(
    values: Mapping[str, Sequence[float]],
) -> dict[str, tuple[float, ...]]:
    if not values or any(not name for name in values):
        raise ValueError("return matrix is empty")
    rows = {name: _validated_values(row) for name, row in values.items()}
    lengths = {len(row) for row in rows.values()}
    if len(lengths) != 1:
        raise ValueError("return matrix rows must have equal length")
    return rows


def _validated_values(values: Sequence[float]) -> tuple[float, ...]:
    row = tuple(float(value) for value in values)
    if len(row) < 2 or any(not isfinite(value) for value in row):
        raise ValueError("returns must contain finite observations")
    return row
