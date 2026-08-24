from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from bithumb_coin_trader.data import (
    Candle,
    DataError,
    aggregate_candles,
    dataset_manifest,
    fetch_daily_candles,
    fetch_minute_candles,
    load_candles_csv,
    save_candles_csv,
)
from bithumb_coin_trader.research import run_chronological_research, walk_forward
from bithumb_coin_trader.strategy import StrategyParameters


def api_candle(day: int, close: float = 100.0) -> dict[str, object]:
    return {
        "market": "KRW-BTC",
        "candle_date_time_utc": f"2026-01-{day:02d}T00:00:00",
        "candle_date_time_kst": f"2026-01-{day:02d}T09:00:00",
        "opening_price": close - 1,
        "high_price": close + 2,
        "low_price": close - 2,
        "trade_price": close,
        "candle_acc_trade_volume": 12.5,
    }


def api_minute(timestamp: datetime, close: float = 100.0) -> dict[str, object]:
    raw = api_candle(1, close)
    raw["candle_date_time_utc"] = timestamp.astimezone(timezone.utc).replace(
        tzinfo=None
    ).isoformat(timespec="seconds")
    raw["candle_date_time_kst"] = timestamp.astimezone(
        timezone(timedelta(hours=9))
    ).replace(tzinfo=None).isoformat(timespec="seconds")
    return raw


class FetchDailyCandlesTests(unittest.TestCase):
    def test_fetch_uses_get_timeout_and_returns_chronological_candles(self) -> None:
        calls = []

        def transport(request, timeout):
            calls.append((request, timeout))
            return json.dumps([api_candle(3), api_candle(2), api_candle(1)]).encode()

        candles = fetch_daily_candles("KRW-BTC", 3, timeout=4.5, transport=transport)

        self.assertEqual([c.timestamp.day for c in candles], [1, 2, 3])
        self.assertEqual(calls[0][0].get_method(), "GET")
        self.assertEqual(calls[0][1], 4.5)
        query = parse_qs(urlparse(calls[0][0].full_url).query)
        self.assertEqual(query, {"market": ["KRW-BTC"], "count": ["3"]})
        self.assertIsNone(calls[0][0].get_header("Authorization"))

    def test_paginates_with_exclusive_oldest_cursor(self) -> None:
        requested_queries = []
        first_page = [api_candle(day) for day in range(31, 0, -1)]
        # Duplicate day 1 simulates an API boundary overlap; it is deduplicated.
        second_page = [api_candle(1)] + [api_candle(day, 200.0) for day in range(30, 0, -1)]

        def transport(request, _timeout):
            requested_queries.append(parse_qs(urlparse(request.full_url).query))
            return json.dumps(first_page if len(requested_queries) == 1 else second_page).encode()

        candles = fetch_daily_candles("KRW-BTC", 201, transport=transport)

        # Short first page signals history exhaustion, so no second request.
        self.assertEqual(len(candles), 31)
        self.assertEqual(len(requested_queries), 1)

    def test_paginates_when_page_is_full(self) -> None:
        requested_queries = []
        first_page = []
        for index in range(200):
            candle = api_candle(1)
            candle["candle_date_time_utc"] = f"2026-01-01T{(199-index)//60:02d}:{(199-index)%60:02d}:00"
            first_page.append(candle)
        second = api_candle(1, 90.0)
        second["candle_date_time_utc"] = "2025-12-31T23:59:00"

        def transport(request, _timeout):
            requested_queries.append(parse_qs(urlparse(request.full_url).query))
            page = first_page if len(requested_queries) == 1 else [second]
            return json.dumps(page).encode()

        candles = fetch_daily_candles("KRW-BTC", 201, transport=transport)

        self.assertEqual(len(candles), 201)
        self.assertEqual(requested_queries[0]["count"], ["200"])
        self.assertEqual(requested_queries[1]["count"], ["1"])
        self.assertEqual(requested_queries[1]["to"], ["2026-01-01T09:00:00"])

    def test_rejects_bad_schema_and_insecure_endpoint(self) -> None:
        with self.assertRaisesRegex(DataError, "missing fields"):
            fetch_daily_candles("KRW-BTC", 1, transport=lambda _request, _timeout: b"[{}]")
        with self.assertRaisesRegex(DataError, "HTTPS"):
            fetch_daily_candles(
                "KRW-BTC",
                1,
                endpoint="http://api.bithumb.com/v1/candles/days",
                transport=lambda _request, _timeout: b"[]",
            )

    def test_historical_as_of_starts_at_completed_daily_boundary(self) -> None:
        previous = api_candle(9)
        previous["candle_date_time_utc"] = "2026-08-09T00:00:00"
        queries = []

        def transport(request, _timeout):
            queries.append(parse_qs(urlparse(request.full_url).query))
            return json.dumps([previous]).encode()

        candles = fetch_daily_candles(
            "KRW-BTC",
            1,
            as_of=datetime(2026, 8, 10, 3, tzinfo=timezone.utc),
            transport=transport,
        )

        self.assertEqual([candle.timestamp.day for candle in candles], [9])
        self.assertEqual(len(queries), 1)
        self.assertEqual(queries[0]["to"], ["2026-08-10T00:00:00"])

    def test_historical_as_of_paginates_until_requested_count(self) -> None:
        boundary = datetime(2020, 1, 1, tzinfo=timezone.utc)
        pages = []
        for page_index, page_size in enumerate((200, 200, 1)):
            page = []
            offset = page_index * 200
            for item_index in range(page_size):
                candle = api_candle(1)
                timestamp = boundary - timedelta(days=offset + item_index + 1)
                candle["candle_date_time_utc"] = timestamp.isoformat().replace("+00:00", "")
                page.append(candle)
            pages.append(page)
        queries = []

        def transport(request, _timeout):
            queries.append(parse_qs(urlparse(request.full_url).query))
            return json.dumps(pages[len(queries) - 1]).encode()

        candles = fetch_daily_candles(
            "KRW-BTC",
            401,
            as_of=boundary,
            transport=transport,
        )

        self.assertEqual(len(candles), 401)
        self.assertEqual(len(queries), 3)
        self.assertEqual(queries[0]["to"], ["2020-01-01T00:00:00"])

    def test_fetch_rejects_non_krw_market_without_http(self) -> None:
        called = False

        def transport(_request, _timeout):
            nonlocal called
            called = True
            return b"[]"

        with self.assertRaisesRegex(DataError, "KRW market"):
            fetch_daily_candles("BTC-ETH", 1, transport=transport)
        self.assertFalse(called)


