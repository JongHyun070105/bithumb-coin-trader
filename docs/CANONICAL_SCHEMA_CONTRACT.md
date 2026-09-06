# CANONICAL SCHEMA CONTRACT & SERIALIZATION TRACE (PHASE 4)

## 1. Overview & Provenance Trace

This document establishes the exact raw schema contract between the production collector raw serialization path and the offline research canonical transformer (`cmd_transform_canonical`).

### Pipeline Trace:
```
WebSocket Raw Bytes (Bithumb / Binance / Upbit)
   ↓ json.loads()
Collector Event Payload
   ↓ MultiExchangeMicrostructureCollector._enqueue(exchange, stream, market, payload, recv_ts, exch_ts, recv_monotonic_ns)
Internal Write Queue
   ↓ _writer_worker()
RawMicrostructureStorage.append_raw_record()
   ↓ JSON serialization (separators=(",", ":"))
Partition File (.jsonl / compressed .ndjson.zst)
   ↓ research_cli transform-canonical
CanonicalOrderBook (with distinct exchange_timestamp_ms & receive_timestamp_ms)
```

---

## 2. Raw Envelope Schema Contract

Every record appended by `RawMicrostructureStorage.append_raw_record()` is serialized with the following top-level envelope schema:

| Field Name | Type | Description |
| :--- | :--- | :--- |
| `exchange` | `str` | Exchange identifier (`"bithumb"`, `"binance"`, `"upbit"`). |
| `stream` | `str` | Stream identifier (`"orderbook"`, `"trade"`, `"ticker"`). |
| `market` | `str` | Standardized market symbol (e.g. `"BTC-KRW"`, `"BTCUSDT"`). |
| `exchange_ts` | `str \| null` | ISO 8601 UTC timestamp provided by the exchange, or null. |
| `local_recv_ts` | `str` | ISO 8601 UTC wall-clock timestamp recorded immediately upon WebSocket frame arrival. |
| `local_recv_monotonic_ns` | `int \| null` | High-resolution monotonic clock (`time.monotonic_ns()`) for skew-free relative interval calculation. |
| `collector_run_id` | `str \| null` | UUID / run identity of the collector process. |
| `local_write_ts` | `str` | ISO 8601 UTC timestamp when the record was persisted to disk. |
| `payload` | `dict` | Original unadulterated payload dictionary from the exchange. |

---

## 3. Stream Support Classification Matrix

In accordance with Phase 4 P10 invariants, claims of multi-exchange conversion are strictly bounded to supported streams:

| Exchange | Stream | Classification | Canonical Target | Notes / Field Mapping |
| :--- | :--- | :--- | :--- | :--- |
| **Bithumb** | `orderbook` | **SUPPORTED** | `CanonicalOrderBook` | Maps `payload.bids`, `payload.asks`, preserves `local_recv_ts` |
| **Bithumb** | `trade` | **UNSUPPORTED** | N/A | Raw stored; conversion to `CanonicalTrade` not in offline CLI scope |
| **Bithumb** | `ticker` | **NOT_REQUIRED** | N/A | Used for live heartbeat/monitoring, not microstructure backtesting |
| **Binance** | `orderbook` | **SUPPORTED** | `CanonicalOrderBook` | Maps `data.b` (`payload.b`), `data.a` (`payload.a`), preserves `local_recv_ts` |
| **Binance** | `trade` | **UNSUPPORTED** | N/A | Raw stored; conversion to `CanonicalTrade` not in offline CLI scope |
| **Upbit** | `orderbook` | **SUPPORTED** | `CanonicalOrderBook` | Maps `payload.orderbook_units` (`bid_price`/`bid_size`), preserves `local_recv_ts` |
| **Upbit** | `trade` | **UNSUPPORTED** | N/A | Raw stored; conversion to `CanonicalTrade` not in offline CLI scope |

> [!IMPORTANT]
> **Claim Boundary**: Phase 4 canonical transformation supports **OrderBook streams only** across Bithumb, Binance, and Upbit. It does **not** claim full multi-exchange trade or ticker canonicalization. Any mixed raw file containing non-orderbook streams must be isolated or transformed with stream-specific filtering.

---

## 4. Clock Domain Separation & Latency Safety

`cmd_transform_canonical` preserves clock domain separation:
1. `exchange_timestamp_ms`: Extracted from payload event timestamp.
2. `receive_timestamp_ms`: Derived strictly from `local_recv_ts` (or `receive_timestamp_ms` / `local_receive_ms`). If absent, it is populated as `None` (or `UNKNOWN`). It is **never** fabricated by copying `exchange_timestamp_ms`.
3. `timestamp_semantics`:
   - Set to `TimestampSemantics.LOCAL_RECEIVE` if local receive time is available.
   - Set to `TimestampSemantics.EXCHANGE_EVENT` if only exchange time is available.
4. `receive_monotonic_ns`: Preserved directly from `local_recv_monotonic_ns`.
