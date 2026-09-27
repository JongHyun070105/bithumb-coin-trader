from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bithumb_coin_trader.research_infra.definition_registry import (
    DefinitionRegistryError,
    FeatureDefinition,
    StrategyDefinition,
    VersionedDefinitionRegistry,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_versioned_definitions_are_append_only_and_bind_config_hashes(tmp_path: Path) -> None:
    registry = VersionedDefinitionRegistry(tmp_path / "definitions.jsonl")
    strategy = StrategyDefinition(
        definition_id="example_strategy",
        version="1.0.0",
        implementation_sha256=_sha("strategy source"),
        config_schema={"type": "object"},
    )
    feature = FeatureDefinition(
        definition_id="example_feature",
        version="1.0.0",
        implementation_sha256=_sha("feature source"),
        config_schema={"type": "object"},
    )

    strategy_record = registry.register(strategy)
    assert registry.register(strategy) == strategy_record
    registry.register(feature)
    binding = registry.bind("strategy", "example_strategy", "1.0.0", {"window": 10})

    assert binding["definition_sha256"] == strategy_record["definition_sha256"]
    assert binding["config_sha256"] == _sha('{"window":10}')
    assert len(registry.path.read_text(encoding="utf-8").splitlines()) == 2

    with pytest.raises(DefinitionRegistryError, match="immutable; bump the version"):
        registry.register(StrategyDefinition(
            definition_id="example_strategy",
            version="1.0.0",
            implementation_sha256=_sha("changed source"),
            config_schema={"type": "object"},
        ))

    next_version = registry.register(StrategyDefinition(
        definition_id="example_strategy",
        version="2.0.0",
        implementation_sha256=_sha("changed source"),
        config_schema={"type": "object"},
    ))
    assert next_version["version"] == "2.0.0"


def test_definition_registry_rejects_tampered_records(tmp_path: Path) -> None:
    path = tmp_path / "definitions.jsonl"
    registry = VersionedDefinitionRegistry(path)
    registry.register(FeatureDefinition(
        definition_id="feature_a",
        version="1.0.0",
        implementation_sha256=_sha("feature"),
        config_schema={},
    ))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["implementation_sha256"] = _sha("tampered")
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(DefinitionRegistryError, match="hash or schema validation failed"):
        registry.resolve("feature", "feature_a", "1.0.0")
