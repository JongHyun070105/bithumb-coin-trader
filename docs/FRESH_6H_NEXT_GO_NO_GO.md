# Fresh 6H next-run readiness after v4r1

## Decision

`FRESH_6H_READINESS = GO_FOR_AUTHORIZATION`

Fresh 6H-v4r1 remains an immutable official `FAIL`. A new local remediation candidate now fixes both validation-semantics defects, passes the complete local gate, and is ready for separate human authorization of a new Fresh 6H. This decision does not authorize timer arming or AWS launch.

- `FRESH_6H_V4R1_TECHNICAL_GATE = FAIL`
- `RECEIPTS_PRESENT = 5 / 5`
- `COHORT_PASS = 0 / 5`
- `RECEIPT_IMMUTABILITY = 5 / 5`
- `SCHEDULER_HOL = PASS`
- `DEFECT_A = FIXED_LOCALLY`
- `DEFECT_B = FIXED_LOCALLY`
- `KRW_XRP_TICKER_CASE = IDENTITY_COLLISION`
- `HISTORICAL_REPLAY = PASS`
- `TRUE_DUAL_SOURCE_GAP_DETECTION = PASS`
- `LAUNCH_12_AUTHORIZED = NO`
- `FRESH_30H_V3_LAUNCHED = NO`
- `EARLIER_PARTIAL_REMEDIATION_COMMIT = a4fb7e5391fa370856160d57f95ac63c51764d29`
- `FINAL_CANDIDATE_COMMIT = enclosing candidate Git revision; reported after freeze`
- `LOCAL_REMEDIATION_AWS_VALIDATED = NO`

The candidate commit includes the earlier duplicate-scoping remediation and the final union-aware validator fix. The historical v4r1 runtime, receipts, terminal audit, and sealed evidence were not changed.

## Current remediation verification ledger

| Gate | Result | Evidence and limit |
| --- | --- | --- |
| Focused reliability tests | PASS | `135 passed` across coverage, finalizer, collector, fault injection, historical replay, and redundancy soak. |
| Full pytest | PASS | `1663 passed, 2 skipped in 165.70s`; zero failures on the final run. |
| Changed-file Pyright | PASS | `0 errors, 0 warnings, 0 informations` for every Python file changed from runtime `525a7d3`, including the replay test. |
| Compileall | PASS | `.venv/bin/python -m compileall -q src/bithumb_coin_trader scripts tests`. |
| Diff check | PASS | `git diff --check`. |
| Historical semantic replay | PASS | `_04`, `_05`, `_06`, `_07`, and `_08` failure patterns replay with corrected classification; immutable historical receipts remain `FAIL`. |
| Active-active logical union | PASS | Single-source reconnects remain physical evidence without a logical gap; a true uncovered union interval still emits `COLLECTION_GAP`. |
| Conflict isolation | PASS | Conflicts are scoped by exchange, market, stream, and receive-time UTC cohort; Binance, Upbit, unrelated Bithumb feeds, and adjacent cohorts remain clean. |
| Local accelerated soak | PASS | 7,200 virtual seconds; timestamp-jitter trade duplicates `133433/133433`, semantic conflicts `7/7`, dual-source gaps `48/48`, single-source outage seconds 480, one UTC cohort boundary. |
| Resource trend | PASS within model | Tasks `1 -> 5 -> 1`, FD `7 -> 7 -> 7`, RSS peak growth 7,569,408 bytes, queue peak/final `2048/0`, cache peak/final `10860/10680`, writer errors 0, unpersisted 0. Synthetic local evidence only. |
| Protected test results | PASS | `test-results/.last-run.json`: 45 bytes, mtime `2026-09-08T14:33:39+0900`, SHA-256 `e22df5d0991eb28c09093b1e678b3fa8cd1fab48185d38e67cf79fb6e63ad5ea`; unchanged and unstaged. |

The new soak evidence is `reliability-artifacts/local-validation/bithumb-redundancy-soak-20260922-v4r1-remediation.json`, SHA-256 `4c994db5c459e3bb910f42b8ca2f8e6a1928c57c49789554c25d19bfa48d64f3`. It is an accelerated deterministic model, not cloud or exchange qualification.

