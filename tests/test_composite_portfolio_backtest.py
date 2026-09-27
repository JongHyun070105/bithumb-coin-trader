from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.composite_portfolio_backtest import run_composite_portfolio_backtest
from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.research_infra.costs import SpotCostScenario


def _candles() -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Candle(
            timestamp=start + timedelta(days=index),
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1.0,
        )
        for index in range(3)
    ]


def test_composite_backtest_runs_with_valid_weight_streams() -> None:
    result = run_composite_portfolio_backtest(
        _candles(), [0.5, 0.5, 0.0], [0.0, 0.5, 0.0], TradingSettings()
    )

    assert result.initial_equity == TradingSettings().initial_capital_krw
    assert result.fill_count > 0


def test_core_satellite_adapter_passes_spot_cost_scenario_through() -> None:
    scenario = SpotCostScenario(
        name="core-satellite-stress",
        maker_fee_bps=2.0,
        taker_fee_bps=20.0,
        slippage_bps=10.0,
        latency_ms=0.0,
        minimum_order_notional=5_000.0,
        tick_size=1.0,
        lot_size=1.0,
        partial_fill_probability=0.0,
        partial_fill_status=None,
    )
    settings = TradingSettings(
        initial_capital_krw=20_000,
        fee_rate=0.0,
        slippage_bps=0.0,
        allocation_fraction=1.0,
        minimum_order_krw=1,
        maximum_order_krw=20_000,
        maximum_daily_entries=2,
        cash_reserve_krw=0,
    )

    result = run_composite_portfolio_backtest(
        _candles(),
        [0.5, 0.0, 0.0],
        [0.0, 0.0, 0.0],
        settings,
        cost_scenario=scenario,
    )

    assert result.fee_regime == scenario.name
    assert result.cost_scenario == scenario.to_dict()
    assert result.total_fees_krw > 0
    assert not result.unsupported_execution_semantics


@pytest.mark.parametrize("core_weights", [[-0.1, 0.0, 0.0], [float("nan"), 0.0, 0.0], [1.1, 0.0, 0.0]])
def test_composite_rejects_invalid_source_weights_even_if_the_sum_could_be_capped(
    core_weights: list[float],
) -> None:
    with pytest.raises(ValueError, match="core weights must be finite fractions"):
        run_composite_portfolio_backtest(
            _candles(), core_weights, [0.0, 0.0, 0.0], TradingSettings()
        )


@pytest.mark.parametrize("ratios", [(-0.1, 1.1), (float("nan"), 0.0), (1.1, 0.0)])
def test_composite_rejects_invalid_component_ratios(ratios: tuple[float, float]) -> None:
    with pytest.raises(ValueError, match="ratios must be finite fractions"):
        run_composite_portfolio_backtest(
            _candles(),
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            TradingSettings(),
            core_ratio=ratios[0],
            satellite_ratio=ratios[1],
        )
