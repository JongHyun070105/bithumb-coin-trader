import io
import json
import tempfile
import unittest
import urllib.error
from email.message import Message
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bithumb_coin_trader.external_news import (
    FINNHUB_ENDPOINT,
    ExternalNewsError,
    FinnhubNewsClient,
    append_news_reference_signals,
    format_news_lines,
    parse_finnhub_news,
    read_news_reference_signals,
)


class _Response:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return self._body


class ExternalNewsTests(unittest.TestCase):
    def payload(self) -> list[dict[str, object]]:
        return [{
            "category": "crypto",
            "datetime": 1787621400,
            "headline": "Bitcoin and Ethereum market update",
            "id": 123,
            "source": "Example Wire",
            "summary": "This field must not be persisted or sent to Discord.",
            "url": "https://news.example.test/article/123",
        }]

    def test_client_keeps_key_out_of_url_and_uses_header(self) -> None:
        captured = {}

        def opener(request, *, timeout):
            captured["url"] = request.full_url
            captured["token"] = request.get_header("X-finnhub-token")
            captured["timeout"] = timeout
            return _Response(self.payload())

        payload = FinnhubNewsClient("secret-token", opener=opener).fetch()
        self.assertEqual(payload, self.payload())
        self.assertEqual(captured["url"], FINNHUB_ENDPOINT)
        self.assertNotIn("secret-token", captured["url"])
        self.assertEqual(captured["token"], "secret-token")

    def test_parses_reference_only_without_provider_summary(self) -> None:
        signals = parse_finnhub_news(
            self.payload(),
            observed_at="2026-08-25T02:00:00Z",
            known_markets=["KRW-BTC", "KRW-ETH", "KRW-XRP"],
        )
        self.assertEqual(signals[0].affected_markets, ("KRW-BTC", "KRW-ETH"))
        self.assertFalse(signals[0].executable)
        self.assertNotIn("summary", signals[0].__dataclass_fields__)
        self.assertNotIn("This field", format_news_lines(signals)[0])

    def test_append_is_deduplicated(self) -> None:
        signals = parse_finnhub_news(
            self.payload(),
            observed_at="2026-08-25T02:00:00Z",
            known_markets=["KRW-BTC", "KRW-ETH"],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.jsonl"
            self.assertEqual(len(append_news_reference_signals(path, signals)), 1)
            self.assertEqual(append_news_reference_signals(path, signals), [])
            self.assertEqual(read_news_reference_signals(path), signals)

    def test_article_id_deduplicates_url_revisions(self) -> None:
        payload = [self.payload()[0], {**self.payload()[0], "url": "https://news.example.test/revised"}]
        signals = parse_finnhub_news(
            payload,
            observed_at="2026-08-25T02:00:00Z",
            known_markets=["KRW-BTC", "KRW-ETH"],
        )
        self.assertEqual(len(signals), 1)

    def test_rejects_implausible_future_and_stale_articles(self) -> None:
        observed = datetime(2026, 8, 25, 2, 0, tzinfo=UTC)
        future = int((observed + timedelta(minutes=6)).timestamp())
        stale = int((observed - timedelta(hours=49)).timestamp())
        payload = [
            {**self.payload()[0], "id": 1, "datetime": future},
            {**self.payload()[0], "id": 2, "datetime": stale},
        ]
        self.assertEqual(
            parse_finnhub_news(
                payload,
                observed_at=observed.isoformat(),
                known_markets=["KRW-BTC"],
            ),
            [],
        )

    def test_discord_line_neutralizes_mentions_and_markdown(self) -> None:
        payload = [{
            **self.payload()[0],
            "headline": "@everyone **Bitcoin** update",
            "source": "[Wire]",
        }]
        signal = parse_finnhub_news(
            payload,
            observed_at="2026-08-25T02:00:00Z",
            known_markets=["KRW-BTC"],
        )[0]
        line = format_news_lines([signal])[0]
        self.assertNotIn("@everyone", line)
        self.assertIn("@\u200beveryone", line)
        self.assertIn("<https://news.example.test/article/123>", line)

    def test_http_error_is_sanitized(self) -> None:
        def opener(_request, *, timeout):
            headers = Message()
            raise urllib.error.HTTPError(
                "https://example.test/?token=secret-token", 401, "bad", headers, io.BytesIO()
            )

        with self.assertRaisesRegex(ExternalNewsError, r"Finnhub HTTP failure \(401\)") as raised:
            FinnhubNewsClient("secret-token", opener=opener).fetch()
        self.assertNotIn("secret-token", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
