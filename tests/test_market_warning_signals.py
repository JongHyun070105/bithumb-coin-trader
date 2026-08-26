import json
import tempfile
import unittest
from pathlib import Path

from bithumb_coin_trader.market_warning_signals import (
    append_market_warning_snapshot,
    format_warning_lines,
    parse_market_warning_snapshot,
    read_market_warning_snapshots,
)


class MarketWarningSignalTests(unittest.TestCase):
    def payload(self) -> dict[str, object]:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "data": {"data": [
                        {
                            "market": "KRW-SAND",
                            "warning_type": "PRICE_DIFFERENCE_HIGH",
                            "warning_step": "DANGER",
                            "end_date": "2026-08-25 14:11:59",
                        },
                        {
                            "market": "KRW-STX",
                            "warning_type": "DEPOSIT_AMOUNT_SUDDEN_FLUCTUATION",
                            "warning_step": "WARNING",
                            "end_date": "2026-08-26 01:59:59",
                        },
                    ]}
                }),
            }]
        }

    def test_parses_official_warning_type_step_and_kst_end_time(self) -> None:
        snapshot = parse_market_warning_snapshot(
            self.payload(), observed_at="2026-08-25T01:00:00Z"
        )
        self.assertEqual(len(snapshot.warnings), 2)
        self.assertEqual(snapshot.warnings[0].end_at, "2026-08-25T05:11:59Z")
        self.assertFalse(snapshot.executable)
        self.assertIn("DANGER", format_warning_lines(snapshot)[0])

    def test_appends_only_when_warning_state_changes(self) -> None:
        first = parse_market_warning_snapshot(
            self.payload(), observed_at="2026-08-25T01:00:00Z"
        )
        same_state = parse_market_warning_snapshot(
            self.payload(), observed_at="2026-08-25T01:10:00Z"
        )
        cleared = parse_market_warning_snapshot([], observed_at="2026-08-25T01:20:00Z")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "warnings.jsonl"
            self.assertTrue(append_market_warning_snapshot(path, first))
            self.assertFalse(append_market_warning_snapshot(path, same_state))
            self.assertTrue(append_market_warning_snapshot(path, cleared))
            loaded = read_market_warning_snapshots(path)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[-1].warnings, ())


if __name__ == "__main__":
    unittest.main()
