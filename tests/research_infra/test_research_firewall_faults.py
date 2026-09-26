"""Adversarial fault injection tests for the ResearchFirewall and CandidateFreeze governance."""

from __future__ import annotations

import pytest

from bithumb_coin_trader.research_infra.research_firewall import (
    CandidateFreezeContract,
    DatasetProvenance,
    FirewallViolationError,
    ResearchFirewall,
    ResearchHypothesis,
)


def _make_dummy_candidate(
    eval_prov: DatasetProvenance = DatasetProvenance.CANONICAL_PROSPECTIVE,
    threshold: float = 1.5,
) -> CandidateFreezeContract:
    return CandidateFreezeContract(
        candidate_id="cand-001",
        hypothesis_id="hyp-001",
        feature_names=("spread_bps", "orderbook_imbalance", "realized_vol_5m"),
        model_architecture="LightGBMClassifier",
        hyperparameters={"max_depth": 4, "learning_rate": 0.05},
        execution_assumptions={"fee_bps": 4.0, "latency_ms": 250},
        eval_metric="sharpe_ratio",
        threshold_pass_value=threshold,
        training_data_provenance=DatasetProvenance.CANONICAL_PROSPECTIVE,
        eval_data_provenance=eval_prov,
        frozen_at_utc="2026-09-27T00:00:00Z",
    )


def test_fault_prohibited_feature_expert_pnl() -> None:
    with pytest.raises(FirewallViolationError, match="Prohibited feature 'expert_pnl'"):
        ResearchFirewall.validate_training_dataset(
            provenance=DatasetProvenance.CANONICAL_PROSPECTIVE,
            feature_names=["spread_bps", "expert_pnl"],
            target_name="forward_return_10m",
        )


def test_fault_prohibited_feature_expert_maker_ratio() -> None:
    with pytest.raises(FirewallViolationError, match="Prohibited feature 'expert_maker_ratio'"):
        ResearchFirewall.validate_training_dataset(
            provenance=DatasetProvenance.CANONICAL_PROSPECTIVE,
            feature_names=["expert_maker_ratio", "spread_bps"],
            target_name="forward_return_10m",
        )


def test_fault_prohibited_feature_future_price() -> None:
    with pytest.raises(FirewallViolationError, match="Prohibited feature 'future_price'"):
        ResearchFirewall.validate_training_dataset(
            provenance=DatasetProvenance.CANONICAL_PROSPECTIVE,
            feature_names=["future_price", "spread_bps"],
            target_name="forward_return_10m",
        )


def test_fault_target_leakage_expert_action() -> None:
    with pytest.raises(FirewallViolationError, match="Prohibited target 'expert_action'"):
        ResearchFirewall.validate_training_dataset(
            provenance=DatasetProvenance.CANONICAL_PROSPECTIVE,
            feature_names=["spread_bps", "imbalance"],
            target_name="expert_action",
        )


def test_fault_target_leakage_expert_direction() -> None:
    with pytest.raises(FirewallViolationError, match="Prohibited target 'expert_direction'"):
        ResearchFirewall.validate_training_dataset(
            provenance=DatasetProvenance.CANONICAL_PROSPECTIVE,
            feature_names=["spread_bps", "imbalance"],
            target_name="expert_direction",
        )


def test_fault_unattributed_dataset_training() -> None:
    with pytest.raises(FirewallViolationError, match="Unattributed historical data"):
        ResearchFirewall.validate_training_dataset(
            provenance=DatasetProvenance.HISTORICAL_UNATTRIBUTED,
            feature_names=["spread_bps"],
            target_name="forward_return",
        )


def test_fault_holdout_external_expert_dataset_blocked() -> None:
    cand = _make_dummy_candidate()
    with pytest.raises(FirewallViolationError, match="External expert dataset CANNOT be used as holdout"):
        ResearchFirewall.validate_holdout_evaluation(
            eval_provenance=DatasetProvenance.EXTERNAL_EXPERT,
            candidate=cand,
        )


def test_fault_holdout_unattributed_dataset_blocked() -> None:
    cand = _make_dummy_candidate()
    with pytest.raises(FirewallViolationError, match="Unattributed data cannot serve as holdout"):
        ResearchFirewall.validate_holdout_evaluation(
            eval_provenance=DatasetProvenance.HISTORICAL_UNATTRIBUTED,
            candidate=cand,
        )


def test_fault_candidate_contract_mismatch() -> None:
    # Candidate contract specifies external expert as eval, which is invalid
    cand = _make_dummy_candidate(eval_prov=DatasetProvenance.EXTERNAL_EXPERT)
    with pytest.raises(FirewallViolationError, match="Candidate evaluation contract specifies"):
        ResearchFirewall.validate_holdout_evaluation(
            eval_provenance=DatasetProvenance.CANONICAL_PROSPECTIVE,
            candidate=cand,
        )


def test_fault_candidate_promotion_threshold_failure() -> None:
    cand = _make_dummy_candidate(threshold=1.5)
    with pytest.raises(FirewallViolationError, match="did not reach threshold"):
        ResearchFirewall.validate_candidate_promotion(
            candidate=cand,
            eval_provenance=DatasetProvenance.CANONICAL_PROSPECTIVE,
            metric_value=1.2,  # 1.2 < 1.5
        )


def test_valid_candidate_promotion_success() -> None:
    cand = _make_dummy_candidate(threshold=1.5)
    success = ResearchFirewall.validate_candidate_promotion(
        candidate=cand,
        eval_provenance=DatasetProvenance.CANONICAL_PROSPECTIVE,
        metric_value=1.8,  # 1.8 >= 1.5
    )
    assert success is True


def test_research_hypothesis_catalog_structure() -> None:
    hyp = ResearchHypothesis(
        hypothesis_id="HYP-INVENTORY-SKEW-01",
        title="Inventory Skew Volatility Response",
        description="Market makers skew quotes away from accumulated inventory during high volatility bursts.",
        rationale_from_expert_dataset="Observed in 2020-2021 altcoin cycles where maker quote sizes asymmetrically shifted.",
        required_features=("inventory_depth_ratio", "orderbook_imbalance_10s"),
        testable_prediction="Quote skewness predicts 1-minute inventory reversion with Sharpe > 1.0 on spot.",
        falsification_criteria="If Bithumb KRW spot spread is wider than mean reversion profit after 4bps fee.",
    )
    d = hyp.to_dict()
    assert d["hypothesis_id"] == "HYP-INVENTORY-SKEW-01"
    assert d["status"] == "PROPOSED"
    assert len(d["required_features"]) == 2
