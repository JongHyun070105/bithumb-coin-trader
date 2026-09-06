import dataclasses
import pytest
import math
from bithumb_coin_trader.canonical_market_data import CanonicalOrderBook
from bithumb_coin_trader.risk_engine import (
    RiskEngine,
    RiskEngineConfig,
    RiskVerdict,
)


@pytest.fixture
def clean_book():
    return CanonicalOrderBook(
        exchange="bithumb",
        market="KRW-BTC",
        exchange_timestamp_ms=1000,
        receive_timestamp_ms=1000,
        bids=((100_000_000.0, 1.0),),
        asks=((100_050_000.0, 1.0),),  # 5 bps spread
    )


def test_normal_order_allow(clean_book):
    engine = RiskEngine()
    verdict, reasons, audit = engine.evaluate_preflight(
        order_id="ord_1",
        side="BUY",
        requested_notional_krw=1_000_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=0.0,
        daily_loss_fraction=0.01,
        orderbook=clean_book,
        current_time_ms=1050,
    )
    assert verdict == RiskVerdict.ALLOW
    assert not reasons
    assert audit.verdict == RiskVerdict.ALLOW
    assert len(audit.context_hash) == 64


def test_stale_market_data_reject(clean_book):
    engine = RiskEngine(RiskEngineConfig(max_data_age_ms=1000.0))
    verdict, reasons, _ = engine.evaluate_preflight(
        order_id="ord_2",
        side="BUY",
        requested_notional_krw=1_000_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=0.0,
        daily_loss_fraction=0.01,
        orderbook=clean_book,
        current_time_ms=3000,  # 2000ms age > 1000ms limit
    )
    assert verdict == RiskVerdict.REJECT
    assert any("stale" in r for r in reasons)


def test_wide_spread_reject():
    wide_book = CanonicalOrderBook(
        exchange="bithumb",
        market="KRW-BTC",
        exchange_timestamp_ms=1000,
        receive_timestamp_ms=1000,
        bids=((100_000_000.0, 1.0),),
        asks=((101_000_000.0, 1.0),),  # 100 bps spread > 50 bps limit
    )
    engine = RiskEngine(RiskEngineConfig(max_spread_bps=50.0))
    verdict, reasons, _ = engine.evaluate_preflight(
        order_id="ord_3",
        side="BUY",
        requested_notional_krw=1_000_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=0.0,
        daily_loss_fraction=0.01,
        orderbook=wide_book,
        current_time_ms=1050,
    )
    assert verdict == RiskVerdict.REJECT
    assert any("Spread" in r for r in reasons)


def test_daily_drawdown_circuit_breaker_halt(clean_book):
    engine = RiskEngine(RiskEngineConfig(max_daily_loss_fraction=0.05))
    verdict, reasons, _ = engine.evaluate_preflight(
        order_id="ord_4",
        side="BUY",
        requested_notional_krw=1_000_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=0.0,
        daily_loss_fraction=0.06,  # 6% loss >= 5% limit
        orderbook=clean_book,
        current_time_ms=1050,
    )
    assert verdict == RiskVerdict.HALT
    assert engine.halted is True

    # Subsequent orders must immediately receive HALT
    verdict2, _, _ = engine.evaluate_preflight(
        order_id="ord_5",
        side="BUY",
        requested_notional_krw=100_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=0.0,
        daily_loss_fraction=0.0,
        orderbook=clean_book,
        current_time_ms=1050,
    )
    assert verdict2 == RiskVerdict.HALT


def test_consecutive_rejections_halt(clean_book):
    engine = RiskEngine(RiskEngineConfig(consecutive_rejection_limit=3))
    engine.record_execution_outcome(False)
    engine.record_execution_outcome(False)
    assert engine.halted is False
    engine.record_execution_outcome(False)
    assert engine.halted is True

    verdict, reasons, _ = engine.evaluate_preflight(
        order_id="ord_6",
        side="BUY",
        requested_notional_krw=1_000_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=0.0,
        daily_loss_fraction=0.0,
        orderbook=clean_book,
        current_time_ms=1050,
    )
    assert verdict == RiskVerdict.HALT


def test_kill_switch_activation(tmp_path, clean_book):
    ks_file = tmp_path / "KILL_SWITCH"
    engine = RiskEngine(RiskEngineConfig(kill_switch_file=ks_file))

    # In-memory kill switch
    engine.set_kill_switch(True)
    v1, _, _ = engine.evaluate_preflight("ord_k1", "BUY", 1000.0, 10000.0, 0.0, 0.0, clean_book, 1050)
    assert v1 == RiskVerdict.HALT
    engine.set_kill_switch(False)

    # File-based kill switch
    ks_file.write_text("HALT")
    v2, _, _ = engine.evaluate_preflight("ord_k2", "BUY", 1000.0, 10000.0, 0.0, 0.0, clean_book, 1050)
    assert v2 == RiskVerdict.HALT


