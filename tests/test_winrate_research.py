from __future__ import annotations

from datetime import UTC, datetime, timedelta
import math
import unittest

from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.winrate_research import (
    WinRateResearchConfig,
    _evaluate_window,
    _validate_holdout_prefix_stability,
    build_report,
    evaluate_development_gate,
    normalized_settings,
    wilson_lower_bound,
)


def candles(count: int) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(
            market="KRW-BTC",
            timestamp=start + timedelta(minutes=30 * index),
            open=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            close=100.5 + index,
            volume=10.0,
        )
        for index in range(count)
    ]


class FlatCandidate:
    name = "flat_candidate"

    def generate(self, values: list[Candle] | tuple[Candle, ...], **_: object) -> list[Signal]:
        return [Signal.FLAT] * len(values)


class ShortCandidate:
    name = "short_candidate"

    def generate(self, values: list[Candle] | tuple[Candle, ...], **_: object) -> list[Signal]:
        return [Signal.SHORT] * len(values)


class LengthDependentCandidate:
    name = "length_dependent"

    def generate(
        self, values: list[Candle] | tuple[Candle, ...], **_: object
    ) -> list[Signal]:
        first = Signal.LONG if len(values) >= 55 else Signal.FLAT
        return [first, *([Signal.FLAT] * (len(values) - 1))]


class RecordingFlatCandidate:
    name = "recording_flat"

    def __init__(self, calls: list[int]) -> None:
        self.calls = calls

    def generate(
        self, values: list[Candle] | tuple[Candle, ...], **_: object
    ) -> list[Signal]:
        self.calls.append(len(values))
        return [Signal.FLAT] * len(values)


