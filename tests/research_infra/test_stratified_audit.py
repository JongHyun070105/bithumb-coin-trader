"""Unit test for stratified audit logic on synthetic orders, positions, and cycles."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from bithumb_coin_trader.research_infra.external_reconstruction import (
    OrderSummary,
    PositionCycle,
    PositionEvent,
)
from bithumb_coin_trader.research_infra.stratified_audit import (
    audit_cycle_sample,
    audit_order_sample,
    audit_position_sample,
)


def test_audit_order_sample_synthetic() -> None:
    order = OrderSummary(
        order_id="test-ord-1",
        symbol="XBTUSD",
        side="Buy",
        order_type="Limit",
        first_execution_time=datetime(2020, 1, 1, tzinfo=timezone.utc),
        last_execution_time=datetime(2020, 1, 1, 0, 1, tzinfo=timezone.utc),
        execution_count=1,
        maker_execution_count=1,
        taker_execution_count=0,
        filled_quantity=Decimal("100"),
        order_quantity=Decimal("100"),
        average_execution_price=Decimal("10000"),
        fees=Decimal("-0.000025"),
        fee_currency="XBt",
        status="Filled",
        reconstruction_confidence="RECONSTRUCTED",
    )
    result = audit_order_sample([order])
    assert result["sample_count"] == 1
    assert result["mismatch_count"] == 0
    assert result["mismatch_rate"] == 0.0


def test_audit_position_sample_synthetic() -> None:
    event = PositionEvent(
        timestamp=datetime(2020, 1, 1, tzinfo=timezone.utc),
        symbol="XBTUSD",
        execution_id="exec-1",
        side="Buy",
        signed_quantity_delta=Decimal("100"),
        estimated_position_after=Decimal("100"),
        execution_price=Decimal("10000"),
        maker_taker="Maker",
        fee=Decimal("-0.000025"),
        confidence="HIGH",
        intent="OPEN_LONG",
    )
    result = audit_position_sample([event])
    assert result["sample_count"] == 1
    assert result["mismatch_count"] == 0
    assert result["mismatch_rate"] == 0.0


def test_audit_cycle_sample_synthetic() -> None:
    cycle = PositionCycle(
        cycle_id="cycle-1",
        symbol="XBTUSD",
        direction="LONG",
        open_time=datetime(2020, 1, 1, tzinfo=timezone.utc),
        close_time=datetime(2020, 1, 1, 1, 0, tzinfo=timezone.utc),
        duration_seconds=3600.0,
        gross_execution_pnl_estimate=Decimal("0.000909"),
        fees=Decimal("-0.00005"),
        funding=Decimal("0"),
        net_estimate=Decimal("0.000859"),
        max_position=Decimal("100"),
        entry_vwap=Decimal("10000"),
        exit_vwap=Decimal("11000"),
        maker_ratio=1.0,
        execution_count=2,
        confidence="RECONSTRUCTED",
        confidence_score=1.0,
        confidence_class="HIGH",
        confidence_reasons=(),
        left_boundary_censored=False,
        right_boundary_censored=False,
    )
    result = audit_cycle_sample([cycle])
    assert result["sample_count"] == 1
    assert result["mismatch_count"] == 0
    assert result["mismatch_rate"] == 0.0