def test_fail_closed_on_nan_or_inf(clean_book):
    engine = RiskEngine()
    v1, _, _ = engine.evaluate_preflight("ord_bad", "BUY", float("nan"), 10000.0, 0.0, 0.0, clean_book, 1050)
    assert v1 == RiskVerdict.HALT
    v2, _, _ = engine.evaluate_preflight("ord_bad2", "BUY", 1000.0, float("inf"), 0.0, 0.0, clean_book, 1050)
    assert v2 == RiskVerdict.HALT


# ─── Phase 2.5 Forensic Adversarial Tests ───────────────────────────────────

def test_sell_decreases_exposure(clean_book):
    """BUG-3 FIX: SELL orders must DECREASE exposure, not increase it."""
    # Starting position: 5,000,000 / 20,000,000 equity = 25% exposure
    # Selling 1,000,000 should bring position to 4,000,000 = 20% exposure
    engine = RiskEngine(RiskEngineConfig(max_portfolio_exposure_fraction=0.95))
    verdict, reasons, audit = engine.evaluate_preflight(
        order_id="sell_exp_test",
        side="SELL",
        requested_notional_krw=1_000_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=5_000_000.0,
        daily_loss_fraction=0.01,
        orderbook=clean_book,
        current_time_ms=1050,
    )
    assert verdict == RiskVerdict.ALLOW, f"SELL should ALLOW when reducing exposure; reasons: {reasons}"
    assert not any("exposure" in r.lower() for r in reasons), \
        f"SELL that reduces exposure must not trigger exposure rejection: {reasons}"


def test_sell_does_not_blow_exposure_limit(clean_book):
    """BUG-3 REGRESSION: Verify SELL with remaining position above limit still rejects correctly."""
    # Position = 19,000,000, equity = 20,000,000 (95%), sell 500k -> 18.5M / 20M = 92.5% -> ALLOW
    engine = RiskEngine(RiskEngineConfig(max_portfolio_exposure_fraction=0.90))
    # Before sell: position = 19M, exposure = 95% > 90% limit, BUT this is a sell, reducing exposure
    verdict, reasons, _ = engine.evaluate_preflight(
        order_id="sell_high_pos",
        side="SELL",
        requested_notional_krw=1_000_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=19_000_000.0,
        daily_loss_fraction=0.01,
        orderbook=clean_book,
        current_time_ms=1050,
    )
    # After sell: 18M / 20M = 90% = exactly at limit -> ALLOW (not > limit)
    assert verdict == RiskVerdict.ALLOW, f"SELL to 90% exposure at limit should ALLOW; reasons: {reasons}"


def test_buy_increases_exposure_correctly(clean_book):
    """BUG-3 REGRESSION: BUY still uses position + notional for exposure."""
    # Position=18M, buy 3M -> 21M / 20M = 105% > 95% limit -> REJECT
    engine = RiskEngine(RiskEngineConfig(max_portfolio_exposure_fraction=0.95))
    verdict, reasons, _ = engine.evaluate_preflight(
        order_id="buy_excess",
        side="BUY",
        requested_notional_krw=3_000_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=18_000_000.0,
        daily_loss_fraction=0.01,
        orderbook=clean_book,
        current_time_ms=1050,
    )
    assert verdict == RiskVerdict.REJECT
    assert any("exposure" in r.lower() for r in reasons)


def test_max_slippage_bps_is_enforced(clean_book):
    """BUG-2 FIX: max_slippage_bps must be checked and cause REJECT when exceeded.

    clean_book: bid=100_000_000, ask=100_050_000.
    mid = 100_025_000. Half-spread = 25_000.
    Estimated BUY slippage = (ask - mid) / mid * 10_000 ≈ 2.50 bps.
    Set max_slippage_bps=2.0 -> estimated slippage (2.5 bps) > limit -> REJECT.
    """
    engine = RiskEngine(RiskEngineConfig(max_slippage_bps=2.0, max_spread_bps=100.0))
    verdict, reasons, _ = engine.evaluate_preflight(
        order_id="slippage_test",
        side="BUY",
        requested_notional_krw=1_000_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=0.0,
        daily_loss_fraction=0.01,
        orderbook=clean_book,
        current_time_ms=1050,
    )
    assert verdict == RiskVerdict.REJECT, \
        f"Slippage exceeding max_slippage_bps must cause REJECT; got {verdict}, reasons: {reasons}"
    assert any("slippage" in r.lower() for r in reasons), \
        f"Rejection reason must mention slippage; got: {reasons}"


