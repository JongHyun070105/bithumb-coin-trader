from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import subprocess

import pytest

from bithumb_coin_trader.models import Candle
from bithumb_coin_trader.research_infra import cli
from bithumb_coin_trader.research_infra.batch import BatchExperiment, run_research_batch
from bithumb_coin_trader.research_infra.builtin_strategies import create_builtin_strategy
from bithumb_coin_trader.research_infra.candidate_freeze import (
    CandidateFreezeError,
    _validate_cost_sensitivity,
    freeze_candidate_experiment,
)
from bithumb_coin_trader.research_infra.candidate_registry import (
    CandidateLifecycle,
    CandidateRegistry,
)
from bithumb_coin_trader.research_infra.costs import SpotCostScenario
from bithumb_coin_trader.research_infra.paper_readiness import _check_candidate
from tests.research_infra.definition_fixtures import definition_record


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha_json(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _scenario(name: str, *, fee: float, slippage: float) -> SpotCostScenario:
    return SpotCostScenario(
        name=name,
        maker_fee_bps=fee / 2.0,
        taker_fee_bps=fee,
        slippage_bps=slippage,
        latency_ms=0.0,
        minimum_order_notional=0.000001,
        tick_size=1.0,
        lot_size=0.00000001,
        partial_fill_probability=0.0,
    )


def _candles(count: int = 730) -> list[Candle]:
    start = datetime(2023, 1, 1, 15, tzinfo=UTC)
    result: list[Candle] = []
    previous = 1_000.0
    for index in range(count):
        close = 1_000.0 * (1.0025**index) * (1.0 + 0.06 * math.sin(index / 23.0))
        result.append(Candle(
            timestamp=start + timedelta(days=index),
            open=previous,
            high=max(previous, close) * 1.001,
            low=min(previous, close) * 0.999,
            close=close,
            volume=1_000.0,
            market="KRW-BTC",
        ))
        previous = close
    return result


def _prepare_selected_experiment(tmp_path: Path) -> tuple[Path, Path, str, Path]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    data_sha256 = "a" * 64
    manifest = {
        "schema_version": 1,
        "dataset_id": "synthetic-daily-development",
        "dataset_role": "DEVELOPMENT_EXPLORATORY",
        "allowed_for_candidate_selection": True,
        "integrity_status": "PASS",
        "provenance_confidence": "PROVEN",
        "data_path": "candles.csv",
        "data_sha256": data_sha256,
        "candle_count": len(_candles()),
    }
    provenance = {
        "dataset_manifest": manifest,
        "dataset_role": manifest["dataset_role"],
        "allowed_for_candidate_selection": manifest["allowed_for_candidate_selection"],
        "integrity_status": manifest["integrity_status"],
        "provenance_confidence": manifest["provenance_confidence"],
        "dataset_manifest_sha256": "b" * 64,
        "dataset_manifest_content_sha256": _sha_json(manifest),
        "data_sha256": data_sha256,
    }
    candidate_spec = BatchExperiment(
        candidate_family="daily_weekly_trend_and_momentum",
        strategy_id="daily_weekly_absolute_momentum_126_63",
        strategy_factory=lambda seed, parameters: create_builtin_strategy(
            "daily_weekly_absolute_momentum_126_63", seed, parameters
        ),
        strategy_config={"fixed_parameters": True},
        feature_config={"input": "completed_KST_daily_candles"},
        parameter_sets=({},),
        seed=31,
        strategy_definition=definition_record("strategy", "daily_weekly_absolute_momentum_126_63"),
        feature_definition=definition_record("feature", "completed_candle_history"),
    )
    controls = (
        BatchExperiment("baseline_controls", "cash", lambda _seed, _params: create_builtin_strategy("cash", 0, {}), {}, {}, ({},), 31,
                        strategy_definition=definition_record("strategy", "cash"),
                        feature_definition=definition_record("feature", "completed_candle_history")),
        BatchExperiment("baseline_controls", "buy_and_hold", lambda _seed, _params: create_builtin_strategy("buy_and_hold", 0, {}), {}, {}, ({},), 31,
                        strategy_definition=definition_record("strategy", "buy_and_hold"),
                        feature_definition=definition_record("feature", "completed_candle_history")),
        BatchExperiment(
            "baseline_controls",
            "randomized_placebo",
            lambda seed, params: create_builtin_strategy("randomized_placebo", seed, params),
            {}, {}, ({"exposure_probability": 0.5, "target_weight": 1.0},), 31,
            strategy_definition=definition_record("strategy", "randomized_placebo"),
            feature_definition=definition_record("feature", "completed_candle_history"),
        ),
    )
    research_root = tmp_path / "research-output"
    report = run_research_batch(
        candles=_candles(),
        dataset_id=manifest["dataset_id"],
        dataset_sha256=data_sha256,
        code_revision=revision,
        dataset_provenance=provenance,
        experiments=(*controls, candidate_spec),
        cost_scenarios=(
            _scenario("base", fee=2.0, slippage=1.0),
            _scenario("conservative", fee=4.0, slippage=2.0),
            _scenario("stress", fee=8.0, slippage=5.0),
            _scenario("extreme", fee=16.0, slippage=10.0),
        ),
        output_dir=research_root,
        n_folds=2,
        window_mode="EXPANDING",
        purge_s=86_400.0,
        embargo_s=86_400.0,
    )
    assert report["status"] == "COMPLETE", report["runs"]
    selected = next(item for item in report["runs"] if item["strategy_id"] == candidate_spec.strategy_id)
    attempt_dir = Path(selected["metrics"]).parent
    registry_path = tmp_path / "candidate-registry.jsonl"
    registry = CandidateRegistry(registry_path)
    candidate_id = selected["experiment_id"]
    registry.create_hypothesis(candidate_id, {
        "hypothesis_id": "H-ABS-MOMENTUM",
        "origin": "internal daily strategy research",
        "economic_intuition": "Trend persistence may justify long-only exposure.",
        "required_data": ["completed daily candles"],
        "feature_definitions": {"momentum": "frozen weekly absolute-momentum rule"},
        "entry_concept": "frozen source implementation",
        "exit_concept": "frozen source implementation",
        "risk_concept": "long-only, target weight at most one",
        "known_confounders": ["small effective sample"],
        "falsification_criteria": ["fails conservative cost tiers"],
    })
    identity = json.loads((attempt_dir / "manifest.json").read_text())["identity"]
    metrics = json.loads((attempt_dir / "metrics.json").read_text())
    first_fold = next(row for row in metrics["folds"] if row["fold_id"] == 0)
    cost_config = identity["cost_config"]
    latency_hash = _sha_json([
        {"name": item["name"], "latency_ms": item["latency_ms"]}
        for item in cost_config
    ])
    registry.transition(candidate_id, CandidateLifecycle.RETROSPECTIVE_EXPERIMENT, {
        "experiment_id": candidate_id,
        "dataset_manifest_sha256": provenance["dataset_manifest_sha256"],
        "result_manifest_sha256": _sha_file(attempt_dir / "complete.json"),
        "code_commit": revision,
        "strategy_config_sha256": _sha_json(identity["strategy_config"]),
        "feature_definition_sha256": _sha_json(identity["feature_config"]),
        "cost_model_sha256": _sha_json(cost_config),
        "latency_assumptions_sha256": latency_hash,
        "provenance_sha256": _sha_json(provenance),
        "metrics_sha256": _sha_file(attempt_dir / "metrics.json"),
        "dataset_roles": ["DEVELOPMENT_EXPLORATORY"],
        "training_range": {
            "start_utc": first_fold["train_start_utc"].replace("+00:00", "Z"),
            "end_utc": first_fold["train_end_utc"].replace("+00:00", "Z"),
        },
        "validation_range": {
            "start_utc": first_fold["validation_start_utc"].replace("+00:00", "Z"),
            "end_utc": first_fold["validation_end_utc"].replace("+00:00", "Z"),
        },
        "random_seed": identity["seed"],
        "deterministic_rerun": "PASS",
        "purge_embargo_seconds": identity["fold_config"]["purge_s"] + identity["fold_config"]["embargo_s"],
    })
    batch_root = attempt_dir.parents[2]
    aggregate = json.loads((batch_root / "aggregate_report.json").read_text())
    candidate_comparisons = [
        row for row in aggregate["baseline_comparisons"]
        if row["candidate_experiment_id"] == candidate_id
    ]
    registry.transition(candidate_id, CandidateLifecycle.ROBUSTNESS_TESTED, {
        "robustness_report_sha256": _sha_file(batch_root / "aggregate_report.json"),
        "baseline_report_sha256": _sha_json([row for row in candidate_comparisons if row["baseline_strategy_id"] != "randomized_placebo"]),
        "placebo_report_sha256": _sha_json([row for row in candidate_comparisons if row["baseline_strategy_id"] == "randomized_placebo"]),
        "walk_forward_status": "PASS",
        "cost_scenarios": [item["name"] for item in cost_config],
    })
    registry.transition(candidate_id, CandidateLifecycle.CANDIDATE, {
        "promotion_report_sha256": "c" * 64,
        "acceptance_rules_sha256": "d" * 64,
        "decision": "PASS",
    })
    return research_root, registry_path, candidate_id, attempt_dir


def test_candidate_freeze_binds_experiment_and_appends_frozen_once(tmp_path: Path) -> None:
    research_root, registry_path, candidate_id, _attempt = _prepare_selected_experiment(tmp_path)
    output_path = research_root / "frozen-candidates" / f"{candidate_id}.json"

    artifact = freeze_candidate_experiment(
        experiment_id=candidate_id,
        candidate_id=candidate_id,
        research_root=research_root,
        candidate_registry_path=registry_path,
        output_path=output_path,
    )
    record = artifact["candidate"]
    assert record["schema_version"] == 2
    assert record["candidate_family"] == "daily_weekly_trend_and_momentum"
    assert record["definition_bindings"]["strategy"]["definition_sha256"]
    assert record["definition_bindings"]["feature"]["definition_sha256"]
    assert [item["name"] for item in record["cost_scenarios"]] == ["base", "conservative", "stress", "extreme"]
    assert artifact["lifecycle_events"][-1]["to_status"] == "FROZEN"
    assert output_path.read_text() == _canonical(artifact) + "\n"
    assert _check_candidate(artifact, None, {"metrics_sha256": record["experiment_metrics_sha256"]})["status"] == "PASS"

    before = registry_events = CandidateRegistry(registry_path).events_for(candidate_id)
    repeated = freeze_candidate_experiment(
        experiment_id=candidate_id,
        candidate_id=candidate_id,
        research_root=research_root,
        candidate_registry_path=registry_path,
        output_path=output_path,
    )
    assert repeated == artifact
    assert CandidateRegistry(registry_path).events_for(candidate_id) == before
    assert [item["to_status"] for item in registry_events][-1] == "FROZEN"


def test_candidate_freeze_rejects_non_monotone_or_incomplete_cost_sensitivity() -> None:
    with pytest.raises(CandidateFreezeError, match="base, conservative, stress, and extreme"):
        _validate_cost_sensitivity([_scenario("base", fee=1, slippage=1).to_dict()])

    with pytest.raises(CandidateFreezeError, match="increase monotonically"):
        _validate_cost_sensitivity([
            _scenario("base", fee=1, slippage=1).to_dict(),
            _scenario("conservative", fee=2, slippage=2).to_dict(),
            _scenario("stress", fee=8, slippage=5).to_dict(),
            _scenario("extreme", fee=4, slippage=3).to_dict(),
        ])


def test_candidate_freeze_cli_fails_closed_before_any_candidate_exists(tmp_path: Path, capsys) -> None:
    research_root = tmp_path / "research-output"
    research_root.mkdir()
    registry = tmp_path / "candidate-registry.jsonl"
    registry.write_text("", encoding="utf-8")
    experiment_id = "exp_" + "a" * 64

    assert cli.main([
        "candidate-freeze",
        "--experiment", experiment_id,
        "--research-root", str(research_root),
        "--candidate-registry", str(registry),
    ]) == 2
    assert "no lifecycle evidence" in capsys.readouterr().err
    assert not list(research_root.rglob("*.json"))


def test_candidate_freeze_does_not_advance_registry_when_output_conflicts(tmp_path: Path) -> None:
    research_root, registry_path, candidate_id, _attempt = _prepare_selected_experiment(tmp_path)
    output_path = research_root / "frozen-candidates" / f"{candidate_id}.json"
    output_path.parent.mkdir()
    output_path.write_text("{}\n", encoding="utf-8")
    before = CandidateRegistry(registry_path).events_for(candidate_id)

    with pytest.raises(CandidateFreezeError, match="already exists"):
        freeze_candidate_experiment(
            experiment_id=candidate_id,
            candidate_id=candidate_id,
            research_root=research_root,
            candidate_registry_path=registry_path,
            output_path=output_path,
        )

    assert CandidateRegistry(registry_path).events_for(candidate_id) == before
