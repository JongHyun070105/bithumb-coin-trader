from __future__ import annotations

from contextlib import redirect_stderr
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_winrate_research as runner


def report(*, passed: list[str], opened: bool = False) -> dict[str, object]:
    evaluated = passed[:1] if opened else []
    return {
        "generated_at": "2026-08-24T12:00:00+00:00",
        "dataset": {"sha256": "dataset-hash"},
        "candidate_manifest": {"sha256": "candidate-hash"},
        "protocol": {"sealed_holdout": {"maximum_candidates": 1}},
        "development": {"passed_candidates": passed},
        "sealed_holdout": {"evaluated_candidates": evaluated},
        "selection": {
            "research_candidate": evaluated[0] if evaluated else "cash",
            "historical_target_met": bool(evaluated),
        },
    }


class WinRateRunnerTests(unittest.TestCase):
    def run_main(
        self,
        directory: Path,
        reports: list[dict[str, object]],
        *,
        open_holdout: bool = False,
        ledger_contents: str | None = None,
    ) -> tuple[int, object, Path]:
        output = directory / "result.json"
        mirror = directory / "report.json"
        ledger = directory / "holdout-ledger.json"
        if ledger_contents is not None:
            ledger.write_text(ledger_contents, encoding="utf-8")
        argv = [
            "--input",
            str(directory / "candles.csv"),
            "--output",
            str(output),
            "--report",
            str(mirror),
        ]
        if open_holdout:
            argv.append("--open-holdout")
        with (
            patch.object(runner, "DEFAULT_HOLDOUT_LEDGER", ledger),
            patch.object(runner, "load_candles_csv", return_value=[object()]),
            patch.object(runner, "build_report", side_effect=reports) as build,
        ):
            result = runner.main(argv)
        self.assertEqual(output.read_bytes(), mirror.read_bytes())
        return result, build, ledger

    def test_default_run_never_opens_holdout(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            result, build, ledger = self.run_main(
                Path(raw_directory), [report(passed=["candidate-a"])]
            )
            self.assertFalse(ledger.exists())
        self.assertEqual(result, 0)
        build.assert_called_once()
        self.assertFalse(build.call_args.kwargs["evaluate_holdout"])

    def test_open_flag_with_no_pass_creates_no_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            result, build, ledger = self.run_main(
                Path(raw_directory), [report(passed=[])], open_holdout=True
            )
            self.assertFalse(ledger.exists())
        self.assertEqual(result, 0)
        build.assert_called_once()

    def test_first_explicit_open_creates_opened_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            result, build, ledger = self.run_main(
                Path(raw_directory),
                [report(passed=["candidate-a"]), report(passed=["candidate-a"], opened=True)],
                open_holdout=True,
            )
            payload = json.loads(ledger.read_text(encoding="utf-8"))
        self.assertEqual(result, 0)
        self.assertEqual(build.call_count, 2)
        self.assertFalse(build.call_args_list[0].kwargs["evaluate_holdout"])
        self.assertTrue(build.call_args_list[1].kwargs["evaluate_holdout"])
        self.assertEqual(payload["state"], "opened")
        self.assertEqual(payload["finalists"], ["candidate-a"])
        self.assertEqual(payload["evaluated_candidates"], ["candidate-a"])
        self.assertIn("report_sha256", payload)

    def test_existing_opening_ledger_refuses_before_holdout_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            ledger = directory / "holdout-ledger.json"
            ledger.write_text('{"state":"opening"}\n', encoding="utf-8")
            with self.assertRaisesRegex(
                runner.HoldoutLedgerExistsError, "already exists"
            ):
                with (
                    patch.object(runner, "load_candles_csv", return_value=[object()]),
                    patch.object(
                        runner,
                        "build_report",
                        return_value=report(passed=["candidate-a"]),
                    ) as build,
                    patch.object(runner, "DEFAULT_HOLDOUT_LEDGER", ledger),
                ):
                    runner.main(
                        [
                            "--input",
                            str(directory / "candles.csv"),
                            "--output",
                            str(directory / "result.json"),
                            "--report",
                            str(directory / "report.json"),
                            "--open-holdout",
                        ]
                    )
            build.assert_not_called()

    def test_existing_opened_ledger_also_refuses_before_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            ledger = directory / "holdout-ledger.json"
            ledger.write_text('{"state":"opened"}\n', encoding="utf-8")
            with self.assertRaises(runner.HoldoutLedgerExistsError):
                with (
                    patch.object(runner, "load_candles_csv", return_value=[object()]),
                    patch.object(
                        runner,
                        "build_report",
                        return_value=report(passed=["candidate-a"]),
                    ) as build,
                    patch.object(runner, "DEFAULT_HOLDOUT_LEDGER", ledger),
                ):
                    runner.main(
                        [
                            "--input",
                            str(directory / "candles.csv"),
                            "--output",
                            str(directory / "result.json"),
                            "--report",
                            str(directory / "report.json"),
                        ]
                    )
            build.assert_not_called()

    def test_crashed_evaluation_leaves_opening_ledger_and_retry_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            ledger = directory / "holdout-ledger.json"
            argv = [
                "--input",
                str(directory / "candles.csv"),
                "--output",
                str(directory / "result.json"),
                "--report",
                str(directory / "report.json"),
                "--open-holdout",
            ]
            with (
                patch.object(runner, "DEFAULT_HOLDOUT_LEDGER", ledger),
                patch.object(runner, "load_candles_csv", return_value=[object()]),
                patch.object(
                    runner,
                    "build_report",
                    side_effect=[report(passed=["candidate-a"]), RuntimeError("crash")],
                ),
                self.assertRaisesRegex(RuntimeError, "crash"),
            ):
                runner.main(argv)
            self.assertEqual(
                json.loads(ledger.read_text(encoding="utf-8"))["state"], "opening"
            )

            with (
                patch.object(runner, "DEFAULT_HOLDOUT_LEDGER", ledger),
                patch.object(runner, "load_candles_csv", return_value=[object()]),
                patch.object(
                    runner,
                    "build_report",
                    return_value=report(passed=["candidate-a"]),
                ) as retry_build,
                self.assertRaises(runner.HoldoutLedgerExistsError),
            ):
                runner.main(argv)
            retry_build.assert_not_called()

    def test_cli_cannot_select_a_second_ledger_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            canonical = directory / "holdout-ledger.json"
            alternate = directory / "alternate-ledger.json"
            canonical.write_text('{"state":"opened"}\n', encoding="utf-8")
            with (
                patch.object(runner, "DEFAULT_HOLDOUT_LEDGER", canonical),
                patch.object(runner, "load_candles_csv") as load,
                patch.object(runner, "build_report") as build,
                redirect_stderr(StringIO()),
                self.assertRaises(SystemExit),
            ):
                runner.main(
                    [
                        "--input",
                        str(directory / "candles.csv"),
                        "--output",
                        str(directory / "result.json"),
                        "--report",
                        str(directory / "report.json"),
                        "--open-holdout",
                        "--holdout-ledger",
                        str(alternate),
                    ]
                )
            load.assert_not_called()
            build.assert_not_called()
            self.assertFalse(alternate.exists())


if __name__ == "__main__":
    unittest.main()
