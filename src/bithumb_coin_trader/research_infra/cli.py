"""CLI Entry Points for Microstructure Research Infrastructure.

Reproducible commands for:
    register/list datasets
    inspect dataset
    build canonical derived data
    build DQ catalog
    compute features
    compute labels
    run hypothesis
    run exploratory suite
    run execution simulation
    run chronological evaluation
    generate report
    resume interrupted build
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import cast

from .registry import DatasetRegistry, register_default_datasets
from .dq import (
    DQCatalog,
    CoverageState,
    build_dq_catalog_from_local,
    build_v2_known_missing,
    build_v2_authoritative_dq_catalog,
)
from .hypotheses import HypothesisRegistry, register_default_hypotheses
from .build import CanonicalBuildError, build_canonical_dataset
from .paper_readiness import evaluate_paper_readiness, write_paper_readiness_report
from .batch import BatchExperiment, run_research_batch
from .builtin_strategies import create_builtin_strategy, registered_strategy_ids
from .candidate_freeze import CandidateFreezeError, freeze_candidate_experiment
from .costs import SpotCostScenario
from .paper_start_gate import PaperStartGateError, evaluate_paper_start_gates
from .reliability_seal import ReliabilitySealError, write_reliability_seal
from .definition_registry import (
    DefinitionRegistryError,
    FeatureDefinition,
    StrategyDefinition,
    VersionedDefinitionRegistry,
)
from .research_catalog import (
    HypothesisCatalog,
    default_candidate_families,
    export_candidate_families,
)
from ..data import DataError, load_candles_csv


_RESEARCH_BATCH_ROLE = "DEVELOPMENT_EXPLORATORY"
_HYPOTHESIS_CATALOG_PATH = Path("research-data/hypothesis_catalog.jsonl")
_CANDIDATE_FAMILY_PATH = Path("research-data/candidate_families.json")
_DAILY_CANDIDATE_STRATEGIES = {
    "daily_weekly_absolute_momentum_126_63",
    "daily_weekly_sma_50_200",
    "daily_weekly_donchian_90_30",
    "daily_weekly_dual_momentum_42_168_vol80",
}


def _get_data_root() -> Path:
    return Path("data/microstructure/raw")


def _get_registry_path() -> Path:
    return Path("research-data/dataset_registry.json")


def _get_dq_path(dataset_id: str) -> Path:
    return Path(f"research-data/dq/{dataset_id}_dq_catalog.json")


def _get_artifacts_path() -> Path:
    return Path("research-artifacts")


def cmd_datasets_list(args: argparse.Namespace) -> None:
    """List registered datasets."""
    registry_path = _get_registry_path()
    if registry_path.exists():
        registry = DatasetRegistry.load(registry_path)
    else:
        registry = DatasetRegistry()
        register_default_datasets(registry)

    for ds in registry.list_datasets():
        role_str = ds.dataset_role.value
        exploration = "YES" if ds.allowed_for_exploration else "NO"
        candidate = "YES" if ds.allowed_for_candidate_selection else "NO"
        holdout = "YES" if ds.allowed_for_final_holdout else "NO"
        print(f"{ds.dataset_id:15s} | {role_str:30s} | explore={exploration} "
              f"candidate={candidate} holdout={holdout} | {ds.description[:60]}")


def cmd_datasets_register(args: argparse.Namespace) -> None:
    """Register/save default datasets."""
    registry = DatasetRegistry()
    register_default_datasets(registry)
    path = _get_registry_path()
    registry.save(path)
    print(f"Saved {len(registry.list_datasets())} datasets to {path}")


def cmd_dq_build(args: argparse.Namespace) -> None:
    """Build DQ catalog for a dataset."""
    dataset_id = args.dataset
    data_root = Path(args.data_root) if args.data_root else _get_data_root()

    print(f"Building DQ catalog for {dataset_id}...")

    if dataset_id == "v2":
        # V2 uses the authoritative 2280-slot model, NOT local file counts
        cat = build_v2_authoritative_dq_catalog()
        print("Using V2 authoritative 2280-slot universe (not local file counts)")
    else:
        cat = build_dq_catalog_from_local(data_root, dataset_id)
        print(f"Scanned local files from {data_root}")

    path = _get_dq_path(dataset_id)
    cat.save(path)

    summary = cat.summary(dataset_id)
    print(f"\nDQ Summary for {dataset_id}:")
    print(f"  Total slots: {summary.total_slots}")
    print(f"  DATA_PRESENT: {summary.data_present}")
    print(f"  VERIFIED_ZERO: {summary.verified_zero}")
    print(f"  UNKNOWN_MISSING: {summary.unknown_missing}")
    print(f"  INCOMPLETE: {summary.incomplete}")
    print(f"  CORRUPT: {summary.corrupt}")
    print(f"  Safe slots: {summary.safe_slots} ({summary.coverage_pct:.1%})")
    print(f"  Exclusion count: {summary.exclusion_count}")
    if summary.affected_hours:
        print(f"  Affected hours: {summary.affected_hours[:10]}...")
    print(f"\nSaved to {path}")


def cmd_dq_report(args: argparse.Namespace) -> None:
    """Show DQ report for a dataset."""
    dataset_id = args.dataset
    path = _get_dq_path(dataset_id)
    if not path.exists():
        print(f"DQ catalog not found at {path}. Run 'dq build --dataset {dataset_id}' first.")
        return

    cat = DQCatalog.load(path)
    summary = cat.summary(dataset_id)

    print(f"\n=== DQ Report: {dataset_id} ===")
    print(f"Total slots: {summary.total_slots}")
    print(f"Safe (DATA_PRESENT + VERIFIED_ZERO): {summary.safe_slots} ({summary.coverage_pct:.1%})")
    print(f"UNKNOWN_MISSING: {summary.unknown_missing}")
    print(f"Exclusions required: {summary.exclusion_count}")
    if summary.affected_feeds:
        print(f"\nAffected feeds ({len(summary.affected_feeds)}):")
        for feed in summary.affected_feeds[:20]:
            print(f"  {feed}")


def cmd_hypotheses_list(args: argparse.Namespace) -> None:
    """List registered hypotheses."""
    registry = HypothesisRegistry()
    register_default_hypotheses(registry)

    for h in registry.list_hypotheses():
        print(f"\n{h.hypothesis_id}: {h.description}")
        print(f"  Status: {h.status.value}")
        print(f"  Feeds: {h.required_feeds}")
        print(f"  Features: {h.feature_names}")
        print(f"  Target: {h.target_type} @ {h.target_horizon_s}s")
        print(f"  Expected sign: {h.expected_sign}")
        print(f"  Metric: {h.evaluation_metric}")


def cmd_hypotheses_catalog_seed(args: argparse.Namespace) -> int:
    """Append the source-present hypothesis inventory to a durable ledger."""
    catalog_path = Path(args.path)
    try:
        added = HypothesisCatalog(catalog_path).seed_defaults()
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Added {added} hypotheses; catalog contains {len(HypothesisCatalog(catalog_path).read())}: {catalog_path}")
    return 0


def cmd_hypotheses_catalog_list(args: argparse.Namespace) -> int:
    catalog_path = Path(args.path)
    try:
        events = HypothesisCatalog(catalog_path).read()
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for event in events:
        record = event["record"]
        print(f"{record['hypothesis_id']} | {record['strategy_family']} | {record['research_status']} | {record['role']}")
    print(f"Hypotheses: {len(events)}")
    return 0


def cmd_candidate_families_list(args: argparse.Namespace) -> int:
    for family in default_candidate_families():
        print(f"{family.family_id} | {family.current_status} | retest={family.retest_required}")
    print(f"Candidate families: {len(default_candidate_families())}")
    return 0


def cmd_candidate_families_export(args: argparse.Namespace) -> int:
    destination = Path(args.path)
    try:
        digest = export_candidate_families(destination)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Candidate-family inventory: {destination}")
    print(f"SHA-256: {digest}")
    print(f"Families: {len(default_candidate_families())}")
    return 0


def cmd_candidate_freeze(args: argparse.Namespace) -> int:
    research_root = Path(args.research_root)
    candidate_id = args.candidate_id or args.experiment
    output = (
        Path(args.output)
        if args.output
        else research_root / "frozen-candidates" / f"{candidate_id}.json"
    )
    try:
        record = freeze_candidate_experiment(
            experiment_id=args.experiment,
            candidate_id=candidate_id,
            research_root=research_root,
            candidate_registry_path=Path(args.candidate_registry),
            output_path=output,
        )
    except (CandidateFreezeError, OSError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    candidate = record["candidate"]
    print(f"Candidate: {candidate['candidate_id']}")
    print(f"Experiment: {candidate['experiment_id']}")
    print(f"Freeze hash: {candidate['freeze_hash']}")
    print(f"Artifact: {output}")
    print("PAPER: NOT_STARTED; freeze does not start PAPER")
    return 0


def cmd_paper_start(args: argparse.Namespace) -> int:
    """Readiness-only gate check; never starts a runtime or contacts an exchange."""
    try:
        report = evaluate_paper_start_gates(
            evidence_dir=Path(args.readiness_evidence_dir),
            candidate_freeze=Path(args.candidate_freeze),
            reliability_seal=Path(args.reliability_seal),
        )
    except (PaperStartGateError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for name, check in report["checks"].items():
        print(f"{name}={check['status']}: {check['reason']}")
    print(f"PAPER_START_ALLOWED={report['PAPER_START_ALLOWED']}")
    print("PAPER=NOT_STARTED; this command only validates gates")
    return 0 if report["PAPER_START_ALLOWED"] else 1


def cmd_reliability_seal(args: argparse.Namespace) -> int:
    """Write a new local seal only for a verified terminal auditor PASS."""
    try:
        seal = write_reliability_seal(Path(args.terminal_audit), Path(args.output))
    except (ReliabilitySealError, OSError, ValueError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"RELIABILITY_SEALED={seal['seal_sha256']}")
    print(f"TERMINAL_AUDIT_SHA256={seal['terminal_audit_sha256']}")
    print("This command seals local evidence only; it does not start PAPER or contact AWS.")
    return 0


def cmd_hypothesis_run(args: argparse.Namespace) -> None:
    """Run a single hypothesis evaluation.

    This is a placeholder that demonstrates the pipeline structure.
    Full execution requires the complete event processing pipeline.
    """
    hypothesis_id = args.hypothesis
    dataset_id = args.dataset

    # Load registry
    registry_path = _get_registry_path()
    if registry_path.exists():
        registry = DatasetRegistry.load(registry_path)
    else:
        registry = DatasetRegistry()
        register_default_datasets(registry)

    # Check dataset access
    try:
        ds = registry.require_exploration_allowed(dataset_id)
    except Exception as e:
        print(f"ERROR: {e}")
        return

    # Load hypothesis
    hyp_registry = HypothesisRegistry()
    register_default_hypotheses(hyp_registry)
    hyp = hyp_registry.get(hypothesis_id)

    print(f"Running {hypothesis_id}: {hyp.description}")
    print(f"Dataset: {dataset_id} (role={ds.dataset_role.value})")
    print(f"Features: {hyp.feature_names}")
    print(f"Target: {hyp.target_type} @ {hyp.target_horizon_s}s")
    print(f"\nFull pipeline execution requires data processing.")
    print(f"Use 'research build --dataset {dataset_id}' to prepare canonical data first.")


def cmd_build(args: argparse.Namespace) -> int:
    """Build canonical derived data for a dataset."""
    dataset_id = args.dataset
    data_root = Path(args.data_root) if args.data_root else _get_data_root()

    # Load registry
    registry_path = _get_registry_path()
    if registry_path.exists():
        registry = DatasetRegistry.load(registry_path)
    else:
        registry = DatasetRegistry()
        register_default_datasets(registry)

    try:
        ds = registry.require_exploration_allowed(dataset_id)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir) if args.output_dir else _get_artifacts_path() / "canonical" / dataset_id
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False
        )
        git_commit = revision.stdout.strip() if revision.returncode == 0 else "unknown"
        manifest = build_canonical_dataset(
            ds,
            data_root=data_root,
            output_dir=output_dir,
            git_commit=git_commit,
        )
    except (CanonicalBuildError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Canonical events: {manifest['event_count']:,}")
    print(f"Dataset output: {output_dir / manifest['events_file']}")
    print(f"Build manifest: {output_dir / 'manifest.json'}")
    print(f"Build status: {manifest['build_status']} (DQ remains NOT_RUN)")
    return 0


def cmd_paper_readiness(args: argparse.Namespace) -> int:
    """Audit local evidence for prospective paper eligibility; never starts PAPER."""
    evidence_dir = Path(args.evidence_dir)
    output_dir = Path(args.output_dir)
    report = evaluate_paper_readiness(evidence_dir)
    try:
        write_paper_readiness_report(report, output_dir, evidence_dir)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for name, check in report["checks"].items():
        print(f"{name}={check['status']}: {check['reason']}")
    print(f"PAPER_ELIGIBLE={report['PAPER_ELIGIBLE']}")
    print(f"Report: {output_dir}")
    if report["PAPER_ELIGIBLE"]:
        return 0
    if any(check["status"] == "FAIL" for check in report["checks"].values()):
        return 1
    return 2


def cmd_research_batch(args: argparse.Namespace) -> int:
    """Run the bounded local walk-forward batch; never accesses network data."""
    try:
        if not args.walk_forward:
            raise ValueError("the research-batch CLI currently requires explicit --walk-forward")
        dataset_path = Path(args.dataset_manifest).resolve(strict=True)
        specification_path = Path(args.hypotheses).resolve(strict=True)
        dataset_manifest = _load_json_object(dataset_path)
        experiment_spec = _load_json_object(specification_path)
        _validate_research_dataset_manifest(dataset_manifest)

        candle_root = dataset_path.parent.resolve()
        candle_path = (candle_root / cast(str, dataset_manifest["data_path"])).resolve(strict=True)
        try:
            candle_path.relative_to(candle_root)
        except ValueError as exc:
            raise ValueError("dataset data_path must resolve beneath its manifest directory") from exc
        candle_bytes = candle_path.read_bytes()
        dataset_sha256 = hashlib.sha256(candle_bytes).hexdigest()
        if dataset_sha256 != dataset_manifest["data_sha256"]:
            raise ValueError("dataset candle file SHA-256 does not match its manifest")
        candles = load_candles_csv(candle_path)
        if len(candles) != cast(int, dataset_manifest["candle_count"]):
            raise ValueError("dataset candle count does not match its manifest")

        scenarios = _load_cost_grid(experiment_spec, args.cost_grid)
        definitions = _load_batch_experiments(experiment_spec)
        output_dir = Path(args.output).resolve()
        code_revision = _require_clean_code_revision()
        provenance = {
            "dataset_manifest": dataset_manifest,
            "dataset_role": dataset_manifest["dataset_role"],
            "allowed_for_candidate_selection": dataset_manifest["allowed_for_candidate_selection"],
            "integrity_status": dataset_manifest["integrity_status"],
            "provenance_confidence": dataset_manifest["provenance_confidence"],
            "dataset_manifest_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
            "dataset_manifest_content_sha256": hashlib.sha256(
                json.dumps(dataset_manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            ).hexdigest(),
            "data_sha256": dataset_sha256,
        }
        report = run_research_batch(
            candles=candles,
            dataset_id=cast(str, dataset_manifest["dataset_id"]),
            dataset_sha256=dataset_sha256,
            code_revision=code_revision,
            dataset_provenance=provenance,
            experiments=_register_experiment_definitions(
                definitions,
                VersionedDefinitionRegistry(
                    Path(args.definition_registry).resolve()
                    if args.definition_registry
                    else Path(args.output).resolve() / "definition-registry.jsonl"
                ),
            ),
            cost_scenarios=scenarios,
            output_dir=output_dir,
            n_folds=args.folds,
            window_mode=args.window_mode,
            purge_s=args.purge_seconds,
            embargo_s=args.embargo_seconds,
            max_experiments=args.max_experiments,
            max_fold_cost_runs=args.max_fold_cost_runs,
            retry_failed=args.retry_failed,
        )
    except (OSError, DataError, DefinitionRegistryError, TypeError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    batch_path = output_dir / "batches" / report["batch_id"]
    print(f"Batch: {report['batch_id']}")
    print(f"Status: {report['status']}")
    print(f"Experiments: {report['completed_count']}/{report['experiment_count']}")
    print(f"Evidence: {batch_path}")
    if report["status"] == "NEEDS_RETRY":
        print("A prior failed attempt requires an explicit retry with --retry-failed", file=sys.stderr)
        return 2
    return 0 if report["status"] == "COMPLETE" else 1


def _load_json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _validate_research_dataset_manifest(value: dict[str, object]) -> None:
    required = {
        "schema_version", "dataset_id", "dataset_role", "allowed_for_candidate_selection",
        "integrity_status", "provenance_confidence", "data_path", "data_sha256", "candle_count",
    }
    if set(value) != required:
        raise ValueError("dataset manifest fields must be exactly: " + ", ".join(sorted(required)))
    if value["schema_version"] != 1:
        raise ValueError("unsupported dataset manifest schema_version")
    if not isinstance(value["dataset_id"], str) or not value["dataset_id"].strip():
        raise ValueError("dataset_id must be non-empty")
    if value["dataset_role"] != _RESEARCH_BATCH_ROLE:
        raise ValueError("research-batch only accepts DEVELOPMENT_EXPLORATORY datasets")
    if value["allowed_for_candidate_selection"] is not True:
        raise ValueError("dataset is not authorized for candidate selection")
    if value["integrity_status"] != "PASS" or value["provenance_confidence"] != "PROVEN":
        raise ValueError("dataset provenance and integrity must be PASS/PROVEN")
    if not isinstance(value["data_path"], str) or not value["data_path"].strip():
        raise ValueError("data_path must be non-empty")
    digest = value["data_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(
        char not in "0123456789abcdefABCDEF" for char in digest
    ):
        raise ValueError("data_sha256 must be a SHA-256 hex digest")
    count = value["candle_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("candle_count must be a positive integer")


def _load_cost_grid(spec: dict[str, object], name: str) -> tuple[SpotCostScenario, ...]:
    grids = spec.get("cost_grids")
    if not isinstance(grids, dict) or name not in grids:
        raise ValueError(f"hypothesis file must declare cost_grids[{name!r}]")
    raw = grids[name]
    if not isinstance(raw, list) or not raw:
        raise ValueError("selected cost grid must be a non-empty array of explicit scenarios")
    scenarios = tuple(SpotCostScenario.from_dict(item) for item in raw if isinstance(item, dict))
    if len(scenarios) != len(raw):
        raise ValueError("every cost scenario must be a JSON object")
    if len(scenarios) < 2:
        raise ValueError("cost sensitivity requires at least two distinct scenarios")
    if len({scenario.name for scenario in scenarios}) != len(scenarios):
        raise ValueError("cost scenario names must be unique")
    assumption_signatures = {
        json.dumps(
            {key: value for key, value in scenario.to_dict().items() if key != "name"},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        for scenario in scenarios
    }
    if len(assumption_signatures) < 2:
        raise ValueError("cost sensitivity scenarios must contain at least two distinct assumption sets")
    return scenarios


def _load_batch_experiments(spec: dict[str, object]) -> tuple[BatchExperiment, ...]:
    if spec.get("schema_version") != 1:
        raise ValueError("unsupported hypotheses specification schema_version")
    raw_experiments = spec.get("experiments")
    if not isinstance(raw_experiments, list) or not raw_experiments:
        raise ValueError("hypothesis file must contain a non-empty experiments array")
    experiments: list[BatchExperiment] = []
    candidate_families = {family.family_id for family in default_candidate_families()}
    for index, raw in enumerate(raw_experiments):
        if not isinstance(raw, dict):
            raise ValueError(f"experiments[{index}] must be an object")
        required = {
            "candidate_family", "strategy_id", "strategy_config", "feature_config",
            "parameter_sets", "seed",
        }
        if set(raw) != required:
            raise ValueError(f"experiments[{index}] fields must be exactly: {', '.join(sorted(required))}")
        strategy_id = raw["strategy_id"]
        family = raw["candidate_family"]
        if not isinstance(strategy_id, str) or strategy_id not in registered_strategy_ids():
            raise ValueError(
                f"experiments[{index}] strategy_id must be one of: {', '.join(registered_strategy_ids())}"
            )
        if not isinstance(family, str) or not family.strip():
            raise ValueError(f"experiments[{index}].candidate_family must be non-empty")
        if family not in candidate_families:
            raise ValueError(
                f"experiments[{index}].candidate_family must be registered; "
                f"known families: {', '.join(sorted(candidate_families))}"
            )
        if strategy_id in {"cash", "buy_and_hold", "randomized_placebo"}:
            if family != "baseline_controls":
                raise ValueError(f"baseline strategy {strategy_id!r} must use candidate_family='baseline_controls'")
        else:
            expected_family = (
                "daily_weekly_trend_and_momentum"
                if strategy_id in _DAILY_CANDIDATE_STRATEGIES
                else "builtin_sma_trend_example"
            )
            if family != expected_family:
                raise ValueError(
                    f"strategy {strategy_id!r} belongs to candidate_family={expected_family!r}, "
                    f"not {family!r}"
                )
        if strategy_id not in {"cash", "buy_and_hold", "randomized_placebo", "sma_trend"} | _DAILY_CANDIDATE_STRATEGIES:
            raise ValueError(
                f"strategy {strategy_id!r} has no governed execution adapter for family {family!r}"
            )
        strategy_config = raw["strategy_config"]
        feature_config = raw["feature_config"]
        parameter_sets = raw["parameter_sets"]
        seed = raw["seed"]
        if not isinstance(strategy_config, dict) or not isinstance(feature_config, dict):
            raise ValueError(f"experiments[{index}] configs must be JSON objects")
        if not isinstance(parameter_sets, list) or not parameter_sets or any(
            not isinstance(item, dict) for item in parameter_sets
        ):
            raise ValueError(f"experiments[{index}].parameter_sets must be non-empty JSON objects")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError(f"experiments[{index}].seed must be a non-negative integer")
        experiments.append(BatchExperiment(
            candidate_family=family,
            strategy_id=strategy_id,
            strategy_factory=lambda current_seed, parameters, name=strategy_id: create_builtin_strategy(
                name, current_seed, parameters
            ),
            strategy_config=strategy_config,
            feature_config=feature_config,
            parameter_sets=tuple(parameter_sets),
            seed=seed,
        ))
    strategy_ids = {experiment.strategy_id for experiment in experiments}
    if any(strategy_id not in {"cash", "buy_and_hold", "randomized_placebo"} for strategy_id in strategy_ids):
        missing_baselines = {"cash", "buy_and_hold", "randomized_placebo"} - strategy_ids
        if missing_baselines:
            raise ValueError(
                "candidate experiments require explicit cash, buy_and_hold, and randomized_placebo controls; missing: "
                + ", ".join(sorted(missing_baselines))
            )
    return tuple(experiments)


def _register_experiment_definitions(
    experiments: tuple[BatchExperiment, ...], registry: VersionedDefinitionRegistry
) -> tuple[BatchExperiment, ...]:
    package_root = Path(__file__).resolve().parents[1]
    feature_source_paths = (
        package_root / "models.py",
        package_root / "research_infra" / "walk_forward_runner.py",
    )
    feature = registry.register(FeatureDefinition(
        definition_id="completed_candle_history",
        version="1.0.0",
        implementation_sha256=_definition_sources_sha256(feature_source_paths),
        config_schema={"type": "object"},
        description="Causal completed OHLCV candle history passed to a train-only strategy.",
    ))

    registered: list[BatchExperiment] = []
    for experiment in experiments:
        strategy_paths = [package_root / "research_infra" / "builtin_strategies.py"]
        if experiment.strategy_id in _DAILY_CANDIDATE_STRATEGIES:
            strategy_paths.append(package_root / "daily_strategy_candidates.py")
        strategy = registry.register(StrategyDefinition(
            definition_id=experiment.strategy_id,
            version="1.0.0",
            implementation_sha256=_definition_sources_sha256(tuple(strategy_paths)),
            config_schema={"type": "object"},
            description=f"Governed local target-weight adapter for {experiment.strategy_id}.",
        ))
        registered.append(replace(
            experiment,
            strategy_definition=strategy,
            feature_definition=feature,
        ))
    return tuple(registered)


def _definition_sources_sha256(paths: tuple[Path, ...]) -> str:
    package_root = Path(__file__).resolve().parents[1]
    source_hashes: dict[str, str] = {}
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file():
            raise DefinitionRegistryError(f"definition implementation source is missing or a symlink: {path}")
        try:
            relative = path.relative_to(package_root.parent.parent).as_posix()
        except ValueError as exc:
            raise DefinitionRegistryError("definition implementation source escapes the repository") from exc
        source_hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    encoded = json.dumps(source_hashes, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_clean_code_revision() -> str:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False
    )
    if revision.returncode != 0 or not revision.stdout.strip():
        raise ValueError("research-batch must run inside a Git checkout")
    tracked = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    source_untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", "src", "pyproject.toml", "setup.cfg"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if tracked.returncode != 0 or source_untracked.returncode != 0:
        raise ValueError("unable to verify repository source state")
    if tracked.stdout.strip() or source_untracked.stdout.strip():
        raise ValueError(
            "research-batch requires committed source code so the experiment is bound to an immutable revision"
        )
    return revision.stdout.strip()


def cmd_report_generate(args: argparse.Namespace) -> None:
    """Generate a human-readable research report."""
    print("=== Microstructure Research Infrastructure Report ===")
    print()

    # Dataset registry
    registry_path = _get_registry_path()
    if registry_path.exists():
        registry = DatasetRegistry.load(registry_path)
        print("## Datasets")
        for ds in registry.list_datasets():
            print(f"  {ds.dataset_id}: {ds.dataset_role.value}")
        print()

    # Hypotheses
    hyp_registry = HypothesisRegistry()
    register_default_hypotheses(hyp_registry)
    print("## Hypotheses")
    for h in hyp_registry.list_hypotheses():
        print(f"  {h.hypothesis_id}: {h.description} [{h.status.value}]")
    print()

    # DQ summaries
    print("## DQ Status")
    for dataset_id in ["fresh45", "old72h", "v2", "v4"]:
        path = _get_dq_path(dataset_id)
        if path.exists():
            cat = DQCatalog.load(path)
            summary = cat.summary(dataset_id)
            print(f"  {dataset_id}: {summary.safe_slots}/{summary.total_slots} safe "
                  f"({summary.coverage_pct:.1%})")
        else:
            print(f"  {dataset_id}: DQ catalog not built")
    print()

    print("## Scientific State")
    print("  ALPHA: UNPROVEN")
    print("  PAPER: NOT STARTED")
    print("  LIVE: DISABLED")
    print("  PRIVATE API: DISABLED")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research",
        description="Microstructure Research Infrastructure CLI",
    )
    sub = parser.add_subparsers(dest="command")

    # datasets
    ds = sub.add_parser("datasets", help="Dataset management")
    ds_sub = ds.add_subparsers(dest="subcommand")
    ds_sub.add_parser("list", help="List datasets")
    ds_sub.add_parser("register", help="Register default datasets")

    # dq
    dq = sub.add_parser("dq", help="Data quality")
    dq_sub = dq.add_subparsers(dest="subcommand")
    dq_build = dq_sub.add_parser("build", help="Build DQ catalog")
    dq_build.add_argument("--dataset", required=True)
    dq_build.add_argument("--data-root", default=None)
    dq_report = dq_sub.add_parser("report", help="DQ report")
    dq_report.add_argument("--dataset", required=True)

    # hypotheses
    hyp = sub.add_parser("hypotheses", help="Hypothesis management")
    hyp_sub = hyp.add_subparsers(dest="subcommand")
    hyp_sub.add_parser("list", help="List hypotheses")
    hyp_catalog_seed = hyp_sub.add_parser("catalog-seed", help="Append source-present hypotheses to the persistent ledger")
    hyp_catalog_seed.add_argument("--path", default=str(_HYPOTHESIS_CATALOG_PATH))
    hyp_catalog_list = hyp_sub.add_parser("catalog-list", help="Verify and list the persistent hypothesis ledger")
    hyp_catalog_list.add_argument("--path", default=str(_HYPOTHESIS_CATALOG_PATH))
    hyp_run = hyp_sub.add_parser("run", help="Run hypothesis")
    hyp_run.add_argument("--hypothesis", required=True)
    hyp_run.add_argument("--dataset", required=True)

    families = sub.add_parser("candidate-families", help="Candidate-family source inventory; no promotion")
    family_sub = families.add_subparsers(dest="subcommand")
    family_sub.add_parser("list", help="List inventoried strategy families")
    family_export = family_sub.add_parser("export", help="Write the immutable candidate-family snapshot")
    family_export.add_argument("--path", default=str(_CANDIDATE_FAMILY_PATH))

    candidate_freeze = sub.add_parser(
        "candidate-freeze",
        help="Freeze an already selected, fully evidenced experiment; does not start PAPER",
    )
    candidate_freeze.add_argument("--experiment", required=True)
    candidate_freeze.add_argument("--candidate-id", default=None)
    candidate_freeze.add_argument("--research-root", default="research-artifacts")
    candidate_freeze.add_argument("--candidate-registry", default="research-data/candidate_registry.jsonl")
    candidate_freeze.add_argument("--output", default=None)

    paper_start = sub.add_parser(
        "paper-start",
        help="Read-only fail-closed gate check; this command never starts PAPER",
    )
    paper_start.add_argument("--candidate-freeze", required=True)
    paper_start.add_argument("--readiness-evidence-dir", required=True)
    paper_start.add_argument("--reliability-seal", required=True)

    reliability_seal = sub.add_parser(
        "reliability-seal",
        help="Write a new local hash-bound seal from a terminal-audit PASS",
    )
    reliability_seal.add_argument("--terminal-audit", required=True)
    reliability_seal.add_argument("--output", required=True)

    # build
    build = sub.add_parser("build", help="Build canonical data")
    build.add_argument("--dataset", required=True)
    build.add_argument("--data-root", default=None)
    build.add_argument("--output-dir", default=None)

    paper_readiness = sub.add_parser(
        "paper-readiness",
        help="Verify a local evidence bundle for PAPER eligibility; does not start PAPER",
    )
    paper_readiness.add_argument("--evidence-dir", required=True)
    paper_readiness.add_argument("--output-dir", required=True)

    batch = sub.add_parser(
        "research-batch",
        help="Run a bounded, resumable, train-only walk-forward research batch",
    )
    batch.add_argument("--dataset-manifest", required=True)
    batch.add_argument("--hypotheses", required=True)
    batch.add_argument("--cost-grid", default="conservative")
    batch.add_argument("--walk-forward", action="store_true")
    batch.add_argument("--folds", type=int, default=5)
    batch.add_argument("--window-mode", choices=("ROLLING", "EXPANDING"), default="EXPANDING")
    batch.add_argument("--purge-seconds", type=float, required=True)
    batch.add_argument("--embargo-seconds", type=float, required=True)
    batch.add_argument("--max-experiments", type=int, default=100)
    batch.add_argument("--max-fold-cost-runs", type=int, default=2_000)
    batch.add_argument("--retry-failed", action="store_true")
    batch.add_argument("--output", required=True)
    batch.add_argument(
        "--definition-registry",
        default=None,
        help="Append-only definition registry (defaults to OUTPUT/definition-registry.jsonl)",
    )

    # report
    sub.add_parser("report", help="Generate research report")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    commands = {
        ("datasets", "list"): cmd_datasets_list,
        ("datasets", "register"): cmd_datasets_register,
        ("dq", "build"): cmd_dq_build,
        ("dq", "report"): cmd_dq_report,
        ("hypotheses", "list"): cmd_hypotheses_list,
        ("hypotheses", "catalog-seed"): cmd_hypotheses_catalog_seed,
        ("hypotheses", "catalog-list"): cmd_hypotheses_catalog_list,
        ("hypotheses", "run"): cmd_hypothesis_run,
        ("candidate-families", "list"): cmd_candidate_families_list,
        ("candidate-families", "export"): cmd_candidate_families_export,
        ("candidate-freeze", None): cmd_candidate_freeze,
        ("paper-start", None): cmd_paper_start,
        ("reliability-seal", None): cmd_reliability_seal,
        ("build", None): cmd_build,
        ("paper-readiness", None): cmd_paper_readiness,
        ("research-batch", None): cmd_research_batch,
        ("report", None): cmd_report_generate,
    }

    subcmd = getattr(args, "subcommand", None)
    handler = commands.get((args.command, subcmd))
    if handler is None:
        # Try without subcommand
        handler = commands.get((args.command, None))

    if handler:
        result = handler(args)
        return result if isinstance(result, int) else 0
    else:
        parser.print_help()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
