"""Dataset Adapters for Microstructure Research.

Reads raw JSONL files from historical datasets and produces CanonicalEvents.
Handles the existing collector output format with envelope fields:
    exchange, stream, market, exchange_ts, local_recv_ts, local_write_ts, payload

Reuses the existing canonical_market_data.raw_record_to_canonical() for
exchange-specific payload normalization, then wraps in the research envelope.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterator, Sequence

from .canonical_events import (
    CanonicalEvent,
    EventKind,
    TimestampRole,
    parse_iso_to_ms,
)
from .dq import CoverageState
from ..canonical_market_data import (
    CanonicalOrderBook,
    CanonicalTrade,
    CanonicalTicker,
    raw_record_to_canonical,
)
from ..data_quality_flags import DataQualityFlag, scan_orderbook_quality


_STREAM_TO_KIND = {
    "trade": EventKind.TRADE,
    "orderbook": EventKind.ORDERBOOK,
    "ticker": EventKind.TICKER,
}


class AdapterError(ValueError):
    """Raised when a raw record cannot be adapted to a canonical event."""


def adapt_raw_record(
    record: dict,
    dataset_id: str,
    source_file: str | None = None,
    source_file_offset: int | None = None,
    source_run_id: str | None = None,
    collector_epoch: str | None = None,
) -> CanonicalEvent | None:
    """Convert a raw JSONL record to a CanonicalEvent.

    Returns None for records that fail validation (logged but not raised).
    """
    try:
        stream = str(record.get("stream", record.get("type", ""))).lower()
        exchange = str(record.get("exchange", "")).lower()
        market = str(record.get("market", record.get("code", ""))).upper().replace("_", "-")

        event_kind = _STREAM_TO_KIND.get(stream)
        if event_kind is None:
            return None

        # Parse timestamps from envelope
        exchange_ts_str = record.get("exchange_ts", "")
        local_recv_ts_str = record.get("local_recv_ts", "")
        local_write_ts_str = record.get("local_write_ts", "")

        if not exchange_ts_str or not local_recv_ts_str:
            return None

        exchange_ms = parse_iso_to_ms(str(exchange_ts_str))
        local_recv_ms = parse_iso_to_ms(str(local_recv_ts_str))
        local_write_ms = parse_iso_to_ms(str(local_write_ts_str)) if local_write_ts_str else local_recv_ms

        # Derive cohort from local_write timestamp
        local_write_dt = datetime.fromtimestamp(local_write_ms / 1000.0, tz=timezone.utc)
        cohort_utc = local_write_dt.strftime("%Y-%m-%d_%H")

        # Parse payload based on stream type
        payload = record.get("payload", record)
        dq_flags = 0

        if event_kind == EventKind.TRADE:
            try:
                canonical = raw_record_to_canonical(record)
                if not isinstance(canonical, CanonicalTrade):
                    return None
                trade_payload = {
                    "price": canonical.price,
                    "quantity": canonical.quantity,
                    "aggressor_side": canonical.aggressor_side,
                    "trade_id": canonical.trade_id,
                }
            except Exception:
                # Fallback: parse directly from payload
                price = float(payload.get("trade_price", payload.get("price", 0)))
                qty = float(payload.get("trade_volume", payload.get("quantity", 0)))
                side_raw = payload.get("ask_bid", payload.get("side", ""))
                from ..microstructure_features import normalize_aggressor_side
                try:
                    side = normalize_aggressor_side(exchange, side_raw)
                except ValueError:
                    side = "BUY" if str(side_raw).upper() in ("BID", "BUY") else "SELL"
                trade_payload = {
                    "price": price,
                    "quantity": qty,
                    "aggressor_side": side,
                    "trade_id": str(payload.get("sequential_id", payload.get("trade_id", ""))),
                }

        elif event_kind == EventKind.ORDERBOOK:
            # Parse orderbook directly — filter out zero-size levels
            # (Bithumb sends levels with zero size that the canonical model rejects)
            units = payload.get("orderbook_units", [])
            if not units:
                return None
            bids = sorted(
                [(float(u["bid_price"]), float(u["bid_size"]))
                 for u in units if u.get("bid_price") and float(u.get("bid_size", 0)) > 0],
                key=lambda x: -x[0],
            )
            asks = sorted(
                [(float(u["ask_price"]), float(u["ask_size"]))
                 for u in units if u.get("ask_price") and float(u.get("ask_size", 0)) > 0],
                key=lambda x: x[0],
            )
            if not bids or not asks:
                return None
            ob_payload = {
                "bids": bids,
                "asks": asks,
                "is_snapshot": payload.get("stream_type", "") == "SNAPSHOT",
            }

        elif event_kind == EventKind.TICKER:
            # Parse ticker directly — more robust than canonical conversion
            ticker_payload = {
                "last_price": float(payload.get("trade_price", payload.get("last_price", 0))),
                "volume_24h": float(payload.get("acc_trade_volume_24h", 0))
                    if payload.get("acc_trade_volume_24h") else None,
            }

        # Build the final payload
        if event_kind == EventKind.TRADE:
            final_payload = trade_payload  # type: ignore[possibly-undefined]
        elif event_kind == EventKind.ORDERBOOK:
            final_payload = ob_payload  # type: ignore[possibly-undefined]
        else:
            final_payload = ticker_payload  # type: ignore[possibly-undefined]

        return CanonicalEvent(
            dataset_id=dataset_id,
            source_run_id=source_run_id,
            collector_epoch=collector_epoch,
            source_file=source_file,
            source_file_offset=source_file_offset,
            exchange=exchange,
            market=market,
            event_kind=event_kind,
            exchange_timestamp_ms=exchange_ms,
            local_recv_timestamp_ms=local_recv_ms,
            local_write_timestamp_ms=local_write_ms,
            ordering_timestamp_ns=local_write_ms * 1_000_000,
            exchange_timestamp_role=TimestampRole.EXCHANGE_EVENT,
            cohort_utc=cohort_utc,
            dq_status="DATA_PRESENT",
            dq_flags=dq_flags,
            payload=final_payload,
        )

    except Exception:
        return None


def iter_raw_jsonl_file(
    path: Path,
    dataset_id: str,
    source_run_id: str | None = None,
    collector_epoch: str | None = None,
) -> Iterator[CanonicalEvent]:
    """Iterate over a raw JSONL file, yielding CanonicalEvents.

    Supports both plain .jsonl and zstd-compressed .jsonl.zst files.
    """
    import io

    is_zst = str(path).endswith(".zst")

    def _iter_lines(file_handle):
        if is_zst:
            import zstandard
            dctx = zstandard.ZstdDecompressor()
            reader = dctx.stream_reader(file_handle)
            wrapper = io.TextIOWrapper(reader, encoding="utf-8")
            yield from wrapper
        else:
            yield from file_handle

    with open(path, "rb" if is_zst else "r") as f:
        for line_num, line in enumerate(_iter_lines(f), 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = adapt_raw_record(
                record,
                dataset_id=dataset_id,
                source_file=str(path),
                source_file_offset=line_num,
                source_run_id=source_run_id,
                collector_epoch=collector_epoch,
            )
            if event is not None:
                yield event


def iter_raw_jsonl_streaming(
    raw_root: Path,
    dataset_id: str,
    exchanges: Sequence[str] | None = None,
    feeds: Sequence[str] | None = None,
    markets: Sequence[str] | None = None,
    cohorts: Sequence[str] | None = None,
    source_run_id: str | None = None,
    collector_epoch: str | None = None,
) -> Iterator[CanonicalEvent]:
    """Stream events from raw JSONL files with optional filtering.

    Memory-efficient: processes one file at a time.
    Events are yielded in file order (approximately chronological within a partition).
    """
    if not raw_root.exists():
        return

    for date_dir in sorted(raw_root.iterdir()):
        if not date_dir.is_dir():
            continue
        date = date_dir.name

        for exchange_dir in sorted(date_dir.iterdir()):
            if not exchange_dir.is_dir():
                continue
            exchange = exchange_dir.name
            if exchanges and exchange not in exchanges:
                continue

            for feed_dir in sorted(exchange_dir.iterdir()):
                if not feed_dir.is_dir():
                    continue
                feed = feed_dir.name
                if feeds and feed not in feeds:
                    continue

                for jsonl_file in sorted(feed_dir.iterdir()):
                    fname = jsonl_file.name
                    if fname.endswith(".jsonl.zst"):
                        pass  # zstd-compressed JSONL
                    elif fname.endswith(".jsonl"):
                        pass  # plain JSONL
                    else:
                        continue

                    # Parse cohort from filename (strip .jsonl.zst or .jsonl)
                    stem = fname
                    for ext in (".jsonl.zst", ".jsonl"):
                        if stem.endswith(ext):
                            stem = stem[: -len(ext)]
                            break
                    parts = stem.split("_")
                    if len(parts) >= 5:
                        market = parts[2].upper().replace("-", "-")
                        hour = parts[4]
                        cohort = f"{date}_{hour}"

                        if markets and market not in markets:
                            continue
                        if cohorts and cohort not in cohorts:
                            continue

                    yield from iter_raw_jsonl_file(
                        jsonl_file,
                        dataset_id=dataset_id,
                        source_run_id=source_run_id,
                        collector_epoch=collector_epoch,
                    )
