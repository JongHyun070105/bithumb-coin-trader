from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta, timezone

from bithumb_coin_trader.backtest import Backtester
from bithumb_coin_trader.daily_strategy_candidates import (
    BuyHoldBenchmark,
    WeeklyAbsoluteMomentumStrategy,
    WeeklyDonchianStrategy,
    WeeklyDualMomentumStrategy,
    WeeklySmaCrossStrategy,
    daily_candidate_factories,
)
from bithumb_coin_trader.models import Candle, Signal


def _candles(count: int, *, start: datetime | None = None) -> list[Candle]:
    # 2024-01-01 00:00 KST.
    start = start or datetime(2023, 12, 31, 15, tzinfo=UTC)
    result: list[Candle] = []
    price = 100.0
    for index in range(count):
        price *= 1.005
        result.append(
            Candle(
                timestamp=start + timedelta(days=index),
                open=price * 0.999,
                high=price * 1.003,
                low=price * 0.997,
                close=price,
                volume=10.0 + index % 5,
            )
        )
    return result


STRATEGIES = (
    BuyHoldBenchmark,
    WeeklyAbsoluteMomentumStrategy,
    WeeklySmaCrossStrategy,
    WeeklyDonchianStrategy,
    WeeklyDualMomentumStrategy,
)


class DailyCandidateContractTests(unittest.TestCase):
    def test_registry_and_required_history_are_frozen(self) -> None:
        factories = daily_candidate_factories()
        self.assertEqual(len(factories), 5)
        self.assertEqual(
            [factory().required_history_bars for factory in factories.values()],
            [1, 127, 200, 91, 169],
        )

    def test_candidates_are_prefix_stable_and_long_flat(self) -> None:
        candles = _candles(420)
        for strategy_type in STRATEGIES:
            with self.subTest(strategy=strategy_type.__name__):
                prefix = strategy_type().generate(candles[:350])
                extended = strategy_type().generate(candles)
                self.assertEqual(prefix, extended[:350])
                self.assertLessEqual(set(extended), {Signal.FLAT, Signal.LONG})

    def test_indicators_remain_flat_before_required_history(self) -> None:
        candles = _candles(420)
        for strategy_type in STRATEGIES[1:]:
            strategy = strategy_type()
            signals = strategy.generate(candles)
            self.assertEqual(
                signals[: strategy.required_history_bars - 1],
                [Signal.FLAT] * (strategy.required_history_bars - 1),
                strategy_type.__name__,
            )

    def test_state_changes_only_on_completed_kst_sunday(self) -> None:
        candles = _candles(420)
        for strategy_type in STRATEGIES:
            signals = strategy_type().generate(candles)
            changes = [
                index
                for index, signal in enumerate(signals)
                if signal != (signals[index - 1] if index else Signal.FLAT)
            ]
            self.assertTrue(changes, strategy_type.__name__)
            self.assertTrue(
                all(
                    candles[index].timestamp.astimezone(
                        timezone(timedelta(hours=9))
                    ).weekday()
                    == 6
                    for index in changes
                ),
                strategy_type.__name__,
            )

    def test_current_close_cannot_cause_same_bar_open_execution(self) -> None:
        candles = _candles(220)
        strategy = WeeklySmaCrossStrategy()
        signals = strategy.generate(candles)
        change = next(
            index
            for index in range(1, len(signals))
            if signals[index] is Signal.LONG and signals[index - 1] is Signal.FLAT
        )
        # The completed Sunday decides LONG at its close. The position before
        # that candle (and therefore its open) remains FLAT.
        self.assertIs(signals[change - 1], Signal.FLAT)
        self.assertIs(signals[change], Signal.LONG)
        result = Backtester(expected_interval=timedelta(days=1)).run(candles, signals)
        self.assertEqual(result.trades[0].entry_index, change + 1)

    def test_future_candle_mutation_does_not_change_prior_signal(self) -> None:
        candles = _candles(260)
        prior = WeeklyDonchianStrategy().generate(candles)
        future = Candle(
            timestamp=candles[-1].timestamp + timedelta(days=1),
            open=1.0,
            high=1.0,
            low=1.0,
            close=1.0,
            volume=0.0,
        )
        extended = WeeklyDonchianStrategy().generate([*candles, future])
        self.assertEqual(prior, extended[:-1])

    def test_rejects_wrong_market_non_midnight_duplicates_and_daily_gaps(self) -> None:
        base = _candles(4)
        cases = []
        source = base[1]
        cases.append(
            [
                base[0],
                Candle(
                    timestamp=source.timestamp,
                    open=source.open,
                    high=source.high,
                    low=source.low,
                    close=source.close,
                    volume=source.volume,
                    market="KRW-ETH",
                ),
            ]
        )
        cases.append([base[0], base[2]])
        cases.append([base[0], base[0]])
        shifted = Candle(
            timestamp=source.timestamp + timedelta(hours=1),
            open=source.open,
            high=source.high,
            low=source.low,
            close=source.close,
            volume=source.volume,
        )
        cases.append([shifted])
        for candles in cases:
            with self.subTest(case=candles):
                with self.assertRaises(ValueError):
                    BuyHoldBenchmark().generate(candles)


if __name__ == "__main__":
    unittest.main()
