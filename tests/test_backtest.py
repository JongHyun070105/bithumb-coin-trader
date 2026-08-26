from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from math import sqrt

from bithumb_coin_trader.backtest import Backtester
from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.risk import RiskLimits


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
        self.assertEqual(result.position_curve, (Signal.FLAT, Signal.LONG, Signal.FLAT))

    def test_final_liquidation_is_identifiable(self) -> None:
        result = Backtester(self.settings).run(
            make_candles([100, 100, 100]),
            [Signal.LONG, Signal.LONG, Signal.LONG],
        )

        self.assertEqual(result.trade_count, 1)
        self.assertTrue(result.trades[0].is_final_liquidation)
        self.assertEqual(result.closed_trade_count, 0)
        self.assertEqual(result.win_rate, 0.0)

        with self.assertRaisesRegex(ValueError, "closed_trade_count"):
            replace(result, closed_trade_count=1)

    def test_gap_forces_flat_before_processing_stale_signal(self) -> None:
        candles = make_candles([100, 110, 120, 130])
        candles[2] = Candle(
            candles[2].timestamp + timedelta(days=1),
            120,
            120,
            120,
            120,
            1,
        )
        candles[3] = Candle(
            candles[3].timestamp + timedelta(days=1),
            130,
            130,
            130,
            130,
            1,
        )
        result = Backtester(
            self.settings,
            expected_interval=timedelta(days=1),
        ).run(candles, [Signal.LONG, Signal.LONG, Signal.FLAT, Signal.FLAT])
        self.assertEqual(result.position_curve[2], Signal.FLAT)
        self.assertTrue(result.trades[0].is_gap_liquidation)

    def test_order_notional_respects_maximum_order(self) -> None:
        settings = TradingSettings(
            initial_capital_krw=100_000,
            fee_rate=0,
            slippage_bps=0,
            allocation_fraction=1,
            minimum_order_krw=5_000,
            maximum_order_krw=10_000,
            cash_reserve_krw=0,
        )
        result = Backtester(settings).run(
            make_candles([100, 100, 100]),
            [Signal.LONG, Signal.FLAT, Signal.FLAT],
        )
        self.assertEqual(result.trades[0].notional, 10_000)

    def test_target_allocation_controls_entry_without_rebalancing(self) -> None:
        settings = TradingSettings(
            fee_rate=0,
            slippage_bps=0,
            allocation_fraction=1,
            minimum_order_krw=1,
            maximum_order_krw=20_000,
            cash_reserve_krw=0,
        )
        result = Backtester(settings).run(
            make_candles([100, 100, 100]),
            [Signal.LONG, Signal.FLAT, Signal.FLAT],
            target_allocations=[0.25, 0.25, 0.25],
        )
        self.assertEqual(result.trades[0].notional, 5_000)

    def test_fee_and_turnover_ledger_matches_trade_evidence(self) -> None:
        settings = TradingSettings(
            fee_rate=0.0025,
            slippage_bps=0,
            allocation_fraction=1,
            cash_reserve_krw=0,
        )
        result = Backtester(settings).run(
            make_candles([100, 100, 100]),
            [Signal.LONG, Signal.FLAT, Signal.FLAT],
        )
        trade = result.trades[0]
        self.assertAlmostEqual(result.total_fees, trade.entry_fee + trade.exit_fee)
        self.assertAlmostEqual(
            result.gross_traded_notional,
            trade.notional + trade.exit_notional,
        )
        self.assertGreater(result.turnover, 0)

    def test_entry_fee_cannot_spend_the_cash_reserve(self) -> None:
        settings = TradingSettings(
            fee_rate=0.01,
            slippage_bps=0,
            allocation_fraction=1,
            minimum_order_krw=1,
            maximum_order_krw=20_000,
            cash_reserve_krw=10_000,
        )
        result = Backtester(settings).run(
            make_candles([100, 100, 100]),
            [Signal.LONG, Signal.FLAT, Signal.FLAT],
        )
        trade = result.trades[0]
        self.assertGreaterEqual(20_000 - trade.notional - trade.entry_fee, 10_000)

    def test_live_risk_drawdown_blocks_reentry_but_not_exit(self) -> None:
        settings = TradingSettings(
            initial_capital_krw=20_000,
            fee_rate=0,
            slippage_bps=0,
            allocation_fraction=1,
            minimum_order_krw=1,
            maximum_order_krw=20_000,
            maximum_daily_entries=4,
            cash_reserve_krw=0,
        )
        result = Backtester(
            settings,
            risk_limits=RiskLimits(
                minimum_order_krw=1,
                maximum_order_krw=20_000,
                maximum_daily_loss_fraction=1,
                maximum_drawdown_fraction=0.10,
                maximum_daily_entries=4,
            ),
        ).run(
            make_candles([100, 100, 80, 80, 80]),
            [Signal.LONG, Signal.FLAT, Signal.LONG, Signal.FLAT, Signal.FLAT],
        )
        self.assertEqual(result.closed_trade_count, 1)
        self.assertTrue(
            any(
                "maximum drawdown reached" in rejection.reasons
                for rejection in result.entry_rejections
            )
        )

    def test_daily_entry_limit_does_not_defer_stale_long_to_next_day(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        candles = [
            Candle(start + timedelta(minutes=30 * index), 100, 100, 100, 100, 1)
            for index in range(8)
        ]
        signals = [
            Signal.LONG,
            Signal.FLAT,
            Signal.LONG,
            Signal.LONG,
            Signal.LONG,
            Signal.FLAT,
            Signal.FLAT,
            Signal.FLAT,
        ]
        result = Backtester(
            self.settings,
            expected_interval=timedelta(minutes=30),
        ).run(candles, signals)
        self.assertEqual(result.trade_count, 1)

    def test_sharpe_annualization_uses_candle_frequency(self) -> None:
        daily = make_candles([100, 110, 105, 115, 110])
        intraday = [
            Candle(
                daily[0].timestamp + timedelta(minutes=30 * index),
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
            )
            for index, candle in enumerate(daily)
        ]
        signals = [Signal.LONG, Signal.LONG, Signal.LONG, Signal.LONG, Signal.FLAT]
        daily_result = Backtester(self.settings).run(daily, signals)
        intraday_result = Backtester(self.settings).run(intraday, signals)
        self.assertAlmostEqual(intraday_result.sharpe / daily_result.sharpe, sqrt(48), places=6)


if __name__ == "__main__":
    unittest.main()
