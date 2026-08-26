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
    CompletedCalendarMonthStrategy,
    CompletedIntervalStrategy,
    DCBollingerRsiArmedReentryStrategy,
    DCBollingerRsiParameters,
    DailyCloseAboveSmaParameters,
    DailyCloseAboveSmaStrategy,
    DailyMacdPvoTrendParameters,
    DailyMacdPvoTrendStrategy,
    DailySmaAdxTrendParameters,
    DailySmaAdxTrendStrategy,
    DailySmaTrendParameters,
    DailySmaTrendStrategy,
    DonchianBreakoutParameters,
    DonchianBreakoutStrategy,
    IntersectionLongStrategy,
    MajorityVoteLongStrategy,
    MeanReversionParameters,
    SqueezeBreakoutParameters,
    StrategyParameters,
    TimeSeriesMomentumParameters,
    TimeSeriesMomentumStrategy,
    TradingRangeBreakoutParameters,
    TradingRangeBreakoutStrategy,
    TrendBreakoutStrategy,
    _completed_daily_regime,
    _completed_four_hour_uptrend,
    daily_close_above_sma140_strategy,
    daily_close_above_sma200_strategy,
    daily_sma50_above_sma200_strategy,
    daily_tsmom_365_strategy,
    dc_with_4h_sma50_uptrend_strategy,
    dc_with_daily_sma140_uptrend_strategy,
    donchian_4h_20_10_strategy,
    donchian_4h_55_20_strategy,
    donchian_daily_20_10_strategy,
    donchian_daily_55_20_strategy,
    ensemble_daily_3_of_5_strategy,
    monthly_close_above_sma10_strategy,
    trading_range_daily_50_band_1pct_strategy,
    trading_range_daily_50_no_band_strategy,
    trend_daily_macd12_26_9_pvo12_26_strategy,
    trend_daily_sma50_200_adx14_25_strategy,
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
    @staticmethod
    def _thirty_minute_days(closes: list[float]) -> list[Candle]:
        start = datetime(2024, 1, 1, 15, tzinfo=UTC)  # KST midnight
        return [
            Candle(
                start + timedelta(minutes=30 * index),
                value,
                value,
                value,
                value,
                1,
            )
            for index in range(48 * len(closes))
            for value in [closes[index // 48]]
        ]

    @staticmethod
    def _thirty_minute_four_hour_bars(
        bars: list[tuple[float, float, float]],
    ) -> list[Candle]:
        start = datetime(2024, 1, 1, 15, tzinfo=UTC)  # KST midnight
        candles: list[Candle] = []
        for bar_index, (high, low, close) in enumerate(bars):
            for source_index in range(8):
                value = close if source_index == 7 else (high + low) / 2
                candles.append(
                    Candle(
                        start + timedelta(minutes=30 * (bar_index * 8 + source_index)),
                        value,
                        high,
                        low,
                        value,
                        1,
                    )
                )
        return candles

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

    def test_fixed_trend_candidate_factories_expose_registry_names(self) -> None:
        candidates = (
            daily_close_above_sma140_strategy(),
            daily_close_above_sma200_strategy(),
            daily_sma50_above_sma200_strategy(),
            donchian_4h_55_20_strategy(),
            donchian_4h_20_10_strategy(),
        )
        self.assertEqual(
            [candidate.name for candidate in candidates],
            [
                "trend_daily_close_above_sma140",
                "trend_daily_close_above_sma200",
                "trend_daily_sma50_above_sma200",
                "donchian_4h_55_20_breakout",
                "donchian_4h_20_10_breakout",
            ],
        )

    def test_second_wave_factories_expose_exact_names_and_intervals(self) -> None:
        candidates = (
            daily_tsmom_365_strategy(),
            monthly_close_above_sma10_strategy(),
            donchian_daily_55_20_strategy(),
            donchian_daily_20_10_strategy(),
        )
        self.assertEqual(
            [candidate.name for candidate in candidates],
            [
                "trend_daily_tsmom_365",
                "trend_monthly_close_above_sma10",
                "donchian_daily_55_20_breakout",
                "donchian_daily_20_10_breakout",
            ],
        )
        self.assertEqual(
            [
                candidate.target_minutes
                for candidate in candidates
                if isinstance(candidate, CompletedIntervalStrategy)
            ],
            [1440, 1440, 1440],
        )

    def test_dc_trend_filter_factories_are_long_flat_intersections(self) -> None:
        candidates = (
            dc_with_4h_sma50_uptrend_strategy(),
            dc_with_daily_sma140_uptrend_strategy(),
        )
        self.assertEqual(
            [candidate.name for candidate in candidates],
            [
                "dc_30m_bb20_rsi14_with_4h_sma50_uptrend",
                "dc_30m_bb20_rsi14_with_daily_sma140_uptrend",
            ],
        )

        class Fixed:
            def __init__(self, signals: list[Signal]) -> None:
                self.signals = signals

            def generate(self, candles: list[Candle]) -> list[Signal]:
                return self.signals

        candles = candles_from_closes([100, 101, 102])
        intersection = IntersectionLongStrategy(
            Fixed([Signal.LONG, Signal.LONG, Signal.FLAT]),
            Fixed([Signal.FLAT, Signal.LONG, Signal.LONG]),
            name="test_intersection",
        )
        self.assertEqual(
            intersection.generate(candles),
            [Signal.FLAT, Signal.LONG, Signal.FLAT],
        )

    def test_third_wave_factories_expose_exact_names_and_one_daily_wrapper(self) -> None:
        candidates = (
            trading_range_daily_50_band_1pct_strategy(),
            trading_range_daily_50_no_band_strategy(),
            trend_daily_sma50_200_adx14_25_strategy(),
            trend_daily_macd12_26_9_pvo12_26_strategy(),
            ensemble_daily_3_of_5_strategy(),
        )
        self.assertEqual(
            [candidate.name for candidate in candidates],
            [
                "trading_range_daily_50_band_1pct",
                "trading_range_daily_50_no_band",
                "trend_daily_sma50_200_adx14_25",
                "trend_daily_macd12_26_9_pvo12_26",
                "ensemble_daily_3_of_5",
            ],
        )
        self.assertTrue(
            all(candidate.target_minutes == 1440 for candidate in candidates)
        )
        ensemble = candidates[-1]
        self.assertIsInstance(ensemble.inner, MajorityVoteLongStrategy)
        self.assertFalse(
            any(
                isinstance(strategy, CompletedIntervalStrategy)
                for strategy in ensemble.inner.strategies
            )
        )

    def test_majority_vote_requires_three_long_constituents(self) -> None:
        class Fixed:
            def __init__(self, signals: list[Signal]) -> None:
                self.signals = signals

            def generate(self, candles: list[Candle]) -> list[Signal]:
                return self.signals

        candles = candles_from_closes([100, 101])
        vote = MajorityVoteLongStrategy(
            Fixed([Signal.LONG, Signal.LONG]),
            Fixed([Signal.LONG, Signal.FLAT]),
            Fixed([Signal.LONG, Signal.LONG]),
            Fixed([Signal.FLAT, Signal.FLAT]),
            Fixed([Signal.FLAT, Signal.LONG]),
            minimum_votes=3,
            name="three_of_five",
        )
        self.assertEqual(vote.generate(candles), [Signal.LONG, Signal.LONG])

    def test_trading_range_uses_prior_50_bars_and_is_stateful(self) -> None:
        candles = candles_from_closes([100.0] * 50 + [103.0, 97.0])
        strategy = TradingRangeBreakoutStrategy()
        prefix = strategy.generate(candles[:51])
        extended = strategy.generate(candles)
        self.assertEqual(prefix[-1], Signal.LONG)
        self.assertEqual(extended[:51], prefix)
        self.assertEqual(extended[-1], Signal.FLAT)

    def test_trading_range_current_bar_is_excluded_from_channel(self) -> None:
        candles = candles_from_closes([100.0] * 50 + [102.1])
        current = candles[-1]
        candles[-1] = Candle(
            current.timestamp,
            current.open,
            200.0,
            current.low,
            current.close,
            current.volume,
        )
        signals = TradingRangeBreakoutStrategy().generate(candles)
        self.assertEqual(signals[-1], Signal.LONG)

    def test_trading_range_daily_signal_maps_only_at_completed_close(self) -> None:
        raw = self._thirty_minute_days([100.0] * 50 + [102.0, 98.0])
        strategy = trading_range_daily_50_band_1pct_strategy()
        before_entry = strategy.generate(raw[: 48 * 51 - 1])
        at_entry = strategy.generate(raw[: 48 * 51])
        extended = strategy.generate(raw)
        self.assertEqual(before_entry[-1], Signal.FLAT)
        self.assertEqual(at_entry[-2:], [Signal.FLAT, Signal.LONG])
        self.assertEqual(extended[: 48 * 51], at_entry)
        self.assertEqual(extended[-1], Signal.FLAT)

    def test_daily_sma_adx_requires_trend_direction_and_strict_strength(self) -> None:
        candles = candles_from_closes([100.0] * 199 + [101.0, 102.0])
        length = len(candles)
        with patch(
            "bithumb_coin_trader.strategy.directional_indicators",
            return_value=(
                [None] * (length - 1) + [30.0],
                [None] * (length - 1) + [10.0],
                [None] * (length - 1) + [25.0],
            ),
        ):
            blocked = DailySmaAdxTrendStrategy().generate(candles)
        with patch(
            "bithumb_coin_trader.strategy.directional_indicators",
            return_value=(
                [None] * (length - 1) + [30.0],
                [None] * (length - 1) + [10.0],
                [None] * (length - 1) + [25.01],
            ),
        ):
            allowed = DailySmaAdxTrendStrategy().generate(candles)
        self.assertEqual(blocked[-1], Signal.FLAT)
        self.assertEqual(allowed[-1], Signal.LONG)

    def test_daily_macd_pvo_requires_positive_momentum_and_volume(self) -> None:
        candles = candles_from_closes([100.0, 101.0, 102.0])
        line = [None, None, 1.0]
        signal = [None, None, 0.5]
        with (
            patch(
                "bithumb_coin_trader.strategy.macd",
                return_value=(line, signal, [None, None, 0.5]),
            ),
            patch(
                "bithumb_coin_trader.strategy.percentage_volume_oscillator",
                return_value=[None, None, 0.1],
            ),
        ):
            allowed = DailyMacdPvoTrendStrategy().generate(candles)
        with (
            patch(
                "bithumb_coin_trader.strategy.macd",
                return_value=(line, signal, [None, None, 0.5]),
            ),
            patch(
                "bithumb_coin_trader.strategy.percentage_volume_oscillator",
                return_value=[None, None, 0.0],
            ),
        ):
            blocked = DailyMacdPvoTrendStrategy().generate(candles)
        self.assertEqual(allowed[-1], Signal.LONG)
        self.assertEqual(blocked[-1], Signal.FLAT)

    def test_daily_close_sma_signal_appears_only_at_completed_kst_day(self) -> None:
        raw = self._thirty_minute_days([100, 110, 90])
        strategy = CompletedIntervalStrategy(
            DailyCloseAboveSmaStrategy(DailyCloseAboveSmaParameters(2)),
            source_minutes=30,
            target_minutes=1440,
        )
        before_close = strategy.generate(raw[:95])
        at_close = strategy.generate(raw[:96])
        extended = strategy.generate(raw)
        self.assertEqual(before_close[-1], Signal.FLAT)
        self.assertEqual(at_close[-2:], [Signal.FLAT, Signal.LONG])
        self.assertEqual(extended[:96], at_close)
        self.assertEqual(extended[-1], Signal.FLAT)

    def test_daily_sma_cross_uses_only_completed_days_and_is_prefix_stable(self) -> None:
        raw = self._thirty_minute_days([100, 90, 120, 50])
        strategy = CompletedIntervalStrategy(
            DailySmaTrendStrategy(DailySmaTrendParameters(2, 3)),
            source_minutes=30,
            target_minutes=1440,
        )
        at_entry = strategy.generate(raw[:144])
        extended = strategy.generate(raw)
        self.assertEqual(at_entry[-2:], [Signal.FLAT, Signal.LONG])
        self.assertEqual(extended[:144], at_entry)
        self.assertEqual(extended[-1], Signal.FLAT)

    def test_donchian_entry_and_exit_map_at_completed_four_hour_close(self) -> None:
        raw = self._thirty_minute_four_hour_bars(
            [
                (100, 90, 95),
                (101, 91, 96),
                (110, 100, 105),
                (106, 85, 89),
            ]
        )
        strategy = CompletedIntervalStrategy(
            DonchianBreakoutStrategy(DonchianBreakoutParameters(2, 1)),
            source_minutes=30,
            target_minutes=240,
        )
        before_entry = strategy.generate(raw[:23])
        at_entry = strategy.generate(raw[:24])
        extended = strategy.generate(raw)
        self.assertEqual(before_entry[-1], Signal.FLAT)
        self.assertEqual(at_entry[-2:], [Signal.FLAT, Signal.LONG])
        self.assertEqual(extended[:24], at_entry)
        self.assertEqual(extended[-2:], [Signal.LONG, Signal.FLAT])

    def test_donchian_current_bar_does_not_contaminate_prior_channel(self) -> None:
        bars = [
            Candle(
                datetime(2024, 1, 1, index * 4, tzinfo=UTC),
                close,
                high,
                low,
                close,
                1,
            )
            for index, (high, low, close) in enumerate(
                [(100, 90, 95), (101, 91, 96), (110, 100, 105)]
            )
        ]
        signals = DonchianBreakoutStrategy(DonchianBreakoutParameters(2, 1)).generate(bars)
        self.assertEqual(signals, [Signal.FLAT, Signal.FLAT, Signal.LONG])

    def test_completed_interval_restarts_inner_history_after_source_gap(self) -> None:
        first = self._thirty_minute_four_hour_bars(
            [(100, 90, 95), (101, 91, 96), (110, 100, 105)]
        )
        second = self._thirty_minute_four_hour_bars(
            [(120, 110, 115), (130, 120, 125)]
        )
        offset = timedelta(minutes=30 * len(first) + 240)
        second = [
            Candle(
                candle.timestamp + offset,
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
            )
            for candle in second
        ]
        signals = CompletedIntervalStrategy(
            DonchianBreakoutStrategy(DonchianBreakoutParameters(2, 1)),
            source_minutes=30,
            target_minutes=240,
        ).generate(first + second)
        self.assertEqual(signals[len(first) - 1], Signal.LONG)
        self.assertEqual(signals[len(first) :], [Signal.FLAT] * len(second))

    def test_daily_tsmom_changes_only_at_completed_day_and_is_prefix_stable(self) -> None:
        raw = self._thirty_minute_days([100, 90, 110, 80])
        strategy = CompletedIntervalStrategy(
            TimeSeriesMomentumStrategy(TimeSeriesMomentumParameters(2)),
            source_minutes=30,
            target_minutes=1440,
        )
        before_entry = strategy.generate(raw[:143])
        at_entry = strategy.generate(raw[:144])
        extended = strategy.generate(raw)
        self.assertEqual(before_entry[-1], Signal.FLAT)
        self.assertEqual(at_entry[-2:], [Signal.FLAT, Signal.LONG])
        self.assertEqual(extended[:144], at_entry)
        self.assertEqual(extended[-1], Signal.FLAT)

    def test_monthly_sma_changes_only_after_complete_kst_calendar_month(self) -> None:
        start = datetime(2023, 12, 31, 15, tzinfo=UTC)  # 2024-01-01 KST
        daily_closes = [100.0] * 31 + [110.0] * 29
        raw = [
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
        strategy = CompletedCalendarMonthStrategy(
            DailyCloseAboveSmaStrategy(DailyCloseAboveSmaParameters(2))
        )
        before_month_end = strategy.generate(raw[:-1])
        at_month_end = strategy.generate(raw)
        partial_march = raw + [
            Candle(raw[-1].timestamp + timedelta(minutes=30), 50, 50, 50, 50, 1)
        ]
        extended = strategy.generate(partial_march)
        self.assertEqual(before_month_end[-1], Signal.FLAT)
        self.assertEqual(at_month_end[-2:], [Signal.FLAT, Signal.LONG])
        self.assertEqual(extended[: len(raw)], at_month_end)
        self.assertEqual(extended[-1], Signal.LONG)

    def test_monthly_close_survives_intramonth_data_gap_when_boundary_closes_exist(self) -> None:
        start = datetime(2023, 12, 31, 15, tzinfo=UTC)
        raw = [
            Candle(start + timedelta(minutes=30 * index), 100, 100, 100, 100, 1)
            for index in range(48 * 31)
            if index != 500
        ]
        strategy = CompletedCalendarMonthStrategy(
            DailyCloseAboveSmaStrategy(DailyCloseAboveSmaParameters(2))
        )
        signals = strategy.generate(raw)
        self.assertEqual(len(signals), len(raw))

    def test_daily_donchian_entry_exit_and_prefix_timing(self) -> None:
        raw = self._thirty_minute_days([100, 101, 110, 90])
        strategy = CompletedIntervalStrategy(
            DonchianBreakoutStrategy(DonchianBreakoutParameters(2, 1)),
            source_minutes=30,
            target_minutes=1440,
        )
        before_entry = strategy.generate(raw[:143])
        at_entry = strategy.generate(raw[:144])
        extended = strategy.generate(raw)
        self.assertEqual(before_entry[-1], Signal.FLAT)
        self.assertEqual(at_entry[-2:], [Signal.FLAT, Signal.LONG])
        self.assertEqual(extended[:144], at_entry)
        self.assertEqual(extended[-2:], [Signal.LONG, Signal.FLAT])

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
            lambda: DailyCloseAboveSmaParameters(sma_period=True),
            lambda: DailyCloseAboveSmaParameters(sma_period=1),
            lambda: DailySmaTrendParameters(fast_period=50, slow_period=50),
            lambda: DailySmaTrendParameters(fast_period=1, slow_period=200),
            lambda: DonchianBreakoutParameters(entry_period=0),
            lambda: DonchianBreakoutParameters(entry_period=20, exit_period=20),
            lambda: TimeSeriesMomentumParameters(lookback_period=False),
            lambda: TimeSeriesMomentumParameters(lookback_period=0),
            lambda: TradingRangeBreakoutParameters(lookback_period=0),
            lambda: TradingRangeBreakoutParameters(entry_band_fraction=-0.01),
            lambda: TradingRangeBreakoutParameters(exit_band_fraction=float("nan")),
            lambda: DailySmaAdxTrendParameters(fast_period=200, slow_period=50),
            lambda: DailySmaAdxTrendParameters(directional_period=True),
            lambda: DailySmaAdxTrendParameters(adx_threshold=101),
            lambda: DailyMacdPvoTrendParameters(
                macd_fast_period=26, macd_slow_period=26
            ),
            lambda: DailyMacdPvoTrendParameters(
                pvo_fast_period=26, pvo_slow_period=12
            ),
            lambda: CompletedCalendarMonthStrategy(object()),
            lambda: CompletedCalendarMonthStrategy(object(), source_minutes=60),
            lambda: IntersectionLongStrategy(object(), name="bad"),
            lambda: MajorityVoteLongStrategy(
                TimeSeriesMomentumStrategy(), minimum_votes=2, name="bad"
            ),
            lambda: MajorityVoteLongStrategy(
                object(), minimum_votes=1, name="bad"
            ),
            lambda: CompletedIntervalStrategy(object(), source_minutes=60, target_minutes=30),
        )
        for factory in invalid_factories:
            with self.subTest(factory=factory), self.assertRaises(ValueError):
                factory()


if __name__ == "__main__":
    unittest.main()
