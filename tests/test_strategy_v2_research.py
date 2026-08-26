from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.strategy_v2_research import (
    StrategyV2Config,
    assert_finite_report,
    build_strategy_v2_report,
    research_settings,
)


KST = timezone(timedelta(hours=9))


class StrategyV2ResearchTests(unittest.TestCase):
    def test_report_keeps_holdout_sealed_and_execution_unchanged(self) -> None:
        config = StrategyV2Config(
            daily_historical_count=420,
            daily_development_count=360,
            daily_initial_train_count=240,
            daily_test_count=60,
            daily_fold_count=2,
            daily_sealed_holdout_count=60,
            minute_historical_count=6_000,
            minute_development_count=5_600,
            minute_initial_train_count=4_000,
            prior_non_cash_trials=52,
        )
        report = build_strategy_v2_report(
            _candles(420, timedelta(days=1), datetime(2025, 1, 1, tzinfo=KST)),
            _candles(6_000, timedelta(minutes=30), datetime(2025, 1, 1, tzinfo=timezone.utc)),
            generated_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
            config=config,
        )
        holdout = report["datasets"]["daily_sealed_holdout"]
        self.assertFalse(holdout["opened"])
        self.assertEqual(holdout["evaluated_candidates"], [])
        self.assertEqual(holdout["results"], [])
        self.assertFalse(report["selection"]["can_promote"])
        self.assertFalse(report["selection"]["paper_or_live_strategy_changed"])
        self.assertEqual(report["multiple_testing"]["deflated_sharpe"]["status"], "unavailable")
        self.assertTrue(all(row["passed"] for row in report["daily_prefix_audits"].values()))
        self.assertEqual(report["minute_development_audit"]["long_signal_bars_inside_warmup"], 0)
        assert_finite_report(report)

    def test_research_settings_use_real_order_floor_reserve_and_cost_stress(self) -> None:
        base = research_settings(1)
        stress = research_settings(3)
        self.assertEqual(base.minimum_order_krw, 5_000)
        self.assertEqual(base.cash_reserve_krw, 33_000)
        self.assertEqual(base.allocation_fraction, 0.30)
        self.assertEqual(stress.fee_rate, base.fee_rate * 3)
        self.assertEqual(stress.slippage_bps, base.slippage_bps * 3)

    def test_invalid_cost_multiplier_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "cost multiplier"):
            research_settings(4)


def _candles(count: int, delta: timedelta, start: datetime) -> list[Candle]:
    candles: list[Candle] = []
    price = 10_000_000.0
    for index in range(count):
        cycle = (index % 90) - 45
        price *= 1.0008 if cycle < 25 else 0.9994
        candles.append(
            Candle(
                market="KRW-BTC",
                timestamp=start + index * delta,
                open=price * 0.999,
                high=price * 1.006,
                low=price * 0.994,
                close=price,
                volume=1.0 + index % 7,
            )
        )
    return candles


if __name__ == "__main__":
    unittest.main()