def test_max_slippage_bps_allows_within_limit(clean_book):
    """BUG-2 REGRESSION: Orders within slippage limit must still be ALLOW."""
    # clean_book half-spread = 25 bps, set limit to 50 -> should allow
    engine = RiskEngine(RiskEngineConfig(max_slippage_bps=50.0, max_spread_bps=100.0))
    verdict, reasons, _ = engine.evaluate_preflight(
        order_id="slippage_ok",
        side="BUY",
        requested_notional_krw=1_000_000.0,
        current_equity_krw=20_000_000.0,
        current_position_notional_krw=0.0,
        daily_loss_fraction=0.01,
        orderbook=clean_book,
        current_time_ms=1050,
    )
    assert verdict == RiskVerdict.ALLOW, f"Slippage within limit must ALLOW; reasons: {reasons}"


def test_invalid_side_causes_halt(clean_book):
    """BUG-ADD: Any side other than BUY/SELL must trigger HALT."""
    engine = RiskEngine()
    for bad_side in ["buy", "sell", "LONG", "SHORT", "", "   ", "MARKET", "LIMIT"]:
        verdict, reasons, _ = engine.evaluate_preflight(
            order_id=f"bad_side_{bad_side}",
            side=bad_side,
            requested_notional_krw=1_000_000.0,
            current_equity_krw=20_000_000.0,
            current_position_notional_krw=0.0,
            daily_loss_fraction=0.01,
            orderbook=clean_book,
            current_time_ms=1050,
        )
        assert verdict == RiskVerdict.HALT, \
            f"Invalid side '{bad_side}' must HALT; got {verdict}, reasons: {reasons}"


def test_zero_notional_causes_halt(clean_book):
    """BUG-ADD: Zero or negative notional must trigger HALT."""
    engine = RiskEngine()
    for bad_notional in [0.0, -1.0, -1_000_000.0]:
        verdict, reasons, _ = engine.evaluate_preflight(
            order_id=f"bad_notional_{bad_notional}",
            side="BUY",
            requested_notional_krw=bad_notional,
            current_equity_krw=20_000_000.0,
            current_position_notional_krw=0.0,
            daily_loss_fraction=0.01,
            orderbook=clean_book,
            current_time_ms=1050,
        )
        assert verdict == RiskVerdict.HALT, \
            f"Notional {bad_notional} must HALT; got {verdict}, reasons: {reasons}"


def test_zero_or_negative_equity_causes_halt(clean_book):
    """BUG-ADD: Zero or negative equity must trigger HALT."""
    engine = RiskEngine()
    for bad_equity in [0.0, -1.0]:
        verdict, reasons, _ = engine.evaluate_preflight(
            order_id=f"bad_equity_{bad_equity}",
            side="BUY",
            requested_notional_krw=1_000_000.0,
            current_equity_krw=bad_equity,
            current_position_notional_krw=0.0,
            daily_loss_fraction=0.01,
            orderbook=clean_book,
            current_time_ms=1050,
        )
        assert verdict == RiskVerdict.HALT, \
            f"Equity {bad_equity} must HALT; got {verdict}, reasons: {reasons}"


def test_invalid_config_raises_at_construction():
    """BUG-ADD: Invalid config must raise at RiskEngineConfig construction."""
    with pytest.raises(ValueError, match="max_order_notional_krw"):
        RiskEngineConfig(max_order_notional_krw=0.0)
    with pytest.raises(ValueError, match="max_portfolio_exposure_fraction"):
        RiskEngineConfig(max_portfolio_exposure_fraction=0.0)
    with pytest.raises(ValueError, match="max_slippage_bps"):
        RiskEngineConfig(max_slippage_bps=-1.0)
    with pytest.raises(ValueError, match="max_daily_loss_fraction"):
        RiskEngineConfig(max_daily_loss_fraction=0.0)


def test_audit_context_hash_includes_side_and_params(clean_book):
    """BUG-ADD: Different parameters must produce different context_hash values."""
    engine = RiskEngine()
    _, _, audit_buy = engine.evaluate_preflight(
        "same_id", "BUY", 1_000_000.0, 20_000_000.0, 0.0, 0.01, clean_book, 1050
    )
    _, _, audit_sell = engine.evaluate_preflight(
        "same_id", "SELL", 1_000_000.0, 20_000_000.0, 5_000_000.0, 0.01, clean_book, 1050
    )
    assert audit_buy.context_hash != audit_sell.context_hash, \
        "BUY and SELL orders with same order_id must have different context hashes"
import pytest
from bithumb_coin_trader.risk_engine import RiskEngine, RiskEngineConfig, RiskVerdict, simulate_taker_execution

