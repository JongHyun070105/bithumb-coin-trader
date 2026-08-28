# Strategy V9 pre-72h infrastructure and integrity audit

- Audit snapshot: 2026-08-26 22:55 KST (2026-08-26 13:55 UTC)
- Repository: `JongHyun070105/bithumb-coin-trader`, `main`, audit-time HEAD `608521870a31e2579ca310eb90e53c86c861da50` (not asserted as PID 30933 launch-time HEAD)
- Canonical scope: `data/microstructure/raw/**`; the legacy `data/microstructure/{trade,ticker,orderbook}` layout was excluded
- Safety: `BITHUMB_NEW_ENTRIES=false` remains the operational default; no live/order setting was changed
- Classification: **PRE-72H / INFRASTRUCTURE AUDIT ONLY / RESEARCH_ONLY**

## Executive Summary

This is not the final 72-hour audit. The raw local-receive boundary was 2026-08-25 15:21:25.664848 UTC through approximately 2026-08-26 13:54:58 UTC, or 22.5591 hours. Because the raw start includes earlier process epochs, the final continuous-soak clock is anchored to uninterrupted PID 30933, which started at 2026-08-26 01:19:33 KST. Its 72-hour boundary is **2026-08-29 01:19:33 KST**. A 01:05 KST check is preparation only and must not stop the collector.

The closed-partition full scan parsed and hashed 1,673 files containing 19,508,017 records and 22,638,042,414 bytes. UTF-8/JSON, required-field, schema/path, non-finite-number, and timestamp-format checks found zero errors in that closed scope. That evidence does **not** establish feed completeness.

The epoch nevertheless fails research readiness:

1. all 2,810,312 Binance orderbook records have `market=UNKNOWN`, because the collector discarded combined-stream identity;
2. the wall-clock `local_recv_ts` reversed 70 times in five system-wide clusters, by as much as 2.032 seconds, and no monotonic clock was stored;
3. 76 partition-local duplicate trade IDs were detected, primarily around SNAPSHOT/REALTIME transitions;
4. repeated reconnect/SNAPSHOT bursts occurred around the reported Wi-Fi-to-Ethernet transition, but no persistent collector log or metrics ledger exists to prove the cause or queue/drop state;
5. less than 72 hours have elapsed and at least three collector process epochs are evidenced.

Therefore `alpha_research_ready=false` and `live_trading_ready=false`.

### Resumption update — 2026-08-27 02:14 KST

- PID 30933 was reverified unchanged, with active appends and approximately 25 hours uninterrupted runtime.
- Closed-hour FULL-SCAN scope increased to 1,892 files, 26,315,219,332 bytes, and 22,912,787 records.
- UTF-8/JSON/schema/required-field/non-finite/timestamp-format errors remain 0 in that closed scope.
- Findings increased with additional data: 3,224,737 Binance orderbook `UNKNOWN` records, 86 wall-clock receive-time reversals, and 111 partition-local duplicate trade IDs.
- 19,688,050 records had parseable exchange/local timestamp pairs; 81,427 were outside the prior ±60-second percentile window and are now explicitly counted instead of discarded.
- All 22,912,787 current-epoch records lack a monotonic receive timestamp, which confirms causal ordering cannot be repaired retroactively for V9.
- 1,892 / 1,892 closed partitions now have current extended manifests; active current-hour files remain excluded.
- V9.1 code prepares fail-closed writer handling, durable metrics, preserved Binance combined-stream identity, run-scoped monotonic receive timestamps, bounded recent sampling, and strict manifest validation. None of it has been loaded into PID 30933.
- Running-epoch provenance is frozen separately in `docs/V9_EPOCH_PROVENANCE_PID_30933_2026-08-26.md`. The `6085218` commit postdates process start and is retained as a corroborating Git reference snapshot, not mislabelled as the verified launch-time commit. The exact launch-time in-memory source hash remains `NOT DIRECTLY VERIFIABLE` because no sealed pre-launch hash was found.

## Epoch provenance boundary

