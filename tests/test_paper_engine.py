import pytest
from decimal import Decimal
from bithumb_coin_trader.execution_simulator import (
    DeterministicTakerSimulator,
    MarketOrderRequest,
    OrderBookSnapshot,
)
from bithumb_coin_trader.paper_engine import (
    OrderStatus,
    PaperOrder,
    PaperPortfolio,
    IllegalOrderStateTransitionError,
    NegativeBalanceError,
    CashConservationError,
)


def test_order_state_transitions():
    order = PaperOrder("ord_1", "key_1", "KRW-BTC", "BUY")
    assert order.status == OrderStatus.CREATED

    # Valid progression
    assert order.transition_to(OrderStatus.RISK_APPROVED, 100)
    assert order.transition_to(OrderStatus.SUBMITTED, 101)
    assert order.transition_to(OrderStatus.PENDING_FILL, 102)
    assert order.transition_to(OrderStatus.PARTIALLY_FILLED, 103)
    assert order.transition_to(OrderStatus.FILLED, 104)
    assert order.status == OrderStatus.FILLED

    # Illegal transition from terminal state FILLED
    with pytest.raises(IllegalOrderStateTransitionError):
        order.transition_to(OrderStatus.SUBMITTED, 105)


def test_illegal_jump_transitions():
    order = PaperOrder("ord_2", "key_2", "KRW-BTC", "BUY")
    # Cannot jump directly from CREATED to FILLED
    with pytest.raises(IllegalOrderStateTransitionError):
        order.transition_to(OrderStatus.FILLED, 100)


def test_illegal_transition_does_not_consume_idempotency_key():
    order = PaperOrder("ord_retry", "key_retry", "KRW-BTC", "BUY")

    with pytest.raises(IllegalOrderStateTransitionError):
        order.transition_to(OrderStatus.SUBMITTED, 100, idempotency_key="event-1")

    assert order.transition_to(OrderStatus.RISK_APPROVED, 101, idempotency_key="event-1")


def test_idempotency():
    order = PaperOrder("ord_3", "key_3", "KRW-BTC", "BUY")
    assert order.transition_to(OrderStatus.RISK_APPROVED, 100, idempotency_key="tx_1") is True
    # Re-applying same idempotency key is a no-op returning False
    assert order.transition_to(OrderStatus.RISK_APPROVED, 100, idempotency_key="tx_1") is False
    assert order.status == OrderStatus.RISK_APPROVED
    assert len(order.transitions) == 1


def test_cash_conservation_buy_and_sell():
    portfolio = PaperPortfolio(cash_krw=Decimal("100000000.0"))
    initial_wealth = portfolio.cash_krw

    # BUY 0.5 BTC at 100M KRW with 0.04% fee
    buy_price = Decimal("100000000.0")
    buy_qty = Decimal("0.5")
    buy_fee = buy_price * buy_qty * Decimal("0.0004")  # 20,000 KRW
    portfolio.apply_fill("BUY", buy_price, buy_qty, buy_fee)

    assert portfolio.base_quantity == Decimal("0.5")
    assert portfolio.cash_krw == Decimal("100000000.0") - Decimal("50000000.0") - buy_fee
    assert portfolio.cost_basis_krw == Decimal("50000000.0")

    # SELL 0.5 BTC at 110M KRW with 0.04% fee
    sell_price = Decimal("110000000.0")
    sell_qty = Decimal("0.5")
    sell_notional = sell_price * sell_qty  # 55M KRW
    sell_fee = sell_notional * Decimal("0.0004")  # 22,000 KRW
    cost_basis_before_sell = portfolio.cost_basis_krw
    pnl = portfolio.apply_fill("SELL", sell_price, sell_qty, sell_fee)

    expected_pnl = sell_notional - cost_basis_before_sell - sell_fee  # 55M - 50M - 22k = 4,978,000
    assert portfolio.base_quantity == Decimal("0.0")
    assert portfolio.cost_basis_krw == Decimal("0.0")
    assert pnl == expected_pnl
    assert portfolio.realized_pnl_krw == expected_pnl
    # Net wealth change equals realized PnL minus buy fee
    assert (portfolio.cash_krw - initial_wealth) == (expected_pnl - buy_fee)


