from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.strategy import (
    BollingerRsiReentryStrategy,
    BollingerRsiFourHourUptrendReentryStrategy,
    BollingerRsiUptrendReentryStrategy,
    BollingerSqueezeBreakoutStrategy,
    CompletedIntervalStrategy,
    DCBollingerRsiArmedReentryStrategy,
    DCBollingerRsiParameters,
    MeanReversionParameters,
    SqueezeBreakoutParameters,
    StrategyParameters,
    TrendBreakoutStrategy,
    _completed_daily_regime,
    _completed_four_hour_uptrend,
)


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


class CandidateStrategyTests(unittest.TestCase):
    def test_completed_hour_signal_maps_at_second_30m_close_and_is_prefix_stable(self) -> None:
        class HourlyState:
            name = "hourly_state"

            def generate(self, candles: list[Candle]) -> list[Signal]:
                return [
                    Signal.FLAT if index % 2 == 0 else Signal.LONG
                    for index in range(len(candles))
                ]

        start = datetime(2024, 1, 1, 15, tzinfo=UTC)  # KST midnight
        raw = [
            Candle(start + timedelta(minutes=30 * index), 100, 100, 100, 100, 1)
            for index in range(8)
        ]
        wrapper = CompletedIntervalStrategy(HourlyState())
        partial = wrapper.generate(raw[:5])
        prefix = wrapper.generate(raw[:6])
        extended = wrapper.generate(raw)
        self.assertEqual(
            prefix,
            [
                Signal.FLAT,
                Signal.FLAT,
                Signal.FLAT,
                Signal.LONG,
                Signal.LONG,
                Signal.FLAT,
            ],
        )
        self.assertEqual(extended[:5], partial)
        self.assertEqual(extended[:6], prefix)

    def test_dc_setup_must_arm_before_reentry_and_uses_five_percent_exit(self) -> None:
        candles = candles_from_closes([100, 80, 95, 105])
        candles[3] = Candle(
            candles[3].timestamp, 110, 111, 104, 105, 1
        )
        bands = ([90.0] * 4, [110.0] * 4, [90.0] * 4)
        with (
            patch("bithumb_coin_trader.strategy.bollinger_bands", return_value=bands),
            patch("bithumb_coin_trader.strategy.wilder_rsi", return_value=[40, 10, 40, 40]),
            patch("bithumb_coin_trader.strategy._completed_daily_regime", return_value=[False] * 4),
        ):
            signals = DCBollingerRsiArmedReentryStrategy().generate(candles)
        self.assertEqual(signals, [Signal.FLAT, Signal.FLAT, Signal.LONG, Signal.LONG])
        self.assertNotIn(Signal.SHORT, signals)

    def test_dc_bearish_daily_regime_uses_stricter_rsi_20_threshold(self) -> None:
        candles = candles_from_closes([100, 80, 95])
        bands = ([90.0] * 3, [110.0] * 3, [90.0] * 3)
        rsi = [40.0, 25.0, 40.0]
        with (
            patch("bithumb_coin_trader.strategy.bollinger_bands", return_value=bands),
            patch("bithumb_coin_trader.strategy.wilder_rsi", return_value=rsi),
            patch("bithumb_coin_trader.strategy._completed_daily_regime", return_value=[False] * 3),
        ):
            normal = DCBollingerRsiArmedReentryStrategy().generate(candles)
        with (
            patch("bithumb_coin_trader.strategy.bollinger_bands", return_value=bands),
            patch("bithumb_coin_trader.strategy.wilder_rsi", return_value=rsi),
            patch("bithumb_coin_trader.strategy._completed_daily_regime", return_value=[True] * 3),
        ):
            bearish = DCBollingerRsiArmedReentryStrategy().generate(candles)
        self.assertEqual(normal[-1], Signal.LONG)
        self.assertEqual(bearish[-1], Signal.FLAT)

    def test_daily_regime_uses_only_completed_prior_kst_day(self) -> None:
        start = datetime(2024, 1, 1, 15, tzinfo=UTC)  # KST midnight
        daily_closes = [100.0, 80.0, 60.0, 40.0]
        candles = [
            Candle(
                start + timedelta(minutes=30 * index),
                daily_closes[index // 48],
                daily_closes[index // 48],
                daily_closes[index // 48],
                daily_closes[index // 48],
                1,
            )
            for index in range(48 * len(daily_closes))
        ]
        candles[-1] = Candle(candles[-1].timestamp, 1, 1, 1, 1, 1)
        regimes = _completed_daily_regime(candles, period=3)
        self.assertFalse(regimes[96])
        self.assertTrue(regimes[144])
        self.assertEqual(regimes[144], regimes[-1])

    def test_four_hour_filter_changes_only_after_completed_bucket(self) -> None:
        start = datetime(2024, 1, 1, 15, tzinfo=UTC)  # KST midnight
        closes = [100, 100, 100, 100, 90, 90, 90, 90, 110, 110, 110, 110]
        candles = [
            Candle(start + timedelta(hours=index), value, value, value, value, 1)
            for index, value in enumerate(closes)
        ]
        uptrend = _completed_four_hour_uptrend(candles, period=2)
        self.assertFalse(uptrend[10])
        self.assertTrue(uptrend[11])

    def test_four_hour_candidate_requires_latest_completed_4h_uptrend(self) -> None:
        candles = candles_from_closes([80, 91])
        bands = ([200.0] * 2, [220.0] * 2, [90.0] * 2)
        parameters = MeanReversionParameters(bollinger_period=2, rsi_period=2)
        with (
            patch("bithumb_coin_trader.strategy.bollinger_bands", return_value=bands),
            patch("bithumb_coin_trader.strategy.wilder_rsi", return_value=[20, 31]),
            patch(
                "bithumb_coin_trader.strategy._completed_four_hour_uptrend",
                return_value=[False, False],
            ),
        ):
            blocked = BollingerRsiFourHourUptrendReentryStrategy(parameters).generate(candles)
        with (
            patch("bithumb_coin_trader.strategy.bollinger_bands", return_value=bands),
            patch("bithumb_coin_trader.strategy.wilder_rsi", return_value=[20, 31]),
            patch(
                "bithumb_coin_trader.strategy._completed_four_hour_uptrend",
                return_value=[False, True],
            ),
        ):
            allowed = BollingerRsiFourHourUptrendReentryStrategy(parameters).generate(candles)
        self.assertEqual(blocked[-1], Signal.FLAT)
        self.assertEqual(allowed[-1], Signal.LONG)

    def test_mean_reversion_exits_after_exact_holding_limit(self) -> None:
        candles = candles_from_closes([80, 91, 92, 93])
        bands = ([200.0] * 4, [220.0] * 4, [90.0] * 4)
        parameters = MeanReversionParameters(
            bollinger_period=2, rsi_period=2, maximum_holding_bars=2
        )
        with (
            patch("bithumb_coin_trader.strategy.bollinger_bands", return_value=bands),
            patch("bithumb_coin_trader.strategy.wilder_rsi", return_value=[20, 31, 32, 33]),
        ):
            signals = BollingerRsiReentryStrategy(parameters).generate(candles)
        self.assertEqual(signals, [Signal.FLAT, Signal.LONG, Signal.LONG, Signal.FLAT])

    def test_uptrend_variant_requires_close_above_ema(self) -> None:
        candles = candles_from_closes([80, 91])
        bands = ([200.0] * 2, [220.0] * 2, [90.0] * 2)
        parameters = MeanReversionParameters(bollinger_period=2, rsi_period=2, trend_ema_period=2)
        with (
            patch("bithumb_coin_trader.strategy.bollinger_bands", return_value=bands),
            patch("bithumb_coin_trader.strategy.wilder_rsi", return_value=[20, 31]),
            patch("bithumb_coin_trader.strategy.ema", return_value=[100.0, 100.0]),
        ):
            blocked = BollingerRsiUptrendReentryStrategy(parameters).generate(candles)
        with (
            patch("bithumb_coin_trader.strategy.bollinger_bands", return_value=bands),
            patch("bithumb_coin_trader.strategy.wilder_rsi", return_value=[20, 31]),
            patch("bithumb_coin_trader.strategy.ema", return_value=[70.0, 70.0]),
        ):
            allowed = BollingerRsiUptrendReentryStrategy(parameters).generate(candles)
        self.assertEqual(blocked[-1], Signal.FLAT)
        self.assertEqual(allowed[-1], Signal.LONG)

    def test_squeeze_arms_then_enters_on_upper_band_cross(self) -> None:
        candles = candles_from_closes([100, 100, 110])
        bands = ([100.0] * 3, [101.0, 101.0, 105.0], [91.0, 99.0, 101.0])
        parameters = SqueezeBreakoutParameters(
            bollinger_period=2, bandwidth_lookback=2, squeeze_quantile=0.5
        )
        with (
            patch("bithumb_coin_trader.strategy.bollinger_bands", return_value=bands),
            patch(
                "bithumb_coin_trader.strategy.bollinger_bandwidth",
                return_value=[0.10, 0.02, 0.01],
            ),
        ):
            signals = BollingerSqueezeBreakoutStrategy(parameters).generate(candles)
        self.assertEqual(signals, [Signal.FLAT, Signal.FLAT, Signal.LONG])
        self.assertNotIn(Signal.SHORT, signals)

    def test_old_squeeze_does_not_arm_a_later_non_squeeze_breakout(self) -> None:
        candles = candles_from_closes([100, 100, 110])
        bands = ([100.0] * 3, [101.0, 101.0, 105.0], [91.0, 99.0, 101.0])
        parameters = SqueezeBreakoutParameters(
            bollinger_period=2, bandwidth_lookback=2, squeeze_quantile=0.5
        )
        with (
            patch("bithumb_coin_trader.strategy.bollinger_bands", return_value=bands),
            patch(
                "bithumb_coin_trader.strategy.bollinger_bandwidth",
                return_value=[0.10, 0.01, 0.05],
            ),
        ):
            signals = BollingerSqueezeBreakoutStrategy(parameters).generate(candles)
        self.assertEqual(signals[-1], Signal.FLAT)

    def test_candidate_parameters_fail_closed_on_invalid_values(self) -> None:
        invalid_factories = (
            lambda: DCBollingerRsiParameters(bollinger_period=1),
            lambda: DCBollingerRsiParameters(bearish_rsi_threshold=40, normal_rsi_threshold=35),
            lambda: DCBollingerRsiParameters(take_profit_fraction=1),
            lambda: MeanReversionParameters(rsi_threshold=float("nan")),
            lambda: MeanReversionParameters(maximum_holding_bars=0),
            lambda: SqueezeBreakoutParameters(bandwidth_lookback=1),
            lambda: SqueezeBreakoutParameters(squeeze_quantile=0),
            lambda: CompletedIntervalStrategy(object(), source_minutes=60, target_minutes=30),
        )
        for factory in invalid_factories:
            with self.subTest(factory=factory), self.assertRaises(ValueError):
                factory()


if __name__ == "__main__":
    unittest.main()
