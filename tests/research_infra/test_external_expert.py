"""Synthetic checks for the local external expert data preparation lane."""

import csv
from datetime import datetime, timezone
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
    ExecutionRow,
    WalletEvent,
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


def test_canonical_ingestion_and_parquet_partitioning(tmp_path: Path) -> None:
    from bithumb_coin_trader.research_infra.external_canonical import CanonicalIngestor
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True)

    import io
    rows = [
        {
            "date": "2020-01-01", "execid": "e1", "orderid": "o1", "symbol": "XBTUSD", "side": "Buy",
            "lastqty": "100", "lastpx": "8000", "lastliquidityind": "AddedLiquidity", "orderqty": "100",
            "price": "8000", "exectype": "Trade", "ordtype": "Limit", "ordstatus": "Filled",
            "leavesqty": "0", "cumqty": "100", "avgpx": "8000", "execcomm": "-0.002", "settlcurrency": "XBt",
            "execcost": "100", "homenotional": "1", "foreignnotional": "100",
            "transacttime": "2020-01-01 10:00:00.000000", "timestamp": "2020-01-01 10:00:00.000000",
        },
        {
            "date": "2020-01-01", "execid": "e2", "orderid": "00000000-0000-0000-0000-000000000000",
            "symbol": "XBTUSD", "side": "", "lastqty": "", "lastpx": "", "lastliquidityind": "AddedLiquidity",
            "orderqty": "", "price": "0", "exectype": "Funding", "ordtype": "", "ordstatus": "",
            "leavesqty": "", "cumqty": "0", "avgpx": "0", "execcomm": "0.001", "settlcurrency": "XBt",
            "execcost": "0", "homenotional": "0", "foreignnotional": "0",
            "transacttime": "2020-01-01 12:00:00.000000", "timestamp": "2020-01-01 12:00:00.000000",
        },
        {
            "date": "2020-01-01", "execid": "e3", "orderid": "o2", "symbol": "XBTH20", "side": "Buy",
            "lastqty": "50", "lastpx": "8100", "lastliquidityind": "RemovedLiquidity", "orderqty": "50",
            "price": "8100", "exectype": "Settlement", "ordtype": "Market", "ordstatus": "Filled",
            "leavesqty": "0", "cumqty": "50", "avgpx": "8100", "execcomm": "0.001", "settlcurrency": "XBt",
            "execcost": "50", "homenotional": "1", "foreignnotional": "50",
            "transacttime": "2020-01-01 14:00:00.000000", "timestamp": "2020-01-01 14:00:00.000000",
        },
    ]
    sio = io.StringIO()
    w = csv.DictWriter(sio, fieldnames=list(rows[0].keys()))
    w.writeheader()
    for r in rows:
        w.writerow(r)
    (raw_dir / "aoa-execution-2020-01-01-2020-12-31.csv").write_text(sio.getvalue(), encoding="utf-8")

    wallet_content = (
        "date,transactid,account,currency,amount,transactstatus,address,network,text,timestamp,transacttime,transacttype,tx,walletbalance\n"
        "2020-01-01,w1,aoa,XBt,1000000,Completed,,,,00:00.0,00:00.0,Deposit,-,1000000\n"
        ",,,,,,,,,,,,\n"
        "2020-01-02,w2,aoa,XBt,50000,Completed,XBTUSD,,,00:00.5,00:00.0,RealisedPNL,-,1050000\n"
    )
    (raw_dir / "aoa-wallet-2018-03-01-2021-12-31.csv").write_text(wallet_content, encoding="utf-8")

    ingestor = CanonicalIngestor(tmp_path)
    manifest = ingestor.ingest_all(batch_size=10)

    assert manifest["canonical_counts"]["execution_trades"] == 1
    assert manifest["canonical_counts"]["funding_events"] == 1
    assert manifest["canonical_counts"]["settlement_events"] == 1
    assert manifest["canonical_counts"]["wallet_events"] == 2
    assert manifest["canonical_counts"]["wallet_blank_rows"] == 1
    assert (tmp_path / "derived" / "parquet" / "execution").exists()


