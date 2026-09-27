from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Sequence

import pytest

from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.rebalance_backtest import RebalanceBacktester
from bithumb_coin_trader.research_infra.costs import SpotCostScenario
from bithumb_coin_trader.research_infra.result_schema import ResultStatus
from bithumb_coin_trader.research_infra.walk_forward_runner import (
    WalkForwardGovernanceError,
    run_walk_forward,
)


DATASET_SHA256 = "a" * 64
CODE_REVISION = "b" * 40


def _scenario(name: str = "base", **overrides: object) -> SpotCostScenario:
    values: dict[str, object] = {
        "name": name,
        "maker_fee_bps": 2.0,
        "taker_fee_bps": 10.0,
        "slippage_bps": 1.0,
        "latency_ms": 0.0,
        "minimum_order_notional": 1.0,
        "tick_size": 1.0,
        "lot_size": 1.0,
        "partial_fill_probability": 0.0,
        "partial_fill_status": None,
    }
    values.update(overrides)
    return SpotCostScenario(**values)  # type: ignore[arg-type]


def _candles(*, future_spike: bool = True) -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    result: list[Candle] = []
    for index in range(12):
        close = 1_000.0 if future_spike and index in {5, 6} else 100.0
        open_price = 100.0 if index == 5 else close
        result.append(Candle(
            timestamp=start + timedelta(hours=index),
            open=open_price,
            high=max(open_price, close),
            low=min(open_price, close),
            close=close,
            volume=100.0,
        ))
    return result


class LeaksIfValidationIsPassedToFit:
    def __init__(self, seen_training: list[tuple[datetime, ...]], seed: int) -> None:
        self._seen_training = seen_training
        self._seed = seed
        self._weight = 0.0

    def fit(
        self,
        training_candles: Sequence[Candle],
        validation_candles: Sequence[Candle] | None = None,
    ) -> "LeaksIfValidationIsPassedToFit":
        self._seen_training.append(tuple(candle.timestamp for candle in training_candles))
        # A broken runner passing the held-out segment would choose the
        # apparently profitable all-in rule on this synthetic future spike.
        self._weight = float(bool(validation_candles) and any(c.close >= 1_000 for c in validation_candles or ()))
        return self

    def parameters(self) -> Mapping[str, Any]:
        return {"weight": self._weight, "seed": self._seed}

    def target_weight(self, point_in_time_history: Sequence[Candle]) -> float:
        assert point_in_time_history
        return self._weight


def _settings() -> TradingSettings:
    return TradingSettings(
        initial_capital_krw=20_000,
        fee_rate=0.0,
        slippage_bps=0.0,
        allocation_fraction=1.0,
        minimum_order_krw=1,
        maximum_order_krw=20_000,
        maximum_daily_entries=2,
        cash_reserve_krw=0,
    )


def _run(
    candles: Sequence[Candle],
    factory: Any,
    *,
    window_mode: str = "EXPANDING",
    scenarios: tuple[SpotCostScenario, ...] | None = None,
):
    return run_walk_forward(
        candles,
        factory,
        scenarios or (_scenario(),),
        dataset_id="synthetic",
        dataset_sha256=DATASET_SHA256,
        code_revision=CODE_REVISION,
        candidate_family="adversarial",
        strategy_id="leak-if-exposed",
        strategy_config={"allocation": "fitted"},
        feature_config={"features": ["causal-candle-history"]},
        n_folds=2,
        window_mode=window_mode,  # type: ignore[arg-type]
        purge_s=3_600.0,
        embargo_s=3_600.0,
        seed=17,
        settings=_settings(),
    )


def test_adversarial_leak_is_prevented_and_purged_embargoed_folds_are_reported() -> None:
    candles = _candles()
    seen_training: list[tuple[datetime, ...]] = []
    report = _run(
        candles,
        lambda seed: LeaksIfValidationIsPassedToFit(seen_training, seed),
    )

    first = next(fold for fold in report.folds if fold.fold_id == 0)
    assert first.result.status is ResultStatus.COMPLETED
    assert first.result.net_return == 0.0
    assert seen_training[0][-1] < datetime.fromisoformat(first.validation_start_utc)
    assert (
        datetime.fromisoformat(first.validation_start_utc)
        - datetime.fromisoformat(first.train_end_utc)
    ) >= timedelta(hours=2)
    assert report.aggregates["base"]["fold_count"] == 2

    leaked = LeaksIfValidationIsPassedToFit([], 17).fit(
        candles[:4], candles[5:7]
    )
    assert leaked.parameters()["weight"] == 1.0
    leak_result = RebalanceBacktester(_settings()).run(
        [candles[4], *candles[5:7]],
        [1.0, 1.0, 1.0],
    )
    assert leak_result.total_return > 0.0


def test_validation_parameter_mutation_is_rejected() -> None:
    class MutatingModel:
        def __init__(self) -> None:
            self.weight = 0.0

        def fit(self, training_candles: Sequence[Candle]) -> "MutatingModel":
            del training_candles
            return self

        def parameters(self) -> Mapping[str, Any]:
            return {"weight": self.weight}

        def target_weight(self, point_in_time_history: Sequence[Candle]) -> float:
            del point_in_time_history
            self.weight += 0.1
            return self.weight

    with pytest.raises(WalkForwardGovernanceError, match="parameters changed"):
        _run(_candles(), lambda seed: MutatingModel())


def test_rolling_and_expanding_modes_produce_distinct_train_windows() -> None:
    class FlatModel:
        def fit(self, training_candles: Sequence[Candle]) -> "FlatModel":
            del training_candles
            return self

        def parameters(self) -> Mapping[str, Any]:
            return {"weight": 0.0}

        def target_weight(self, point_in_time_history: Sequence[Candle]) -> float:
            del point_in_time_history
            return 0.0

    expanding = _run(_candles(future_spike=False), lambda seed: FlatModel(), window_mode="EXPANDING")
    rolling = _run(_candles(future_spike=False), lambda seed: FlatModel(), window_mode="ROLLING")

    assert expanding.folds[0].train_samples < expanding.folds[1].train_samples
    assert rolling.folds[0].train_samples == rolling.folds[1].train_samples


def test_walk_forward_requires_complete_hash_provenance_and_costs() -> None:
    with pytest.raises(WalkForwardGovernanceError, match="dataset_sha256"):
        run_walk_forward(
            _candles(),
            lambda seed: object(),  # type: ignore[arg-type]
            (_scenario(),),
            dataset_id="synthetic",
            dataset_sha256="unknown",
            code_revision=CODE_REVISION,
            candidate_family="family",
            strategy_id="strategy",
            strategy_config={},
            feature_config={},
            n_folds=2,
            window_mode="EXPANDING",
            purge_s=1.0,
            embargo_s=1.0,
            seed=0,
        )

    with pytest.raises(WalkForwardGovernanceError, match="cost scenario"):
        run_walk_forward(
            _candles(),
            lambda seed: object(),  # type: ignore[arg-type]
            (),
            dataset_id="synthetic",
            dataset_sha256=DATASET_SHA256,
            code_revision=CODE_REVISION,
            candidate_family="family",
            strategy_id="strategy",
            strategy_config={},
            feature_config={},
            n_folds=2,
            window_mode="EXPANDING",
            purge_s=1.0,
            embargo_s=1.0,
            seed=0,
        )
