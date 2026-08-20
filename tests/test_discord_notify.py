from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from bithumb_coin_trader.discord_notify import (
    DiscordNotifier,
    TradeEvent,
    TradeNotification,
    configured_discord_target,
    format_trade_notification,
    save_local_target,
    target_from_crontab,
)


class DiscordConfigurationTests(unittest.TestCase):
    def test_target_resolves_from_local_env_without_exposing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("BITHUMB_DISCORD_TARGET=discord:123456789\n", encoding="utf-8")
            self.assertEqual(
                configured_discord_target(env={}, env_path=path),
                "discord:123456789",
            )

    def test_crontab_target_is_copied_atomically_and_preserves_other_keys(self) -> None:
        target = target_from_crontab(
            "PATH=/usr/bin\nTOSS_MONITOR_DISCORD_TARGET=discord:123456789\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("BITHUMB_ACCESS_KEY=keep-me\n", encoding="utf-8")
            save_local_target(target, env_path=path)
            saved = path.read_text(encoding="utf-8")
            self.assertIn("BITHUMB_ACCESS_KEY=keep-me", saved)
            self.assertIn("BITHUMB_DISCORD_TARGET=discord:123456789", saved)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_target_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            configured_discord_target(env={"BITHUMB_DISCORD_TARGET": "https://example.com"})
        with self.assertRaises(ValueError):
            target_from_crontab("TOSS_MONITOR_DISCORD_TARGET=not-discord")
        with self.assertRaisesRegex(ValueError, "absolute"):
            DiscordNotifier(target="discord:123456789", hermes_bin="relative/hermes")


class DiscordDeliveryTests(unittest.TestCase):
    def notification(self, event: TradeEvent = TradeEvent.FILLED) -> TradeNotification:
        return TradeNotification(
            event=event,
            market="KRW-BTC",
            side="bid",
            client_order_id="intent-123",
            notional_krw="10000",
            detail="test",
            occurred_at=datetime(2026, 8, 10, tzinfo=UTC),
        )

    def test_formatter_distinguishes_acceptance_from_fill(self) -> None:
        accepted = format_trade_notification(self.notification(TradeEvent.ACCEPTED))
        ambiguous = format_trade_notification(self.notification(TradeEvent.AMBIGUOUS))
        paper = format_trade_notification(self.notification(TradeEvent.PAPER))
        self.assertIn("접수 (체결 아님)", accepted)
        self.assertIn("자동 재시도 금지", ambiguous)
        self.assertIn("실주문 없음", paper)
        self.assertNotIn("ACCESS_KEY", accepted)

    @patch("bithumb_coin_trader.discord_notify.subprocess.run")
    def test_send_uses_hermes_file_and_returns_success(self, run) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        notifier = DiscordNotifier(
            target="discord:123456789", hermes_bin="/usr/local/bin/hermes"
        )
        self.assertTrue(notifier.send(self.notification()))
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["/usr/local/bin/hermes", "send", "--quiet"])
        self.assertEqual(command[command.index("--to") + 1], "discord:123456789")
        self.assertFalse(Path(command[command.index("--file") + 1]).exists())
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["cwd"], tempfile.gettempdir())
        self.assertNotIn("BITHUMB_ACCESS_KEY", kwargs["env"])
        self.assertNotIn("BITHUMB_SECRET_KEY", kwargs["env"])
        self.assertLessEqual(set(kwargs["env"]), {"HOME", "PATH", "LANG", "LC_ALL", "TMPDIR"})

    @patch("bithumb_coin_trader.discord_notify.subprocess.run", side_effect=OSError("offline"))
    def test_delivery_failure_is_best_effort(self, _run) -> None:
        notifier = DiscordNotifier(
            target="discord:123456789", hermes_bin="/missing/hermes"
        )
        self.assertFalse(notifier.send(self.notification()))


if __name__ == "__main__":
    unittest.main()
