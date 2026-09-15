"""Chronological Evaluation Framework for Microstructure Research.

Implements time-aware evaluation with:
- Chronological train/test splits (no random splits)
- Walk-forward evaluation
- Expanding/rolling windows
- Purging/embargo at boundaries to prevent label leakage
- No future information in normalization/scaling

Random train/test split is PROHIBITED for final conclusions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence
import math

from .features import FeatureVector
from .labels import LabelVector


@dataclass(frozen=True, slots=True)
class ChronologicalFold:
    """A single fold in chronological evaluation."""

    fold_id: int
    train_start_ns: int
    train_end_ns: int
    test_start_ns: int
    test_end_ns: int
    embargo_ns: int  # Purge period between train and test

    @property
    def train_duration_s(self) -> float:
        return (self.train_end_ns - self.train_start_ns) / 1e9

    @property
    def test_duration_s(self) -> float:
        return (self.test_end_ns - self.test_start_ns) / 1e9


@dataclass(frozen=True, slots=True)
class FoldResult:
    """Result of evaluating a hypothesis on a single fold."""

    fold_id: int
    hypothesis_id: str
    dataset_id: str

    # Sample sizes
    train_samples: int
    test_samples: int
    excluded_samples: int

    # Training metrics
    train_ic: float | None = None  # Information coefficient
    train_hit_rate: float | None = None
    train_mean_return: float | None = None

    # Test metrics (out-of-sample)
    test_ic: float | None = None
    test_hit_rate: float | None = None
    test_mean_return: float | None = None
    test_mean_return_cost_adjusted: float | None = None

    # Statistical robustness
    test_ic_se: float | None = None  # Standard error
    test_ic_tstat: float | None = None

    # Execution metrics
    test_trade_count: int = 0
    test_total_cost: float = 0.0
    test_gross_pnl: float = 0.0
    test_net_pnl: float = 0.0
    test_sharpe: float | None = None

    # DQ
    dq_exclusions: int = 0
    missing_labels: int = 0


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Aggregated evaluation report across all folds."""

    hypothesis_id: str
    dataset_id: str
    dataset_role: str
    n_folds: int
    total_test_samples: int
    total_train_samples: int
    total_excluded: int
    total_missing_labels: int

    # Cross-fold metrics
    mean_test_ic: float | None = None
    std_test_ic: float | None = None
    mean_test_hit_rate: float | None = None
    mean_test_sharpe: float | None = None
    mean_test_net_pnl: float | None = None
    weighted_test_ic: float | None = None  # Weighted by fold sample size

    # Robustness
    folds_with_positive_ic: int = 0
    folds_with_positive_net_pnl: int = 0
    worst_fold_ic: float | None = None
    best_fold_ic: float | None = None

    # Time consistency
    first_half_ic: float | None = None
    second_half_ic: float | None = None

    # Classification
    scientific_classification: str = "UNTESTED"


def create_chronological_folds(
    timestamps_ns: Sequence[int],
    n_folds: int = 5,
    embargo_s: float = 5.0,
    expanding: bool = True,
) -> list[ChronologicalFold]:
    """Create chronological train/test folds from sorted timestamps.

    Args:
        timestamps_ns: Sorted sequence of event timestamps
        n_folds: Number of folds
        embargo_s: Embargo period in seconds between train and test
        expanding: If True, use expanding window (train grows). If False, rolling.

    Returns:
        List of ChronologicalFold with proper embargo
    """
    if len(timestamps_ns) < n_folds * 2:
        return []

    total_start = timestamps_ns[0]
    total_end = timestamps_ns[-1]
    embargo_ns = int(embargo_s * 1_000_000_000)

    # Divide into n_folds + 1 segments
    segment_size = (total_end - total_start) // (n_folds + 1)

    folds = []
    for i in range(n_folds):
        if expanding:
            train_start = total_start
        else:
            train_start = total_start + (i) * segment_size

        train_end = total_start + (i + 1) * segment_size
        test_start = train_end + embargo_ns
        test_end = total_start + (i + 2) * segment_size

        # Ensure test doesn't exceed data range
        if test_end > total_end:
            test_end = total_end

        folds.append(ChronologicalFold(
            fold_id=i,
            train_start_ns=train_start,
            train_end_ns=train_end,
            test_start_ns=test_start,
            test_end_ns=test_end,
            embargo_ns=embargo_ns,
        ))

    return folds


