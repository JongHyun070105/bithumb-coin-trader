from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.live_policy_research import (
    DEFENSIVE_BASELINE_POLICY,
    ENHANCED_EXIT_POLICY,
    FIXED_EXIT_POLICY,
    ExitPolicy,
    live_entry_eligibility,
    run_policy_backtest,
)
from bithumb_coin_trader.models import Candle


def _candles(closes: list[float], *, opens: list[float] | None = None) -> list[Candle]:
    actual_opens = opens or closes
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return [
        Candle(
            timestamp=start + timedelta(minutes=30 * index),
            open=actual_opens[index],
            high=max(actual_opens[index], close) * 1.001,
            low=min(actual_opens[index], close) * 0.999,
            close=close,
            volume=100.0 + index,
        )
        for index, close in enumerate(closes)
    ]


def _settings(*, capital: int = 50_000) -> TradingSettings:
    return TradingSettings(
        initial_capital_krw=capital,
        fee_rate=0.0,
        slippage_bps=0.0,
        allocation_fraction=0.30,
        minimum_order_krw=5_000,
        maximum_order_krw=30_000,
        maximum_daily_entries=4,
        cash_reserve_krw=0,
    )


class LiveEntryProxyTests(unittest.TestCase):
    def test_entry_proxy_is_prefix_stable(self) -> None:
        closes = [100.0 + index * 0.08 + (index % 9) * 0.02 for index in range(180)]
        candles = _candles(closes)
        prefix = live_entry_eligibility(candles[:150])
        extended = live_entry_eligibility(candles)
        self.assertEqual(prefix, extended[:150])

    def test_gap_disables_every_window_that_contains_it(self) -> None:
        candles = _candles([100.0 + index * 0.1 for index in range(220)])
        for index in range(100, len(candles)):
            source = candles[index]
            candles[index] = Candle(
                timestamp=source.timestamp + timedelta(minutes=30),
                open=source.open,
                high=source.high,
                low=source.low,
                close=source.close,
                volume=source.volume,
            )
        decisions = live_entry_eligibility(candles)
        self.assertFalse(any(decisions[100:199]))


class LivePolicyBacktestTests(unittest.TestCase):
    def test_entry_and_exit_decisions_fill_at_next_open(self) -> None:
        candles = _candles(
            [100.0, 100.0, 104.0, 120.0],
            opens=[100.0, 110.0, 111.0, 112.0],
        )
        result = run_policy_backtest(
            candles,
            [True, False, False, False],
            settings=_settings(),
            policy=FIXED_EXIT_POLICY,
        )
        self.assertEqual(result.trade_count, 1)
        self.assertEqual(result.trades[0].entry_index, 1)
        self.assertEqual(result.trades[0].entry_price, 110.0)
        self.assertEqual(result.trades[0].exit_index, 2)
        self.assertEqual(result.trades[0].exit_price, 111.0)

    def test_enhanced_policy_executes_one_partial_exit(self) -> None:
        candles = _candles(
            [100.0, 100.0, 102.1, 102.2, 104.0],
            opens=[100.0, 100.0, 100.0, 102.0, 103.0],
        )
        result = run_policy_backtest(
            candles,
            [True, False, False, False, False],
            settings=_settings(),
            policy=ENHANCED_EXIT_POLICY,
        )
        self.assertEqual(result.partial_exit_count, 1)
        self.assertEqual(result.trades[0].partial_exit_count, 1)

    def test_timecut_is_observed_after_four_hours_and_filled_next_open(self) -> None:
        candles = _candles([100.0] * 11)
        result = run_policy_backtest(
            candles,
            [True] + [False] * 10,
            settings=_settings(),
            policy=ENHANCED_EXIT_POLICY,
        )
        self.assertEqual(result.timecut_exit_count, 1)
        self.assertEqual(result.trades[0].exit_index, 9)

    def test_defensive_baseline_does_not_apply_new_timecut(self) -> None:
        candles = _candles([100.0] * 11)
        result = run_policy_backtest(
            candles,
            [True] + [False] * 10,
            settings=_settings(),
            policy=DEFENSIVE_BASELINE_POLICY,
        )
        self.assertEqual(result.timecut_exit_count, 0)
        self.assertTrue(result.trades[0].is_final_liquidation)

    def test_partial_exit_respects_bithumb_minimum_for_both_legs(self) -> None:
        candles = _candles([100.0, 100.0, 102.1, 102.2])
        result = run_policy_backtest(
            candles,
            [True, False, False, False],
            settings=_settings(capital=32_000),
            policy=ENHANCED_EXIT_POLICY,
        )
        self.assertEqual(result.partial_exit_count, 0)

    def test_partial_exit_is_cancelled_when_next_open_gap_breaks_minimum(self) -> None:
        partial_only = ExitPolicy(
            name="partial_only",
            stop_loss=0.50,
            take_profit=1.0,
            partial_take_profit=0.020,
        )
        candles = _candles(
            [100.0, 100.0, 102.1, 60.0],
            opens=[100.0, 100.0, 100.0, 60.0],
        )
        result = run_policy_backtest(
            candles,
            [True, False, False, False],
            settings=_settings(),
            policy=partial_only,
        )
        self.assertEqual(result.partial_exit_count, 0)
        self.assertEqual(result.partial_rejection_count, 1)
        self.assertTrue(result.trades[0].is_final_liquidation)
        self.assertEqual(result.trades[0].partial_exit_count, 0)

    def test_fifth_kst_daily_entry_is_blocked_and_next_day_resets(self) -> None:
        closes = [100.0] * 34
        for index in (1, 3, 5, 7, 9, 31):
            closes[index] = 101.0
        candles = _candles(closes, opens=[100.0] * len(closes))
        decisions = [False] * len(candles)
        for index in (0, 2, 4, 6, 8, 30):
            decisions[index] = True
        fast_exit = ExitPolicy(name="fast_exit", stop_loss=0.50, take_profit=0.005)
        result = run_policy_backtest(
            candles,
            decisions,
            settings=_settings(),
            policy=fast_exit,
        )
        self.assertEqual(
            [trade.entry_index for trade in result.trades],
            [1, 3, 5, 7, 31],
        )

    def test_policy_contract_is_research_only_data(self) -> None:
        self.assertEqual(FIXED_EXIT_POLICY.stop_loss, 0.018)
        self.assertEqual(DEFENSIVE_BASELINE_POLICY.breakeven_activate, 0.010)
        self.assertEqual(DEFENSIVE_BASELINE_POLICY.trailing_activate, 0.022)
        self.assertIsNone(DEFENSIVE_BASELINE_POLICY.partial_take_profit)
        self.assertIsNone(DEFENSIVE_BASELINE_POLICY.timecut)
        self.assertEqual(ENHANCED_EXIT_POLICY.partial_take_profit, 0.020)
        with self.assertRaisesRegex(ValueError, "positive"):
            ExitPolicy(name="invalid", stop_loss=0.0)


if __name__ == "__main__":
    unittest.main()
