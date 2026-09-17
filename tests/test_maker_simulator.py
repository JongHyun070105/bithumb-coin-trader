"""Deterministic Golden Tests for Conservative Maker Execution Simulator.

Verifies the 11 required Golden Tests:
1. NO_FILL
2. TOUCH_NO_FILL
3. QUEUE_NOT_CLEARED
4. PARTIAL_FILL
5. FULL_FILL
6. CANCELLED
7. STALE
8. ADVERSE_SELECTION
9. PASSIVE_ENTRY_TAKER_EXIT
10. PASSIVE_ENTRY_PASSIVE_EXIT
11. FORCED_EXIT
"""

import pytest
from bithumb_coin_trader.maker_simulator import (
    MakerSimulator,
    MakerAssumptions,
    FillModel,
    OrderStatus,
    MakerFill,
)
from bithumb_coin_trader.research_infra.canonical_events import (
    CanonicalEvent,
    EventKind,
    TimestampRole,
)


def _make_ob(ts_ns: int, bids: list, asks: list, market: str = "KRW-BTC") -> CanonicalEvent:
    return CanonicalEvent(
        dataset_id="test",
        source_run_id=None,
        collector_epoch=None,
        source_file=None,
        source_file_offset=None,
        exchange="bithumb",
        market=market,
        event_kind=EventKind.ORDERBOOK,
        exchange_timestamp_ms=ts_ns // 1_000_000,
        local_recv_timestamp_ms=ts_ns // 1_000_000,
        local_write_timestamp_ms=ts_ns // 1_000_000,
        ordering_timestamp_ns=ts_ns,
        availability_timestamp_ns=ts_ns,
        exchange_timestamp_role=TimestampRole.EXCHANGE_EVENT,
        payload={"bids": bids, "asks": asks, "is_snapshot": True},
    )


def _make_trade(ts_ns: int, price: float, qty: float, side: str, market: str = "KRW-BTC") -> CanonicalEvent:
    return CanonicalEvent(
        dataset_id="test",
        source_run_id=None,
        collector_epoch=None,
        source_file=None,
        source_file_offset=None,
        exchange="bithumb",
        market=market,
        event_kind=EventKind.TRADE,
        exchange_timestamp_ms=ts_ns // 1_000_000,
        local_recv_timestamp_ms=ts_ns // 1_000_000,
        local_write_timestamp_ms=ts_ns // 1_000_000,
        ordering_timestamp_ns=ts_ns,
        availability_timestamp_ns=ts_ns,
        exchange_timestamp_role=TimestampRole.EXCHANGE_EVENT,
        payload={"price": price, "quantity": qty, "aggressor_side": side},
    )


