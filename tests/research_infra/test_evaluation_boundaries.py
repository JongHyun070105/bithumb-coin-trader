"""Adversarial checks for chronological walk-forward fold boundaries."""

from __future__ import annotations

import math

import pytest

from bithumb_coin_trader.research_infra.evaluation import create_chronological_folds


def test_fold_separates_train_and_test_by_purge_plus_embargo() -> None:
    timestamps = list(range(0, 101_000_000_000, 1_000_000_000))

    folds = create_chronological_folds(
        timestamps,
        n_folds=3,
        embargo_s=3.0,
        purge_s=7.0,
    )

    assert folds
    for fold in folds:
        assert fold.purge_ns == 7_000_000_000
        assert fold.embargo_ns == 3_000_000_000
        assert fold.test_start_ns - fold.train_end_ns == 10_000_000_000
        # A forward label ending within the configured 7s horizon cannot
        # overlap the test boundary; the additional 3s embargo remains clear.
        worst_case_train_label_end = fold.train_end_ns + fold.purge_ns
        assert worst_case_train_label_end < fold.test_start_ns


def test_unsorted_timestamps_fail_closed() -> None:
    with pytest.raises(ValueError, match="sorted"):
        create_chronological_folds([0, 2, 1], n_folds=1, embargo_s=0, purge_s=0)


@pytest.mark.parametrize("kwargs", [
    {"n_folds": 0},
    {"embargo_s": -1.0},
    {"embargo_s": math.inf},
    {"purge_s": -1.0},
    {"purge_s": math.nan},
])
def test_invalid_fold_parameters_fail_closed(kwargs: dict[str, float | int]) -> None:
    params: dict[str, float | int] = {"n_folds": 2, "embargo_s": 0.0, "purge_s": 0.0}
    params.update(kwargs)
    with pytest.raises(ValueError):
        create_chronological_folds(
            list(range(0, 100_000_000_000, 1_000_000_000)),
            n_folds=int(params["n_folds"]),
            embargo_s=float(params["embargo_s"]),
            purge_s=float(params["purge_s"]),
        )


def test_buffer_that_consumes_test_window_yields_no_folds() -> None:
    timestamps = list(range(0, 31_000_000_000, 1_000_000_000))

    folds = create_chronological_folds(
        timestamps,
        n_folds=2,
        embargo_s=20.0,
        purge_s=20.0,
    )

    assert folds == []
