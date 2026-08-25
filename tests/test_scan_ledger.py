import json
import tempfile
import unittest
from pathlib import Path

from bithumb_coin_trader.scan_ledger import (
    ScanLedgerError,
    ScanAuditSnapshot,
    append_scan_snapshot,
    read_scan_snapshots,
)


class ScanLedgerTests(unittest.TestCase):
    def test_snapshot_appends_one_complete_scan_with_explicit_health(self):
        snapshot = ScanAuditSnapshot(
            observed_at="2026-08-24T12:00:02Z",
            scan_id="scan-1",
            scan_started_at="2026-08-24T12:00:00Z",
            scan_completed_at="2026-08-24T12:00:02Z",
            data_timestamp="2026-08-24T11:59:59Z",
            universe_size=2,
            markets_scanned=("KRW-BTC", "KRW-ETH"),
            markets_skipped={"KRW-XRP": "investment-warning"},
            feed_health={
                "warning_feed_ok": True,
                "ticker_feed_ok": True,
                "orderbook_feed_ok": False,
                "mcp_ok": True,
            },
            candidates=(
                {
                    "rank": 1,
                    "market": "KRW-BTC",
                    "score": 0.7,
                    "pass_reasons": ["liquid"],
                    "fail_reasons": ["orderbook-unavailable"],
                },
            ),
            errors=("orderbook timeout",),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshots.jsonl"
            signed = append_scan_snapshot(path, snapshot)
            self.assertEqual(read_scan_snapshots(path), [signed])
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)
            self.assertFalse(signed.feed_health["orderbook_feed_ok"])

            second = append_scan_snapshot(path, snapshot)
            self.assertEqual(second.previous_sha256, signed.canonical_sha256)

            payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            payload["universe_size"] = 99
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ScanLedgerError, "canonical hash"):
                read_scan_snapshots(path)

    def test_snapshot_requires_explicit_feed_health(self):
        with self.assertRaisesRegex(ScanLedgerError, "explicit boolean"):
            ScanAuditSnapshot(
                observed_at="2026-08-24T12:00:02Z",
                scan_id="scan-1",
                scan_started_at="2026-08-24T12:00:00Z",
                scan_completed_at="2026-08-24T12:00:02Z",
                data_timestamp=None,
                universe_size=0,
                markets_scanned=(),
                markets_skipped={},
                feed_health={"warning_feed_ok": True},
                candidates=(),
            )

    def test_snapshot_rejects_non_finite_candidate_evidence(self):
        with self.assertRaisesRegex(ScanLedgerError, "non-finite"):
            ScanAuditSnapshot(
                observed_at="2026-08-24T12:00:02Z",
                scan_id="scan-1",
                scan_started_at="2026-08-24T12:00:00Z",
                scan_completed_at="2026-08-24T12:00:02Z",
                data_timestamp=None,
                universe_size=1,
                markets_scanned=("KRW-BTC",),
                markets_skipped={},
                feed_health={
                    "warning_feed_ok": True,
                    "ticker_feed_ok": True,
                    "orderbook_feed_ok": True,
                    "mcp_ok": True,
                },
                candidates=(
                    {
                        "rank": 1,
                        "market": "KRW-BTC",
                        "score": float("nan"),
                        "pass_reasons": [],
                        "fail_reasons": [],
                    },
                ),
            )


if __name__ == "__main__":
    unittest.main()
