from __future__ import annotations

import math
import unittest

from bithumb_coin_trader.indicators import (
    bollinger_bands,
    bollinger_bandwidth,
    rolling_percentile,
    rolling_percentile_rank,
    simple_moving_average,
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

    def test_rejects_invalid_indicator_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive integer"):
            simple_moving_average([1.0], 0)
        with self.assertRaisesRegex(ValueError, "finite"):
            wilder_rsi([1.0, math.nan], 1)
        with self.assertRaisesRegex(ValueError, "between 0 and 100"):
            rolling_percentile([1.0], 1, 101.0)


if __name__ == "__main__":
    unittest.main()