class TestMakerGoldenSuite:
    """11 Deterministic Golden Tests for Maker Execution."""

    def test_golden_01_no_fill(self):
        """1. NO_FILL: Prices never reach limit order before events exhaust."""
        sim = MakerSimulator(MakerAssumptions(latency_ms=50.0, cancellation_horizon_s=5.0))
        t0 = 1_000_000_000_000
        # Limit buy at 100_000_000. Book stays above at 100_010_000 / 100_020_000.
        events = [
            _make_ob(t0 + 100_000_000, [[100_010_000, 1.0]], [[100_020_000, 1.0]]),
            _make_trade(t0 + 200_000_000, 100_015_000, 0.5, "BUY"),
        ]
        fill = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=100_015_000,
        )
        assert fill.status == OrderStatus.EXPIRED
        assert fill.fill_quantity == 0.0

    def test_golden_02_touch_no_fill(self):
        """2. TOUCH_NO_FILL: In CONSERVATIVE model, orderbook touch alone without trades does NOT fill."""
        sim = MakerSimulator(MakerAssumptions(
            fill_model=FillModel.CONSERVATIVE,
            latency_ms=50.0,
            cancellation_horizon_s=5.0,
        ))
        t0 = 1_000_000_000_000
        # Ask touches limit at 100_000_000, but no trade-through and no trades occur
        events = [
            _make_ob(t0 + 100_000_000, [[99_990_000, 1.0]], [[100_000_000, 2.0]])
        ]
        fill = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=100_005_000,
        )
        assert fill.status == OrderStatus.EXPIRED
        assert fill.fill_quantity == 0.0

    def test_golden_03_queue_not_cleared(self):
        """3. QUEUE_NOT_CLEARED: Trade volume is <= queue ahead, so our order gets no fill."""
        sim = MakerSimulator(MakerAssumptions(
            fill_model=FillModel.CONSERVATIVE,
            latency_ms=50.0,
            queue_multiplier=1.0,
            cancellation_horizon_s=5.0,
        ))
        t0 = 1_000_000_000_000
        # Initial book at placement has 2.0 BTC ahead at 100_000_000
        initial_book = _make_ob(t0, [[100_000_000, 2.0]], [[100_010_000, 1.0]])
        # Incoming SELL trade is only 1.5 BTC (does not clear 2.0 queue)
        events = [
            _make_trade(t0 + 100_000_000, 100_000_000, 1.5, "SELL")
        ]
        fill = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=100_005_000,
            initial_book=initial_book,
        )
        assert fill.status == OrderStatus.EXPIRED
        assert fill.fill_quantity == 0.0

    def test_golden_04_partial_fill(self):
        """4. PARTIAL_FILL: Trade volume clears queue and fills part of our order."""
        sim = MakerSimulator(MakerAssumptions(
            fill_model=FillModel.CONSERVATIVE,
            latency_ms=50.0,
            queue_multiplier=1.0,
            cancellation_horizon_s=5.0,
        ))
        t0 = 1_000_000_000_000
        # 1.0 BTC ahead in queue. Order is for 2.0 BTC. Trade is 2.2 BTC.
        # Queue takes 1.0, remaining 1.2 fills our order (partial: 1.2 < 2.0).
        initial_book = _make_ob(t0, [[100_000_000, 1.0]], [[100_010_000, 1.0]])
        events = [
            _make_trade(t0 + 100_000_000, 100_000_000, 2.2, "SELL")
        ]
        fill = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=2.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=100_005_000,
            initial_book=initial_book,
        )
        assert fill.status == OrderStatus.PARTIAL
        assert abs(fill.fill_quantity - 1.2) < 1e-8
        assert fill.fill_price == 100_000_000

    def test_golden_05_full_fill(self):
        """5. FULL_FILL: Trade volume clears queue and satisfies entire order."""
        sim = MakerSimulator(MakerAssumptions(
            fill_model=FillModel.CONSERVATIVE,
            latency_ms=50.0,
            queue_multiplier=1.0,
            cancellation_horizon_s=5.0,
        ))
        t0 = 1_000_000_000_000
        # 1.0 BTC ahead in queue. Order is for 1.0 BTC. Trade is 2.5 BTC.
        initial_book = _make_ob(t0, [[100_000_000, 1.0]], [[100_010_000, 1.0]])
        events = [
            _make_trade(t0 + 100_000_000, 100_000_000, 2.5, "SELL")
        ]
        fill = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=100_005_000,
            initial_book=initial_book,
        )
        assert fill.status == OrderStatus.FILLED
        assert fill.fill_quantity == 1.0
        assert fill.fill_price == 100_000_000
        assert fill.reason == "FULL_FILL"

    def test_golden_06_cancelled(self):
        """6. CANCELLED: Cancellation horizon elapses without fill."""
        sim = MakerSimulator(MakerAssumptions(
            fill_model=FillModel.BASE,
            latency_ms=50.0,
            cancellation_horizon_s=1.0,
        ))
        t0 = 1_000_000_000_000
        # Event happens at t0 + 2.0s (after 1.0s cancel boundary)
        events = [
            _make_ob(t0 + 2_000_000_000, [[100_000_000, 1.0]], [[100_000_000, 1.0]])
        ]
        fill = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=100_005_000,
        )
        assert fill.status == OrderStatus.CANCELLED
        assert fill.fill_quantity == 0.0

    def test_golden_07_stale(self):
        """7. STALE: Event stream has an initial gap exceeding max_staleness_ms."""
        sim = MakerSimulator(MakerAssumptions(
            max_staleness_ms=1000.0,  # 1s max staleness
        ))
        t0 = 1_000_000_000_000
        # First event is 5.0s after placement
        events = [
            _make_ob(t0 + 5_000_000_000, [[100_000_000, 1.0]], [[100_010_000, 1.0]])
        ]
        fill = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=100_005_000,
        )
        assert fill.status == OrderStatus.STALE

    def test_golden_08_adverse_selection(self):
        """8. ADVERSE_SELECTION: Correct calculation of adverse selection against reference mid."""
        sim = MakerSimulator(MakerAssumptions(fill_model=FillModel.BASE))
        t0 = 1_000_000_000_000
        ref_mid = 100_000_000.0
        # Buy limit is 99_900_000. Filled at 99_900_000.
        events = [
            _make_trade(t0 + 100_000_000, 99_900_000, 1.0, "SELL")
        ]
        fill = sim.evaluate_passive_buy(
            limit_price=99_900_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=ref_mid,
        )
        assert fill.status == OrderStatus.FILLED
        # Favorable execution: bought 100_000 below mid -> +10 bps
        expected_bps = (ref_mid - 99_900_000) / ref_mid * 10_000.0
        assert abs(fill.adverse_selection_bps - expected_bps) < 1e-4

    def test_golden_09_passive_entry_taker_exit(self):
        """9. PASSIVE_ENTRY_TAKER_EXIT (Variant A): Passive entry filled, then taker exit at holding horizon."""
        sim = MakerSimulator(MakerAssumptions(
            fill_model=FillModel.BASE,
            holding_horizon_s=2.0,
            maker_fee_rate=0.0,
            taker_fee_rate=0.0004,  # 4 bps taker exit
        ))
        t0 = 1_000_000_000_000
        events = [
            # Entry fill at t0 + 100ms
            _make_trade(t0 + 100_000_000, 100_000_000, 1.0, "SELL"),
            # Holding period book at t0 + 1.0s
            _make_ob(t0 + 1_000_000_000, [[100_020_000, 1.0]], [[100_030_000, 1.0]]),
            # Horizon exit book at t0 + 2.1s (>= entry + 2.0s)
            _make_ob(t0 + 2_150_000_000, [[100_050_000, 1.0]], [[100_060_000, 1.0]]),
        ]
        entry = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=100_000_000,
        )
        assert entry.status == OrderStatus.FILLED

        trip = sim.simulate_variant_a(entry, events)
        assert trip.variant == "A"
        assert trip.exit_type == "TAKER"
        assert trip.entry_price == 100_000_000
        assert trip.exit_price == 100_050_000  # Sold into best bid at exit
        # Gross gain: 50_000 KRW = +5 bps
        assert abs(trip.gross_bps - 5.0) < 1e-4
        # Net gain: gross (50,000 KRW) - taker exit fee (100,050,000 * 0.0004 = 40,020 KRW) = 9,980 KRW (0.998 bps)
        assert abs(trip.net_bps - 0.998) < 1e-4
        assert trip.success is True

    def test_golden_10_passive_entry_passive_exit(self):
        """10. PASSIVE_ENTRY_PASSIVE_EXIT (Variant B): Passive entry filled, then passive exit filled at ask."""
        sim = MakerSimulator(MakerAssumptions(
            fill_model=FillModel.BASE,
            maker_fee_rate=0.0,  # Zero maker fee on both legs!
            taker_fee_rate=0.0004,
        ))
        t0 = 1_000_000_000_000
        events = [
            # Entry fill at t0 + 100ms
            _make_trade(t0 + 100_000_000, 100_000_000, 1.0, "SELL"),
            # Book establishing best ask at 100_040_000
            _make_ob(t0 + 150_000_000, [[100_000_000, 1.0]], [[100_040_000, 1.0]]),
            # Incoming aggressive BUY trade at 100_040_000 filling our passive exit ask
            _make_trade(t0 + 300_000_000, 100_040_000, 1.0, "BUY"),
        ]
        entry = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=100_000_000,
        )
        assert entry.status == OrderStatus.FILLED

        trip = sim.simulate_variant_b(entry, events)
        assert trip.variant == "B"
        assert trip.exit_type == "PASSIVE"
        assert trip.entry_price == 100_000_000
        assert trip.exit_price == 100_040_000
        # Captures full 4 bps spread with 0 fees!
        assert abs(trip.gross_bps - 4.0) < 1e-4
        assert abs(trip.net_bps - 4.0) < 1e-4
        assert trip.success is True

    def test_golden_11_forced_exit(self):
        """11. FORCED_EXIT: Passive exit unfulfilled falls back to forced taker unwind."""
        sim = MakerSimulator(MakerAssumptions(
            fill_model=FillModel.BASE,
            cancellation_horizon_s=0.5,
            holding_horizon_s=1.0,
            maker_fee_rate=0.0,
            taker_fee_rate=0.0004,
        ))
        t0 = 1_000_000_000_000
        events = [
            # Entry fill
            _make_trade(t0 + 100_000_000, 100_000_000, 1.0, "SELL"),
            # Passive exit posted at 100_040_000, but no trades hit it
            _make_ob(t0 + 150_000_000, [[100_000_000, 1.0]], [[100_040_000, 1.0]]),
            # Market drops to 99_980_000 after exit cancel horizon (forced taker exit)
            _make_ob(t0 + 1_200_000_000, [[99_980_000, 1.0]], [[99_990_000, 1.0]]),
        ]
        entry = sim.evaluate_passive_buy(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=100_000_000,
        )
        trip = sim.simulate_variant_b(entry, events)
        assert trip.exit_type == "FORCED_UNWIND"
        assert trip.exit_price == 99_980_000
        assert trip.gross_bps < 0  # Loss due to adverse price move before forced unwind