class WinRateResearchTests(unittest.TestCase):
    def test_default_geometry_is_exact_and_expanding(self) -> None:
        config = WinRateResearchConfig()
        self.assertEqual(
            config.expanding_boundaries(),
            (
                (0, 17_000, 17_000, 21_000),
                (0, 21_000, 21_000, 25_000),
                (0, 25_000, 25_000, 29_000),
                (0, 29_000, 29_000, 33_000),
                (0, 33_000, 33_000, 37_000),
                (0, 37_000, 37_000, 41_000),
            ),
        )
        self.assertEqual(config.development_count + config.sealed_holdout_count, 45_000)

    def test_wilson_gate_uses_integer_wins_and_trials(self) -> None:
        self.assertAlmostEqual(wilson_lower_bound(21, 30), 0.5212421254128504)
        self.assertGreater(wilson_lower_bound(21, 30), 0.50)
        self.assertLess(wilson_lower_bound(20, 30), 0.50)

    def test_development_gate_includes_every_boundary(self) -> None:
        config = WinRateResearchConfig()
        base = {
            "win_rate": 0.70,
            "closed_trade_count": 30,
            "total_return": 0.01,
            "profit_factor": 1.01,
            "profit_factor_is_infinite": False,
            "maximum_drawdown": 0.15,
            "positive_fold_count": 4,
            "wilson_95_lower_bound": wilson_lower_bound(21, 30),
        }
        stress = {"total_return": 0.001}
        self.assertTrue(evaluate_development_gate(base, stress, config)["passed"])
        for field, value in (
            ("win_rate", math.nextafter(0.70, 0.0)),
            ("closed_trade_count", 29),
            ("total_return", 0.0),
            ("profit_factor", 1.0),
            ("maximum_drawdown", math.nextafter(0.15, 1.0)),
            ("positive_fold_count", 3),
            ("wilson_95_lower_bound", math.nextafter(0.50, 0.0)),
        ):
            changed = dict(base)
            changed[field] = value
            self.assertFalse(evaluate_development_gate(changed, stress, config)["passed"], field)
        self.assertFalse(
            evaluate_development_gate(base, {"total_return": 0.0}, config)["passed"]
        )

    def test_final_liquidation_affects_equity_but_not_closed_win_rate(self) -> None:
        values = candles(20)
        signals = [Signal.FLAT] * 5 + [Signal.LONG] * 15
        metrics = _evaluate_window(
            values,
            signals,
            start=5,
            end=20,
            settings=normalized_settings(),
            fold_size=None,
        )
        self.assertEqual(metrics["closed_trade_count"], 0)
        self.assertEqual(metrics["forced_final_liquidation_count"], 1)
        self.assertEqual(metrics["win_rate"], 0.0)
        self.assertNotEqual(metrics["total_return"], 0.0)

    def test_failed_development_candidates_leave_holdout_sealed(self) -> None:
        config = WinRateResearchConfig(
            historical_count=60,
            development_count=50,
            initial_train_count=20,
            development_test_count=10,
            development_fold_count=3,
            sealed_holdout_count=10,
            maximum_holdout_candidates=2,
            minimum_development_closed_trades=2,
            minimum_holdout_closed_trades=1,
        )
        report = build_report(
            candles(60),
            generated_at=datetime(2026, 1, 2, tzinfo=UTC),
            config=config,
            factories={"flat_candidate": FlatCandidate},
            families={"flat_candidate": "test"},
        )
        self.assertFalse(report["sealed_holdout"]["opened"])
        self.assertEqual(report["sealed_holdout"]["evaluated_candidates"], [])
        self.assertEqual(report["selection"]["research_candidate"], "cash")

    def test_default_report_never_passes_holdout_to_candidate(self) -> None:
        config = WinRateResearchConfig(
            historical_count=60,
            development_count=50,
            initial_train_count=20,
            development_test_count=10,
            development_fold_count=3,
            sealed_holdout_count=10,
            maximum_holdout_candidates=2,
            minimum_development_closed_trades=2,
            minimum_holdout_closed_trades=1,
        )
        calls: list[int] = []
        build_report(
            candles(60),
            config=config,
            factories={"recording_flat": lambda: RecordingFlatCandidate(calls)},
            families={"recording_flat": "test"},
        )
        self.assertEqual(calls, [50])

    def test_holdout_tail_cannot_change_development_metrics(self) -> None:
        config = WinRateResearchConfig(
            historical_count=60,
            development_count=50,
            initial_train_count=20,
            development_test_count=10,
            development_fold_count=3,
            sealed_holdout_count=10,
            maximum_holdout_candidates=2,
            minimum_development_closed_trades=2,
            minimum_holdout_closed_trades=1,
        )
        original = candles(60)
        changed = list(original)
        for index in range(50, 60):
            candle = changed[index]
            changed[index] = Candle(
                market=candle.market,
                timestamp=candle.timestamp,
                open=candle.open * 10,
                high=candle.high * 10,
                low=candle.low * 10,
                close=candle.close * 10,
                volume=candle.volume,
            )
        kwargs = {
            "config": config,
            "factories": {"length_dependent": LengthDependentCandidate},
            "families": {"length_dependent": "test"},
            "generated_at": datetime(2026, 1, 2, tzinfo=UTC),
        }
        first = build_report(original, **kwargs)
        second = build_report(changed, **kwargs)
        self.assertEqual(first["development"], second["development"])

    def test_generic_holdout_prefix_check_rejects_tail_dependency(self) -> None:
        values = candles(60)
        full = LengthDependentCandidate().generate(values)
        with self.assertRaisesRegex(ValueError, "prefix-stable"):
            _validate_holdout_prefix_stability(
                LengthDependentCandidate,
                values,
                full,
                development_count=50,
            )

    def test_short_candidate_fails_closed(self) -> None:
        config = WinRateResearchConfig(
            historical_count=60,
            development_count=50,
            initial_train_count=20,
            development_test_count=10,
            development_fold_count=3,
            sealed_holdout_count=10,
            minimum_development_closed_trades=2,
            minimum_holdout_closed_trades=1,
        )
        with self.assertRaisesRegex(ValueError, "LONG/FLAT"):
            build_report(
                candles(60),
                config=config,
                factories={"short_candidate": ShortCandidate},
                families={"short_candidate": "test"},
            )

    def test_dataset_length_is_exact_not_tail_selected(self) -> None:
        config = WinRateResearchConfig(
            historical_count=60,
            development_count=50,
            initial_train_count=20,
            development_test_count=10,
            development_fold_count=3,
            sealed_holdout_count=10,
            minimum_development_closed_trades=2,
            minimum_holdout_closed_trades=1,
        )
        with self.assertRaisesRegex(ValueError, "exactly 60"):
            build_report(
                candles(61),
                config=config,
                factories={"flat_candidate": FlatCandidate},
                families={"flat_candidate": "test"},
            )


if __name__ == "__main__":
    unittest.main()
