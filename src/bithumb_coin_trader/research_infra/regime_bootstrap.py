"""Year-stratified moving-block summaries of execution liquidity by regime.

These descriptive intervals preserve within-year seven-day clusters while
holding each symbol's observed year coverage fixed. They do not measure alpha,
queue position, or causal effects.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import random
from typing import Any, Iterable, Mapping


REGIME_COLUMNS = (
    "volatility_regime",
    "trend_regime",
    "volume_regime",
    "funding_regime",
)
LIQUIDITY_COLUMNS = {
    "AddedLiquidity": "maker",
    "RemovedLiquidity": "taker",
}
SYMBOLS = ("XBTUSD", "ETHUSD")
BLOCK_DAYS = 7


def _parse_utc_day(value: str) -> date:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).date()


def _moving_windows(values: list[int], width: int) -> list[int]:
    if not values or width < 1:
        return []
    return [sum(values[start:start + width])
            for start in range(len(values) - width + 1)]


def _percentile_interval(values: list[float]) -> list[float] | None:
    if not values:
        return None
    ordered = sorted(values)
    low = max(0, int(0.025 * (len(ordered) - 1)))
    high = min(len(ordered) - 1, int(0.975 * (len(ordered) - 1) + 0.999999))
    return [ordered[low], ordered[high]]


def _stratum_rng(seed: int, symbol: str, dimension: str, regime: str) -> random.Random:
    material = f"{seed}|{symbol}|{dimension}|{regime}".encode("utf-8")
    stable_seed = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
    return random.Random(stable_seed)


def _bootstrap_ratio(
    yearly_daily: Mapping[int, tuple[list[int], list[int]]],
    *,
    reps: int,
    rng: random.Random,
    block_days: int = BLOCK_DAYS,
) -> tuple[list[float], int]:
    """Resample non-wrapping moving blocks within year, preserving year weights."""
    prepared: list[tuple[int, dict[int, tuple[list[int], list[int]]]]] = []
    for year in sorted(yearly_daily):
        maker, taker = yearly_daily[year]
        if not maker:
            continue
        windows = {
            width: (_moving_windows(maker, width),
                    _moving_windows(taker, width))
            for width in range(1, min(block_days, len(maker)) + 1)
        }
        prepared.append((len(maker), windows))

    ratios: list[float] = []
    for _ in range(reps):
        maker_total = 0
        known_total = 0
        for day_count, windows in prepared:
            sampled_days = 0
            while sampled_days < day_count:
                width = min(block_days, day_count - sampled_days)
                start = rng.randrange(len(windows[width][0]))
                maker_window, taker_window = windows[width]
                maker_total += maker_window[start]
                known_total += maker_window[start] + taker_window[start]
                sampled_days += width
        if known_total:
            ratios.append(maker_total / known_total)
    return ratios, len(ratios)


def analyze_records(
    records: Iterable[Mapping[str, str]],
    *,
    input_sha256: str,
    reps: int = 5_000,
    seed: int = 20260927,
) -> dict[str, Any]:
    if reps < 100:
        raise ValueError("At least 100 bootstrap replicates are required")

    daily: dict[tuple[str, str, str, int], dict[date, list[int]]] = defaultdict(dict)
    year_bounds: dict[tuple[str, int], tuple[date, date]] = {}
    symbol_counts: dict[str, dict[str, int]] = {
        symbol: {"rows": 0, "maker_fills": 0, "taker_fills": 0,
                 "unknown_liquidity_fills": 0}
        for symbol in SYMBOLS
    }
    regime_counts: dict[tuple[str, str, str], dict[str, int]] = defaultdict(
        lambda: {"rows": 0, "maker_fills": 0, "taker_fills": 0,
                 "unknown_liquidity_fills": 0}
    )
    unknown_regime_counts: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"rows": 0, "known_liquidity_fills": 0}
    )
    observed_regimes: set[tuple[str, str, str]] = set()

    for record in records:
        symbol = record.get("symbol", "")
        if symbol not in SYMBOLS:
            continue
        day = _parse_utc_day(record["execution_timestamp_utc"])
        year_key = (symbol, day.year)
        old_bounds = year_bounds.get(year_key)
        year_bounds[year_key] = (
            min(old_bounds[0], day) if old_bounds else day,
            max(old_bounds[1], day) if old_bounds else day,
        )
        symbol_counts[symbol]["rows"] += 1

        liquidity = record.get("lastliquidityind", "")
        liquidity_side = LIQUIDITY_COLUMNS.get(liquidity)
        if liquidity_side:
            symbol_counts[symbol][f"{liquidity_side}_fills"] += 1
        else:
            symbol_counts[symbol]["unknown_liquidity_fills"] += 1

        for dimension in REGIME_COLUMNS:
            regime = (record.get(dimension) or "UNKNOWN").strip() or "UNKNOWN"
            key = (symbol, dimension, regime)
            observed_regimes.add(key)
            counts = regime_counts[key]
            counts["rows"] += 1
            if liquidity_side:
                counts[f"{liquidity_side}_fills"] += 1
            else:
                counts["unknown_liquidity_fills"] += 1

            if regime.upper() == "UNKNOWN":
                unknown = unknown_regime_counts[(symbol, dimension)]
                unknown["rows"] += 1
                if liquidity_side:
                    unknown["known_liquidity_fills"] += 1
                continue

            daily_key = (symbol, dimension, regime, day.year)
            bucket = daily[daily_key].setdefault(day, [0, 0])
            if liquidity_side == "maker":
                bucket[0] += 1
            elif liquidity_side == "taker":
                bucket[1] += 1

    strata: list[dict[str, Any]] = []
    for symbol, dimension, regime in sorted(observed_regimes):
        if regime.upper() == "UNKNOWN":
            continue
        yearly_daily: dict[int, tuple[list[int], list[int]]] = {}
        for (bound_symbol, year), (first_day, last_day) in sorted(year_bounds.items()):
            if bound_symbol != symbol:
                continue
            year_days: list[date] = []
            current_day = first_day
            while current_day <= last_day:
                year_days.append(current_day)
                current_day += timedelta(days=1)
            by_day = daily.get((symbol, dimension, regime, year), {})
            yearly_daily[year] = (
                [by_day.get(day, [0, 0])[0] for day in year_days],
                [by_day.get(day, [0, 0])[1] for day in year_days],
            )

        # Counts come from the exact per-regime rows, avoiding any dependence
        # on grid construction for the point estimate.
        counts = regime_counts[(symbol, dimension, regime)]
        known = counts["maker_fills"] + counts["taker_fills"]
        maker = counts["maker_fills"]
        estimate = maker / known if known else None
        bootstrap, valid_reps = _bootstrap_ratio(
            yearly_daily,
            reps=reps,
            rng=_stratum_rng(seed, symbol, dimension, regime),
        )
        strata.append({
            "symbol": symbol,
            "dimension": dimension,
            "regime": regime,
            "rows": counts["rows"],
            "maker_fills": maker,
            "taker_fills": counts["taker_fills"],
            "unknown_liquidity_fills": counts["unknown_liquidity_fills"],
            "maker_share": estimate,
            "ci95_year_stratified_7d_block": _percentile_interval(bootstrap),
            "valid_replicates": valid_reps,
            "replicates_requested": reps,
        })

    return {
        "analysis": "fill-weighted maker share by as-of regime",
        "interpretation": "descriptive only; no alpha, queue, or causal inference",
        "input_sha256": input_sha256,
        "seed": seed,
        "block_days": BLOCK_DAYS,
        "bootstrap_method": (
            "non-wrapping moving blocks within each calendar year; sample each year's "
            "observed day count; aggregate year-stratified maker/(maker+taker) ratios"
        ),
        "interval_method": "empirical order-statistic 2.5th/97.5th percentiles",
        "symbol_counts": symbol_counts,
        "unknown_regime_counts": [
            {"symbol": symbol, "dimension": dimension, **counts}
            for (symbol, dimension), counts in sorted(unknown_regime_counts.items())
        ],
        "strata": strata,
    }
