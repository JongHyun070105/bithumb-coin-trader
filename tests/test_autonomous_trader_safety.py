from __future__ import annotations

import json
import os
import tempfile
import unittest
import argparse
from decimal import Decimal
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts.autonomous_trader import (
    NEW_ENTRIES_ENV,
    PYRAMIDING_ENV,
    PortfolioState,
    _bithumb_timestamp_is_fresh,
    _read_asset_balances,
    fetch_dynamic_universe,
    acquire_daemon_lock,
    get_market_orderbook_ratio,
    get_market_warnings,
    get_realtime_ticker_price,
    load_market_rest,
    load_recent_exits,
    new_entries_enabled,
    pyramiding_enabled,
    recover_pending_order,
    reconcile_with_exchange,
    repair_portfolio_invariant,
)
from bithumb_coin_trader.config import TradingMode, TradingSettings
from bithumb_coin_trader.fill_ledger import FillLedger
from bithumb_coin_trader.state import BotState, save_state
from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.discord_notify import SilentNotifier
from scripts.scan_and_trade import analyze_market, completed_candles_are_fresh, run_scan_and_trade
from scripts import execute_live_trader


class AutonomousTraderSafetyTests(unittest.TestCase):
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
                repair_portfolio_invariant(CashOnlyClient(), portfolio)  # idempotent
            self.assertEqual(portfolio.active_market, "")
            self.assertEqual(portfolio.losing_trades, 1)
            self.assertEqual(portfolio.total_pnl_krw, -1008.0)
            self.assertEqual(portfolio.accounted_realized_pnl["KRW-BTC"], "-1008")
            self.assertTrue(rest_path.exists())
            self.assertTrue(exits_path.exists())

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
