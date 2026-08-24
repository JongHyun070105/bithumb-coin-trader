from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.winrate_meta_candidates import candidate_factories


def _candles(count: int) -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    price = 100.0
    result: list[Candle] = []
    for index in range(count):
        phase = index % 144
        drift = 0.12 if phase < 96 else (-0.18 if phase < 112 else 0.04)
        close = max(10.0, price + drift + ((index % 7) - 3) * 0.015)
        result.append(
            Candle(
                timestamp=start + timedelta(minutes=30 * index),
                open=price,
                high=max(price, close) + 0.20,
                low=min(price, close) - 0.20,
                close=close,
                volume=10.0 + index % 11,
            )
        )
        price = close
    return result


class MetaCandidateTests(unittest.TestCase):
    def test_factories_are_named_deterministic_long_flat_strategies(self) -> None:
        candles = _candles(1_200)
        factories = candidate_factories()
        self.assertEqual(len(factories), 3)
        for name, factory in factories.items():
            strategy = factory()
            first = strategy.generate(candles)
            second = strategy.generate(candles)
            self.assertEqual(strategy.name, name)
            self.assertEqual(first, second)
            self.assertEqual(len(first), len(candles))
            self.assertNotIn(Signal.SHORT, first)

    def test_future_tail_changes_cannot_alter_earlier_signals(self) -> None:
        candles = _candles(1_400)
        cutoff = 1_260
        altered = list(candles)
        price = altered[cutoff - 1].close
        for index in range(cutoff, len(altered)):
            price *= 1.03 if index % 2 else 0.97
            source = altered[index]
            altered[index] = Candle(
                timestamp=source.timestamp,
                open=price,
                high=price * 1.02,
                low=price * 0.98,
                close=price,
                volume=source.volume * 20.0,
            )
        for factory in candidate_factories().values():
            baseline = factory().generate(candles)
            changed = factory().generate(altered)
            self.assertEqual(baseline[:cutoff], changed[:cutoff])

    def test_gap_resets_learning_and_requires_a_fresh_warmup(self) -> None:
        candles = _candles(900)
        gap_at = 650
        shifted = candles[:gap_at] + [
            Candle(
                timestamp=candle.timestamp + timedelta(minutes=30),
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
            )
            for candle in candles[gap_at:]
        ]
        for factory in candidate_factories().values():
            signals = factory().generate(shifted)
            warmup_end = min(len(signals), gap_at + 256)
            self.assertEqual(
                signals[gap_at:warmup_end],
                [Signal.FLAT] * (warmup_end - gap_at),
            )

    def test_mixed_market_and_non_chronological_inputs_fail_closed(self) -> None:
        candles = _candles(400)
        source = candles[-1]
        mixed = candles[:-1] + [
            Candle(
                timestamp=source.timestamp,
                open=source.open,
                high=source.high,
                low=source.low,
                close=source.close,
                volume=source.volume,
                market="KRW-ETH",
            )
        ]
        with self.assertRaisesRegex(ValueError, "exactly one KRW-BTC"):
            next(iter(candidate_factories().values()))().generate(mixed)
        reordered = list(candles)
        reordered[100], reordered[101] = reordered[101], reordered[100]
        with self.assertRaisesRegex(ValueError, "chronological"):
            next(iter(candidate_factories().values()))().generate(reordered)


if __name__ == "__main__":
    unittest.main()
