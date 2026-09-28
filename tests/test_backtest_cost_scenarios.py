from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bithumb_coin_trader.backtest import Backtester
from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.research_infra.cost_sensitivity import run_cost_sensitivity
from bithumb_coin_trader.research_infra.cost_sensitivity import run_target_weight_cost_sensitivity
from bithumb_coin_trader.research_infra.costs import (
    CostScenarioError,
    SpotCostScenario,
    spot_fill_terms,
)


def _scenario(name: str = "base", **overrides: object) -> SpotCostScenario:
    values: dict[str, object] = {
        "name": name,
        "maker_fee_bps": 2.0,
        "taker_fee_bps": 10.0,
        "slippage_bps": 10.0,
        "latency_ms": 0.0,
        "minimum_order_notional": 5_000.0,
        "tick_size": 1.0,
        "lot_size": 1.0,
        "partial_fill_probability": 0.0,
        "partial_fill_status": None,
    }
    values.update(overrides)
    return SpotCostScenario(**values)  # type: ignore[arg-type]


def _candles(prices: tuple[float, ...]) -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Candle(start + timedelta(days=index), price, price, price, price, 1000)
        for index, price in enumerate(prices)
    ]


def test_fill_terms_round_price_adversely_and_quantity_down_to_lot() -> None:
    scenario = _scenario()

    buy = spot_fill_terms(
        scenario, reference_price=100.0, requested_quantity=99.9, side="BUY"
    )
    sell = spot_fill_terms(
        scenario, reference_price=110.0, requested_quantity=99.0, side="SELL"
    )

    assert buy.fill_price == 101.0
    assert buy.quantity == 99.0
    assert buy.notional == 9_999.0
    assert buy.fee == pytest.approx(9.999)
    assert buy.slippage_cost == 99.0
    assert sell.fill_price == 109.0
    assert sell.quantity == 99.0
    assert sell.slippage_cost == 99.0


def test_authoritative_backtester_applies_scenario_and_exposes_unsupported_semantics() -> None:
    settings = TradingSettings(
        initial_capital_krw=20_000,
        fee_rate=0.0,
        slippage_bps=0.0,
        allocation_fraction=1.0,
        minimum_order_krw=1,
        maximum_order_krw=20_000,
        maximum_daily_entries=2,
        cash_reserve_krw=0,
    )
    result = Backtester(settings).run(
        _candles((100.0, 100.0, 110.0)),
        (Signal.LONG, Signal.FLAT, Signal.FLAT),
        cost_scenario=_scenario(latency_ms=100.0, partial_fill_probability=None, partial_fill_status="UNSUPPORTED"),
    )

    assert result.execution_assumptions is not None
    assert result.execution_assumptions["taker_fee_bps"] == 10.0
    assert result.trades[0].entry_price == 101.0
    assert result.trades[0].exit_price == 109.0
    assert result.total_fees > 0
    assert result.total_slippage_cost > 0
    assert "LATENCY_NOT_MODELED_AT_CANDLE_RESOLUTION" in result.unsupported_execution_semantics
    assert "PARTIAL_FILLS_EXPLICITLY_UNSUPPORTED_BY_SCENARIO" in result.unsupported_execution_semantics


def test_cost_sensitivity_is_machine_readable_and_bounded() -> None:
    report = run_cost_sensitivity(
        _candles((100.0, 100.0, 110.0)),
        (Signal.LONG, Signal.FLAT, Signal.FLAT),
        (
            _scenario("base"),
            _scenario("stress", taker_fee_bps=40.0, slippage_bps=50.0),
        ),
        settings=TradingSettings(
            initial_capital_krw=20_000,
            fee_rate=0.0,
            slippage_bps=0.0,
            allocation_fraction=1.0,
            minimum_order_krw=1,
            maximum_order_krw=20_000,
            maximum_daily_entries=2,
            cash_reserve_krw=0,
        ),
    )

    encoded = report.to_dict()
    assert encoded["schema_version"] == 1
    assert [row["scenario"]["name"] for row in encoded["rows"]] == ["base", "stress"]
    assert encoded["rows"][1]["net_return"] < encoded["rows"][0]["net_return"]
    assert all("fees" in row and "slippage_cost" in row for row in encoded["rows"])
    assert all(row["execution_complete"] is True for row in encoded["rows"])

    with pytest.raises(ValueError, match="unique"):
        run_cost_sensitivity(
            _candles((100.0, 100.0)),
            (Signal.FLAT, Signal.FLAT),
            (_scenario("same"), _scenario("same")),
        )


def test_rounded_entry_below_minimum_is_rejected_without_a_fill() -> None:
    settings = TradingSettings(
        initial_capital_krw=20_000,
        fee_rate=0.0,
        slippage_bps=0.0,
        allocation_fraction=0.30,
        minimum_order_krw=1,
        maximum_order_krw=20_000,
        maximum_daily_entries=2,
        cash_reserve_krw=0,
    )
    result = Backtester(settings).run(
        _candles((100.0, 100.0)),
        (Signal.LONG, Signal.FLAT),
        cost_scenario=_scenario(minimum_order_notional=6_000.0),
    )
    assert not result.trades
    assert result.entry_rejections
    assert "minimum order notional" in result.entry_rejections[0].reasons[0]


def test_authoritative_backtester_rejects_shorts_with_spot_scenario() -> None:
    with pytest.raises(CostScenarioError, match="long-only"):
        Backtester(allow_short=True).run(
            _candles((100.0, 100.0)),
            (Signal.FLAT, Signal.FLAT),
            cost_scenario=_scenario(),
        )


def test_authoritative_target_weight_path_uses_scenario_and_cost_grid() -> None:
    settings = TradingSettings(
        initial_capital_krw=20_000,
        fee_rate=0.0,
        slippage_bps=0.0,
        allocation_fraction=1.0,
        minimum_order_krw=1,
        maximum_order_krw=20_000,
        maximum_daily_entries=2,
        cash_reserve_krw=0,
    )
    scenarios = (
        _scenario("base"),
        _scenario("stress", taker_fee_bps=40.0, slippage_bps=50.0),
    )
    report = run_target_weight_cost_sensitivity(
        _candles((100.0, 100.0, 110.0)),
        (0.5, 0.0, 0.0),
        scenarios,
        settings=settings,
    )

    rows = report.to_dict()["rows"]
    assert rows[0]["engine_id"] == "rebalance_backtester.v1"
    assert rows[0]["trade_count"] == 1
    assert rows[0]["fees"] > 0
    assert rows[0]["slippage_cost"] > 0
    assert rows[1]["net_return"] < rows[0]["net_return"]
    assert rows[0]["execution_complete"] is True
