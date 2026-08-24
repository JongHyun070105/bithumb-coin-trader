from __future__ import annotations

import unittest

from bithumb_coin_trader.self_growth import EvolutionaryReviewer


class EvolutionaryReviewerTests(unittest.TestCase):
    def test_pair_trades_does_not_sum_cumulative_scale_in_volume(self) -> None:
        trades = [
            {
                "timestamp": "2026-08-24T02:32:05+09:00",
                "action": "BUY",
                "market": "KRW-SUI",
                "price": 1124.0,
                "volume": "8.47602131",
                "amount_krw": 9544,
                "reason": "initial",
            },
            {
                "timestamp": "2026-08-24T02:53:50+09:00",
                "action": "BUY",
                "market": "KRW-SUI",
                "price": 1127.0,
                "volume": "12.88519239",
                "amount_krw": 5000,
                "reason": "scale-in",
            },
            {
                "timestamp": "2026-08-24T04:33:33+09:00",
                "action": "SELL",
                "market": "KRW-SUI",
                "price": 1137.0,
                "volume": "12.88519239",
                "amount_krw": 14650.46,
                "pnl_krw": 110.53,
                "reason": "TRAILING-STOP",
            },
        ]

        paired = EvolutionaryReviewer().pair_trades(trades)

        self.assertEqual(len(paired), 1)
        self.assertAlmostEqual(paired[0]["entry_price"], 14544 / 12.88519239)
        self.assertAlmostEqual(paired[0]["pnl_pct"], 110.53 / 14544 * 100)
        self.assertLess(paired[0]["pnl_pct"], 1.0)

    def test_non_monotonic_cumulative_volume_is_rejected(self) -> None:
        trades = [
            {"timestamp": "2026-01-01T00:00:00+09:00", "action": "BUY", "market": "KRW-BTC", "volume": "2", "amount_krw": 10},
            {"timestamp": "2026-01-01T00:01:00+09:00", "action": "BUY", "market": "KRW-BTC", "volume": "1", "amount_krw": 5},
            {"timestamp": "2026-01-01T00:02:00+09:00", "action": "SELL", "market": "KRW-BTC", "volume": "1", "amount_krw": 9, "pnl_krw": -1},
        ]
        self.assertEqual(EvolutionaryReviewer().pair_trades(trades), [])


if __name__ == "__main__":
    unittest.main()
