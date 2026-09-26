"""Tests for the deterministic clean rebuild and logical manifest reproducibility harness."""

import io
import json
from pathlib import Path
import tempfile
import zipfile

import pytest

from bithumb_coin_trader.research_infra.reproducibility import (
    clean_rebuild_from_zip,
    compare_rebuild_manifests,
    compute_dataset_logical_manifest,
    compute_table_hashes,
)

SYNTHETIC_EXECUTION = (
    "date,execid,orderid,clordid,clordlinkid,account,symbol,side,lastqty,lastpx,lastliquidityind,"
    "orderqty,price,displayqty,stoppx,pegoffsetvalue,pegpricetype,currency,settlcurrency,exectype,"
    "ordtype,timeinforce,execinst,contingencytype,ordstatus,triggered,workingindicator,ordrejreason,"
    "leavesqty,cumqty,avgpx,commission,tradepublishindicator,text,trdmatchid,execcost,execcomm,"
    "homenotional,foreignnotional,transacttime,timestamp\n"
    "2020-01-01,e1,o1,,,acc,XBTUSD,Buy,1000,10000,AddedLiquidity,1000,10000,,,,USD,XBt,Trade,"
    "Limit,GoodTillCancel,,,Filled,,true,,0,1000,10000,-0.00025,PublishTrade,text,m1,10000000,-2500,"
    "0.1,1000,2020-01-01 00:00:00,2020-01-01 00:00:00\n"
)

SYNTHETIC_WALLET = (
    "date,transactid,account,currency,amount,transactstatus,address,network,text,timestamp,transacttime,transacttype,tx,walletbalance\n"
    "2020-01-01,w1,acc,XBt,100000000,Completed,,,,00:00.0,00:00.0,Deposit,-,100000000\n"
)


def create_synthetic_zip(zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("aoa-execution-2020-01-01-2020-12-31.csv", SYNTHETIC_EXECUTION)
        zf.writestr("aoa-wallet-2018-03-01-2021-12-31.csv", SYNTHETIC_WALLET)
        zf.writestr("90일 서한.txt", "메타데이터 테스트 서한")


def test_reproducibility_synthetic_rebuild(tmp_path: Path) -> None:
    zip_path = tmp_path / "test_archive.zip"
    create_synthetic_zip(zip_path)

    run1_dir = tmp_path / "rebuild_run1"
    run2_dir = tmp_path / "rebuild_run2"

    run1 = clean_rebuild_from_zip(zip_path, run1_dir, max_reconstruct_sample=100)
    run2 = clean_rebuild_from_zip(zip_path, run2_dir, max_reconstruct_sample=100)

    comparison = compare_rebuild_manifests(run1, run2)
    assert comparison["deterministic"] is True
    assert comparison["all_table_logical_hashes_match"] is True
    assert comparison["canonical_counts_match"] is True
    assert comparison["dq_status_match"] is True
    assert comparison["reconstruction_match"] is True


def test_compare_rebuild_manifests_detects_mismatch() -> None:
    run1 = {
        "canonical_counts": {"total": 10},
        "dq_status": "PASS",
        "order_reconstruction": {"orders": 5},
        "logical_manifest": {
            "tables": {
                "execution": {
                    "row_count": 10,
                    "logical_content_hash": "hash_a",
                    "byte_hash": "byte_a",
                    "schema": "s1",
                }
            }
        },
    }
    run2 = {
        "canonical_counts": {"total": 10},
        "dq_status": "PASS",
        "order_reconstruction": {"orders": 5},
        "logical_manifest": {
            "tables": {
                "execution": {
                    "row_count": 10,
                    "logical_content_hash": "hash_b",  # Tampered hash
                    "byte_hash": "byte_b",
                    "schema": "s1",
                }
            }
        },
    }
    comp = compare_rebuild_manifests(run1, run2)
    assert comp["deterministic"] is False
    assert comp["all_table_logical_hashes_match"] is False