class FetchMinuteCandlesTests(unittest.TestCase):
    def test_excludes_newest_incomplete_candle_and_moves_kst_cursor(self) -> None:
        as_of = datetime(2026, 1, 1, 3, 45, tzinfo=timezone.utc)
        pages = [
            [
                api_minute(datetime(2026, 1, 1, 3, 30, tzinfo=timezone.utc), 103.0),
                api_minute(datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc), 102.0),
            ],
            [api_minute(datetime(2026, 1, 1, 2, 30, tzinfo=timezone.utc), 101.0)],
        ]
        queries = []

        def transport(request, _timeout):
            queries.append(parse_qs(urlparse(request.full_url).query))
            return json.dumps(pages[len(queries) - 1]).encode()

        candles = fetch_minute_candles(
            "KRW-BTC", 30, 2, as_of=as_of, transport=transport
        )

        self.assertEqual(
            [candle.timestamp for candle in candles],
            [
                datetime(2026, 1, 1, 2, 30, tzinfo=timezone.utc),
                datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc),
            ],
        )
        self.assertEqual(queries[0]["to"], ["2026-01-01T12:30:00"])
        self.assertEqual(queries[1]["to"], ["2026-01-01T12:00:00"])

    def test_supports_60_minutes_and_explicit_incomplete_data(self) -> None:
        captured_urls = []
        partial = api_minute(datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc))

        def transport(request, _timeout):
            captured_urls.append(request.full_url)
            return json.dumps([partial]).encode()

        candles = fetch_minute_candles(
            "KRW-BTC",
            60,
            1,
            as_of=datetime(2026, 1, 1, 3, 15, tzinfo=timezone.utc),
            include_incomplete=True,
            transport=transport,
        )

        self.assertEqual(len(candles), 1)
        self.assertIn("/minutes/60?", captured_urls[0])
        self.assertNotIn("to=", captured_urls[0])

    def test_rejects_unsupported_unit_without_http(self) -> None:
        called = False

        def transport(_request, _timeout):
            nonlocal called
            called = True
            return b"[]"

        with self.assertRaisesRegex(DataError, "minute unit"):
            fetch_minute_candles("KRW-BTC", 2, 1, transport=transport)
        self.assertFalse(called)


