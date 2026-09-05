"""Comprehensive Unit Tests for Deterministic Taker Execution Simulator."""

from datetime import datetime, timezone
import pytest

from bithumb_coin_trader.execution_simulator import (
    CrossedBookError,
    DeterministicTakerSimulator,
    EmptyBookError,
    ExecutionFill,
    ExecutionResult,
    InvalidOrderBookError,
    MarketOrderRequest,
    OrderBookSnapshot,
)


@pytest.fixture
def clean_orderbook() -> OrderBookSnapshot:
    """Fixture providing a well-ordered 5-level order book around 100,000,000 KRW."""
    bids = (
        (99_900_000.0, 1.0),
        (99_800_000.0, 2.0),
        (99_700_000.0, 3.0),
        (99_600_000.0, 4.0),
        (99_500_000.0, 5.0),
    )
    asks = (
        (100_100_000.0, 1.0),
        (100_200_000.0, 2.0),
        (100_300_000.0, 3.0),
        (100_400_000.0, 4.0),
        (100_500_000.0, 5.0),
    )
    return OrderBookSnapshot(
        timestamp=datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc),
        bids=bids,
        asks=asks,
        market="KRW-BTC",
    )


class TestOrderBookValidation:
    def test_clean_orderbook_properties(self, clean_orderbook: OrderBookSnapshot):
        assert clean_orderbook.best_bid == 99_900_000.0
        assert clean_orderbook.best_ask == 100_100_000.0
        assert clean_orderbook.mid_price == 100_000_000.0
        assert clean_orderbook.spread == 200_000.0
        assert pytest.approx(clean_orderbook.spread_bps, rel=1e-5) == 20.0
        assert clean_orderbook.total_bid_size == 15.0
        assert clean_orderbook.total_ask_size == 15.0

    def test_crossed_orderbook_raises(self):
        bids = ((100_500_000.0, 1.0), (99_000_000.0, 1.0))
        asks = ((100_000_000.0, 1.0), (101_000_000.0, 1.0))
        with pytest.raises(CrossedBookError, match="Crossed or locked"):
            OrderBookSnapshot(timestamp=1000, bids=bids, asks=asks)

    def test_locked_orderbook_raises(self):
        bids = ((100_000_000.0, 1.0),)
        asks = ((100_000_000.0, 1.0),)
        with pytest.raises(CrossedBookError, match="Crossed or locked"):
            OrderBookSnapshot(timestamp=1000, bids=bids, asks=asks)

    def test_unsorted_bids_raises(self):
        bids = ((99_000_000.0, 1.0), (99_500_000.0, 1.0))  # Ascending instead of descending
        asks = ((100_000_000.0, 1.0),)
        with pytest.raises(InvalidOrderBookError, match="Bids must be sorted descending"):
            OrderBookSnapshot(timestamp=1000, bids=bids, asks=asks)

    def test_unsorted_asks_raises(self):
        bids = ((99_000_000.0, 1.0),)
        asks = ((101_000_000.0, 1.0), (100_500_000.0, 1.0))  # Descending instead of ascending
        with pytest.raises(InvalidOrderBookError, match="Asks must be sorted ascending"):
            OrderBookSnapshot(timestamp=1000, bids=bids, asks=asks)

    def test_empty_orderbook_raises(self):
        with pytest.raises(EmptyBookError):
            OrderBookSnapshot(timestamp=1000, bids=(), asks=((100_000_000.0, 1.0),))
        with pytest.raises(EmptyBookError):
            OrderBookSnapshot(timestamp=1000, bids=((99_000_000.0, 1.0),), asks=())


