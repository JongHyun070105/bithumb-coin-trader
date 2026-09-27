"""Independent intent-conditional descriptive analysis and block uncertainty.

Reads raw executions plus the independently generated hourly context join. Position
intent is reconstructed in a separate reference pass and is explicitly conditional
on an unverified flat starting inventory. No result from this script establishes alpha.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
import json
import math
from pathlib import Path
import random
import sqlite3
import statistics
import tempfile
from typing import Any


DEFAULT_RAW = Path(".external-research-data/external-bitmex-trader-2018-2021/raw")
DEFAULT_CONTEXT = Path(".external-research-data/external-bitmex-market-context-2018-2021")
SATOSHI = Decimal(100_000_000)
BLOCK_DAYS = 7
BOOTSTRAP_REPS = 500


def parse_ts(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def pct_ci(values: list[float], estimate: float) -> dict[str, Any]:
    if not values:
        return {"estimate": estimate, "ci95_block": None, "replicates": 0}
    ordered = sorted(values)
    low = ordered[max(0, math.floor(0.025 * (len(ordered) - 1)))]
    high = ordered[min(len(ordered) - 1, math.ceil(0.975 * (len(ordered) - 1)))]
    return {"estimate": estimate, "ci95_block": [low, high], "replicates": len(values)}


def daily_blocks(days: list[date], rng: random.Random, block_len: int = BLOCK_DAYS) -> list[int]:
    if not days:
        return []
    selected: list[int] = []
    valid_starts = max(1, len(days) - block_len + 1)
    while len(selected) < len(days):
        start = rng.randrange(valid_starts)
        selected.extend(range(start, min(start + block_len, len(days))))
    return selected[:len(days)]


def bootstrap_ratio(
    daily: dict[date, tuple[float, float]],
    year: int,
    rng: random.Random,
    reps: int = BOOTSTRAP_REPS,
) -> dict[str, Any]:
    days = sorted(d for d in daily if d.year == year)
    if not days:
        return pct_ci([], 0.0)
    numerator = sum(daily[d][0] for d in days)
    denominator = sum(daily[d][1] for d in days)
    estimate = numerator / denominator if denominator else 0.0
    values: list[float] = []
    for _ in range(reps):
        indexes = daily_blocks(days, rng)
        num = sum(daily[days[i]][0] for i in indexes)
        den = sum(daily[days[i]][1] for i in indexes)
        if den:
            values.append(num / den)
    return pct_ci(values, estimate)


def bootstrap_daily_mean(daily: dict[date, float], year: int, rng: random.Random) -> dict[str, Any]:
    days = sorted(d for d in daily if d.year == year)
    if not days:
        return pct_ci([], 0.0)
    estimate = sum(daily[d] for d in days) / len(days)
    values = [statistics.fmean(daily[days[i]] for i in daily_blocks(days, rng))
              for _ in range(BOOTSTRAP_REPS)]
    return pct_ci(values, estimate)


def bootstrap_median_by_day(
    daily_values: dict[date, list[float]], year: int, rng: random.Random,
    reps: int = 250,
) -> dict[str, Any]:
    days = sorted(d for d in daily_values if d.year == year)
    observations = [value for day in days for value in daily_values[day]]
    estimate = statistics.median(observations) if observations else 0.0
    values: list[float] = []
    for _ in range(reps):
        sampled = [value for i in daily_blocks(days, rng) for value in daily_values[days[i]]]
        if sampled:
            values.append(statistics.median(sampled))
    result = pct_ci(values, estimate)
    result["observations_n"] = len(observations)
    return result


def binomial_deviance(makers: list[int], known: list[int], start: int, end: int) -> float:
    maker = sum(makers[start:end])
    total = sum(known[start:end])
    taker = total - maker
    if not total:
        return 0.0
    result = 0.0
    if maker:
        result += maker * math.log(maker / total)
    if taker:
        result += taker * math.log(taker / total)
    return -2.0 * result


def pelt_like_binomial(makers: list[int], known: list[int], penalty: float, min_segment: int) -> list[int]:
    """Exact dynamic-programming segmentation with a BIC-style segment penalty."""
    n = len(makers)
    best = [float("inf")] * (n + 1)
    previous = [-1] * (n + 1)
    best[0] = 0.0
    for end in range(min_segment, n + 1):
        for start in range(0, end - min_segment + 1):
            if best[start] == float("inf"):
                continue
            value = best[start] + binomial_deviance(makers, known, start, end) + penalty
            if value < best[end]:
                best[end], previous[end] = value, start
    if previous[n] < 0:
        return []
    boundaries: list[int] = []
    end = n
    while end > 0:
        start = previous[end]
        if start < 0:
            return []
        if start > 0:
            boundaries.append(start)
        end = start
    return sorted(boundaries)


def change_point_audit(months: list[str], makers: list[int], known: list[int]) -> dict[str, Any]:
    if len(months) != len(makers) or len(months) != len(known) or len(months) < 12:
        return {"status": "NOT_IDENTIFIABLE", "reason": "Insufficient aligned monthly raw counts"}
    rate = sum(makers) / sum(known)
    total_var = sum(count * rate * (1.0 - rate) for count in known)
    cum = 0.0
    cusum = (0.0, None)
    pearson: list[float] = []
    for i, (maker, count) in enumerate(zip(makers, known)):
        residual = maker - count * rate
        cum += residual
        if i < len(months) - 1 and total_var:
            value = abs(cum) / math.sqrt(total_var)
            if value > cusum[0]:
                cusum = (value, months[i])
        variance = count * rate * (1.0 - rate)
        pearson.append((residual * residual / variance) if variance else 0.0)

    q_cum = 0.0
    q_total = sum(pearson)
    cusumsq = (0.0, None)
    for i, value in enumerate(pearson[:-1]):
        q_cum += value
        fraction = (i + 1) / len(pearson)
        statistic = abs((q_cum / q_total) - fraction) if q_total else 0.0
        if statistic > cusumsq[0]:
            cusumsq = (statistic, months[i])

    rolling: list[tuple[float, str, float, float]] = []
    for i in range(3, len(months) - 2):
        prior_maker, prior_total = sum(makers[i - 3:i]), sum(known[i - 3:i])
        next_maker, next_total = sum(makers[i:i + 3]), sum(known[i:i + 3])
        if prior_total and next_total:
            prior_rate, next_rate = prior_maker / prior_total, next_maker / next_total
            rolling.append((abs(next_rate - prior_rate), months[i], prior_rate, next_rate))
    max_rolling = max(rolling, default=(0.0, None, None, None))

    pelt_results: dict[str, list[str]] = {}
    logn = math.log(len(months))
    for min_segment in (3, 6):
        for penalty_mult in (1, 2, 4, 8):
            indexes = pelt_like_binomial(makers, known, penalty_mult * logn, min_segment)
            pelt_results[f"min{min_segment}_penalty{penalty_mult}logN"] = [months[i] for i in indexes]

    month_sets = [set(value) for value in pelt_results.values()]
    method_dependent = sorted(set.union(*month_sets)) if month_sets else []
    all_six_month = set.intersection(*(set(pelt_results[k]) for k in pelt_results if k.startswith("min6_")))
    robust = sorted(all_six_month & {cusum[1], cusumsq[1]}) if cusum[1] and cusumsq[1] else []
    return {
        "series": "raw monthly maker fills / (maker + taker fills); observed months only",
        "sample_months": len(months),
        "weighted_mean_maker_ratio": rate,
        "cusum_bridge_max_abs_standardized": {"statistic": cusum[0], "month_before_split": cusum[1], "p_value": None},
        "cusumsq_pearson_residual_screen": {"normalized_max_deviation": cusumsq[0], "month_before_split": cusumsq[1], "p_value": None},
        "rolling_3_month_divergence_max": {
            "month_split_start": max_rolling[1],
            "prior_rate": max_rolling[2],
            "following_rate": max_rolling[3],
            "absolute_difference": max_rolling[0],
        },
        "binomial_piecewise_constant_dp": pelt_results,
        "ROBUST_BREAKS": robust,
        "METHOD_DEPENDENT_BREAKS": method_dependent,
        "UNSUPPORTED_BREAKS": ["the claimed single 2020-10/11 break with OLS-CUSUM p<0.001; that statistic and p-value are not supported by the raw-count methods"],
        "inference_limitations": [
            "CUSUM/CUSUMSQ screens have no valid p-values here: monthly proportions are serially dependent and denominators vary.",
            "BIC-style binomial segmentation assumes independent fills within each monthly bin and cannot absorb serial or overdispersion without a cluster model.",
            "Break locations depend on minimum segment length and penalty; no unique breakpoint is identified.",
            "Only one descriptive series was preselected for this audit; other unregistered series would add multiple-testing risk.",
            "A linear time trend and structural breaks are not separately identified by these screens.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--context-root", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--output", type=Path, default=DEFAULT_CONTEXT / "derived" / "execution-behavior-audit-v3.json")
    parser.add_argument("--bootstrap-reps", type=int, default=BOOTSTRAP_REPS)
    args = parser.parse_args()
    if args.bootstrap_reps < 100:
        raise ValueError("At least 100 bootstrap replicates are required")

    joined_path = args.context_root / "derived" / "joined-trade-executions.csv.gz"
    if not joined_path.exists():
        raise FileNotFoundError(joined_path)

    base_daily: dict[date, dict[str, float]] = {}
    intent_daily: dict[tuple[date, str], dict[str, float]] = {}
    contextual: dict[tuple[str, str, str], dict[str, int]] = {}
    intent_counts: dict[tuple[str, str], dict[str, int]] = {}
    placebo_strata: dict[tuple[str, str, str], dict[str, int]] = {}
    scale_counts: dict[tuple[str, str], int] = {}
    monthly: dict[str, list[int]] = {}
    funding_daily: dict[date, float] = {}
    leakage = 0
    matched = 0
    unmatched = 0
    total_trade_rows = 0

    with tempfile.TemporaryDirectory(prefix="external-execution-audit-") as tmp:
        db_path = Path(tmp) / "fills.sqlite"
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=FILE")
        conn.execute("""CREATE TABLE fills (
            ts TEXT NOT NULL, day TEXT NOT NULL, execution_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
            qty TEXT NOT NULL, price REAL NOT NULL, liquidity TEXT NOT NULL,
            fee_sat INTEGER NOT NULL, vol_regime TEXT NOT NULL,
            trend_regime TEXT NOT NULL, funding_regime TEXT NOT NULL,
            context_age REAL, context_ts TEXT
        )""")
        insert_sql = "INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        pending: list[tuple[Any, ...]] = []
        with gzip.open(joined_path, "rt", encoding="utf-8", newline="") as joined_file:
            joined_reader = csv.DictReader(joined_file)
            for path in sorted(args.raw_dir.glob("aoa-execution-*.csv")):
                with path.open("r", encoding="utf-8-sig", newline="") as raw_file:
                    for raw in csv.DictReader(raw_file):
                        kind = (raw.get("exectype") or "").strip()
                        raw_ts = raw.get("transacttime") or raw.get("timestamp") or raw.get("date") or ""
                        ts = parse_ts(raw_ts)
                        day = ts.date()
                        if kind == "Funding":
                            fee_sat = int(Decimal(raw.get("execcomm") or "0"))
                            funding_daily[day] = funding_daily.get(day, 0.0) - fee_sat / float(SATOSHI)
                        if kind != "Trade":
                            continue
                        total_trade_rows += 1
                        joined = next(joined_reader, None)
                        if joined is None or joined.get("execution_id") != (raw.get("execid") or ""):
                            raise ValueError(f"Independent raw/join row alignment mismatch at {raw.get('execid')}")
                        context_ts = joined.get("context_timestamp_utc") or ""
                        context_age = float(joined["context_age_seconds"]) if joined.get("context_age_seconds") else None
                        if context_ts:
                            matched += 1
                            if parse_ts(context_ts) >= ts:
                                leakage += 1
                        else:
                            unmatched += 1
                        fee_sat = int(Decimal(raw.get("execcomm") or "0"))
                        pending.append((
                            ts.isoformat(), day.isoformat(), raw.get("execid") or "",
                            raw.get("orderid") or "", raw.get("symbol") or "",
                            raw.get("side") or "", str(Decimal(raw.get("lastqty") or "0")),
                            float(Decimal(raw.get("lastpx") or "0")),
                            raw.get("lastliquidityind") or "", fee_sat,
                            joined.get("volatility_regime") or "UNKNOWN",
                            joined.get("trend_regime") or "UNKNOWN",
                            joined.get("funding_regime") or "UNKNOWN",
                            context_age, context_ts,
                        ))
                        if len(pending) >= 20_000:
                            conn.executemany(insert_sql, pending)
                            conn.commit()
                            pending.clear()
            if next(joined_reader, None) is not None:
                raise ValueError("Joined context artifact contains extra rows after raw fills")
        if pending:
            conn.executemany(insert_sql, pending)
            conn.commit()
        if leakage:
            raise AssertionError(f"FUTURE_LEAKAGE_COUNT={leakage}")
        if matched + unmatched != total_trade_rows:
            raise AssertionError("Join cardinality changed during independent analysis")
        conn.execute("CREATE INDEX fills_order ON fills(symbol,order_id)")
        conn.execute("CREATE INDEX fills_ts ON fills(ts,symbol,execution_id)")

        # Independent order-level aggregates for fills/order and size robustness.
        conn.execute("""CREATE TABLE orders AS
            SELECT symbol, order_id, min(day) AS day, count(*) AS fill_count,
                   sum(CAST(qty AS REAL)) AS total_size,
                   sum(CASE WHEN liquidity='AddedLiquidity' THEN 1 ELSE 0 END) AS maker_fills,
                   sum(CASE WHEN liquidity='RemovedLiquidity' THEN 1 ELSE 0 END) AS taker_fills
            FROM fills GROUP BY symbol, order_id""")
        conn.execute("CREATE INDEX orders_day ON orders(day,symbol)")

        # Annual/monthly/day maker/taker observations.
        daily_counts: dict[date, list[float]] = {}
        by_symbol_year: dict[tuple[str, int], list[int]] = {}
        by_year_month: dict[str, list[int]] = {}
        for day_text, symbol, maker, taker in conn.execute(
            "SELECT day,symbol,sum(liquidity='AddedLiquidity'),sum(liquidity='RemovedLiquidity') "
            "FROM fills GROUP BY day,symbol ORDER BY day,symbol"
        ):
            day = date.fromisoformat(day_text)
            counts = daily_counts.setdefault(day, [0.0, 0.0])
            counts[0] += maker
            counts[1] += maker + taker
            sy = by_symbol_year.setdefault((symbol, day.year), [0, 0])
            sy[0] += maker
            sy[1] += maker + taker
            ym = day.strftime("%Y-%m")
            mm = by_year_month.setdefault(ym, [0, 0])
            mm[0] += maker
            mm[1] += maker + taker

        for day_text, maker, taker, maker_fee, taker_fee in conn.execute(
            "SELECT day, sum(liquidity='AddedLiquidity'), sum(liquidity='RemovedLiquidity'),"
            "sum(CASE WHEN liquidity='AddedLiquidity' THEN fee_sat ELSE 0 END),"
            "sum(CASE WHEN liquidity='RemovedLiquidity' THEN fee_sat ELSE 0 END) "
            "FROM fills GROUP BY day"
        ):
            d = date.fromisoformat(day_text)
            base = base_daily.setdefault(d, {})
            base.update({"maker": float(maker), "taker": float(taker),
                         "maker_fee_btc": float(maker_fee) / float(SATOSHI),
                         "taker_fee_btc": float(taker_fee) / float(SATOSHI)})

        # Flat-start conditional position intent reference reconstruction.
        positions: dict[str, Decimal] = {}
        last_prices: dict[str, float] = {}
        active_cycles: dict[str, dict[str, Any]] = {}
        cycles: list[dict[str, Any]] = []
        for row in conn.execute(
            "SELECT ts,day,execution_id,symbol,side,qty,price,liquidity,fee_sat,"
            "vol_regime,trend_regime,funding_regime FROM fills ORDER BY ts,symbol,execution_id"
        ):
            (ts_text, day_text, eid, symbol, side, qty_text, price, liq, fee_sat,
             vol_regime, trend_regime, funding_regime) = row
            ts = parse_ts(ts_text)
            day = date.fromisoformat(day_text)
            quantity = Decimal(qty_text)
            delta = quantity if side == "Buy" else -quantity
            before = positions.get(symbol, Decimal(0))
            after = before + delta
            positions[symbol] = after
            if before == 0:
                intent = "OPEN_LONG" if delta > 0 else "OPEN_SHORT"
                active_cycles[symbol] = {"open_time": ts, "direction": "LONG" if delta > 0 else "SHORT", "maker": 0, "fills": 0}
            elif before > 0 and delta > 0:
                intent = "ADD_LONG"
            elif before > 0 and after > 0:
                intent = "REDUCE_LONG"
            elif before > 0 and after == 0:
                intent = "CLOSE_LONG"
            elif before > 0:
                intent = "FLIP_LONG_TO_SHORT"
            elif before < 0 and delta < 0:
                intent = "ADD_SHORT"
            elif before < 0 and after < 0:
                intent = "REDUCE_SHORT"
            elif before < 0 and after == 0:
                intent = "CLOSE_SHORT"
            else:
                intent = "FLIP_SHORT_TO_LONG"

            intent_row = intent_counts.setdefault((symbol, intent), {"maker": 0, "taker": 0, "fees_sat": 0, "fills": 0})
            if liq == "AddedLiquidity":
                intent_row["maker"] += 1
            elif liq == "RemovedLiquidity":
                intent_row["taker"] += 1
            intent_row["fees_sat"] += fee_sat
            intent_row["fills"] += 1

            d_intent = intent_daily.setdefault((day, intent), {"maker": 0.0, "known": 0.0})
            if liq in ("AddedLiquidity", "RemovedLiquidity"):
                d_intent["known"] += 1
                d_intent["maker"] += int(liq == "AddedLiquidity")

            if intent.startswith(("OPEN_", "ADD_", "REDUCE_", "CLOSE_", "FLIP_")) and vol_regime != "UNKNOWN":
                group = "ENTRY" if intent.startswith("OPEN_") else "EXIT" if intent.startswith("CLOSE_") else "ADD" if intent.startswith("ADD_") else "REDUCE" if intent.startswith("REDUCE_") else "FLIP"
                key = (group, vol_regime, "TAKER" if liq == "RemovedLiquidity" else "MAKER" if liq == "AddedLiquidity" else "UNKNOWN")
                contextual.setdefault(key, {"fills": 0})["fills"] += 1
            if intent.startswith(("OPEN_", "CLOSE_")):
                group = "ENTRY" if intent.startswith("OPEN_") else "EXIT"
                key = (group, trend_regime, "TAKER" if liq == "RemovedLiquidity" else "MAKER" if liq == "AddedLiquidity" else "UNKNOWN")
                contextual.setdefault(key, {"fills": 0})["fills"] += 1
                if symbol in {"XBTUSD", "ETHUSD"} and liq in ("AddedLiquidity", "RemovedLiquidity"):
                    stratum = (symbol, vol_regime, funding_regime)
                    pool = placebo_strata.setdefault(stratum, {
                        "entry_maker": 0, "entry_taker": 0, "exit_maker": 0, "exit_taker": 0,
                    })
                    pool[f"{group.lower()}_{'maker' if liq == 'AddedLiquidity' else 'taker'}"] += 1
                key = (group, funding_regime, "TAKER" if liq == "RemovedLiquidity" else "MAKER" if liq == "AddedLiquidity" else "UNKNOWN")
                contextual.setdefault(key, {"fills": 0})["fills"] += 1

            if intent.startswith("ADD_") and symbol in last_prices:
                long_position = before > 0
                adverse = price < last_prices[symbol] if long_position else price > last_prices[symbol]
                key = (symbol, "ADVERSE" if adverse else "FAVORABLE_OR_FLAT")
                scale_counts[key] = scale_counts.get(key, 0) + 1
            last_prices[symbol] = price

            cycle = active_cycles.get(symbol)
            if cycle:
                cycle["fills"] += 1
                cycle["maker"] += int(liq == "AddedLiquidity")
            if after == 0 and cycle:
                cycles.append({"symbol": symbol, "open_time": cycle["open_time"], "close_time": ts,
                               "duration_seconds": (ts - cycle["open_time"]).total_seconds(),
                               "exit_year": ts.year, "fills": cycle["fills"],
                               "maker_ratio": cycle["maker"] / cycle["fills"] if cycle["fills"] else None})
                active_cycles.pop(symbol, None)
            elif intent.startswith("FLIP_") and cycle:
                cycles.append({"symbol": symbol, "open_time": cycle["open_time"], "close_time": ts,
                               "duration_seconds": (ts - cycle["open_time"]).total_seconds(),
                               "exit_year": ts.year, "fills": cycle["fills"],
                               "maker_ratio": cycle["maker"] / cycle["fills"] if cycle["fills"] else None})
                active_cycles[symbol] = {"open_time": ts, "direction": "SHORT" if after < 0 else "LONG", "maker": int(liq == "AddedLiquidity"), "fills": 1}

        # SQL order summaries support outlier analysis and bootstrap metrics.
        order_rows = list(conn.execute(
            "SELECT symbol,day,fill_count,total_size,maker_fills,taker_fills FROM orders ORDER BY symbol,day"
        ))
        order_daily: dict[date, list[float]] = {}
        order_daily_counts: dict[date, list[float]] = {}
        order_sizes_year_symbol: dict[tuple[int, str], dict[date, list[float]]] = {}
        for symbol, day_text, fill_count, size, maker_n, taker_n in order_rows:
            day = date.fromisoformat(day_text)
            order_daily.setdefault(day, []).append(float(size))
            values = order_daily_counts.setdefault(day, [0.0, 0.0])
            values[0] += fill_count
            values[1] += 1
            order_sizes_year_symbol.setdefault((day.year, symbol), {}).setdefault(day, []).append(float(size))

        first_observed_day = min(daily_counts)
        last_observed_day = max(daily_counts)
        all_observed_days: list[date] = []
        cursor_day = first_observed_day
        while cursor_day <= last_observed_day:
            all_observed_days.append(cursor_day)
            daily_counts.setdefault(cursor_day, [0.0, 0.0])
            order_daily_counts.setdefault(cursor_day, [0.0, 0.0])
            order_daily.setdefault(cursor_day, [])
            cursor_day += timedelta(days=1)
        for by_day in order_sizes_year_symbol.values():
            for day in all_observed_days:
                by_day.setdefault(day, [])

        cycle_daily_by_symbol: dict[tuple[int, str], dict[date, list[float]]] = {}
        for cycle in cycles:
            close_day = cycle["close_time"].date()
            cycle_daily_by_symbol.setdefault((cycle["exit_year"], cycle["symbol"]), {}).setdefault(close_day, []).append(cycle["duration_seconds"])
        for by_day in cycle_daily_by_symbol.values():
            for day in all_observed_days:
                by_day.setdefault(day, [])

        years = sorted({d.year for d in daily_counts})
        first_day = min(daily_counts)
        last_day = max(daily_counts)
        funding_calendar: dict[date, float] = {}
        day_cursor = first_day
        while day_cursor <= last_day:
            funding_calendar[day_cursor] = funding_daily.get(day_cursor, 0.0)
            day_cursor += timedelta(days=1)
        rng = random.Random(20260927)
        uncertainty: dict[str, Any] = {}
        for year in years:
            maker_daily = {d: (counts[0], counts[1]) for d, counts in daily_counts.items()}
            taker_daily = {d: (counts[1] - counts[0], counts[1]) for d, counts in daily_counts.items()}
            order_ratio_daily = {d: (float(values[0]), float(values[1])) for d, values in order_daily_counts.items()}
            entry_daily: dict[date, tuple[float, float]] = {}
            exit_daily: dict[date, tuple[float, float]] = {}
            for (d, intent), values in intent_daily.items():
                target = entry_daily if intent.startswith("OPEN_") else exit_daily if intent.startswith("CLOSE_") else None
                if target is not None:
                    old = target.get(d, (0.0, 0.0))
                    target[d] = (old[0] + values["maker"], old[1] + values["known"])
            year_days = [d for d in all_observed_days if d.year == year]
            for day in year_days:
                entry_daily.setdefault(day, (0.0, 0.0))
                exit_daily.setdefault(day, (0.0, 0.0))
            uncertainty[str(year)] = {
                "maker_ratio": bootstrap_ratio(maker_daily, year, rng),
                "taker_ratio": bootstrap_ratio(taker_daily, year, rng),
                "fills_per_order": bootstrap_ratio(order_ratio_daily, year, rng),
                "maker_by_entry": bootstrap_ratio(entry_daily, year, rng),
                "maker_by_close_exit": bootstrap_ratio(exit_daily, year, rng),
                "fee_contribution_btc_per_active_day": {
                    role: bootstrap_daily_mean({d: v.get(role, 0.0) for d, v in base_daily.items()}, year, rng)
                    for role in ("maker_fee_btc", "taker_fee_btc")
                },
                "funding_income_btc_per_calendar_day": bootstrap_daily_mean(funding_calendar, year, rng),
                "median_order_size_contracts_by_symbol": {
                    symbol: bootstrap_median_by_day(days, year, rng)
                    for (sample_year, symbol), days in order_sizes_year_symbol.items() if sample_year == year and symbol in {"XBTUSD", "ETHUSD"}
                },
                "median_conditional_cycle_duration_seconds_by_symbol": {
                    symbol: bootstrap_median_by_day(days, year, rng)
                    for (sample_year, symbol), days in cycle_daily_by_symbol.items() if sample_year == year and symbol in {"XBTUSD", "ETHUSD"}
                },
            }

        maker_share = sum(v[0] for v in daily_counts.values()) / sum(v[1] for v in daily_counts.values())
        raw_months = sorted(by_year_month)
        change_points = change_point_audit(raw_months,
                                           [by_year_month[k][0] for k in raw_months],
                                           [by_year_month[k][1] for k in raw_months])

        # Order-level outlier robustness for fill-weighted maker usage.
        by_symbol_orders: dict[str, list[tuple[float, int, int]]] = {}
        for symbol, _day, _fills, size, maker_n, taker_n in order_rows:
            by_symbol_orders.setdefault(symbol, []).append((float(size), int(maker_n), int(taker_n)))
        outlier: dict[str, Any] = {"all_fill_weighted_maker_ratio": maker_share, "by_symbol": {}}
        for symbol, rows in by_symbol_orders.items():
            if symbol not in {"XBTUSD", "ETHUSD"}:
                continue
            base_known = sum(m + t for _, m, t in rows)
            base_maker = sum(m for _, m, _ in rows)
            ordered_sizes = sorted(size for size, _, _ in rows)
            p99 = ordered_sizes[max(0, math.ceil(0.99 * len(ordered_sizes)) - 1)]
            p999 = ordered_sizes[max(0, math.ceil(0.999 * len(ordered_sizes)) - 1)]
            outlier[symbol] = {
                "all_fills": base_maker / base_known if base_known else None,
                "top_1pct_orders_excluded": sum(m for size, m, _ in rows if size <= p99) / max(1, sum(m + t for size, m, t in rows if size <= p99)),
                "top_0_1pct_orders_excluded": sum(m for size, m, _ in rows if size <= p999) / max(1, sum(m + t for size, m, t in rows if size <= p999)),
                "equal_weight_per_order_maker_share": statistics.fmean(m / (m + t) for _, m, t in rows if m + t),
                "log_size_weighted_maker_share": (
                    sum(math.log1p(size) * m for size, m, _ in rows if size > 0)
                    / sum(math.log1p(size) * (m + t) for size, m, t in rows if size > 0)
                ),
                "top_1pct_order_size_cutoff_contracts": p99,
                "top_0_1pct_order_size_cutoff_contracts": p999,
                "order_rows": len(rows),
            }

        intent_report: dict[str, Any] = {}
        for (symbol, intent), counts in sorted(intent_counts.items()):
            known = counts["maker"] + counts["taker"]
            intent_report.setdefault(symbol, {})[intent] = {
                "fills": counts["fills"], "known_liquidity_fills": known,
                "maker_fills": counts["maker"], "taker_fills": counts["taker"],
                "maker_ratio": counts["maker"] / known if known else None,
                "execcomm_btc_signed": counts["fees_sat"] / float(SATOSHI),
                "state_assumption": "flat start; no independent position snapshot verifies absolute intent",
            }

        entry_taker = sum(v["taker"] for (s, i), v in intent_counts.items() if i.startswith("OPEN_"))
        entry_known = sum(v["maker"] + v["taker"] for (s, i), v in intent_counts.items() if i.startswith("OPEN_"))
        exit_taker = sum(v["taker"] for (s, i), v in intent_counts.items() if i.startswith("CLOSE_"))
        exit_known = sum(v["maker"] + v["taker"] for (s, i), v in intent_counts.items() if i.startswith("CLOSE_"))

        year_fills: dict[int, list[int]] = {}
        for month, (maker, known) in by_year_month.items():
            year = int(month[:4])
            totals = year_fills.setdefault(year, [0, 0])
            totals[0] += maker
            totals[1] += known
        annual_rates = {y: (n[0] / n[1] if n[1] else 0.0) for y, n in sorted(year_fills.items())}
        year_rates = {str(y): rate for y, rate in annual_rates.items()}
        active_day_rates = [counts[0] / counts[1] for counts in daily_counts.values() if counts[1]]
        outlier["equal_weight_per_active_day_maker_share"] = statistics.fmean(active_day_rates) if active_day_rates else None
        outlier["high_confidence_cycles_only"] = {
            "status": "NOT_IDENTIFIABLE",
            "maker_share": None,
            "reason": "No independently observed initial position snapshot; no reconstructed cycle is high-confidence.",
        }
        symbol_year_rates = {
            f"{symbol}:{year}": counts[0] / counts[1] if counts[1] else None
            for (symbol, year), counts in sorted(by_symbol_year.items()) if symbol in {"XBTUSD", "ETHUSD"}
        }
        paired_symbol_robustness: dict[str, Any] = {}
        for symbol in ("XBTUSD", "ETHUSD"):
            rate_2020 = symbol_year_rates.get(f"{symbol}:2020")
            rate_2021 = symbol_year_rates.get(f"{symbol}:2021")
            paired_symbol_robustness[symbol] = {
                "2020_maker_ratio": rate_2020,
                "2021_maker_ratio": rate_2021,
                "direction_same_as_aggregate": (rate_2021 > rate_2020) if rate_2020 is not None and rate_2021 is not None else None,
                "classification": "DESCRIPTIVE_REPLICATION_ONLY; not a formal holdout",
            }
        loa = {
            f"omit_{year}": {
                "remaining_annual_maker_rates": {str(y): year_rates[str(y)] for y in years if y != year},
                "remaining_annual_slope_per_year": (
                    (sum((y - statistics.fmean(yy for yy in years if yy != year)) * annual_rates[y] for y in years if y != year)
                     / sum((y - statistics.fmean(yy for yy in years if yy != year)) ** 2 for y in years if y != year))
                    if len(years) - 1 > 1 else None
                ),
            } for year in years
        }

        # Approximate hypergeometric label-shuffle placebo, stratified by symbol,
        # volatility regime, and funding sign. It tests entry-vs-close maker usage,
        # not whether the assumed-flat intent labels represent the account's truth.
        placebo_groups = [
            values for values in placebo_strata.values()
            if values["entry_maker"] + values["entry_taker"] > 0
            and values["exit_maker"] + values["exit_taker"] > 0
        ]
        weighted_differences: list[tuple[float, int]] = []
        for values in placebo_groups:
            entry_n = values["entry_maker"] + values["entry_taker"]
            exit_n = values["exit_maker"] + values["exit_taker"]
            weight = min(entry_n, exit_n)
            entry_maker_rate = values["entry_maker"] / entry_n
            exit_maker_rate = values["exit_maker"] / exit_n
            weighted_differences.append((exit_maker_rate - entry_maker_rate, weight))
        placebo_observed = (
            sum(value * weight for value, weight in weighted_differences) / sum(weight for _, weight in weighted_differences)
            if weighted_differences else 0.0
        )
        placebo_rng = random.Random(20260928)
        placebo_null: list[float] = []
        for _ in range(999):
            differences: list[tuple[float, int]] = []
            for values in placebo_groups:
                entry_n = values["entry_maker"] + values["entry_taker"]
                exit_n = values["exit_maker"] + values["exit_taker"]
                total = entry_n + exit_n
                takers = values["entry_taker"] + values["exit_taker"]
                if total <= 1 or entry_n <= 0 or exit_n <= 0:
                    continue
                probability = takers / total
                mean = entry_n * probability
                variance = entry_n * probability * (1 - probability) * (total - entry_n) / (total - 1)
                sampled_entry_takers = min(takers, max(0, round(placebo_rng.gauss(mean, math.sqrt(max(0.0, variance))))))
                sampled_exit_takers = takers - sampled_entry_takers
                entry_taker_rate = sampled_entry_takers / entry_n
                exit_taker_rate = sampled_exit_takers / exit_n
                differences.append((exit_taker_rate - entry_taker_rate, min(entry_n, exit_n)))
            if differences:
                placebo_null.append(sum(value * weight for value, weight in differences) / sum(weight for _, weight in differences))
        placebo_null_sorted = sorted(placebo_null)
        placebo_p = (
            (1 + sum(abs(value) >= abs(placebo_observed) for value in placebo_null)) / (len(placebo_null) + 1)
            if placebo_null else None
        )
        placebo_result = {
            "status": "EXPLORATORY_APPROXIMATE_PLACEBO",
            "stratification": "symbol x volatility regime x funding sign; entry/close labels shuffled within each block",
            "blocks_with_entry_and_close": len(placebo_groups),
            "observed_weighted_exit_minus_entry_maker_ratio": placebo_observed,
            "null_ci95": [placebo_null_sorted[max(0, math.floor(.025 * (len(placebo_null_sorted) - 1)))],
                          placebo_null_sorted[min(len(placebo_null_sorted) - 1, math.ceil(.975 * (len(placebo_null_sorted) - 1)))]] if placebo_null_sorted else None,
            "two_sided_approximate_p": placebo_p,
            "replicates": len(placebo_null),
            "limitations": ["Hypergeometric draws use a normal approximation.",
                            "Fill-level shuffling does not preserve temporal clusters, so p-values are descriptive only.",
                            "Intent labels depend on an assumed-flat initial position."],
        }
        conn.close()

    report = {
        "analysis": "raw CSV + joined hourly context, independent SQLite/Decimal state pass",
        "execution_fill_rows": total_trade_rows,
        "context_join_rows": matched,
        "context_unmatched_rows": unmatched,
        "future_leakage_count_independent_recheck": leakage,
        "position_intent_classification": "conditional assumed-flat reconstruction; absolute intent remains UNIDENTIFIABLE without an initial position snapshot",
        "intent_counts_and_maker_taker": intent_report,
        "entry_taker_ratio_conditional": entry_taker / entry_known if entry_known else None,
        "close_exit_taker_ratio_conditional": exit_taker / exit_known if exit_known else None,
        "exit_minus_entry_taker_ratio_conditional": (exit_taker / exit_known - entry_taker / entry_known) if exit_known and entry_known else None,
        "fills_per_order_and_median_size_bootstrap": uncertainty,
        "cycle_duration_status": "CONDITIONAL_ONLY; cycles assume flat at dataset boundary",
        "completed_assumed_flat_cycles": len(cycles),
        "open_assumed_flat_cycles_at_dataset_end": len(active_cycles),
        "high_confidence_cycles": 0,
        "annual_maker_share_by_fill_count": year_rates,
        "maker_behavior_by_symbol_year": symbol_year_rates,
        "symbol_holdout_robustness": paired_symbol_robustness,
        "maker_share_leave_one_year_out": loa,
        "outlier_robustness": outlier,
        "scaling_fill_to_fill_direction": {
            f"{symbol}:{direction}": count for (symbol, direction), count in sorted(scale_counts.items())
        },
        "execution_behavior_by_context": {
            f"{group}|{regime}|{liq}": values["fills"]
            for (group, regime, liq), values in sorted(contextual.items())
        },
        "funding_income_by_year_btc": {
            str(year): sum(value for day, value in funding_daily.items() if day.year == year)
            for year in sorted({day.year for day in funding_daily})
        },
        "structural_break_methods": change_points,
        "event_study": {
            "status": "NOT_IDENTIFIABLE",
            "reasons": ["No independently verified starting position means no HIGH_CONFIDENCE entries.",
                        "Acquired context is hourly, so +/-5m and +/-15m event windows are unavailable."],
        },
        "directional_vs_execution_attribution": {
            "status": "NOT_IDENTIFIABLE",
            "reasons": ["No initial/current position snapshots or unfilled order book.",
                        "Wallet RealisedPNL is aggregate cash-flow evidence and cannot separate timing, sizing, beta, execution, and funding.",
                        "No matched counterfactual fills or order-book queue history."],
        },
        "placebo_tests": placebo_result,
        "scope_limitations": [
            "Context conditioning is available only for XBTUSD and ETHUSD; 170,772 fills in other instruments have no joined context.",
            "Maker/taker intent rates are fill-count weighted; no order queue or unfilled orders exist in the source.",
            "Bootstrap uses 7-day moving blocks over active UTC days and 500 seeded replicates (median metrics use 250).",
            "No p-values are reported for the monthly break screens because dependent/overdispersed observations invalidate iid reference distributions.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output.exists() and args.output.read_text(encoding="utf-8") != output:
        raise FileExistsError(f"Refusing to overwrite existing report: {args.output}")
    args.output.write_text(output, encoding="utf-8")
    print(f"REPORT={args.output}")
    print(json.dumps({k: report[k] for k in (
        "execution_fill_rows", "context_join_rows", "context_unmatched_rows",
        "future_leakage_count_independent_recheck", "position_intent_classification",
        "entry_taker_ratio_conditional", "close_exit_taker_ratio_conditional",
        "completed_assumed_flat_cycles", "high_confidence_cycles", "structural_break_methods"
    )}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
