from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.research import (
    compare_candidate_factories,
    compare_registered_candidates,
    registered_candidate_factories,
)


class _AlwaysFlat:
    def generate(self, candles: list[Candle] | tuple[Candle, ...]) -> list[Signal]:
        return [Signal.FLAT] * len(candles)


class _Factory:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args: object) -> _AlwaysFlat:
        self.calls.append(args)
        return _AlwaysFlat()


class _AlwaysLong:
    def generate(self, candles: list[Candle] | tuple[Candle, ...]) -> list[Signal]:
        return [Signal.LONG] * len(candles)


class CandidateResearchTests(unittest.TestCase):
    def test_registry_contains_sixteen_explicit_spot_candidates(self) -> None:
        registry = registered_candidate_factories()
        self.assertEqual(len(registry), 16)
        self.assertEqual(
            {
                "trend_daily_close_above_sma140",
                "trend_daily_close_above_sma200",
                "trend_daily_sma50_above_sma200",
                "donchian_4h_55_20_breakout",
                "donchian_4h_20_10_breakout",
                "trend_daily_tsmom_365",
                "trend_monthly_close_above_sma10",
                "donchian_daily_55_20_breakout",
                "donchian_daily_20_10_breakout",
                "dc_30m_bb20_rsi14_with_4h_sma50_uptrend",
                "dc_30m_bb20_rsi14_with_daily_sma140_uptrend",
            },
            set(registry) - {
                "dc_30m_bb20_rsi14_armed_reentry_5pct_exit",
                "mean_reversion_1h_bb20_rsi30_reentry_24bar_exit",
                "mean_reversion_1h_bb20_rsi30_reentry_ema200_uptrend",
                "mean_reversion_1h_bb20_rsi30_reentry_4h_sma50_uptrend",
                "bb_squeeze_bottom20_breakout_120_exit_midline",
            },
        )

    def test_every_registered_candidate_is_prefix_stable(self) -> None:
        start = datetime(2024, 1, 1, 15, tzinfo=UTC)  # KST midnight
        candles = [
            Candle(
                start + timedelta(minutes=30 * index),
                100 + (index % 97) * 0.1,
                101 + (index % 97) * 0.1,
                99 + (index % 97) * 0.1,
                100 + (index % 97) * 0.1,
                1,
            )
            for index in range(20_000)
        ]
        prefix_length = 18_000
        for name, factory in registered_candidate_factories().items():
            with self.subTest(candidate=name):
                prefix = factory().generate(candles[:prefix_length])
                extended = factory().generate(candles)
                self.assertEqual(extended[:prefix_length], prefix)

    def test_all_registered_candidates_use_identical_raw_30m_folds(self) -> None:
        start = datetime(2024, 1, 1, 15, tzinfo=UTC)  # KST midnight
        candles = [
            Candle(
                start + timedelta(minutes=30 * index),
                100,
                100,
                100,
                100,
                1,
            )
            for index in range(12)
        ]
        report = compare_registered_candidates(candles, train_size=6, test_size=2)
        self.assertEqual(report.candidate_count, 16)
        self.assertEqual(
            report.fold_boundaries,
            ((0, 6, 6, 8), (2, 8, 8, 10), (4, 10, 10, 12)),
        )
        for candidate in report.candidates:
            self.assertEqual(
                tuple(
                    (fold.train_start, fold.train_end, fold.test_start, fold.test_end)
                    for fold in candidate.folds
                ),
                report.fold_boundaries,
            )

    def test_candidates_share_fold_boundaries_costs_and_receive_no_oos_data(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        candles = [
            Candle(start + timedelta(hours=index), 100, 100, 100, 100, 1)
            for index in range(16)
        ]
        first = _Factory()
        second = _Factory()
        settings = TradingSettings(fee_rate=0.001, slippage_bps=7)
        with patch(
            "bithumb_coin_trader.research.registered_candidate_factories",
            return_value={"first": first, "second": second},
        ):
            report = compare_registered_candidates(
                candles,
                train_size=6,
                test_size=5,
                settings=settings,
            )
        self.assertEqual(report.candidate_count, 2)
        self.assertEqual(
            [candidate.candidate_name for candidate in report.candidates],
            ["first", "second"],
        )
        self.assertEqual(report.fold_boundaries, ((0, 6, 6, 11), (5, 11, 11, 16)))
        self.assertEqual(first.calls, [(), ()])
        self.assertEqual(second.calls, [(), ()])
        self.assertEqual(
            tuple(
                (fold.train_start, fold.train_end, fold.test_start, fold.test_end)
                for fold in report.candidates[0].folds
            ),
            tuple(
                (fold.train_start, fold.train_end, fold.test_start, fold.test_end)
                for fold in report.candidates[1].folds
            ),
        )

    def test_long_spanning_fold_boundary_is_closed_only_at_overall_oos_end(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        candles = [
            Candle(
                start + timedelta(hours=index),
                100 + index,
                100 + index,
                100 + index,
                100 + index,
                1,
            )
            for index in range(8)
        ]
        settings = TradingSettings(
            fee_rate=0.01,
            slippage_bps=0,
            allocation_fraction=1,
            cash_reserve_krw=0,
        )

        report = compare_candidate_factories(
            candles,
            candidate_factories={"always_long": _AlwaysLong},
            train_size=4,
            test_size=2,
            settings=settings,
        ).candidates[0]

        self.assertEqual(len(report.folds), 2)
        self.assertEqual(report.trade_count, 1)
        self.assertEqual([fold.result.trade_count for fold in report.folds], [0, 1])
        trade = report.folds[1].result.trades[0]
        self.assertEqual((trade.entry_index, trade.exit_index), (1, 4))
        self.assertTrue(trade.is_final_liquidation)
        self.assertEqual(report.folds[0].result.position_curve[-1], Signal.LONG)
        self.assertEqual(report.folds[1].result.position_curve[0], Signal.LONG)
        self.assertEqual(
            report.folds[0].result.final_equity,
            report.folds[1].result.initial_equity,
        )
        quantity = settings.initial_capital_krw / candles[4].open
        expected_final = (
            settings.initial_capital_krw
            - settings.initial_capital_krw * settings.fee_rate
            + quantity * (candles[7].close - candles[4].open)
            - quantity * candles[7].close * settings.fee_rate
        )
        self.assertAlmostEqual(report.oos_equity_curve[-1], expected_final)


if __name__ == "__main__":
    unittest.main()