## Historical prelaunch readiness baseline

The following section is retained as the historical state that authorized v4r1. It no longer describes current readiness.

- `LOCAL_VALIDATION_CANDIDATE = YES`
- `VALIDATION_CANDIDATE_COMMIT = 525a7d339260481e63e36f1a3948ce08eb15d9be`
- `VALIDATION_CANDIDATE_TREE = 08c89c63e0a5de8e94450a2715ea19664211b091`
- `AWS_NEW_LAUNCHES = 0`
- `FRESH_30H_V2_TECHNICAL_GATE = FAIL`
- `ALPHA = UNPROVEN`
- `PAPER = NOT_STARTED`
- `LIVE = DISABLED`
- `PRIVATE_API = DISABLED`

The candidate source, tests, reconnect policy, redundancy configuration, deduplication rules, coverage semantics and telemetry schema are frozen at the commit and tree above. This document is a later metadata-only record. Any source or test change requires a new candidate commit and a complete re-run of the local gates.

## PR #13 red-team result

`PR13_RED_TEAM = PASS`

The review found and fixed these correctness-level defects:

1. A single Bithumb socket was the common failure domain for all 60 logical feeds.
2. Reconnect evidence bypassed the injected UTC clock.
3. Receive, heartbeat and reconnect tasks needed a single decision and teardown owner with explicit cancellation and gathering.
4. A close failure could obscure the original receive/reconnect cause.
5. Staleness needed an absolute monotonic deadline rather than a fresh timeout around each receive attempt.
6. Coverage treated physical disconnects as logical gaps even when another confirmed source could provide continuity.
7. Deduplication needed bounded state, provenance, conflict quarantine, and rollback when canonical queue admission is cancelled.
8. Scheduler completion checks accepted missing identity dimensions and legacy path scope as identity.
9. Per-feed confirmation evidence inherited a session-level timestamp rather than each feed's first confirmation.
10. Naive launch timestamps and invalid schedule relations were not fully rejected.
11. One background-finalization test called an asyncio primitive from a worker thread, which hung under asyncio debug mode.

PR #13 was still open and draft at `e8e6811e4838c001ce8c606c2796b4e194ef92f8` when checked on 2026-09-21. GitHub reported no reviews, issue comments, inline review comments, or unresolved correctness-level review threads on PR #13.

## Historical architecture and protocol decision

`REDUNDANCY_DECISION = ACTIVE_ACTIVE`

Two physical public Bithumb WebSocket connections subscribe to the same 20 markets and three public streams. Their evidence remains physically separate; logical coverage is the union of confirmed source intervals. One physical failure therefore does not create a logical gap while the other source provides timely confirmed data. An interval where both are absent still fails.

The selected policy and alternatives are detailed in [BITHUMB_REDUNDANCY_DESIGN_20260921.md](BITHUMB_REDUNDANCY_DESIGN_20260921.md). The official documentation checked on 2026-09-21 states a limit of 10 WebSocket connection requests per IP per second, describes multi-type requests and ping/pong/idle behavior, and documents trade and orderbook identity fields. The collector serializes attempts to at most four per second and uses exactly two connections. The public pages do not state a simultaneous connection cap, per-connection subscription cap, or duplicate public-subscription rule. This bounded design does not infer an unlimited allowance; exchange acceptance remains a future authorized-run observation.

Deduplication is first-arrival canonical with no lookahead. Trade uses `sequential_id`; its comparison excludes only the top-level source-local `timestamp`, while every other current or future payload field remains semantic and fail-closed. Orderbook and ticker use the complete canonical payload SHA-256 because their schemas do not document a unique update identity. Exact duplicates persist one canonical raw record plus compact source provenance. A semantic difference for the same trade identity keeps the first raw record, quarantines the conflicting copy, increments `conflicting_duplicate_frames`, and fails only that logical feed's receive-time UTC cohort.

