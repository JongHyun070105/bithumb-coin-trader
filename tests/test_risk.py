from __future__ import annotations

import unittest

from bithumb_coin_trader.models import Signal
from bithumb_coin_trader.risk import RiskContext, RiskLimits, evaluate_pretrade


class RiskTests(unittest.TestCase):
    def context(self, **overrides: object) -> RiskContext:
        values = {
            "requested_side": Signal.LONG,
            "requested_notional_krw": 10_000,
            "current_equity_krw": 20_000,
            "start_of_day_equity_krw": 20_000,
            "peak_equity_krw": 20_000,
            "daily_entries": 0,
            "data_is_fresh": True,
        }
        values.update(overrides)
        return RiskContext(**values)

    def test_allows_valid_small_account_order(self) -> None:
        self.assertTrue(evaluate_pretrade(self.context()).allowed)

    def test_short_fails_closed(self) -> None:
        decision = evaluate_pretrade(self.context(requested_side=Signal.SHORT))
        self.assertFalse(decision.allowed)
        self.assertIn("short execution adapter is disabled", decision.reasons)

    def test_untracked_order_blocks_new_order(self) -> None:
        decision = evaluate_pretrade(self.context(has_untracked_order=True))
        self.assertFalse(decision.allowed)

    def test_drawdown_limit_blocks_order(self) -> None:
        decision = evaluate_pretrade(
            self.context(current_equity_krw=17_000, peak_equity_krw=20_000),
            RiskLimits(maximum_drawdown_fraction=0.10),
        )
        self.assertFalse(decision.allowed)


if __name__ == "__main__":
    unittest.main()
