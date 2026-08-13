from __future__ import annotations

import math
import unittest

from bithumb_coin_trader.indicators import (
    average_true_range,
    bollinger_bands,
    bollinger_bandwidth,
    directional_indicators,
    macd,
    percentage_volume_oscillator,
    rolling_percentile,
    rolling_percentile_rank,
    simple_moving_average,
    true_range,
    wilder_rsi,
)


class IndicatorTests(unittest.TestCase):
    def test_simple_moving_average_has_deterministic_warmup(self) -> None:
        self.assertEqual(
            simple_moving_average([1.0, 2.0, 3.0, 4.0], 3),
            [None, None, 2.0, 3.0],
        )

    def test_wilder_rsi_matches_published_reference_sequence(self) -> None:
        closes = [
            44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
            45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00,
            46.03, 46.41, 46.22, 45.64,
        ]

        values = wilder_rsi(closes, 14)

        self.assertEqual(values[:14], [None] * 14)
        self.assertAlmostEqual(values[14], 70.464135, places=5)
        self.assertAlmostEqual(values[15], 66.249619, places=5)

    def test_rsi_handles_one_sided_and_flat_sequences(self) -> None:
        self.assertEqual(wilder_rsi([1.0, 2.0, 3.0], 2)[-1], 100.0)
        self.assertEqual(wilder_rsi([3.0, 2.0, 1.0], 2)[-1], 0.0)
        self.assertEqual(wilder_rsi([2.0, 2.0, 2.0], 2)[-1], 50.0)

    def test_bollinger_bands_and_bandwidth_use_population_deviation(self) -> None:
        middle, upper, lower = bollinger_bands([1.0, 2.0, 3.0], 3, 2.0)
        deviation = math.sqrt(2.0 / 3.0)

        self.assertEqual(middle, [None, None, 2.0])
        self.assertAlmostEqual(upper[-1], 2.0 + 2.0 * deviation)
        self.assertAlmostEqual(lower[-1], 2.0 - 2.0 * deviation)
        self.assertAlmostEqual(bollinger_bandwidth([1.0, 2.0, 3.0], 3)[-1], 2.0 * deviation)

    def test_rolling_percentile_and_rank_have_warmups_and_known_values(self) -> None:
        self.assertEqual(
            rolling_percentile([1.0, 4.0, 2.0, 8.0], 3, 50.0),
            [None, None, 2.0, 4.0],
        )
        self.assertEqual(
            rolling_percentile_rank([1.0, 4.0, 2.0, 8.0], 3),
            [None, None, 50.0, 83.33333333333333],
        )

    def test_true_range_matches_gap_aware_reference_values(self) -> None:
        self.assertEqual(
            true_range(
                [10.0, 12.0, 13.0, 14.0],
                [8.0, 9.0, 10.0, 11.0],
                [9.0, 11.0, 10.5, 13.0],
            ),
            [None, 3.0, 3.0, 3.5],
        )

    def test_average_true_range_uses_wilder_seed_and_smoothing(self) -> None:
        values = average_true_range(
            [10.0, 12.0, 13.0, 14.0],
            [8.0, 9.0, 10.0, 11.0],
            [9.0, 11.0, 10.5, 13.0],
            period=2,
        )

        self.assertEqual(values[:2], [None, None])
        self.assertEqual(values[2], 3.0)
        self.assertEqual(values[3], 3.25)

    def test_directional_indicators_have_talib_style_warmups(self) -> None:
        positive, negative, adx = directional_indicators(
            [10.0, 11.0, 12.0, 13.0, 14.0],
            [8.0, 9.0, 10.0, 11.0, 12.0],
            [9.0, 10.0, 11.0, 12.0, 13.0],
            period=2,
        )

        self.assertEqual(positive[:2], [None, None])
        self.assertEqual(negative[:2], [None, None])
        self.assertEqual(adx[:3], [None, None, None])
        self.assertEqual(positive[2:], [50.0, 50.0, 50.0])
        self.assertEqual(negative[2:], [0.0, 0.0, 0.0])
        self.assertEqual(adx[3:], [100.0, 100.0])

    def test_directional_indicators_handle_flat_market(self) -> None:
        positive, negative, adx = directional_indicators(
            [2.0] * 6,
            [2.0] * 6,
            [2.0] * 6,
            period=2,
        )

        self.assertEqual(positive[2:], [0.0] * 4)
        self.assertEqual(negative[2:], [0.0] * 4)
        self.assertEqual(adx[3:], [0.0] * 3)

    def test_macd_matches_sma_seeded_reference_sequence(self) -> None:
        line, signal, histogram = macd(
            [1.0, 4.0, 3.0, 2.0, 5.0, 4.0],
            fast_period=2,
            slow_period=3,
            signal_period=2,
        )

        self.assertEqual(line[:3], [None, None, None])
        self.assertEqual(signal[:3], [None, None, None])
        self.assertEqual(histogram[:3], [None, None, None])
        self.assertAlmostEqual(line[3], 1.0 / 6.0)
        self.assertAlmostEqual(signal[3], 1.0 / 2.0)
        self.assertAlmostEqual(histogram[3], -1.0 / 3.0)
        self.assertAlmostEqual(line[4], 1.0 / 2.0)
        self.assertAlmostEqual(signal[4], 1.0 / 2.0)
        self.assertAlmostEqual(histogram[4], 0.0)

    def test_percentage_volume_oscillator_matches_reference_sequence(self) -> None:
        values = percentage_volume_oscillator(
            [1.0, 2.0, 3.0, 2.0, 5.0],
            fast_period=2,
            slow_period=3,
        )

        self.assertEqual(values[:2], [None, None])
        self.assertAlmostEqual(values[2], 25.0)
        self.assertAlmostEqual(values[3], 100.0 / 12.0)
        self.assertAlmostEqual(values[4], 1000.0 / 63.0)

    def test_new_indicators_are_prefix_stable(self) -> None:
        highs = [10.0 + index + index % 3 for index in range(40)]
        lows = [value - 2.0 - index % 2 for index, value in enumerate(highs)]
        closes = [(high + low) / 2.0 for high, low in zip(highs, lows)]
        volume = [100.0 + index * 4.0 + (index % 5) * 7.0 for index in range(40)]
        prefix_length = 30

        self.assertEqual(
            true_range(highs, lows, closes)[:prefix_length],
            true_range(highs[:prefix_length], lows[:prefix_length], closes[:prefix_length]),
        )
        self.assertEqual(
            average_true_range(highs, lows, closes, 5)[:prefix_length],
            average_true_range(
                highs[:prefix_length], lows[:prefix_length], closes[:prefix_length], 5
            ),
        )
        full_directional = directional_indicators(highs, lows, closes, 5)
        prefix_directional = directional_indicators(
            highs[:prefix_length], lows[:prefix_length], closes[:prefix_length], 5
        )
        for full_values, prefix_values in zip(full_directional, prefix_directional):
            self.assertEqual(full_values[:prefix_length], prefix_values)
        full_macd = macd(closes, 4, 8, 3)
        prefix_macd = macd(closes[:prefix_length], 4, 8, 3)
        for full_values, prefix_values in zip(full_macd, prefix_macd):
            self.assertEqual(full_values[:prefix_length], prefix_values)
        self.assertEqual(
            percentage_volume_oscillator(volume, 4, 8)[:prefix_length],
            percentage_volume_oscillator(volume[:prefix_length], 4, 8),
        )

    def test_new_indicators_return_all_warmup_for_short_inputs(self) -> None:
        self.assertEqual(average_true_range([2.0], [1.0], [1.5], 1), [None])
        self.assertEqual(
            directional_indicators([2.0], [1.0], [1.5], 1),
            ([None], [None], [None]),
        )
        self.assertEqual(macd([1.0, 2.0], 2, 3, 2), ([None] * 2,) * 3)
        self.assertEqual(percentage_volume_oscillator([1.0, 2.0], 2, 3), [None] * 2)

    def test_rejects_invalid_indicator_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive integer"):
            simple_moving_average([1.0], 0)
        with self.assertRaisesRegex(ValueError, "finite"):
            wilder_rsi([1.0, math.nan], 1)
        with self.assertRaisesRegex(ValueError, "between 0 and 100"):
            rolling_percentile([1.0], 1, 101.0)
        with self.assertRaisesRegex(ValueError, "equal lengths"):
            true_range([2.0], [1.0, 2.0], [1.5])
        with self.assertRaisesRegex(ValueError, "greater than or equal"):
            true_range([1.0], [2.0], [1.5])
        with self.assertRaisesRegex(ValueError, "positive integer"):
            average_true_range([2.0], [1.0], [1.5], False)
        with self.assertRaisesRegex(ValueError, "less than"):
            macd([1.0, 2.0], 3, 3, 2)
        with self.assertRaisesRegex(ValueError, "finite"):
            percentage_volume_oscillator([1.0, math.inf], 1, 2)
        with self.assertRaisesRegex(ValueError, "non-negative"):
            percentage_volume_oscillator([1.0, -1.0], 1, 2)
        with self.assertRaisesRegex(ValueError, "undefined"):
            percentage_volume_oscillator([0.0, 0.0, 0.0], 1, 2)


if __name__ == "__main__":
    unittest.main()
