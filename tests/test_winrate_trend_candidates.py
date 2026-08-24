from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.winrate_trend_candidates import candidate_factories


def _candles(count: int) -> list[Candle]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    result: list[Candle] = []
    price = 100.0
    for index in range(count):
        phase = index % 320
        if phase < 120:
            change = 0.025
        elif phase < 160:
            change = -0.04
        elif phase < 280:
            change = 0.11
        else:
            change = -0.08
        open_price = price
        price = max(10.0, price + change)
        result.append(
            Candle(
                timestamp=start + timedelta(minutes=30 * index),
                open=open_price,
                high=max(open_price, price) + 0.08,
                low=min(open_price, price) - 0.08,
                close=price,
                volume=20.0 + index % 11,
            )
        )
    return result


class TrendCandidateContractTests(unittest.TestCase):
    def test_factories_return_named_long_flat_strategies(self) -> None:
        factories = candidate_factories()
        self.assertEqual(len(factories), 4)
        candles = _candles(1_200)
        for name, factory in factories.items():
            strategy = factory()
            self.assertEqual(strategy.name, name)
            signals = strategy.generate(candles)
            self.assertEqual(len(signals), len(candles))
            self.assertNotIn(Signal.SHORT, signals)

    def test_outputs_are_prefix_stable(self) -> None:
        candles = _candles(1_500)
        for factory in candidate_factories().values():
            strategy = factory()
            prefix = strategy.generate(candles[:-120])
            extended = strategy.generate(candles)
            self.assertEqual(prefix, extended[: len(prefix)], strategy.name)

    def test_gap_resets_position_and_indicator_warmup(self) -> None:
        candles = _candles(1_500)
        for factory in candidate_factories().values():
            strategy = factory()
            signals = strategy.generate(candles)
            long_indices = [
                index for index, signal in enumerate(signals) if signal is Signal.LONG
            ]
            if not long_indices:
                continue
            gap_at = long_indices[len(long_indices) // 2]
            gapped = candles[:gap_at] + candles[gap_at + 1 :]
            after_gap = strategy.generate(gapped)
            self.assertEqual(after_gap[gap_at], Signal.FLAT, strategy.name)
            self.assertTrue(
                all(
                    signal is Signal.FLAT
                    for signal in after_gap[
                        gap_at : gap_at + strategy.slow_period - 1
                    ]
                ),
                strategy.name,
            )

    def test_invalid_source_alignment_and_mixed_markets_fail_closed(self) -> None:
        candles = _candles(300)
        source = candles[-1]
        candles[-1] = Candle(
            timestamp=source.timestamp + timedelta(minutes=1),
            open=source.open,
            high=source.high,
            low=source.low,
            close=source.close,
            volume=source.volume,
        )
        strategy = next(iter(candidate_factories().values()))()
        with self.assertRaisesRegex(ValueError, "aligned 30-minute"):
            strategy.generate(candles)

        candles = _candles(300)
        source = candles[-1]
        candles[-1] = Candle(
            timestamp=source.timestamp,
            open=source.open,
            high=source.high,
            low=source.low,
            close=source.close,
            volume=source.volume,
            market="KRW-ETH",
        )
        with self.assertRaisesRegex(ValueError, "exactly one market"):
            strategy.generate(candles)


if __name__ == "__main__":
    unittest.main()
