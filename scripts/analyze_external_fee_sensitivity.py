"""Independent fee/slippage transfer-risk grid from raw BitMEX fills.

The calculation is a transaction-cost sensitivity on realized filled notional,
not a profitability or Bithumb/Upbit performance estimate.
"""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal
import json
from pathlib import Path
from typing import Any


RAW_DEFAULT = Path(".external-research-data/external-bitmex-trader-2018-2021/raw")
OUTPUT_DEFAULT = Path(".external-research-data/external-bitmex-market-context-2018-2021/derived/fee-sensitivity-grid-v2.json")
SCHEDULES = {
    "historical_like": (Decimal("-2.5"), Decimal("7.5")),
    "zero_maker_rebate": (Decimal("0"), Decimal("7.5")),
    "maker_1_taker_4": (Decimal("1"), Decimal("4")),
    "maker_4_taker_4": (Decimal("4"), Decimal("4")),
    "maker_10_taker_10": (Decimal("10"), Decimal("10")),
}


def analyze(raw_dir: Path) -> dict[str, Any]:
    maker_notional: dict[str, Decimal] = {"XBTUSD": Decimal(0), "ETHUSD": Decimal(0)}
    taker_notional: dict[str, Decimal] = {"XBTUSD": Decimal(0), "ETHUSD": Decimal(0)}
    maker_fills = {"XBTUSD": 0, "ETHUSD": 0}
    taker_fills = {"XBTUSD": 0, "ETHUSD": 0}
    independent_fee_satoshi = {
        symbol: {"maker": Decimal(0), "taker": Decimal(0)}
        for symbol in maker_notional
    }
    funding_exec_comm_satoshi = Decimal(0)
    missing_usd_notional = 0

    for path in sorted(raw_dir.glob("aoa-execution-*.csv")):
        with path.open("r", newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                symbol = (row.get("symbol") or "").strip()
                if row.get("exectype") == "Funding":
                    funding_exec_comm_satoshi += Decimal(row.get("execcomm") or "0")
                    continue
                if row.get("exectype") != "Trade" or symbol not in maker_notional:
                    continue
                liquidity = (row.get("lastliquidityind") or "").strip()
                try:
                    # XBTUSD is $1/contract. For ETHUSD, the raw BitMEX
                    # foreignNotional field is already the BTC-converted USD
                    # equivalent under the quanto contract.
                    notional = abs(Decimal(row["lastqty"])) if symbol == "XBTUSD" else abs(Decimal(row["foreignnotional"]))
                    fee = Decimal(row.get("execcomm") or "0")
                except (KeyError, ValueError, ArithmeticError):
                    missing_usd_notional += 1
                    continue
                if not notional.is_finite() or not fee.is_finite():
                    missing_usd_notional += 1
                    continue
                if liquidity == "AddedLiquidity":
                    maker_notional[symbol] += notional
                    maker_fills[symbol] += 1
                    independent_fee_satoshi[symbol]["maker"] += fee
                elif liquidity == "RemovedLiquidity":
                    taker_notional[symbol] += notional
                    taker_fills[symbol] += 1
                    independent_fee_satoshi[symbol]["taker"] += fee

    core_maker = sum(maker_notional.values(), Decimal(0))
    core_taker = sum(taker_notional.values(), Decimal(0))
    fractions = (Decimal("0.50"), Decimal("0.75"), Decimal("1.00"))
    slippage_bps_values = (Decimal("0"), Decimal("1"), Decimal("3"), Decimal("5"))
    grid: list[dict[str, Any]] = []
    for name, (maker_bps, taker_bps) in SCHEDULES.items():
        for fill_fraction in fractions:
            for slippage_bps in slippage_bps_values:
                maker_cost = core_maker * fill_fraction * (maker_bps + slippage_bps) / Decimal(10_000)
                taker_cost = core_taker * fill_fraction * (taker_bps + slippage_bps) / Decimal(10_000)
                grid.append({
                    "fee_schedule": name,
                    "maker_fee_bps": str(maker_bps),
                    "taker_fee_bps": str(taker_bps),
                    "recorded_turnover_fraction_assumed_filled": str(fill_fraction),
                    "additional_slippage_bps_each_side": str(slippage_bps),
                    "maker_cost_usd_equiv": str(maker_cost),
                    "taker_cost_usd_equiv": str(taker_cost),
                    "combined_transaction_cost_usd_equiv": str(maker_cost + taker_cost),
                })

    return {
        "method": "raw CSV, independent Decimal aggregation; no production fee or contract-analysis functions imported",
        "scope": "XBTUSD and ETHUSD realized fills only",
        "notional_method": {
            "XBTUSD": "abs(lastqty) USD because each inverse contract is $1",
            "ETHUSD": "abs(raw foreignnotional), the exchange's USD-equivalent quanto amount",
        },
        "filled_notional_usd_equiv": {
            symbol: {
                "maker": str(maker_notional[symbol]),
                "taker": str(taker_notional[symbol]),
                "total": str(maker_notional[symbol] + taker_notional[symbol]),
                "maker_fills": maker_fills[symbol],
                "taker_fills": taker_fills[symbol],
                "raw_execcomm_btc_signed_by_liquidity": {
                    role: str(amount / Decimal(100_000_000))
                    for role, amount in independent_fee_satoshi[symbol].items()
                },
            }
            for symbol in maker_notional
        },
        "GRID": grid,
        "historical_funding_income_btc_if_negative_execcomm_is_credit": str(-funding_exec_comm_satoshi / Decimal(100_000_000)),
        "funding_removal": "Report separately; do not net against USD costs without time-specific BTC/USD conversion and position attribution.",
        "short_spot_constraint": "NOT_IDENTIFIABLE from fills alone; spot borrowing/availability and counterfactual position paths are absent.",
        "partial_fill_assumption": "Scaling observed filled turnover by 50/75/100%; no model for unfilled/canceled orders because they are not observed.",
        "limitations": [
            "Transaction costs only; no gross strategy PnL is inferred.",
            "USDT/USD basis, venue-specific liquidity, queue priority, adverse selection, and latency are not modeled.",
            "The ETHUSD foreignNotional field is taken as publisher-calculated USD equivalent and should be validated against date-specific contract metadata before any precise transfer estimate.",
            f"Rows with missing/invalid scope notional or fee: {missing_usd_notional}.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    args = parser.parse_args()
    report = analyze(args.raw_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output.exists() and args.output.read_text(encoding="utf-8") != serialized:
        raise FileExistsError(f"Refusing to replace prior fee sensitivity evidence: {args.output}")
    args.output.write_text(serialized, encoding="utf-8")
    print(f"REPORT={args.output}")
    print(json.dumps(report["filled_notional_usd_equiv"], indent=2, sort_keys=True))
    print(f"GRID_SCENARIOS={len(report['GRID'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
