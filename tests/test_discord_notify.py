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
    format_hourly_briefing,
    format_trade_notification,
    save_local_target,
    send_discord_message,
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

    def test_hourly_briefing_is_mode_aware_and_has_no_personal_defaults(self) -> None:
        message = format_hourly_briefing(
            total_capital=51_000,
            cash_available=51_000,
            active_market="",
            active_price=0,
            entry_price=0,
            active_pnl_pct=0,
            active_val_krw=0,
            top_candidates=[],
            target_capital=75_000,
            winning_trades=1,
            losing_trades=2,
            total_pnl_krw=1_000,
            initial_capital=50_000,
            runtime_mode="live",
            new_entries_enabled=False,
            reconciliation_healthy=True,
            scan_status={"healthy": False, "detail": "MCP timeout"},
        )
        self.assertIn("감시 전용 · 신규 진입 잠금", message)
        self.assertIn("MCP timeout", message)
        self.assertIn("원금: `50,000 KRW`", message)
        self.assertNotIn("9/1", message)
        self.assertNotIn("45,000", message)
        self.assertNotIn("무중단 자율 운용", message)

    def test_hourly_briefing_requires_explicit_positive_baseline(self) -> None:
        with self.assertRaises(ValueError):
            format_hourly_briefing(
                total_capital=1,
                cash_available=1,
                active_market="",
                active_price=0,
                entry_price=0,
                active_pnl_pct=0,
                active_val_krw=0,
                top_candidates=[],
                target_capital=2,
                winning_trades=0,
                losing_trades=0,
                total_pnl_krw=0,
                initial_capital=0,
                runtime_mode="monitoring",
                new_entries_enabled=False,
                reconciliation_healthy=False,
            )

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

    @patch("bithumb_coin_trader.discord_notify.subprocess.run")
    def test_rich_send_does_not_inherit_exchange_or_model_keys(self, run) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        with patch.dict(
            "os.environ",
            {
                "BITHUMB_ACCESS_KEY": "secret-access",
                "BITHUMB_SECRET_KEY": "secret-signing",
                "GEMINI_API_KEY": "secret-model",
            },
            clear=False,
        ):
            self.assertTrue(
                send_discord_message(
                    "unique secure delivery test", target="discord:123456789"
                )
            )

        environment = run.call_args.kwargs["env"]
        self.assertNotIn("BITHUMB_ACCESS_KEY", environment)
        self.assertNotIn("BITHUMB_SECRET_KEY", environment)
        self.assertNotIn("GEMINI_API_KEY", environment)
        self.assertLessEqual(set(environment), {"HOME", "PATH", "LANG", "LC_ALL", "TMPDIR"})


if __name__ == "__main__":
    unittest.main()
