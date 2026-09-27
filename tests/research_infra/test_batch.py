from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.research_infra.batch import (
    BatchExperiment,
    run_research_batch,
)
from bithumb_coin_trader.research_infra.costs import SpotCostScenario


DATASET_SHA256 = "a" * 64
CODE_REVISION = "b" * 40


def _candles() -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    values = [100.0, 100.0, 100.0, 100.0, 100.0, 102.0, 103.0, 104.0, 101.0, 99.0, 100.0, 102.0]
    return [
        Candle(
            timestamp=start + timedelta(hours=index),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=100.0,
        )
        for index, price in enumerate(values)
    ]


def _scenario() -> SpotCostScenario:
    return SpotCostScenario(
        name="base",
        maker_fee_bps=2.0,
        taker_fee_bps=10.0,
        slippage_bps=1.0,
        latency_ms=0.0,
        minimum_order_notional=1.0,
        tick_size=1.0,
        lot_size=1.0,
        partial_fill_probability=0.0,
    )


class _FlatModel:
    def fit(self, training_candles: Sequence[Candle]) -> "_FlatModel":
        assert training_candles
        return self

    def parameters(self) -> Mapping[str, Any]:
        return {"weight": 0.0}

    def target_weight(self, point_in_time_history: Sequence[Candle]) -> float:
        assert point_in_time_history
        return 0.0


def _experiment(factory: Any = None) -> BatchExperiment:
    return BatchExperiment(
        candidate_family="test-family",
        strategy_id="cash-test",
        strategy_factory=factory or (lambda seed, parameters: _FlatModel()),
        strategy_config={"allocation": "all"},
        feature_config={"input": "completed-candles"},
        parameter_sets=({},),
        seed=19,
    )


def _run(tmp_path: Path, experiment: BatchExperiment) -> dict[str, Any]:
    return run_research_batch(
        candles=_candles(),
        dataset_id="synthetic-retrospective",
        dataset_sha256=DATASET_SHA256,
        code_revision=CODE_REVISION,
        experiments=(experiment,),
        cost_scenarios=(_scenario(),),
        output_dir=tmp_path,
        n_folds=2,
        window_mode="EXPANDING",
        purge_s=3_600.0,
        embargo_s=3_600.0,
    )


def test_batch_resumes_interrupted_attempt_without_repeating_completed_fold(tmp_path: Path) -> None:
    calls = 0

    def factory(seed: int, parameters: Mapping[str, Any]) -> _FlatModel:
        nonlocal calls
        del seed, parameters
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt("simulated interruption between folds")
        return _FlatModel()

    experiment = _experiment(factory)
    with pytest.raises(KeyboardInterrupt, match="simulated interruption"):
        _run(tmp_path, experiment)

    report = _run(tmp_path, experiment)
    assert report["status"] == "COMPLETE"
    assert calls == 3
    run_root = tmp_path / "batches" / report["batch_id"] / "runs"
    attempts = list(run_root.glob("*/attempt-*"))
    assert len(attempts) == 1
    metrics = json.loads((attempts[0] / "metrics.json").read_text())
    assert len(metrics["folds"]) == 2
    assert len((attempts[0] / "events.jsonl").read_text().splitlines()) == 3


def test_batch_skips_identical_completed_experiment_and_refuses_changed_inputs(tmp_path: Path) -> None:
    calls = 0

    def factory(seed: int, parameters: Mapping[str, Any]) -> _FlatModel:
        nonlocal calls
        del seed, parameters
        calls += 1
        return _FlatModel()

    experiment = _experiment(factory)
    first = _run(tmp_path, experiment)
    second = _run(tmp_path, experiment)
    assert second == first
    assert calls == 2

    changed = BatchExperiment(
        candidate_family="test-family",
        strategy_id="cash-test",
        strategy_factory=factory,
        strategy_config={"allocation": "half"},
        feature_config={"input": "completed-candles"},
        parameter_sets=({},),
        seed=19,
    )
    different = _run(tmp_path, changed)
    assert different["batch_id"] != first["batch_id"]


def test_failed_attempt_requires_explicit_retry_and_preserves_both_attempts(tmp_path: Path) -> None:
    calls = 0

    def factory(seed: int, parameters: Mapping[str, Any]) -> _FlatModel:
        nonlocal calls
        del seed, parameters
        calls += 1
        if calls == 1:
            raise RuntimeError("deliberate strategy failure")
        return _FlatModel()

    experiment = _experiment(factory)
    failed = _run(tmp_path, experiment)
    assert failed["status"] == "PARTIAL"
    assert failed["runs"][0]["status"] == "FAILED"
    assert _run(tmp_path, experiment)["status"] == "NEEDS_RETRY"

    retried = run_research_batch(
        candles=_candles(),
        dataset_id="synthetic-retrospective",
        dataset_sha256=DATASET_SHA256,
        code_revision=CODE_REVISION,
        experiments=(experiment,),
        cost_scenarios=(_scenario(),),
        output_dir=tmp_path,
        n_folds=2,
        window_mode="EXPANDING",
        purge_s=3_600.0,
        embargo_s=3_600.0,
        retry_failed=True,
    )
    assert retried["status"] == "COMPLETE"
    assert retried["runs"][0]["attempt"] == 2
    attempts = list((tmp_path / "batches" / failed["batch_id"] / "runs").glob("*/attempt-*"))
    assert len(attempts) == 2


def test_batch_bounds_grid_before_creating_evidence(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="experiment grid has"):
        run_research_batch(
            candles=_candles(),
            dataset_id="synthetic-retrospective",
            dataset_sha256=DATASET_SHA256,
            code_revision=CODE_REVISION,
            experiments=(_experiment(),),
            cost_scenarios=(_scenario(),),
            output_dir=tmp_path,
            n_folds=2,
            window_mode="EXPANDING",
            purge_s=3_600.0,
            embargo_s=3_600.0,
            max_fold_cost_runs=1,
        )
    assert not (tmp_path / "batches").exists()
