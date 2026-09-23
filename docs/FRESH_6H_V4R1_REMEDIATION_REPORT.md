# Fresh 6H-v4r1 local remediation report

## Scope and historical status

- `HISTORICAL_V4R1 = OFFICIAL FAIL`
- `COLLECTOR_LIFECYCLE = CLEAN_SUCCESS`
- `AWS_LAUNCH = NOT_PERFORMED`
- `HISTORICAL_RUNTIME_OR_EVIDENCE_MUTATED = NO`
- `TEST_RESULTS_MUTATED = NO`

This report describes a new local candidate. It does not revise the five immutable v4r1 `FAIL` receipts or claim that a corrected classifier changes historical evidence.

## Defect A: duplicate equivalence and conflict propagation

[Bithumb's public trade schema](https://apidocs.bithumb.com/reference/%EC%B2%B4%EA%B2%B0-trade) exposes `sequential_id` as the nominal identity. For two copies with the same `sequential_id`, the comparison excludes only the top-level source-local `timestamp`. Every other present payload field remains semantic, including unknown future fields. The complete bytes of both copies still retain distinct SHA-256 hashes and source provenance. A semantic price, quantity, or other field difference remains a quarantined conflict.

The historical `_05` KRW-XRP ticker pair had the same top-level ticker timestamp but different `trade_timestamp` and `trade_volume`. [Bithumb's public ticker schema](https://apidocs.bithumb.com/reference/%ED%98%84%EC%9E%AC%EA%B0%80-ticker) does not document the generic ticker timestamp as a unique update identifier. The case is therefore classified `IDENTITY_COLLISION`. Ticker and orderbook now use their complete canonical payload SHA-256 as the fallback event identity: exact copies deduplicate, while different states remain distinct canonical events.

Conflict qualification is stored per `(UTC cohort, exchange, stream, market)` and uses the frame's receive time. The cumulative process metric remains available for observability but cannot poison unrelated logical observations.

## Defect B: active-active logical coverage

`feed_hour_coverage.evaluate_observation_gate` is now the single source for health, confirmation, heartbeat, and continuity reasons. The closed-hour finalizer consumes this result and adds only cohort qualification and sealed-universe checks.

For redundant Bithumb feeds, only a physical session that confirmed the specific logical feed contributes. Its interval begins at the latest of cohort start, connection, and confirmation; it ends at the earliest of disconnect and cohort end. Logical continuity is the union of those eligible intervals. An unconfirmed reconnect cannot fill a gap, and a true interval where neither source is eligible still emits `COLLECTION_GAP`.

All physical session segments, disconnect counts, reconnect counts, reasons, and successor IDs remain in the frozen observation and coverage evidence. A primary interruption with continuous confirmed secondary coverage therefore remains visible without becoming a false logical gap.

## Historical semantic replay

| Case | Corrected result |
| --- | --- |
| `_04` timestamp-only trade copies | duplicate, no conflict |
| `_05` timestamp-only trades | duplicate, no conflict |
| `_05` KRW-XRP ticker pair | two distinct canonical updates |
| `_06` primary reconnect, continuous secondary | physical interruption preserved; logical union complete |
| `_07` / `_08` timestamp-only trade copies | duplicate, no conflict |
| One scoped real trade conflict | only its Bithumb feed and receive-time cohort fail |
| True dual-source uncovered interval | `COLLECTION_GAP` and affected feed failure |

## Verification

- Focused reliability suite: `135 passed`.
- Full pytest: `1663 passed, 2 skipped in 165.70s`.
- Changed-file Pyright: `0 errors, 0 warnings, 0 informations`.
- Compileall: pass.
- `git diff --check`: pass.
- Protected `test-results/.last-run.json`: unchanged SHA-256, size, and mtime.
- 7,200-second accelerated deterministic soak: pass.

The soak observed timestamp-jitter duplicates `133433/133433`, semantic trade conflicts `7/7`, dual-source gaps `48/48`, 480 seconds of single-source outage, queue peak/final `2048/0`, dedup cache peak/final `10860/10680`, task count `1 -> 5 -> 1`, FD count `7 -> 7 -> 7`, RSS peak growth 7,569,408 bytes, writer errors 0, and unpersisted events 0. This is synthetic local evidence, not AWS, exchange, paper, alpha, or live qualification.

## Runtime semantic delta from `525a7d3`

1. Trade duplicate comparison ignores only the top-level transport timestamp for the same `sequential_id`; complete raw hashes and provenance remain intact.
2. Ticker and orderbook without a documented unique update ID use complete-payload identity.
3. Conflict qualification is isolated by logical feed and UTC cohort, using receive time at boundaries.
4. One authoritative observation gate evaluates writer health, source confirmation, logical heartbeat union, and logical interval union.
5. The finalizer no longer converts redundant physical reconnect counters into a logical gap.
6. Redundancy evidence records equivalence mode, stream, market, sources, and both hashes.
7. The soak and replay additions are validation-only and do not alter collection semantics.

## Readiness

`FRESH_6H_NEXT_READINESS = GO_FOR_AUTHORIZATION`

This means the local runtime candidate is ready for review and a separate exact-identity authorization. It does not authorize AWS execution. `ALPHA = UNPROVEN`, `PAPER = NOT_STARTED`, `LIVE = DISABLED`, and `PRIVATE_API = DISABLED`.
