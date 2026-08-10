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


if __name__ == "__main__":
    unittest.main()
