from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.wave4 import (
    WAVE4_CANDIDATE_NAMES,
    DailyTsmom84Rv20MedianGateStrategy,
    DailyTsmom84Strategy,
    VolumeClockFirstLastMomentumStrategy,
    Wave4NestedConfig,
    compare_wave4_candidates,
    fit_volume_clock,
    run_wave4_nested_research,
    _run_train_aware_candidate,
    wave4_candidate_builders,
    wave4_candidate_manifest,
    wave4_candidate_manifest_hash,
)


def _candles(
    days: int,
    *,
    start: datetime = datetime(2025, 1, 1, 15, tzinfo=UTC),
    high_volume_slot: int = 5,
) -> list[Candle]:
    result: list[Candle] = []
    for index in range(days * 48):
        price = 100.0 + index * 0.02
        slot = index % 48
        result.append(
            Candle(
                timestamp=start + timedelta(minutes=30 * index),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price + 0.1,
                volume=100.0 if slot == high_volume_slot else 1.0,
            )
        )
    return result


class Wave4StrategyTests(unittest.TestCase):
    def test_frozen_manifest_and_public_builder_surface(self) -> None:
        self.assertEqual(tuple(wave4_candidate_builders()), WAVE4_CANDIDATE_NAMES)
        self.assertEqual(wave4_candidate_manifest()["status"], "RESEARCH_ONLY")
        self.assertEqual(Wave4NestedConfig.__name__, "NestedWalkForwardConfig")
        self.assertEqual(
            wave4_candidate_manifest_hash(),
            "1b36e392930c8bc29682442ac7a9e200c741f4cc13264d7b087ac59afe99874b",
        )

    def test_daily_tsmom_is_prefix_stable_long_flat_only_and_next_open_eligible(self) -> None:
        raw = _candles(87)
        strategy = DailyTsmom84Strategy()
        prefix = strategy.generate(raw[:-48])
        extended = strategy.generate(raw)
        self.assertEqual(prefix, extended[: len(prefix)])
        self.assertNotIn(Signal.SHORT, extended)
        # Day 84 completes at its last source candle; the signal can only fill
        # on the next source open, never at that close.
        self.assertEqual(extended[84 * 48 + 47], Signal.LONG)

    def test_volatility_gate_is_prefix_stable_and_never_short(self) -> None:
        raw = _candles(275)
        strategy = DailyTsmom84Rv20MedianGateStrategy()
        prefix = strategy.generate(raw[:-48])
        extended = strategy.generate(raw)
        self.assertEqual(prefix, extended[: len(prefix)])
        self.assertNotIn(Signal.SHORT, extended)

    def test_gap_resets_daily_carry_until_a_later_complete_daily_close(self) -> None:
        raw = _candles(88)
        # Delete one 30m candle after the daily momentum is already long.
        gap_at = 86 * 48
        gapped = raw[:gap_at] + raw[gap_at + 1 :]
        signals = DailyTsmom84Strategy().generate(gapped)
        self.assertEqual(signals[gap_at], Signal.FLAT)
        self.assertEqual(signals[gap_at + 10], Signal.FLAT)

    def test_wrong_source_cadence_fails_closed(self) -> None:
        raw = _candles(1)
        wrong = [
            Candle(
                timestamp=raw[0].timestamp + timedelta(minutes=15 * index),
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1,
            )
            for index in range(4)
        ]
        with self.assertRaisesRegex(ValueError, "30-minute cadence"):
            DailyTsmom84Strategy().generate(wrong)


class VolumeClockTests(unittest.TestCase):
    def test_train_fitted_anchor_is_unchanged_by_test_volume_mutation(self) -> None:
        train = _candles(3, high_volume_slot=7)
        test = _candles(2, start=train[-1].timestamp + timedelta(minutes=30), high_volume_slot=7)
        changed_test = [
            Candle(
                timestamp=candle.timestamp,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=999_999 if index % 48 == 25 else candle.volume,
            )
            for index, candle in enumerate(test)
        ]
        fitted = fit_volume_clock(train)
        self.assertEqual(fitted.anchor_slot, 7)
        self.assertEqual(fit_volume_clock(train).anchor_slot, fitted.anchor_slot)
        self.assertEqual(
            fitted.generate((*train, *test)),
            fitted.generate((*train, *changed_test)),
        )

    def test_anchor_plus_47_open_has_exactly_one_bar_exposure(self) -> None:
        raw = _candles(2, high_volume_slot=0)
        first = raw[0]
        raw[0] = Candle(
            timestamp=first.timestamp,
            open=100,
            high=102,
            low=99,
            close=101,
            volume=first.volume,
        )
        signals = VolumeClockFirstLastMomentumStrategy(anchor_slot=0).generate(raw)
        self.assertTrue(all(signal is Signal.FLAT for signal in signals[:46]))
        self.assertEqual(signals[46], Signal.LONG)
        self.assertEqual(signals[47], Signal.FLAT)
        self.assertNotIn(Signal.SHORT, signals)

    def test_gap_crossing_cycle_is_skipped(self) -> None:
        raw = _candles(2, high_volume_slot=0)
        gap_index = 20
        gapped = raw[:gap_index] + raw[gap_index + 1 :]
        signals = VolumeClockFirstLastMomentumStrategy(anchor_slot=0).generate(gapped)
        self.assertEqual(signals[46], Signal.FLAT)


class Wave4ResearchTests(unittest.TestCase):
    @staticmethod
    def _small_config() -> Wave4NestedConfig:
        return Wave4NestedConfig(
            historical_count=1_000,
            outer_train_size=400,
            outer_test_size=300,
            outer_fold_count=2,
            inner_initial_train_size=100,
            inner_test_size=100,
            inner_fold_count=3,
            minimum_profitable_stress_folds=1,
        )

    def test_train_aware_comparison_and_nested_runner_are_fold_safe(self) -> None:
        raw = _candles(21)
        config = self._small_config()
        comparison = compare_wave4_candidates(
            raw, settings=TradingSettings(), config=config
        )
        self.assertEqual(comparison.candidate_count, 3)
        nested = run_wave4_nested_research(
            raw,
            base_settings=TradingSettings(),
            stress_settings=TradingSettings(fee_rate=0.005, slippage_bps=10),
            config=config,
        )
        self.assertEqual(len(nested.decisions), 2)
        self.assertNotIn(
            Signal.SHORT,
            [
                signal
                for decision in nested.decisions
                for score in decision.candidate_scores
                for signal in ()
            ],
        )

    def test_persistent_long_has_no_synthetic_outer_fold_liquidation(self) -> None:
        class AlwaysLong:
            name = "always_long"

            def generate(self, candles):
                return [Signal.LONG] * len(candles)

        report = _run_train_aware_candidate(
            _candles(18),
            candidate_name="always_long",
            builder=lambda train: AlwaysLong(),
            train_size=400,
            test_size=200,
            settings=TradingSettings(),
            expanding=True,
        )
        self.assertEqual(len(report.folds), 2)
        self.assertEqual(report.folds[0].result.trades, ())
        self.assertEqual(report.trade_count, 1)
        self.assertTrue(report.folds[-1].result.trades[0].is_final_liquidation)
