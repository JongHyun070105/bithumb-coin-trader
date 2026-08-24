from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.winrate_volatility_candidates import candidate_factories


def _candles(count: int) -> list[Candle]:
    start = datetime(2025, 1, 1, 16, tzinfo=UTC)  # KST 01:00; exercises bucket alignment.
    result: list[Candle] = []
    price = 100.0
    for index in range(count):
        phase = index % 96
        drift = 0.035 if phase < 72 else -0.015
        price = max(10.0, price + drift)
        volume = 10.0
        if phase == 72:
            price += 1.8
            volume = 24.0
        result.append(
            Candle(
                timestamp=start + timedelta(minutes=30 * index),
                open=price - 0.02,
                high=price + (1.7 if phase == 72 else 0.12),
                low=price - 0.12,
                close=price,
                volume=volume,
            )
        )
    return result


class VolatilityCandidateTests(unittest.TestCase):
    def test_factories_have_matching_unique_names_and_long_flat_output(self) -> None:
        factories = candidate_factories()
        self.assertEqual(len(factories), 4)
        self.assertEqual(len(set(factories)), len(factories))
        candles = _candles(800)
        for name, factory in factories.items():
            with self.subTest(name=name):
                strategy = factory()
                self.assertEqual(strategy.name, name)
                signals = strategy.generate(candles)
                self.assertEqual(len(signals), len(candles))
                self.assertNotIn(Signal.SHORT, signals)

    def test_all_candidates_are_prefix_stable(self) -> None:
        candles = _candles(900)
        for name, factory in candidate_factories().items():
            with self.subTest(name=name):
                prefix = factory().generate(candles[:-80])
                extended = factory().generate(candles)
                self.assertEqual(prefix, extended[: len(prefix)])

    def test_gap_resets_an_open_position_to_flat(self) -> None:
        candles = _candles(1_200)
        strategies_with_entries = 0
        for name, factory in candidate_factories().items():
            signals = factory().generate(candles)
            long_indices = [
                index
                for index, signal in enumerate(signals)
                if signal is Signal.LONG
            ]
            if not long_indices:
                continue
            strategies_with_entries += 1
            gap_source_index = long_indices[0] + 1
            gapped = candles[:gap_source_index] + candles[gap_source_index + 1 :]
            gapped_signals = factory().generate(gapped)
            with self.subTest(name=name):
                self.assertEqual(gapped_signals[gap_source_index], Signal.FLAT)
        self.assertGreaterEqual(strategies_with_entries, 2)

    def test_non_chronological_and_mixed_market_inputs_fail_closed(self) -> None:
        factory = next(iter(candidate_factories().values()))
        candles = _candles(32)
        candles[10], candles[11] = candles[11], candles[10]
        with self.assertRaisesRegex(ValueError, "chronological"):
            factory().generate(candles)

        candles = _candles(32)
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
            factory().generate(candles)


if __name__ == "__main__":
    unittest.main()
