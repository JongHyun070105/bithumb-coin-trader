"""Recompute reported BitMEX dataset claims from raw CSV using stdlib only.

This intentionally does not import the production ingestion, verification,
contract, wallet, or behavioral-analysis modules.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

SATOSHI = Decimal("100000000")


def audit_raw_dataset(raw_dir: Path) -> dict[str, Any]:
    rows_by_type: Counter[str] = Counter()
    trades_by_symbol: Counter[str] = Counter()
    contracts_by_symbol: dict[str, Decimal] = defaultdict(Decimal)
    year_liquidity: dict[str, Counter[str]] = defaultdict(Counter)
    month_liquidity: dict[str, Counter[str]] = defaultdict(Counter)
    year_symbol_liquidity: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    exec_commission_sat: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    orders: dict[str, dict[str, Any]] = {}
    first_trade_date: str | None = None
    last_trade_date: str | None = None

    execution_files = sorted(Path(raw_dir).glob("aoa-execution-*.csv"))
    if not execution_files:
        raise FileNotFoundError(f"No execution CSV files under {raw_dir}")

    for path in execution_files:
        with path.open("r", newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                event_type = (row.get("exectype") or "").strip()
                rows_by_type[event_type] += 1
                liquidity = (row.get("lastliquidityind") or "").strip()
                if row.get("execcomm"):
                    exec_commission_sat[(event_type, liquidity)] += Decimal(row["execcomm"])

                if event_type != "Trade":
                    continue

                symbol = (row.get("symbol") or "").strip()
                trade_date = (row.get("date") or "")[:10]
                year = trade_date[:4]
                quantity = abs(Decimal(row.get("lastqty") or "0"))
                trades_by_symbol[symbol] += 1
                contracts_by_symbol[symbol] += quantity
                year_liquidity[year][liquidity] += 1
                month_liquidity[trade_date[:7]][liquidity] += 1
                year_symbol_liquidity[(year, symbol)][liquidity] += 1
                first_trade_date = min(first_trade_date, trade_date) if first_trade_date else trade_date
                last_trade_date = max(last_trade_date, trade_date) if last_trade_date else trade_date

                order_id = (row.get("orderid") or "").strip()
                if not order_id:
                    continue
                order = orders.setdefault(
                    order_id,
                    {
                        "symbol": symbol,
                        "first_date": trade_date,
                        "fill_count": 0,
                        "quantity": Decimal(0),
                        "maker_count": 0,
                        "taker_count": 0,
                    },
                )
                order["first_date"] = min(order["first_date"], trade_date)
                order["fill_count"] += 1
                order["quantity"] += quantity
                order["maker_count"] += liquidity == "AddedLiquidity"
                order["taker_count"] += liquidity == "RemovedLiquidity"

    wallet_path = Path(raw_dir) / "aoa-wallet-2018-03-01-2021-12-31.csv"
    if not wallet_path.exists():
        raise FileNotFoundError(wallet_path)
    wallet_totals: dict[str, Decimal] = defaultdict(Decimal)
    wallet_status_counts: Counter[tuple[str, str]] = Counter()
    canceled_withdrawals: list[Decimal] = []
    valid_wallet_rows = 0
    last_wallet_balance_sat: Decimal | None = None
    with wallet_path.open("r", newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            event_type = (row.get("transacttype") or "").strip()
            if not event_type:
                continue
            valid_wallet_rows += 1
            status = (row.get("transactstatus") or "").strip()
            wallet_status_counts[(event_type, status)] += 1
            amount = Decimal(row["amount"])
            last_wallet_balance_sat = Decimal(row["walletbalance"])
            if status == "Completed" and event_type in {"Deposit", "Withdrawal", "RealisedPNL"}:
                wallet_totals[event_type] += amount
            if event_type == "Withdrawal" and status == "Canceled":
                canceled_withdrawals.append(amount)

    if last_wallet_balance_sat is None:
        raise ValueError("Wallet CSV has no valid event rows")

    year_ratios: dict[str, dict[str, Any]] = {}
    for year, counts in sorted(year_liquidity.items()):
        maker = counts["AddedLiquidity"]
        taker = counts["RemovedLiquidity"]
        known = maker + taker
        year_ratios[year] = {
            "maker_fills": maker,
            "taker_fills": taker,
            "fills_with_known_liquidity": known,
            "maker_ratio": maker / known if known else None,
            "unknown_liquidity_fills": counts[""] + sum(
                n for flag, n in counts.items() if flag not in {"", "AddedLiquidity", "RemovedLiquidity"}
            ),
        }

    month_ratios: dict[str, dict[str, Any]] = {}
    for month, counts in sorted(month_liquidity.items()):
        maker = counts["AddedLiquidity"]
        taker = counts["RemovedLiquidity"]
        known = maker + taker
        month_ratios[month] = {
            "maker_fills": maker,
            "taker_fills": taker,
            "fills_with_known_liquidity": known,
            "maker_ratio": maker / known if known else None,
            "unknown_liquidity_fills": counts[""] + sum(
                n for flag, n in counts.items() if flag not in {"", "AddedLiquidity", "RemovedLiquidity"}
            ),
        }

    composition: dict[str, dict[str, dict[str, float | int]]] = {}
    for year in ("2020", "2021"):
        category_counts: dict[str, Counter[str]] = defaultdict(Counter)
        for (row_year, symbol), counts in year_symbol_liquidity.items():
            if row_year != year:
                continue
            category = "XBTUSD" if symbol == "XBTUSD" else "ETHUSD" if symbol == "ETHUSD" else "OTHER"
            category_counts[category].update(counts)
        total_known = sum(sum(c.values()) for c in category_counts.values())
        composition[year] = {
            category: {
                "fills": counts["AddedLiquidity"] + counts["RemovedLiquidity"],
                "weight": (counts["AddedLiquidity"] + counts["RemovedLiquidity"]) / total_known if total_known else 0.0,
                "maker_rate": counts["AddedLiquidity"] / (counts["AddedLiquidity"] + counts["RemovedLiquidity"])
                if counts["AddedLiquidity"] + counts["RemovedLiquidity"] else 0.0,
            }
            for category, counts in sorted(category_counts.items())
        }

    weights_2020 = {k: v["weight"] for k, v in composition["2020"].items()}
    weights_2021 = {k: v["weight"] for k, v in composition["2021"].items()}
    rates_2020 = {k: v["maker_rate"] for k, v in composition["2020"].items()}
    rates_2021 = {k: v["maker_rate"] for k, v in composition["2021"].items()}
    categories = sorted(set(weights_2020) | set(weights_2021))
    period_1_rate = sum(weights_2020.get(s, 0.0) * rates_2020.get(s, 0.0) for s in categories)
    period_2_rate = sum(weights_2021.get(s, 0.0) * rates_2021.get(s, 0.0) for s in categories)
    within = sum(weights_2020.get(s, 0.0) * (rates_2021.get(s, 0.0) - rates_2020.get(s, 0.0)) for s in categories)
    mix = sum((weights_2021.get(s, 0.0) - weights_2020.get(s, 0.0)) * rates_2020.get(s, 0.0) for s in categories)
    interaction = sum(
        (weights_2021.get(s, 0.0) - weights_2020.get(s, 0.0))
        * (rates_2021.get(s, 0.0) - rates_2020.get(s, 0.0))
        for s in categories
    )

    commissions = {
        f"{event_type}:{liquidity or 'UNSPECIFIED'}": int(amount)
        for (event_type, liquidity), amount in sorted(exec_commission_sat.items())
    }
    maker_rebate_sat = exec_commission_sat[("Trade", "AddedLiquidity")]
    taker_fee_sat = exec_commission_sat[("Trade", "RemovedLiquidity")]
    funding_execcomm_sat = exec_commission_sat[("Funding", "")]

    wallet_sum = sum(wallet_totals.values(), Decimal(0))
    xbtusd_gross_usd_contracts = contracts_by_symbol["XBTUSD"]  # XBTUSD is a $1 inverse contract.
    reported_contracts = {"XBTUSD": 9_258_603_804, "ETHUSD": 13_777_628, "XRPUSD": 29_413_562}
    contract_audit = {}
    for symbol, reported in reported_contracts.items():
        recomputed = contracts_by_symbol[symbol]
        contract_audit[symbol] = {
            "reported": reported,
            "raw_sum_abs_lastqty": int(recomputed),
            "matches_gross_contract_count": recomputed == reported,
        }

    return {
        "method": "stdlib_csv_decimal_raw_path_v1",
        "raw_coverage": {"first_trade_date": first_trade_date, "last_trade_date": last_trade_date},
        "rows_by_execution_type": dict(sorted(rows_by_type.items())),
        "trade_fills_by_symbol": dict(sorted(trades_by_symbol.items())),
        "gross_contracts_abs_lastqty_by_symbol": {k: int(v) for k, v in sorted(contracts_by_symbol.items())},
        "annual_maker_taker_by_fill_count": year_ratios,
        "monthly_maker_taker_by_fill_count": month_ratios,
        "commission_execcomm_satoshi_by_type_liquidity": commissions,
        "fee_funding_summary_btc": {
            "maker_execcomm_btc_negative_rebate": str(maker_rebate_sat / SATOSHI),
            "taker_execcomm_btc_fee": str(taker_fee_sat / SATOSHI),
            "net_trade_execcomm_btc": str((maker_rebate_sat + taker_fee_sat) / SATOSHI),
            "funding_execcomm_satoshi_signed": int(funding_execcomm_sat),
            "funding_income_btc_if_negative_is_credit": str(-funding_execcomm_sat / SATOSHI),
        },
        "wallet_audit": {
            "valid_rows": valid_wallet_rows,
            "status_counts": {f"{k[0]}:{k[1]}": v for k, v in sorted(wallet_status_counts.items())},
            "completed_event_satoshi": {k: int(v) for k, v in sorted(wallet_totals.items())},
            "completed_cashflow_sum_satoshi": int(wallet_sum),
            "final_wallet_balance_satoshi": int(last_wallet_balance_sat),
            "final_balance_matches_completed_cashflows": wallet_sum == last_wallet_balance_sat,
            "canceled_withdrawal_count": len(canceled_withdrawals),
            "canceled_withdrawal_abs_btc": str(abs(sum(canceled_withdrawals, Decimal(0))) / SATOSHI),
        },
        "reported_contract_claims": contract_audit,
        "xbtusd_gross_usd_notional_lower_bound": int(xbtusd_gross_usd_contracts),
        "reported_aggregate_usd_equivalent": 10_570_000_000,
        "aggregate_claim_cannot_be_gross_usd_notional": xbtusd_gross_usd_contracts > Decimal("10570000000"),
        "maker_ratio_composition_2020_vs_2021": {
            "groups_by_fill_count": composition,
            "period_1_maker_ratio": period_1_rate,
            "period_2_maker_ratio": period_2_rate,
            "total_change": period_2_rate - period_1_rate,
            "within_symbol_effect": within,
            "composition_effect": mix,
            "interaction_effect": interaction,
            "identity_error": (period_2_rate - period_1_rate) - (within + mix + interaction),
        },
        "order_counts": {
            "orders_with_nonblank_id": len(orders),
            "orders_by_year_first_fill": dict(sorted(Counter(o["first_date"][:4] for o in orders.values()).items())),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=Path(".external-research-data/external-bitmex-trader-2018-2021/raw"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_raw_dataset(args.raw_dir)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
