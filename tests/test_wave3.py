from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.research import ResearchError
from bithumb_coin_trader.wave3 import (
    WAVE3_CANDIDATE_NAMES,
    CandidateInnerScore,
    NestedWalkForwardConfig,
    SelectionDecision,
    assert_wave3_cost_settings_match_manifest,
    assert_wave3_candidate_factories_match_manifest,
    build_nested_selections,
    deterministic_daily_moving_block_bootstrap,
    execute_nested_outer_oos,
    project_report_as_dict,
    select_nested_candidate,
    wave3_candidate_factories,
    wave3_candidate_manifest,
    wave3_candidate_manifest_hash,
)


class _AlwaysLong:
    def generate(self, candles):
        return [Signal.LONG] * len(candles)


class _AlwaysFlat:
    def generate(self, candles):
        return [Signal.FLAT] * len(candles)


def _candles(count: int, *, start_price: float = 100.0) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(
            market="KRW-BTC",
            timestamp=start + timedelta(minutes=30 * index),
            open=start_price + index * 4,
            high=start_price + index * 4 + 3,
            low=start_price + index * 4 - 1,
            close=start_price + index * 4 + 2,
            volume=10 + index,
        )
        for index in range(count)
    ]


def _small_config(*, outer_folds: int = 2) -> NestedWalkForwardConfig:
    return NestedWalkForwardConfig(
        historical_count=8 + 3 * outer_folds,
        outer_train_size=8,
        outer_test_size=3,
        outer_fold_count=outer_folds,
        inner_initial_train_size=4,
        inner_test_size=2,
        inner_fold_count=2,
        minimum_profitable_stress_folds=1,
    )


