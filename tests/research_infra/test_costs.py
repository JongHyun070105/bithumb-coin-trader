from __future__ import annotations

import pytest

from bithumb_coin_trader.research_infra.costs import (
    CostScenarioError,
    SpotCostScenario,
    conservative_sensitivity_grid,
)


def _scenario(**overrides: object) -> SpotCostScenario:
    values: dict[str, object] = {
        "name": "zero-fee-base",
        "maker_fee_bps": 0.0,
        "taker_fee_bps": 0.0,
        "slippage_bps": 5.0,
        "latency_ms": 100.0,
        "minimum_order_notional": 5000.0,
        "tick_size": 1.0,
        "lot_size": 0.00000001,
        "partial_fill_probability": None,
        "partial_fill_status": "UNSUPPORTED",
    }
    values.update(overrides)
    return SpotCostScenario(**values)  # type: ignore[arg-type]


def test_cost_scenario_round_trip_separates_maker_taker_fees_and_slippage() -> None:
    scenario = _scenario(maker_fee_bps=1.0, taker_fee_bps=5.0, slippage_bps=10.0)

    result = scenario.estimate_round_trip_cost(100_000.0, entry_order_type="MAKER", exit_order_type="TAKER")

    assert scenario.fee_rate("MAKER") == pytest.approx(0.0001)
    assert scenario.execution_price(100.0, "BUY") == pytest.approx(100.1)
    assert scenario.execution_price(100.0, "SELL") == pytest.approx(99.9)
    assert result["entry_fee_krw"] == pytest.approx(10.0)
    assert result["exit_fee_krw"] == pytest.approx(50.0)
    assert result["slippage_cost_krw"] == pytest.approx(200.0)
    assert result["total_cost_krw"] == pytest.approx(260.0)


def test_cost_scenario_requires_explicit_partial_fill_semantics() -> None:
    with pytest.raises(CostScenarioError, match="partial fills require"):
        _scenario(partial_fill_status=None)
    assert _scenario(partial_fill_probability=0.25, partial_fill_status=None).partial_fill_probability == 0.25


def test_cost_scenario_rejects_invalid_units_and_ranges() -> None:
    with pytest.raises(CostScenarioError, match="tick_size"):
        _scenario(tick_size=0.0)
    with pytest.raises(CostScenarioError, match="partial_fill_probability"):
        _scenario(partial_fill_probability=1.1, partial_fill_status=None)
    with pytest.raises(CostScenarioError, match="minimum_order_notional"):
        _scenario().estimate_round_trip_cost(1000.0)


def test_sensitivity_grid_increases_configured_costs_and_preserves_fill_state() -> None:
    baseline = _scenario()

    grid = conservative_sensitivity_grid(
        baseline,
        fee_additions_bps=(0.0, 10.0),
        slippage_additions_bps=(0.0, 5.0),
        latency_values_ms=(100.0, 250.0),
    )

    assert len(grid) == 8
    assert grid[0].taker_fee_bps == baseline.taker_fee_bps
    assert grid[-1].taker_fee_bps == 10.0
    assert grid[-1].maker_fee_bps == 10.0
    assert grid[-1].slippage_bps == 10.0
    assert grid[-1].latency_ms == 250.0
    assert grid[-1].partial_fill_status == "UNSUPPORTED"


def test_sensitivity_grid_rejects_more_optimistic_inputs() -> None:
    with pytest.raises(CostScenarioError, match="non-negative additions"):
        conservative_sensitivity_grid(
            _scenario(),
            fee_additions_bps=(-1.0,),
            slippage_additions_bps=(0.0,),
            latency_values_ms=(100.0,),
        )
    with pytest.raises(CostScenarioError, match="at least the baseline latency"):
        conservative_sensitivity_grid(
            _scenario(),
            fee_additions_bps=(0.0,),
            slippage_additions_bps=(0.0,),
            latency_values_ms=(50.0,),
        )
