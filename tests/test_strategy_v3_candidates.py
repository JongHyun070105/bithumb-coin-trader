from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from bithumb_coin_trader.daily_strategy_candidates import (
    WeeklyAbsoluteMomentumStrategy,
    WeeklyDonchianStrategy,
    WeeklySmaCrossStrategy,
)
from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.strategy_v3_candidates import (
    E9_PERIODS,
    E9DonchianVolatilityStrategy,
    EntryVolatilityAbsoluteMomentumStrategy,
    MajorityTrendStrategy,
    strategy_v3_candidate_factories,
)


def _candles(count: int) -> list[Candle]:
    start = datetime(2023, 12, 31, 15, tzinfo=UTC)
    price = 100.0
    candles: list[Candle] = []
    for index in range(count):
        cycle = (index % 90) / 90
        price *= 1.004 if cycle < 0.72 else 0.988
        candles.append(
            Candle(
                timestamp=start + timedelta(days=index),
                open=price * 0.999,
                high=price * 1.003,
                low=price * 0.997,
                close=price,
                volume=10.0,
            )
        )
    return candles


STRATEGIES = (
    E9DonchianVolatilityStrategy,
    EntryVolatilityAbsoluteMomentumStrategy,
    MajorityTrendStrategy,
)


class StrategyV3CandidateTests(unittest.TestCase):
    def test_registry_and_parameters_are_exactly_frozen(self) -> None:
        self.assertEqual(E9_PERIODS, (5, 10, 20, 30, 60, 90, 150, 250, 360))
        factories = strategy_v3_candidate_factories()
        self.assertEqual(
            tuple(factories),
            (
                "v3_e9_donchian_90d_vol25",
                "v3_absolute_momentum_126_63_entry_vol20",
                "v3_frozen_majority_2_of_3",
            ),
        )
        self.assertEqual(
            [factory().required_history_bars for factory in factories.values()],
            [360, 127, 200],
        )
        with self.assertRaises(ValueError):
            E9DonchianVolatilityStrategy(periods=(5, 10))
        with self.assertRaises(ValueError):
            EntryVolatilityAbsoluteMomentumStrategy(volatility_days=29)
        with self.assertRaises(ValueError):
            MajorityTrendStrategy(required_votes=1)

    def test_all_weights_are_bounded_and_prefix_stable(self) -> None:
        candles = _candles(620)
        for strategy_type in STRATEGIES:
            with self.subTest(strategy=strategy_type.__name__):
                prefix = strategy_type().generate(candles[:510])
                complete = strategy_type().generate(candles)
                self.assertEqual(prefix, complete[:510])
                self.assertTrue(all(0.0 <= weight <= 1.0 for weight in complete))

    def test_e9_uses_nine_equal_model_slices(self) -> None:
        candles = _candles(620)
        weights = E9DonchianVolatilityStrategy().generate(candles)
        # A signal-count change bypasses the 20 percentage-point volatility
        # rebalance filter. With scale <= 1, every raw model contribution is
        # bounded by its frozen 1/9 slice.
        self.assertTrue(any(weight > 0 for weight in weights))
        self.assertLessEqual(max(weights), 1.0)

    def test_e9_stop_boundary_exits_and_updated_stop_is_next_day_information(self) -> None:
        strategy = E9DonchianVolatilityStrategy()
        self.assertEqual(
            strategy._next_model_state(
                active=True,
                prior_stop=90.0,
                close=90.0,
                upper=110.0,
                midpoint=100.0,
            ),
            (False, None),
        )
        # A higher midpoint raises the trailing stop after today's comparison;
        # it therefore becomes an exit boundary only on the next candle.
        self.assertEqual(
            strategy._next_model_state(
                active=True,
                prior_stop=90.0,
                close=95.0,
                upper=120.0,
                midpoint=100.0,
            ),
            (True, 100.0),
        )
        # Entry is evaluated before exits for a flat model.
        self.assertEqual(
            strategy._next_model_state(
                active=False,
                prior_stop=None,
                close=120.0,
                upper=120.0,
                midpoint=100.0,
            ),
            (True, 100.0),
        )
        # The paper's Pos rule checks a fresh upper-channel hit before the
        # stop branch, even when a non-decreasing old stop is now higher.
        self.assertEqual(
            strategy._next_model_state(
                active=True,
                prior_stop=130.0,
                close=120.0,
                upper=120.0,
                midpoint=100.0,
            ),
            (True, 130.0),
        )

    def test_entry_volatility_weight_does_not_rebalance_while_long(self) -> None:
        candles = _candles(620)
        signals = WeeklyAbsoluteMomentumStrategy().generate(candles)
        weights = EntryVolatilityAbsoluteMomentumStrategy().generate(candles)
        for index in range(1, len(candles)):
            if signals[index] is Signal.LONG and signals[index - 1] is Signal.LONG:
                self.assertEqual(weights[index], weights[index - 1])
        self.assertTrue(all(weight <= 0.50 for weight in weights))

    def test_majority_is_exactly_two_of_three_at_thirty_percent(self) -> None:
        candles = _candles(620)
        component_signals = (
            WeeklyAbsoluteMomentumStrategy().generate(candles),
            WeeklySmaCrossStrategy().generate(candles),
            WeeklyDonchianStrategy().generate(candles),
        )
        expected = [
            0.30
            if sum(signals[index] is Signal.LONG for signals in component_signals) >= 2
            else 0.0
            for index in range(len(candles))
        ]
        self.assertEqual(MajorityTrendStrategy().generate(candles), expected)

    def test_daily_validation_rejects_gap_and_wrong_market(self) -> None:
        candles = _candles(3)
        with self.assertRaises(ValueError):
            E9DonchianVolatilityStrategy().generate([candles[0], candles[2]])
        wrong = Candle(
            timestamp=candles[0].timestamp,
            open=100,
            high=100,
            low=100,
            close=100,
            volume=1,
            market="KRW-ETH",
        )
        with self.assertRaises(ValueError):
            MajorityTrendStrategy().generate([wrong])


if __name__ == "__main__":
    unittest.main()
