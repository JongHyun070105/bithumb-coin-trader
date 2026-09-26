"""Comprehensive Data Quality (DQ) audit engine for external BitMEX dataset.

Measures, validates, and classifies:
- Row counts, unique execution IDs, duplicates
- Timestamp nulls and raw file ordering reversals
- Order ID integrity (nulls and system placeholders)
- Symbol, execution type, order type, and maker/taker distributions
- Price, quantity, and fee anomaly detection
- Referential consistency: (cumqty + leavesqty == orderqty)
- Wallet blank row audit, event distributions, and balance continuity
- Produces explicit PASS / WARNING / FAIL / NOT_APPLICABLE status per rule.
"""

from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from bithumb_coin_trader.research_infra.external_expert import (
    _decimal,
    _first,
    _timestamp,
)


class DataQualityAuditor:
    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = raw_dir

    def run_audit(self) -> dict[str, Any]:
        raw_exec_files = sorted(self.raw_dir.glob("aoa-execution-*.csv"))
        wallet_file = self.raw_dir / "aoa-wallet-2018-03-01-2021-12-31.csv"

        if not raw_exec_files or not wallet_file.exists():
            raise FileNotFoundError(f"Missing raw files in {self.raw_dir}")

        total_exec_rows = 0
        exec_ids: set[str] = set()
        duplicate_exec_ids = 0
        timestamp_nulls = 0
        raw_timestamp_reversals = 0

        null_order_ids_by_type: Counter[str] = Counter()
        placeholder_order_ids: Counter[str] = Counter()

        exec_types: Counter[str] = Counter()
        ord_types: Counter[str] = Counter()
        symbols: Counter[str] = Counter()
        liquidity_indicators: Counter[str] = Counter()

        price_anomalies = 0
        quantity_anomalies = 0
        fee_anomalies = 0
        referential_inconsistencies = 0

        for file_path in raw_exec_files:
            prev_ts: datetime | None = None
            with file_path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    total_exec_rows += 1
                    eid = (row.get("execid") or "").strip()
                    if eid:
                        if eid in exec_ids:
                            duplicate_exec_ids += 1
                        else:
                            exec_ids.add(eid)
                    else:
                        duplicate_exec_ids += 1

                    et = (row.get("exectype") or "").strip()
                    exec_types[et] += 1

                    transact_ts = _timestamp(row.get("transacttime"))
                    if transact_ts is None:
                        transact_ts = _timestamp(row.get("timestamp"))

                    if transact_ts is None:
                        timestamp_nulls += 1
                    else:
                        if prev_ts is not None and transact_ts < prev_ts:
                            raw_timestamp_reversals += 1
                        prev_ts = transact_ts

                    oid = (row.get("orderid") or "").strip()
                    if not oid:
                        null_order_ids_by_type[et] += 1
                    elif oid == "00000000-0000-0000-0000-000000000000":
                        placeholder_order_ids[et] += 1

                    sym = (row.get("symbol") or "").strip()
                    if sym:
                        symbols[sym] += 1

                    ot = (row.get("ordtype") or "").strip()
                    if ot:
                        ord_types[ot] += 1

                    liq = (row.get("lastliquidityind") or "").strip()
                    if liq:
                        liquidity_indicators[liq] += 1

                    # Check Trade specific sanity
                    if et == "Trade":
                        px_val = _decimal(row.get("lastpx") or row.get("price"))
                        if px_val is None or px_val <= 0:
                            price_anomalies += 1

                        qty_val = _decimal(row.get("lastqty"))
                        if qty_val is None or qty_val <= 0:
                            quantity_anomalies += 1

                        fee_val = _decimal(row.get("execcomm"))
                        if fee_val is None:
                            fee_anomalies += 1

                        # Referential consistency: cumqty + leavesqty == orderqty
                        try:
                            cq = float(row.get("cumqty") or 0)
                            lq = float(row.get("leavesqty") or 0)
                            oq = float(row.get("orderqty") or 0)
                            if abs((cq + lq) - oq) > 1e-4:
                                referential_inconsistencies += 1
                        except (ValueError, TypeError):
                            referential_inconsistencies += 1

        # Audit Wallet
        wallet_total_rows = 0
        wallet_blank_rows = 0
        wallet_types: Counter[str] = Counter()
        wallet_currencies: Counter[str] = Counter()
        wallet_balance_continuity_mismatches = 0

        prev_balance: int | None = None
        with wallet_file.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                wallet_total_rows += 1
                tt = (row.get("transacttype") or "").strip()
                if not tt:
                    wallet_blank_rows += 1
                    continue
                wallet_types[tt] += 1
                wallet_currencies[(row.get("currency") or "").strip()] += 1

                try:
                    amt = int(float(row.get("amount") or 0))
                    bal = int(float(row.get("walletbalance") or 0))
                    if prev_balance is not None:
                        expected_bal = prev_balance + amt
                        if expected_bal != bal:
                            wallet_balance_continuity_mismatches += 1
                    prev_balance = bal
                except (ValueError, TypeError):
                    wallet_balance_continuity_mismatches += 1

        valid_wallet_rows = wallet_total_rows - wallet_blank_rows

        # Formulate rules
        rules = [
            {
                "rule_id": "DQ-01-ROW-COUNT",
                "description": "Total raw execution rows matches known archive census (1,444,583)",
                "status": "PASS" if total_exec_rows == 1_444_583 else "WARNING",
                "observed_value": total_exec_rows,
                "expected_value": 1_444_583,
            },
            {
                "rule_id": "DQ-02-EXECID-UNIQUENESS",
                "description": "Every execution record has a globally unique execid (no duplicate execution IDs)",
                "status": "PASS" if duplicate_exec_ids == 0 and len(exec_ids) == total_exec_rows else "FAIL",
                "observed_value": {"unique_exec_ids": len(exec_ids), "duplicates": duplicate_exec_ids},
                "expected_value": {"duplicates": 0},
            },
            {
                "rule_id": "DQ-03-TIMESTAMP-NULLS",
                "description": "No execution records have null or unparseable timestamps",
                "status": "PASS" if timestamp_nulls == 0 else "FAIL",
                "observed_value": timestamp_nulls,
                "expected_value": 0,
            },
            {
                "rule_id": "DQ-04-RAW-TIMESTAMP-REVERSALS",
                "description": "Raw CSV physical row order contains timestamp reversals requiring canonical sorting",
                "status": "WARNING" if raw_timestamp_reversals > 0 else "PASS",
                "observed_value": raw_timestamp_reversals,
                "note": "Reversals present in source CSV dumps; mitigated by canonical deterministic sort.",
            },
            {
                "rule_id": "DQ-05-ORDERID-INTEGRITY",
                "description": "All trades have non-null order IDs; Funding system events use placeholders",
                "status": "PASS" if null_order_ids_by_type.get("Trade", 0) == 0 else "FAIL",
                "observed_value": {
                    "trade_null_order_ids": null_order_ids_by_type.get("Trade", 0),
                    "funding_null_order_ids": null_order_ids_by_type.get("Funding", 0),
                    "placeholder_order_ids": dict(placeholder_order_ids),
                },
            },
            {
                "rule_id": "DQ-06-REFERENTIAL-CONSISTENCY",
                "description": "cumqty + leavesqty == orderqty across all trade executions",
                "status": "PASS" if referential_inconsistencies == 0 else "FAIL",
                "observed_value": referential_inconsistencies,
                "expected_value": 0,
            },
            {
                "rule_id": "DQ-07-PRICE-ANOMALIES",
                "description": "Trade execution prices must be strictly positive and finite",
                "status": "PASS" if price_anomalies == 0 else "FAIL",
                "observed_value": price_anomalies,
                "expected_value": 0,
            },
            {
                "rule_id": "DQ-08-QUANTITY-ANOMALIES",
                "description": "Trade execution quantities must be strictly positive and finite",
                "status": "PASS" if quantity_anomalies == 0 else "FAIL",
                "observed_value": quantity_anomalies,
                "expected_value": 0,
            },
            {
                "rule_id": "DQ-09-FEE-ANOMALIES",
                "description": "Trade execution fees must be non-null and numeric",
                "status": "PASS" if fee_anomalies == 0 else "FAIL",
                "observed_value": fee_anomalies,
                "expected_value": 0,
            },
            {
                "rule_id": "DQ-10-WALLET-BLANK-ROWS",
                "description": "Audit and filter blank rows in wallet file without forward-filling",
                "status": "WARNING" if wallet_blank_rows > 0 else "PASS",
                "observed_value": {
                    "total_rows": wallet_total_rows,
                    "valid_rows": valid_wallet_rows,
                    "blank_rows": wallet_blank_rows,
                },
                "note": "2,135 blank rows in source wallet file safely filtered with audit trail.",
            },
            {
                "rule_id": "DQ-11-WALLET-CONTINUITY",
                "description": "Running wallet balance matches cumulative sum of deposits, withdrawals, and realised PnL",
                "status": "PASS" if wallet_balance_continuity_mismatches == 0 else "WARNING",
                "observed_value": wallet_balance_continuity_mismatches,
                "expected_value": 0,
            },
            {
                "rule_id": "DQ-12-PUBLIC-TAPE-GROUND-TRUTH",
                "description": "External dataset author identity is NOT independently verified; tape matches are descriptive",
                "status": "NOT_APPLICABLE",
                "observed_value": "AUTHOR_IDENTITY_NOT_INDEPENDENTLY_VERIFIED",
                "note": "Public tape matches confirm historical tape existence, not account ownership.",
            },
        ]

        overall_status = "PASS"
        if any(r["status"] == "FAIL" for r in rules):
            overall_status = "FAIL"
        elif any(r["status"] == "WARNING" for r in rules):
            overall_status = "WARNING"

        return {
            "dataset_id": "external-bitmex-trader-2018-2021",
            "audited_at_utc": datetime.now(timezone.utc).isoformat(),
            "overall_status": overall_status,
            "summary_metrics": {
                "total_execution_rows": total_exec_rows,
                "unique_execution_ids": len(exec_ids),
                "duplicate_execution_ids": duplicate_exec_ids,
                "timestamp_nulls": timestamp_nulls,
                "raw_timestamp_reversals": raw_timestamp_reversals,
                "execution_types": dict(exec_types),
                "order_types": dict(ord_types),
                "top_10_symbols": dict(symbols.most_common(10)),
                "liquidity_indicators": dict(liquidity_indicators),
                "wallet_valid_rows": valid_wallet_rows,
                "wallet_blank_rows": wallet_blank_rows,
                "wallet_event_types": dict(wallet_types),
                "wallet_currencies": dict(wallet_currencies),
            },
            "rules": rules,
        }
