from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import bithumb_coin_trader.paper as paper_module

from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.paper import (
    PaperEngine,
    PaperError,
    PaperState,
    audit_evidence,
    load_paper_state,
    save_paper_state,
    verify_audit,
)


def candles(prices: list[float], *, start: datetime | None = None) -> list[Candle]:
    beginning = start or datetime(2026, 1, 1, 15, tzinfo=UTC)
    return [
        Candle(
            timestamp=beginning + timedelta(days=index),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=1.0,
            market="KRW-BTC",
        )
        for index, price in enumerate(prices)
    ]


class PaperStateTests(unittest.TestCase):
    def test_state_round_trip_preserves_exact_decimal_strings(self) -> None:
        state = PaperState(
            cash_krw="9975.0000",
            position="long",
            strategy_position="long",
            quantity="0.1234567890123456789",
            cost_basis_krw="10025.0000",
            realized_pnl_krw="1.2300",
            last_decision_at="2026-01-01T15:00:00+00:00",
            last_execution_at="2026-01-02T15:00:00+00:00",
            daily_entry_date="2026-01-03",
            daily_entries=1,
            decision_count=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.json"
            save_paper_state(path, state)
            payload = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_paper_state(path)

        self.assertEqual(loaded, state)
        self.assertIsInstance(payload["cash_krw"], str)
        self.assertEqual(payload["quantity"], "0.1234567890123456789")

    def test_state_rejects_inconsistent_or_inexact_values(self) -> None:
        with self.assertRaisesRegex(PaperError, "exact decimal"):
            PaperState(cash_krw=1.5)  # type: ignore[arg-type]
        with self.assertRaisesRegex(PaperError, "flat"):
            PaperState(quantity="1")
        with self.assertRaisesRegex(PaperError, "execution timestamp"):
            PaperState(
                last_decision_at="2026-01-01T15:00:00+00:00",
                last_execution_at="2026-01-03T15:00:00+00:00",
            )
        self.assertEqual(PaperState(realized_pnl_krw="-12.5").realized_pnl_krw, "-12.5")


class PaperEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        directory = Path(self.temporary.name)
        self.state_path = directory / "paper.json"
        self.audit_path = directory / "paper.jsonl"
        self.engine = PaperEngine(self.state_path, self.audit_path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_buy_uses_penultimate_signal_latest_open_and_is_idempotent(self) -> None:
        series = candles([90.0, 100.0, 110.0])
        signals = [Signal.FLAT, Signal.LONG, Signal.FLAT]

        result = self.engine.process(series, signals)
        duplicate = self.engine.process(series, signals)

        self.assertTrue(result.processed)
        self.assertEqual(result.action, "buy")
        self.assertEqual(Decimal(result.execution_price), Decimal("110") * Decimal("1.0005"))
        self.assertEqual(result.fee_krw, "25.0000")
        self.assertEqual(result.state.cash_krw, "9975.0000")
        self.assertEqual(result.state.cost_basis_krw, "10025.0000")
        self.assertEqual(result.state.strategy_position, "long")
        self.assertEqual(result.state.daily_entries, 1)
        self.assertEqual(result.state.decision_count, 1)
        self.assertFalse(duplicate.processed)
        self.assertEqual(duplicate.action, "already_processed")
        self.assertEqual(len(self.audit_path.read_text(encoding="utf-8").splitlines()), 1)

    def test_next_decision_sells_and_reports_realized_and_equity(self) -> None:
        first = candles([90.0, 100.0, 110.0])
        self.engine.process(first, [Signal.FLAT, Signal.LONG, Signal.FLAT])
        next_series = candles([100.0, 110.0, 120.0], start=first[1].timestamp)

        result = self.engine.process(next_series, [Signal.LONG, Signal.FLAT, Signal.FLAT])

        self.assertEqual(result.action, "sell")
        self.assertEqual(result.state.position, "flat")
        self.assertEqual(result.unrealized_pnl_krw, "0")
        self.assertEqual(Decimal(result.equity_krw), Decimal(result.state.cash_krw))
        self.assertEqual(Decimal(result.realized_pnl_krw), Decimal(result.state.realized_pnl_krw))
        self.assertEqual(result.trade_realized_pnl_krw, result.realized_pnl_krw)
        self.assertGreater(Decimal(result.realized_pnl_krw), 0)
        self.assertEqual(result.state.decision_count, 2)
        self.assertEqual(result.state.daily_entries, 0)

    def test_realized_loss_remains_an_exact_negative_decimal_after_reload(self) -> None:
        first = candles([90.0, 100.0, 110.0])
        self.engine.process(first, [Signal.FLAT, Signal.LONG, Signal.FLAT])
        falling = candles([110.0, 80.0], start=first[-1].timestamp)

        result = self.engine.process(falling, [Signal.FLAT, Signal.FLAT])
        loaded = load_paper_state(self.state_path)

        self.assertLess(Decimal(result.trade_realized_pnl_krw), 0)
        self.assertEqual(loaded.realized_pnl_krw, result.realized_pnl_krw)

    def test_below_minimum_available_cash_holds_without_negative_cash(self) -> None:
        save_paper_state(self.state_path, PaperState(cash_krw="9999"))

        result = self.engine.process(
            candles([100.0, 101.0]),
            [Signal.LONG, Signal.FLAT],
        )

        self.assertEqual(result.action, "hold")
        self.assertEqual(result.state.cash_krw, "9999")
        self.assertEqual(result.state.strategy_position, "long")
        self.assertGreaterEqual(Decimal(result.state.cash_krw), 0)

    def test_buy_fee_is_included_when_preserving_cash_reserve(self) -> None:
        save_paper_state(self.state_path, PaperState(cash_krw="15000"))

        result = self.engine.process(candles([100.0, 101.0]), [Signal.LONG, Signal.FLAT])

        self.assertEqual(result.action, "buy")
        self.assertEqual(Decimal(result.state.cash_krw), Decimal("5000"))
        self.assertLess(Decimal(result.state.cost_basis_krw), Decimal("10001"))

    def test_rejects_short_gap_duplicate_nonchronological_and_stale_state(self) -> None:
        normal = candles([100.0, 101.0, 102.0])
        with self.assertRaisesRegex(PaperError, "long and flat"):
            self.engine.process(normal, [Signal.FLAT, Signal.SHORT, Signal.FLAT])

        gap = [normal[0], normal[2]]
        with self.assertRaisesRegex(PaperError, "gap"):
            self.engine.process(gap, [Signal.FLAT, Signal.FLAT])
        duplicate = [normal[0], normal[0]]
        with self.assertRaisesRegex(PaperError, "duplicate"):
            self.engine.process(duplicate, [Signal.FLAT, Signal.FLAT])
        reverse = [normal[1], normal[0]]
        with self.assertRaisesRegex(PaperError, "non-chronological"):
            self.engine.process(reverse, [Signal.FLAT, Signal.FLAT])

        self.engine.process(normal, [Signal.FLAT, Signal.FLAT, Signal.FLAT])
        skipped = candles([103.0, 104.0], start=normal[-1].timestamp + timedelta(days=1))
        with self.assertRaisesRegex(PaperError, "stale"):
            self.engine.process(skipped, [Signal.FLAT, Signal.FLAT])

        self.assertEqual(len(self.audit_path.read_text(encoding="utf-8").splitlines()), 1)

    def test_rejects_unfinished_or_misaligned_daily_candle(self) -> None:
        series = candles([100.0, 101.0])
        with self.assertRaisesRegex(PaperError, "not completed"):
            self.engine.process(
                series,
                [Signal.FLAT, Signal.FLAT],
                as_of=series[-1].timestamp + timedelta(hours=23),
            )
        shifted = [
            Candle(
                timestamp=candle.timestamp + timedelta(hours=1),
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                market=candle.market,
            )
            for candle in series
        ]
        with self.assertRaisesRegex(PaperError, "KST midnight"):
            self.engine.process(shifted, [Signal.FLAT, Signal.FLAT])

    def test_next_day_accepts_only_the_latest_two_candles(self) -> None:
        first = candles([100.0, 101.0])
        self.engine.process(first, [Signal.FLAT, Signal.FLAT])
        next_pair = candles([101.0, 102.0], start=first[-1].timestamp)

        result = self.engine.process(next_pair, [Signal.FLAT, Signal.FLAT])

        self.assertTrue(result.processed)
        self.assertEqual(result.state.decision_count, 2)

    def test_audit_record_contains_exact_accounting_fields(self) -> None:
        result = self.engine.process(
            candles([100.0, 101.0]),
            [Signal.LONG, Signal.FLAT],
        )

        record = json.loads(self.audit_path.read_text(encoding="utf-8"))
        self.assertEqual(record["action"], "buy")
        self.assertIsInstance(record["fee_krw"], str)
        self.assertIsInstance(record["equity_krw"], str)
        self.assertEqual(record["decision_count"], 1)
        self.assertEqual(record["requested_signal"], "long")
        self.assertEqual(record["mark_price_krw"], "101.0")
        self.assertEqual(record["state_after"], asdict(result.state))
        unsigned = {key: value for key, value in record.items() if key != "canonical_sha256"}
        self.assertEqual(record["canonical_sha256"], paper_module._canonical_sha256(unsigned))

    def test_recovers_without_duplicate_after_audit_append_raises_post_write(self) -> None:
        series = candles([100.0, 101.0])
        original_append = paper_module._append_audit

        def append_then_fail(path, record):
            original_append(path, record)
            raise OSError("simulated crash after audit write")

        with patch("bithumb_coin_trader.paper._append_audit", side_effect=append_then_fail):
            with self.assertRaisesRegex(OSError, "simulated crash"):
                self.engine.process(series, [Signal.LONG, Signal.FLAT])

        self.assertTrue(self.engine.pending_path.exists())
        recovered = self.engine.process(series, [Signal.LONG, Signal.FLAT])
        self.assertFalse(recovered.processed)
        self.assertFalse(self.engine.pending_path.exists())
        self.assertEqual(len(self.audit_path.read_text(encoding="utf-8").splitlines()), 1)
        self.assertEqual(load_paper_state(self.state_path).decision_count, 1)

    def test_recovers_state_and_audit_after_state_save_failure(self) -> None:
        series = candles([100.0, 101.0])

        with patch("bithumb_coin_trader.paper.save_paper_state", side_effect=OSError("state save failed")):
            with self.assertRaisesRegex(OSError, "state save failed"):
                self.engine.process(series, [Signal.LONG, Signal.FLAT])

        self.assertTrue(self.engine.pending_path.exists())
        self.assertFalse(self.state_path.exists())
        recovered = self.engine.process(series, [Signal.LONG, Signal.FLAT])
        self.assertFalse(recovered.processed)
        self.assertFalse(self.engine.pending_path.exists())
        self.assertEqual(len(self.audit_path.read_text(encoding="utf-8").splitlines()), 1)
        self.assertEqual(load_paper_state(self.state_path).decision_count, 1)

    def test_recovers_partial_final_audit_write_without_touching_history(self) -> None:
        first = candles([90.0, 100.0, 110.0])
        self.engine.process(first, [Signal.FLAT, Signal.LONG, Signal.FLAT])
        second = candles([110.0, 120.0], start=first[-1].timestamp)

        def partial_append_then_fail(path, record):
            encoded = (paper_module._canonical_json(record) + "\n").encode("utf-8")
            descriptor = paper_module.os.open(
                path,
                paper_module.os.O_APPEND | paper_module.os.O_CREAT | paper_module.os.O_WRONLY,
                0o600,
            )
            try:
                paper_module.os.write(descriptor, encoded[: len(encoded) // 2])
                paper_module.os.fsync(descriptor)
            finally:
                paper_module.os.close(descriptor)
            raise OSError("simulated partial audit write")

        with patch("bithumb_coin_trader.paper._append_audit", side_effect=partial_append_then_fail):
            with self.assertRaisesRegex(OSError, "partial audit"):
                self.engine.process(second, [Signal.FLAT, Signal.FLAT])

        self.assertTrue(self.engine.pending_path.exists())
        recovered = self.engine.process(second, [Signal.FLAT, Signal.FLAT])

        self.assertFalse(recovered.processed)
        self.assertFalse(self.engine.pending_path.exists())
        self.assertEqual(len(self.audit_path.read_text(encoding="utf-8").splitlines()), 2)
        evidence = verify_audit(self.audit_path, self.state_path)
        self.assertEqual((evidence.buy_count, evidence.sell_count), (1, 1))

    def test_recovery_adds_newline_to_complete_unterminated_pending_record(self) -> None:
        series = candles([100.0, 101.0])

        def complete_without_newline_then_fail(path, record):
            encoded = paper_module._canonical_json(record).encode("utf-8")
            descriptor = paper_module.os.open(
                path,
                paper_module.os.O_APPEND | paper_module.os.O_CREAT | paper_module.os.O_WRONLY,
                0o600,
            )
            try:
                paper_module.os.write(descriptor, encoded)
                paper_module.os.fsync(descriptor)
            finally:
                paper_module.os.close(descriptor)
            raise OSError("simulated missing newline")

        with patch("bithumb_coin_trader.paper._append_audit", side_effect=complete_without_newline_then_fail):
            with self.assertRaisesRegex(OSError, "missing newline"):
                self.engine.process(series, [Signal.LONG, Signal.FLAT])

        recovered = self.engine.process(series, [Signal.LONG, Signal.FLAT])

        self.assertFalse(recovered.processed)
        self.assertTrue(self.audit_path.read_bytes().endswith(b"\n"))
        self.assertEqual(len(self.audit_path.read_text(encoding="utf-8").splitlines()), 1)
        verify_audit(self.audit_path, self.state_path)

    def test_audit_replay_verifies_accounting_and_round_trips(self) -> None:
        first = candles([90.0, 100.0, 110.0])
        self.engine.process(first, [Signal.FLAT, Signal.LONG, Signal.FLAT])
        second = candles([110.0, 120.0], start=first[-1].timestamp)
        self.engine.process(second, [Signal.FLAT, Signal.FLAT])

        replay = audit_evidence(self.audit_path)
        verified = verify_audit(self.audit_path, self.state_path)

        self.assertEqual(replay, verified)
        self.assertEqual((verified.buy_count, verified.sell_count), (1, 1))
        self.assertEqual(verified.round_trip_count, 1)
        self.assertEqual(verified.winning_round_trips, 1)
        self.assertEqual(verified.final_state, load_paper_state(self.state_path))

    def test_audit_replay_rejects_tampered_accounting(self) -> None:
        self.engine.process(candles([100.0, 101.0]), [Signal.LONG, Signal.FLAT])
        record = json.loads(self.audit_path.read_text(encoding="utf-8"))
        record["equity_krw"] = "999999"
        unsigned = {key: value for key, value in record.items() if key != "canonical_sha256"}
        record["canonical_sha256"] = paper_module._canonical_sha256(unsigned)
        self.audit_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        with self.assertRaisesRegex(PaperError, "equity"):
            audit_evidence(self.audit_path)


if __name__ == "__main__":
    unittest.main()
