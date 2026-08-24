from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bithumb_coin_trader.state import BotState, append_event, load_state, save_state


class StateTests(unittest.TestCase):
    def test_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            expected = BotState(
                position="long",
                position_volume="0.25",
                active_client_order_id="btc-20240101",
                pending_order_side="ask",
                pending_market="KRW-BTC",
                pending_order_volume="0.10",
            )
            save_state(path, expected)
            self.assertEqual(load_state(path), expected)

    def test_position_volume_is_exact_and_consistent(self) -> None:
        with self.assertRaisesRegex(ValueError, "exact decimal"):
            BotState(position_volume=0.1)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "zero"):
            BotState(position="flat", position_volume="0.1")
        with self.assertRaisesRegex(ValueError, "positive"):
            BotState(position="long", position_volume="0")
        with self.assertRaisesRegex(ValueError, "pending_order_side"):
            BotState(active_client_order_id="order-without-metadata")
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            BotState(
                position="long",
                position_volume="0.1",
                active_client_order_id="too-large-sell",
                pending_order_side="ask",
                pending_market="KRW-BTC",
                pending_order_volume="0.2",
            )

    def test_unknown_state_fields_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"version": 1, "surprise": true}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown"):
                load_state(path)

    def test_position_policy_is_retained_only_while_long(self) -> None:
        state = BotState(
            position="long",
            position_volume="0.1",
            position_policy_version=1,
        )
        self.assertEqual(state.position_policy_version, 1)
        with self.assertRaisesRegex(ValueError, "flat state"):
            BotState(position_policy_version=1)

    def test_event_journal_is_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            append_event(path, "signal", {"side": "long"})
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["event"], "signal")


if __name__ == "__main__":
    unittest.main()