def test_negative_balance_prevention():
    portfolio = PaperPortfolio(cash_krw=Decimal("1000.0"), base_quantity=Decimal("0.0"))

    # Attempt to BUY 10,000 KRW with only 1,000 KRW available
    with pytest.raises(NegativeBalanceError, match="Insufficient cash"):
        portfolio.apply_fill("BUY", Decimal("10000.0"), Decimal("1.0"), Decimal("4.0"))

    # Attempt to SELL without having base asset
    with pytest.raises(NegativeBalanceError, match="Insufficient base quantity"):
        portfolio.apply_fill("SELL", Decimal("10000.0"), Decimal("1.0"), Decimal("4.0"))


@pytest.mark.parametrize("fee", [Decimal("-0.01"), Decimal("NaN")])
def test_invalid_fill_fee_is_rejected_without_balance_changes(fee: Decimal):
    portfolio = PaperPortfolio(cash_krw=Decimal("100000.0"))

    with pytest.raises(ValueError, match="fee"):
        portfolio.apply_fill("BUY", Decimal("10000.0"), Decimal("1.0"), fee)

    assert portfolio.cash_krw == Decimal("100000.0")
    assert portfolio.base_quantity == Decimal("0.0")


def test_execution_result_integration():
    portfolio = PaperPortfolio(cash_krw=Decimal("100000000.0"))
    book = OrderBookSnapshot(
        timestamp=1000.0,
        bids=((99_000_000.0, 1.0),),
        asks=((100_000_000.0, 0.2), (100_100_000.0, 0.3)),
    )
    req = MarketOrderRequest(
        timestamp=1000.0,
        side="BUY",
        requested_quantity_btc=0.5,
        fee_rate=0.0004,
    )
    res = DeterministicTakerSimulator.execute_order(req, book)
    assert res.is_filled

    order = PaperOrder("ord_e2e", "key_e2e", "KRW-BTC", "BUY")
    portfolio.apply_execution_result(order, res, timestamp_ms=1000)

    assert order.status == OrderStatus.FILLED
    assert order.filled_quantity == Decimal("0.5")
    assert portfolio.base_quantity == Decimal("0.5")
    assert portfolio.cash_krw < Decimal("50000000.0")


def test_replaying_execution_result_does_not_apply_fill_twice():
    portfolio = PaperPortfolio(cash_krw=Decimal("100000000.0"))
    book = OrderBookSnapshot(
        timestamp=1000.0,
        bids=((99_000_000.0, 1.0),),
        asks=((100_000_000.0, 0.2), (100_100_000.0, 0.3)),
    )
    request = MarketOrderRequest(
        timestamp=1000.0,
        side="BUY",
        requested_quantity_btc=0.5,
        fee_rate=0.0004,
    )
    result = DeterministicTakerSimulator.execute_order(request, book)
    order = PaperOrder("ord_replay", "key_replay", "KRW-BTC", "BUY")

    assert portfolio.apply_execution_result(order, result, timestamp_ms=1000)
    after_first = (
        portfolio.cash_krw,
        portfolio.base_quantity,
        portfolio.total_fees_paid_krw,
        tuple(order.transitions),
    )
    assert not portfolio.apply_execution_result(order, result, timestamp_ms=1000)

    assert (
        portfolio.cash_krw,
        portfolio.base_quantity,
        portfolio.total_fees_paid_krw,
        tuple(order.transitions),
    ) == after_first


def test_execution_result_side_and_market_must_match_order():
    portfolio = PaperPortfolio(cash_krw=Decimal("100000000.0"))
    book = OrderBookSnapshot(
        timestamp=1000.0,
        bids=((99_000_000.0, 1.0),),
        asks=((100_000_000.0, 1.0),),
    )
    result = DeterministicTakerSimulator.execute_order(
        MarketOrderRequest(timestamp=1000.0, side="BUY", requested_quantity_btc=0.1),
        book,
    )
    mismatched_order = PaperOrder("ord_wrong", "key_wrong", "KRW-BTC", "SELL")

    with pytest.raises(ValueError, match="side does not match"):
        portfolio.apply_execution_result(mismatched_order, result, timestamp_ms=1000)

    assert portfolio.cash_krw == Decimal("100000000.0")
    assert portfolio.base_quantity == Decimal("0.0")
    assert mismatched_order.status == OrderStatus.CREATED


