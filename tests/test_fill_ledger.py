from __future__ import annotations

import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from bithumb_coin_trader.fill_ledger import FillLedger, FillLedgerError


def fill(
    trade_id: str,
    *,
    price: str,
    volume: str,
    funds: str,
    paid_fee: str | None = None,
) -> dict[str, str]:
    result = {
        "uuid": trade_id,
        "price": price,
        "volume": volume,
        "funds": funds,
        "created_at": "2026-08-24T00:00:00+09:00",
    }
    if paid_fee is not None:
        result["paid_fee"] = paid_fee
    return result


def order(
    order_id: str,
    side: str,
    trades: list[dict[str, str]],
    *,
    paid_fee: str,
    executed_volume: str = "999999",
) -> dict[str, object]:
    return {
        "uuid": order_id,
        "client_order_id": f"client-{order_id}",
        "market": "KRW-BTC",
        "side": side,
        "executed_volume": executed_volume,
        "paid_fee": paid_fee,
        "trades": trades,
    }


class FillLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "fills.jsonl"
        self.ledger = FillLedger(self.path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_pyramiding_uses_incremental_fills_not_cumulative_executed_volume(self) -> None:
        first = order(
            "order-1",
            "bid",
            [fill("trade-1", price="1000", volume="1", funds="1000")],
            paid_fee="1",
            executed_volume="1",
        )
        second = order(
            "order-2",
            "bid",
            [fill("trade-2", price="2000", volume="1", funds="2000")],
            paid_fee="2",
            executed_volume="2",  # cumulative holding; must never become a fill
        )

        self.ledger.append_order(first)
        self.ledger.append_order(second)
        position = self.ledger.position("KRW-BTC")

        self.assertEqual(position.volume, Decimal("2"))
        self.assertEqual(position.cost_basis, Decimal("3003"))
        self.assertEqual(position.average_cost, Decimal("1501.5"))
        records = [json.loads(line) for line in self.path.read_text().splitlines()]
        self.assertEqual([record["volume"] for record in records], ["1", "1"])

    def test_partial_sell_keeps_weighted_cost_and_realizes_net_pnl(self) -> None:
        self.ledger.append_order(
            order(
                "buy-order",
                "bid",
                [fill("buy-fill", price="1000", volume="2", funds="2000")],
                paid_fee="2",
            )
        )
        result = self.ledger.append_order(
            order(
                "sell-order",
                "ask",
                [fill("sell-fill", price="1200", volume="0.5", funds="600")],
                paid_fee="0.6",
                executed_volume="0.5",
            )
        )

        position = result.positions["KRW-BTC"]
        self.assertEqual(position.volume, Decimal("1.5"))
        self.assertEqual(position.cost_basis, Decimal("1501.50"))
        self.assertEqual(position.average_cost, Decimal("1001.0"))
        self.assertEqual(position.realized_pnl, Decimal("98.90"))
        self.assertEqual(position.paid_fees, Decimal("2.6"))

    def test_duplicate_trade_is_idempotent_but_conflict_fails_closed(self) -> None:
        payload = order(
            "order-1",
            "bid",
            [fill("trade-1", price="1000", volume="1", funds="1000")],
            paid_fee="1",
        )
        first = self.ledger.append_order(payload)
        second = self.ledger.append_order(payload)

        self.assertEqual(first.appended_trade_ids, ("trade-1",))
        self.assertEqual(second.appended_trade_ids, ())
        self.assertEqual(second.duplicate_trade_ids, ("trade-1",))
        self.assertEqual(len(self.path.read_text().splitlines()), 1)

        conflicting = order(
            "order-1",
            "bid",
            [fill("trade-1", price="1001", volume="1", funds="1001")],
            paid_fee="1",
        )
        with self.assertRaisesRegex(FillLedgerError, "conflicts"):
            self.ledger.append_order(conflicting)

    def test_multiple_fills_allocate_exact_order_fee_without_float_rounding(self) -> None:
        payload = order(
            "order-1",
            "bid",
            [
                fill("trade-1", price="100", volume="1", funds="100"),
                fill("trade-2", price="300", volume="1", funds="300"),
            ],
            paid_fee="0.7",
        )
        self.ledger.append_order(payload)

        records = [json.loads(line) for line in self.path.read_text().splitlines()]
        self.assertEqual(
            sum((Decimal(record["paid_fee"]) for record in records), Decimal("0")),
            Decimal("0.7"),
        )
        self.assertEqual(self.ledger.position("KRW-BTC").cost_basis, Decimal("400.7"))

    def test_incremental_partial_fill_uses_cumulative_order_fee_delta(self) -> None:
        first_snapshot = order(
            "order-1",
            "bid",
            [fill("trade-1", price="100", volume="1", funds="100")],
            paid_fee="0.1",
        )
        second_snapshot = order(
            "order-1",
            "bid",
            [
                fill("trade-1", price="100", volume="1", funds="100"),
                fill("trade-2", price="200", volume="1", funds="200"),
            ],
            paid_fee="0.3",
        )
        self.ledger.append_order(first_snapshot)
        result = self.ledger.append_order(second_snapshot)

        self.assertEqual(result.appended_trade_ids, ("trade-2",))
        self.assertEqual(result.duplicate_trade_ids, ("trade-1",))
        self.assertEqual(result.positions["KRW-BTC"].paid_fees, Decimal("0.3"))
        self.assertEqual(result.positions["KRW-BTC"].volume, Decimal("2"))

    def test_rejects_sell_beyond_position_and_strictly_validates_disk_schema(self) -> None:
        with self.assertRaisesRegex(FillLedgerError, "exceeds tracked"):
            self.ledger.append_order(
                order(
                    "sell-order",
                    "ask",
                    [fill("sell-fill", price="1000", volume="1", funds="1000")],
                    paid_fee="1",
                )
            )
        self.assertFalse(self.path.exists())

        self.path.write_text('{"unexpected": true}\n', encoding="utf-8")
        with self.assertRaisesRegex(FillLedgerError, "schema mismatch"):
            self.ledger.positions()

    def test_ledger_file_is_private_and_each_line_is_complete_json(self) -> None:
        self.path.touch(mode=0o644)
        self.ledger.append_order(
            order(
                "order-1",
                "bid",
                [fill("trade-1", price="1000", volume="1", funds="1000")],
                paid_fee="1",
            )
        )
        mode = os.stat(self.path).st_mode & 0o777
        self.assertEqual(mode, 0o600)
        for line in self.path.read_text(encoding="utf-8").splitlines():
            self.assertIsInstance(json.loads(line), dict)


if __name__ == "__main__":
    unittest.main()