class AggregateCandlesTests(unittest.TestCase):
    @staticmethod
    def candles(count: int, *, start_kst: datetime | None = None) -> list[Candle]:
        start = start_kst or datetime(
            2026, 1, 1, tzinfo=timezone(timedelta(hours=9))
        )
        return [
            Candle(
                market="KRW-BTC",
                timestamp=start + timedelta(minutes=30 * index),
                open=100.0 + index,
                high=102.0 + index,
                low=99.0 + index,
                close=101.0 + index,
                volume=1.0 + index,
            )
            for index in range(count)
        ]

    def test_aggregates_only_complete_four_hour_buckets(self) -> None:
        source = self.candles(9)

        aggregated = aggregate_candles(
            source,
            30,
            240,
            as_of=datetime(2026, 1, 1, 4, 30, tzinfo=timezone(timedelta(hours=9))),
        )

        self.assertEqual(len(aggregated), 1)
        candle = aggregated[0]
        self.assertEqual(candle.timestamp, datetime(2025, 12, 31, 15, tzinfo=timezone.utc))
        self.assertEqual((candle.open, candle.high, candle.low, candle.close), (100.0, 109.0, 99.0, 108.0))
        self.assertEqual(candle.volume, sum(1.0 + index for index in range(8)))

    def test_omits_gapped_bucket_and_aligns_completed_kst_day(self) -> None:
        source = self.candles(48)
        gapped = source[:10] + source[11:]

        self.assertEqual(
            aggregate_candles(
                gapped,
                30,
                1440,
                as_of=datetime(2026, 1, 2, tzinfo=timezone(timedelta(hours=9))),
            ),
            [],
        )
        daily = aggregate_candles(
            source,
            30,
            1440,
            as_of=datetime(2026, 1, 2, tzinfo=timezone(timedelta(hours=9))),
        )
        self.assertEqual([c.timestamp for c in daily], [datetime(2025, 12, 31, 15, tzinfo=timezone.utc)])

    def test_rejects_bad_chronology_market_and_alignment(self) -> None:
        source = self.candles(2)
        with self.assertRaisesRegex(DataError, "chronological"):
            aggregate_candles(list(reversed(source)), 30, 60)
        mixed = [
            source[0],
            Candle(
                market="KRW-ETH",
                timestamp=source[1].timestamp,
                open=source[1].open,
                high=source[1].high,
                low=source[1].low,
                close=source[1].close,
                volume=source[1].volume,
            ),
        ]
        with self.assertRaisesRegex(DataError, "one market"):
            aggregate_candles(mixed, 30, 60)
        unaligned = [
            Candle(
                market="KRW-BTC",
                timestamp=source[0].timestamp + timedelta(minutes=1),
                open=100.0,
                high=102.0,
                low=99.0,
                close=101.0,
                volume=1.0,
            )
        ]
        with self.assertRaisesRegex(DataError, "aligned"):
            aggregate_candles(unaligned, 30, 60)


