"""Hypothesis Registry for Microstructure Research.

Each hypothesis includes:
- Economic/microstructure rationale
- Required feeds and DQ conditions
- Feature set
- Target horizon
- Expected sign
- Execution assumption
- Evaluation metric
- Status (never ALPHA_PROVEN from historical data)

Initial hypotheses:
    H1: Orderbook imbalance predicts short-term mid-price direction
    H2: Signed trade-flow imbalance predicts short-term continuation
    H3: Microprice displacement predicts near-term price movement
    H4: Binance/Upbit short-term moves lead Bithumb
    H5: Cross-exchange basis shocks revert into Bithumb

ALL results from Old72H/V2 are DEVELOPMENT / EXPLORATORY ONLY.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Mapping, Sequence


class HypothesisStatus(str, Enum):
    UNTESTED = "UNTESTED"
    EXPLORATORY_POSITIVE = "EXPLORATORY_POSITIVE"
    EXPLORATORY_NEGATIVE = "EXPLORATORY_NEGATIVE"
    FRAGILE = "FRAGILE"
    COST_KILLED = "COST_KILLED"
    INCONSISTENT_ACROSS_DATASETS = "INCONSISTENT_ACROSS_DATASETS"
    CANDIDATE_FOR_PROSPECTIVE_RESEARCH = "CANDIDATE_FOR_PROSPECTIVE_RESEARCH"
    FROZEN_FOR_FUTURE_HOLDOUT = "FROZEN_FOR_FUTURE_HOLDOUT"
    PROSPECTIVE_PASS = "PROSPECTIVE_PASS"
    PROSPECTIVE_FAIL = "PROSPECTIVE_FAIL"


@dataclass(frozen=True)
class Hypothesis:
    """Immutable hypothesis definition."""

    hypothesis_id: str
    description: str
    rationale: str
    required_feeds: tuple[str, ...]  # e.g., ("bithumb/orderbook", "bithumb/trade")
    required_dq: str  # DQ condition description
    feature_names: tuple[str, ...]  # Feature vector field names
    target_horizon_s: int  # Seconds
    target_type: str  # "mid_return", "direction", "executable_return"
    expected_sign: str  # "positive", "negative", "uncertain"
    execution_assumption: str  # Key into execution assumptions
    evaluation_metric: str  # "sharpe", "ic", "hit_rate", "return_by_quantile"
    status: HypothesisStatus = HypothesisStatus.UNTESTED
    dataset_roles_used: tuple[str, ...] = ()
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Hypothesis:
        return cls(
            hypothesis_id=d["hypothesis_id"],
            description=d["description"],
            rationale=d["rationale"],
            required_feeds=tuple(d["required_feeds"]),
            required_dq=d["required_dq"],
            feature_names=tuple(d["feature_names"]),
            target_horizon_s=d["target_horizon_s"],
            target_type=d["target_type"],
            expected_sign=d["expected_sign"],
            execution_assumption=d["execution_assumption"],
            evaluation_metric=d["evaluation_metric"],
            status=HypothesisStatus(d.get("status", "UNTESTED")),
            dataset_roles_used=tuple(d.get("dataset_roles_used", [])),
            notes=d.get("notes", ""),
        )


@dataclass
class HypothesisRegistry:
    """Registry of research hypotheses."""

    def __init__(self) -> None:
        self._hypotheses: dict[str, Hypothesis] = {}

    def register(self, hypothesis: Hypothesis) -> None:
        if hypothesis.hypothesis_id in self._hypotheses:
            raise ValueError(f"Hypothesis '{hypothesis.hypothesis_id}' already registered")
        self._hypotheses[hypothesis.hypothesis_id] = hypothesis

    def get(self, hypothesis_id: str) -> Hypothesis:
        if hypothesis_id not in self._hypotheses:
            raise ValueError(f"Hypothesis '{hypothesis_id}' not found")
        return self._hypotheses[hypothesis_id]

    def list_hypotheses(self) -> list[Hypothesis]:
        return list(self._hypotheses.values())

    def update_status(
        self,
        hypothesis_id: str,
        status: HypothesisStatus,
        dataset_role: str | None = None,
    ) -> None:
        h = self._hypotheses[hypothesis_id]
        roles = list(h.dataset_roles_used)
        if dataset_role and dataset_role not in roles:
            roles.append(dataset_role)
        updated = Hypothesis(
            hypothesis_id=h.hypothesis_id,
            description=h.description,
            rationale=h.rationale,
            required_feeds=h.required_feeds,
            required_dq=h.required_dq,
            feature_names=h.feature_names,
            target_horizon_s=h.target_horizon_s,
            target_type=h.target_type,
            expected_sign=h.expected_sign,
            execution_assumption=h.execution_assumption,
            evaluation_metric=h.evaluation_metric,
            status=status,
            dataset_roles_used=tuple(roles),
            notes=h.notes,
        )
        self._hypotheses[hypothesis_id] = updated


def register_default_hypotheses(registry: HypothesisRegistry) -> None:
    """Register the initial 5 hypotheses for microstructure research."""

    registry.register(Hypothesis(
        hypothesis_id="H1",
        description="Orderbook imbalance predicts short-term mid-price direction/return",
        rationale="Persistent buy/sell pressure in the limit order book reflects "
                  "informed trader positioning. Imbalance should predict short-term "
                  "price movement toward the heavier side.",
        required_feeds=("bithumb/orderbook",),
        required_dq="Bithumb orderbook DATA_PRESENT for target market",
        feature_names=("depth_imbalance_l1", "depth_imbalance_l5", "qi_l1", "qi_l5"),
        target_horizon_s=5,
        target_type="mid_return",
        expected_sign="positive",  # positive imbalance → positive return
        execution_assumption="default_taker",
        evaluation_metric="ic",
    ))

    registry.register(Hypothesis(
        hypothesis_id="H2",
        description="Signed trade-flow imbalance predicts short-term continuation",
        rationale="Aggressive buy/sell volume imbalance reflects directional "
                  "informed flow. Short-term continuation expected, with possible "
                  "reversal at extremes (exhaustion).",
        required_feeds=("bithumb/trade",),
        required_dq="Bithumb trade DATA_PRESENT for target market",
        feature_names=("ati_5s", "ati_30s", "signed_volume_30s", "trade_count_30s"),
        target_horizon_s=10,
        target_type="mid_return",
        expected_sign="positive",  # positive ATI → positive return
        execution_assumption="default_taker",
        evaluation_metric="ic",
    ))

    registry.register(Hypothesis(
        hypothesis_id="H3",
        description="Microprice displacement from mid predicts near-term price movement",
        rationale="Microprice (size-weighted mid) deviating from simple mid indicates "
                  "asymmetric depth. The market is likely to move toward the microprice "
                  "as the heavier side absorbs flow.",
        required_feeds=("bithumb/orderbook",),
        required_dq="Bithumb orderbook DATA_PRESENT for target market",
        feature_names=("microprice_bias_bps", "microprice_displacement"),
        target_horizon_s=5,
        target_type="mid_return",
        expected_sign="positive",  # positive displacement → positive return
        execution_assumption="default_taker",
        evaluation_metric="ic",
    ))

    registry.register(Hypothesis(
        hypothesis_id="H4",
        description="Binance/Upbit short-term moves lead Bithumb over very short horizons",
        rationale="Price discovery may happen first on larger/more-liquid exchanges. "
                  "Short-term returns on Binance/Upbit may predict subsequent Bithumb "
                  "returns over very short horizons (< 30s).",
        required_feeds=("bithumb/orderbook", "binance/trade", "upbit/orderbook"),
        required_dq="All three exchanges DATA_PRESENT for overlapping market",
        feature_names=(
            "cross_exchange_return_diff_binance_5s",
            "cross_exchange_return_diff_upbit_5s",
            "cross_exchange_basis_binance",
            "cross_exchange_basis_upbit",
        ),
        target_horizon_s=10,
        target_type="mid_return",
        expected_sign="positive",  # positive diff → Bithumb catches up
        execution_assumption="default_taker",
        evaluation_metric="ic",
    ))

    registry.register(Hypothesis(
        hypothesis_id="H5",
        description="Cross-exchange basis shocks revert or transmit into Bithumb",
        rationale="Large deviations in Bithumb price vs Binance/Upbit (basis shocks) "
                  "may revert if caused by temporary Bithumb-specific flow, or transmit "
                  "if caused by global price moves. Direction depends on regime.",
        required_feeds=("bithumb/orderbook", "binance/trade"),
        required_dq="Both Bithumb and Binance DATA_PRESENT",
        feature_names=(
            "cross_exchange_basis_binance",
            "cross_exchange_basis_upbit",
        ),
        target_horizon_s=30,
        target_type="mid_return",
        expected_sign="uncertain",  # Could be reversion or continuation
        execution_assumption="default_taker",
        evaluation_metric="return_by_quantile",
    ))