def compute_information_coefficient(
    features: Sequence[float | None],
    targets: Sequence[float | None],
) -> tuple[float | None, int]:
    """Compute Pearson IC (information coefficient) between features and targets.

    Returns (IC, sample_size). None if insufficient data.
    """
    # Filter to pairs where both are non-None
    pairs = [
        (f, t) for f, t in zip(features, targets)
        if f is not None and t is not None
        and math.isfinite(f) and math.isfinite(t)
    ]

    if len(pairs) < 10:
        return None, len(pairs)

    n = len(pairs)
    mean_f = sum(f for f, _ in pairs) / n
    mean_t = sum(t for _, t in pairs) / n

    cov = sum((f - mean_f) * (t - mean_t) for f, t in pairs) / n
    var_f = sum((f - mean_f) ** 2 for f, _ in pairs) / n
    var_t = sum((t - mean_t) ** 2 for _, t in pairs) / n

    if var_f <= 0 or var_t <= 0:
        return 0.0, n

    ic = cov / math.sqrt(var_f * var_t)
    return ic, n


def compute_spearman_rank_ic(
    features: Sequence[float | None],
    targets: Sequence[float | None],
) -> tuple[float | None, int]:
    """Compute Spearman rank IC between features and targets."""
    pairs = [
        (f, t) for f, t in zip(features, targets)
        if f is not None and t is not None
        and math.isfinite(f) and math.isfinite(t)
    ]

    if len(pairs) < 10:
        return None, len(pairs)

    def rankdata(xs: list[float]) -> list[float]:
        n = len(xs)
        indexed = sorted(enumerate(xs), key=lambda x: x[1])
        ranks = [0.0] * n
        for rank, (idx, _) in enumerate(indexed):
            ranks[idx] = rank + 1.0
        return ranks

    feat_ranks = rankdata([f for f, _ in pairs])
    target_ranks = rankdata([t for _, t in pairs])

    n = len(pairs)
    mean_f = sum(feat_ranks) / n
    mean_t = sum(target_ranks) / n

    cov = sum((f - mean_f) * (t - mean_t) for f, t in zip(feat_ranks, target_ranks)) / n
    var_f = sum((f - mean_f) ** 2 for f in feat_ranks) / n
    var_t = sum((t - mean_t) ** 2 for t in target_ranks) / n

    if var_f <= 0 or var_t <= 0:
        return 0.0, n

    return cov / math.sqrt(var_f * var_t), n


def compute_hit_rate(
    features: Sequence[float | None],
    targets: Sequence[float | None],
    expected_sign: str = "positive",
) -> tuple[float | None, int]:
    """Compute directional hit rate: how often feature direction matches target."""
    pairs = [
        (f, t) for f, t in zip(features, targets)
        if f is not None and t is not None
        and math.isfinite(f) and math.isfinite(t)
    ]

    if len(pairs) < 10:
        return None, len(pairs)

    correct = 0
    for f, t in pairs:
        if expected_sign == "positive":
            if (f > 0 and t > 0) or (f < 0 and t < 0):
                correct += 1
        else:
            if (f > 0 and t < 0) or (f < 0 and t > 0):
                correct += 1

    return correct / len(pairs), len(pairs)


def compute_quantile_returns(
    features: Sequence[float | None],
    targets: Sequence[float | None],
    n_quantiles: int = 5,
) -> list[dict[str, Any]]:
    """Compute conditional returns by feature quantile.

    Returns list of dicts with quantile stats.
    """
    pairs = [
        (f, t) for f, t in zip(features, targets)
        if f is not None and t is not None
        and math.isfinite(f) and math.isfinite(t)
    ]

    if len(pairs) < n_quantiles * 5:
        return []

    # Sort by feature
    sorted_pairs = sorted(pairs, key=lambda x: x[0])
    chunk_size = len(sorted_pairs) // n_quantiles

    results = []
    for q in range(n_quantiles):
        start = q * chunk_size
        end = start + chunk_size if q < n_quantiles - 1 else len(sorted_pairs)
        chunk = sorted_pairs[start:end]

        targets_in_chunk = [t for _, t in chunk]
        mean_t = sum(targets_in_chunk) / len(targets_in_chunk) if targets_in_chunk else 0

        results.append({
            "quantile": q + 1,
            "count": len(chunk),
            "feature_mean": sum(f for f, _ in chunk) / len(chunk),
            "target_mean": mean_t,
            "target_std": math.sqrt(
                sum((t - mean_t) ** 2 for t in targets_in_chunk) / len(targets_in_chunk)
            ) if targets_in_chunk else 0,
        })

    return results


def compute_rolling_sharpe(
    returns: Sequence[float],
    window: int = 100,
) -> list[float]:
    """Compute rolling Sharpe ratio (annualized, assuming returns are per-trade)."""
    sharpes = []
    for i in range(window, len(returns)):
        window_returns = returns[i - window:i]
        mean_r = sum(window_returns) / len(window_returns)
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in window_returns) / len(window_returns))
        if std_r > 0:
            sharpe = mean_r / std_r * math.sqrt(252 * 24)  # Annualize assuming hourly
        else:
            sharpe = 0.0
        sharpes.append(sharpe)
    return sharpes