The final audit must not use the current working-tree source or hash as the code executed by PID 30933. Verified process evidence is PID `30933`, OS start `2026-08-26 01:19:33 KST`, working directory `/Users/macintosh/Documents/ChatGPT/bitcoin-trader`, Homebrew Python `3.14.6` through `.venv/bin/python`, `PYTHONPATH=src`, and sanitized command `scripts/run_cross_market_collector.py --bithumb-markets 20`.

Audit-time Git HEAD `6085218` contains a stable reconstruction reference for the later-committed V9 collector, storage, runner, and universe blobs. However, it was committed at `2026-08-26 22:36:07 KST`, after the process began. Runtime raw partitions corroborate the configured 20 Bithumb markets, four Binance markets, four Upbit markets, and the old Binance `UNKNOWN` orderbook behavior. They do not prove byte-for-byte equality with that later commit.

All post-start collector/storage modifications are classified as V9.1 preparation and are not loaded by PID 30933.

## Data Coverage

- Earliest raw `local_recv_ts`: `2026-08-25T15:21:25.664848+00:00`
- Snapshot latest raw `local_recv_ts`: approximately `2026-08-26T13:54:58.397805+00:00`
- Snapshot coverage: `22.5591 h`
- Raw-history 72-hour boundary (not the continuous-soak stop rule): `2026-08-28T15:21:25.664848+00:00` / `2026-08-29 00:21:25.664848 KST`
- Continuous PID 30933 72-hour boundary: `2026-08-28T16:19:33+00:00` / `2026-08-29 01:19:33 KST`
- Current process at audit: PID `30933`, OS start `2026-08-26 01:19:33 KST`
- Process epochs: at least three; an early restart left an approximately 29-second gap, and the transition to PID 30933 left an approximately 3-second high-frequency-stream boundary gap
- `72h_continuous_coverage`: **FAIL (not elapsed; multiple epochs)**

The active current UTC-hour partitions were excluded from the full scan and manifest finalization.

## Exchange/Stream Inventory and Record Counts

These are FULL-SCAN counts for 1,673 closed UTC-hour partitions.

| Exchange / stream | Files | Bytes | Records | Unknown market | Missing exchange timestamp | Local time reversals | Partition-local duplicate IDs |
|---|---:|---:|---:|---:|---:|---:|---:|
| Binance / orderbook | 23 | 4,211,269,965 | 2,810,312 | 2,810,312 | 2,810,312 | 5 | 0 |
| Binance / trade | 92 | 3,002,689,040 | 8,450,856 | 0 | 24,873 | 10 | 0 |
| Bithumb / orderbook | 460 | 8,398,408,813 | 5,298,134 | 0 | 32,068 | 35 | 0 |
| Bithumb / ticker | 457 | 195,499,376 | 177,041 | 0 | 1,436 | 2 | 0 |
| Bithumb / trade | 457 | 158,654,471 | 289,136 | 0 | 2,442 | 2 | 72 |
| Upbit / orderbook | 92 | 6,372,279,217 | 2,027,822 | 0 | 15,138 | 16 | 0 |
| Upbit / trade | 92 | 299,241,532 | 454,716 | 0 | 5,444 | 0 | 4 |
| **Total** | **1,673** | **22,638,042,414** | **19,508,017** | **2,810,312** | **2,891,713** | **70** | **76** |

The missing exchange-timestamp count is a data-field observation, not automatically a malformed-record count. Binance partial depth payloads in this epoch contain neither symbol nor exchange event timestamp after the current parser discarded the combined-stream envelope.

## Storage Integrity

FULL-SCAN results over all closed partitions:

- invalid UTF-8 or JSON: 0
- schema/path mismatch: 0
- missing required top-level fields: 0
- non-finite numeric constants: 0
- malformed stored timestamps: 0
- zero-byte closed files: 0 observed in the inventory
- local receive timestamp reversals: 70 (**FAIL for strict causal wall-clock ordering**)
- quarantined files: 0; this only proves no quarantine artifact exists, not that no upstream event was lost

The 70 reversals clustered at `2026-08-25 15:34`, `16:38`, `18:46`, `19:25`, and `23:29` UTC. The maximum backward movement was 2.032 seconds. The system-wide clustering across exchanges is consistent with local wall-clock adjustment. Because V9 did not store `time.monotonic_ns()`, strict receive-order causality is not recoverable from this epoch during those boundaries.

