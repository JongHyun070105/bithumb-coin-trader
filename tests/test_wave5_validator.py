from __future__ import annotations

import copy
from hashlib import sha256
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import validate_wave5_research as validator  # noqa: E402
from bithumb_coin_trader.wave5 import (  # noqa: E402
    WAVE5_CANDIDATE_NAMES,
    WAVE5_RUNNABLE_BTC_CANDIDATES,
    Wave5Config,
    wave5_candidate_manifest,
    wave5_candidate_manifest_hash,
)


def _metric(total_return: float, *, trades: int) -> dict[str, object]:
    config = Wave5Config()
    periods = config.test_size * config.fold_count
    factor = (1.0 + total_return) ** (1.0 / periods)
    curve = [20_000.0 * factor**index for index in range(periods + 1)]
    folds = []
    for index, boundary in enumerate(config.boundaries()):
        train_start, train_end, test_start, test_end = boundary
        start = index * config.test_size
        end = (index + 1) * config.test_size
        folds.append(
            {
                "fold": index + 1,
                "train": [train_start, train_end],
                "test": [test_start, test_end],
                "initial_equity_krw": curve[start],
                "final_equity_krw": curve[end],
                "total_return": curve[end] / curve[start] - 1.0,
                "maximum_drawdown": 0.0,
                "trade_count": trades // config.fold_count,
                "win_rate": 1.0 if total_return > 0 else 0.0,
                "exposure": 0.5 if trades else 0.0,
            }
        )
    digest = sha256(b"bithumb-coin-trader:wave5-equity:v1\n")
    for point in curve:
        digest.update(float(point).hex().encode("ascii"))
        digest.update(b"\n")
    return {
        "candidate_name": "candidate",
        "fold_count": config.fold_count,
        "compounded_return": curve[-1] / curve[0] - 1.0,
        "maximum_drawdown": validator._maximum_drawdown(curve),
        "mean_sharpe": 1.0 if total_return > 0 else 0.0,
        "trade_count": trades,
        "weighted_win_rate": 1.0 if total_return > 0 else 0.0,
        "closed_trade_count": trades,
        "oos_equity_evidence": {
            "point_count": len(curve),
            "initial_equity_krw": curve[0],
            "final_equity_krw": curve[-1],
            "sha256": digest.hexdigest(),
        },
        "folds": folds,
    }


def _gate(base: dict[str, object], stress: dict[str, object]) -> dict[str, object]:
    checks, actual = validator._expected_gate(base, stress)
    return {"checks": checks, "actual": actual, "passed": all(checks.values())}


def valid_report() -> dict[str, object]:
    config = Wave5Config()
    manifest = wave5_candidate_manifest()
    supplied_manifest = {
        **manifest,
        "candidate_count": len(WAVE5_CANDIDATE_NAMES),
        "sha256": wave5_candidate_manifest_hash(manifest),
    }
    cash = _metric(0.0, trades=0)
    winner = _metric(0.08, trades=16)
    winner_stress = _metric(0.03, trades=16)
    loser = _metric(-0.02, trades=8)
    rows = []
    for name in WAVE5_RUNNABLE_BTC_CANDIDATES:
        base, stress = (
            (cash, cash)
            if name == "cash"
            else ((winner, winner_stress) if name == "four_hour_trend_pullback" else (loser, loser))
        )
        base = copy.deepcopy(base)
        stress = copy.deepcopy(stress)
        base["candidate_name"] = name
        stress["candidate_name"] = name
        rows.append(
            {
                "name": name,
                "base": base,
                "double_cost_stress": stress,
                "gate_evaluation": _gate(base, stress),
            }
        )
    return {
        "schema_version": 5,
        "status": "RESEARCH_ONLY",
        "dataset": {
            "markets": ["KRW-BTC"],
            "market_count": 1,
            "candle_count": config.historical_count,
            "start_at": "2024-01-01T00:00:00+00:00",
            "end_at": "2026-01-01T00:00:00+00:00",
            "sha256": "a" * 64,
        },
        "candidate_manifest": supplied_manifest,
        "candidate_availability": {
            "runnable": list(WAVE5_RUNNABLE_BTC_CANDIDATES),
            "unavailable": {
                "cross_sectional_momentum": {
                    "reason": "requires at least three aligned market histories",
                    "observed_market_count": 1,
                    "result": None,
                }
            },
        },
        "validation_geometry": {
            "expanding": True,
            "boundaries": [
                {"train": [a, b], "test": [c, d]}
                for a, b, c, d in config.boundaries()
            ],
        },
        "costs": manifest["costs"],
        "candidates": rows,
        "selection": {
            "research_candidate": "four_hour_trend_pullback",
            "fallback_to_cash": False,
            "automatic_promotion": "forbidden",
            "can_promote": False,
            "paper_or_live_strategy_changed": False,
        },
    }


class Wave5ValidatorTests(unittest.TestCase):
    def test_valid_report_passes(self) -> None:
        self.assertEqual(validator.validate_report(valid_report()), [])

    def test_rejects_manifest_mutation(self) -> None:
        payload = valid_report()
        payload["candidate_manifest"]["execution"]["allow_pyramiding"] = True
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_cost_mutation(self) -> None:
        payload = valid_report()
        payload["costs"]["double_cost_stress"]["fee_rate_per_fill"] = 0.004
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_curve_metric_mutation(self) -> None:
        payload = valid_report()
        payload["candidates"][1]["base"]["compounded_return"] = 0.9
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_false_cash_fallback(self) -> None:
        payload = valid_report()
        payload["selection"]["research_candidate"] = "cash"
        payload["selection"]["fallback_to_cash"] = True
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_promotion_claim(self) -> None:
        payload = valid_report()
        payload["selection"]["can_promote"] = True
        self.assertTrue(validator.validate_report(payload))

    def test_rejects_fake_cross_sectional_result(self) -> None:
        payload = valid_report()
        payload["candidate_availability"]["unavailable"]["cross_sectional_momentum"]["result"] = {"return": 1.0}
        self.assertTrue(validator.validate_report(payload))


if __name__ == "__main__":
    unittest.main()
