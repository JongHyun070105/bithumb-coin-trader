"""Synthetic checks for the local external expert data preparation lane."""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

import pytest

from scripts.prepare_external_bitmex_dataset import prepare_dataset, validate_source_manifest
from bithumb_coin_trader.experiment_runner import (
    DatasetRole as PartitionRole,
    ExperimentGatingError,
    GovernedExperimentRunner,
)
from bithumb_coin_trader.prospective_dataset import build_and_export_dataset
from bithumb_coin_trader.research_infra.external_expert import (
    group_fills_by_order,
    iter_csv_rows,
    normalize_execution,
    normalize_wallet,
    profile_csv,
    reconstruct_position_transitions,
    verify_public_trade,
    wallet_flow_totals,
)
from bithumb_coin_trader.research_infra.registry import (
    DatasetRegistry,
    DatasetRole,
    DatasetValidationError,
    EXTERNAL_BITMEX_DATASET_ID,
)


EXECUTIONS = ("timestamp,symbol,side,lastPx,lastQty,orderID,trdMatchID,execType,"
              "lastLiquidityInd,execComm,settlCurrency,unexpected\n"
              "2020-01-01T00:00:00Z,XBTUSD,Buy,100,2,o1,t1,Trade,Maker,-1,XBt,keep\n"
              "2020-01-01T00:00:01Z,XBTUSD,Buy,102,3,o1,t2,Trade,Taker,2,XBt,other\n"
              "2020-01-01T00:00:02Z,XBTUSD,Sell,103,5,o2,t3,Trade,Taker,2,XBt,end\n")
WALLET = ("timestamp,transactType,amount,currency,walletBalance,extra\n"
          "2020-01-01T00:00:00Z,Deposit,100,XBt,100,a\n"
          "2020-01-02T00:00:00Z,Withdrawal,-20,XBt,80,b\n"
          "2020-01-03T00:00:00Z,RealisedPNL,5,XBt,85,c\n")


