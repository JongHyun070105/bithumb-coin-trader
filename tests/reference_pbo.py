"""Independent Reference Implementation of CSCV Probability of Backtest Overfitting (PBO).

Bailey, Borwein, López de Prado, Zhu (2015),
"The Probability of Backtest Overfitting", Journal of Computational Finance.

Transparent, educational reference implementation for oracle testing.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from statistics import mean
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class ReferencePboResult:
    pbo: float
    split_count: int
    ranks: tuple[float, ...]
    median_rank: float


def reference_cscv_pbo(
    returns_matrix: Mapping[str, Sequence[float]],
    *,
    blocks: int = 8,
) -> ReferencePboResult:
    """Calculates PBO via Combinatorially Symmetric Cross-Validation (CSCV)."""
    if blocks < 4 or blocks % 2 != 0:
        raise ValueError("blocks must be an even integer >= 4")
    if len(returns_matrix) < 2:
        raise ValueError("Must have at least two candidate strategies")

    names = sorted(returns_matrix)
    t_len = len(returns_matrix[names[0]])
    for name in names:
        if len(returns_matrix[name]) != t_len:
            raise ValueError("All candidate series must have identical length")
    if t_len < blocks:
        raise ValueError("Total observations must be >= number of blocks")

    # Construct block partitions
    boundaries = [round(i * t_len / blocks) for i in range(blocks + 1)]
    block_slices = [
        slice(boundaries[i], boundaries[i + 1])
        for i in range(blocks)
    ]

    below_median_count = 0
    rank_fractions: list[float] = []
    num_splits = 0

    # All combinations of S/2 blocks chosen from S blocks
    for train_blocks in combinations(range(blocks), blocks // 2):
        train_set = set(train_blocks)
        test_blocks = [b for b in range(blocks) if b not in train_set]

        # Calculate In-Sample performance for each candidate
        is_scores: dict[str, float] = {}
        for name in names:
            series = returns_matrix[name]
            is_vals = [r for b in train_blocks for r in series[block_slices[b]]]
            is_scores[name] = mean(is_vals) if is_vals else 0.0

        # Deterministic winner selection: highest IS score (tie-break alphabetically)
        is_winner = max(names, key=lambda n: (is_scores[n], n))

        # Calculate Out-of-Sample performance for each candidate
        oos_scores: dict[str, float] = {}
        for name in names:
            series = returns_matrix[name]
            oos_vals = [r for b in test_blocks for r in series[block_slices[b]]]
            oos_scores[name] = mean(oos_vals) if oos_vals else 0.0

        # Sort all candidates by OOS score (ascending)
        sorted_oos = sorted(names, key=lambda n: (oos_scores[n], n))
        rank = sorted_oos.index(is_winner)  # 0-indexed: 0 = worst, len(names)-1 = best
        rank_fraction = (rank + 1) / len(names)  # 1-indexed fraction in (0, 1]

        rank_fractions.append(rank_fraction)
        if rank_fraction <= 0.5:
            below_median_count += 1
        num_splits += 1

    ordered_ranks = sorted(rank_fractions)
    med_rank = ordered_ranks[len(ordered_ranks) // 2]
    pbo_val = below_median_count / num_splits

    return ReferencePboResult(
        pbo=pbo_val,
        split_count=num_splits,
        ranks=tuple(rank_fractions),
        median_rank=med_rank,
    )