def test_order_and_position_and_cycle_reconstruction() -> None:
    from bithumb_coin_trader.research_infra.external_reconstruction import (
        CycleReconstructor,
        OrderReconstructor,
        PositionReconstructor,
    )

    t1 = datetime(2020, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2020, 1, 1, 10, 1, 0, tzinfo=timezone.utc)
    t3 = datetime(2020, 1, 1, 11, 0, 0, tzinfo=timezone.utc)

    # 1 buy order filled in 2 parts, then 1 sell order to close
    f1 = ExecutionRow(
        timestamp=t1, symbol="XBTUSD", side="Buy", price=Decimal(8000), size=Decimal(50),
        order_id="ord-1", trade_match_id="tm-1", execution_id="ex-1", execution_type="Trade",
        order_type="Limit", liquidity="AddedLiquidity", fee=Decimal("-0.01"), fee_currency="XBt",
        raw_fields={"orderqty": "100", "ordstatus": "PartiallyFilled"},
    )
    f2 = ExecutionRow(
        timestamp=t2, symbol="XBTUSD", side="Buy", price=Decimal(8000), size=Decimal(50),
        order_id="ord-1", trade_match_id="tm-2", execution_id="ex-2", execution_type="Trade",
        order_type="Limit", liquidity="AddedLiquidity", fee=Decimal("-0.01"), fee_currency="XBt",
        raw_fields={"orderqty": "100", "ordstatus": "Filled"},
    )
    f3 = ExecutionRow(
        timestamp=t3, symbol="XBTUSD", side="Sell", price=Decimal(8200), size=Decimal(100),
        order_id="ord-2", trade_match_id="tm-3", execution_id="ex-3", execution_type="Trade",
        order_type="Limit", liquidity="RemovedLiquidity", fee=Decimal("0.05"), fee_currency="XBt",
        raw_fields={"orderqty": "100", "ordstatus": "Filled"},
    )

    fills = [f1, f2, f3]
    orders = OrderReconstructor.reconstruct_orders(fills)
    assert len(orders) == 2
    assert orders[0].order_id == "ord-1"
    assert orders[0].execution_count == 2
    assert orders[0].filled_quantity == Decimal(100)
    assert orders[0].reconstruction_confidence == "RECONSTRUCTED"
    assert "EXECUTION_ONLY_VISIBILITY" in orders[0].limitations

    positions = PositionReconstructor.reconstruct_positions(fills, initial_positions={"XBTUSD": Decimal(0)})
    assert len(positions) == 3
    assert positions[0].estimated_position_after == Decimal(50)
    assert positions[1].estimated_position_after == Decimal(100)
    assert positions[2].estimated_position_after == Decimal(0)

    cycles = CycleReconstructor.extract_cycles(positions)
    assert len(cycles) == 1
    assert cycles[0].direction == "LONG"
    assert cycles[0].gross_execution_pnl_estimate > 0
    assert cycles[0].confidence == "RECONSTRUCTED"


def test_behavior_features_and_regimes() -> None:
    from bithumb_coin_trader.research_infra.external_behavior import (
        BehaviorEvolutionAnalyzer,
        BehaviorFeatureExtractor,
        RegimeConditioningInterface,
    )

    t1 = datetime(2018, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2018, 5, 1, 13, 0, 0, tzinfo=timezone.utc)
    f1 = ExecutionRow(
        timestamp=t1, symbol="XBTUSD", side="Buy", price=Decimal(6000), size=Decimal(10),
        order_id="o1", trade_match_id="m1", execution_id="e1", execution_type="Trade",
        order_type="Limit", liquidity="AddedLiquidity", fee=Decimal("-0.001"), fee_currency="XBt",
        raw_fields={},
    )
    f2 = ExecutionRow(
        timestamp=t2, symbol="XBTUSD", side="Sell", price=Decimal(6100), size=Decimal(10),
        order_id="o2", trade_match_id="m2", execution_id="e2", execution_type="Trade",
        order_type="Limit", liquidity="RemovedLiquidity", fee=Decimal("0.005"), fee_currency="XBt",
        raw_fields={},
    )

    daily = BehaviorFeatureExtractor.extract_daily_features([f1, f2])
    assert len(daily) == 1
    assert daily[0].trade_count == 2
    assert daily[0].maker_ratio == 0.5
    assert daily[0].taker_ratio == 0.5
    assert daily[0].symbol_mix == {"XBTUSD": 1.0}

    phases = BehaviorEvolutionAnalyzer.identify_behavioral_phases(daily)
    assert len(phases) >= 1
    assert phases[0]["phase_id"] == "PHASE_1_BEAR_MARKET_XBT_SCALING"

    schema = RegimeConditioningInterface.get_interface_schema()
    assert "no_lookahead_contract" in schema
    valid, err = RegimeConditioningInterface.validate_market_context_record({
        "timestamp_bucket_utc": "2020-01-01T00:00:00Z",
        "symbol": "XBTUSD",
        "realized_volatility_1h": 0.02,
        "trend_strength_adx": 25.0,
        "range_chop_index": 50.0,
        "market_volume_1h": 1000.0,
        "bid_ask_spread_bps": 1.5,
        "orderbook_depth_top10": 500000.0,
        "funding_rate_8h": 0.0001,
        "perp_spot_basis_bps": 5.0,
        "liquidation_volume_1h": 0.0,
        "cross_exchange_divergence_bps": 2.0,
    })
    assert valid is True
    assert err is None


def test_context_document_parser(tmp_path: Path) -> None:
    from bithumb_coin_trader.research_infra.external_context import parse_context_document
    doc_path = tmp_path / "90일 서한.txt"
    doc_path.write_text("90일 서한\n비트코인 적정가 85k 전망...", encoding="utf-8")
    parsed = parse_context_document(doc_path)
    assert parsed.document_name == "90일 서한.txt"
    assert parsed.temporal_relationship == "NON_CONTEMPORANEOUS_POST_HOC"
    assert parsed.permissible_role == "QUALITATIVE_SELF_DESCRIBED_CONTEXT_ONLY"
    assert parsed.prohibited_role == "NO_QUANTITATIVE_GROUND_TRUTH_NO_TRADE_LABELING"
