from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Sequence, cast
from unittest.mock import MagicMock, patch

from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.weekly_research import run_weekly_research
from bithumb_coin_trader.weekly_research import WeeklyResearchResult
from scripts import run_weekly_research as weekly_cli


class WeeklyResearchTests(unittest.TestCase):
    def test_cli_returns_nonzero_when_research_fails(self) -> None:
        failed = WeeklyResearchResult(
            week="2026-W35",
            status="validation_failed",
            validation_passed=False,
            research_candidate="cash",
            can_promote=False,
            artifact_dir="state/research/2026-W35",
            detail="validator failed",
        )
        with patch.object(weekly_cli, "run_weekly_research", return_value=failed):
            self.assertEqual(weekly_cli.main(), 1)

    @staticmethod
    def fetch_stub(*_args: object) -> Sequence[Candle]:
        candle = cast(Candle, MagicMock(spec=Candle))
        return (candle,) * 45_000

    @staticmethod
    def save_stub(path: str | Path, _candles: Iterable[Candle]) -> None:
        Path(path).write_text("dataset", encoding="utf-8")

    def test_validated_weekly_run_is_idempotent_and_never_promotes(self) -> None:
        notifications: list[str] = []
        environments: list[dict[str, str]] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            environments.append(dict(kwargs["env"]))  # type: ignore[arg-type]
            if "run_winrate_research.py" in command[1]:
                output = Path(command[command.index("--output") + 1])
                report = Path(command[command.index("--report") + 1])
                payload = {"selection": {"research_candidate": "cash", "can_promote": False}}
                output.write_text(json.dumps(payload), encoding="utf-8")
                report.write_text(json.dumps(payload), encoding="utf-8")
            else:
                validation = Path(command[command.index("--output") + 1])
                validation.write_text(json.dumps({"passed": True}), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            outcome = run_weekly_research(
                root,
                observed_at=datetime(2026, 8, 25, tzinfo=UTC),
                fetcher=self.fetch_stub,
                saver=self.save_stub,
                command_runner=runner,
                notify=lambda message: not notifications.append(message),
            )
            duplicate = run_weekly_research(
                root,
                observed_at=datetime(2026, 8, 26, tzinfo=UTC),
                fetcher=lambda *_args: self.fail("duplicate must not refetch"),
                command_runner=runner,
                notify=lambda _message: True,
            )

        self.assertEqual(outcome.status, "completed")
        self.assertTrue(outcome.validation_passed)
        self.assertFalse(outcome.can_promote)
        self.assertEqual(duplicate.status, "skipped_duplicate")
        self.assertIn("실거래 자동 승격: `금지`", notifications[0])
        self.assertTrue(all("BITHUMB_ACCESS_KEY" not in env for env in environments))
        self.assertTrue(all(env["BITHUMB_NEW_ENTRIES"] == "false" for env in environments))

    def test_promotable_result_is_rejected_by_automation(self) -> None:
        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            output = Path(command[command.index("--output") + 1])
            if "run_winrate_research.py" in command[1]:
                report = Path(command[command.index("--report") + 1])
                payload = {"selection": {"research_candidate": "candidate-x", "can_promote": True}}
                output.write_text(json.dumps(payload), encoding="utf-8")
                report.write_text(json.dumps(payload), encoding="utf-8")
            else:
                output.write_text(json.dumps({"passed": True}), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            outcome = run_weekly_research(
                root,
                observed_at=datetime(2026, 9, 1, tzinfo=UTC),
                fetcher=self.fetch_stub,
                saver=self.save_stub,
                command_runner=runner,
                notify=lambda _message: True,
            )
        self.assertEqual(outcome.status, "failed")
        self.assertFalse(outcome.can_promote)
        self.assertIn("refuses promotable output", outcome.detail)


if __name__ == "__main__":
    unittest.main()
