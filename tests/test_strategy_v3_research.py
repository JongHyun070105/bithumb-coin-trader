from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.strategy_v3_candidates import strategy_v3_candidate_factories
from bithumb_coin_trader.strategy_v3_research import _nested_outer, v3_settings


class StrategyV3ResearchTests(unittest.TestCase):
    def test_v3_execution_contract_is_frozen_and_costs_scale(self) -> None:
        base = v3_settings(1)
        stress = v3_settings(3)
        self.assertEqual(base.initial_capital_krw, 100_000)
        self.assertEqual(base.minimum_order_krw, 5_000)
        self.assertEqual(base.maximum_order_krw, 60_000)
        self.assertEqual(base.cash_reserve_krw, 5_000)
        self.assertEqual(stress.fee_rate, base.fee_rate * 3)
        self.assertEqual(stress.slippage_bps, base.slippage_bps * 3)

    def test_outer_test_mutation_cannot_change_its_selection(self) -> None:
        original = _candles(2_220)
        mutated = list(original)
        for index in range(1_320, 1_620):
            candle = mutated[index]
            factor = 0.4 if index % 2 else 2.5
            close = candle.close * factor
            mutated[index] = Candle(
                market=candle.market,
                timestamp=candle.timestamp,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=candle.volume,
            )
        factories = strategy_v3_candidate_factories()
        before = _nested_outer(original, factories)["selections"][0]
        after = _nested_outer(mutated, factories)["selections"][0]
        self.assertEqual(before["selected"], after["selected"])
        self.assertEqual(before["selection_sha256"], after["selection_sha256"])


def _candles(count: int) -> list[Candle]:
    start = datetime(2020, 1, 1, 15, tzinfo=UTC)
    price = 10_000_000.0
    result: list[Candle] = []
    for index in range(count):
        phase = index % 240
        price *= 1.006 if phase < 150 else 0.994
        result.append(
            Candle(
                market="KRW-BTC",
                timestamp=start + timedelta(days=index),
                open=price * 0.999,
                high=price * 1.004,
                low=price * 0.996,
                close=price,
                volume=10.0,
            )
        )
    return result


if __name__ == "__main__":
    unittest.main()
