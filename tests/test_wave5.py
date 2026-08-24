from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.wave5 import (
    WAVE5_CANDIDATE_NAMES,
    CashStrategy,
    FourHourTrendPullbackStrategy,
    Wave5Config,
    compare_wave5_btc_candidates,
    select_research_candidate,
    wave5_candidate_factories,
    wave5_candidate_manifest,
    wave5_candidate_manifest_hash,
)


def _candles(count: int, *, start: datetime | None = None) -> list[Candle]:
    start = start or datetime(2025, 1, 1, 15, tzinfo=UTC)
    result: list[Candle] = []
    price = 100.0
    for index in range(count):
        cycle = index % 160
        drift = 0.08 if cycle < 120 else -0.12
        price = max(10.0, price + drift)
        result.append(
            Candle(
                timestamp=start + timedelta(minutes=30 * index),
                open=price - drift / 2,
                high=price + 0.5,
                low=price - 0.5,
                close=price,
                volume=10.0 + index % 7,
            )
        )
    return result


class Wave5ContractTests(unittest.TestCase):
    def test_manifest_freezes_research_and_execution_boundaries(self) -> None:
        manifest = wave5_candidate_manifest()
        self.assertEqual(
            [item["name"] for item in manifest["candidate_set"]],
            list(WAVE5_CANDIDATE_NAMES),
        )
        self.assertEqual(manifest["status"], "RESEARCH_ONLY")
        self.assertFalse(manifest["execution"]["allow_pyramiding"])
        self.assertFalse(manifest["execution"]["llm_historical_signal"])
        self.assertFalse(manifest["execution"]["orderbook_historical_signal"])
        self.assertEqual(manifest["promotion"]["automatic_promotion"], "forbidden")
        self.assertEqual(
            wave5_candidate_manifest_hash(),
            "8887fc2e8204f66b747a0a118f8f2148796374f1597b9b9b1ae56cb0afb755e3",
        )

    def test_cross_sectional_candidate_is_declared_but_not_faked_on_btc(self) -> None:
        manifest = wave5_candidate_manifest()
        cross = next(
            item
            for item in manifest["candidate_set"]
            if item["name"] == "cross_sectional_momentum"
        )
        self.assertEqual(
            cross["availability"], "requires_at_least_three_aligned_markets"
        )
        self.assertNotIn("cross_sectional_momentum", wave5_candidate_factories())

    def test_config_requires_exact_expanding_fold_coverage(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly cover"):
            Wave5Config(historical_count=999, train_size=400, test_size=200, fold_count=3)


class Wave5StrategyTests(unittest.TestCase):
    def test_cash_is_always_flat(self) -> None:
        self.assertEqual(CashStrategy().generate(_candles(32)), [Signal.FLAT] * 32)

    def test_mixed_market_input_fails_closed(self) -> None:
        candles = _candles(16)
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
            CashStrategy().generate(candles)

    def test_trend_pullback_is_prefix_stable_long_flat_and_completed_4h_only(self) -> None:
        candles = _candles(1_600)
        strategy = FourHourTrendPullbackStrategy()
        prefix = strategy.generate(candles[:-80])
        extended = strategy.generate(candles)
        self.assertEqual(prefix, extended[: len(prefix)])
        self.assertNotIn(Signal.SHORT, extended)
        changes = [
            index
            for index in range(1, len(extended))
            if extended[index] != extended[index - 1]
        ]
        self.assertTrue(changes)
        self.assertTrue(all(index % 8 == 7 for index in changes))

    def test_gap_resets_carried_position_until_a_complete_bucket(self) -> None:
        candles = _candles(1_600)
        signals = FourHourTrendPullbackStrategy().generate(candles)
        long_index = next(index for index, signal in enumerate(signals) if signal is Signal.LONG)
        gap_at = long_index + 2
        gapped = candles[:gap_at] + candles[gap_at + 1 :]
        gapped_signals = FourHourTrendPullbackStrategy().generate(gapped)
        self.assertEqual(gapped_signals[gap_at], Signal.FLAT)


class Wave5ResearchTests(unittest.TestCase):
    def test_comparison_uses_identical_chronological_folds_and_fails_to_cash(self) -> None:
        config = Wave5Config(
            historical_count=1_200,
            train_size=600,
            test_size=200,
            fold_count=3,
            minimum_positive_folds=2,
            minimum_closed_trades=100,
        )
        candles = _candles(1_200)
        base = compare_wave5_btc_candidates(
            candles, settings=TradingSettings(), config=config
        )
        stress = compare_wave5_btc_candidates(
            candles,
            settings=TradingSettings(fee_rate=0.005, slippage_bps=10),
            config=config,
        )
        self.assertEqual(base.fold_boundaries, config.boundaries())
        self.assertEqual(base.candidate_count, 3)
        selected, gates = select_research_candidate(base, stress, config)
        self.assertEqual(selected, "cash")
        self.assertFalse(gates["cash"]["passed"])


if __name__ == "__main__":
    unittest.main()
