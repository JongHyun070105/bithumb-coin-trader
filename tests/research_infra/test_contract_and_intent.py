"""Tests for historical BitMEX contract semantics, intent classification, and cycle confidence model."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from bithumb_coin_trader.research_infra.contract_specs import (
    CONTRACT_SPECS,
    HistoricalContractSpec,
    get_contract_spec,
)
from bithumb_coin_trader.research_infra.external_expert import ExecutionRow
from bithumb_coin_trader.research_infra.external_reconstruction import (
    CycleReconstructor,
    OrderReconstructor,
    PositionCycle,
    PositionEvent,
    PositionReconstructor,
)


def test_xbtusd_inverse_semantics() -> None:
    spec = get_contract_spec("XBTUSD")
    assert spec.is_inverse is True
    assert spec.is_quanto is False

    # 10,000 contracts at $10,000 = $10,000 USD notional, 1.0 BTC notional
    notional = spec.calculate_notional(Decimal("10000"), Decimal("10000"))
    assert notional["contracts"] == Decimal("10000")
    assert notional["usd_notional"] == Decimal("10000.00")
    assert notional["btc_notional"] == Decimal("1.00000000")

    # Long: Entry 10,000, Exit 20,000. Contracts = 10,000
    # PnL in BTC = 10,000 * (1/10,000 - 1/20,000) = 10,000 * (0.0001 - 0.00005) = 0.5 BTC
    pnl = spec.calculate_cycle_pnl_btc("LONG", Decimal("10000"), Decimal("10000"), Decimal("20000"))
    assert pnl == Decimal("0.5")


def test_ethusd_quanto_semantics() -> None:
    spec = get_contract_spec("ETHUSD")
    assert spec.is_inverse is False
    assert spec.is_quanto is True
    assert spec.multiplier == Decimal("0.000001")

    # 400 contracts at $350: USD notional = 400 * 350 = 140,000 USD
    notional = spec.calculate_notional(Decimal("400"), Decimal("350"))
    assert notional["contracts"] == Decimal("400")
    assert notional["usd_notional"] == Decimal("140000.00")
    # BTC notional = 140,000 * 0.000001 = 0.14 BTC
    assert notional["btc_notional"] == Decimal("0.14000000")

    # Long: Entry 300, Exit 400. 100 contracts.
    # Diff = 100 USD. PnL = 100 * 100 * 0.000001 = 0.01 BTC
    pnl = spec.calculate_cycle_pnl_btc("LONG", Decimal("100"), Decimal("300"), Decimal("400"))
    assert pnl == Decimal("0.01")


def test_position_intent_classification() -> None:
    ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
    # Sequence of fills simulating:
    # Flat (0) -> Buy 1000 (OPEN_LONG)
    # Long 1000 -> Buy 500 (ADD_LONG, pos=1500)
    # Long 1500 -> Sell 500 (REDUCE_LONG, pos=1000)
    # Long 1000 -> Sell 1000 (CLOSE_LONG, pos=0)
    # Flat (0) -> Sell 2000 (OPEN_SHORT, pos=-2000)
    # Short -2000 -> Sell 500 (ADD_SHORT, pos=-2500)
    # Short -2500 -> Buy 1000 (REDUCE_SHORT, pos=-1500)
    # Short -1500 -> Buy 1500 (CLOSE_SHORT, pos=0)
    # Flat (0) -> Buy 1000 (OPEN_LONG, pos=1000)
    # Long 1000 -> Sell 2000 (FLIP_LONG_TO_SHORT, pos=-1000)
    # Short -1000 -> Buy 2000 (FLIP_SHORT_TO_LONG, pos=1000)

    def make_fill(minute: int, side: str, qty: str, px: str, oid: str, eid: str, liq: str) -> ExecutionRow:
        return ExecutionRow(
            timestamp=datetime(2020, 1, 1, 0, minute, tzinfo=timezone.utc),
            symbol="XBTUSD",
            side=side,
            price=Decimal(px),
            size=Decimal(qty),
            order_id=oid,
            trade_match_id=f"m_{eid}",
            execution_id=eid,
            execution_type="Trade",
            order_type="Limit",
            liquidity=liq,
            fee=Decimal(0),
            fee_currency="XBt",
            raw_fields={},
        )

    fills = [
        make_fill(1, "Buy", "1000", "10000", "o1", "e1", "Maker"),
        make_fill(2, "Buy", "500", "10000", "o2", "e2", "Maker"),
        make_fill(3, "Sell", "500", "10500", "o3", "e3", "Taker"),
        make_fill(4, "Sell", "1000", "10500", "o4", "e4", "Maker"),
        make_fill(5, "Sell", "2000", "10000", "o5", "e5", "Maker"),
        make_fill(6, "Sell", "500", "10000", "o6", "e6", "Maker"),
        make_fill(7, "Buy", "1000", "9500", "o7", "e7", "Taker"),
        make_fill(8, "Buy", "1500", "9500", "o8", "e8", "Maker"),
        make_fill(9, "Buy", "1000", "10000", "o9", "e9", "Maker"),
        make_fill(10, "Sell", "2000", "10000", "o10", "e10", "Taker"),
        make_fill(11, "Buy", "2000", "10000", "o11", "e11", "Taker"),
    ]

    events = PositionReconstructor.reconstruct_positions(fills)
    intents = [ev.intent for ev in events]

    expected = [
        "OPEN_LONG",
        "ADD_LONG",
        "REDUCE_LONG",
        "CLOSE_LONG",
        "OPEN_SHORT",
        "ADD_SHORT",
        "REDUCE_SHORT",
        "CLOSE_SHORT",
        "OPEN_LONG",
        "FLIP_LONG_TO_SHORT",
        "FLIP_SHORT_TO_LONG",
    ]
    assert intents == expected


def test_position_cycle_confidence_model() -> None:
    ts1 = datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc)
    ts2 = datetime(2020, 1, 1, 1, 0, tzinfo=timezone.utc)

    # 1. Closed cycle starting from flat
    events_closed = [
        PositionEvent(ts1, "XBTUSD", "e1", "Buy", Decimal("1000"), Decimal("1000"), Decimal("10000"), "Maker", Decimal(0), "HIGH", "OPEN_LONG"),
        PositionEvent(ts2, "XBTUSD", "e2", "Sell", Decimal("-1000"), Decimal("0"), Decimal("11000"), "Taker", Decimal(0), "HIGH", "CLOSE_LONG"),
    ]
    cycles_closed = CycleReconstructor.extract_cycles(events_closed)
    assert len(cycles_closed) == 1
    assert cycles_closed[0].confidence_class == "HIGH"
    assert cycles_closed[0].confidence_score == 1.0
    assert cycles_closed[0].left_boundary_censored is False
    assert cycles_closed[0].right_boundary_censored is False

    # 2. Open trailing cycle (right censored)
    events_open = [
        PositionEvent(ts1, "XBTUSD", "e1", "Buy", Decimal("1000"), Decimal("1000"), Decimal("10000"), "Maker", Decimal(0), "HIGH", "OPEN_LONG"),
    ]
    cycles_open = CycleReconstructor.extract_cycles(events_open)
    assert len(cycles_open) == 1
    assert cycles_open[0].confidence_class == "MEDIUM"
    assert cycles_open[0].confidence_score == 0.5
    assert cycles_open[0].right_boundary_censored is True
    assert "RIGHT_BOUNDARY_CENSORED_POSITION_NOT_FLAT_AT_CLOSE" in cycles_open[0].confidence_reasons
