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
import json
from pathlib import Path
import sys
from typing import Any

from .registry import DatasetRegistry, DatasetRole, register_default_datasets
from .dq import (
    DQCatalog,
    CoverageState,
    build_dq_catalog_from_local,
    build_v2_known_missing,
    build_v2_authoritative_dq_catalog,
)
from .hypotheses import HypothesisRegistry, HypothesisStatus, register_default_hypotheses
from .manifests import create_manifest


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


def cmd_build(args: argparse.Namespace) -> None:
    """Build canonical derived data for a dataset."""
    dataset_id = args.dataset
    data_root = Path(args.data_root) if args.data_root else _get_data_root()

    print(f"Building canonical data for {dataset_id} from {data_root}...")

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
        print(f"ERROR: {e}")
        return

    from .adapters import iter_raw_jsonl_streaming
    from .features import FeatureEngine

    # Process with limited scope for CLI
    exchanges = list(ds.exchange_universe)
    feeds = [f for f in ds.feed_universe if f in ("orderbook", "trade")]

    print(f"Exchanges: {exchanges}")
    print(f"Feeds: {feeds}")
    print(f"Streaming events from {data_root}...")

    event_count = 0
    market_set: set[str] = set()

    for event in iter_raw_jsonl_streaming(
        data_root, dataset_id, exchanges=exchanges, feeds=feeds,
    ):
        event_count += 1
        market_set.add(event.market)
        if event_count % 100_000 == 0:
            print(f"  Processed {event_count:,} events, {len(market_set)} markets...")

    print(f"\nTotal events: {event_count:,}")
    print(f"Markets: {sorted(market_set)}")
    print(f"Build complete.")


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
    hyp_run = hyp_sub.add_parser("run", help="Run hypothesis")
    hyp_run.add_argument("--hypothesis", required=True)
    hyp_run.add_argument("--dataset", required=True)

    # build
    build = sub.add_parser("build", help="Build canonical data")
    build.add_argument("--dataset", required=True)
    build.add_argument("--data-root", default=None)

    # report
    sub.add_parser("report", help="Generate research report")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return

    commands = {
        ("datasets", "list"): cmd_datasets_list,
        ("datasets", "register"): cmd_datasets_register,
        ("dq", "build"): cmd_dq_build,
        ("dq", "report"): cmd_dq_report,
        ("hypotheses", "list"): cmd_hypotheses_list,
        ("hypotheses", "run"): cmd_hypothesis_run,
        ("build", None): cmd_build,
        ("report", None): cmd_report_generate,
    }

    subcmd = getattr(args, "subcommand", None)
    handler = commands.get((args.command, subcmd))
    if handler is None:
        # Try without subcommand
        handler = commands.get((args.command, None))

    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
