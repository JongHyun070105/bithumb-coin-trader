# Local PAPER session command

`paper-start` is a finite local runner. It first verifies `RELIABILITY_SEALED`,
`DATA_QUALIFIED`, `CANDIDATE_FROZEN`, `RISK_READY`, `PAPER_ENGINE_READY`,
`OBSERVABILITY_READY`, `SECRETS_SAFE`, and `PRIVATE_API_DISABLED`. A failed
check returns before the session adapter is constructed and before a journal
is created. With all checks passing, it runs the frozen candidate over a
caller-supplied normalized public event stream. It does not fetch market data,
start a websocket, contact AWS, or import an exchange client.

The event file is UTF-8 JSONL, with one event per line and exactly these fields:

```json
{
  "event_id": "provider-event-id",
  "received_at_ms": 1735689600000,
  "candle": {
    "timestamp": "2025-01-01T00:00:00Z",
    "open": 100.0,
    "high": 101.0,
    "low": 99.0,
    "close": 100.5,
    "volume": 2.0,
    "market": "KRW-BTC"
  },
  "orderbooks": [
    {
      "timestamp_ms": 1735689600000,
      "bids": [[100.0, 10.0]],
      "asks": [[101.0, 10.0]],
      "market": "KRW-BTC"
    }
  ]
}
```

`candle` may be `null` for an order-book-only update. Timestamps must be
non-negative integers in milliseconds (ISO timestamp with an explicit offset
for candle time); values must be finite, books non-empty and uncrossed, and all
markets must be KRW spot. Event ids are idempotency keys. Reusing an id with
different content or sending events out of chronological order halts the
runtime. The caller is responsible for retaining provider/source provenance
and ensuring the warmup CSV and event stream match the candidate's frozen
market and time assumptions.

Example invocation shape; this is documentation only and has not been run:

```sh
python -m bithumb_coin_trader.research_infra.cli paper-start \
  --candidate-freeze research-artifacts/frozen-candidates/EXPERIMENT.json \
  --readiness-evidence-dir research-artifacts/paper-readiness \
  --reliability-seal research-artifacts/reliability-seal.json \
  --research-root research-artifacts \
  --warmup-csv /path/to/qualified-public-warmup.csv \
  --events-jsonl /path/to/normalized-public-events.jsonl \
  --cost-scenario /path/to/paper-cost-scenario.json \
  --risk-config /path/to/paper-risk-config.json \
  --journal research-artifacts/paper-session.sqlite \
  --initial-cash-krw 1000000 \
  --market KRW-BTC
```

An existing journal is rejected unless `--resume` is supplied. `--resume`
requires that journal to exist and the runtime verifies its frozen candidate,
warmup history, and hash-protected state. A durable halt marker still blocks
startup until an operator explicitly acknowledges recovery through the runtime
recovery API. The CLI does not silently clear a halt. The command is not a
service supervisor: an exhausted file ends the session, and a process crash
requires an explicit later restart.

The session stops reading the file as soon as the runtime enters a halt state,
emits the final metrics snapshot, and exits non-zero. It never continues the
remaining input events under a halted strategy.

The command emits one JSON event-result record per input event and a final
`metrics_snapshot` JSON record to standard output. That snapshot includes data
age, strategy state, signals, orders, fills, rejections, positions, cash and
reserves, equity, realized and unrealized PnL, fees, slippage, turnover,
drawdown, risk verdict/reasons, halt reason, journal health, restart count, and
fill semantics. The authoritative event and accounting history remains in the
SQLite journal; standard output is an operational view, not a replacement for
that journal.

The existing loopback dashboard API exposes the latest journal snapshot at
`GET /api/paper/runtime` when its process is started with
`BITHUMB_PAPER_JOURNAL_PATH=/path/to/paper-session.sqlite`. The endpoint opens
the configured journal in SQLite read-only mode. It returns
`metrics_integrity=NOT_VERIFIED` because the runtime result JSON is not
independently hashed; use the journal recovery checks as the accounting source
of truth.

The PAPER fill path is taker-only and consumes observable book depth with
configured positive latency, taker fee, and slippage, plus tick, lot, and
minimum notional rounding. Depth-limited partial fills are explicit. Maker
fills, unobserved liquidity, and a probabilistic fill model are unsupported.
No private exchange API or real order endpoint exists on this path. Preparing
this command does not start PAPER; PAPER remains `NOT_STARTED` until an
independently authorized invocation with a complete evidence bundle.