class TestDeterministicExecution:
    def test_single_level_buy_exact_quantity(self, clean_orderbook: OrderBookSnapshot):
        req = MarketOrderRequest(
            timestamp=datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc),
            side="BUY",
            requested_quantity_btc=0.5,
            fee_rate=0.0004,
        )
        res = DeterministicTakerSimulator.execute_order(req, clean_orderbook)

        assert res.is_filled
        assert res.status == "FILLED"
        assert res.side == "BUY"
        assert res.filled_quantity == 0.5
        assert res.unfilled_quantity == 0.0
        assert res.vwap_price == 100_100_000.0
        assert res.filled_amount_krw == 50_050_000.0
        assert pytest.approx(res.fee_paid_krw) == 50_050_000.0 * 0.0004
        assert len(res.fills) == 1
        assert res.fills[0].level_index == 0
        assert res.fills[0].price == 100_100_000.0
        assert res.fills[0].size == 0.5

    def test_single_level_sell_exact_amount(self, clean_orderbook: OrderBookSnapshot):
        req = MarketOrderRequest(
            timestamp=datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc),
            side="SELL",
            requested_amount_krw=49_950_000.0,  # exactly 0.5 BTC at 99,900,000 KRW
            fee_rate=0.0004,
        )
        res = DeterministicTakerSimulator.execute_order(req, clean_orderbook)

        assert res.is_filled
        assert res.side == "SELL"
        assert pytest.approx(res.filled_quantity) == 0.5
        assert res.filled_amount_krw == 49_950_000.0
        assert res.vwap_price == 99_900_000.0
        assert pytest.approx(res.fee_paid_krw) == 49_950_000.0 * 0.0004
        assert len(res.fills) == 1
        assert res.fills[0].price == 99_900_000.0

    def test_multi_level_walk_buy_through_three_levels(self, clean_orderbook: OrderBookSnapshot):
        # Level 0: 1.0 @ 100,100,000 = 100,100,000 KRW
        # Level 1: 2.0 @ 100,200,000 = 200,400,000 KRW
        # Level 2: 0.5 @ 100,300,000 =  50,150,000 KRW
        # Total BTC = 3.5 BTC, total KRW = 350,650,000 KRW
        # Expected VWAP = 350,650,000 / 3.5 = 100,185,714.28571429
        req = MarketOrderRequest(
            timestamp=1000,
            side="BUY",
            requested_quantity_btc=3.5,
            fee_rate=0.0004,
        )
        res = DeterministicTakerSimulator.execute_order(req, clean_orderbook)

        assert res.is_filled
        assert res.filled_quantity == 3.5
        assert len(res.fills) == 3
        assert res.fills[0].level_index == 0
        assert res.fills[0].size == 1.0
        assert res.fills[1].level_index == 1
        assert res.fills[1].size == 2.0
        assert res.fills[2].level_index == 2
        assert res.fills[2].size == 0.5
        assert pytest.approx(res.filled_amount_krw) == 350_650_000.0
        assert pytest.approx(res.vwap_price) == 350_650_000.0 / 3.5
        # Top of book was 100,100,000. Slippage vs top:
        expected_slip_top = ((res.vwap_price - 100_100_000.0) / 100_100_000.0) * 10_000.0
        assert pytest.approx(res.slippage_vs_top_bps) == expected_slip_top

    def test_reverse_side_invariant_buy_walks_asks_sell_walks_bids(self, clean_orderbook: OrderBookSnapshot):
        req_buy = MarketOrderRequest(timestamp=1000, side="BUY", requested_quantity_btc=1.0)
        req_sell = MarketOrderRequest(timestamp=1000, side="SELL", requested_quantity_btc=1.0)

        res_buy = DeterministicTakerSimulator.execute_order(req_buy, clean_orderbook)
        res_sell = DeterministicTakerSimulator.execute_order(req_sell, clean_orderbook)

        # Buyer pays ask price, seller receives bid price
        assert res_buy.vwap_price == clean_orderbook.best_ask
        assert res_sell.vwap_price == clean_orderbook.best_bid
        assert res_buy.vwap_price > res_sell.vwap_price

    def test_book_exhaustion_partial_fill_allowed(self, clean_orderbook: OrderBookSnapshot):
        # Total ask depth is 15.0 BTC. Request 20.0 BTC with allow_partial=True.
        req = MarketOrderRequest(
            timestamp=1000,
            side="BUY",
            requested_quantity_btc=20.0,
            allow_partial=True,
        )
        res = DeterministicTakerSimulator.execute_order(req, clean_orderbook)

        assert res.is_partial
        assert res.status == "PARTIALLY_FILLED"
        assert res.filled_quantity == 15.0
        assert pytest.approx(res.unfilled_quantity) == 5.0
        assert len(res.fills) == 5

    def test_book_exhaustion_partial_fill_prohibited(self, clean_orderbook: OrderBookSnapshot):
        # Total ask depth is 15.0 BTC. Request 20.0 BTC with allow_partial=False.
        req = MarketOrderRequest(
            timestamp=1000,
            side="BUY",
            requested_quantity_btc=20.0,
            allow_partial=False,
        )
        res = DeterministicTakerSimulator.execute_order(req, clean_orderbook)

        assert res.is_rejected
        assert res.status == "REJECTED"
        assert res.filled_quantity == 0.0
        assert res.rejection_reason == "INSUFFICIENT_DEPTH_PARTIAL_PROHIBITED"
        assert len(res.fills) == 0

    def test_determinism_identical_runs(self, clean_orderbook: OrderBookSnapshot):
        req1 = MarketOrderRequest(timestamp=1000, side="BUY", requested_amount_krw=75_000_000.0)
        req2 = MarketOrderRequest(timestamp=1000, side="BUY", requested_amount_krw=75_000_000.0)

        res1 = DeterministicTakerSimulator.execute_order(req1, clean_orderbook)
        res2 = DeterministicTakerSimulator.execute_order(req2, clean_orderbook)

        assert res1.filled_quantity == res2.filled_quantity
        assert res1.filled_amount_krw == res2.filled_amount_krw
        assert res1.vwap_price == res2.vwap_price
        assert res1.fee_paid_krw == res2.fee_paid_krw
        assert res1.slippage_vs_mid_bps == res2.slippage_vs_mid_bps
        assert res1.fills == res2.fills


