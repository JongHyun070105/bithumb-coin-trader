from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.strategy import StrategyParameters, TrendBreakoutStrategy


def candles_from_closes(closes: list[float]) -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Candle(start + timedelta(days=index), value, value * 1.01, value * 0.99, value, 1.0)
        for index, value in enumerate(closes)
    ]


class StrategyTests(unittest.TestCase):
    def test_breakout_generates_long_signal(self) -> None:
        closes = [100 + index * 0.2 for index in range(45)] + [120, 125, 130]
        strategy = TrendBreakoutStrategy(
            StrategyParameters(
                fast_period=5,
                slow_period=15,
                breakout_period=10,
                exit_period=4,
                volatility_period=5,
                maximum_annualized_volatility=10,
            )
        )
        signals = strategy.generate(candles_from_closes(closes))
        self.assertEqual(signals[-1], Signal.LONG)

    def test_short_signal_can_be_disabled(self) -> None:
        closes = [130 - index * 0.2 for index in range(45)] + [110, 100, 90]
        strategy = TrendBreakoutStrategy(
            StrategyParameters(
                fast_period=5,
                slow_period=15,
                breakout_period=10,
                exit_period=4,
                volatility_period=5,
                maximum_annualized_volatility=10,
                allow_short_signals=False,
            )
        )
        self.assertNotIn(Signal.SHORT, strategy.generate(candles_from_closes(closes)))

    def test_rejects_non_chronological_data(self) -> None:
        candles = candles_from_closes([100] * 20)
        candles[10], candles[11] = candles[11], candles[10]
        with self.assertRaisesRegex(ValueError, "chronological"):
            TrendBreakoutStrategy(StrategyParameters(fast_period=3, slow_period=5)).generate(candles)

    def test_persisted_position_can_resume_at_a_later_decision(self) -> None:
        candles = candles_from_closes([100.0] * 100)
        strategy = TrendBreakoutStrategy()
        resumed = strategy.generate(
            candles,
            initial_position=Signal.LONG,
            start_index=98,
        )
        restarted = strategy.generate(candles)
        self.assertEqual(resumed[-2:], [Signal.LONG, Signal.LONG])
        self.assertEqual(restarted[-1], Signal.FLAT)


if __name__ == "__main__":
    unittest.main()
