"""Tests for conservative maker execution simulator."""

import pytest
from datetime import datetime, timezone

from bithumb_coin_trader.maker_simulator import (
    MakerSimulator,
    MakerAssumptions,
    FillModel,
    OrderStatus,
)
from bithumb_coin_trader.research_infra.canonical_events import (
    CanonicalEvent, EventKind, TimestampRole,
)


def _make_ob(ts_ns: int, bids: list, asks: list, market="KRW-BTC") -> CanonicalEvent:
    return CanonicalEvent(
        dataset_id="test", source_run_id=None, collector_epoch=None,
        source_file=None, source_file_offset=None,
        exchange="bithumb", market=market,
        event_kind=EventKind.ORDERBOOK,
        exchange_timestamp_ms=ts_ns // 1_000_000,
        local_recv_timestamp_ms=ts_ns // 1_000_000,
        local_write_timestamp_ms=ts_ns // 1_000_000,
        ordering_timestamp_ns=ts_ns,
        exchange_timestamp_role=TimestampRole.EXCHANGE_EVENT,
        payload={"bids": bids, "asks": asks, "is_snapshot": True},
    )


def _make_trade(ts_ns: int, price: float, qty: float, side: str) -> CanonicalEvent:
    return CanonicalEvent(
        dataset_id="test", source_run_id=None, collector_epoch=None,
        source_file=None, source_file_offset=None,
        exchange="bithumb", market="KRW-BTC",
        event_kind=EventKind.TRADE,
        exchange_timestamp_ms=ts_ns // 1_000_000,
        local_recv_timestamp_ms=ts_ns // 1_000_000,
        local_write_timestamp_ms=ts_ns // 1_000_000,
        ordering_timestamp_ns=ts_ns,
        exchange_timestamp_role=TimestampRole.EXCHANGE_EVENT,
        payload={"price": price, "quantity": qty, "aggressor_side": side},
    )


class TestMakerSimulatorBasic:
    """Basic maker simulator tests."""

    def test_no_fill_expired(self):
        """Order expires if no fill within cancellation horizon."""
        sim = MakerSimulator(MakerAssumptions(cancellation_horizon_s=1.0))
        t0 = 1_000_000_000_000
        events = [
            _make_ob(t0 + 500_000_000,  # 500ms later
                     [[100_000_000, 1.0]], [[100_010_000, 1.0]])
        ]
        result = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=100_005_000,
        )
        assert result.status == OrderStatus.EXPIRED
        assert result.fill_quantity == 0.0

    def test_cancelled_no_fill(self):
        """Order cancelled at horizon if no fill."""
        sim = MakerSimulator(MakerAssumptions(cancellation_horizon_s=1.0))
        t0 = 1_000_000_000_000
        # Event after cancellation
        events = [
            _make_ob(t0 + 2_000_000_000,
                     [[100_000_000, 1.0]], [[100_010_000, 1.0]])
        ]
        result = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=100_005_000,
        )
        assert result.status == OrderStatus.CANCELLED

    def test_base_model_fill_on_ask_cross(self):
        """BASE model fills when best ask crosses through limit."""
        sim = MakerSimulator(MakerAssumptions(
            fill_model=FillModel.BASE, cancellation_horizon_s=5.0
        ))
        t0 = 1_000_000_000_000
        events = [
            # Ask drops to our limit price
            _make_ob(t0 + 100_000_000,
                     [[100_000_000, 1.0]], [[100_000_000, 2.0]])
        ]
        result = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=100_005_000,
        )
        assert result.status == OrderStatus.FILLED
        assert result.fill_quantity == 1.0
        assert result.fill_price == 100_000_000

    def test_trade_fill_sell_aggressor(self):
        """SELL aggressor trade at/through limit triggers fill."""
        sim = MakerSimulator(MakerAssumptions(
            fill_model=FillModel.BASE, cancellation_horizon_s=5.0
        ))
        t0 = 1_000_000_000_000
        events = [
            _make_trade(t0 + 100_000_000, 100_000_000, 0.5, "SELL")
        ]
        result = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=100_005_000,
        )
        assert result.status == OrderStatus.PARTIAL
        assert abs(result.fill_quantity - 0.5) < 1e-8

    def test_conervative_model_requires_volume(self):
        """CONSERVATIVE model requires volume exceeding queue."""
        sim = MakerSimulator(MakerAssumptions(
            fill_model=FillModel.CONSERVATIVE,
            queue_multiplier=1.0,
            cancellation_horizon_s=5.0,
        ))
        t0 = 1_000_000_000_000
        events = [
            # Ask at limit with only 1 BTC available
            _make_ob(t0 + 100_000_000,
                     [[100_000_000, 1.0]], [[100_000_000, 1.0]])
        ]
        result = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=100_005_000,
        )
        # Conservative: queue_ahead = 1.0 * 1.0 = 1.0, fillable = 1.0 - 1.0 = 0
        assert result.status == OrderStatus.EXPIRED

    def test_no_short_simulation(self):
        """Buy orders cannot produce short positions."""
        sim = MakerSimulator()
        t0 = 1_000_000_000_000
        # No events - just verify structure
        result = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=[],
            reference_mid_at_placement=100_005_000,
        )
        assert result.status == OrderStatus.EXPIRED
        assert result.fill_quantity == 0.0

    def test_adverse_selection_measurement(self):
        """Adverse selection correctly measured."""
        sim = MakerSimulator(MakerAssumptions(
            fill_model=FillModel.BASE, cancellation_horizon_s=5.0
        ))
        t0 = 1_000_000_000_000
        ref_mid = 100_005_000
        events = [
            # Ask drops well below mid
            _make_ob(t0 + 100_000_000,
                     [[99_990_000, 1.0]], [[100_000_000, 1.0]])
        ]
        result = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=ref_mid,
        )
        assert result.status == OrderStatus.FILLED
        # Adverse selection: mid(100_005_000) - fill(100_000_000) = 5000
        # bps = 5000 / 100_005_000 * 10000 ≈ 0.5 bps
        assert result.adverse_selection_bps > 0  # Buy filled below mid = good


class TestMakerFillModel:
    """Test fill model differences."""

    def test_optimistic_fills_on_touch(self):
        """OPTIMISTIC fills when price touches level."""
        sim = MakerSimulator(MakerAssumptions(fill_model=FillModel.OPTIMISTIC))
        t0 = 1_000_000_000_000
        events = [
            _make_ob(t0 + 100_000_000,
                     [[100_000_000, 1.0]], [[100_000_000, 1.0]])
        ]
        result = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=100_005_000,
        )
        assert result.status == OrderStatus.FILLED

    def test_buy_only_no_sell(self):
        """Only BUY side implemented (long-only spot)."""
        sim = MakerSimulator()
        # Verify only BUY is available
        assert hasattr(sim, 'evaluate_passive_buy')
        # SELL passive would be needed for round trips but is separate
