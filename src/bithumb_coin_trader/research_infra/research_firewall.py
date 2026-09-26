"""External Expert Research Firewall and Post-30H Governance Gate.

Enforces:
- Section 16: External expert dataset may ONLY be used for hypothesis generation.
- Section 17: Strict firewall against target leakage, holdout contamination, and candidate promotion.
- Fail-closed validation for candidate freeze contracts and prospective evaluations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


class DatasetProvenance(str, Enum):
    CANONICAL_PROSPECTIVE = "CANONICAL_PROSPECTIVE"
    EXTERNAL_EXPERT = "EXTERNAL_EXPERT"
    SYNTHETIC_TEST = "SYNTHETIC_TEST"
    HISTORICAL_UNATTRIBUTED = "HISTORICAL_UNATTRIBUTED"


class FirewallViolationError(PermissionError):
    """Raised when an operation violates scientific governance or the external expert firewall."""


@dataclass(frozen=True)
class ResearchHypothesis:
    hypothesis_id: str
    title: str
    description: str
    rationale_from_expert_dataset: str
    required_features: tuple[str, ...]
    testable_prediction: str
    falsification_criteria: str
    target_market: str = "BITHUMB_KRW"
    status: str = "PROPOSED"  # PROPOSED, REJECTED, FROZEN_FOR_TESTING, RETIRED

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "title": self.title,
            "description": self.description,
            "rationale_from_expert_dataset": self.rationale_from_expert_dataset,
            "required_features": list(self.required_features),
            "testable_prediction": self.testable_prediction,
            "falsification_criteria": self.falsification_criteria,
            "target_market": self.target_market,
            "status": self.status,
        }


@dataclass(frozen=True)
class CandidateFreezeContract:
    candidate_id: str
    hypothesis_id: str
    feature_names: tuple[str, ...]
    model_architecture: str
    hyperparameters: Mapping[str, Any]
    execution_assumptions: Mapping[str, Any]
    eval_metric: str
    threshold_pass_value: float
    training_data_provenance: DatasetProvenance
    eval_data_provenance: DatasetProvenance
    frozen_at_utc: str
    status: str = "FROZEN"

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "hypothesis_id": self.hypothesis_id,
            "feature_names": list(self.feature_names),
            "model_architecture": self.model_architecture,
            "hyperparameters": dict(self.hyperparameters),
            "execution_assumptions": dict(self.execution_assumptions),
            "eval_metric": self.eval_metric,
            "threshold_pass_value": self.threshold_pass_value,
            "training_data_provenance": self.training_data_provenance.value,
            "eval_data_provenance": self.eval_data_provenance.value,
            "frozen_at_utc": self.frozen_at_utc,
            "status": self.status,
        }


class ResearchFirewall:
    """Enforces scientific boundaries and prevents expert data leakage."""

    PROHIBITED_FEATURE_NAMES = {
        "expert_pnl",
        "expert_maker_ratio",
        "expert_order_side",
        "expert_order_price",
        "expert_action",
        "expert_trade_direction",
        "future_price",
        "future_return",
    }

    @classmethod
    def validate_training_dataset(
        cls,
        provenance: DatasetProvenance,
        feature_names: Sequence[str],
        target_name: str,
    ) -> None:
        """Validates that training dataset does not leak prohibited features or labels."""
        if provenance == DatasetProvenance.HISTORICAL_UNATTRIBUTED:
            raise FirewallViolationError(
                "Training aborted: Unattributed historical data violates provenance rules."
            )

        # Check prohibited features
        for f in feature_names:
            if f.lower() in cls.PROHIBITED_FEATURE_NAMES:
                raise FirewallViolationError(
                    f"Firewall violation: Prohibited feature '{f}' cannot be used in training."
                )

        # Check target name
        if "expert_" in target_name.lower():
            raise FirewallViolationError(
                f"Firewall violation: Prohibited target '{target_name}'. "
                "Mimicking expert actions as labels creates fatal target leakage."
            )

    @classmethod
    def validate_holdout_evaluation(
        cls,
        eval_provenance: DatasetProvenance,
        candidate: CandidateFreezeContract,
    ) -> None:
        """Validates that final prospective evaluation strictly uses canonical prospective data."""
        if eval_provenance == DatasetProvenance.EXTERNAL_EXPERT:
            raise FirewallViolationError(
                "CRITICAL FIREWALL VIOLATION: External expert dataset CANNOT be used as holdout evaluation! "
                "Holdout must be canonical prospective data."
            )

        if eval_provenance == DatasetProvenance.HISTORICAL_UNATTRIBUTED:
            raise FirewallViolationError(
                "Firewall violation: Unattributed data cannot serve as holdout evaluation."
            )

        if candidate.eval_data_provenance != DatasetProvenance.CANONICAL_PROSPECTIVE:
            raise FirewallViolationError(
                f"Candidate evaluation contract specifies '{candidate.eval_data_provenance}', "
                f"expected '{DatasetProvenance.CANONICAL_PROSPECTIVE}'."
            )

    @classmethod
    def validate_candidate_promotion(
        cls,
        candidate: CandidateFreezeContract,
        eval_provenance: DatasetProvenance,
        metric_value: float,
    ) -> bool:
        """Enforces that candidate promotion to alpha/paper requires canonical prospective holdout pass."""
        cls.validate_holdout_evaluation(eval_provenance, candidate)

        if metric_value < candidate.threshold_pass_value:
            raise FirewallViolationError(
                f"Candidate rejected: Metric value {metric_value:.4f} did not reach threshold {candidate.threshold_pass_value:.4f}."
            )

        return True
