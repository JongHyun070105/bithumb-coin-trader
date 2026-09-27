from __future__ import annotations

from datetime import UTC, datetime, timedelta
import math

import pytest

from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.research_infra.builtin_strategies import (
    UnsupportedStrategyError,
    create_builtin_strategy,
    registered_strategy_ids,
)
from bithumb_coin_trader.research_infra.costs import SpotCostScenario
from bithumb_coin_trader.research_infra.result_schema import ResultStatus
from bithumb_coin_trader.research_infra.walk_forward_runner import run_walk_forward


DAILY_CANDIDATES = (
    "daily_weekly_absolute_momentum_126_63",
    "daily_weekly_sma_50_200",
    "daily_weekly_donchian_90_30",
    "daily_weekly_dual_momentum_42_168_vol80",
)


def _daily_candles(count: int = 730) -> list[Candle]:
    start = datetime(2023, 1, 1, 15, tzinfo=UTC)  # KST midnight boundary
    candles: list[Candle] = []
    previous_close = 1_000.0
    for index in range(count):
        close = 1_000.0 * (1.0025**index) * (1.0 + 0.06 * math.sin(index / 23.0))
        candles.append(Candle(
            timestamp=start + timedelta(days=index),
            open=previous_close,
            high=max(previous_close, close) * 1.001,
            low=min(previous_close, close) * 0.999,
            close=close,
            volume=1_000.0,
            market="KRW-BTC",
        ))
        previous_close = close
    return candles


@pytest.mark.parametrize("strategy_id", DAILY_CANDIDATES)
def test_existing_frozen_daily_candidate_runs_through_governed_walk_forward(strategy_id: str) -> None:
    assert strategy_id in registered_strategy_ids()
    strategy = create_builtin_strategy(strategy_id, 71, {})
    scenario = SpotCostScenario(
        name="explicit_costed",
        maker_fee_bps=4.0,
        taker_fee_bps=12.0,
        slippage_bps=7.0,
        latency_ms=0.0,
        minimum_order_notional=1_000.0,
        tick_size=1.0,
        lot_size=0.000001,
        partial_fill_probability=0.0,
    )

    report = run_walk_forward(
        _daily_candles(),
        lambda _seed: strategy,
        (scenario,),
        dataset_id="synthetic-daily",
        dataset_sha256="a" * 64,
        code_revision="b" * 40,
        candidate_family="daily_weekly_trend_and_momentum",
        strategy_id=strategy_id,
        strategy_config={"existing_parameters_frozen": True},
        feature_config={"input": "completed_KST_daily_candles"},
        n_folds=2,
        window_mode="EXPANDING",
        purge_s=86_400.0,
        embargo_s=86_400.0,
        seed=71,
    )

    assert len(report.folds) == 2
    assert all(fold.result.status is ResultStatus.COMPLETED for fold in report.folds)
    assert all(fold.result.fees is not None and fold.result.fees > 0.0 for fold in report.folds)
    assert all(fold.frozen_parameter_sha256 for fold in report.folds)


def test_existing_daily_candidate_parameters_cannot_be_overridden() -> None:
    with pytest.raises(UnsupportedStrategyError, match="accepts no overrides"):
        create_builtin_strategy(
            "daily_weekly_absolute_momentum_126_63",
            1,
            {"entry_lookback_days": 10},
        )
