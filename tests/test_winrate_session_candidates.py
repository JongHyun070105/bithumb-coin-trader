from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.winrate_session_candidates import candidate_factories


def _candles(count: int, *, market: str = "KRW-BTC") -> list[Candle]:
    start = datetime(2025, 1, 1, 15, tzinfo=UTC)  # KST midnight
    result: list[Candle] = []
    price = 100.0
    for index in range(count):
        cycle = index % 96
        drift = 0.13 if cycle < 64 else -0.08
        pulse = 0.55 if index % 31 == 0 else 0.0
        open_price = price
        price = max(10.0, price + drift + pulse)
        result.append(
            Candle(
                timestamp=start + timedelta(minutes=30 * index),
                open=open_price,
                high=max(open_price, price) + 0.18 + pulse,
                low=min(open_price, price) - 0.18,
                close=price,
                volume=10.0 + index % 8 + (18.0 if index % 31 == 0 else 0.0),
                market=market,
            )
        )
    return result


class SessionCandidateContractTests(unittest.TestCase):
    def test_registry_exposes_distinct_named_factories(self) -> None:
        factories = candidate_factories()
        self.assertEqual(
            tuple(factories),
            (
                "kst_vwap_momentum",
                "kst_vwap_reclaim",
                "kst_relative_volume_breakout",
                "kst_low_vol_vwap_expansion",
            ),
        )
        for name, factory in factories.items():
            self.assertEqual(factory().name, name)

    def test_all_candidates_are_prefix_stable_and_long_flat_only(self) -> None:
        candles = _candles(1_200)
        for factory in candidate_factories().values():
            strategy = factory()
            prefix = strategy.generate(candles[:-100])
            extended = strategy.generate(candles)
            self.assertEqual(prefix, extended[: len(prefix)], strategy.name)
            self.assertNotIn(Signal.SHORT, extended, strategy.name)
            self.assertEqual(len(extended), len(candles))

    def test_candidates_can_emit_selective_entries_on_session_volume_data(self) -> None:
        candles = _candles(2_000)
        counts = {
            name: sum(signal is Signal.LONG for signal in factory().generate(candles))
            for name, factory in candidate_factories().items()
        }
        self.assertGreaterEqual(sum(value > 0 for value in counts.values()), 2, counts)

    def test_gap_forces_flat_and_resets_indicator_state(self) -> None:
        candles = _candles(2_000)
        strategy = candidate_factories()["kst_vwap_momentum"]()
        signals = strategy.generate(candles)
        long_index = next(index for index, signal in enumerate(signals) if signal is Signal.LONG)
        gap_at = long_index + 2
        gapped = candles[:gap_at] + candles[gap_at + 1 :]
        self.assertEqual(strategy.generate(gapped)[gap_at], Signal.FLAT)

    def test_non_btc_market_and_non_chronological_input_fail_closed(self) -> None:
        strategy = candidate_factories()["kst_vwap_momentum"]()
        with self.assertRaisesRegex(ValueError, "KRW-BTC"):
            strategy.generate(_candles(100, market="KRW-ETH"))
        candles = _candles(100)
        candles[40], candles[41] = candles[41], candles[40]
        with self.assertRaisesRegex(ValueError, "chronological"):
            strategy.generate(candles)


if __name__ == "__main__":
    unittest.main()