class Wave3ResearchTests(unittest.TestCase):
    def test_cost_settings_fail_closed_on_manifest_drift(self) -> None:
        assert_wave3_cost_settings_match_manifest(
            TradingSettings(),
            TradingSettings(fee_rate=0.005, slippage_bps=10),
        )
        with self.assertRaisesRegex(Exception, "runtime cost settings differ"):
            assert_wave3_cost_settings_match_manifest(
                TradingSettings(fee_rate=0.001),
                TradingSettings(fee_rate=0.005, slippage_bps=10),
            )

    def test_frozen_candidate_set_and_manifest_hash(self) -> None:
        self.assertEqual(tuple(wave3_candidate_factories()), WAVE3_CANDIDATE_NAMES)
        self.assertEqual(
            wave3_candidate_manifest_hash(),
            "41afcddf791ced95f6e92751e45d8f71dacd94083d1ea5c516001407d179674a",
        )

    def test_manifest_fails_closed_on_runtime_definition_drift(self) -> None:
        manifest = wave3_candidate_manifest()
        self.assertEqual(manifest["schema_version"], 3)
        ensemble = manifest["candidates"][-1]
        self.assertEqual(
            [item["strategy_name"] for item in ensemble["constituents"]],
            [
                "tsmom_365",
                "trading_range_50_100bps",
                "daily_sma50_above_sma200",
                "daily_sma50_200_adx14_25",
                "daily_macd12_26_9_pvo12_26",
            ],
        )
        manifest["candidates"][0]["parameters"]["exit_band_fraction"] = 0.0
        with self.assertRaisesRegex(
            ResearchError, "runtime candidate definitions differ from the manifest"
        ):
            assert_wave3_candidate_factories_match_manifest(manifest=manifest)

    def test_nested_geometry_uses_expanding_outer_and_inner_history(self) -> None:
        config = NestedWalkForwardConfig()
        self.assertEqual(
            config.inner_boundaries(),
            (
                (0, 12_000, 12_000, 13_200),
                (0, 13_200, 13_200, 14_400),
                (0, 14_400, 14_400, 15_600),
                (0, 15_600, 15_600, 16_800),
                (0, 16_800, 16_800, 18_000),
                (0, 18_000, 18_000, 19_200),
            ),
        )
        self.assertEqual(
            config.inner_boundaries(21_600),
            (
                (0, 14_400, 14_400, 15_600),
                (0, 15_600, 15_600, 16_800),
                (0, 16_800, 16_800, 18_000),
                (0, 18_000, 18_000, 19_200),
                (0, 19_200, 19_200, 20_400),
                (0, 20_400, 20_400, 21_600),
            ),
        )
        self.assertEqual(config.outer_boundaries()[0], (0, 19_200, 19_200, 21_600))
        self.assertEqual(config.outer_boundaries()[-1], (0, 36_000, 36_000, 38_400))

    def test_cash_fallback_when_no_candidate_qualifies(self) -> None:
        config = _small_config(outer_folds=1)
        decision = select_nested_candidate(
            _candles(8),
            candidate_factories={"flat": _AlwaysFlat},
            config=config,
            base_settings=TradingSettings(),
            stress_settings=TradingSettings(fee_rate=0.005, slippage_bps=10),
            fold=0,
            train_start=0,
            train_end=8,
            test_start=8,
            test_end=11,
        )
        self.assertIsNone(decision.selected_candidate)
        self.assertFalse(decision.candidate_scores[0].qualifies)

    def test_later_outer_fold_anchors_inner_folds_at_full_prefix_end(self) -> None:
        observed_lengths: list[int] = []

        class _RecordingFlat:
            def generate(self, candles):
                observed_lengths.append(len(candles))
                return [Signal.FLAT] * len(candles)

        config = _small_config(outer_folds=2)
        decisions = build_nested_selections(
            _candles(config.historical_count),
            candidate_factories={"flat": _RecordingFlat},
            config=config,
            base_settings=TradingSettings(),
            stress_settings=TradingSettings(fee_rate=0.005, slippage_bps=10),
        )
        self.assertEqual(
            [(item.train_start, item.train_end) for item in decisions],
            [(0, 8), (0, 11)],
        )
        self.assertEqual(observed_lengths, [6, 8, 9, 11])

    def test_outer_test_prices_do_not_affect_its_selection(self) -> None:
        config = _small_config(outer_folds=1)
        original = _candles(11)
        changed = original[:8] + _candles(3, start_price=10_000.0)
        for index, candle in enumerate(changed[8:], start=8):
            changed[index] = Candle(
                market=candle.market,
                timestamp=original[index].timestamp,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
            )
        kwargs = {
            "candidate_factories": {"long": _AlwaysLong, "flat": _AlwaysFlat},
            "config": config,
            "base_settings": TradingSettings(),
            "stress_settings": TradingSettings(fee_rate=0.005, slippage_bps=10),
        }
        first = build_nested_selections(original, **kwargs)[0]
        second = build_nested_selections(changed, **kwargs)[0]
        self.assertEqual(first, second)

    def test_fold_boundary_long_to_cash_pays_one_real_exit(self) -> None:
        config = _small_config()
        score = CandidateInnerScore(
            candidate_name="long",
            base_compounded_return=0.1,
            base_maximum_drawdown=0.0,
            stress_compounded_return=0.05,
            stress_maximum_drawdown=0.0,
            base_fold_returns=(0.04, 0.05),
            stress_fold_returns=(0.02, 0.03),
            profitable_stress_fold_count=2,
            qualifies=True,
        )
        decisions = (
            SelectionDecision(0, 0, 8, 8, 11, "long", (score,)),
            SelectionDecision(1, 0, 11, 11, 14, None, (score,)),
        )
        result = execute_nested_outer_oos(
            _candles(14),
            decisions=decisions,
            candidate_factories={"long": _AlwaysLong},
            config=config,
            base_settings=TradingSettings(),
            stress_settings=TradingSettings(fee_rate=0.005, slippage_bps=10),
        )
        self.assertEqual(result.base.trade_count, 1)
        self.assertFalse(result.base.folds[1].result.trades[0].is_final_liquidation)
        self.assertLess(result.stress.compounded_return, result.base.compounded_return)
        self.assertAlmostEqual(
            result.base.folds[0].result.final_equity,
            result.base.folds[1].result.initial_equity,
        )

    def test_bootstrap_is_deterministic(self) -> None:
        candles = [
            Candle(
                market=candle.market,
                timestamp=candle.timestamp + timedelta(hours=23.5 * index),
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
            )
            for index, candle in enumerate(_candles(20))
        ]
        candidate = [20_000.0 * (1.001**index) for index in range(20)]
        control = [20_000.0 * (1.0005**index) for index in range(20)]
        first = deterministic_daily_moving_block_bootstrap(
            candidate, control, candles, block_days=2, iterations=100, seed=7
        )
        second = deterministic_daily_moving_block_bootstrap(
            candidate, control, candles, block_days=2, iterations=100, seed=7
        )
        self.assertEqual(first, second)
        self.assertGreater(first.point_estimate, 0)

    def test_serializer_keeps_recomputable_accounting(self) -> None:
        config = _small_config()
        decisions = (
            SelectionDecision(0, 0, 8, 8, 11, None, ()),
            SelectionDecision(1, 0, 11, 11, 14, None, ()),
        )
        result = execute_nested_outer_oos(
            _candles(14),
            decisions=decisions,
            candidate_factories={"flat": _AlwaysFlat},
            config=config,
            base_settings=TradingSettings(),
            stress_settings=TradingSettings(fee_rate=0.005, slippage_bps=10),
        )
        payload = project_report_as_dict(result.base)
        self.assertEqual(payload["profitable_folds"], 0)
        self.assertEqual(payload["folds"][0]["train"], [0, 8])
        self.assertIn("initial_equity_krw", payload["folds"][0])
        self.assertIn("final_equity_krw", payload["folds"][0])
        self.assertIn("exposure", payload["folds"][0])


if __name__ == "__main__":
    unittest.main()
