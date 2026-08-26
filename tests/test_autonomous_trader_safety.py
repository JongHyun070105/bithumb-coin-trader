from __future__ import annotations

import json
import os
import tempfile
import unittest
import argparse
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.autonomous_trader import (
    ExitAction,
    FINNHUB_API_KEY_ENV,
    NEWS_CACHE_TTL_SEC,
    ScanSnapshot,
    NEW_ENTRIES_ENV,
    PYRAMIDING_ENV,
    PortfolioState,
    _bithumb_timestamp_is_fresh,
    _read_asset_balances,
    compose_reference_lines,
    fetch_dynamic_universe,
    acquire_daemon_lock,
    decide_position_exit,
    get_market_orderbook_ratio,
    get_market_warnings,
    get_recent_external_news,
    get_realtime_ticker_price,
    enhanced_exit_eligible_for_policy,
    external_news_cache_expired,
    load_market_rest,
    load_recent_exits,
    live_settings_for_portfolio,
    new_entries_enabled,
    pyramiding_enabled,
    rebase_after_external_cash_flow,
    recover_pending_order,
    reconcile_with_exchange,
    repair_portfolio_invariant,
    scan_snapshot_is_fresh,
    scan_and_rank_universe_isolated,
    start_external_news_fetch,
)
from bithumb_coin_trader.config import TradingMode, TradingSettings
from bithumb_coin_trader.fill_ledger import FillLedger
from bithumb_coin_trader.state import BotState, load_state, save_state
from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.discord_notify import SilentNotifier
from scripts.scan_and_trade import analyze_market, completed_candles_are_fresh, run_scan_and_trade
from scripts import execute_live_trader


