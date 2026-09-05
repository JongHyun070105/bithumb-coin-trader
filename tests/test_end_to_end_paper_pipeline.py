import pytest
from decimal import Decimal
from bithumb_coin_trader.canonical_market_data import CanonicalOrderBook
from bithumb_coin_trader.synthetic_market import SignalMarketGenerator
from bithumb_coin_trader.replay import MultiStreamReplay
from bithumb_coin_trader.microstructure_features import MicrostructureFeatureEngine
from bithumb_coin_trader.risk_engine import RiskEngine, RiskVerdict
from bithumb_coin_trader.execution_simulator import DeterministicTakerSimulator, MarketOrderRequest
from bithumb_coin_trader.paper_engine import PaperOrder, PaperPortfolio, OrderStatus


def test_end_to_end_paper_pipeline(monkeypatch):
    import time
    # Strictly forbid wall-clock access during pipeline
    monkeypatch.setattr(time, "sleep", lambda *args: (_ for _ in ()).throw(AssertionError("time.sleep called")))

    # 1. Generate 50 synthetic signal orderbooks
    gen = SignalMarketGenerator(initial_price=100_000_000.0, seed=777)
    books, signals = gen.generate_signal_orderbooks(count=50, interval_ms=100)

    # 2. Setup Replay, Feature Engine, Risk Engine, Simulator, Portfolio
    from bithumb_coin_trader.microstructure_features import compute_ofi_v2
    replay = MultiStreamReplay([iter(books)])
    risk_engine = RiskEngine()
    portfolio = PaperPortfolio(cash_krw=Decimal("100000000.0"))

    executed_orders = 0
    trade_history = []

    # Stream through replay
    prev_ob = None
    for ev in replay:
        ob = ev.payload
        if prev_ob is None:
            prev_ob = ob
            continue

        ofi = compute_ofi_v2(prev_ob, ob)
        prev_ob = ob

        # Strategy logic: buy on positive OFI, sell on negative OFI
        if ofi > 0.5 and portfolio.base_quantity < Decimal("1.0"):
            order_id = f"buy_{ev.timestamp_ms}"
            verdict, reasons, _ = risk_engine.evaluate_preflight(
                order_id=order_id,
                side="BUY",
                requested_notional_krw=10_000_000.0,
                current_equity_krw=float(portfolio.cash_krw),
                current_position_notional_krw=float(portfolio.base_quantity) * ob.mid_price,
                daily_loss_fraction=0.0,
                orderbook=ob,
                current_time_ms=ev.timestamp_ms,
            )
            if verdict == RiskVerdict.ALLOW:
                req = MarketOrderRequest(
                    timestamp=ev.timestamp_ms / 1000.0,
                    side="BUY",
                    requested_amount_krw=10_000_000.0,
                    latency_delay_ms=0.0,
                )
                res = DeterministicTakerSimulator.execute_order(req, ob)
                if res.is_filled:
                    p_order = PaperOrder(order_id, f"key_{order_id}", "KRW-BTC", "BUY")
                    portfolio.apply_execution_result(p_order, res, ev.timestamp_ms)
                    executed_orders += 1
                    trade_history.append((order_id, res.filled_quantity, res.vwap_price))

        elif ofi < -1.0 and portfolio.base_quantity > Decimal("0.0"):
            order_id = f"sell_{ev.timestamp_ms}"
            verdict, reasons, _ = risk_engine.evaluate_preflight(
                order_id=order_id,
                side="SELL",
                requested_notional_krw=float(portfolio.base_quantity) * ob.mid_price,
                current_equity_krw=float(portfolio.cash_krw),
                current_position_notional_krw=float(portfolio.base_quantity) * ob.mid_price,
                daily_loss_fraction=0.0,
                orderbook=ob,
                current_time_ms=ev.timestamp_ms,
            )
            if verdict == RiskVerdict.ALLOW:
                req = MarketOrderRequest(
                    timestamp=ev.timestamp_ms / 1000.0,
                    side="SELL",
                    requested_quantity_btc=float(portfolio.base_quantity),
                    latency_delay_ms=0.0,
                )
                res = DeterministicTakerSimulator.execute_order(req, ob)
                if res.is_filled:
                    p_order = PaperOrder(order_id, f"key_{order_id}", "KRW-BTC", "SELL")
                    portfolio.apply_execution_result(p_order, res, ev.timestamp_ms)
                    executed_orders += 1
                    trade_history.append((order_id, res.filled_quantity, res.vwap_price))

    # Assertions
    assert executed_orders > 0
    assert portfolio.cash_krw >= Decimal(0)
    assert portfolio.base_quantity >= Decimal(0)
    assert portfolio.total_fees_paid_krw > Decimal(0)