## Manifest Integrity and SHA Verification

Before repair:

- 945 manifests for 1,673 closed files
- 728 missing manifests
- 45 stale manifests whose recorded byte size no longer matched raw
- the stale set included 43 first-hour files and two quiet `KRW-MANA` files incorrectly finalized by the old 10-minute-mtime heuristic

After closed-hour regeneration and full rehash:

- 1,673 / 1,673 closed raw files have manifests
- raw path/size mismatches: 0
- generation failures: 0
- independent SHA-256 rehash sample: 5 / 5 PASS

The manifest generator now finalizes only hours strictly older than the current UTC hour, repairs stale manifests, supports `--rehash-all`, bounds timestamp percentile memory, and labels the timestamp difference as something broader than network latency.

## Duplicate Analysis and Completeness Limits

The full scan found 72 Bithumb and 4 Upbit partition-local duplicate trade IDs. Most were SNAPSHOT/REALTIME boundary duplicates; repeated SNAPSHOT records also occurred in quiet markets. Deduplication is required before feature/label generation.

Bithumb officially documents `sequential_id` as a unique trade number but explicitly says it does not guarantee trade order. Bithumb orderbook exposes no sequence/update identifier. Consequently arithmetic `next == previous + 1` gap checks are invalid and `exchange_feed_completeness=NOT_DIRECTLY_VERIFIABLE`. See the official [Bithumb trade](https://apidocs.bithumb.com/reference/%EC%B2%B4%EA%B2%B0-trade) and [orderbook](https://apidocs.bithumb.com/reference/%ED%98%B8%EA%B0%80-orderbook) documentation.

Upbit likewise documents a unique trade identifier but no orderbook sequence/gap-recovery contract in the public schemas; see [Upbit trade](https://global-docs.upbit.com/reference/websocket-trade) and [orderbook](https://global-docs.upbit.com/reference/websocket-orderbook).

## Reconnect Analysis and Network Transition Incident

Persistent reconnect, queue, and writer metrics were not written by the running epoch. Exact disconnect reasons, DNS errors, TCP reset, queue depth, and drop counts are therefore **NOT VERIFIABLE**.

Raw SNAPSHOT bursts provide indirect reconnect evidence. Bithumb emitted 20-market synchronized SNAPSHOT bursts at 21:03, 21:11, 21:30, 21:43, 21:56, 22:03, 22:17, 22:21, and 22:51 KST on 2026-08-26. Upbit emitted four-market bursts at 21:30, 21:43, 21:56, 22:03, and 22:17 KST. This overlaps the user's approximate Wi-Fi-to-Ethernet transition window, so correlation is plausible; causation cannot be established without a network/process log. The repeated bursts over nearly two hours should not be reduced to a single harmless transition.

## Clock and Timestamp Analysis

The corrected status sampler uses bounded tails and equal per-exchange/stream file budgets. A representative current sample reported:

| Exchange / stream | N | p50 ms | p90 ms | p95 ms |
|---|---:|---:|---:|---:|
| Binance / trade | 400 | -2011.3 | -2005.5 | -1985.9 |
| Binance / orderbook | 0 | — | — | — |
| Bithumb / orderbook | 400 | -1938.6 | -1927.0 | -1924.4 |
| Bithumb / ticker | 400 | -1750.7 | -1713.7 | -1708.3 |
| Bithumb / trade | 400 | -1752.4 | -1712.9 | -1703.9 |
| Upbit / orderbook | 400 | -2004.2 | -2001.2 | -1996.4 |
| Upbit / trade | 400 | -1951.7 | -1933.8 | -1930.2 |

These values are `local_receive - exchange-labelled timestamp`. An earlier sample in the same run was near +15 to +319 ms, while the later balanced sample was near -1.7 to -2.0 seconds across exchanges. The cross-exchange shift reinforces that local/exchange clock offset dominates the apparent change. These values combine clock offset, exchange timestamp semantics, exchange publication, network transit, and client scheduling; they are not one-way network latency. Bithumb ticker `trade_timestamp` is the latest underlying trade time, while orderbook `timestamp` is only documented as a microsecond timestamp. Binance trade distinguishes event time `E` and trade time `T`; see the official [Binance Spot WebSocket streams](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/ws-streams/~).

## Upbit Sampler Root Cause

The old status command selected the first 60 recent files globally and then grouped by exchange. Filesystem ordering exhausted that global budget before Upbit files, producing `UPBIT: No samples` despite active growth. The corrected sampler groups by `(exchange, stream)`, sorts within each group, reads bounded tails, and reports missing timestamp/parse counts separately.

## Disk and Storage Analysis

- Snapshot raw size: approximately 21.0 GiB
- Estimated byte growth: approximately 953 MiB/hour / 22.34 GiB/day
- Current free space during audit: approximately 126.6 GiB
- Estimated 72-hour total: approximately 67.0 GiB
- Correct future requirement: approximately 46.0 GiB
- Estimated free space at 72 hours: approximately 80.6 GiB

The old calculation incorrectly subtracted the full projected 72-hour total from current free space. The corrected calculation subtracts only `projected_total - already_collected` and labels binary units as MiB/GiB.

No raw compression or deletion was performed. Compression benchmarking must use copies of finalized files and byte-for-byte decompression verification before any deletion is considered.

## Replay Determinism

`replay_determinism=NOT_VERIFIABLE`. The repository has feature and taker-simulator libraries, but no production adapter currently converts canonical V9 raw records into causally ordered `OrderbookSnapshot`/`TradeTick` inputs and hashes derived outputs. Binance orderbook identity loss and absent monotonic receive timestamps prevent a valid cross-market replay for this epoch.

## PASS/FAIL Matrix

| Claim | Status | Evidence / limit |
|---|---|---|
| collector process alive | PASS | one PID and active appends at audit snapshot |
| collector websocket connected | NOT VERIFIABLE | no durable connection metrics in current process |
| 72h continuous coverage | FAIL | 22.5591h snapshot; multiple epochs |
| closed raw parse/schema integrity | PASS | FULL-SCAN 1,673 files / 19,508,017 records |
| strict causal local clock | FAIL | 70 wall-clock reversals; no monotonic clock |
| manifest integrity | PASS AFTER REPAIR | 1,673/1,673; size/path mismatch 0; SHA sample 5/5 |
| duplicate-free local persistence | FAIL | 76 partition-local duplicate trade IDs |
| queue drop observed | NOT VERIFIABLE | metrics were memory-only |
| reconnect-free collection | FAIL | repeated cross-market SNAPSHOT bursts |
| Binance orderbook identity | FAIL | 2,810,312 records are `UNKNOWN` |
| exchange feed completeness | NOT DIRECTLY VERIFIABLE | no Bithumb/Upbit public gap contract |
| cross-exchange timestamp semantics | FAIL | wall-clock reversals and missing Binance book timestamps |
| replay determinism | NOT VERIFIABLE | canonical raw replay bridge absent |
| alpha research ready | **false** | infrastructure blockers remain |
| live trading ready | **false** | safety invariant |

## Known Limitations

- Active current-hour files were excluded from the full scan.
- Duplicate detection is exact within each partition; cross-partition duplicate detection remains pending.
- The five-file independent SHA check is a sample; manifest creation itself rehashed every closed file.
- No persistent collector log exists for the current epoch.
- No exchange documentation establishes local/exchange clock synchronization error bounds.
- Three days would still be insufficient for strategy promotion even if this infrastructure audit passed.

## Next Step

1. Let the current epoch continue untouched until PID 30933 reaches its exact continuous 72-hour boundary, `2026-08-29 01:19:33 KST`, then finalize the last closed hour and rerun `--rehash-all`.
2. Do not use the current epoch for Binance-orderbook alpha research.
3. Start a separately labelled V9.1 epoch with preserved Binance combined-stream identity, monotonic receive time, durable operational metrics, graceful drain, and non-dropping bounded backpressure.
4. Add a canonical raw replay adapter and raw→derived→feature hash lineage.
5. Benchmark zstd only on finalized copies; retain canonical raw until decompression SHA and record counts match.
6. Preregister storage/depth/frequency changes before prospective V9.1 collection.
7. Continue collecting for weeks before any preregistered alpha study. Live trading remains disabled.