class AutonomousTraderSafetyTests(unittest.TestCase):
    def test_external_news_is_disabled_without_key_and_does_not_touch_network(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("scripts.autonomous_trader.FinnhubNewsClient") as client,
        ):
            os.environ.pop(FINNHUB_API_KEY_ENV, None)
            self.assertEqual(get_recent_external_news(known_markets=["KRW-BTC"]), ([], []))
            client.assert_not_called()

    def test_external_news_worker_does_not_block_protective_loop_thread(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def blocking_fetch(*, known_markets):
            entered.set()
            release.wait(timeout=2)
            return (["done"], [])

        executor = ThreadPoolExecutor(max_workers=1)
        try:
            with patch("scripts.autonomous_trader.get_recent_external_news", side_effect=blocking_fetch):
                started = time.monotonic()
                future = start_external_news_fetch(executor, known_markets=["KRW-BTC"])
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 0.1)
                self.assertTrue(entered.wait(timeout=1))
                self.assertFalse(future.done())
                release.set()
                self.assertEqual(future.result(timeout=1), (["done"], []))
        finally:
            release.set()
            executor.shutdown(wait=True, cancel_futures=True)

    def test_external_news_cache_expires_after_bounded_ttl(self) -> None:
        self.assertTrue(external_news_cache_expired(None, now_monotonic=1.0))
        self.assertFalse(
            external_news_cache_expired(100.0, now_monotonic=100.0 + NEWS_CACHE_TTL_SEC)
        )
        self.assertTrue(
            external_news_cache_expired(100.0, now_monotonic=100.1 + NEWS_CACHE_TTL_SEC)
        )

    def test_reference_briefing_reserves_one_slot_per_source(self) -> None:
        lines = compose_reference_lines(
            warning_lines=["warning-1", "warning-2", "warning-3"],
            notice_lines=["notice-1", "notice-2"],
            news_lines=["news-1", "news-2"],
            news_status_line=None,
        )
        self.assertEqual(lines[:3], ["warning-1", "notice-1", "news-1"])
        failed = compose_reference_lines(
            warning_lines=["warning-1", "warning-2", "warning-3"],
            notice_lines=["notice-1"],
            news_lines=["stale-news"],
            news_status_line="news-error",
        )
        self.assertEqual(failed[:3], ["warning-1", "notice-1", "news-error"])

    def test_scan_ledger_failure_invalidates_candidates(self) -> None:
        analysis = object()
        audit = {
            "universe": ["KRW-BTC"],
            "scanned": ["KRW-BTC"],
            "skipped": {},
            "feed_health": {
                "warning_feed_ok": True,
                "ticker_feed_ok": True,
                "orderbook_feed_ok": True,
                "mcp_ok": True,
            },
            "errors": [],
        }
        client_context = MagicMock()
        client_context.return_value.__enter__.return_value = object()
        with (
            patch("scripts.autonomous_trader.McpStdioClient", client_context),
            patch(
                "scripts.autonomous_trader._scan_and_rank_universe_with_audit",
                return_value=([analysis], [{
                    "market": "KRW-BTC",
                    "confidence": 90.0,
                    "bid_ratio": 60.0,
                    "status": "candidate",
                    "pass_reasons": ["ok"],
                }], audit),
            ),
            patch(
                "scripts.autonomous_trader.append_scan_snapshot",
                side_effect=OSError("disk full"),
            ),
        ):
            snapshot = scan_and_rank_universe_isolated()
        self.assertEqual(snapshot.analyses, ())
        self.assertEqual(snapshot.top_candidates, ())
        self.assertFalse(snapshot.audit_summary["healthy"])

    @staticmethod
    def exit_portfolio(**overrides: object) -> PortfolioState:
        values: dict[str, object] = {
            "total_capital": 20_000.0,
            "cash_available": 0.0,
            "active_market": "KRW-BTC",
            "entry_price": 100.0,
            "position_volume": "200",
            "highest_price": 100.0,
            "entry_timestamp": 1_000.0,
            "enhanced_exit_eligible": True,
        }
        values.update(overrides)
        return PortfolioState(**values)  # type: ignore[arg-type]

    def test_exit_decision_precedence_and_partial_take_profit(self) -> None:
        portfolio = self.exit_portfolio()
        partial = decide_position_exit(portfolio, 102.0, now_timestamp=1_100.0)
        self.assertIs(partial.action, ExitAction.PARTIAL_EXIT)

        portfolio.partial_tp_taken = True
        self.assertIs(
            decide_position_exit(portfolio, 102.0, now_timestamp=1_100.0).action,
            ExitAction.HOLD,
        )
        portfolio.partial_tp_taken = False
        self.assertIs(
            decide_position_exit(portfolio, 103.8, now_timestamp=20_000.0).action,
            ExitAction.FULL_EXIT,
        )

    def test_rejected_enhanced_policy_is_not_eligible_for_new_version_zero_entries(self) -> None:
        self.assertFalse(enhanced_exit_eligible_for_policy(0))
        self.assertTrue(enhanced_exit_eligible_for_policy(1))
        with self.assertRaises(ValueError):
            enhanced_exit_eligible_for_policy(True)  # type: ignore[arg-type]

    def test_timecut_boundaries_and_existing_position_grandfathering(self) -> None:
        portfolio = self.exit_portfolio()
        self.assertIs(
            decide_position_exit(portfolio, 100.0, now_timestamp=15_399.9).action,
            ExitAction.HOLD,
        )
        self.assertIs(
            decide_position_exit(portfolio, 100.0, now_timestamp=15_400.0).action,
            ExitAction.FULL_EXIT,
        )
        self.assertIn(
            "TIMECUT",
            decide_position_exit(portfolio, 100.0, now_timestamp=15_400.0).reason,
        )
        self.assertIs(
            decide_position_exit(portfolio, 100.6001, now_timestamp=15_400.0).action,
            ExitAction.HOLD,
        )
        portfolio.enhanced_exit_eligible = False
        self.assertIs(
            decide_position_exit(portfolio, 100.0, now_timestamp=50_000.0).action,
            ExitAction.HOLD,
        )

    def test_trailing_precedes_partial_and_timecut(self) -> None:
        portfolio = self.exit_portfolio(highest_price=103.0)
        decision = decide_position_exit(portfolio, 100.5, now_timestamp=20_000.0)
        self.assertIs(decision.action, ExitAction.FULL_EXIT)
        self.assertIn("TRAILING-STOP", decision.reason)

    def test_partial_exit_requires_two_tradeable_legs(self) -> None:
        portfolio = self.exit_portfolio(position_volume="90")
        self.assertIs(
            decide_position_exit(portfolio, 102.0, now_timestamp=1_100.0).action,
            ExitAction.HOLD,
        )

    def test_scan_freshness_is_bounded_from_start_to_decision(self) -> None:
        snapshot = ScanSnapshot((), (), 100.0, 150.0)
        self.assertTrue(scan_snapshot_is_fresh(snapshot, now_monotonic=160.0))
        self.assertFalse(scan_snapshot_is_fresh(snapshot, now_monotonic=160.001))
        self.assertFalse(
            scan_snapshot_is_fresh(
                ScanSnapshot((), (), 200.0, 199.0), now_monotonic=200.0
            )
        )

    def test_new_entries_fail_closed(self) -> None:
        self.assertFalse(new_entries_enabled({}))
        self.assertFalse(new_entries_enabled({NEW_ENTRIES_ENV: "false"}))
        self.assertFalse(new_entries_enabled({NEW_ENTRIES_ENV: "1"}))

    def test_new_entries_require_exact_true_value(self) -> None:
        self.assertTrue(new_entries_enabled({NEW_ENTRIES_ENV: "true"}))
        self.assertTrue(new_entries_enabled({NEW_ENTRIES_ENV: " TRUE "}))

    def test_pyramiding_requires_separate_opt_in(self) -> None:
        self.assertFalse(pyramiding_enabled({NEW_ENTRIES_ENV: "true"}))
        self.assertFalse(pyramiding_enabled({PYRAMIDING_ENV: "true"}))

    @patch("scripts.autonomous_trader.urllib.request.urlopen", side_effect=OSError("offline"))
    def test_public_market_failures_block_entries(self, _urlopen: object) -> None:
        self.assertEqual(fetch_dynamic_universe(), [])
        self.assertIsNone(get_market_orderbook_ratio(object(), "KRW-BTC"))

    def test_invalid_orderbook_and_ticker_values_fail_closed(self) -> None:
        class Response:
            def __init__(self, payload: object) -> None:
                self.payload = json.dumps(payload).encode()

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return self.payload

        with patch(
            "scripts.autonomous_trader.urllib.request.urlopen",
            return_value=Response([{"total_ask_size": -1, "total_bid_size": 2}]),
        ):
            self.assertIsNone(get_market_orderbook_ratio(object(), "KRW-BTC"))
        with patch(
            "scripts.autonomous_trader.urllib.request.urlopen",
            return_value=Response([{"trade_price": "NaN"}]),
        ):
            with self.assertRaisesRegex(RuntimeError, "ticker unavailable"):
                get_realtime_ticker_price("KRW-BTC")

    def test_bithumb_timestamp_accepts_epoch_and_observed_kst_shift(self) -> None:
        now_ms = 1_800_000_000_000.0
        with patch("scripts.autonomous_trader.time.time", return_value=now_ms / 1000.0):
            self.assertTrue(_bithumb_timestamp_is_fresh(now_ms - 1000))
            self.assertTrue(
                _bithumb_timestamp_is_fresh(now_ms + 9 * 60 * 60 * 1000 - 1000)
            )
            self.assertFalse(_bithumb_timestamp_is_fresh(now_ms - 120_000))

    def test_warning_feed_failure_blocks_entries(self) -> None:
        class OfflineClient:
            def call_read_tool(self, _name: str, _arguments: object) -> object:
                raise TimeoutError("offline")

        self.assertIsNone(get_market_warnings(OfflineClient()))  # type: ignore[arg-type]

    def test_malformed_warning_feed_blocks_entries(self) -> None:
        class MalformedClient:
            def call_read_tool(self, _name: str, _arguments: object) -> object:
                return {"content": [{"type": "text", "text": "{}"}]}

        self.assertIsNone(get_market_warnings(MalformedClient()))  # type: ignore[arg-type]

    def test_fresh_continuous_candles_reach_positive_analysis_path(self) -> None:
        now = datetime(2026, 8, 24, 5, 5, tzinfo=UTC)
        start = now - timedelta(minutes=30 * 101)
        candles = [
            Candle(
                timestamp=start + timedelta(minutes=30 * index),
                open=100.0 + index,
                high=101.5 + index,
                low=99.5 + index,
                close=101.0 + index,
                volume=1000.0 + index,
                market="KRW-BTC",
            )
            for index in range(100)
        ]
        self.assertTrue(completed_candles_are_fresh(candles, now=now))
        with (
            patch("scripts.scan_and_trade.fetch_minute_candles", return_value=candles),
            patch("scripts.scan_and_trade.datetime") as clock,
        ):
            clock.now.return_value = now
            analysis = analyze_market("KRW-BTC")
        self.assertIsNotNone(analysis)

        stale_now = now + timedelta(hours=2)
        self.assertFalse(completed_candles_are_fresh(candles, now=stale_now))

    def test_corrupt_cooldown_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rest = Path(directory) / "rest.json"
            exits = Path(directory) / "exits.json"
            rest.write_text("not-json", encoding="utf-8")
            exits.write_text(json.dumps([]), encoding="utf-8")
            with patch("scripts.autonomous_trader.REST_PATH", rest):
                with self.assertRaisesRegex(RuntimeError, "entries remain blocked"):
                    load_market_rest()
            with patch("scripts.autonomous_trader.EXITS_PATH", exits):
                with self.assertRaisesRegex(RuntimeError, "entries remain blocked"):
                    load_recent_exits()

    def test_non_finite_cooldown_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rest = Path(directory) / "rest.json"
            exits = Path(directory) / "exits.json"
            rest.write_text('{"rest_until": NaN, "set_at": 1}', encoding="utf-8")
            exits.write_text('{"KRW-BTC": Infinity}', encoding="utf-8")
            with patch("scripts.autonomous_trader.REST_PATH", rest):
                with self.assertRaisesRegex(RuntimeError, "entries remain blocked"):
                    load_market_rest()
            with patch("scripts.autonomous_trader.EXITS_PATH", exits):
                with self.assertRaisesRegex(RuntimeError, "entries remain blocked"):
                    load_recent_exits()

    def test_singleton_lock_rejects_second_daemon(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daemon.lock"
            descriptor = acquire_daemon_lock(path)
            try:
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    acquire_daemon_lock(path)
            finally:
                os.close(descriptor)

    def test_pending_buy_recovery_starts_protective_position_tracking(self) -> None:
        class RecoveryClient:
            def call_read_tool(self, name: str, _arguments: object) -> object:
                if name == "trade_get_order":
                    payload = {
                        "client_order_id": "pending-buy",
                        "uuid": "order-1",
                        "market": "KRW-BTC",
                        "side": "bid",
                        "state": "done",
                        "executed_volume": "0.1",
                        "paid_fee": "4",
                        "trades": [
                            {
                                "uuid": "trade-1",
                                "market": "KRW-BTC",
                                "side": "bid",
                                "price": "100000",
                                "volume": "0.1",
                                "funds": "10000",
                                "created_at": "2026-08-24T14:00:00+09:00",
                            }
                        ],
                    }
                elif name == "account_get_assets":
                    payload = {"data": {"data": [
                        {"currency": "KRW", "balance": "9000"},
                        {"currency": "BTC", "balance": "0.1"},
                    ]}}
                else:
                    raise AssertionError(name)
                return {"content": [{"type": "text", "text": json.dumps(payload)}]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "live.json"
            portfolio_path = root / "portfolio.json"
            ledger_path = root / "fills.jsonl"
            save_state(
                state_path,
                BotState(
                    position="flat",
                    position_volume="0",
                    active_client_order_id="pending-buy",
                    pending_order_side="bid",
                    pending_market="KRW-BTC",
                    untracked_order=True,
                ),
            )
            portfolio = PortfolioState(total_capital=19000, cash_available=19000)
            settings = TradingSettings(mode=TradingMode.LIVE, live_trading_enabled=True)
            with (
                patch("scripts.autonomous_trader.STATE_PATH", state_path),
                patch("scripts.autonomous_trader.PORTFOLIO_PATH", portfolio_path),
                patch("scripts.autonomous_trader.FILL_LEDGER_PATH", ledger_path),
                patch("scripts.autonomous_trader.get_realtime_ticker_price", return_value=100000.0),
            ):
                self.assertTrue(
                    recover_pending_order(
                        RecoveryClient(),  # type: ignore[arg-type]
                        portfolio,
                        settings,
                        notifier=SilentNotifier(),
                    )
                )
            self.assertEqual(portfolio.active_market, "KRW-BTC")
            self.assertEqual(Decimal(portfolio.position_volume), Decimal("0.1"))
            self.assertGreater(portfolio.entry_price, 100000.0)  # buy fee included
            self.assertFalse(portfolio.legacy_position)

    def test_partial_sell_recovery_accepts_fill_already_in_ledger(self) -> None:
        buy = {
            "uuid": "buy-order",
            "market": "KRW-BTC",
            "side": "bid",
            "paid_fee": "4",
            "trades": [{
                "uuid": "buy-fill", "market": "KRW-BTC", "side": "bid",
                "price": "100000", "volume": "0.1", "funds": "10000",
                "created_at": "2026-08-24T14:00:00+09:00",
            }],
        }
        sell = {
            "uuid": "partial-order",
            "client_order_id": "partial-recovery",
            "market": "KRW-BTC",
            "side": "ask",
            "state": "done",
            "executed_volume": "0.04",
            "paid_fee": "2",
            "trades": [{
                "uuid": "partial-fill", "market": "KRW-BTC", "side": "ask",
                "price": "102000", "volume": "0.04", "funds": "4080",
                "created_at": "2026-08-24T15:00:00+09:00",
            }],
        }

        class RecoveryClient:
            def call_read_tool(self, name: str, _arguments: object) -> object:
                payload = sell if name == "trade_get_order" else {
                    "data": {"data": [
                        {"currency": "KRW", "balance": "14078"},
                        {"currency": "BTC", "balance": "0.06"},
                    ]}
                }
                return {"content": [{"type": "text", "text": json.dumps(payload)}]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "live.json"
            portfolio_path = root / "portfolio.json"
            ledger_path = root / "fills.jsonl"
            ledger = FillLedger(ledger_path)
            ledger.append_order(buy)
            ledger.append_order(sell)  # crash after ledger append, before state clear
            save_state(
                state_path,
                BotState(
                    position="long",
                    position_volume="0.1",
                    active_client_order_id="partial-recovery",
                    pending_order_side="ask",
                    pending_market="KRW-BTC",
                    pending_order_volume="0.04",
                    position_policy_version=1,
                ),
            )
            portfolio = PortfolioState(
                total_capital=20_000,
                cash_available=10_000,
                active_market="KRW-BTC",
                entry_price=100_040,
                position_volume="0.1",
                highest_price=102_000,
                entry_timestamp=1,
                enhanced_exit_eligible=True,
                total_trades=1,
            )
            with (
                patch("scripts.autonomous_trader.STATE_PATH", state_path),
                patch("scripts.autonomous_trader.PORTFOLIO_PATH", portfolio_path),
                patch("scripts.autonomous_trader.FILL_LEDGER_PATH", ledger_path),
                patch("scripts.autonomous_trader.get_realtime_ticker_price", return_value=102000.0),
            ):
                self.assertTrue(
                    recover_pending_order(
                        RecoveryClient(),  # type: ignore[arg-type]
                        portfolio,
                        TradingSettings(mode=TradingMode.LIVE, live_trading_enabled=True),
                        notifier=SilentNotifier(),
                    )
                )
            self.assertTrue(portfolio.partial_tp_taken)
            self.assertEqual(Decimal(portfolio.position_volume), Decimal("0.06"))
            self.assertEqual(portfolio.winning_trades, 0)

    def test_full_sell_recovery_accepts_flat_ledger_already_applied(self) -> None:
        buy = {
            "uuid": "buy-order", "market": "KRW-BTC", "side": "bid", "paid_fee": "4",
            "trades": [{
                "uuid": "buy-fill", "market": "KRW-BTC", "side": "bid",
                "price": "100000", "volume": "0.1", "funds": "10000",
                "created_at": "2026-08-24T14:00:00+09:00",
            }],
        }
        sell = {
            "uuid": "full-order", "client_order_id": "full-recovery",
            "market": "KRW-BTC", "side": "ask", "state": "done",
            "executed_volume": "0.1", "paid_fee": "5",
            "trades": [{
                "uuid": "full-fill", "market": "KRW-BTC", "side": "ask",
                "price": "105000", "volume": "0.1", "funds": "10500",
                "created_at": "2026-08-24T15:00:00+09:00",
            }],
        }

        class RecoveryClient:
            def call_read_tool(self, name: str, _arguments: object) -> object:
                payload = sell if name == "trade_get_order" else {
                    "data": {"data": [{"currency": "KRW", "balance": "20500"}]}
                }
                return {"content": [{"type": "text", "text": json.dumps(payload)}]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "live.json"
            portfolio_path = root / "portfolio.json"
            ledger_path = root / "fills.jsonl"
            exits_path = root / "exits.json"
            ledger = FillLedger(ledger_path)
            ledger.append_order(buy)
            ledger.append_order(sell)
            save_state(
                state_path,
                BotState(
                    position="long", position_volume="0.1",
                    active_client_order_id="full-recovery", pending_order_side="ask",
                    pending_market="KRW-BTC", pending_order_volume="0.1",
                    position_policy_version=1,
                ),
            )
            portfolio = PortfolioState(
                total_capital=20_000, cash_available=10_000,
                active_market="KRW-BTC", entry_price=100_040,
                position_volume="0.1", highest_price=105_000,
                entry_timestamp=1, enhanced_exit_eligible=True, total_trades=1,
            )
            with (
                patch("scripts.autonomous_trader.STATE_PATH", state_path),
                patch("scripts.autonomous_trader.PORTFOLIO_PATH", portfolio_path),
                patch("scripts.autonomous_trader.FILL_LEDGER_PATH", ledger_path),
                patch("scripts.autonomous_trader.EXITS_PATH", exits_path),
            ):
                self.assertTrue(
                    recover_pending_order(
                        RecoveryClient(),  # type: ignore[arg-type]
                        portfolio,
                        TradingSettings(mode=TradingMode.LIVE, live_trading_enabled=True),
                        notifier=SilentNotifier(),
                    )
                )
            self.assertEqual(portfolio.active_market, "")
            self.assertEqual(portfolio.winning_trades, 1)

    def test_tracked_sell_recovery_blocks_when_ledger_history_is_missing(self) -> None:
        class NoCallClient:
            def call_read_tool(self, _name: str, _arguments: object) -> object:
                raise AssertionError("exchange must not be queried without ledger history")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "live.json"
            portfolio_path = root / "portfolio.json"
            ledger_path = root / "missing-ledger.jsonl"
            save_state(
                state_path,
                BotState(
                    position="long", position_volume="0.1",
                    active_client_order_id="missing-ledger-sell",
                    pending_order_side="ask", pending_market="KRW-BTC",
                    pending_order_volume="0.1", position_policy_version=1,
                ),
            )
            portfolio = PortfolioState(
                total_capital=20_000, cash_available=10_000,
                active_market="KRW-BTC", entry_price=100_000,
                position_volume="0.1", highest_price=100_000,
                entry_timestamp=1, enhanced_exit_eligible=True,
            )
            with (
                patch("scripts.autonomous_trader.STATE_PATH", state_path),
                patch("scripts.autonomous_trader.PORTFOLIO_PATH", portfolio_path),
                patch("scripts.autonomous_trader.FILL_LEDGER_PATH", ledger_path),
            ):
                with self.assertRaisesRegex(RuntimeError, "ledger history"):
                    recover_pending_order(
                        NoCallClient(),  # type: ignore[arg-type]
                        portfolio,
                        TradingSettings(mode=TradingMode.LIVE, live_trading_enabled=True),
                        notifier=SilentNotifier(),
                    )

    def test_asset_parser_rejects_missing_duplicate_and_non_finite_balances(self) -> None:
        class AssetClient:
            def __init__(self, payload: object) -> None:
                self.payload = payload

            def call_read_tool(self, _name: str, _arguments: object) -> object:
                return {"content": [{"type": "text", "text": json.dumps(self.payload)}]}

        invalid = (
            {"data": {"data": []}},
            {"data": {"data": [{"currency": "KRW", "balance": "NaN"}]}},
            {"data": {"data": [
                {"currency": "KRW", "balance": "1"},
                {"currency": "KRW", "balance": "2"},
            ]}},
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                _read_asset_balances(AssetClient(payload))  # type: ignore[arg-type]

    def test_cross_file_buy_crash_is_repaired_from_ledger(self) -> None:
        class AssetClient:
            def call_read_tool(self, _name: str, _arguments: object) -> object:
                payload = {"data": {"data": [
                    {"currency": "KRW", "balance": "9000"},
                    {"currency": "BTC", "balance": "0.1"},
                ]}}
                return {"content": [{"type": "text", "text": json.dumps(payload)}]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "live.json"
            portfolio_path = root / "portfolio.json"
            ledger_path = root / "fills.jsonl"
            FillLedger(ledger_path).append_order(
                {
                    "uuid": "order-1",
                    "client_order_id": "buy-1",
                    "market": "KRW-BTC",
                    "side": "bid",
                    "paid_fee": "4",
                    "trades": [{
                        "uuid": "trade-1",
                        "market": "KRW-BTC",
                        "side": "bid",
                        "price": "100000",
                        "volume": "0.1",
                        "funds": "10000",
                        "created_at": "2026-08-24T14:00:00+09:00",
                    }],
                }
            )
            save_state(state_path, BotState(position="long", position_volume="0.1"))
            portfolio = PortfolioState(total_capital=19000, cash_available=19000)
            with (
                patch("scripts.autonomous_trader.STATE_PATH", state_path),
                patch("scripts.autonomous_trader.PORTFOLIO_PATH", portfolio_path),
                patch("scripts.autonomous_trader.FILL_LEDGER_PATH", ledger_path),
            ):
                repair_portfolio_invariant(AssetClient(), portfolio)  # type: ignore[arg-type]
            self.assertEqual(portfolio.active_market, "KRW-BTC")
            self.assertEqual(Decimal(portfolio.position_volume), Decimal("0.1"))

    def test_account_reconciliation_never_overwrites_pending_order(self) -> None:
        class NoCallClient:
            def call_read_tool(self, _name: str, _arguments: object) -> object:
                raise AssertionError("asset query must not run while order is pending")

        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "live.json"
            save_state(
                state_path,
                BotState(
                    position="long",
                    position_volume="0.1",
                    active_client_order_id="sell-1",
                    pending_order_side="ask",
                    pending_market="KRW-BTC",
                ),
            )
            portfolio = PortfolioState(
                active_market="KRW-BTC",
                entry_price=100000.0,
                position_volume="0.1",
                legacy_position=True,
            )
            with patch("scripts.autonomous_trader.STATE_PATH", state_path):
                with self.assertRaisesRegex(RuntimeError, "pending order"):
                    reconcile_with_exchange(NoCallClient(), portfolio)  # type: ignore[arg-type]

    def test_account_reconciliation_accounts_manual_legacy_exit_once(self) -> None:
        class CashOnlyClient:
            def call_read_tool(self, _name: str, _arguments: object) -> object:
                payload = {"data": {"data": [
                    {"currency": "KRW", "balance": "19900"},
                    {"currency": "BTC", "balance": "0"},
                ]}}
                return {"content": [{"type": "text", "text": json.dumps(payload)}]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "live.json"
            portfolio_path = root / "portfolio.json"
            ledger_path = root / "fills.jsonl"
            exits_path = root / "exits.json"
            history_path = root / "history.jsonl"
            save_state(state_path, BotState(position="long", position_volume="0.1"))
            portfolio = PortfolioState(
                total_capital=20_000,
                cash_available=10_000,
                active_market="KRW-BTC",
                entry_price=100_000.0,
                position_volume="0.1",
                legacy_position=True,
            )
            with (
                patch("scripts.autonomous_trader.STATE_PATH", state_path),
                patch("scripts.autonomous_trader.PORTFOLIO_PATH", portfolio_path),
                patch("scripts.autonomous_trader.FILL_LEDGER_PATH", ledger_path),
                patch("scripts.autonomous_trader.EXITS_PATH", exits_path),
                patch("scripts.autonomous_trader.TRADE_LOG_PATH", history_path),
                patch("scripts.autonomous_trader.get_realtime_ticker_price", return_value=99_000.0),
            ):
                reconcile_with_exchange(CashOnlyClient(), portfolio)  # type: ignore[arg-type]
                reconcile_with_exchange(CashOnlyClient(), portfolio)  # type: ignore[arg-type]  # idempotent

            self.assertEqual(load_state(state_path).position, "flat")
            self.assertEqual(portfolio.active_market, "")
            self.assertEqual(portfolio.cash_available, 19_900.0)
            self.assertEqual(portfolio.total_capital, 19_900.0)
            self.assertEqual(portfolio.total_pnl_krw, 0.0)
            self.assertEqual(portfolio.losing_trades, 0)
            self.assertFalse(history_path.exists())

    def test_account_reconciliation_blocks_manual_exit_when_cash_delta_looks_like_deposit(self) -> None:
        class CashOnlyClient:
            def call_read_tool(self, _name: str, _arguments: object) -> object:
                payload = {"data": {"data": [
                    {"currency": "KRW", "balance": "30000"},
                    {"currency": "BTC", "balance": "0"},
                ]}}
                return {"content": [{"type": "text", "text": json.dumps(payload)}]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "live.json"
            save_state(state_path, BotState(position="long", position_volume="0.1"))
            portfolio = PortfolioState(
                total_capital=20_000,
                cash_available=10_000,
                active_market="KRW-BTC",
                entry_price=100_000.0,
                position_volume="0.1",
                legacy_position=True,
            )
            with (
                patch("scripts.autonomous_trader.STATE_PATH", state_path),
                patch("scripts.autonomous_trader.FILL_LEDGER_PATH", root / "fills.jsonl"),
                patch("scripts.autonomous_trader.get_realtime_ticker_price", return_value=100_000.0),
            ):
                with self.assertRaisesRegex(RuntimeError, "possible deposit or withdrawal"):
                    reconcile_with_exchange(CashOnlyClient(), portfolio)  # type: ignore[arg-type]
            self.assertEqual(load_state(state_path).position, "long")
            self.assertEqual(portfolio.active_market, "KRW-BTC")

    def test_external_flat_deposit_rebases_risk_and_fifty_percent_target(self) -> None:
        portfolio = PortfolioState(
            total_capital=32_000,
            capital_baseline=32_000,
            cash_available=32_000,
            start_of_day_equity=31_000,
            peak_equity=34_000,
            goal_target=45_000,
            total_pnl_krw=2_400,
        )
        delta = rebase_after_external_cash_flow(portfolio, new_cash_balance=50_000)
        self.assertEqual(delta, 18_000)
        self.assertEqual(portfolio.total_capital, 50_000)
        self.assertEqual(portfolio.capital_baseline, 50_000)
        self.assertEqual(portfolio.start_of_day_equity, 50_000)
        self.assertEqual(portfolio.peak_equity, 50_000)
        self.assertEqual(portfolio.goal_target, 75_000)
        self.assertEqual(portfolio.total_pnl_krw, 2_400)
        self.assertEqual(live_settings_for_portfolio(portfolio).maximum_order_krw, 30_000)

    def test_legacy_portfolio_load_migrates_capital_baseline_from_current_total(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "portfolio.json"
            path.write_text(
                json.dumps({"total_capital": 51_234, "cash_available": 51_234}),
                encoding="utf-8",
            )
            portfolio = PortfolioState.load(path)
            self.assertEqual(portfolio.capital_baseline, 51_234)

    def test_flat_cash_change_does_not_hide_untracked_coin_holding(self) -> None:
        class OrphanAssetClient:
            def call_read_tool(self, _name: str, _arguments: object) -> object:
                payload = {"data": {"data": [
                    {"currency": "KRW", "balance": "50000"},
                    {"currency": "ETH", "balance": "0.01"},
                ]}}
                return {"content": [{"type": "text", "text": json.dumps(payload)}]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "live.json"
            save_state(state_path, BotState(position="flat", position_volume="0"))
            portfolio = PortfolioState(total_capital=32_000, cash_available=32_000)
            with patch("scripts.autonomous_trader.STATE_PATH", state_path):
                # Default allowlist is empty -> any untracked coin must fail closed!
                with self.assertRaisesRegex(RuntimeError, "untracked exchange holdings"):
                    reconcile_with_exchange(OrphanAssetClient(), portfolio)  # type: ignore[arg-type]
            self.assertEqual(portfolio.cash_available, 32_000)

    def test_allowlisted_manual_holding_does_not_block_reconciliation(self) -> None:
        class SyntheticManualHoldingClient:
            def call_read_tool(self, _name: str, _arguments: object) -> object:
                payload = {"data": {"data": [
                    {"currency": "KRW", "balance": "50000"},
                    {"currency": "MANUAL1", "balance": "100.5"},
                ]}}
                return {"content": [{"type": "text", "text": json.dumps(payload)}]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "live.json"
            save_state(state_path, BotState(position="flat", position_volume="0"))
            portfolio = PortfolioState(total_capital=50_000, cash_available=50_000)
            with patch("scripts.autonomous_trader.STATE_PATH", state_path):
                with patch.dict(os.environ, {"MANUAL_HOLDINGS_ALLOWLIST": "MANUAL1"}):
                    # Explicitly allowlisted synthetic symbol -> reconciles smoothly
                    reconcile_with_exchange(SyntheticManualHoldingClient(), portfolio)  # type: ignore[arg-type]
            self.assertEqual(portfolio.cash_available, 50_000)
            self.assertEqual(portfolio.total_capital, 50_000)

    def test_default_allowlist_is_empty_and_fails_closed(self) -> None:
        class UnallowlistedManualClient:
            def call_read_tool(self, _name: str, _arguments: object) -> object:
                payload = {"data": {"data": [
                    {"currency": "KRW", "balance": "50000"},
                    {"currency": "MANUAL1", "balance": "100.5"},
                ]}}
                return {"content": [{"type": "text", "text": json.dumps(payload)}]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "live.json"
            save_state(state_path, BotState(position="flat", position_volume="0"))
            portfolio = PortfolioState(total_capital=50_000, cash_available=50_000)
            with patch("scripts.autonomous_trader.STATE_PATH", state_path):
                with patch.dict(os.environ, {}, clear=True):
                    # Without MANUAL_HOLDINGS_ALLOWLIST env -> default is empty -> must fail closed!
                    with self.assertRaisesRegex(RuntimeError, "untracked exchange holdings"):
                        reconcile_with_exchange(UnallowlistedManualClient(), portfolio)  # type: ignore[arg-type]
            self.assertEqual(portfolio.cash_available, 50_000)

    def test_custom_manual_holdings_allowlist_env_override(self) -> None:
        class MultiHoldingClient:
            def call_read_tool(self, _name: str, _arguments: object) -> object:
                payload = {"data": {"data": [
                    {"currency": "KRW", "balance": "50000"},
                    {"currency": "ASSET_A", "balance": "100"},
                    {"currency": "ASSET_B", "balance": "2.5"},
                ]}}
                return {"content": [{"type": "text", "text": json.dumps(payload)}]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "live.json"
            save_state(state_path, BotState(position="flat", position_volume="0"))
            portfolio = PortfolioState(total_capital=50_000, cash_available=50_000)
            with patch("scripts.autonomous_trader.STATE_PATH", state_path):
                with patch.dict(os.environ, {"MANUAL_HOLDINGS_ALLOWLIST": "ASSET_A,KRW-ASSET_B"}):
                    # ASSET_A & ASSET_B are allowlisted -> passes smoothly
                    reconcile_with_exchange(MultiHoldingClient(), portfolio)  # type: ignore[arg-type]
            self.assertEqual(portfolio.cash_available, 50_000)

    def test_cross_file_sell_crash_recovers_pnl_and_loss_cooldown_once(self) -> None:
        class CashOnlyClient:
            def call_read_tool(self, _name: str, _arguments: object) -> object:
                payload = {"data": {"data": [{"currency": "KRW", "balance": "18000"}]}}
                return {"content": [{"type": "text", "text": json.dumps(payload)}]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "live.json"
            portfolio_path = root / "portfolio.json"
            ledger_path = root / "fills.jsonl"
            rest_path = root / "rest.json"
            exits_path = root / "exits.json"
            ledger = FillLedger(ledger_path)
            ledger.append_order({
                "uuid": "buy-order",
                "market": "KRW-BTC",
                "side": "bid",
                "paid_fee": "4",
                "trades": [{
                    "uuid": "buy-fill", "market": "KRW-BTC", "side": "bid",
                    "price": "100000", "volume": "0.1", "funds": "10000",
                    "created_at": "2026-08-24T14:00:00+09:00",
                }],
            })
            ledger.append_order({
                "uuid": "sell-order",
                "market": "KRW-BTC",
                "side": "ask",
                "paid_fee": "4",
                "trades": [{
                    "uuid": "sell-fill", "market": "KRW-BTC", "side": "ask",
                    "price": "90000", "volume": "0.1", "funds": "9000",
                    "created_at": "2026-08-24T14:05:00+09:00",
                }],
            })
            save_state(state_path, BotState(position="flat", position_volume="0"))
            portfolio = PortfolioState(
                total_capital=19000,
                cash_available=9000,
                active_market="KRW-BTC",
                entry_price=100040.0,
                position_volume="0.1",
            )
            with (
                patch("scripts.autonomous_trader.STATE_PATH", state_path),
                patch("scripts.autonomous_trader.PORTFOLIO_PATH", portfolio_path),
                patch("scripts.autonomous_trader.FILL_LEDGER_PATH", ledger_path),
                patch("scripts.autonomous_trader.REST_PATH", rest_path),
                patch("scripts.autonomous_trader.EXITS_PATH", exits_path),
            ):
                repair_portfolio_invariant(CashOnlyClient(), portfolio)  # type: ignore[arg-type]
                repair_portfolio_invariant(CashOnlyClient(), portfolio)  # type: ignore[arg-type]  # idempotent
            self.assertEqual(portfolio.active_market, "")
            self.assertEqual(portfolio.losing_trades, 1)
            self.assertEqual(portfolio.total_pnl_krw, -1008.0)
            self.assertEqual(portfolio.accounted_realized_pnl["KRW-BTC"], "-1008")
            self.assertTrue(rest_path.exists())
            self.assertTrue(exits_path.exists())

    def test_cross_file_manual_legacy_exit_recovers_accounting_after_state_went_flat(self) -> None:
        class CashOnlyClient:
            def call_read_tool(self, _name: str, _arguments: object) -> object:
                payload = {"data": {"data": [{"currency": "KRW", "balance": "19900"}]}}
                return {"content": [{"type": "text", "text": json.dumps(payload)}]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "live.json"
            portfolio_path = root / "portfolio.json"
            save_state(state_path, BotState(position="flat", position_volume="0"))
            portfolio = PortfolioState(
                total_capital=20_000,
                cash_available=10_000,
                active_market="KRW-BTC",
                entry_price=100_000.0,
                position_volume="0.1",
                legacy_position=True,
            )
            with (
                patch("scripts.autonomous_trader.STATE_PATH", state_path),
                patch("scripts.autonomous_trader.PORTFOLIO_PATH", portfolio_path),
                patch("scripts.autonomous_trader.FILL_LEDGER_PATH", root / "fills.jsonl"),
                patch("scripts.autonomous_trader.EXITS_PATH", root / "exits.json"),
                patch("scripts.autonomous_trader.TRADE_LOG_PATH", root / "history.jsonl"),
                patch("scripts.autonomous_trader.get_realtime_ticker_price", return_value=99_000.0),
            ):
                repair_portfolio_invariant(CashOnlyClient(), portfolio)  # type: ignore[arg-type]
            self.assertEqual(portfolio.active_market, "")
            self.assertEqual(portfolio.cash_available, 19_900.0)
            self.assertEqual(portfolio.total_pnl_krw, 0.0)
            self.assertEqual(portfolio.losing_trades, 0)

    def test_portfolio_rejects_type_confusion_and_flat_position_leakage(self) -> None:
        with self.assertRaises(TypeError):
            PortfolioState(legacy_position="false")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            PortfolioState(total_capital="10000")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            PortfolioState(position_volume="0.1")

    def test_legacy_live_entrypoints_are_disabled(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "legacy --live"):
            run_scan_and_trade(True, 5000, False)
        with patch.object(
            execute_live_trader,
            "parse_args",
            return_value=argparse.Namespace(live=True),
        ):
            with self.assertRaisesRegex(SystemExit, "legacy --live"):
                execute_live_trader.main()


if __name__ == "__main__":
    unittest.main()
