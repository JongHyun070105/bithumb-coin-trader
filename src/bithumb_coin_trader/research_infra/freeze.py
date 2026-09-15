"""Candidate Freeze Mechanism for Microstructure Research.

Once Old72H/V2 research produces a candidate worth validating:
1. Freeze: hypothesis definition, feature set, parameters, label horizon,
   execution model, fee/latency/slippage assumptions, position sizing,
   evaluation metric, decision thresholds, code commit
2. Generate a freeze manifest with SHA-256 hash
3. Future holdout must be evaluated against frozen definition
4. Framework makes it obvious if researcher changed candidate after
   seeing holdout results

This is essential for later alpha claims.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .manifests import ResearchManifest


@dataclass(frozen=True)
class FrozenCandidate:
    """Immutable frozen candidate for prospective validation.

    Contains EVERYTHING needed to reproduce the experiment
    without access to the original research context.
    """

    freeze_id: str
    frozen_at: str
    git_commit: str

    # What was frozen
    hypothesis_id: str
    hypothesis_description: str
    feature_names: tuple[str, ...]
    feature_parameters: dict[str, Any]
    target_horizon_s: int
    target_type: str

    # Execution
    execution_assumptions: dict[str, Any]
    fee_regime: str
    fee_rate: float
    slippage_bps: float
    latency_ms: float
    position_size_krw: float

    # Model
    model_type: str
    model_parameters: dict[str, Any]

    # Decision thresholds
    entry_threshold: float | None
    exit_threshold: float | None

    # Evaluation
    evaluation_metric: str
    expected_sign: str

    # Source research
    source_manifest_fingerprint: str
    source_dataset_ids: tuple[str, ...]
    source_dataset_roles: tuple[str, ...]

    # Exploratory results (recorded but NOT used for validation)
    exploratory_ic: float | None
    exploratory_hit_rate: float | None
    exploratory_sharpe: float | None
    exploratory_net_pnl: float | None

    # Schema
    schema_version: str = "1.0.0"

    @property
    def freeze_hash(self) -> str:
        """SHA-256 hash of the frozen definition (excluding the hash itself)."""
        d = {}
        for k, v in self.__dict__.items():
            if k == "freeze_hash":
                continue
            if isinstance(v, tuple):
                d[k] = list(v)
            else:
                d[k] = v
        canonical = json.dumps(d, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        d = {}
        for k, v in self.__dict__.items():
            if isinstance(v, tuple):
                d[k] = list(v)
            else:
                d[k] = v
        d["freeze_hash"] = self.freeze_hash
        return d

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True))

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> FrozenCandidate:
        return cls(
            freeze_id=d["freeze_id"],
            frozen_at=d["frozen_at"],
            git_commit=d["git_commit"],
            hypothesis_id=d["hypothesis_id"],
            hypothesis_description=d.get("hypothesis_description", ""),
            feature_names=tuple(d["feature_names"]),
            feature_parameters=d.get("feature_parameters", {}),
            target_horizon_s=d["target_horizon_s"],
            target_type=d["target_type"],
            execution_assumptions=d.get("execution_assumptions", {}),
            fee_regime=d.get("fee_regime", "unknown"),
            fee_rate=d.get("fee_rate", 0.0),
            slippage_bps=d.get("slippage_bps", 0.0),
            latency_ms=d.get("latency_ms", 0.0),
            position_size_krw=d.get("position_size_krw", 0.0),
            model_type=d.get("model_type", "correlation"),
            model_parameters=d.get("model_parameters", {}),
            entry_threshold=d.get("entry_threshold"),
            exit_threshold=d.get("exit_threshold"),
            evaluation_metric=d.get("evaluation_metric", "ic"),
            expected_sign=d.get("expected_sign", "positive"),
            source_manifest_fingerprint=d.get("source_manifest_fingerprint", ""),
            source_dataset_ids=tuple(d.get("source_dataset_ids", [])),
            source_dataset_roles=tuple(d.get("source_dataset_roles", [])),
            exploratory_ic=d.get("exploratory_ic"),
            exploratory_hit_rate=d.get("exploratory_hit_rate"),
            exploratory_sharpe=d.get("exploratory_sharpe"),
            exploratory_net_pnl=d.get("exploratory_net_pnl"),
            schema_version=d.get("schema_version", "1.0.0"),
        )

    @classmethod
    def load(cls, path: Path) -> FrozenCandidate:
        return cls.from_dict(json.loads(path.read_text()))


def verify_holdout_integrity(
    frozen: FrozenCandidate,
    holdout_manifest: ResearchManifest,
) -> list[str]:
    """Verify that a holdout evaluation uses the exact frozen definition.

    Returns list of violations. Empty = integrity maintained.
    """
    violations = []

    if holdout_manifest.hypothesis_id != frozen.hypothesis_id:
        violations.append(
            f"Hypothesis mismatch: frozen={frozen.hypothesis_id}, "
            f"holdout={holdout_manifest.hypothesis_id}"
        )

    if holdout_manifest.model_type != frozen.model_type:
        violations.append(
            f"Model type mismatch: frozen={frozen.model_type}, "
            f"holdout={holdout_manifest.model_type}"
        )

    if holdout_manifest.model_parameters != frozen.model_parameters:
        violations.append(
            f"Model parameters changed after freeze"
        )

    # Check feature config
    frozen_features = set(frozen.feature_names)
    holdout_features = set(holdout_manifest.feature_config.get("feature_names", []))
    if frozen_features != holdout_features:
        violations.append(
            f"Feature set mismatch: frozen={frozen_features}, "
            f"holdout={holdout_features}"
        )

    # Check execution assumptions
    frozen_exec = frozen.execution_assumptions
    holdout_exec = holdout_manifest.execution_assumptions
    for key in ["fee_rate", "slippage_bps", "latency_ms"]:
        if frozen_exec.get(key) != holdout_exec.get(key):
            violations.append(
                f"Execution assumption mismatch: {key} "
                f"frozen={frozen_exec.get(key)}, holdout={holdout_exec.get(key)}"
            )

    # Check label config
    if holdout_manifest.label_config.get("target_horizon_s") != frozen.target_horizon_s:
        violations.append(
            f"Target horizon mismatch: frozen={frozen.target_horizon_s}, "
            f"holdout={holdout_manifest.label_config.get('target_horizon_s')}"
        )

    return violations
