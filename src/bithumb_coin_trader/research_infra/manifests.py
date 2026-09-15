"""Research Manifest System.

Every meaningful experiment emits a machine-readable manifest with:
- research_run_id, timestamp, git commit
- dataset IDs, roles, source fingerprints
- DQ filters, feature/label configs
- hypothesis ID, train/validation/test ranges
- execution/fee/latency/slippage assumptions
- model parameters, random seed
- result artifact paths, metrics
- scientific classification
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping
import uuid


def _get_git_commit() -> str:
    """Get current git commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


@dataclass(frozen=True)
class ResearchManifest:
    """Immutable research experiment manifest."""

    research_run_id: str
    timestamp: str
    git_commit: str

    # Dataset
    dataset_ids: tuple[str, ...]
    dataset_roles: tuple[str, ...]
    source_time_range_start: str | None
    source_time_range_end: str | None
    source_fingerprints: dict[str, str]  # dataset_id -> fingerprint

    # DQ
    dq_filters: dict[str, Any]
    dq_exclusion_count: int

    # Feature config
    feature_config: dict[str, Any]

    # Label config
    label_config: dict[str, Any]

    # Hypothesis
    hypothesis_id: str
    hypothesis_description: str

    # Evaluation
    train_range: tuple[str, str] | None  # (start, end) ISO
    test_range: tuple[str, str] | None
    n_folds: int
    embargo_s: float

    # Execution assumptions
    execution_assumptions: dict[str, Any]

    # Model
    model_type: str
    model_parameters: dict[str, Any]
    random_seed: int | None

    # Results
    result_artifact_paths: dict[str, str]
    metrics: dict[str, Any]

    # Classification
    scientific_classification: str

    # Schema
    schema_version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        d = {}
        for k, v in self.__dict__.items():
            if isinstance(v, tuple):
                d[k] = list(v)
            else:
                d[k] = v
        return d

    def compute_fingerprint(self) -> str:
        """Compute SHA-256 fingerprint of the manifest."""
        d = self.to_dict()
        canonical = json.dumps(d, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        d = self.to_dict()
        d["fingerprint"] = self.compute_fingerprint()
        path.write_text(json.dumps(d, indent=2, sort_keys=True))

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ResearchManifest:
        return cls(
            research_run_id=d["research_run_id"],
            timestamp=d["timestamp"],
            git_commit=d["git_commit"],
            dataset_ids=tuple(d["dataset_ids"]),
            dataset_roles=tuple(d["dataset_roles"]),
            source_time_range_start=d.get("source_time_range_start"),
            source_time_range_end=d.get("source_time_range_end"),
            source_fingerprints=d.get("source_fingerprints", {}),
            dq_filters=d.get("dq_filters", {}),
            dq_exclusion_count=d.get("dq_exclusion_count", 0),
            feature_config=d.get("feature_config", {}),
            label_config=d.get("label_config", {}),
            hypothesis_id=d["hypothesis_id"],
            hypothesis_description=d.get("hypothesis_description", ""),
            train_range=tuple(d["train_range"]) if d.get("train_range") else None,
            test_range=tuple(d["test_range"]) if d.get("test_range") else None,
            n_folds=d.get("n_folds", 0),
            embargo_s=d.get("embargo_s", 0.0),
            execution_assumptions=d.get("execution_assumptions", {}),
            model_type=d.get("model_type", "unspecified"),
            model_parameters=d.get("model_parameters", {}),
            random_seed=d.get("random_seed"),
            result_artifact_paths=d.get("result_artifact_paths", {}),
            metrics=d.get("metrics", {}),
            scientific_classification=d.get("scientific_classification", "UNTESTED"),
            schema_version=d.get("schema_version", "1.0.0"),
        )

    @classmethod
    def load(cls, path: Path) -> ResearchManifest:
        return cls.from_dict(json.loads(path.read_text()))


def create_manifest(
    hypothesis_id: str,
    hypothesis_description: str,
    dataset_ids: list[str],
    dataset_roles: list[str],
    feature_config: dict[str, Any],
    label_config: dict[str, Any],
    execution_assumptions: dict[str, Any],
    metrics: dict[str, Any],
    scientific_classification: str,
    model_type: str = "correlation",
    model_parameters: dict[str, Any] | None = None,
    dq_filters: dict[str, Any] | None = None,
    dq_exclusion_count: int = 0,
    train_range: tuple[str, str] | None = None,
    test_range: tuple[str, str] | None = None,
    n_folds: int = 0,
    embargo_s: float = 0.0,
    result_artifact_paths: dict[str, str] | None = None,
    source_fingerprints: dict[str, str] | None = None,
    random_seed: int | None = None,
) -> ResearchManifest:
    """Helper to create a manifest with sensible defaults."""
    return ResearchManifest(
        research_run_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc).isoformat(),
        git_commit=_get_git_commit(),
        dataset_ids=tuple(dataset_ids),
        dataset_roles=tuple(dataset_roles),
        source_time_range_start=None,
        source_time_range_end=None,
        source_fingerprints=source_fingerprints or {},
        dq_filters=dq_filters or {},
        dq_exclusion_count=dq_exclusion_count,
        feature_config=feature_config,
        label_config=label_config,
        hypothesis_id=hypothesis_id,
        hypothesis_description=hypothesis_description,
        train_range=train_range,
        test_range=test_range,
        n_folds=n_folds,
        embargo_s=embargo_s,
        execution_assumptions=execution_assumptions,
        model_type=model_type,
        model_parameters=model_parameters or {},
        random_seed=random_seed,
        result_artifact_paths=result_artifact_paths or {},
        metrics=metrics,
        scientific_classification=scientific_classification,
    )
