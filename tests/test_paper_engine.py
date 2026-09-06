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