def test_oversell_rejected(clean_book):
    engine = RiskEngine()
    verdict, reasons, _ = engine.evaluate_preflight(
        "sell_1", "SELL", 1_000_000.0, 20_000_000.0, 500_000.0, 0.01, clean_book, 1050
    )
    assert verdict == RiskVerdict.REJECT
    assert any("INSUFFICIENT_POSITION" in r for r in reasons)

def test_valid_sell_allowed(clean_book):
    engine = RiskEngine()
    verdict, reasons, _ = engine.evaluate_preflight(
        "sell_2", "SELL", 1_000_000.0, 20_000_000.0, 2_000_000.0, 0.01, clean_book, 1050
    )
    assert verdict == RiskVerdict.ALLOW

def test_negative_position_halts(clean_book):
    engine = RiskEngine()
    verdict, reasons, _ = engine.evaluate_preflight(
        "neg_pos", "SELL", 100_000.0, 20_000_000.0, -100_000.0, 0.01, clean_book, 1050
    )
    assert verdict == RiskVerdict.HALT

def test_future_dated_book_rejected(clean_book):
    engine = RiskEngine()
    # current_time_ms = 500, book = 1000
    verdict, reasons, _ = engine.evaluate_preflight(
        "fut_book", "BUY", 100_000.0, 20_000_000.0, 0.0, 0.01, clean_book, 500
    )
    assert verdict == RiskVerdict.HALT
    assert any("CLOCK_INVERSION" in r for r in reasons)

def test_negative_daily_loss_fraction_halts(clean_book):
    engine = RiskEngine()
    verdict, reasons, _ = engine.evaluate_preflight(
        "neg_dlf", "BUY", 100_000.0, 20_000_000.0, 0.0, -0.01, clean_book, 1050
    )
    assert verdict == RiskVerdict.HALT

def test_absurd_daily_loss_fraction_halts(clean_book):
    engine = RiskEngine()
    verdict, reasons, _ = engine.evaluate_preflight(
        "abs_dlf", "BUY", 100_000.0, 20_000_000.0, 0.0, 1.5, clean_book, 1050
    )
    assert verdict == RiskVerdict.HALT

def test_audit_sink_persists_decisions(tmp_path, clean_book):
    sink_path = tmp_path / "audit.jsonl"
    engine = RiskEngine(audit_sink_path=sink_path)
    engine.evaluate_preflight("t1", "BUY", 100_000.0, 20_000_000.0, 0.0, 0.01, clean_book, 1050)
    engine.evaluate_preflight("t2", "BUY", 100_000.0, 20_000_000.0, 0.0, 0.01, clean_book, 1050)
    lines = sink_path.read_text().strip().split('\n')
    assert len(lines) == 2

def test_deep_book_allows_order(clean_book):
    # Make a deep book
    clean_book = dataclasses.replace(clean_book, asks=((100_050_000.0, 10.0),)) # 1B KRW depth
    engine = RiskEngine(RiskEngineConfig(max_total_execution_cost_bps=80.0, taker_fee_bps=40.0))
    verdict, reasons, _ = engine.evaluate_preflight(
        "dp", "BUY", 1_000_000.0, 20_000_000.0, 0.0, 0.01, clean_book, 1050
    )
    assert verdict == RiskVerdict.ALLOW

def test_thin_book_triggers_slippage_reject(clean_book):
    # Make a very thin book
    clean_book = dataclasses.replace(clean_book, asks=((100_050_000.0, 0.001), (101_000_000.0, 1.0))) # 100k at best, then worse
    engine = RiskEngine(RiskEngineConfig(max_total_execution_cost_bps=80.0, taker_fee_bps=40.0))
    verdict, reasons, _ = engine.evaluate_preflight(
        "thin", "BUY", 1_000_000.0, 20_000_000.0, 0.0, 0.01, clean_book, 1050
    )
    assert verdict == RiskVerdict.REJECT

def test_spread_and_depth_not_double_counted():
    # Write a test to simulate_taker_execution
    levels = ((100, 1.0), (102, 1.0))
    mid = 98.0
    # mid=98, best_ask=100. spread_crossing = (100-98)/98 = ~204 bps
    # order 150 -> fills 100 at 100, 50 at 102.
    # size: 1.0 at 100, 50/102 at 102.
    # vwap = 150 / (1.0 + 50/102)
    # total cost, depth slippage, etc.
    res = simulate_taker_execution("BUY", 150, levels, mid, 40.0)
    assert res.spread_crossing_bps > 0
    assert res.depth_slippage_bps > 0
    assert res.total_execution_cost_bps == res.spread_crossing_bps + res.depth_slippage_bps + res.fee_bps
