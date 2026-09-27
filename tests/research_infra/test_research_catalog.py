from __future__ import annotations

import json
from pathlib import Path

import pytest

from bithumb_coin_trader.research_infra import cli
from bithumb_coin_trader.research_infra.research_catalog import (
    CandidateFamilyRecord,
    HypothesisCatalog,
    HypothesisRecord,
    ResearchCatalogError,
    default_candidate_families,
)


def test_hypothesis_catalog_seed_is_append_only_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "hypotheses.jsonl"
    catalog = HypothesisCatalog(path)

    assert catalog.seed_defaults() > 0
    first_bytes = path.read_bytes()
    first_events = catalog.read()
    assert catalog.seed_defaults() == 0
    assert path.read_bytes() == first_bytes
    assert len(first_events) >= 10
    assert first_events[-1]["record"]["role"] == "HYPOTHESIS_GENERATION_ONLY"

    existing = first_events[0]["record"]
    altered = HypothesisRecord.from_dict({
        **existing,
        "description": "attempt to rewrite historical definition",
    })
    with pytest.raises(ResearchCatalogError, match="immutable once registered"):
        catalog.register(altered)


def test_hypothesis_catalog_detects_hash_chain_tampering(tmp_path: Path) -> None:
    path = tmp_path / "hypotheses.jsonl"
    catalog = HypothesisCatalog(path)
    catalog.seed_defaults()
    lines = path.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])
    event["record"]["description"] = "tampered"
    lines[0] = json.dumps(event, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ResearchCatalogError, match="hash chain invalid"):
        catalog.read()


def test_external_hypothesis_must_remain_generation_only() -> None:
    with pytest.raises(ResearchCatalogError, match="HYPOTHESIS_GENERATION_ONLY"):
        HypothesisRecord(
            hypothesis_id="external-1",
            origin="AOA expert notes",
            description="source not verified",
            economic_rationale="unknown",
            strategy_family="external_aoa_expert_ideas",
            required_features=("unknown",),
            known_confounders=("provenance",),
            falsification_criteria=("recover source first",),
            research_status="SOURCE_NOT_PRESENT",
        )


def test_candidate_family_inventory_preserves_failed_and_untrusted_history(tmp_path: Path, capsys) -> None:
    families = {family.family_id: family for family in default_candidate_families()}

    assert "Core + Satellite" in families["v6_satellite_and_core_satellite"].configuration_notes
    assert "+48.43" in families["v6_satellite_and_core_satellite"].current_status
    assert "INVALIDATED" in families["v7_multi_asset_and_intraday"].current_status
    assert "LOAO_FAIL" in families["v8_cross_sectional_intraday"].current_status
    assert families["external_aoa_expert_ideas"].source_role == "HYPOTHESIS_GENERATION_ONLY"

    output = tmp_path / "families.json"
    assert cli.main(["candidate-families", "export", "--path", str(output)]) == 0
    snapshot = json.loads(output.read_text(encoding="utf-8"))
    assert snapshot["catalog_status"] == "INVENTORY_ONLY_NO_PROMOTIONS"
    assert {item["family_id"] for item in snapshot["families"]} == set(families)

    output.write_text("{}", encoding="utf-8")
    assert cli.main(["candidate-families", "export", "--path", str(output)]) == 2
    assert "refusing to overwrite" in capsys.readouterr().err


def test_hypothesis_catalog_cli_seed_and_list(tmp_path: Path, capsys) -> None:
    path = tmp_path / "hypotheses.jsonl"
    assert cli.main(["hypotheses", "catalog-seed", "--path", str(path)]) == 0
    assert "Added" in capsys.readouterr().out
    assert cli.main(["hypotheses", "catalog-list", "--path", str(path)]) == 0
    output = capsys.readouterr().out
    assert "H-AOA-EXPERT-UNKNOWN" in output
    assert "HYPOTHESIS_GENERATION_ONLY" in output


def test_candidate_family_rejects_mismatched_strategy_adapter() -> None:
    with pytest.raises(ValueError, match="belongs to candidate_family"):
        cli._load_batch_experiments({
            "schema_version": 1,
            "experiments": [{
                "candidate_family": "v2_daily_absolute_momentum",
                "strategy_id": "sma_trend",
                "strategy_config": {},
                "feature_config": {},
                "parameter_sets": [{}],
                "seed": 1,
            }],
        })


def test_governed_candidate_families_are_accepted_with_all_control_strategies() -> None:
    experiments = []
    for strategy_id, family_id, parameters in (
        ("cash", "baseline_controls", {}),
        ("buy_and_hold", "baseline_controls", {}),
        ("randomized_placebo", "baseline_controls", {"exposure_probability": 0.5, "target_weight": 1.0}),
        ("daily_weekly_absolute_momentum_126_63", "daily_weekly_trend_and_momentum", {}),
        ("v3_frozen_majority_2_of_3", "v3_daily_target_weight", {}),
        ("v4_adaptive_donchian_atr", "v4_v4b_regime_breakout_and_trend", {}),
        ("v4_52week_high_breakout", "v4_v4b_regime_breakout_and_trend", {}),
        ("v5_regime_adaptive_donchian", "v5_regime_dual_momentum_pullback", {}),
        ("v6_daily_ema_pullback", "v6_satellite_and_core_satellite", {}),
        ("core70_satellite30_v6_fast_donchian", "v6_satellite_and_core_satellite", {}),
    ):
        experiments.append({
            "candidate_family": family_id,
            "strategy_id": strategy_id,
            "strategy_config": {},
            "feature_config": {"input": "completed_KST_daily_candles"},
            "parameter_sets": [parameters],
            "seed": 23,
        })
    parsed = cli._load_batch_experiments({"schema_version": 1, "experiments": experiments})
    assert [item.strategy_id for item in parsed] == [item["strategy_id"] for item in experiments]