Established sessions with a valid frame and at least 30 seconds of life get one bounded 50–200 ms retry; initial or short-lived failures use exponential backoff from about one second to 30 seconds. All attempts share the four-per-second limiter. Receive cancellation is safe for installed `websockets 17.1`; close uses its one-second `close_timeout`, an outer two-second bound, and transport abort only after timeout or close exception.

## Historical v4r1 prelaunch verification ledger

| Gate | Result | Evidence and limit |
| --- | --- | --- |
| Full pytest | PASS | `1649 passed, 2 skipped in 153.57s`; zero failures. |
| Changed-file Pyright | PASS | `0 errors, 0 warnings, 0 informations` across every changed Python source, script, and test module. |
| Full-repository Pyright measurement | BASELINE DEBT | `607 errors, 0 warnings`; the release gate uses zero errors in changed files. The repository-wide debt is not claimed fixed. |
| Compileall | PASS | `python -m compileall -q src/bithumb_coin_trader scripts tests`. |
| Diff check | PASS | `git diff --check`. |
| Historical replay and fault injection | PASS | `53/53` cases in `test_bithumb_gap_fault_injection.py`, including named cases 25–48 and the frozen historical observation model. The active-active counterfactual is explicitly synthetic. |
| Targeted reliability bundle | PASS | `150 passed` across launch artifacts, scheduler, fault injection, collector, redundancy soak, and observer readiness. |
| Async task/resource checks | PASS | `82 passed` with `PYTHONASYNCIODEBUG=1` and `ResourceWarning` promoted to errors; tasks are cancelled and gathered in tested paths. |
| Scheduler HOL and identity | PASS | Matching identity-bound FAIL stays FAIL and does not block later cohorts; foreign, missing, conflicting, corrupt, wrong-cohort, partial, PASS, restart, multiple-FAIL and legacy-sidecar cases are covered. |
| Local accelerated soak | PASS | 7,200 virtual seconds, 429,120 canonical frames, 400,313 deduplicated frames, 7/7 injected conflicts classified, expected 48 seconds of dual-failure logical gap, 24 close timeouts, 24 connect timeouts and 24 queue-pressure events. |
| Resource trend | ACCEPTABLE | Task count `1 -> 5 -> 1`, leak count 0; FD `7 -> peak 7 -> 7`; RSS peak growth 6,078,464 bytes; queue peak 2,048 and final 0; stable dedup samples at 10,680 entries; unpersisted events 0; writer errors 0. This deterministic model does not prove leak freedom. |
| Protected test results | PASS | `test-results/.last-run.json` remained untracked, size 45, mtime `2026-09-08T14:33:39+0900`, SHA-256 `e22df5d0991eb28c09093b1e678b3fa8cd1fab48185d38e67cf79fb6e63ad5ea`. It was neither edited nor staged. |
| Candidate freeze | PASS | Candidate commit and tree are recorded above; this document is metadata-only. |

The soak evidence is stored at `reliability-artifacts/local-validation/bithumb-redundancy-soak-20260921.json`, SHA-256 `59184222ce39e5589c41dcf33e16142a30cd249874171331a73b7c55c86e3e7a`.

## Residual limits

- Two sockets share the same endpoint, route and exchange. A common failure can stop both and correctly fails logical coverage.
- The deterministic soak models state, queues and resources; it does not reproduce real Bithumb or cloud behavior and cannot prove leak freedom.
- Compact duplicate provenance adds local I/O. The model exercised bounded queue pressure, but six-hour and thirty-hour disk behavior remains for separately authorized validation.
- Public Bithumb documentation does not publish all simultaneous/subscription limits. The implementation meets the published connection-attempt limit and deliberately caps itself at two connections.
- The historical incident's server, network path, host socket and task-level initiating cause remain `NOT_VERIFIABLE`.
- Full-repository Pyright debt remains outside the changed-file gate.

## Next action

The next single action is human review of the frozen remediation commit and tree reported with this document's delivery. If accepted, issue a new, explicit authorization for one Fresh 6H using that exact candidate and newly sealed launch artifacts. Do not arm or launch from this document. Fresh 30H remains gated on a complete Fresh 6H PASS.