class CsvTests(unittest.TestCase):
    def test_csv_round_trip(self) -> None:
        original = [
            Candle(
                market="KRW-BTC",
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                open=100.0,
                high=110.0,
                low=90.0,
                close=105.0,
                volume=4.25,
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candles.csv"
            save_candles_csv(path, original)
            loaded = load_candles_csv(path)
        self.assertEqual(loaded, original)

    def test_csv_rejects_unexpected_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candles.csv"
            path.write_text("timestamp,close\n2026-01-01T00:00:00+00:00,1\n", encoding="utf-8")
            with self.assertRaisesRegex(DataError, "CSV header"):
                load_candles_csv(path)

    def test_dataset_manifest_is_deterministic_and_content_addressed(self) -> None:
        candles = [
            Candle(
                market="KRW-BTC",
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index),
                open=100.0 + index,
                high=102.0 + index,
                low=99.0 + index,
                close=101.0 + index,
                volume=1.0,
            )
            for index in range(2)
        ]
        changed_candle = Candle(
            market="KRW-BTC",
            timestamp=candles[1].timestamp,
            open=candles[1].open,
            high=candles[1].high,
            low=candles[1].low,
            close=candles[1].close + 0.5,
            volume=candles[1].volume,
        )

        first = dataset_manifest(candles)
        second = dataset_manifest(tuple(candles))
        changed = dataset_manifest([candles[0], changed_candle])

        self.assertEqual(first, second)
        self.assertNotEqual(first.sha256, changed.sha256)
        self.assertEqual((first.market, first.candle_count), ("KRW-BTC", 2))

    def test_csv_and_manifest_reject_non_krw_markets(self) -> None:
        candle = Candle(
            market="BTC-ETH",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.0,
            volume=1.0,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candles.csv"
            with self.assertRaisesRegex(DataError, "KRW market"):
                save_candles_csv(path, [candle])
            path.write_text(
                "market,timestamp,open,high,low,close,volume\n"
                "BTC-ETH,2026-01-01T00:00:00+00:00,100,102,99,101,1\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(DataError, "KRW market"):
                load_candles_csv(path)
        with self.assertRaisesRegex(DataError, "KRW market"):
            dataset_manifest([candle])


class ResearchTests(unittest.TestCase):
    def test_walk_forward_never_mixes_training_and_test_windows(self) -> None:
        seen = []

        def train(values):
            return tuple(values)

        def evaluate(trained, test):
            seen.append((trained, tuple(test)))
            return sum(test)

        folds = walk_forward(
            list(range(10)),
            train_size=4,
            test_size=2,
            strategy_factory=train,
            backtest=evaluate,
        )

        self.assertEqual(seen, [((0, 1, 2, 3), (4, 5)), ((2, 3, 4, 5), (6, 7)), ((4, 5, 6, 7), (8, 9))])
        self.assertEqual([fold.result for fold in folds], [9, 13, 17])

    def test_project_adapters_use_offline_strategy_and_backtester(self) -> None:
        candles = [
            Candle(
                market="KRW-BTC",
                timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=index),
                open=100.0 + index,
                high=102.0 + index,
                low=99.0 + index,
                close=101.0 + index,
                volume=1.0,
            )
            for index in range(10)
        ]
        report = run_chronological_research(
            candles,
            train_size=6,
            test_size=2,
        )

        self.assertEqual(len(report.folds), 2)
        self.assertEqual(report.trade_count, 0)
        self.assertEqual(report.weighted_win_rate, 0.0)

    def test_training_close_signal_executes_at_first_oos_open(self) -> None:
        closes = [100.0, 100.0, 100.0, 110.0, 120.0, 130.0]
        candles = [
            Candle(
                market="KRW-BTC",
                timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=index),
                open=close,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=1.0,
            )
            for index, close in enumerate(closes)
        ]
        parameters = StrategyParameters(
            fast_period=2,
            slow_period=3,
            breakout_period=2,
            exit_period=2,
            volatility_period=2,
            maximum_annualized_volatility=10.0,
            allow_short_signals=False,
        )

        report = run_chronological_research(
            candles,
            train_size=4,
            test_size=2,
            parameters=parameters,
        )

        trade = report.folds[0].result.trades[0]
        self.assertEqual(trade.entry_index, 1)
        self.assertAlmostEqual(trade.entry_price, candles[4].open * 1.0005)

    def test_aggregate_drawdown_uses_stitched_oos_equity(self) -> None:
        results = iter(
            [
                SimpleNamespace(
                    equity_curve=(100.0, 200.0, 150.0),
                    trade_count=0,
                    closed_trade_count=0,
                    win_rate=0.0,
                    sharpe=1.0,
                ),
                SimpleNamespace(
                    equity_curve=(100.0, 80.0),
                    trade_count=0,
                    closed_trade_count=0,
                    win_rate=0.0,
                    sharpe=-1.0,
                ),
            ]
        )
        adapters = (lambda _train: object(), lambda _strategy, _test: next(results))

        with patch("bithumb_coin_trader.research.project_adapters", return_value=adapters):
            report = run_chronological_research(range(6), train_size=2, test_size=2)

        self.assertEqual(report.oos_equity_curve, (100.0, 200.0, 150.0, 120.0))
        self.assertAlmostEqual(report.maximum_drawdown, 0.4)
        self.assertAlmostEqual(report.compounded_return, 0.2)


if __name__ == "__main__":
    unittest.main()