def test_streaming_profile_and_unknown_columns(tmp_path: Path) -> None:
    source = tmp_path / "execution.csv"
    source.write_text(EXECUTIONS + EXECUTIONS.splitlines()[-1] + "\n", encoding="utf-8")
    profile = profile_csv(source)
    assert profile["row_count"] == 4
    assert profile["delimiter"] == ","
    assert profile["null_counts"]["unexpected"] == 0
    assert profile["first_timestamp"] == "2020-01-01T00:00:00+00:00"
    assert profile["last_timestamp"] == "2020-01-01T00:00:02+00:00"
    assert profile["unique_symbols"] == ["XBTUSD"]
    assert profile["adjacent_duplicate_row_count"] == 1
    assert profile["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    rows = list(iter_csv_rows(source))
    assert normalize_execution(rows[0]).raw_fields["unexpected"] == "keep"
    assert normalize_execution(rows[0]).price == Decimal(100)


def test_import_is_ignored_hashed_and_sealed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text(".external-research-data/\n", encoding="utf-8")
    archive = tmp_path / "original.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("execution.csv", EXECUTIONS)
        zipped.writestr("wallet.csv", WALLET)
    manifest = prepare_dataset([archive], repository_root=repo)
    validate_source_manifest(manifest)
    root = repo / ".external-research-data" / EXTERNAL_BITMEX_DATASET_ID
    assert (root / "raw" / archive.name).read_bytes() == archive.read_bytes()
    assert manifest["source_files"][0]["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert sorted(item["row_count"] for item in manifest["source_files"] if item["row_count"] is not None) == [3, 3]
    assert prepare_dataset([archive], repository_root=repo) == manifest
    ignored = subprocess.run(["git", "check-ignore", "-q", str(root / "raw" / "execution.csv")], cwd=repo)
    assert ignored.returncode == 0
    tracked = subprocess.run(["git", "ls-files", "--cached", "--", ".external-research-data"],
                             cwd=repo, capture_output=True, text=True, check=True)
    assert tracked.stdout == ""
    another = tmp_path / "another.csv"
    another.write_text(EXECUTIONS, encoding="utf-8")
    with pytest.raises(ValueError, match="sealed"):
        prepare_dataset([another], repository_root=repo)


def test_import_rejects_zip_traversal(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text(".external-research-data/\n", encoding="utf-8")
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("../escape.csv", EXECUTIONS)
    with pytest.raises(ValueError, match="Unsafe archive member"):
        prepare_dataset([archive], repository_root=repo)
    assert not (repo / "escape.csv").exists()


def test_tape_match_is_record_evidence_only() -> None:
    execution = normalize_execution(next(iter_csv_rows_from_text(EXECUTIONS)))
    public = {
        "trdMatchID": "t1", "timestamp": "2020-01-01T00:00:00Z",
        "symbol": "XBTUSD", "side": "Buy", "price": "100", "size": "2",
    }
    exact = verify_public_trade(execution, public)
    assert exact.classification == "EXACT_PUBLIC_MATCH"
    assert exact.ownership_verified is False
    mismatch = verify_public_trade(execution, {**public, "price": "101"})
    assert mismatch.classification == "PARTIAL_PUBLIC_MATCH"
    assert mismatch.mismatched_fields == ("price",)
    assert verify_public_trade(execution, None).classification == "INSUFFICIENT_PUBLIC_HISTORY"
    assert verify_public_trade(execution, None, history_complete=True).classification == "PUBLIC_RECORD_NOT_FOUND"
    assert verify_public_trade(normalize_execution({"timestamp": "2020-01-01T00:00:00Z"}), public).classification == "SOURCE_FIELD_MISSING"


def iter_csv_rows_from_text(content: str):
    import csv
    import io
    yield from csv.DictReader(io.StringIO(content))


def test_order_partial_fills_and_chronological_positions() -> None:
    fills = [normalize_execution(row) for row in iter_csv_rows_from_text(EXECUTIONS)]
    orders = group_fills_by_order(fills)
    assert orders[0].fill_count == 2
    assert orders[0].quantity == Decimal(5)
    assert orders[0].vwap == Decimal("101.2")
    assert orders[0].liquidity_counts == {"Maker": 1, "Taker": 1}
    assert orders[0].fees_by_currency == {"XBt": Decimal(1)}
    transitions = reconstruct_position_transitions(
        orders, initial_positions={"XBTUSD": Decimal(0)}, quantity_semantics="XBTUSD contract count",
    )
    assert [item.action for item in transitions] == ["OPEN_LONG", "CLOSE"]
    assert [item.quantity_after for item in transitions] == [Decimal(5), Decimal(0)]
    assert all(item.realized_pnl is None for item in transitions)
    with pytest.raises(ValueError, match="chronological"):
        reconstruct_position_transitions(reversed(orders), initial_positions={"XBTUSD": Decimal(0)},
                                         quantity_semantics="XBTUSD contract count")
    with pytest.raises(ValueError, match="Initial position"):
        reconstruct_position_transitions(orders, initial_positions={}, quantity_semantics="contracts")


def test_wallet_flows_are_not_trading_pnl() -> None:
    events = [normalize_wallet(row) for row in iter_csv_rows_from_text(WALLET)]
    assert events[0].raw_fields["extra"] == "a"
    totals = wallet_flow_totals(events)
    assert totals[("XBt", "DEPOSIT")] == Decimal(100)
    assert totals[("XBt", "WITHDRAWAL")] == Decimal(-20)
    assert totals[("XBt", "REALIZED_PNL")] == Decimal(5)
    assert totals[("XBt", "REALIZED_PNL")] != sum(totals.values())


def test_external_data_cannot_become_holdout_or_experiment(tmp_path: Path) -> None:
    registry = DatasetRegistry.load(Path("research-data/dataset_registry.json"))
    external = registry.get(EXTERNAL_BITMEX_DATASET_ID)
    assert external.dataset_role == DatasetRole.EXTERNAL_EXPERT_BEHAVIOR_DATASET
    assert external.source_class == "EXTERNAL_EXPERT_BEHAVIOR_DATASET"
    with pytest.raises(DatasetValidationError):
        registry.require_final_holdout_allowed(EXTERNAL_BITMEX_DATASET_ID)
    with pytest.raises(DatasetValidationError):
        registry.require_candidate_selection_allowed(EXTERNAL_BITMEX_DATASET_ID)
    with pytest.raises(DatasetValidationError):
        registry.update_role(EXTERNAL_BITMEX_DATASET_ID, DatasetRole.FROZEN_HOLDOUT)
    runner = GovernedExperimentRunner(tmp_path / "ledger.json")
    with pytest.raises(ExperimentGatingError, match="hypothesis-generation only"):
        runner.access_dataset(EXTERNAL_BITMEX_DATASET_ID, PartitionRole.HOLDOUT)
    with pytest.raises(ValueError, match="cannot be partitioned"):
        build_and_export_dataset(EXTERNAL_BITMEX_DATASET_ID, tmp_path / "out", [], None)  # type: ignore[arg-type]


def test_metadata_contracts_are_empty_and_no_raw_rows() -> None:
    root = Path("research-data/external") / EXTERNAL_BITMEX_DATASET_ID
    spec = json.loads((root / "dataset-spec.json").read_text(encoding="utf-8"))
    dashboard = json.loads((root / "dashboard-contract.json").read_text(encoding="utf-8"))
    assert spec["primary_use"] == "HYPOTHESIS_GENERATION_ONLY"
    assert "PROSPECTIVE_HOLDOUT" in spec["prohibited_uses"]
    assert json.loads((root / "hypotheses.json").read_text()) == []
    assert dashboard["source_files"] == [] and dashboard["raw_rows_exposed"] is False
