from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from bithumb_coin_trader.cli import main
from bithumb_coin_trader.data import save_candles_csv
from bithumb_coin_trader.models import Candle


def sample_candles(count: int = 700) -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    values: list[Candle] = []
    price = 50_000_000.0
    for index in range(count):
        price *= 1.0005 if (index // 80) % 2 == 0 else 0.9997
        values.append(
            Candle(
                start + timedelta(days=index),
                price,
                price * 1.01,
                price * 0.99,
                price,
                10,
            )
        )
    return values


class CliTests(unittest.TestCase):
    def test_research_outputs_fail_closed_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candles.csv"
            save_candles_csv(path, sample_candles())
            output = StringIO()
            with redirect_stdout(output):
                code = main(["research", "--input", str(path), "--train-size", "400", "--test-size", "100"])
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["dataset"]["candle_count"], 700)
        self.assertEqual(len(payload["dataset"]["sha256"]), 64)
        self.assertEqual(payload["promotion"]["status"], "RESEARCH_ONLY")
        self.assertFalse(payload["promotion"]["checks"]["at_least_six_folds"])

    def test_signal_never_submits_order(self) -> None:
        output = StringIO()
        with patch("bithumb_coin_trader.cli.fetch_daily_candles", return_value=sample_candles(200)):
            with redirect_stdout(output):
                code = main(["signal"])
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertFalse(payload["order_submitted"])

    def test_discord_setup_masks_target_and_test_never_orders(self) -> None:
        output = StringIO()
        crontab = subprocess.CompletedProcess(
            ["crontab", "-l"],
            0,
            "TOSS_MONITOR_DISCORD_TARGET=discord:123456789\n",
            "",
        )
        with (
            patch("bithumb_coin_trader.cli.subprocess.run", return_value=crontab),
            patch(
                "bithumb_coin_trader.cli.save_local_target",
                return_value=Path("/tmp/test.env"),
            ) as save,
            redirect_stdout(output),
        ):
            code = main(["discord-setup"])
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["target"], "discord:<configured>")
        save.assert_called_once()
        self.assertEqual(save.call_args.args, ("discord:123456789",))
        self.assertTrue(save.call_args.kwargs["env_path"].is_absolute())

        output = StringIO()
        with (
            patch("bithumb_coin_trader.cli.DiscordNotifier") as notifier,
            redirect_stdout(output),
        ):
            notifier.return_value.send.return_value = True
            code = main(["discord-test"])
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["sent"])
        self.assertFalse(payload["order_submitted"])

    def test_paper_run_and_status_persist_without_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "paper.json"
            audit = root / "paper.jsonl"
            lock = root / "paper.lock"
            output = StringIO()
            paper_candles = [
                Candle(
                    candle.timestamp.replace(hour=15),
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                    candle.market,
                )
                for candle in sample_candles(200)
            ]
            with (
                patch(
                    "bithumb_coin_trader.cli.fetch_daily_candles",
                    return_value=paper_candles,
                ),
                redirect_stdout(output),
            ):
                code = main(
                    [
                        "paper-run",
                        "--state-path", str(state),
                        "--audit-path", str(audit),
                        "--lock-path", str(lock),
                    ]
                )
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(payload["processed"])
            self.assertEqual(payload["mode"], "paper")
            self.assertFalse(payload["order_submitted"])
            self.assertTrue(state.exists())

            output = StringIO()
            with redirect_stdout(output):
                status_code = main(
                    ["paper-status", "--state-path", str(state), "--audit-path", str(audit)]
                )
            status = json.loads(output.getvalue())
            self.assertEqual(status_code, 0)
            self.assertEqual(status["evidence"]["decision_count"], 1)
            self.assertEqual(status["evidence"]["accounting_mismatches"], 0)
            self.assertFalse(status["order_submitted"])

            extended = [
                Candle(
                    candle.timestamp.replace(hour=15),
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                    candle.market,
                )
                for candle in sample_candles(202)
            ]
            output = StringIO()
            with (
                patch("bithumb_coin_trader.cli.fetch_daily_candles", return_value=extended),
                redirect_stdout(output),
            ):
                main(
                    [
                        "paper-run",
                        "--state-path", str(state),
                        "--audit-path", str(audit),
                        "--lock-path", str(lock),
                    ]
                )
            caught_up = json.loads(output.getvalue())
            self.assertEqual(caught_up["processed_decisions"], 2)
            self.assertEqual(caught_up["state"]["decision_count"], 3)

            records = audit.read_text(encoding="utf-8").splitlines()
            tampered = json.loads(records[-1])
            tampered["equity_krw"] = "999999"
            records[-1] = json.dumps(tampered, separators=(",", ":"))
            audit.write_text("\n".join(records) + "\n", encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                main(["paper-status", "--state-path", str(state), "--audit-path", str(audit)])
            self.assertEqual(
                json.loads(output.getvalue())["evidence"]["accounting_mismatches"],
                1,
            )

    def test_live_readiness_is_not_ready_and_never_orders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "research.json"
            report.write_text('{"promotion":"RESEARCH_ONLY"}', encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "live-readiness",
                        "--research-report", str(report),
                        "--paper-state-path", str(root / "paper.json"),
                        "--paper-audit-path", str(root / "paper.jsonl"),
                        "--live-state-path", str(root / "live.json"),
                    ]
                )
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "NOT_READY")
        self.assertFalse(payload["order_submitted"])

    def test_schedule_install_preserves_crontab_and_is_paper_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / ".venv" / "bin" / "bithumb-trader"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o700)
            reads = subprocess.CompletedProcess(["crontab", "-l"], 0, "5 5 * * * existing\n", "")
            writes = subprocess.CompletedProcess(["crontab", "-"], 0, "", "")
            output = StringIO()
            with (
                patch("bithumb_coin_trader.cli.subprocess.run", side_effect=[reads, writes]) as run,
                redirect_stdout(output),
            ):
                code = main(["paper-schedule-install", "--project-root", str(root)])
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["installed"])
        installed = run.call_args_list[1].kwargs["input"]
        self.assertIn("5 5 * * * existing", installed)
        self.assertIn("paper-run --notify", installed)
        self.assertIn("# bithumb-coin-trader-paper", installed)
        self.assertNotIn("live", installed)


if __name__ == "__main__":
    unittest.main()
