"""Tests for fee economics, CUSUM structural breaks, and Oaxaca-Blinder composition analysis."""

from __future__ import annotations

from decimal import Decimal

from bithumb_coin_trader.research_infra.behavioral_and_fee_analysis import (
    ChangePointAnalyzer,
    CompositionAnalyzer,
    FeeEconomicsAnalyzer,
)


def test_fee_counterfactual_scenarios() -> None:
    scenarios = FeeEconomicsAnalyzer.evaluate_scenarios()
    assert len(scenarios) == 3

    # Check Zero Maker Rebate scenario
    zero_rebate = next(s for s in scenarios if s.scenario_name == "ZERO_MAKER_REBATE")
    assert zero_rebate.counterfactual_maker_fee_btc == Decimal("0")
    # PnL delta should be exactly the negative of the rebate received (-298.46 BTC lost)
    assert zero_rebate.pnl_delta_btc < Decimal("0")

    # Check Bithumb Spot Portability scenario
    spot = next(s for s in scenarios if s.scenario_name == "BITHUMB_SPOT_PORTABILITY")
    assert spot.counterfactual_funding_btc == Decimal("0")
    # Under spot fees (+4bps maker, +4bps taker), total fees must be positive and large
    assert spot.counterfactual_total_fee_btc > Decimal("100")
    assert not spot.is_economically_viable or spot.fail_reason is not None

    # Check No Funding scenario
    no_funding = next(s for s in scenarios if s.scenario_name == "NO_FUNDING_INCOME")
    assert no_funding.counterfactual_funding_btc == Decimal("0")
    assert no_funding.pnl_delta_btc < Decimal("0")


def test_cusum_structural_break_detection() -> None:
    # Stable series with sudden regime shift in the middle
    series = [0.45] * 12 + [0.85] * 12
    labels = [f"2020-{i:02d}" for i in range(1, 13)] + [f"2021-{i:02d}" for i in range(1, 13)]

    breaks = ChangePointAnalyzer.detect_cusum_breaks(series, labels)
    assert len(breaks) == 1
    assert breaks[0].is_statistically_significant
    # Break should be detected around the jump boundary
    assert "2021" in breaks[0].label or "2020-12" in breaks[0].label


def test_composition_decomposition_exact_identity() -> None:
    # Period 1: 80% XBT (40% maker), 20% ETH (90% maker)
    # Aggregate P1 = 0.8 * 0.4 + 0.2 * 0.9 = 0.32 + 0.18 = 0.50
    p1_weights = {"XBTUSD": 0.80, "ETHUSD": 0.20}
    p1_rates = {"XBTUSD": 0.40, "ETHUSD": 0.90}

    # Period 2: 40% XBT (50% maker), 60% ETH (95% maker)
    # Aggregate P2 = 0.4 * 0.5 + 0.6 * 0.95 = 0.20 + 0.57 = 0.77
    p2_weights = {"XBTUSD": 0.40, "ETHUSD": 0.60}
    p2_rates = {"XBTUSD": 0.50, "ETHUSD": 0.95}

    decomp = CompositionAnalyzer.decompose(
        p1_weights, p1_rates, p2_weights, p2_rates, "2020", "2021"
    )

    assert abs(decomp.period_1_maker_ratio - 0.50) < 1e-4
    assert abs(decomp.period_2_maker_ratio - 0.77) < 1e-4
    # Total change = 0.27
    assert abs(decomp.total_change - 0.27) < 1e-4

    # The mathematical identity must hold exactly:
    # Total = intra_symbol + composition + interaction
    sum_components = (
        decomp.intra_symbol_effect
        + decomp.composition_effect
        + decomp.unexplained_interaction
    )
    assert abs(decomp.total_change - sum_components) < 1e-6
    # Composition effect must be positive due to moving weight into ETH (90% maker)
    assert decomp.composition_effect > 0.0
