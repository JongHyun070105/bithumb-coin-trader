# Bithumb public feed redundancy decision

## Decision

The local validation candidate uses **two active-active Bithumb public WebSocket connections**. Each requests the same 20 markets and three public stream types, so two physical sessions jointly provide 60 logical feeds. Existing sealed runtime configurations that do not contain the new `bithumb_redundancy` section keep their historical one-connection behavior. Newly generated runtime configurations bind the two-connection mode and its limits into the configuration fingerprint.

This is public market-data collection only. It adds no private API, order, paper, or live-trading path.

## Official protocol facts checked on 2026-09-21

- Bithumb documents public v1 `ticker`, `trade`, and `orderbook` at `wss://ws-api.bithumb.com/websocket/v1` and limits WebSocket **connection requests** to 10 per IP per second. Excess requests receive 429 responses and repeated excess may cause a temporary ten-minute block. [Bithumb WebSocket basic information](https://apidocs.bithumb.com/reference/%EA%B8%B0%EB%B3%B8-%EC%A0%95%EB%B3%B4)
- One subscription request can contain multiple type fields. [Bithumb request format](https://apidocs.bithumb.com/reference/%EC%9A%94%EC%B2%AD-%ED%8F%AC%EB%A7%B7)
- Bithumb documents RFC 6455 PING/PONG support and an approximately 120-second idle timeout. [Bithumb connection management](https://apidocs.bithumb.com/reference/%EC%97%B0%EA%B2%B0-%EA%B4%80%EB%A6%AC)
- Public trades expose a unique `sequential_id`, but Bithumb explicitly says it does not guarantee order. [Bithumb trade stream](https://apidocs.bithumb.com/reference/%EC%B2%B4%EA%B2%B0-trade)
- Public orderbooks expose a microsecond `timestamp` and no documented sequence ID. [Bithumb orderbook stream](https://apidocs.bithumb.com/reference/%ED%98%B8%EA%B0%80-orderbook)

The official pages do not publish a maximum simultaneous public-connection count, a per-connection subscription count, or a separate rule for subscribing to the same public feeds from two connections. The design therefore uses exactly two connections, does not infer an unlimited allowance, and serializes attempts at 250 ms intervals: at most four attempts per second from this collector. This is below the published 10 connection requests per IP per second, although another process sharing the IP remains outside this collector's limiter.

The installed package is `websockets 17.1`. Its official asyncio client documentation states that canceling `recv()` is safe, `close()` is idempotent, and `open_timeout` / `close_timeout` are supported. The connection owner uses those installed semantics: one receive task, one heartbeat task, one first-wins reconnect decision, one teardown owner, and transport abort only after close timeout or close exception. [websockets 17.1 asyncio client](https://websockets.readthedocs.io/en/latest/reference/asyncio/client.html)

## Architecture comparison

| Option | Failure isolation | Continuity after one socket failure | Connection count | Duplicate work | Complexity | Decision |
| --- | --- | --- | ---: | --- | --- | --- |
| A. One socket | One failure affects all 60 feeds | No | 1 | None | Low | Rejected: observed blast radius remains. |
| B. Split by stream | Isolates orderbook, trade, ticker | Only unaffected stream types continue | 3 | None | Medium | Rejected: failed stream still loses all 20 markets. |
| C. Market sharding | Limits failure to one market shard | Only unaffected shards continue | 2+ | None | Medium | Rejected: a strict hour still fails for the affected shard. |
| D. Two active-active sockets | One transport can cover every logical feed | Yes, when the other source is confirmed and timely | 2 | Expected and bounded | Medium | **Selected.** Smallest design that protects every logical feed from one physical failure. |
| E. Redundant shards | Stronger per-shard isolation | Yes | 4+ | Expected | High | Deferred: additional connections and state do not improve the current single-failure requirement enough. |

## Deterministic event handling

The first arrival is canonical; there is no lookahead and no later replacement.

- Trade nominal identity: exchange + stream + market + documented `sequential_id`.
- Orderbook and ticker nominal identity: exchange + stream + market + documented message `timestamp` + stream type.
- Fallback when the native field is absent: canonical payload SHA-256. This can deduplicate exact copies but cannot classify different payloads as the same nominal event.
- Equivalent copy: persist the canonical raw event once, including its physical connection ID; increment `redundant_frames_received` and `deduplicated_frames`; append a compact provenance record containing both connection IDs and both complete-payload hashes. For trades with a documented `sequential_id`, conflict comparison excludes the source-local top-level `timestamp`; `trade_timestamp` and every other trade field remain covered. Provenance distinguishes exact copies from this narrow semantic equivalence.
- Same nominal identity with different canonical comparison hash: keep the first canonical event, quarantine the conflicting copy with both hashes and sources, increment `conflicting_duplicate_frames`, and fail only that feed's UTC cohort through writer health. No payload is silently selected as scientifically valid.

The cache is bounded to 100,000 identities and 180 seconds. Evictions are counted. A copy arriving after eviction can be persisted again; the counter makes cache pressure visible. A canonical claim is released if queue admission is canceled, allowing the other source to become canonical. Any received item canceled while waiting for queue capacity increments `unpersisted_event_count` and therefore fails coverage.

## Physical health and logical coverage

Each physical connection has its own session, confirmation times, heartbeat history, reconnect successor, state, and last diagnostic. The top-level Bithumb state remains `CONNECTED` while at least one source is connected.

Logical feed coverage uses the union of **confirmed** physical-session intervals and their heartbeat/frame observations. A primary disconnect is retained in the evidence and counters but does not create `COLLECTION_GAP` if a confirmed secondary session continuously covers that feed. Any interval where both sources are absent, unconfirmed, or outside the heartbeat threshold remains a logical failure.

## Reconnect and teardown policy

- Initial connection failure or a session shorter than 30 seconds: exponential backoff beginning near one second, capped at 30 seconds.
- Established session failure after at least 30 seconds and one valid frame: one near-immediate retry in the 50–200 ms range, then normal backoff if the replacement also fails.
- All attempts across both Bithumb owners are serialized by the 250 ms limiter.
- Connection opening uses a 10-second bound. Context close uses the installed one-second `close_timeout` and an outer two-second bound. A timeout or close exception records the close failure and aborts the transport; it does not overwrite the original receive/reconnect cause.

## Evidence and limits

The historical 2026-09-19 incident remains a Bithumb-specific connection/path reception interruption. The new design does not identify its server, network, host-socket, or task-level root cause. Historical Fresh 30H-v2 remains `FAIL`.

The accelerated 7,200-second local soak is a deterministic state-model and bounded-resource check. It does not prove leak freedom, current exchange acceptance of duplicate subscriptions, cloud network behavior, or a future six-hour result. Those claims require a separately authorized fresh validation.
