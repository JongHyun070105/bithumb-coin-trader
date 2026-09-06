"""Independent Reference Implementation of White Reality Check (WRC).

White (2000), "A Reality Check for Data Snooping", Econometrica.
Politis & Romano (1994), "The Stationary Bootstrap", JASA.

Transparent, educational implementation without external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, log
import random
from statistics import mean
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class ReferenceWrcResult:
    observed_best_mean: float
    p_value: float
    iterations: int
    bootstrap_max_distribution: tuple[float, ...]


def reference_stationary_bootstrap_sample(
    length: int,
    mean_block_length: float,
    rng: random.Random,
) -> list[int]:
    """Generates a stationary bootstrap index sequence of specified length."""
    if length <= 0:
        return []
    p = 1.0 / max(mean_block_length, 1.0)
    indices: list[int] = []
    current_idx = rng.randint(0, length - 1)
    for _ in range(length):
        if indices and rng.random() < p:
            current_idx = rng.randint(0, length - 1)
        indices.append(current_idx)
        current_idx = (current_idx + 1) % length
    return indices


def reference_white_reality_check(
    candidate_excess_returns: Mapping[str, Sequence[float]],
    *,
    iterations: int = 1000,
    mean_block_length: float = 5.0,
    seed: int = 42,
) -> ReferenceWrcResult:
    """Direct, transparent calculation of White Reality Check p-value.
    
    H0: max_k E[f_k] <= 0, where f_k is excess return of strategy k over benchmark.
    """
    if not candidate_excess_returns:
        raise ValueError("candidate_excess_returns cannot be empty")

    names = sorted(candidate_excess_returns)
    t_len = len(candidate_excess_returns[names[0]])
    for name in names:
        if len(candidate_excess_returns[name]) != t_len:
            raise ValueError("All candidate return series must have identical length")

    # 1. Observed means and observed best strategy mean
    obs_means = {name: mean(candidate_excess_returns[name]) for name in names}
    obs_best_mean = max(obs_means.values())

    # 2. Centered series for null hypothesis (mean = 0)
    centered = {
        name: [r - obs_means[name] for r in candidate_excess_returns[name]]
        for name in names
    }

    # 3. Stationary bootstrap
    rng = random.Random(seed)
    bootstrap_maxima: list[float] = []
    exceedance_count = 0

    for _ in range(iterations):
        sample_indices = reference_stationary_bootstrap_sample(t_len, mean_block_length, rng)
        # Compute mean under bootstrap sample for each candidate
        sample_max = -float("inf")
        for name in names:
            c_series = centered[name]
            s_mean = mean([c_series[idx] for idx in sample_indices])
            if s_mean > sample_max:
                sample_max = s_mean
        bootstrap_maxima.append(sample_max)
        if sample_max >= obs_best_mean:
            exceedance_count += 1

    # Standard White/Hansen formula: (count + 1) / (iterations + 1) to avoid p=0.0
    p_val = (exceedance_count + 1) / (iterations + 1)

    return ReferenceWrcResult(
        observed_best_mean=obs_best_mean,
        p_value=p_val,
        iterations=iterations,
        bootstrap_max_distribution=tuple(bootstrap_maxima),
    )
