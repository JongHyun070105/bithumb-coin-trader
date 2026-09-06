import pytest
import random
from decimal import Decimal
from bithumb_coin_trader.canonical_market_data import CanonicalOrderBook
from bithumb_coin_trader.execution_simulator import DeterministicTakerSimulator, MarketOrderRequest
from bithumb_coin_trader.paper_engine import PaperPortfolio, PaperOrder


def test_property_fuzzing_simulation_and_portfolio():
    rng = random.Random(1337)
    portfolio = PaperPortfolio(cash_krw=Decimal("1000000000.0"))

    for i in range(100):
        mid_price = rng.uniform(50_000_000.0, 150_000_000.0)
        spread = rng.uniform(1_000.0, 50_000.0)
        best_bid = mid_price - spread / 2.0
        best_ask = mid_price + spread / 2.0

        bids = ((best_bid, rng.uniform(0.1, 5.0)), (best_bid - 5000.0, rng.uniform(0.1, 5.0)))
        asks = ((best_ask, rng.uniform(0.1, 5.0)), (best_ask + 5000.0, rng.uniform(0.1, 5.0)))

        ob = CanonicalOrderBook(
            exchange="bithumb",
            market="KRW-BTC",
            exchange_timestamp_ms=1000 + i * 100,
            receive_timestamp_ms=1010 + i * 100,
            bids=bids,
            asks=asks,
        )

        side = rng.choice(["BUY", "SELL"])
        if side == "SELL" and portfolio.base_quantity <= Decimal("0.001"):
            side = "BUY"

        if side == "BUY":
            qty = rng.uniform(0.01, 1.0)
            req = MarketOrderRequest(
                timestamp=ob.receive_timestamp_ms / 1000.0,
                side="BUY",
                requested_quantity_btc=qty,
            )
        else:
            sell_qty = min(float(portfolio.base_quantity), rng.uniform(0.01, 0.5))
            req = MarketOrderRequest(
                timestamp=ob.receive_timestamp_ms / 1000.0,
                side="SELL",
                requested_quantity_btc=sell_qty,
            )

        res = DeterministicTakerSimulator.execute_order(req, ob)
        if res.is_filled:
            order = PaperOrder(f"fuzz_{i}", f"key_{i}", "KRW-BTC", side)
            portfolio.apply_execution_result(order, res, ob.receive_timestamp_ms)

        # Invariants assertion on every iteration
        assert portfolio.cash_krw >= Decimal("0.0")
        assert portfolio.base_quantity >= Decimal("0.0")
        assert portfolio.cost_basis_krw >= Decimal("0.0")
