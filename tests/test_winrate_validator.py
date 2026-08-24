from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.winrate_research import WinRateResearchConfig


SCRIPT = Path(__file__).parents[1] / "scripts/validate_winrate_research.py"
SPEC = importlib.util.spec_from_file_location("independent_winrate_validator", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class FlatCandidate:
    name = "flat_candidate"

    def generate(self, values: list[Candle] | tuple[Candle, ...], **_: object) -> list[Signal]:
        return [Signal.FLAT] * len(values)


def _candles(count: int) -> list[Candle]:
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


class IndependentValidatorMutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.csv_path = root / "candles.csv"
        self.report_path = root / "result.json"
        self.mirror_path = root / "mirror.json"
        self.ledger_path = root / "ledger.json"
        self.config = WinRateResearchConfig(
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
        self.factories = {"flat_candidate": FlatCandidate}
        self.families = {"flat_candidate": "test"}
        values = _candles(60)
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=("market", "timestamp", "open", "high", "low", "close", "volume"),
            )
            writer.writeheader()
            for candle in values:
                writer.writerow({
                    "market": candle.market,
                    "timestamp": candle.timestamp.isoformat(),
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                })
        skeleton = {"sealed_holdout": {"opened": False}}
        expected = validator._independent_recompute(
            values, self.config, self.factories, self.families, skeleton
        )
        self.report = {
            "schema_version": 1,
            "generated_at": datetime(2026, 1, 2, tzinfo=UTC).isoformat(),
            "status": "RESEARCH_ONLY",
            **expected,
        }
        self._write_both(self.report)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_both(self, report: dict[str, object]) -> None:
        payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
        self.report_path.write_text(payload, encoding="utf-8")
        self.mirror_path.write_text(payload, encoding="utf-8")

    def _validate(self) -> dict[str, object]:
        live = {
            "installed_plist_off": True,
            "installed_wrapper_off": True,
            "launchctl_off": True,
            "installed_plist_sha256": "plist",
            "installed_wrapper_sha256": "wrapper",
        }
        with patch.object(validator, "_live_entry_off_evidence", return_value=live):
            return validator.validate(
                input_path=self.csv_path,
                report_path=self.report_path,
                mirror_path=self.mirror_path,
                ledger_path=self.ledger_path,
                config=self.config,
                factories=self.factories,
                families=self.families,
            )

    def test_unopened_holdout_and_unmodified_report_pass(self) -> None:
        result = self._validate()
        self.assertTrue(result["passed"], result["issues"])
        self.assertFalse(self.report["sealed_holdout"]["opened"])

    def test_mutated_candidate_metric_fails(self) -> None:
        self.report["development"]["candidates"][0]["base"]["win_rate"] = 0.75
        self._write_both(self.report)
        result = self._validate()
        self.assertFalse(result["passed"])
        self.assertTrue(any("development candidate metrics" in item for item in result["issues"]))

    def test_mutated_gate_decision_fails(self) -> None:
        self.report["development"]["candidates"][0]["gate_evaluation"]["passed"] = True
        self._write_both(self.report)
        result = self._validate()
        self.assertFalse(result["passed"])
        self.assertTrue(any("development candidate metrics" in item for item in result["issues"]))

    def test_mutated_fold_metric_fails(self) -> None:
        fold = self.report["development"]["candidates"][0]["base"]["folds"][0]
        fold["total_return"] = 0.123
        self._write_both(self.report)
        result = self._validate()
        self.assertFalse(result["passed"])
        self.assertTrue(any("development candidate metrics" in item for item in result["issues"]))

    def test_mutated_protocol_fails(self) -> None:
        self.report["protocol"]["allow_short"] = True
        self._write_both(self.report)
        result = self._validate()
        self.assertFalse(result["passed"])
        self.assertTrue(any("research protocol" in item for item in result["issues"]))

    def test_mutated_candidate_manifest_fails(self) -> None:
        self.report["candidate_manifest"]["sha256"] = "f" * 64
        self._write_both(self.report)
        result = self._validate()
        self.assertFalse(result["passed"])
        self.assertTrue(any("candidate manifest" in item for item in result["issues"]))

    def test_mutated_selection_fails(self) -> None:
        self.report["selection"]["can_promote"] = True
        self._write_both(self.report)
        result = self._validate()
        self.assertFalse(result["passed"])
        self.assertTrue(any("research selection" in item for item in result["issues"]))

    def test_malformed_nested_artifact_returns_failed(self) -> None:
        self.report["sealed_holdout"] = ["not", "a", "mapping"]
        self._write_both(self.report)
        result = self._validate()
        self.assertFalse(result["passed"])
        self.assertIsInstance(result["issues"], list)

    def test_mutated_dataset_artifact_fails(self) -> None:
        self.report["dataset"]["sha256"] = "0" * 64
        self._write_both(self.report)
        result = self._validate()
        self.assertFalse(result["passed"])
        self.assertTrue(any("dataset identity" in item for item in result["issues"]))

    def test_mirror_mutation_fails(self) -> None:
        self.mirror_path.write_text("{}\n", encoding="utf-8")
        result = self._validate()
        self.assertFalse(result["passed"])
        self.assertTrue(any("mirror" in item for item in result["issues"]))

    def test_opened_holdout_requires_valid_ledger(self) -> None:
        self.report["sealed_holdout"]["opened"] = True
        self._write_both(self.report)
        result = self._validate()
        self.assertFalse(result["passed"])
        self.assertTrue(any("holdout" in item for item in result["issues"]))

    def test_unopened_report_fails_if_any_ledger_exists(self) -> None:
        self.ledger_path.write_text("{}\n", encoding="utf-8")
        result = self._validate()
        self.assertFalse(result["passed"])
        self.assertTrue(any("ledger exists" in item for item in result["issues"]))

    def test_opened_ledger_report_hash_mutation_fails(self) -> None:
        report = {
            "dataset": {"sha256": "data"},
            "candidate_manifest": {"sha256": "candidates"},
            "protocol": {"version": 1},
            "sealed_holdout": {
                "opened": True,
                "evaluated_candidates": ["candidate"],
                "results": [{"name": "candidate"}],
            },
        }
        self.report_path.write_text(json.dumps(report) + "\n", encoding="utf-8")
        ledger = {
            "state": "opened",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "opened_at": datetime(2026, 1, 2, tzinfo=UTC).isoformat(),
            "dataset_sha256": "data",
            "candidate_manifest_sha256": "candidates",
            "protocol_sha256": validator._canonical_hash(report["protocol"]),
            "finalists": ["candidate"],
            "evaluated_candidates": ["candidate"],
            "report_sha256": "0" * 64,
        }
        self.ledger_path.write_text(json.dumps(ledger) + "\n", encoding="utf-8")
        issues: list[str] = []
        validator._validate_holdout_ledger(
            report, self.report_path, self.ledger_path, issues
        )
        self.assertTrue(any("report hash" in item for item in issues))


if __name__ == "__main__":
    unittest.main()
