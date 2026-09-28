from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bithumb_coin_trader.microstructure_features import OrderbookSnapshot
from bithumb_coin_trader.microstructure_taker_simulator import RealisticTakerExecutionSimulator


def test_partial_depth_slice_contributes_only_its_filled_quantity() -> None:
    orderbook = OrderbookSnapshot(
        market="KRW-BTC",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        bids=((99.0, 2.0),),
        asks=((100.0, 1.0), (110.0, 1.0)),
    )

    result = RealisticTakerExecutionSimulator(
        default_latency_ms=0.0,
        adverse_selection_bps_per_sec=0.0,
    ).execute_market_order(orderbook, "BUY", target_notional_krw=150.0)

    expected_size = 1.0 + 50.0 / 110.0
    assert result.filled_notional_krw == pytest.approx(150.0)
    assert result.filled_size == pytest.approx(expected_size)
    assert result.filled_size == pytest.approx(sum(fill.size for fill in result.fill_slices))
    assert result.vwap_price == pytest.approx(150.0 / expected_size)