class TestMakerSimulatorSellSymmetry:
    """Verify BUY and SELL symmetry."""

    def test_passive_sell_full_fill(self):
        sim = MakerSimulator(MakerAssumptions(fill_model=FillModel.BASE))
        t0 = 1_000_000_000_000
        events = [
            _make_trade(t0 + 100_000_000, 100_000_000, 1.0, "BUY")
        ]
        fill = sim.evaluate_passive_sell(
            limit_price=100_000_000,
            quantity_btc=1.0,
            placement_ts=t0,
            future_events=events,
            reference_mid_at_placement=99_990_000,
        )
        assert fill.status == OrderStatus.FILLED
        assert fill.order.side == "SELL"
        assert fill.fill_quantity == 1.0
        assert fill.fill_price == 100_000_000


class TestMakerResearchArtifactIntegrity:
    """Verify Maker research artifacts consistency and accounting integrity."""

    def test_maker_total_trials_aggregation_equals_70(self):
        """Regression test for the 23 vs 70 aggregation bug."""
        import json
        from pathlib import Path

        report_path = Path("research-artifacts/maker/reports/MAKER_RESULTS.json")
        assert report_path.exists(), "MAKER_RESULTS.json must exist"

        with open(report_path) as f:
            data = json.load(f)

        assert data["total_trials"] == 70, f"Expected 70 total trials, got {data['total_trials']}"
        assert data["findings"]["cycle1_baseline"]["trials"] == 54
        assert data["findings"]["cycle2_refinement"]["trials"] == 4
        assert data["findings"]["cycle3_discriminating"]["trials"] == 12

        # Verify sum across cycles (cycle1 is dict with 'results', cycle2 and cycle3 are lists of scenarios)
        cycle1_count = len(data["cycles"]["cycle1"]["results"])
        cycle2_count = len(data["cycles"]["cycle2"]) if isinstance(data["cycles"]["cycle2"], list) else len(data["cycles"]["cycle2"]["results"])
        cycle3_count = len(data["cycles"]["cycle3"]) if isinstance(data["cycles"]["cycle3"], list) else len(data["cycles"]["cycle3"]["results"])
        assert cycle1_count == 54
        assert cycle2_count == 4
        assert cycle3_count == 12
        assert cycle1_count + cycle2_count + cycle3_count == 70

    def test_maker_best_xrp_candidate_metrics(self):
        """Verify best candidate trial ID and exact metrics."""
        import json
        from pathlib import Path

        report_path = Path("research-artifacts/maker/reports/MAKER_RESULTS.json")
        with open(report_path) as f:
            data = json.load(f)

        best = data["best_candidate"]
        assert best["trial_id"] == "MAKER-C3-XRP-Q0.5-C20S-CONS"
        assert best["fills"] == 54
        assert abs(best["fill_rate"] - 0.010553) < 1e-4
        assert abs(best["net_bps"] - 2.19785) < 1e-3
        assert best["queue_multiplier"] == 0.5

    def test_maker_partial_fill_accounting_no_dropped_position(self):
        """Verify partial passive exit unwinds remainder with taker exit without dropping quantity."""
        sim = MakerSimulator(MakerAssumptions(
            fill_model=FillModel.CONSERVATIVE,
            latency_ms=10.0,
            cancellation_horizon_s=0.1,  # 100ms cancel horizon
            holding_horizon_s=0.5,
            maker_fee_rate=0.0,
            taker_fee_rate=0.0004,
        ))
        t0 = 1_000_000_000_000

        # Entry fill: 1.0 BTC at 100_000_000
        entry = MakerFill(
            order=sim.create_order("BUY", 100_000_000, 1.0, t0),
            status=OrderStatus.FILLED,
            fill_price=100_000_000.0,
            fill_quantity=1.0,
            fill_timestamp_ns=t0 + 50_000_000,
        )

        # Future events:
        # 1. Orderbook at t0 + 60ms setting ask at 100_020_000 with depth 0.4
        # 2. Trade at t0 + 80ms (after effective ts t0+60ms) buying 0.4 at 100_020_000 -> partial fill
        # 3. Orderbook at t0 + 200ms (after cancel ts t0+160ms) with bid at 100_010_000 for remainder market unwind
        events = [
            _make_ob(t0 + 60_000_000, [[100_000_000, 1.0]], [[100_020_000, 0.4]]),
            _make_trade(t0 + 80_000_000, 100_020_000, 0.4, "BUY"),
            _make_ob(t0 + 200_000_000, [[100_010_000, 2.0]], [[100_030_000, 2.0]]),
        ]

        trip = sim.simulate_variant_b(entry, events)
        # Quantity must be full 1.0 (not truncated to 0.4)
        assert trip.quantity == 1.0
        assert trip.exit_type == "PARTIAL_PASSIVE_WITH_TAKER_UNWIND"
        assert trip.entry_price == 100_000_000.0
        # Blended exit price: (0.4 * 100_020_000 + 0.6 * 100_010_000) / 1.0 = 100_014_000.0
        assert abs(trip.exit_price - 100_014_000.0) < 1e-3
        assert trip.gross_bps > 0

