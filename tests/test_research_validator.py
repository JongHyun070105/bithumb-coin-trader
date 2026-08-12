from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_candidate_research.py"


class CandidateResearchValidatorTests(unittest.TestCase):
    def test_repository_candidate_report_passes(self) -> None:
        report = ROOT / "reports" / "krw-btc-candidate-study-2026-08-12.json"
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), str(report)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["passed"])

    def test_missing_candidates_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            report.write_text('{"dataset":{"market":"KRW-BTC"}}', encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(VALIDATOR), str(report)],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["passed"])

    def test_corrupt_accounting_calendar_and_selection_fail_closed(self) -> None:
        source = ROOT / "reports" / "krw-btc-candidate-study-2026-08-12.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["dataset"]["candle_count"] = 1
        payload["dataset"]["sha256"] = "x" * 64
        payload["timeframe"] = "30m_execution_with_completed_1h_signals"
        first = payload["candidates_ranked_by_oos_return"][0]
        first["walk_forward"]["folds"][0]["initial_equity_krw"] = 1
        first["walk_forward"]["folds"][0]["final_equity_krw"] = 999_999_999
        first["walk_forward"]["folds"][0]["sharpe"] = float("nan")
        first["walk_forward"]["folds"][0]["total_return"] = -999
        first["promotion"]["status"] = "PAPER_CANDIDATE"
        first["promotion"]["checks"] = {
            key: True for key in first["promotion"]["checks"]
        }
        payload["validation"]["calendar_folds"][0]["train"][0] = "garbage"
        payload["final_untouched_holdout"]["candidate"] = next(
            name
            for name in {
                row["name"] for row in payload["candidates_ranked_by_oos_return"]
            }
            if name != payload["selection"]["provisional_best_before_holdout"]
        )
        payload["selection"]["status"] = "PAPER_CANDIDATE"
        payload["selection"]["selected_candidate"] = payload["final_untouched_holdout"]["candidate"]

        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "corrupt.json"
            report.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(VALIDATOR), str(report)],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["passed"])


if __name__ == "__main__":
    unittest.main()
