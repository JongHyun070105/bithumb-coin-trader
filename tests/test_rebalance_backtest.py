from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.rebalance_backtest import RebalanceBacktester


def _candles(
    closes: list[float],
    *,
    opens: list[float] | None = None,
) -> list[Candle]:
    start = datetime(2023, 12, 31, 15, tzinfo=UTC)
    opens = opens or closes
    return [
        Candle(
            timestamp=start + timedelta(days=index),
            open=opens[index],
            high=max(opens[index], close),
            low=min(opens[index], close),
            close=close,
            volume=1.0,
        )
        for index, close in enumerate(closes)
    ]


def _settings(**overrides: float | int) -> TradingSettings:
    values: dict[str, float | int] = {
        "initial_capital_krw": 100_000,
        "fee_rate": 0.0,
        "slippage_bps": 0.0,
        "allocation_fraction": 1.0,
        "minimum_order_krw": 1,
        "maximum_order_krw": 100_000,
        "cash_reserve_krw": 0,
    }
    values.update(overrides)
    return TradingSettings(**values)  # type: ignore[arg-type]


class RebalanceBacktesterTests(unittest.TestCase):
    def test_prior_close_target_executes_at_next_open(self) -> None:
        candles = _candles([100, 150, 300], opens=[100, 200, 300])
        result = RebalanceBacktester(_settings()).run(candles, [0.50, 0.0, 0.0])
        self.assertEqual(result.fills[0].index, 1)
        self.assertEqual(result.fills[0].price, 200)
        self.assertEqual(result.fills[0].notional, 50_000)
        self.assertEqual(result.fills[1].index, 2)

    def test_cash_reserve_and_nonnegative_ledger_are_invariant(self) -> None:
        result = RebalanceBacktester(
            _settings(fee_rate=0.01, cash_reserve_krw=20_000)
        ).run(_candles([100, 100, 100]), [1.0, 1.0, 1.0])
        self.assertTrue(all(cash >= -1e-8 for cash in result.cash_curve))
        self.assertGreaterEqual(result.cash_curve[1], 20_000 - 1e-8)
        self.assertTrue(all(quantity >= 0 for quantity in result.base_quantity_curve))

    def test_higher_costs_cannot_improve_same_path(self) -> None:
        candles = _candles([100, 110, 105, 115, 120])
        targets = [0.8, 0.2, 0.8, 0.0, 0.0]
        free = RebalanceBacktester(_settings()).run(candles, targets)
        costly = RebalanceBacktester(
            _settings(fee_rate=0.005, slippage_bps=50)
        ).run(candles, targets)
        self.assertLess(costly.final_equity, free.final_equity)
        self.assertGreater(costly.total_fees, 0)

    def test_maximum_order_causes_partial_rebalance(self) -> None:
        result = RebalanceBacktester(
            _settings(maximum_order_krw=20_000)
        ).run(_candles([100, 100, 100]), [1.0, 1.0, 1.0])
        buys = [fill for fill in result.fills if fill.side == "buy"]
        self.assertEqual([fill.notional for fill in buys], [20_000, 20_000])
        self.assertAlmostEqual(result.base_quantity_curve[1], 200.0)
        self.assertAlmostEqual(result.base_quantity_curve[2], 0.0)  # final liquidation

    def test_subminimum_delta_is_deferred_until_large_enough(self) -> None:
        result = RebalanceBacktester(
            _settings(minimum_order_krw=20_000)
        ).run(_candles([100, 100, 100, 100]), [0.10, 0.30, 0.0, 0.0])
        self.assertEqual(result.fills[0].side, "buy")
        self.assertEqual(result.fills[0].index, 2)
        self.assertEqual(result.fills[0].notional, 30_000)

    def test_final_liquidation_and_ledger_metrics(self) -> None:
        result = RebalanceBacktester(
            _settings(fee_rate=0.0025, slippage_bps=5)
        ).run(_candles([100, 110, 120]), [0.50, 0.50, 0.50])
        self.assertTrue(result.fills[-1].is_final_liquidation)
        self.assertEqual(result.base_quantity_curve[-1], 0.0)
        self.assertAlmostEqual(result.final_equity, result.cash_curve[-1])
        self.assertAlmostEqual(result.total_fees, sum(fill.fee for fill in result.fills))
        self.assertAlmostEqual(
            result.gross_traded_notional,
            sum(fill.notional for fill in result.fills),
        )
        self.assertGreater(result.turnover, 0)

    def test_rejects_short_like_weights_and_non_daily_input(self) -> None:
        candles = _candles([100, 100])
        with self.assertRaises(ValueError):
            RebalanceBacktester(_settings()).run(candles, [-0.1, 0.0])
        with self.assertRaises(ValueError):
            RebalanceBacktester(_settings()).run([candles[0], candles[0]], [0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
