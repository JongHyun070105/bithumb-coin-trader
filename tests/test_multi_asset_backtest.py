from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.multi_asset_backtest import MultiAssetSharedCashBacktester


MARKET = "KRW-BTC"


def _candles(*, duplicate: bool = False) -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    timestamps = [start, start if duplicate else start + timedelta(days=1)]
    return [
        Candle(
            market=MARKET,
            timestamp=timestamp,
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1.0,
        )
        for timestamp in timestamps
    ]


def _backtester() -> MultiAssetSharedCashBacktester:
    settings = TradingSettings(
        initial_capital_krw=100_000,
        fee_rate=0.0,
        slippage_bps=0.0,
        allocation_fraction=1.0,
        minimum_order_krw=1,
        maximum_order_krw=100_000,
        maximum_daily_entries=1,
        cash_reserve_krw=0,
    )
    return MultiAssetSharedCashBacktester(
        settings,
        target_total_exposure=0.50,
        drift_total_exposure_limit=0.60,
        target_per_asset_exposure=0.50,
        drift_per_asset_exposure_limit=0.60,
        min_listing_days=0,
    )


def test_shared_cash_backtest_uses_prior_bar_target_and_liquidates() -> None:
    result = _backtester().run({MARKET: _candles()}, {MARKET: [0.25, 0.25]})

    assert result.fills[0].timestamp == _candles()[1].timestamp
    assert result.fills[0].side == "buy"
    assert result.fills[-1].reason == "final_liquidation"
    assert result.final_equity == 100_000
    assert all(cash >= 0 for cash in result.cash_curve)


def test_duplicate_market_timestamp_is_rejected_instead_of_overwritten() -> None:
    with pytest.raises(ValueError, match="unique chronological timestamps"):
        _backtester().run({MARKET: _candles(duplicate=True)}, {MARKET: [0.2, 0.0]})


@pytest.mark.parametrize("invalid_weight", [-0.01, 1.01, float("nan"), float("inf")])
def test_non_fraction_or_non_finite_target_weight_is_rejected(invalid_weight: float) -> None:
    with pytest.raises(ValueError, match="finite fractions in \\[0, 1\\]"):
        _backtester().run({MARKET: _candles()}, {MARKET: [invalid_weight, 0.0]})


def test_market_key_mismatch_is_rejected() -> None:
    wrong_market_candle = Candle(
        market="KRW-ETH",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        open=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        volume=1.0,
    )
    with pytest.raises(ValueError, match="does not match mapping key"):
        _backtester().run({MARKET: [wrong_market_candle]}, {MARKET: [0.0]})


def test_weights_for_unknown_market_are_rejected() -> None:
    with pytest.raises(ValueError, match="without candles"):
        _backtester().run({MARKET: _candles()}, {"KRW-ETH": [0.0, 0.0]})
