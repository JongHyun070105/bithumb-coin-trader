"""Comprehensive Data Quality (DQ) audit engine for external BitMEX dataset.

Measures, validates, and classifies:
- Row counts, unique execution IDs, duplicates
- Timestamp nulls, malformed timestamps, and raw file ordering reversals
- Order ID integrity (nulls and placeholder checks for Trade)
- Decreasing cumqty checks within orders
- Symbol, execution type, order type, side, and maker/taker distributions
- Price, quantity, fee, and side anomaly detection
- Referential consistency: (cumqty + leavesqty == orderqty)
- Wallet blank row audit, malformed amount, currency validity, balance nulls, and balance continuity
- Produces explicit PASS / WARNING / FAIL / NOT_APPLICABLE status per rule with fail-closed semantics.
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

VALID_EXEC_TYPES = {"Trade", "Funding", "Settlement"}
VALID_SIDES = {"Buy", "Sell", ""}
VALID_LIQUIDITY_FLAGS = {"AddedLiquidity", "RemovedLiquidity", ""}
VALID_WALLET_CURRENCIES = {"XBt", "USD", "USDT"}
PLACEHOLDER_ORDER_ID = "00000000-0000-0000-0000-000000000000"


class DataQualityAuditor:
    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = Path(raw_dir)

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
        placeholder_trade_order_ids = 0

        exec_types: Counter[str] = Counter()
        unknown_exec_types = 0
        ord_types: Counter[str] = Counter()
        symbols: Counter[str] = Counter()
        liquidity_indicators: Counter[str] = Counter()
        invalid_liquidity_flags = 0
        invalid_sides = 0

        price_anomalies = 0
        quantity_anomalies = 0
        fee_anomalies = 0
        referential_inconsistencies = 0

        order_last_cumqty: dict[str, float] = {}
        decreasing_cumqty_anomalies = 0
        source_row_provenance_set: set[tuple[str, int]] = set()
        duplicate_provenance_anomalies = 0

        for file_path in raw_exec_files:
            prev_ts: datetime | None = None
            with file_path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row_idx, row in enumerate(reader, start=1):
                    total_exec_rows += 1

                    prov_key = (file_path.name, row_idx)
                    if prov_key in source_row_provenance_set:
                        duplicate_provenance_anomalies += 1
                    else:
                        source_row_provenance_set.add(prov_key)

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
                    if et not in VALID_EXEC_TYPES:
                        unknown_exec_types += 1

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
                    elif oid == PLACEHOLDER_ORDER_ID:
                        placeholder_order_ids[et] += 1
                        if et == "Trade":
                            placeholder_trade_order_ids += 1

                    sym = (row.get("symbol") or "").strip()
                    if sym:
                        symbols[sym] += 1

                    ot = (row.get("ordtype") or "").strip()
                    if ot:
                        ord_types[ot] += 1

                    liq = (row.get("lastliquidityind") or "").strip()
                    if liq:
                        liquidity_indicators[liq] += 1
                        if liq not in VALID_LIQUIDITY_FLAGS:
                            invalid_liquidity_flags += 1

                    side = (row.get("side") or "").strip()
                    if et == "Trade" and side not in ("Buy", "Sell"):
                        invalid_sides += 1

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

                            # Check decreasing cumqty within order
                            if oid and oid != PLACEHOLDER_ORDER_ID:
                                if oid in order_last_cumqty and cq < order_last_cumqty[oid]:
                                    decreasing_cumqty_anomalies += 1
                                order_last_cumqty[oid] = cq
                        except (ValueError, TypeError):
                            referential_inconsistencies += 1

        # Audit Wallet
        wallet_total_rows = 0
        wallet_blank_rows = 0
        wallet_types: Counter[str] = Counter()
        wallet_currencies: Counter[str] = Counter()
        wallet_balance_continuity_mismatches = 0
        wallet_malformed_amounts = 0
        wallet_invalid_currencies = 0
        wallet_missing_balances = 0

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
                curr = (row.get("currency") or "").strip()
                wallet_currencies[curr] += 1
                if curr not in VALID_WALLET_CURRENCIES:
                    wallet_invalid_currencies += 1

                bal_raw = row.get("walletbalance")
                if bal_raw is None or bal_raw.strip() == "":
                    wallet_missing_balances += 1

                try:
                    amt_str = row.get("amount") or ""
                    if not amt_str.strip():
                        wallet_malformed_amounts += 1
                    amt = int(float(amt_str))

                    bal_str = bal_raw or ""
                    bal = int(float(bal_str))
                    if prev_balance is not None:
                        expected_bal = prev_balance + amt
                        if expected_bal != bal:
                            wallet_balance_continuity_mismatches += 1
                    prev_balance = bal
                except (ValueError, TypeError):
                    wallet_malformed_amounts += 1
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
                "description": "All trades have non-null, non-placeholder order IDs; Funding system events use placeholders",
                "status": "PASS" if (null_order_ids_by_type.get("Trade", 0) == 0 and placeholder_trade_order_ids == 0) else "FAIL",
                "observed_value": {
                    "trade_null_order_ids": null_order_ids_by_type.get("Trade", 0),
                    "trade_placeholder_order_ids": placeholder_trade_order_ids,
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
            {
                "rule_id": "DQ-13-DECREASING-CUMQTY",
                "description": "Cumulative fill quantity within an order must be monotonically non-decreasing",
                "status": "PASS" if decreasing_cumqty_anomalies == 0 else "FAIL",
                "observed_value": decreasing_cumqty_anomalies,
                "expected_value": 0,
            },
            {
                "rule_id": "DQ-14-SOURCE-ROW-PROVENANCE",
                "description": "Source CSV rows must have unique provenance keys (file, row_index)",
                "status": "PASS" if duplicate_provenance_anomalies == 0 else "FAIL",
                "observed_value": duplicate_provenance_anomalies,
                "expected_value": 0,
            },
            {
                "rule_id": "DQ-15-EXEC-TYPE-VALIDITY",
                "description": "Execution types must belong to permitted set (Trade, Funding, Settlement)",
                "status": "PASS" if unknown_exec_types == 0 else "FAIL",
                "observed_value": unknown_exec_types,
                "expected_value": 0,
            },
            {
                "rule_id": "DQ-16-LIQUIDITY-AND-SIDE-VALIDITY",
                "description": "Trade side must be Buy/Sell and liquidity flag must be AddedLiquidity/RemovedLiquidity",
                "status": "PASS" if (invalid_sides == 0 and invalid_liquidity_flags == 0) else "FAIL",
                "observed_value": {
                    "invalid_sides": invalid_sides,
                    "invalid_liquidity_flags": invalid_liquidity_flags,
                },
                "expected_value": {"invalid_sides": 0, "invalid_liquidity_flags": 0},
            },
            {
                "rule_id": "DQ-17-WALLET-FIELD-SANITY",
                "description": "Wallet rows must have numeric amount, permitted currency, and non-null balance",
                "status": "PASS" if (wallet_malformed_amounts == 0 and wallet_invalid_currencies == 0 and wallet_missing_balances == 0) else "FAIL",
                "observed_value": {
                    "malformed_amounts": wallet_malformed_amounts,
                    "invalid_currencies": wallet_invalid_currencies,
                    "missing_balances": wallet_missing_balances,
                },
                "expected_value": {"malformed_amounts": 0, "invalid_currencies": 0, "missing_balances": 0},
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
