"""Acquire a checksummed public Bithumb/Upbit snapshot for transfer analysis.

Only public market, candle, orderbook, and orderbook-instrument endpoints are
used. No authentication, account, order, or private exchange endpoint is used.
Raw responses are immutable within a run directory under the ignored research
data root; conflicting existing files are never overwritten.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import statistics
import urllib.request
from typing import Any


DEFAULT_ROOT = Path(
    ".external-research-data/external-bitmex-market-context-2018-2021/current-spot"
)
USER_AGENT = "bithumb-coin-trader-public-research/1.0"
MARKETS = ("KRW-BTC", "KRW-ETH")
PROVIDERS = {
    "bithumb": {
        "base": "https://api.bithumb.com/v1",
        "market_list": "market/all?isDetails=false",
        "orderbook": "orderbook?markets=KRW-BTC,KRW-ETH",
        "candles": {
            market: f"candles/minutes/60?market={market}&count=200" for market in MARKETS
        },
    },
    "upbit": {
        "base": "https://api.upbit.com/v1",
        "market_list": "market/all?is_details=false",
        "orderbook": "orderbook?markets=KRW-BTC,KRW-ETH",
        "orderbook_instruments": "orderbook/instruments?markets=KRW-BTC,KRW-ETH",
        "candles": {
            market: f"candles/minutes/60?market={market}&count=200" for market in MARKETS
        },
    },
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str) -> tuple[bytes, int, str]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        if response.status != 200:
            raise RuntimeError(f"GET {url} returned HTTP {response.status}")
        return body, response.status, response.headers.get("Date", "")


def persist_response(output_dir: Path, name: str, body: bytes) -> Path:
    path = output_dir / "raw" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != body:
            raise FileExistsError(f"Refusing to replace conflicting public response: {path}")
    else:
        path.write_bytes(body)
    return path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def candle_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: row["candle_date_time_utc"])
    timestamps = [
        datetime.fromisoformat(row["candle_date_time_utc"].replace("Z", "+00:00")).replace(
            tzinfo=UTC
        )
        for row in ordered
    ]
    closes = [float(row["trade_price"]) for row in ordered]
    highs = [float(row["high_price"]) for row in ordered]
    lows = [float(row["low_price"]) for row in ordered]
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    gaps = sum(
        max(0, round((timestamps[i] - timestamps[i - 1]).total_seconds() / 3600) - 1)
        for i in range(1, len(timestamps))
    )
    ranges = [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        / closes[i]
        for i in range(1, len(closes))
    ]

    def realized(window: int) -> float | None:
        selected = returns[-window:]
        return math.sqrt(sum(value * value for value in selected)) if selected else None

    return {
        "rows": len(ordered),
        "first_candle_start_utc": timestamps[0].isoformat() if timestamps else None,
        "last_candle_start_utc": timestamps[-1].isoformat() if timestamps else None,
        "internal_missing_hour_intervals": gaps,
        "last_close_krw": closes[-1] if closes else None,
        "realized_volatility_24h": realized(24),
        "realized_volatility_7d": realized(168),
        "median_true_range_pct": statistics.median(ranges) if ranges else None,
        "quote_turnover_krw_sum": sum(float(row.get("candle_acc_trade_price", 0)) for row in ordered),
        "timestamp_semantics": "candle_date_time_utc is treated as the UTC hour start",
    }


def cross_venue_summary(
    bithumb_rows: list[dict[str, Any]], upbit_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    def by_time(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {row["candle_date_time_utc"]: row for row in rows}

    left, right = by_time(bithumb_rows), by_time(upbit_rows)
    common = sorted(left.keys() & right.keys())
    diffs = [
        abs(float(left[key]["trade_price"]) - float(right[key]["trade_price"]))
        / ((float(left[key]["trade_price"]) + float(right[key]["trade_price"])) / 2)
        * 10_000
        for key in common
    ]
    return {
        "bithumb_only_timestamps": len(left.keys() - right.keys()),
        "upbit_only_timestamps": len(right.keys() - left.keys()),
        "aligned_timestamps": len(common),
        "absolute_close_difference_bps_median": statistics.median(diffs) if diffs else None,
        "absolute_close_difference_bps_p95": (
            sorted(diffs)[math.ceil(0.95 * len(diffs)) - 1] if diffs else None
        ),
        "interpretation": "Same KRW quote; venue price differences still include timing and market basis.",
    }


def orderbook_summary(row: dict[str, Any]) -> dict[str, Any]:
    units = row.get("orderbook_units", [])
    if not units:
        raise ValueError(f"No orderbook units for {row.get('market')}")
    best = units[0]
    bid = float(best["bid_price"])
    ask = float(best["ask_price"])
    midpoint = (bid + ask) / 2
    return {
        "market": row["market"],
        "snapshot_timestamp_ms": row.get("timestamp"),
        "bid_ask_spread_krw": ask - bid,
        "bid_ask_spread_bps": (ask - bid) / midpoint * 10_000 if midpoint else None,
        "best_bid_krw": bid,
        "best_ask_krw": ask,
        "top_5_bid_notional_krw": sum(
            float(unit["bid_price"]) * float(unit["bid_size"]) for unit in units[:5]
        ),
        "top_5_ask_notional_krw": sum(
            float(unit["ask_price"]) * float(unit["ask_size"]) for unit in units[:5]
        ),
        "returned_levels": len(units),
    }


def run(output_root: Path) -> Path:
    requested_at = datetime.now(UTC)
    run_dir = output_root / requested_at.strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir.mkdir(parents=True, exist_ok=False)
    artifacts: list[dict[str, Any]] = []
    decoded: dict[str, dict[str, Any]] = {provider: {} for provider in PROVIDERS}

    for provider, config in PROVIDERS.items():
        endpoints = {
            "market_list": (config["market_list"], "all KRW markets"),
            "orderbook": (config["orderbook"], ",".join(MARKETS)),
        }
        if "orderbook_instruments" in config:
            endpoints["orderbook_instruments"] = (
                config["orderbook_instruments"], ",".join(MARKETS)
            )
        for market, endpoint in config["candles"].items():
            key = f"candles_{market.replace('-', '_')}"
            endpoints[key] = (endpoint, market)

        for name, (endpoint, instrument) in endpoints.items():
            url = f"{config['base']}/{endpoint}"
            body, status, server_date = fetch(url)
            raw_path = persist_response(run_dir, f"{provider}_{name}", body)
            artifacts.append({
                "provider": provider,
                "endpoint_or_dataset": url,
                "instrument": instrument,
                "frequency": "snapshot" if name in {"orderbook", "market_list", "orderbook_instruments"} else "1h candles, latest 200",
                "downloaded_at_utc": datetime.now(UTC).isoformat(),
                "http_status": status,
                "server_date_header": server_date,
                "path": str(raw_path.relative_to(run_dir)),
                "sha256": sha256(body),
                "bytes": len(body),
                "known_limitations": (
                    [
                        "A current snapshot is not a historical execution sample.",
                        "This candle endpoint returns at most the latest 200 hourly observations in this run.",
                    ]
                    if name.startswith("candles_")
                    else [
                        "A point-in-time public response is not a historical execution sample.",
                        "No private account fee, borrow, or order-permission data are requested.",
                    ]
                ),
            })
            decoded[provider][name] = read_json(raw_path)

    for provider in PROVIDERS:
        markets = {row["market"] for row in decoded[provider]["market_list"]}
        absent = sorted(set(MARKETS) - markets)
        if absent:
            raise ValueError(f"Requested public KRW market(s) unavailable on {provider}: {absent}")

    market_context: dict[str, Any] = {}
    for market in MARKETS:
        candle_key = f"candles_{market.replace('-', '_')}"
        market_context[market] = {
            "venues": {},
            "cross_venue_candles": cross_venue_summary(
                decoded["bithumb"][candle_key], decoded["upbit"][candle_key]
            ),
        }
        for provider in PROVIDERS:
            book = next(
                row for row in decoded[provider]["orderbook"] if row.get("market") == market
            )
            market_context[market]["venues"][provider] = {
                "hourly_candles": candle_summary(decoded[provider][candle_key]),
                "orderbook": orderbook_summary(book),
            }

    upbit_tick_rows = decoded["upbit"].get("orderbook_instruments", [])
    market_context["upbit_orderbook_policy"] = {
        row.get("market"): {
            "tick_size": row.get("tick_size"),
            "supported_levels": row.get("supported_levels"),
        }
        for row in upbit_tick_rows
        if row.get("market") in MARKETS
    }
    report = {
        "requested_at_utc": requested_at.isoformat(),
        "scope": "public-only point-in-time BTC/ETH KRW spot comparison",
        "private_endpoints_used": False,
        "markets": market_context,
        "source_artifacts": artifacts,
        "limitations": [
            "Point-in-time books and a maximum of 200 recent hourly candles do not establish long-run venue behavior.",
            "No participant identity, order queue, user-specific fee, borrowing, or complete lot-size data are available from these public responses.",
            "The historical BitMEX context is 2018-2021; these spot snapshots are a dated descriptive comparison, not a causal transfer test.",
        ],
    }
    (run_dir / "derived").mkdir()
    (run_dir / "derived" / "transfer-snapshot-summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (run_dir / "source-manifest.json").write_text(
        json.dumps({"artifacts": artifacts}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    run_dir = run(args.output_root)
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