class TestLatencySimulation:
    def test_latency_adverse_selection(self):
        # T0 book: best ask = 100,000,000
        # T+100ms book: market moved up, best ask = 100,500,000
        book_t0 = OrderBookSnapshot(
            timestamp=1000.0,
            bids=((99_900_000.0, 1.0),),
            asks=((100_000_000.0, 1.0),),
        )
        book_t1 = OrderBookSnapshot(
            timestamp=1000.1,  # +100ms
            bids=((100_400_000.0, 1.0),),
            asks=((100_500_000.0, 1.0),),
        )
        stream = [book_t0, book_t1]

        req = MarketOrderRequest(
            timestamp=1000.0,
            side="BUY",
            requested_quantity_btc=0.5,
            latency_delay_ms=100.0,
        )

        res = DeterministicTakerSimulator.execute_with_latency(req, stream)

        assert res.is_filled
        assert res.vwap_price == 100_500_000.0
        assert res.top_of_book_at_order == 100_000_000.0
        assert res.top_of_book_at_fill == 100_500_000.0
        # Adverse movement = (100,500,000 - 100,000,000) / 100,000,000 = 0.005 = 50 bps
        assert pytest.approx(res.adverse_selection_bps) == 50.0
        assert res.slippage_vs_top_bps == 50.0

    def test_zero_latency_immediate_execution(self, clean_orderbook: OrderBookSnapshot):
        req = MarketOrderRequest(
            timestamp=clean_orderbook.timestamp,
            side="BUY",
            requested_quantity_btc=1.0,
            latency_delay_ms=0.0,
        )
        res = DeterministicTakerSimulator.execute_with_latency(req, [clean_orderbook])
        assert res.is_filled
        assert res.adverse_selection_bps == 0.0
        assert res.vwap_price == clean_orderbook.best_ask
