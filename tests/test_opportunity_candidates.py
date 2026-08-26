from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.opportunity_candidates import (
    DonchianRetestCandidate,
    _completed_four_hour_bars,
    candidate_factories,
)


def _candles(count: int) -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Candle(
            market="KRW-BTC",
            timestamp=start + timedelta(minutes=30 * index),
            open=101.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=10.0,
        )
        for index in range(count)
    ]


class OpportunityCandidateTests(unittest.TestCase):
    def test_all_opportunity_candidates_are_prefix_stable_and_long_flat(self) -> None:
        candles = _candles(6_000)
        for factory in candidate_factories().values():
            prefix = factory().generate(candles[:5_000])
            full = factory().generate(candles)
            self.assertEqual(full[:5_000], prefix)
            self.assertLessEqual(set(full), {Signal.FLAT, Signal.LONG})

    def test_donchian_breakout_candle_cannot_also_be_its_retest(self) -> None:
        candles = _candles(58 * 8)
        breakout_index = _completed_four_hour_bars(candles)[55].source_index
        candles[breakout_index] = Candle(
            market="KRW-BTC",
            timestamp=candles[breakout_index].timestamp,
            open=101.0,
            high=103.0,
            low=100.5,
            close=102.0,
            volume=10.0,
        )
        candles[breakout_index + 1] = Candle(
            market="KRW-BTC",
            timestamp=candles[breakout_index + 1].timestamp,
            open=102.0,
            high=102.2,
            low=100.8,
            close=102.0,
            volume=10.0,
        )
        signals = DonchianRetestCandidate(name="test").generate(candles)
        self.assertIs(signals[breakout_index], Signal.FLAT)
        self.assertIs(signals[breakout_index + 1], Signal.LONG)

    def test_gap_resets_every_candidate_to_flat(self) -> None:
        candles = _candles(6_000)
        shifted = []
        for index, candle in enumerate(candles):
            timestamp = candle.timestamp + (
                timedelta(hours=2) if index >= 5_000 else timedelta()
            )
            shifted.append(
                Candle(
                    market=candle.market,
                    timestamp=timestamp,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    volume=candle.volume,
                )
            )
        for factory in candidate_factories().values():
            self.assertIs(factory().generate(shifted)[5_000], Signal.FLAT)