def test_execution_result_market_must_match_order():
    portfolio = PaperPortfolio(cash_krw=Decimal("100000000.0"))
    book = OrderBookSnapshot(
        timestamp=1000.0,
        bids=((99_000_000.0, 1.0),),
        asks=((100_000_000.0, 1.0),),
        market="KRW-BTC",
    )
    result = DeterministicTakerSimulator.execute_order(
        MarketOrderRequest(timestamp=1000.0, side="BUY", requested_quantity_btc=0.1),
        book,
    )
    mismatched_order = PaperOrder("ord_wrong_market", "key_wrong_market", "KRW-ETH", "BUY")

    with pytest.raises(ValueError, match="market does not match"):
        portfolio.apply_execution_result(mismatched_order, result, timestamp_ms=1000)

    assert portfolio.cash_krw == Decimal("100000000.0")
    assert portfolio.base_quantity == Decimal("0.0")


def test_execution_result_cannot_overfill_order_after_partial_fill():
    portfolio = PaperPortfolio(
        cash_krw=Decimal("95000000.0"),
        base_quantity=Decimal("0.05"),
        cost_basis_krw=Decimal("5000000.0"),
    )
    book = OrderBookSnapshot(
        timestamp=1000.0,
        bids=((99_000_000.0, 1.0),),
        asks=((100_000_000.0, 1.0),),
    )
    result = DeterministicTakerSimulator.execute_order(
        MarketOrderRequest(timestamp=1000.0, side="BUY", requested_quantity_btc=0.1),
        book,
    )
    order = PaperOrder(
        "ord_overfill",
        "key_overfill",
        "KRW-BTC",
        "BUY",
        status=OrderStatus.PARTIALLY_FILLED,
        requested_quantity_btc=Decimal("0.1"),
        filled_quantity=Decimal("0.05"),
        filled_amount_krw=Decimal("5000000.0"),
    )

    with pytest.raises(ValueError, match="overfills requested quantity"):
        portfolio.apply_execution_result(order, result, timestamp_ms=1000)

    assert portfolio.cash_krw == Decimal("95000000.0")
    assert portfolio.base_quantity == Decimal("0.05")
    assert order.status == OrderStatus.PARTIALLY_FILLED
    assert order.processed_execution_keys == set()


def test_multi_level_fill_failure_rolls_back_order_and_portfolio():
    portfolio = PaperPortfolio(cash_krw=Decimal("50030000.0"))
    book = OrderBookSnapshot(
        timestamp=1000.0,
        bids=((99_000_000.0, 1.0),),
        asks=((100_000_000.0, 0.5), (101_000_000.0, 0.5)),
    )
    result = DeterministicTakerSimulator.execute_order(
        MarketOrderRequest(
            timestamp=1000.0,
            side="BUY",
            requested_quantity_btc=0.75,
            fee_rate=0.0004,
        ),
        book,
    )
    assert result.status == "FILLED"
    assert len(result.fills) == 2
    order = PaperOrder("ord_insufficient", "key_insufficient", "KRW-BTC", "BUY")

    with pytest.raises(NegativeBalanceError, match="Insufficient cash"):
        portfolio.apply_execution_result(order, result, timestamp_ms=1000)

    assert portfolio.cash_krw == Decimal("50030000.0")
    assert portfolio.base_quantity == Decimal("0.0")
    assert portfolio.total_fees_paid_krw == Decimal("0.0")
    assert order.status == OrderStatus.CREATED
    assert order.transitions == []
    assert order.processed_execution_keys == set()


def test_fill_can_arrive_while_cancellation_is_pending():
    portfolio = PaperPortfolio(cash_krw=Decimal("100000000.0"))
    book = OrderBookSnapshot(
        timestamp=1000.0,
        bids=((99_000_000.0, 1.0),),
        asks=((100_000_000.0, 1.0),),
    )
    result = DeterministicTakerSimulator.execute_order(
        MarketOrderRequest(timestamp=1000.0, side="BUY", requested_quantity_btc=0.1),
        book,
    )
    order = PaperOrder("ord_cancel_race", "key_cancel_race", "KRW-BTC", "BUY")
    for status in (
        OrderStatus.RISK_APPROVED,
        OrderStatus.SUBMITTED,
        OrderStatus.PENDING_FILL,
        OrderStatus.CANCEL_PENDING,
    ):
        assert order.transition_to(status, 1000 + len(order.transitions))

    assert portfolio.apply_execution_result(order, result, timestamp_ms=1005, idempotency_key="fill-event")
    assert order.status == OrderStatus.FILLED
    assert portfolio.base_quantity == Decimal("0.1")
    with pytest.raises(IllegalOrderStateTransitionError):
        order.transition_to(OrderStatus.CANCELLED, 1006, idempotency_key="late-cancel-ack")
