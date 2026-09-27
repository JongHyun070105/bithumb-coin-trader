"""Tests for the independent second implementation verifier."""

import csv
from decimal import Decimal
import io
import json
from pathlib import Path

import pytest

from bithumb_coin_trader.research_infra.independent_verifier import (
    IndependentVerifier,
    MetricComparison,
    format_markdown_table,
)

HEADER = [
    "date", "execid", "orderid", "clordid", "clordlinkid", "account", "symbol", "side",
    "lastqty", "lastpx", "lastliquidityind", "orderqty", "price", "displayqty", "stoppx",
    "pegoffsetvalue", "pegpricetype", "currency", "settlcurrency", "exectype", "ordtype",
    "timeinforce", "execinst", "contingencytype", "ordstatus", "triggered", "workingindicator",
    "ordrejreason", "leavesqty", "cumqty", "avgpx", "commission", "tradepublishindicator",
    "text", "trdmatchid", "execcost", "execcomm", "homenotional", "foreignnotional",
    "transacttime", "timestamp"
]

def make_exec_csv(rows: list[dict[str, str]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=HEADER)
    writer.writeheader()
    for r in rows:
        row_dict = {h: "" for h in HEADER}
        row_dict.update(r)
        writer.writerow(row_dict)
    return out.getvalue()


SYNTHETIC_WALLET = (
    "date,transactid,account,currency,amount,transactstatus,address,network,text,timestamp,transacttime,transacttype,tx,walletbalance\n"
    "2020-01-01,w1,acc,XBt,100000000,Completed,,,,00:00.0,00:00.0,Deposit,-,100000000\n"
    "2020-01-02,w2,acc,XBt,50000000,Completed,XBTUSD,,,00:00.0,00:00.0,RealisedPNL,-,150000000\n"
    "2020-01-03,w3,acc,XBt,-20000000,Canceled,addr,,,00:00.0,00:00.0,Withdrawal,-,150000000\n"
    "2020-01-04,w4,acc,XBt,-20000000,Completed,addr,,,00:00.0,00:00.0,Withdrawal,-,130000000\n"
    ",,,,,,,,,,,,,\n"
)


def test_independent_verifier_synthetic(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    raw_exec_rows = [
        {
            "date": "2020-01-01", "execid": "e1", "orderid": "o1", "symbol": "XBTUSD",
            "side": "Buy", "lastqty": "1000", "lastpx": "10000", "lastliquidityind": "AddedLiquidity",
            "orderqty": "1000", "price": "10000", "currency": "USD", "settlcurrency": "XBt",
            "exectype": "Trade", "ordtype": "Limit", "ordstatus": "Filled", "transacttime": "2020-01-01 00:00:00",
            "timestamp": "2020-01-01 00:00:00",
        },
        {
            "date": "2020-01-01", "execid": "e2", "orderid": "o2", "symbol": "XBTUSD",
            "side": "Sell", "lastqty": "2000", "lastpx": "10000", "lastliquidityind": "RemovedLiquidity",
            "orderqty": "2000", "price": "10000", "currency": "USD", "settlcurrency": "XBt",
            "exectype": "Trade", "ordtype": "Market", "ordstatus": "Filled", "transacttime": "2020-01-01 01:00:00",
            "timestamp": "2020-01-01 01:00:00",
        },
        {
            "date": "2020-01-01", "execid": "e3", "orderid": "00000000-0000-0000-0000-000000000000",
            "symbol": "XBTUSD", "currency": "USD", "settlcurrency": "XBt", "exectype": "Funding",
            "transacttime": "2020-01-01 08:00:00", "timestamp": "2020-01-01 08:00:00",
        },
        {
            "date": "2020-01-01", "execid": "e4", "orderid": "00000000-0000-0000-0000-000000000000",
            "symbol": "XBTH20", "lastqty": "100", "lastpx": "9000", "currency": "USD",
            "settlcurrency": "XBt", "exectype": "Settlement", "transacttime": "2020-01-01 12:00:00",
            "timestamp": "2020-01-01 12:00:00",
        },
    ]

    (raw_dir / "aoa-execution-2020-01-01.csv").write_text(make_exec_csv(raw_exec_rows), encoding="utf-8")
    (raw_dir / "aoa-wallet-2018-03-01-2021-12-31.csv").write_text(SYNTHETIC_WALLET, encoding="utf-8")

    verifier = IndependentVerifier(raw_dir)
    primary_summary = {
        "total_execution_rows": 4,
        "trade_rows": 2,
        "funding_rows": 1,
        "settlement_rows": 1,
        "unique_execid": 4,
        "unique_non_placeholder_orderid": 2,
        "maker_count": 1,
        "taker_count": 1,
        "wallet_valid_rows": 4,
        "wallet_deposit_total_satoshi": 100000000,
        "wallet_withdrawal_total_satoshi": -40000000,  # Erroneous Phase 1 sum including canceled
        "wallet_realised_pnl_total_satoshi": 50000000,
        "final_wallet_balance_satoshi": 110000000,  # Erroneous column sum
    }

    results = verifier.verify_all(primary_summary=primary_summary)
    assert results["all_discrepancies_explained"] is True

    comp_map = {c["metric"]: c for c in results["comparisons"]}
    assert comp_map["total_execution_rows"]["verdict"] == "MATCH"
    assert comp_map["trade_rows"]["verdict"] == "MATCH"
    assert comp_map["funding_rows"]["verdict"] == "MATCH"
    assert comp_map["settlement_rows"]["verdict"] == "MATCH"
    assert comp_map["unique_execid"]["verdict"] == "MATCH"
    assert comp_map["unique_non_placeholder_orderid"]["verdict"] == "MATCH"
    assert comp_map["maker_count"]["verdict"] == "MATCH"
    assert comp_map["taker_count"]["verdict"] == "MATCH"

    # Wallet checks
    wallet_audit = results["wallet_audit"]
    assert wallet_audit["total_valid_rows"] == 4
    assert wallet_audit["completed_rows"] == 3
    assert wallet_audit["canceled_rows"] == 1
    assert wallet_audit["deposit_total_satoshi"] == 100000000
    assert wallet_audit["withdrawal_completed_satoshi"] == -20000000
    assert wallet_audit["withdrawal_canceled_satoshi"] == -20000000
    assert wallet_audit["realised_pnl_total_satoshi"] == 50000000
    assert wallet_audit["final_reported_balance_satoshi"] == 130000000
    assert wallet_audit["reconciliation_exact"] is True

    # Withdrawal discrepancy is flagged as DISCREPANCY_EXPLAINED
    assert comp_map["wallet_withdrawal_total_satoshi"]["verdict"] == "DISCREPANCY_EXPLAINED"
    assert comp_map["final_wallet_balance_satoshi"]["verdict"] == "DISCREPANCY_EXPLAINED"

    # Markdown format check
    table = format_markdown_table(results["comparisons"])
    assert "| `total_execution_rows` |" in table
    assert "| `final_wallet_balance_satoshi` |" in table
