"""Public Bithumb candle data access, aggregation, and CSV persistence.

This module deliberately supports public market data only.  It never reads API
credentials and only issues bounded ``GET`` requests.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .models import Candle


DAILY_CANDLES_URL = "https://api.bithumb.com/v1/candles/days"
MINUTE_CANDLES_URL = "https://api.bithumb.com/v1/candles/minutes/{unit}"
SUPPORTED_MINUTE_UNITS = frozenset({1, 3, 5, 10, 15, 30, 60, 240})
MAX_CANDLES_PER_REQUEST = 200
MAX_FETCH_PAGES = 1_000
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
CSV_FIELDS = ("market", "timestamp", "open", "high", "low", "close", "volume")
_MARKET_RE = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+$")
KST = ZoneInfo("Asia/Seoul")


class DataError(ValueError):
    """Raised when market data is invalid or cannot be obtained safely."""


def _validate_krw_market(market: str) -> None:
    if not _MARKET_RE.fullmatch(market) or not market.startswith("KRW-"):
        raise DataError("market must be a Bithumb KRW market such as 'KRW-BTC'")


Transport = Callable[[Request, float], bytes]


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    """Stable identity and coverage metadata for a chronological dataset."""

    schema_version: int
    market: str | None
    candle_count: int
    start_at: datetime | None
    end_at: datetime | None
    sha256: str


def _default_transport(request: Request, timeout: float) -> bytes:
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - HTTPS is checked below.
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_RESPONSE_BYTES:
                raise DataError("Bithumb response is too large")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise DataError(f"Bithumb returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise DataError(f"Bithumb request failed: {exc}") from exc
    if len(payload) > MAX_RESPONSE_BYTES:
        raise DataError("Bithumb response is too large")
    return payload


def _parse_api_candle(raw: Mapping[str, Any], expected_market: str) -> Candle:
    required = {
        "market",
        "candle_date_time_utc",
        "opening_price",
        "high_price",
        "low_price",
        "trade_price",
        "candle_acc_trade_volume",
    }
    missing = required.difference(raw)
    if missing:
        raise DataError(f"Bithumb candle is missing fields: {', '.join(sorted(missing))}")
    if raw["market"] != expected_market:
        raise DataError("Bithumb returned a candle for a different market")
    try:
        timestamp_text = str(raw["candle_date_time_utc"])
        timestamp = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        else:
            timestamp = timestamp.astimezone(timezone.utc)
        return Candle(
            market=expected_market,
            timestamp=timestamp,
            open=float(raw["opening_price"]),
            high=float(raw["high_price"]),
            low=float(raw["low_price"]),
            close=float(raw["trade_price"]),
            volume=float(raw["candle_acc_trade_volume"]),
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataError("Bithumb candle contains an invalid value") from exc


def _decode_page(payload: bytes, expected_market: str) -> list[Candle]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataError("Bithumb returned invalid JSON") from exc
    if not isinstance(decoded, list):
        raise DataError("Bithumb candle response must be a JSON array")
    candles: list[Candle] = []
    for item in decoded:
        if not isinstance(item, dict):
            raise DataError("Bithumb candle response contains a non-object item")
        candles.append(_parse_api_candle(item, expected_market))
    return candles


def fetch_daily_candles(
    market: str,
    count: int,
    *,
    to: datetime | None = None,
    timeout: float = 10.0,
    transport: Transport | None = None,
    endpoint: str = DAILY_CANDLES_URL,
    include_incomplete: bool = False,
    as_of: datetime | None = None,
) -> list[Candle]:
    """Fetch up to ``count`` daily candles, oldest first.

    Bithumb limits each response to 200 candles, so larger requests are paged
    backwards with the documented exclusive ``to`` cursor.  ``transport`` is a
    small injection point for deterministic tests; production calls use
    :func:`urllib.request.urlopen` with the supplied timeout.
    """

    return _fetch_candles(
        market,
        count,
        interval=timedelta(days=1),
        to=to,
        timeout=timeout,
        transport=transport,
        endpoint=endpoint,
        include_incomplete=include_incomplete,
        as_of=as_of,
        daily=True,
    )


def fetch_minute_candles(
    market: str,
    unit: int,
    count: int,
    *,
    to: datetime | None = None,
    timeout: float = 10.0,
    transport: Transport | None = None,
    endpoint: str | None = None,
    include_incomplete: bool = False,
    as_of: datetime | None = None,
) -> list[Candle]:
    """Fetch completed Bithumb minute candles, oldest first.

    ``unit`` uses Bithumb's supported minute units.  By default a candle is
    returned only after its complete interval has elapsed.  ``as_of`` makes
    that decision deterministic for historical research; set
    ``include_incomplete`` only when an explicitly partial bar is desired.
    """

    if isinstance(unit, bool) or not isinstance(unit, int) or unit not in SUPPORTED_MINUTE_UNITS:
        supported = ", ".join(str(value) for value in sorted(SUPPORTED_MINUTE_UNITS))
        raise DataError(f"minute unit must be one of: {supported}")
    resolved_endpoint = endpoint or MINUTE_CANDLES_URL.format(unit=unit)
    return _fetch_candles(
        market,
        count,
        interval=timedelta(minutes=unit),
        to=to,
        timeout=timeout,
        transport=transport,
        endpoint=resolved_endpoint,
        include_incomplete=include_incomplete,
        as_of=as_of,
        daily=False,
    )


def _fetch_candles(
    market: str,
    count: int,
    *,
    interval: timedelta,
    to: datetime | None,
    timeout: float,
    transport: Transport | None,
    endpoint: str,
    include_incomplete: bool,
    as_of: datetime | None,
    daily: bool,
) -> list[Candle]:
    """Shared bounded paginator for Bithumb's daily and minute endpoints."""

    _validate_krw_market(market)
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise DataError("count must be a positive integer")
    if count > MAX_CANDLES_PER_REQUEST * MAX_FETCH_PAGES:
        raise DataError("count exceeds the safe pagination limit")
    if not math.isfinite(timeout) or timeout <= 0:
        raise DataError("timeout must be a positive finite number")
    parsed_endpoint = urlparse(endpoint)
    if parsed_endpoint.scheme != "https" or not parsed_endpoint.netloc:
        raise DataError("candle endpoint must be an absolute HTTPS URL")
    if to is not None and (to.tzinfo is None or to.utcoffset() is None):
        raise DataError("to must be timezone-aware")
    if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
        raise DataError("as_of must be timezone-aware")

    get = transport or _default_transport
    observed_at = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cursor = to
    if not include_incomplete and as_of is not None:
        observed_kst = observed_at.astimezone(KST)
        if daily:
            completed_boundary = observed_kst.replace(
                hour=0, minute=0, second=0, microsecond=0
            ).astimezone(timezone.utc)
        else:
            interval_minutes = int(interval.total_seconds() // 60)
            minutes_since_midnight = observed_kst.hour * 60 + observed_kst.minute
            boundary_minutes = (minutes_since_midnight // interval_minutes) * interval_minutes
            completed_boundary = observed_kst.replace(
                hour=0, minute=0, second=0, microsecond=0
            ) + timedelta(minutes=boundary_minutes)
            completed_boundary = completed_boundary.astimezone(timezone.utc)
        if cursor is None or completed_boundary < cursor.astimezone(timezone.utc):
            cursor = completed_boundary
    by_timestamp: dict[datetime, Candle] = {}

    for _ in range(MAX_FETCH_PAGES):
        remaining = count - len(by_timestamp)
        if remaining <= 0:
            break
        params: dict[str, str | int] = {
            "market": market,
            "count": min(MAX_CANDLES_PER_REQUEST, remaining),
        }
        if cursor is not None:
            # The API documents `to` as a naive KST timestamp and excludes the
            # candle at that exact time.
            cursor_for_api = cursor.astimezone(KST)
            params["to"] = cursor_for_api.replace(tzinfo=None).isoformat(timespec="seconds")
        request = Request(
            f"{endpoint}?{urlencode(params)}",
            headers={"Accept": "application/json", "User-Agent": "bithumb-coin-trader/1"},
            method="GET",
        )
        page = _decode_page(get(request, timeout), market)
        if not page:
            break
        prior_size = len(by_timestamp)
        complete_page = [
            candle
            for candle in page
            if include_incomplete or candle.timestamp + interval <= observed_at
        ]
        for candle in complete_page:
            existing = by_timestamp.get(candle.timestamp)
            if existing is not None and existing != candle:
                raise DataError("Bithumb returned conflicting duplicate candles")
            by_timestamp[candle.timestamp] = candle
        if complete_page and len(by_timestamp) == prior_size:
            break
        oldest = min(page, key=lambda candle: candle.timestamp).timestamp
        if cursor is not None and oldest >= cursor.astimezone(timezone.utc):
            raise DataError("Bithumb pagination did not move backwards")
        cursor = oldest
        if len(page) < int(params["count"]):
            break

    return sorted(by_timestamp.values(), key=lambda candle: candle.timestamp)[-count:]


def aggregate_candles(
    candles: Sequence[Candle],
    source_minutes: int,
    target_minutes: int,
    *,
    as_of: datetime | None = None,
) -> list[Candle]:
    """Aggregate complete, consecutive minute candles on KST boundaries.

    The helper is intentionally strict for research reproducibility.  Input
    timestamps must be chronological and aligned to ``source_minutes`` in KST;
    incomplete or gapped target buckets are omitted.  Supported research
    targets are 30-minute candles aggregated to 1 hour, 4 hours, or a completed
    KST day (1440 minutes).
    """

    if source_minutes != 30 or target_minutes not in {60, 240, 1440}:
        raise DataError("aggregation supports 30-minute candles to 60, 240, or 1440 minutes")
    if target_minutes % source_minutes != 0:
        raise DataError("target interval must be a multiple of the source interval")
    if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
        raise DataError("as_of must be timezone-aware")
    if not candles:
        return []

    market = candles[0].market
    _validate_krw_market(market)
    previous: datetime | None = None
    for candle in candles:
        if candle.market != market:
            raise DataError("aggregation candles must belong to one market")
        timestamp = candle.timestamp.astimezone(timezone.utc)
        if previous is not None and timestamp <= previous:
            raise DataError("aggregation candles must be strictly chronological")
        local = timestamp.astimezone(KST)
        if local.second or local.microsecond or local.minute % source_minutes:
            raise DataError("candle timestamp is not aligned to the source interval")
        previous = timestamp

    observed_at = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expected_count = target_minutes // source_minutes
    buckets: dict[datetime, list[Candle]] = {}
    for candle in candles:
        local = candle.timestamp.astimezone(KST)
        minutes_since_midnight = local.hour * 60 + local.minute
        bucket_minutes = (minutes_since_midnight // target_minutes) * target_minutes
        bucket_start = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            minutes=bucket_minutes
        )
        buckets.setdefault(bucket_start.astimezone(timezone.utc), []).append(candle)

    aggregated: list[Candle] = []
    source_delta = timedelta(minutes=source_minutes)
    target_delta = timedelta(minutes=target_minutes)
    for bucket_start in sorted(buckets):
        bucket = buckets[bucket_start]
        expected_timestamps = [bucket_start + source_delta * index for index in range(expected_count)]
        actual_timestamps = [candle.timestamp.astimezone(timezone.utc) for candle in bucket]
        if actual_timestamps != expected_timestamps or bucket_start + target_delta > observed_at:
            continue
        aggregated.append(
            Candle(
                market=market,
                timestamp=bucket_start,
                open=bucket[0].open,
                high=max(candle.high for candle in bucket),
                low=min(candle.low for candle in bucket),
                close=bucket[-1].close,
                volume=sum(candle.volume for candle in bucket),
            )
        )
    return aggregated


def aggregate_minute_candles(
    candles: Sequence[Candle],
    source_unit: int,
    target_unit: int,
    *,
    as_of: datetime | None = None,
) -> list[Candle]:
    """Named compatibility wrapper around :func:`aggregate_candles`."""

    return aggregate_candles(candles, source_unit, target_unit, as_of=as_of)


def save_candles_csv(path: str | Path, candles: Iterable[Candle]) -> None:
    """Write candles to exactly ``path``; parent directories are not created."""

    destination = Path(path)
    candle_rows = tuple(candles)
    for candle in candle_rows:
        _validate_krw_market(candle.market)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for candle in candle_rows:
            writer.writerow(
                {
                    "market": candle.market,
                    "timestamp": candle.timestamp.astimezone(timezone.utc).isoformat(),
                    "open": repr(candle.open),
                    "high": repr(candle.high),
                    "low": repr(candle.low),
                    "close": repr(candle.close),
                    "volume": repr(candle.volume),
                }
            )


def load_candles_csv(path: str | Path) -> list[Candle]:
    """Load and validate normalized candles from a CSV file."""

    source = Path(path)
    with source.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(CSV_FIELDS):
            raise DataError(f"CSV header must be: {', '.join(CSV_FIELDS)}")
        candles: list[Candle] = []
        for line_number, row in enumerate(reader, start=2):
            try:
                _validate_krw_market(row["market"])
                timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                candles.append(
                    Candle(
                        market=row["market"],
                        timestamp=timestamp,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                    )
                )
            except (DataError, TypeError, ValueError, OverflowError) as exc:
                raise DataError(f"invalid candle on CSV line {line_number}: {exc}") from exc
    if len({candle.timestamp for candle in candles}) != len(candles):
        raise DataError("CSV contains duplicate candle timestamps")
    return sorted(candles, key=lambda candle: candle.timestamp)


def closes(candles: Sequence[Candle]) -> list[float]:
    """Return closing prices for simple strategy integrations."""

    return [candle.close for candle in candles]


def dataset_manifest(candles: Sequence[Candle]) -> DatasetManifest:
    """Return a deterministic SHA-256 manifest without writing any files."""

    if any(candles[index].timestamp >= candles[index + 1].timestamp for index in range(len(candles) - 1)):
        raise DataError("dataset candles must be strictly chronological")
    markets = {candle.market for candle in candles}
    if len(markets) > 1:
        raise DataError("dataset candles must belong to one market")
    for market in markets:
        _validate_krw_market(market)

    digest = hashlib.sha256(b"bithumb-coin-trader:candles:v1\n")
    for candle in candles:
        canonical_row = (
            candle.market,
            candle.timestamp.astimezone(timezone.utc).isoformat(timespec="microseconds"),
            float(candle.open).hex(),
            float(candle.high).hex(),
            float(candle.low).hex(),
            float(candle.close).hex(),
            float(candle.volume).hex(),
        )
        digest.update(json.dumps(canonical_row, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
        digest.update(b"\n")
    return DatasetManifest(
        schema_version=1,
        market=next(iter(markets), None),
        candle_count=len(candles),
        start_at=candles[0].timestamp.astimezone(timezone.utc) if candles else None,
        end_at=candles[-1].timestamp.astimezone(timezone.utc) if candles else None,
        sha256=digest.hexdigest(),
    )
