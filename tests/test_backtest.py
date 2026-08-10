from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from bithumb_coin_trader.backtest import Backtester
from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.models import Candle, Signal


def make_candles(prices: list[float]) -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Candle(start + timedelta(days=index), price, price, price, price, 1)
        for index, price in enumerate(prices)
    ]


class BacktestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = TradingSettings(fee_rate=0, slippage_bps=0, allocation_fraction=1, cash_reserve_krw=0)

    def test_signal_executes_on_next_open(self) -> None:
        result = Backtester(self.settings).run(
            make_candles([100, 200, 300]),
            [Signal.LONG, Signal.FLAT, Signal.FLAT],
        )
        self.assertEqual(result.trades[0].entry_price, 200)
        self.assertEqual(result.trades[0].exit_price, 300)

    def test_profitable_short_is_supported_in_research(self) -> None:
        result = Backtester(self.settings, allow_short=True).run(
            make_candles([100, 90, 80]),
            [Signal.SHORT, Signal.SHORT, Signal.FLAT],
        )
        self.assertGreater(result.final_equity, result.initial_equity)
        self.assertEqual(result.trades[0].side, Signal.SHORT)

    def test_short_is_flat_when_execution_not_allowed(self) -> None:
        result = Backtester(self.settings, allow_short=False).run(
            make_candles([100, 90, 80]),
            [Signal.SHORT, Signal.SHORT, Signal.FLAT],
        )
        self.assertEqual(result.trade_count, 0)
        self.assertEqual(result.final_equity, result.initial_equity)

    def test_fees_reduce_equity(self) -> None:
        settings = TradingSettings(fee_rate=0.0025, slippage_bps=0, allocation_fraction=1, cash_reserve_krw=0)
        result = Backtester(settings).run(
            make_candles([100, 100, 100]),
            [Signal.LONG, Signal.FLAT, Signal.FLAT],
        )
        self.assertLess(result.final_equity, result.initial_equity)


if __name__ == "__main__":
    unittest.main()
