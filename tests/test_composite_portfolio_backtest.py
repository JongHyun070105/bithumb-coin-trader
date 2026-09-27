from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.composite_portfolio_backtest import run_composite_portfolio_backtest
from bithumb_coin_trader.models import Candle


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
