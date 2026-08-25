import json
import tempfile
import unittest
from pathlib import Path

from bithumb_coin_trader.reference_signals import (
    ReferenceSignalError,
    append_reference_signals,
    build_notice_digest,
    parse_bithumb_notices,
    read_reference_signals,
)


class ReferenceSignalTests(unittest.TestCase):
    def test_official_naive_kst_timestamp_and_pc_url_are_normalized(self) -> None:
        signals = parse_bithumb_notices(
            {"data": {"data": [{
                "title": "BTC 입출금 일시 중단",
                "published_at": "2026-08-24 18:30:00",
                "pc_url": "https://feed.bithumb.com/notice/1",
            }]}},
            observed_at="2026-08-25T00:00:00+00:00",
        )
        self.assertEqual(signals[0].published_at, "2026-08-24T09:30:00Z")
        self.assertEqual(signals[0].url, "https://feed.bithumb.com/notice/1")
        self.assertFalse(signals[0].executable)

    def payload(self):
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "data": {
                                "data": [
                                    {
                                        "id": 101,
                                        "title": "[투자유의] 비트코인 (BTC, ETH)",
                                        "created_at": "2026-08-24T11:00:00Z",
                                        "url": "https://example.test/101",
                                    },
                                    {
                                        "id": 102,
                                        "title": "KRW-XRP 입출금 일시 중단 안내",
                                        "created_at": "2026-08-24T12:00:00+00:00",
                                    },
                                ]
                            }
                        }
                    ),
                }
            ]
        }

    def test_parses_nested_mcp_payload_as_reference_only(self):
        signals = parse_bithumb_notices(self.payload(), observed_at="2026-08-24T22:00:00+09:00")
        self.assertEqual(len(signals), 2)
        self.assertEqual(signals[0].category, "investment_warning")
        self.assertEqual(signals[0].affected_markets, ("KRW-BTC", "KRW-ETH"))
        self.assertFalse(signals[0].executable)
        self.assertEqual(signals[0].observed_at, "2026-08-24T13:00:00Z")
        self.assertEqual(signals[1].category, "transfer_status")
        self.assertEqual(signals[1].affected_markets, ("KRW-XRP",))

    def test_deduplicates_within_payload_and_across_append(self):
        payload = {"data": [self.payload_item(), self.payload_item()]}
        signals = parse_bithumb_notices(payload, observed_at="2026-08-24T13:00:00Z")
        self.assertEqual(len(signals), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notices.jsonl"
            self.assertEqual(len(append_reference_signals(path, signals)), 1)
            self.assertEqual(append_reference_signals(path, signals), [])
            self.assertEqual(read_reference_signals(path), signals)

    def test_identity_is_stable_across_observation_times(self):
        first = parse_bithumb_notices(
            {"data": [self.payload_item()]}, observed_at="2026-08-24T13:00:00Z"
        )[0]
        second = parse_bithumb_notices(
            {"data": [self.payload_item()]}, observed_at="2026-08-25T13:00:00Z"
        )[0]
        self.assertEqual(first.identity_sha256, second.identity_sha256)

    def test_digest_is_order_independent_and_human_readable(self):
        signals = parse_bithumb_notices(self.payload(), observed_at="2026-08-24T13:00:00Z")
        forward = build_notice_digest(signals)
        reverse = build_notice_digest(reversed(signals))
        self.assertEqual(forward.identity_sha256, reverse.identity_sha256)
        self.assertEqual(forward.signal_count, 2)
        self.assertEqual(forward.affected_markets, ("KRW-BTC", "KRW-ETH", "KRW-XRP"))
        self.assertIn("investment_warning=1", forward.summary)

    def test_rejects_bad_wrapper_and_tampered_identity(self):
        with self.assertRaisesRegex(ReferenceSignalError, "wrapper"):
            parse_bithumb_notices({"unexpected": []}, observed_at="2026-08-24T13:00:00Z")
        signal = parse_bithumb_notices(
            {"data": [self.payload_item()]}, observed_at="2026-08-24T13:00:00Z"
        )[0]
        payload = signal.__dict__ if hasattr(signal, "__dict__") else {
            field: getattr(signal, field) for field in signal.__dataclass_fields__
        }
        payload["identity_sha256"] = "f" * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notices.jsonl"
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ReferenceSignalError, "identity digest"):
                read_reference_signals(path)

    @staticmethod
    def payload_item():
        return {
            "id": "notice-1",
            "title": "신규 거래지원 안내 (ABC)",
            "created_at": "2026-08-24T12:00:00Z",
        }


if __name__ == "__main__":
    unittest.main()
