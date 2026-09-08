"""Offline adapter: FillLedger -> TradingSnapshot v1 builder.

Strictly local, offline conversion using exact Decimal arithmetic.
Never calls external exchange/AWS APIs.
Never guesses market prices or cash.
Never conflates individual fills with round-trip closed trades.
Produces TradingSnapshot with source.kind = "local_snapshot".
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from bithumb_coin_trader.fill_ledger import FillLedger, FillLedgerError

SNAPSHOT_SCHEMA_VERSION = 1

NAME_MAP: dict[str, str] = {
    "KRW-BTC": "비트코인",
    "KRW-ETH": "이더리움",
    "KRW-SOL": "솔라나",
    "KRW-XRP": "리플",
    "KRW-ADA": "에이다",
    "KRW-DOGE": "도지코인",
    "KRW-AVAX": "아발란체",
    "KRW-DOT": "폴카닷",
}


class SnapshotBuilderError(ValueError):
    """Raised when input files are invalid or calculations cannot be completed safely."""


def _to_decimal(value: Any, name: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    if value is None or isinstance(value, bool):
        raise SnapshotBuilderError(f"{name} must be a valid number, got {type(value).__name__}")
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SnapshotBuilderError(f"{name} must be a valid numeric decimal: {value!r}") from exc
    if not dec.is_finite():
        raise SnapshotBuilderError(f"{name} must be finite")
    if positive and dec <= 0:
        raise SnapshotBuilderError(f"{name} must be strictly positive (got {dec})")
    if non_negative and dec < 0:
        raise SnapshotBuilderError(f"{name} must be non-negative (got {dec})")
    return dec


def _parse_iso_timestamp(ts_str: Any, name: str) -> datetime:
    if not isinstance(ts_str, str) or not ts_str.strip():
        raise SnapshotBuilderError(f"{name} must be a non-empty ISO 8601 string")
    clean = ts_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(clean)
    except Exception as exc:
        raise SnapshotBuilderError(f"{name} has invalid ISO 8601 format: {ts_str!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _kst_date_str(dt: datetime) -> str:
    kst_dt = dt.astimezone(timezone(timedelta(hours=9)))
    return kst_dt.strftime("%Y-%m-%d")


def build_trading_snapshot(
    ledger_path: Path,
    account_state_path: Path,
    mark_prices_path: Path,
    equity_history_path: Path | None = None,
) -> dict[str, Any]:
    """Build a verified TradingSnapshot v1 dictionary from offline input files."""
    # 1. Load & validate account_state
    if not account_state_path.exists():
        raise SnapshotBuilderError(f"account state file not found: {account_state_path}")
    try:
        with account_state_path.open("r", encoding="utf-8") as f:
            account_state = json.load(f)
    except json.JSONDecodeError as exc:
        raise SnapshotBuilderError(f"invalid JSON in account state: {exc}") from exc

    if not isinstance(account_state, dict):
        raise SnapshotBuilderError("account state root must be an object")

    schema_ver = account_state.get("schemaVersion") or account_state.get("schema_version")
    if schema_ver != 1:
        raise SnapshotBuilderError(f"unsupported account state schema version: {schema_ver!r}")

    ts_str = account_state.get("timestamp")
    snapshot_dt = _parse_iso_timestamp(ts_str, "account_state.timestamp")
    timestamp_iso = snapshot_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    kst_today = _kst_date_str(snapshot_dt)

    mode = account_state.get("mode", "OFF")
    if mode not in ("OFF", "PAPER", "LIVE"):
        raise SnapshotBuilderError(f"invalid mode in account state: {mode!r}")

    cash = _to_decimal(account_state.get("cash_krw"), "account_state.cash_krw", non_negative=True)
    starting_equity_raw = account_state.get("starting_equity_krw")
    starting_equity: Decimal | None = None
    if starting_equity_raw is not None:
        starting_equity = _to_decimal(starting_equity_raw, "account_state.starting_equity_krw", positive=True)

    # Daily baseline
    daily_baseline_raw = account_state.get("daily_baseline")
    daily_baseline_dict: dict[str, Any] | None = None
    today_pnl: Decimal | None = None
    today_return_pct: Decimal | None = None

    if daily_baseline_raw is not None:
        if not isinstance(daily_baseline_raw, dict):
            raise SnapshotBuilderError("daily_baseline must be an object or null")
        b_equity = _to_decimal(daily_baseline_raw.get("equity"), "daily_baseline.equity", positive=True)
        b_net_cash = _to_decimal(daily_baseline_raw.get("netCashFlow", 0), "daily_baseline.netCashFlow")
        b_trading_day = daily_baseline_raw.get("tradingDay")
        if not isinstance(b_trading_day, str):
            raise SnapshotBuilderError("daily_baseline.tradingDay must be a YYYY-MM-DD string")
        b_tz = daily_baseline_raw.get("timeZone")
        if b_tz != "Asia/Seoul":
            raise SnapshotBuilderError("daily_baseline.timeZone must be 'Asia/Seoul'")

        daily_baseline_dict = {
            "equity": float(b_equity),
            "netCashFlow": float(b_net_cash),
            "tradingDay": b_trading_day,
            "timeZone": "Asia/Seoul",
        }

    # 2. Load & validate mark_prices
    if not mark_prices_path.exists():
        raise SnapshotBuilderError(f"mark prices file not found: {mark_prices_path}")
    try:
        with mark_prices_path.open("r", encoding="utf-8") as f:
            mark_prices_data = json.load(f)
    except json.JSONDecodeError as exc:
        raise SnapshotBuilderError(f"invalid JSON in mark prices: {exc}") from exc

    if not isinstance(mark_prices_data, dict):
        raise SnapshotBuilderError("mark prices root must be an object")

    mp_schema = mark_prices_data.get("schemaVersion") or mark_prices_data.get("schema_version")
    if mp_schema != 1:
        raise SnapshotBuilderError(f"unsupported mark prices schema version: {mp_schema!r}")

    raw_markets = mark_prices_data.get("markets")
    if not isinstance(raw_markets, dict):
        raise SnapshotBuilderError("mark prices 'markets' must be a dictionary")

    marks: dict[str, Decimal] = {}
    for m, p in raw_markets.items():
        marks[m] = _to_decimal(p, f"mark_prices.markets[{m}]", positive=True)

    # 3. Load FillLedger
    ledger = FillLedger(ledger_path)
    records, _, _ = ledger._load()
    positions_map = ledger.positions()

    # Find earliest bid timestamp per market for openedAt
    first_bid_at: dict[str, str] = {}
    for rec in records:
        m = rec["market"]
        if rec.get("side") == "bid" and m not in first_bid_at:
            first_bid_at[m] = rec["executed_at"]

    # 4. Calculate open positions
    calculated_positions: list[dict[str, Any]] = []
    total_exposure = Decimal("0")
    total_unrealized_pnl = Decimal("0")

    for market, pos in sorted(positions_map.items()):
        if pos.volume <= Decimal("0"):
            continue  # Exclude zero positions

        if market not in marks:
            raise SnapshotBuilderError(f"missing mark price for open position in market {market!r}")

        current_mark = marks[market]
        volume = pos.volume
        cost_basis = pos.cost_basis  # Net of opening fees in FillLedger
        average_cost = pos.average_cost
        exposure = volume * current_mark
        # Position unrealized PnL = market value - cost basis
        pnl = exposure - cost_basis
        pnl_pct = (pnl / cost_basis * 100) if cost_basis > Decimal("0") else Decimal("0")
        entry_fee = pos.paid_fees

        opened_at = first_bid_at.get(market) or timestamp_iso
        asset = market.split("-")[1] if "-" in market else market
        name = NAME_MAP.get(market, asset)

        total_exposure += exposure
        total_unrealized_pnl += pnl

        calculated_positions.append({
            "id": f"pos-{market.lower()}",
            "asset": asset,
            "name": name,
            "pair": market,
            "side": "LONG",
            "entry": float(average_cost),
            "current": float(current_mark),
            "quantity": float(volume),
            "exposure": float(exposure),
            "pnl": float(pnl),
            "pnlPct": float(pnl_pct),
            "entryFee": float(entry_fee),
            "openedAt": opened_at,
            "strategy": account_state.get("strategy"),
        })

    # 5. Portfolio aggregation
    equity = cash + total_exposure
    realized_pnl = sum((p.realized_pnl for p in positions_map.values()), Decimal("0"))
    total_fees = sum((p.paid_fees for p in positions_map.values()), Decimal("0"))

    total_pnl: Decimal | None = None
    total_return_pct: Decimal | None = None
    if starting_equity is not None and starting_equity > Decimal("0"):
        total_pnl = equity - starting_equity
        total_return_pct = (total_pnl / starting_equity) * 100

    # Calculate today metrics if daily baseline exists and matches KST today
    if daily_baseline_raw is not None and daily_baseline_dict is not None:
        if daily_baseline_dict["tradingDay"] == kst_today:
            b_eq = Decimal(str(daily_baseline_dict["equity"]))
            b_flow = Decimal(str(daily_baseline_dict["netCashFlow"]))
            today_pnl = equity - b_eq - b_flow
            today_return_pct = (today_pnl / b_eq) * 100

    # 6. Performance & History (Optional)
    equity_curve: list[dict[str, Any]] = []
    daily_performance: list[dict[str, Any]] = []
    perf_metrics = {
        "return7d": None,
        "return30d": None,
        "totalReturn": float(total_return_pct) if total_return_pct is not None else None,
        "maxDrawdown": None,
        "winRate": None,
        "profitFactor": None,
        "averageTrade": None,
    }

    if equity_history_path and equity_history_path.exists():
        try:
            with equity_history_path.open("r", encoding="utf-8") as f:
                hist_data = json.load(f)
            if isinstance(hist_data, dict):
                raw_curve = hist_data.get("equityCurve") or hist_data.get("equity_curve")
                if isinstance(raw_curve, list):
                    equity_curve = raw_curve
                raw_daily = hist_data.get("dailyPerformance") or hist_data.get("daily_performance")
                if isinstance(raw_daily, list):
                    daily_performance = raw_daily
                raw_perf = hist_data.get("performance")
                if isinstance(raw_perf, dict):
                    for k in perf_metrics:
                        if k in raw_perf:
                            perf_metrics[k] = raw_perf[k]
        except Exception as exc:
            raise SnapshotBuilderError(f"failed to read equity history: {exc}") from exc

    # 7. Bot Status
    bot_status_data = account_state.get("botStatus") or account_state.get("bot_status") or {}
    bot_status = {
        "mode": mode,
        "strategy": account_state.get("strategy") or bot_status_data.get("strategy"),
        "marketData": bot_status_data.get("marketData", "READY"),
        "orderExecution": bot_status_data.get("orderExecution", "DISABLED"),
        "riskGuard": bot_status_data.get("riskGuard", "ACTIVE"),
        "lastActivity": bot_status_data.get("lastActivity", timestamp_iso),
        "uptimeSeconds": bot_status_data.get("uptimeSeconds"),
        "todayTrades": bot_status_data.get("todayTrades"),
        "errors": bot_status_data.get("errors", 0),
    }

    # 8. Assemble complete TradingSnapshot v1
    snapshot: dict[str, Any] = {
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "timestamp": timestamp_iso,
        "mode": mode,
        "source": {
            "kind": "local_snapshot",
            "label": "오프라인 FillLedger 스냅샷",
        },
        "portfolio": {
            "equity": float(equity),
            "cash": float(cash),
            "exposure": float(total_exposure),
            "todayPnl": float(today_pnl) if today_pnl is not None else None,
            "todayReturnPct": float(today_return_pct) if today_return_pct is not None else None,
            "totalPnl": float(total_pnl) if total_pnl is not None else None,
            "totalReturnPct": float(total_return_pct) if total_return_pct is not None else None,
            "realizedPnl": float(realized_pnl),
            "unrealizedPnl": float(total_unrealized_pnl),
            "fees": float(total_fees),
        },
        "positions": calculated_positions,
        "recentTrades": [],  # Never invent trades from fills
        "equityCurve": equity_curve,
        "dailyPerformance": daily_performance,
        "botStatus": bot_status,
        "performance": perf_metrics,
        "today": {
            "realizedPnl": None,
            "unrealizedPnl": float(total_unrealized_pnl),
            "fees": None,
            "trades": None,
            "wins": None,
            "losses": None,
            "exposurePct": float(total_exposure / equity * 100) if equity > Decimal("0") else 0.0,
        },
        "dailyBaseline": daily_baseline_dict,
    }

    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Build TradingSnapshot v1 from offline FillLedger and state files.")
    parser.add_argument("--ledger", required=True, type=Path, help="Path to fills.jsonl")
    parser.add_argument("--account-state", required=True, type=Path, help="Path to account_state.json")
    parser.add_argument("--marks", required=True, type=Path, help="Path to mark_prices.json")
    parser.add_argument("--equity-history", type=Path, default=None, help="Optional path to equity_history.json")
    parser.add_argument("--output", required=True, type=Path, help="Output path for trading_snapshot.json")

    args = parser.parse_args()

    try:
        snapshot = build_trading_snapshot(
            ledger_path=args.ledger,
            account_state_path=args.account_state,
            mark_prices_path=args.marks,
            equity_history_path=args.equity_history,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        print(f"Successfully generated TradingSnapshot v1 at {args.output}")
    except (SnapshotBuilderError, FillLedgerError) as exc:
        print(f"Error building snapshot: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
