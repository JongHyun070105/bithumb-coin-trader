from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.winrate_mean_reversion_candidates import (
    _completed_four_hour_regime,
    candidate_factories,
)


def _candles(count: int) -> list[Candle]:
    start = datetime(2024, 1, 1, 15, tzinfo=UTC)  # KST midnight, 4h aligned
    result: list[Candle] = []
    price = 100.0
    for index in range(count):
        phase = index % 96
        if phase < 68:
            change = 0.08
        elif phase < 78:
            change = -0.95
        else:
            change = 0.48
        opening = price
        price = max(20.0, price + change)
        result.append(
            Candle(
                timestamp=start + timedelta(minutes=30 * index),
                open=opening,
                high=max(opening, price) + 0.35,
                low=min(opening, price) - 0.35,
                close=price,
                volume=10.0 + index % 9,
            )
        )
    return result


class MeanReversionCandidateContractTests(unittest.TestCase):
    def test_factories_expose_distinct_named_long_flat_candidates(self) -> None:
        candles = _candles(4_000)
        factories = candidate_factories()
        self.assertEqual(len(factories), 5)
        self.assertEqual(len(set(factories)), 5)
        for name, factory in factories.items():
            strategy = factory()
            self.assertEqual(strategy.name, name)
            signals = strategy.generate(candles)
            self.assertEqual(len(signals), len(candles))
            self.assertNotIn(Signal.SHORT, signals)

    def test_candidates_are_prefix_stable(self) -> None:
        candles = _candles(2_400)
        for factory in candidate_factories().values():
            strategy = factory()
            prefix = strategy.generate(candles[:-200])
            extended = strategy.generate(candles)
            self.assertEqual(prefix, extended[: len(prefix)], strategy.name)

    def test_gap_resets_position_and_indicator_history(self) -> None:
        candles = _candles(4_000)
        generated = [factory().generate(candles) for factory in candidate_factories().values()]
        selected = next(
            (signals for signals in generated if Signal.LONG in signals),
            None,
        )
        self.assertIsNotNone(selected, "synthetic series should exercise at least one candidate")
        assert selected is not None
        long_at = next(index for index, signal in enumerate(selected) if signal is Signal.LONG)
        gap_at = long_at + 1
        gapped = candles[:gap_at] + candles[gap_at + 1 :]
        for factory in candidate_factories().values():
            signals = factory().generate(gapped)
            self.assertEqual(signals[gap_at], Signal.FLAT, factory().name)

    def test_four_hour_regime_is_not_backfilled_into_its_source_bucket(self) -> None:
        candles: list[Candle] = []
        start = datetime(2024, 1, 1, 15, tzinfo=UTC)
        for index in range(24):
            bucket_close = (100.0, 80.0, 120.0)[index // 8]
            candles.append(
                Candle(
                    timestamp=start + timedelta(minutes=30 * index),
                    open=bucket_close,
                    high=bucket_close + 0.5,
                    low=bucket_close - 0.5,
                    close=bucket_close,
                    volume=10.0,
                )
            )
        regime = _completed_four_hour_regime(candles, period=2, floor=1.0)
        self.assertEqual(regime[16:23], [False] * 7)
        self.assertTrue(regime[23])

    def test_mixed_markets_and_non_chronological_input_fail_closed(self) -> None:
        candles = _candles(64)
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
        strategy = next(iter(candidate_factories().values()))()
        with self.assertRaisesRegex(ValueError, "exactly one market"):
            strategy.generate(mixed)
        with self.assertRaisesRegex(ValueError, "strictly chronological"):
            strategy.generate(list(reversed(candles)))


if __name__ == "__main__":
    unittest.main()
