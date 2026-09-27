"""Tests for retired legacy analysis guards and the composition identity."""

from __future__ import annotations

import pytest

from bithumb_coin_trader.research_infra.behavioral_and_fee_analysis import (
    ChangePointAnalyzer,
    CompositionAnalyzer,
    FeeEconomicsAnalyzer,
    LegacyAnalysisDisabled,
    run_full_behavioral_and_fee_analysis,
)


def test_legacy_fee_counterfactual_fails_closed() -> None:
    with pytest.raises(LegacyAnalysisDisabled, match="hardcoded and unverified"):
        FeeEconomicsAnalyzer.evaluate_scenarios()


def test_legacy_ols_cusum_fails_closed() -> None:
    series = [0.45] * 12 + [0.85] * 12
    labels = [f"2020-{i:02d}" for i in range(1, 13)] + [f"2021-{i:02d}" for i in range(1, 13)]

    with pytest.raises(LegacyAnalysisDisabled, match="hardcoded and unverified"):
        ChangePointAnalyzer.detect_cusum_breaks(series, labels)


def test_legacy_report_fails_closed() -> None:
    with pytest.raises(LegacyAnalysisDisabled, match="hardcoded and unverified"):
        run_full_behavioral_and_fee_analysis()


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
