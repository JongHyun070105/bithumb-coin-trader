from decimal import Decimal
import sqlite3

import pytest

from bithumb_coin_trader.execution_simulator import (
    DeterministicTakerSimulator,
    MarketOrderRequest,
    OrderBookSnapshot,
)
from bithumb_coin_trader.paper_engine import PaperOrder, PaperPortfolio
from bithumb_coin_trader.paper_journal import PaperEventJournal, PaperJournalIntegrityError


def _execution_result(*, fee_rate: float = 0.0004):
    book = OrderBookSnapshot(
        timestamp=1000.0,
        bids=((99_000_000.0, 1.0),),
        asks=((100_000_000.0, 0.2), (100_100_000.0, 0.3)),
    )
    request = MarketOrderRequest(
        timestamp=1000.0,
        side="BUY",
        requested_quantity_btc=0.5,
        fee_rate=fee_rate,
    )
    return DeterministicTakerSimulator.execute_order(request, book)


def test_journal_recovers_snapshot_and_deduplicates_after_reopen(tmp_path):
    path = tmp_path / "paper.sqlite3"
    order = PaperOrder(
        "order-1",
        "order-key-1",
        "KRW-BTC",
        "BUY",
        requested_quantity_btc=Decimal("0.5"),
    )
    portfolio = PaperPortfolio(cash_krw=Decimal("100000000.0"))
    result = _execution_result()
    journal = PaperEventJournal(path)
    journal.register_order(order, portfolio)

    assert journal.apply_execution_result(
        order,
        portfolio,
        result,
        timestamp_ms=1000,
        idempotency_key="fill-event-1",
    )
    expected_cash = portfolio.cash_krw
    expected_base = portfolio.base_quantity
    expected_transitions = tuple(order.transitions)

    reopened = PaperEventJournal(path)
    recovered_order = reopened.load_order(order.order_id)
    recovered_portfolio = reopened.load_portfolio()
    assert recovered_portfolio.cash_krw == expected_cash
    assert recovered_portfolio.base_quantity == expected_base
    assert recovered_order.status == order.status
    assert tuple(recovered_order.transitions) == expected_transitions

    assert not reopened.apply_execution_result(
        recovered_order,
        recovered_portfolio,
        result,
        timestamp_ms=1000,
        idempotency_key="fill-event-1",
    )
    assert recovered_portfolio.cash_krw == expected_cash
    assert recovered_portfolio.base_quantity == expected_base
    assert recovered_order.filled_quantity == Decimal("0.5")


def test_journal_rejects_reused_event_key_with_different_payload(tmp_path):
    path = tmp_path / "paper.sqlite3"
    order = PaperOrder(
        "order-2",
        "order-key-2",
        "KRW-BTC",
        "BUY",
        requested_quantity_btc=Decimal("0.5"),
    )
    portfolio = PaperPortfolio(cash_krw=Decimal("100000000.0"))
    journal = PaperEventJournal(path)
    journal.register_order(order, portfolio)
    assert journal.apply_execution_result(
        order, portfolio, _execution_result(), 1000, idempotency_key="fill-event-2"
    )
    after_first = (portfolio.cash_krw, portfolio.base_quantity, order.filled_quantity)

    with pytest.raises(PaperJournalIntegrityError, match="reused"):
        journal.apply_execution_result(
            order,
            portfolio,
            _execution_result(fee_rate=0.001),
            1000,
            idempotency_key="fill-event-2",
        )

    assert (portfolio.cash_krw, portfolio.base_quantity, order.filled_quantity) == after_first


def test_journal_detects_corrupt_persisted_snapshot(tmp_path):
    path = tmp_path / "paper.sqlite3"
    journal = PaperEventJournal(path)
    journal.register_order(
        PaperOrder("order-3", "key-3", "KRW-BTC", "BUY"),
        PaperPortfolio(cash_krw=Decimal("100000.0")),
    )
    with sqlite3.connect(path) as db:
        db.execute("UPDATE paper_portfolio SET payload_json = '{}' WHERE singleton = 1")

    with pytest.raises(PaperJournalIntegrityError, match="checksum"):
        journal.load_portfolio()
