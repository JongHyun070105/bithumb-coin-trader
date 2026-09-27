"""Build causal hourly market features and audit a full execution as-of join.

BitMEX buckets are the canonical execution-venue context. Binance spot klines
provide an independent price-series cross-check. All outputs remain ignored
under .external-research-data.
"""

from __future__ import annotations

import argparse
import bisect
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import statistics
import zipfile
from typing import Any, Iterable


DATASET_ROOT = Path(".external-research-data/external-bitmex-trader-2018-2021")
CONTEXT_ROOT = Path(".external-research-data/external-bitmex-market-context-2018-2021")
SYMBOLS = {"XBTUSD": "BTCUSDT", "ETHUSD": "ETHUSDT"}
HOUR = timedelta(hours=1)


def utc_datetime(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def execution_datetime(row: dict[str, str]) -> datetime:
    raw = row.get("transacttime") or row.get("timestamp") or row.get("date")
    if not raw:
        raise ValueError("Trade row has no execution timestamp")
    return utc_datetime(raw)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_immutable(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    if path.exists():
        existing = sha256_file(path)
        if existing != digest:
            raise FileExistsError(f"Refusing to replace existing research artifact {path}: {existing} != {digest}")
        return digest
    temp = path.with_name(path.name + ".part")
    if temp.exists():
        raise FileExistsError(f"Partial output exists; inspect before resuming: {temp}")
    temp.write_bytes(content)
    temp.replace(path)
    return digest


def load_bitmex_bars(root: Path, symbol: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted((root / "bitmex" / symbol / "trade_bucketed").glob("part-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        records.extend(payload["records"])
    records.sort(key=lambda row: utc_datetime(row["timestamp"]))
    seen: set[datetime] = set()
    result: list[dict[str, Any]] = []
    for row in records:
        timestamp = utc_datetime(row["timestamp"])
        if row.get("symbol") != symbol:
            raise ValueError(f"Wrong symbol in {symbol} source row")
        if timestamp in seen:
            raise ValueError(f"Duplicate BitMEX {symbol} bucket at {timestamp.isoformat()}")
        seen.add(timestamp)
        result.append({
            "timestamp": timestamp,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "foreign_notional": float(row["foreignNotional"]),
            "trades": int(row["trades"]),
            "volume_unit": "contracts",
        })
    if not result:
        raise FileNotFoundError(f"No acquired BitMEX hourly bars for {symbol} under {root}")
    return result


def load_binance_bars(root: Path, symbol: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for archive_path in sorted((root / "binance" / symbol / "1h").glob("*.zip")):
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.namelist():
                with archive.open(member) as binary:
                    reader = csv.reader(io.TextIOWrapper(binary, encoding="utf-8"))
                    for row in reader:
                        if not row:
                            continue
                        open_time = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc)
                        # Binance timestamps identify interval starts. Convert to
                        # end-exclusive hourly timestamps for proper bucket alignment.
                        result.append({
                            "timestamp": open_time + HOUR,
                            "open": float(row[1]),
                            "high": float(row[2]),
                            "low": float(row[3]),
                            "close": float(row[4]),
                            "base_volume": float(row[5]),
                            "quote_volume": float(row[7]),
                            "trades": int(row[8]),
                            "volume_unit": "base_asset_and_quote_asset",
                        })
    result.sort(key=lambda bar: bar["timestamp"])
    deduplicated: list[dict[str, Any]] = []
    prior: datetime | None = None
    for row in result:
        if row["timestamp"] == prior:
            raise ValueError(f"Duplicate Binance {symbol} candle ending {prior}")
        prior = row["timestamp"]
        deduplicated.append(row)
    if not deduplicated:
        raise FileNotFoundError(f"No acquired Binance hourly klines for {symbol} under {root}")
    return deduplicated


def load_funding(root: Path, symbol: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted((root / "bitmex" / symbol / "funding").glob("part-*.json")):
        result.extend(json.loads(path.read_text(encoding="utf-8"))["records"])
    result.sort(key=lambda row: utc_datetime(row["timestamp"]))
    unique: dict[datetime, dict[str, Any]] = {}
    for row in result:
        timestamp = utc_datetime(row["timestamp"])
        if timestamp in unique and unique[timestamp] != row:
            raise ValueError(f"Conflicting duplicate funding row for {symbol} at {timestamp}")
        unique[timestamp] = row
    return [dict(row, _parsed_timestamp=ts) for ts, row in sorted(unique.items())]


def quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(probability * len(ordered)) - 1))
    return ordered[index]


def add_features(bars: list[dict[str, Any]], funding_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    funding_times = [row["_parsed_timestamp"] for row in funding_rows]
    feature_rows: list[dict[str, Any]] = []
    returns: list[float | None] = []
    true_ranges: list[float | None] = []
    for i, bar in enumerate(bars):
        prior = bars[i - 1] if i else None
        contiguous = prior is not None and bar["timestamp"] - prior["timestamp"] == HOUR
        ret = None
        if prior is not None and contiguous and prior["close"]:
            ret = bar["close"] / prior["close"] - 1.0
        returns.append(ret)
        if prior is not None and contiguous:
            true_ranges.append(max(
                bar["high"] - bar["low"],
                abs(bar["high"] - prior["close"]),
                abs(bar["low"] - prior["close"]),
            ))
        else:
            true_ranges.append(None)

        # Feature windows contain only this completed bar and strictly older bars.
        recent_returns = returns[max(0, i - 23):i + 1]
        valid_returns = [x for x in recent_returns if x is not None]
        rv_24h = math.sqrt(sum(x * x for x in valid_returns)) if len(valid_returns) == 24 else None
        atr_window = true_ranges[max(0, i - 13):i + 1]
        atr_14 = (sum(x for x in atr_window if x is not None) / 14.0
                  if len(atr_window) == 14 and all(x is not None for x in atr_window) else None)
        close_24_prior = bars[i - 24]["close"] if i >= 24 and all(
            bars[j]["timestamp"] - bars[j - 1]["timestamp"] == HOUR for j in range(i - 23, i + 1)
        ) else None
        ret_24h = bar["close"] / close_24_prior - 1.0 if close_24_prior else None

        prior_rv = [feature_rows[j]["realized_vol_24h"] for j in range(max(0, i - 168), i)
                    if feature_rows[j]["realized_vol_24h"] is not None]
        rv_threshold = statistics.median(prior_rv) if len(prior_rv) >= 120 else None
        prior_abs_returns = [abs(feature_rows[j]["return_24h"])
                             for j in range(max(0, i - 720), i)
                             if feature_rows[j]["return_24h"] is not None]
        trend_threshold = quantile(prior_abs_returns, 0.60) if len(prior_abs_returns) >= 500 else None
        prior_volumes = [bars[j]["volume"] for j in range(max(0, i - 168), i)]
        volume_threshold = statistics.median(prior_volumes) if len(prior_volumes) >= 120 else None

        fund_index = bisect.bisect_right(funding_times, bar["timestamp"]) - 1
        funding = funding_rows[fund_index] if fund_index >= 0 else None
        fund_rate_raw = funding.get("fundingRate") if funding else None
        fund_rate = float(fund_rate_raw) if fund_rate_raw is not None else None
        funding_timestamp = funding["_parsed_timestamp"].isoformat() if funding else None

        feature = dict(bar)
        feature.update({
            "return_1h": ret,
            "return_24h": ret_24h,
            "realized_vol_24h": rv_24h,
            "atr_14_pct": atr_14 / bar["close"] if atr_14 is not None and bar["close"] else None,
            "volume_z_24h": None,
            "funding_rate": fund_rate,
            "funding_timestamp": funding_timestamp,
            "volatility_regime": (
                "HIGH_VOL" if rv_24h >= rv_threshold else "LOW_VOL"
            ) if rv_24h is not None and rv_threshold is not None else "UNKNOWN",
            "trend_regime": (
                "TREND_UP" if ret_24h > 0 else "TREND_DOWN"
            ) if ret_24h is not None and trend_threshold is not None and abs(ret_24h) >= trend_threshold
              else ("RANGE" if ret_24h is not None and trend_threshold is not None else "UNKNOWN"),
            "volume_regime": (
                "HIGH_VOLUME" if bar["volume"] >= volume_threshold else "LOW_VOLUME"
            ) if volume_threshold is not None else "UNKNOWN",
            "funding_regime": (
                "POSITIVE_FUNDING" if fund_rate > 0 else "NEGATIVE_FUNDING" if fund_rate < 0 else "ZERO_FUNDING"
            ) if fund_rate is not None else "UNKNOWN",
        })
        feature_rows.append(feature)

    # Volume z-score uses trailing 24 complete prior bars, excluding current volume.
    for i, row in enumerate(feature_rows):
        window = bars[max(0, i - 24):i]
        if len(window) == 24 and all(window[j]["timestamp"] - window[j - 1]["timestamp"] == HOUR for j in range(1, 24)):
            values = [item["volume"] for item in window]
            mean = statistics.fmean(values)
            std = statistics.pstdev(values)
            row["volume_z_24h"] = (bars[i]["volume"] - mean) / std if std > 0 else None
    return feature_rows


def gap_summary(rows: list[dict[str, Any]], start: datetime, end_exclusive: datetime) -> dict[str, Any]:
    timestamps = [row["timestamp"] for row in rows]
    in_scope = [ts for ts in timestamps if start <= ts < end_exclusive]
    expected = int((end_exclusive - start).total_seconds() // 3600)
    seen = len(in_scope)
    internal_gaps = sum(max(0, int((right - left).total_seconds() // 3600) - 1)
                        for left, right in zip(in_scope, in_scope[1:]))
    first = min(in_scope) if in_scope else None
    last = max(in_scope) if in_scope else None
    return {
        "requested_start_utc": start.isoformat(),
        "requested_end_exclusive_utc": end_exclusive.isoformat(),
        "actual_first_timestamp_utc": first.isoformat() if first else None,
        "actual_last_timestamp_utc": last.isoformat() if last else None,
        "rows_in_scope": seen,
        "expected_hourly_intervals": expected,
        "missing_intervals_inside_observed_coverage": internal_gaps,
        "missing_vs_full_requested_grid": max(0, expected - seen),
        "leading_missing_intervals": int((first - start).total_seconds() // 3600) if first and first > start else 0,
        "trailing_missing_intervals": int((end_exclusive - last).total_seconds() // 3600) - 1 if last and last < end_exclusive - HOUR else 0,
    }


def compare_sources(bitmex: list[dict[str, Any]], binance: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    binance_by_time = {row["timestamp"]: row for row in binance}
    bitmex_by_time = {row["timestamp"]: row for row in bitmex}
    matched: list[tuple[dict[str, Any], dict[str, Any]]] = [
        (row, binance_by_time[row["timestamp"]]) for row in bitmex
        if row["timestamp"] in binance_by_time
    ]
    disagreements: dict[str, list[float]] = {key: [] for key in ("open", "high", "low", "close")}
    for left, right in matched:
        for field in disagreements:
            if right[field]:
                disagreements[field].append(abs(left[field] / right[field] - 1.0) * 10_000)
    volume_ratios = [left["foreign_notional"] / right["quote_volume"]
                     for left, right in matched
                     if left.get("foreign_notional") is not None and right["quote_volume"] > 0]
    volume_abs_differences_pct = [abs(ratio - 1.0) * 100.0 for ratio in volume_ratios]
    prices: dict[str, Any] = {}
    for field, values in disagreements.items():
        prices[field] = {
            "absolute_difference_bps_median": statistics.median(values) if values else None,
            "absolute_difference_bps_p95": quantile(values, 0.95),
            "absolute_difference_bps_max": max(values) if values else None,
        }
    start = datetime(2018, 3, 1, tzinfo=timezone.utc)
    end = datetime(2022, 1, 1, tzinfo=timezone.utc)
    bitmex_coverage = gap_summary(bitmex, start, end)
    binance_coverage = gap_summary(binance, start, end)
    return {
        "execution_instrument": symbol,
        "independent_spot_instrument": "BTCUSDT" if symbol == "XBTUSD" else "ETHUSDT",
        "timestamp_convention": (
            "BitMEX hourly bucket timestamp is treated as interval end; Binance open-time is shifted by one hour to the same interval end."
        ),
        "bitmex_coverage": bitmex_coverage,
        "binance_coverage": binance_coverage,
        "bitmex_timestamps_missing_from_binance": len(set(bitmex_by_time) - set(binance_by_time)),
        "binance_timestamps_missing_from_bitmex": len(set(binance_by_time) - set(bitmex_by_time)),
        "aligned_timestamp_count": len(matched),
        "ohlc_disagreement": prices,
        "volume_difference": {
            "status": "MEASURED_AS_CROSS_VENUE_USD_VS_USDT_NOTIONAL_PROXY",
            "bitmex_field": "foreignNotional (USD-equivalent per BitMEX bucket)",
            "binance_field": "quote_asset_volume (USDT per Binance spot candle)",
            "aligned_rows": len(volume_ratios),
            "median_bitmex_to_binance_ratio": statistics.median(volume_ratios) if volume_ratios else None,
            "median_absolute_difference_pct": statistics.median(volume_abs_differences_pct) if volume_abs_differences_pct else None,
            "p95_absolute_difference_pct": quantile(volume_abs_differences_pct, 0.95),
            "max_absolute_difference_pct": max(volume_abs_differences_pct) if volume_abs_differences_pct else None,
            "limitations": [
                "These are venue-specific turnover values, not expected to match; USDT is only an approximate USD proxy.",
                "BitMEX foreignNotional contract conversion is instrument-specific and still requires historical contract-spec provenance.",
            ],
        },
        "canonical_selection": "BitMEX for execution-aligned context; Binance is an independent price cross-check only.",
        "fallback": "Binance spot series if BitMEX API coverage becomes unavailable, with venue basis and interval shift retained explicitly.",
    }


def percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None}
    return {"p50": statistics.median(values), "p95": quantile(values, 0.95)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-root", type=Path, default=DATASET_ROOT / "raw")
    parser.add_argument("--context-root", type=Path, default=CONTEXT_ROOT)
    parser.add_argument("--source-audit-only", action="store_true")
    args = parser.parse_args()
    out_dir = args.context_root / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)

    context: dict[str, list[dict[str, Any]]] = {}
    source_audit: dict[str, Any] = {}
    for bitmex_symbol, binance_symbol in SYMBOLS.items():
        bitmex = load_bitmex_bars(args.context_root, bitmex_symbol)
        binance = load_binance_bars(args.context_root, binance_symbol)
        funding = load_funding(args.context_root, bitmex_symbol)
        feature_rows = add_features(bitmex, funding)
        context[bitmex_symbol] = feature_rows
        source_audit[bitmex_symbol] = compare_sources(bitmex, binance, bitmex_symbol)
    source_audit_path = out_dir / "market-source-cross-check-v2.json"
    source_audit_bytes = (json.dumps(source_audit, indent=2, sort_keys=True) + "\n").encode()
    write_immutable(source_audit_path, source_audit_bytes)
    if args.source_audit_only:
        print(f"SOURCE_AUDIT={source_audit_path}")
        print(json.dumps(source_audit, indent=2, sort_keys=True))
        return 0

    context_times = {symbol: [row["timestamp"] for row in rows] for symbol, rows in context.items()}
    execution_files = sorted(args.execution_root.glob("aoa-execution-*.csv"))
    if not execution_files:
        raise FileNotFoundError(f"No source execution CSVs under {args.execution_root}")

    join_path = out_dir / "joined-trade-executions.csv.gz"
    temp_path = join_path.with_name(join_path.name + ".part")
    if temp_path.exists() or join_path.exists():
        raise FileExistsError(f"Refusing to overwrite an existing join artifact: {join_path} / {temp_path}")
    joined_count = 0
    unmatched_count = 0
    trade_count = 0
    future_leakage_count = 0
    ages: list[float] = []
    per_symbol: dict[str, dict[str, int]] = {}
    csv_fields = [
        "execution_file", "execution_id", "execution_timestamp_utc", "symbol", "side",
        "lastqty", "lastpx", "lastliquidityind", "exectype", "context_symbol",
        "context_timestamp_utc", "context_age_seconds", "context_open", "context_high",
        "context_low", "context_close", "context_volume_contracts", "context_trade_count",
        "return_1h", "return_24h", "realized_vol_24h", "atr_14_pct", "volume_z_24h",
        "funding_timestamp_utc", "funding_rate", "volatility_regime", "trend_regime",
        "volume_regime", "funding_regime",
    ]

    with temp_path.open("wb") as raw_out:
        with gzip.GzipFile(fileobj=raw_out, mode="wb", compresslevel=6, mtime=0) as zipped:
            text_out = io.TextIOWrapper(zipped, encoding="utf-8", newline="")
            writer = csv.DictWriter(text_out, fieldnames=csv_fields, extrasaction="ignore")
            writer.writeheader()
            for execution_path in execution_files:
                with execution_path.open("r", newline="", encoding="utf-8-sig") as stream:
                    for row in csv.DictReader(stream):
                        if (row.get("exectype") or "").strip() != "Trade":
                            continue
                        trade_count += 1
                        symbol = (row.get("symbol") or "").strip()
                        stats = per_symbol.setdefault(symbol, {"trades": 0, "joined": 0, "unmatched": 0})
                        stats["trades"] += 1
                        ts = execution_datetime(row)
                        market_rows = context.get(symbol)
                        match: dict[str, Any] | None = None
                        age: float | None = None
                        if market_rows:
                            times = context_times[symbol]
                            index = bisect.bisect_left(times, ts) - 1
                            if index >= 0:
                                candidate = market_rows[index]
                                if candidate["timestamp"] >= ts:
                                    future_leakage_count += 1
                                else:
                                    match = candidate
                                    age = (ts - candidate["timestamp"]).total_seconds()
                        if match is None:
                            unmatched_count += 1
                            stats["unmatched"] += 1
                        else:
                            joined_count += 1
                            stats["joined"] += 1
                            ages.append(age if age is not None else 0.0)
                        writer.writerow({
                            "execution_file": execution_path.name,
                            "execution_id": row.get("execid", ""),
                            "execution_timestamp_utc": ts.isoformat(),
                            "symbol": symbol,
                            "side": row.get("side", ""),
                            "lastqty": row.get("lastqty", ""),
                            "lastpx": row.get("lastpx", ""),
                            "lastliquidityind": row.get("lastliquidityind", ""),
                            "exectype": row.get("exectype", ""),
                            "context_symbol": symbol if match else "",
                            "context_timestamp_utc": match["timestamp"].isoformat() if match else "",
                            "context_age_seconds": age if age is not None else "",
                            "context_open": match["open"] if match else "",
                            "context_high": match["high"] if match else "",
                            "context_low": match["low"] if match else "",
                            "context_close": match["close"] if match else "",
                            "context_volume_contracts": match["volume"] if match else "",
                            "context_trade_count": match["trades"] if match else "",
                            "return_1h": match["return_1h"] if match and match["return_1h"] is not None else "",
                            "return_24h": match["return_24h"] if match and match["return_24h"] is not None else "",
                            "realized_vol_24h": match["realized_vol_24h"] if match and match["realized_vol_24h"] is not None else "",
                            "atr_14_pct": match["atr_14_pct"] if match and match["atr_14_pct"] is not None else "",
                            "volume_z_24h": match["volume_z_24h"] if match and match["volume_z_24h"] is not None else "",
                            "funding_timestamp_utc": match["funding_timestamp"] if match and match["funding_timestamp"] else "",
                            "funding_rate": match["funding_rate"] if match and match["funding_rate"] is not None else "",
                            "volatility_regime": match["volatility_regime"] if match else "UNKNOWN",
                            "trend_regime": match["trend_regime"] if match else "UNKNOWN",
                            "volume_regime": match["volume_regime"] if match else "UNKNOWN",
                            "funding_regime": match["funding_regime"] if match else "UNKNOWN",
                        })
            text_out.flush()
            text_out.detach()
    if future_leakage_count:
        temp_path.unlink(missing_ok=True)
        raise AssertionError(f"FUTURE_LEAKAGE_COUNT={future_leakage_count}; refusing output")
    temp_path.replace(join_path)

    join_audit = {
        "execution_scope": "Trade rows only; funding/settlement rows are not fill observations",
        "context_source": "BitMEX Public REST API 1h buckets; Binance spot is independent price cross-check",
        "timestamp_policy": "strictly earlier completed bar: context_timestamp < execution_timestamp; equal-time buckets excluded",
        "ROWS_JOINED": joined_count,
        "ROWS_UNMATCHED": unmatched_count,
        "TRADE_EXECUTIONS": trade_count,
        "MAX_CONTEXT_AGE_SECONDS": max(ages) if ages else None,
        "CONTEXT_AGE_SECONDS_P50_P95": percentiles(ages),
        "FUTURE_LEAKAGE_COUNT": future_leakage_count,
        "BY_EXECUTION_SYMBOL": per_symbol,
        "join_output": join_path.as_posix(),
        "join_sha256": sha256_file(join_path),
        "join_size_bytes": join_path.stat().st_size,
        "feature_methodology": {
            "return_1h": "current completed close / prior contiguous hourly close - 1",
            "return_24h": "current completed close / close 24 contiguous bars earlier - 1",
            "realized_vol_24h": "sqrt(sum of squared 24 hourly simple returns), requires 24 contiguous returns",
            "atr_14_pct": "14-bar mean true range / current close, requires contiguous hourly history",
            "volume_z_24h": "current bucket contracts vs prior 24 complete hourly bucket mean and population SD",
            "volatility_regime": "current 24h realized vol vs trailing 168 observed prior values median; >=120 prior values required",
            "trend_regime": "signed 24h return vs prior 720 values 60th percentile of absolute returns; >=500 prior values required",
            "volume_regime": "current contract volume vs trailing 168 prior buckets median; >=120 prior values required",
            "funding_regime": "latest BitMEX fundingRate at or before completed bar timestamp",
            "future_information": "regime thresholds use prior values only; execution join strictly excludes same-time bars",
        },
        "unavailable_fields": ["historical spread", "order book depth", "queue position", "liquidations", "cross-exchange order-flow imbalance"],
        "starting_inventory_limitation": "No independent position snapshot exists in this dataset; entry/add/reduce/exit/flip intent is not directly identified by this join.",
    }
    audit_path = out_dir / "execution-context-join-audit.json"
    write_immutable(audit_path, (json.dumps(join_audit, indent=2, sort_keys=True) + "\n").encode())
    print(json.dumps(join_audit, indent=2, sort_keys=True))
    print(f"SOURCE_AUDIT={source_audit_path}")
    print(f"JOIN_AUDIT={audit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
