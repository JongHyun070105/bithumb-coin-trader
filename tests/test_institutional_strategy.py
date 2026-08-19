import unittest
from datetime import datetime, timezone, timedelta
from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.indicators import (
    candle_displacement_ratio,
    institutional_displacement_signals,
)
from bithumb_coin_trader.strategy import (
    InstitutionalDisplacementParameters,
    InstitutionalDisplacementStrategy,
    TradingAgentsMultiAgentParameters,
    TradingAgentsMultiAgentStrategy,
)


class TestInstitutionalAndTradingAgents(unittest.TestCase):
    def setUp(self):
        base_time = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        self.candles = []
        price = 100000.0
        # Generate 250 sample candles
        for i in range(250):
            t = base_time + timedelta(minutes=30 * i)
            # Create an institutional displacement surge at bar 120
            if i == 120:
                o = price
                h = price * 1.05
                l = price * 0.99
                c = price * 1.045  # large body
                v = 5000.0  # high volume
            elif i == 150:
                o = price
                h = price * 1.01
                l = price * 0.95
                c = price * 0.955  # large bear body
                v = 6000.0
            else:
                o = price
                h = price * 1.005
                l = price * 0.995
                c = price * 1.001
                v = 100.0
            self.candles.append(
                Candle(market="KRW-BTC", timestamp=t, open=o, high=h, low=l, close=c, volume=v)
            )
            price = c

    def test_candle_displacement_ratio(self):
        opens = [100.0, 100.0]
        highs = [110.0, 105.0]
        lows = [90.0, 95.0]
        closes = [108.0, 101.0]
        ratios = candle_displacement_ratio(opens, highs, lows, closes)
        self.assertEqual(len(ratios), 2)
        self.assertAlmostEqual(ratios[0], 8.0 / 20.0)  # |108-100| / (110-90) = 8/20 = 0.4
        self.assertAlmostEqual(ratios[1], 1.0 / 10.0)  # |101-100| / (105-95) = 1/10 = 0.1

    def test_institutional_displacement_signals(self):
        opens = [c.open for c in self.candles]
        highs = [c.high for c in self.candles]
        lows = [c.low for c in self.candles]
        closes = [c.close for c in self.candles]
        volumes = [c.volume for c in self.candles]

        bull_shifts, bear_shifts, vol_ma = institutional_displacement_signals(
            opens, highs, lows, closes, volumes, vol_period=20, vol_multiplier=2.0, min_body_pct=50.0
        )
        self.assertTrue(bull_shifts[120])
        self.assertTrue(bear_shifts[150])
        self.assertFalse(bull_shifts[0])

    def test_institutional_displacement_strategy(self):
        strategy = InstitutionalDisplacementStrategy()
        signals = strategy.generate(self.candles)
        self.assertEqual(len(signals), len(self.candles))
        self.assertIn(Signal.LONG, signals)

    def test_tradingagents_multiagent_strategy(self):
        strategy = TradingAgentsMultiAgentStrategy()
        signals = strategy.generate(self.candles)
        self.assertEqual(len(signals), len(self.candles))
        for s in signals:
            self.assertIn(s, (Signal.LONG, Signal.FLAT))


if __name__ == "__main__":
    unittest.main()
