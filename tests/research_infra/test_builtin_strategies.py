from __future__ import annotations

from datetime import UTC, datetime, timedelta
import math

import pytest

from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.research_infra.builtin_strategies import (
    UnsupportedStrategyError,
    candidate_family_for_strategy,
    create_builtin_strategy,
    governed_candidate_strategy_ids,
    registered_strategy_ids,
    strategy_source_modules,
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
ADDITIONAL_SINGLE_MARKET_CANDIDATES = (
    "v3_e9_donchian_90d_vol25",
    "v3_absolute_momentum_126_63_entry_vol20",
    "v3_frozen_majority_2_of_3",
    "v4_adaptive_donchian_atr",
    "v4_trend_volatility_regime",
    "v4_kama_trend",
    "v4_triple_momentum_filter",
    "v4_adx_kama_confluence",
    "v4_volatility_adjusted_momentum",
    "v4_52week_high_breakout",
    "v4_trend_quality_filter",
    "v5_regime_adaptive_donchian",
    "v5_trend_pullback_fixed30",
    "v5_trend_pullback_voltarget25",
    "v5_trend_pullback_kelly025",
    "v6_fast_donchian_swing",
    "v6_daily_ema_pullback",
    "core70_satellite30_v6_fast_donchian",
    "core70_satellite30_v6_daily_ema_pullback",
)
REPRESENTATIVE_FAMILY_RUNS = (
    "v3_frozen_majority_2_of_3",
    "v4_trend_quality_filter",
    "v5_regime_adaptive_donchian",
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


@pytest.mark.parametrize("strategy_id", ADDITIONAL_SINGLE_MARKET_CANDIDATES)
def test_additional_single_market_candidate_fits_and_emits_causal_weight(strategy_id: str) -> None:
    assert strategy_id in governed_candidate_strategy_ids()
    strategy = create_builtin_strategy(strategy_id, 71, {})
    candles = _daily_candles(800)
    fitted = strategy.fit(candles[:500])
    weight = fitted.target_weight(candles)
    assert math.isfinite(weight)
    assert 0.0 <= weight <= 1.0
    assert fitted.parameters()


@pytest.mark.parametrize("strategy_id", ADDITIONAL_SINGLE_MARKET_CANDIDATES)
def test_additional_single_market_candidate_rejects_parameter_overrides(strategy_id: str) -> None:
    with pytest.raises(UnsupportedStrategyError, match="accepts no overrides"):
        create_builtin_strategy(strategy_id, 71, {"target_weight": 0.99})


@pytest.mark.parametrize("strategy_id", REPRESENTATIVE_FAMILY_RUNS)
def test_v3_v4b_v5_candidates_run_through_governed_walk_forward(strategy_id: str) -> None:
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
        candidate_family=candidate_family_for_strategy(strategy_id),
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
    assert all(fold.result.fees is not None and fold.result.fees >= 0.0 for fold in report.folds)
    assert all(fold.frozen_parameter_sha256 for fold in report.folds)


@pytest.mark.parametrize("strategy_id", ADDITIONAL_SINGLE_MARKET_CANDIDATES)
def test_candidate_family_and_source_inventory_are_explicit(strategy_id: str) -> None:
    assert candidate_family_for_strategy(strategy_id) in {
        "v3_daily_target_weight",
        "v4_v4b_regime_breakout_and_trend",
        "v5_regime_dual_momentum_pullback",
        "v6_satellite_and_core_satellite",
    }
    sources = strategy_source_modules(strategy_id)
    assert "research_infra/builtin_strategies.py" in sources
    assert "daily_strategy_candidates.py" in sources
    assert any(source.startswith("strategy_v") for source in sources)


@pytest.mark.parametrize(
    "strategy_id,satellite_type",
    (
        ("core70_satellite30_v6_fast_donchian", "fast"),
        ("core70_satellite30_v6_daily_ema_pullback", "ema"),
    ),
)
def test_core_satellite_adapter_uses_explicit_70_30_target_weights(
    strategy_id: str, satellite_type: str
) -> None:
    from bithumb_coin_trader.strategy_v4_candidates import V4AdaptiveDonchianAtrStrategy
    from bithumb_coin_trader.strategy_v6_candidates import (
        V6DailyEmaPullbackStrategy,
        V6FastDonchianSwingStrategy,
    )

    candles = _daily_candles()
    satellite = V6FastDonchianSwingStrategy() if satellite_type == "fast" else V6DailyEmaPullbackStrategy()
    core = V4AdaptiveDonchianAtrStrategy()
    fitted = create_builtin_strategy(strategy_id, 71, {}).fit(candles)
    expected = 0.70 * core.generate(candles)[-1] + 0.30 * satellite.generate(candles)[-1]
    assert fitted.target_weight(candles) == pytest.approx(expected)
