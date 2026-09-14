# AWS 30H V3 Remediation Design

## 1. Status and authority boundary

This design remediates the three root causes demonstrated by the consumed AWS
30H V2 validation identity. It does not reinterpret or repair V2.

The immutable status remains:

- `30H V2 = FAIL`
- `INFRA VALIDATION CLOSED = NO`
- `READY FOR 24/7 RESEARCH = NO`
- `ALPHA = UNPROVEN`
- `PAPER = NOT STARTED`
- `LIVE = DISABLED`
- `PRIVATE API = DISABLED`

The V2 identity `aws-validation-30h-20260912-6576f63` must never be launched
again. Its evidence was preserved through PR #5 and regular merge commit
`b6f379f363d5e4eb16f2dc835062205639c3d7c8` before this remediation branch was
created.

This work may change local source, tests, evidence schemas, and prelaunch
artifacts. It does not authorize root access, IAM mutation, Terraform apply,
diagnostic S3 writes, trading, a V3 authorization artifact, or a V3 launch.

## 2. Goals

The remediation must deliver all of the following:

1. Finalization work is bounded by dirty, ending, or missing evidence rather
   than total run history.
2. Every qualifying cohort has exactly 76 first-class coverage slots, each in
   `DATA_PRESENT`, `VERIFIED_ZERO_EVENT`, or `FAILED`.
3. Zero-event evidence is control evidence, never an empty or fabricated market
   RAW partition.
4. RAW data and coverage evidence share one immutable archival implementation
   while retaining state-dependent validation rules.
5. Actual-start evidence is normalized through explicit, versioned schemas
   with exact V2 legacy compatibility and fail-closed rejection.
6. A new V3 preparation is minted only after remediation reaches authoritative
   main, and the task stops before authorization or launch.

## 3. Non-goals

- V2 artifacts, receipts, reports, S3 objects, or verdicts are not rewritten.
- Low-activity feeds are not removed from the sealed 76-feed universe.
- Market events are not fabricated or backfilled.
- A second archive implementation is not introduced for coverage evidence.
- Finalization timeout increases are not used to hide linear work.
- CloudTrail permissions are not broadened to make historical IAM activity
  verifiable.
- Paper, live, or private APIs remain disabled.

## 4. Root Cause C: strict actual-start normalization

Root Cause C is implemented first because it is isolated, deterministic, and
provides a clean contract boundary for later V3 sealing.

### 4.1 Component boundary

Create `src/bithumb_coin_trader/actual_start_evidence.py` with:

```python
@dataclass(frozen=True)
class ActualStartIdentity:
    collector_epoch: str
    collector_run_id: str
    runtime_commit: str
    runtime_config_fingerprint: str

@dataclass(frozen=True)
class NormalizedActualStartEvidence:
    schema_version: int
    evidence_kind: str
    collector_epoch: str
    collector_run_id: str
    runtime_commit: str
    runtime_config_fingerprint: str
    actual_start_time_utc: str
    source: str
    captured_at_utc: str

def normalize_actual_start_evidence(
    payload: Mapping[str, object],
    expected: ActualStartIdentity,
) -> NormalizedActualStartEvidence: ...
```

`scripts/compose_epoch_contract.py` delegates all actual-start interpretation to
this module. The composer no longer chains aliases with `or` expressions.

### 4.2 Canonical schema v2

The new canonical input has exactly these required semantic fields:

```json
{
  "schema_version": 2,
  "evidence_kind": "systemd-transient-actual-start-evidence",
  "collector_epoch": "<sealed epoch>",
  "collector_run_id": "<sealed run id>",
  "runtime_commit": "<40-char lowercase commit>",
  "runtime_config_fingerprint": "<64-char lowercase sha256>",
  "actual_start_time_utc": "<UTC timestamp>",
  "source": "<non-empty source identity>",
  "captured_at_utc": "<UTC timestamp>"
}
```

UTC timestamps may use `Z` or an explicit zero offset. Naive timestamps and
non-zero offsets are rejected rather than converted silently. The normalized
form uses a single UTC representation.

### 4.3 Exact V2 legacy schema v1

The only legacy adapter is selected by the exact discriminator pair:

```text
schema_version = 1
evidence_kind = systemd-transient-actual-start-evidence
```

It accepts the tracked V2 structure with its legacy names
`runtime_code_commit`, `runtime_config_fingerprint`,
`actual_start_time_utc`, `observed_pre_launch_utc`,
`observed_post_launch_utc`, and `systemd_unit`. It maps:

- `runtime_code_commit` to `runtime_commit`
- `observed_post_launch_utc` to `captured_at_utc`
- `systemd_unit` to the canonical `source`

The full set of required and allowed v1 keys is declared as constants. A
missing required key, an unexpected canonical alias, or a structure outside
that exact allow-list is rejected. The tracked sanitized fixture is copied from
the real V2 actual-start artifact and retains the real non-secret identity and
timestamps.

### 4.4 Fail-closed rules

The normalizer rejects:

- unsupported schema versions or evidence kinds;
- canonical and legacy aliases mixed in one payload;
- duplicate semantic fields under multiple names, whether equal or
  conflicting;
- wrong epoch, run ID, runtime commit, or configuration fingerprint;
- missing identity or source fields;
- invalid, naive, or non-UTC timestamps; and
- a legacy object that is not the exact supported V2 shape.

Errors use stable reason codes while avoiding secret or full-payload logging.

## 5. Root Cause A: incremental and bounded finalization

### 5.1 Design principle

`MultiExchangeMicrostructureCollector.generate_all_manifests()` is removed from
the shutdown finalization path. The finalizer never discovers work by scanning
all historical RAW files. Work is registered when a partition is first opened,
then transitioned as the partition closes and archive evidence becomes
terminal.

### 5.2 Durable file-based work index

Create `src/bithumb_coin_trader/incremental_finalizer.py`. It stores:

```text
<run-root>/finalization-progress/
  summary.json
  pending.json
  entries/<entry-id>.json
  .lock
```

This follows the repository's existing atomic JSON and `fcntl` locking
patterns. It avoids a database dependency and, crucially, lets shutdown load
only the bounded `pending.json` index rather than enumerate all entries.

An entry ID is the SHA-256 of the canonical epoch/run/cohort/feed/RAW-path
identity. Every entry contains:

- schema version and state: `PENDING`, `REUSED`, `RECOMPUTED`, or `FAILED`;
- environment, epoch, run ID, cohort, exchange, stream, market, and feed ID;
- RAW path, stat identity, size, and SHA-256;
- manifest path and manifest file SHA-256;
- terminal receipt path, receipt file SHA-256, receipt state, artifact kind,
  and the receipt's source identity;
- created, started, and completed UTC timestamps; and
- a stable failure reason code when failed.

Entry and summary writes use write-to-temporary, file `fsync`, `os.replace`,
and parent-directory `fsync`. State transitions and pending-index replacement
occur under the run-scoped lock. The summary keeps counters incrementally; it
does not recount all entries at shutdown.

### 5.3 Historical reuse trust chain

A closed historical partition becomes `REUSED` before shutdown only when all
of the following agree exactly:

- epoch and run ID;
- cohort, exchange, stream, market, and canonical feed identity;
- RAW relative path, size, and SHA-256;
- manifest identity and manifest file SHA-256;
- terminal archive receipt identity and terminal state;
- `artifact_kind = RAW_DATA`; and
- the receipt's source path, size, SHA-256, and manifest binding.

The archive pipeline establishes this chain while it is already reading and
verifying the source. It then records the terminal binding in the progress
entry. Finalization may perform metadata `stat` checks, but it must not open or
read a `REUSED` historical RAW file. The cryptographic source assertion comes
from the previously verified manifest/terminal-receipt chain, not a second RAW
scan.

If any identity or hash is contradictory, the entry becomes `FAILED`.
Finalization must not recompute and overwrite an archived contradiction.

### 5.4 Dirty and ending work

The writer registers a partition as `PENDING` before its first append. The
archive scheduler removes it from pending only after the complete terminal
receipt binding is durable. At collector shutdown, the finalizer reads only the
remaining pending IDs.

For each remaining stable ending partition it:

1. persists/retains `PENDING` with the exact source identity;
2. computes the manifest once from the source;
3. atomically writes and verifies the manifest;
4. writes a `RECOMPUTED` entry and removes the ID from `pending.json`; and
5. updates summary counters in the same locked transition.

Missing, unstable, foreign, or contradictory work becomes `FAILED`. A process
interruption leaves either the old complete files or the new complete files;
temporary files never qualify. Re-entry skips valid `REUSED` and `RECOMPUTED`
entries and processes only pending work.

### 5.5 Lifecycle metrics

Collector lifecycle and metrics expose at least:

- `reused_count`
- `recomputed_count`
- `pending_count`
- `failed_count`
- `historical_raw_files_opened`
- `historical_raw_bytes_read`

`final_manifest_flush_observed=true` requires `pending_count=0`,
`failed_count=0`, durable finalizer summary state `COMPLETE`, and no collector
error. Partial progress remains visible after interruption.

### 5.6 Scale acceptance

Synthetic 1-, 10-, and 30-cohort histories use the same dirty ending tail.
All three must report:

- `historical_raw_files_opened = 0`
- `historical_raw_bytes_read = 0`
- identical `recomputed_count`

A deliberately slow manifest function proves that only the dirty tail incurs
the delay. Timing samples are recorded over repeated runs. The V3 finalization
ceiling is derived only after implementation from measured p95/p99 plus a
documented margin; no timeout increase precedes the algorithmic fix.

## 6. Root Cause B: exact 76-slot coverage contract

### 6.1 Root invariant

The V3 root invariant is:

```text
EXPECTED COVERAGE SLOTS PER QUALIFYING COHORT = 76 EXACT
```

Every sealed feed/hour has exactly one first-class coverage object in one of:

- `DATA_PRESENT`
- `VERIFIED_ZERO_EVENT`
- `FAILED`

Missing, duplicate, foreign-identity, and unknown-state objects fail the
cohort. V2 remains governed by its original contract and cannot use this schema
retroactively.

### 6.2 Coverage evidence schema v1

Create `src/bithumb_coin_trader/feed_hour_coverage.py` with a strict immutable
`FeedHourCoverage` model and `FeedHourCoverageTracker`. A closed object is
written at:

```text
coverage/<YYYY-MM-DD_HH>/<exchange>/<stream>/<market>.coverage.json
```

Its semantic fields include:

```text
schema_version = 1
artifact_kind = COVERAGE_EVIDENCE
environment_id
collector_epoch
collector_run_id
runtime_commit
runtime_config_fingerprint
cohort_utc
interval_start_utc
interval_end_utc
exchange
stream
market
feed_identity
configured = true
coverage_state
event_count
first_event_timestamp | null
last_event_timestamp | null
session_segments[]
disconnect_count
reconnect_count
writer_error_count
queue_dropped_events
unpersisted_event_count
fatal_writer_error_type | null
data_artifact_binding | null
closed_at_utc
evidence_sha256
```

`evidence_sha256` is the canonical hash of the object excluding that field.
The interval is UTC and half-open: `[hour_start, hour_end)`.

### 6.3 Session and subscription evidence

Each `session_segments` item contains:

- exchange and locally generated connection/session ID;
- connected and disconnected UTC timestamps;
- sealed requested subscription set and its canonical hash;
- confirmation method and confirmation timestamp;
- normalized confirmed subscription/feed set and response/evidence hash;
- heartbeat observations and maximum heartbeat gap;
- disconnect reason and reconnect successor ID; and
- epoch/run identity.

A confirmation is valid only for its owning WebSocket connection. Reconnect
immediately invalidates it. The new connection must request and confirm its own
subscriptions before contributing evidence to later slots.

For a zero-event slot, session evidence must cover the entire cohort without an
unobserved connection gap. A reconnecting cohort with any collection gap cannot
claim that no event occurred and resolves to `FAILED`. After the new session is
confirmed, later uninterrupted cohorts may qualify again. This rule is stricter
than merely recording reconnect chronology and prevents an event during a gap
from being silently treated as zero.

### 6.4 Exchange-specific confirmation

Upbit uses `LIST_SUBSCRIPTIONS` on the same owning connection. The request,
normalized response, timestamp, session ID, epoch, and run ID are recorded.

Binance Spot uses `LIST_SUBSCRIPTIONS` on the same owning connection. The
requested stream set and returned stream set must exactly cover the sealed
Binance feed subset for that session.

Bithumb has no relied-upon equivalent list query. Its proof is:

```text
connection established
-> one sealed subscription request
-> feed-specific SNAPSHOT or event observed for each requested feed
-> confirmation valid only while that same connection remains live
```

There is no periodic re-subscription. A reconnect ends every previous Bithumb
confirmation and repeats the one-request, feed-specific confirmation process on
the new connection.

### 6.5 Bithumb feasibility evidence

On 2026-09-14, a credential-free read-only probe used the current public
endpoint and the sealed 20 Bithumb markets. A single request for
orderbook/trade/ticker produced feed-specific `SNAPSHOT` messages for all 60 of
60 requested feeds. Three subsequent independent sessions each produced 60 of
60 snapshots and passed WebSocket Ping/Pong in approximately 0.2 seconds.

This establishes implementation feasibility, not future V3 run truth. Runtime
coverage still records and validates its own owning-session evidence. If a V3
session fails to confirm any feed, that feed cannot become
`VERIFIED_ZERO_EVENT`.

### 6.6 State predicates

`DATA_PRESENT` requires:

- `event_count > 0`;
- a non-empty RAW market-data artifact;
- a matching manifest;
- immutable compressed archive and terminal receipt;
- restore verification and fullscan/DQ success; and
- exact coverage-to-RAW/manifest/receipt identity and hash binding.

`VERIFIED_ZERO_EVENT` requires:

- membership in the sealed feed universe;
- `event_count = 0` and null first/last event timestamps;
- a successfully requested and confirmed subscription on every session segment
  used for the cohort;
- continuous owning-session liveness over the complete cohort;
- zero writer errors, queue drops, and unpersisted events;
- null fatal writer error; and
- an immutable archived coverage artifact with terminal receipt and restore
  verification.

Any missing proof produces `FAILED`. A zero-byte RAW file is never used as
zero-event evidence.

## 7. One generic immutable artifact archive

### 7.1 Artifact kinds

Refactor the existing archive pipeline around a strict descriptor:

```python
class ArtifactKind(str, Enum):
    RAW_DATA = "RAW_DATA"
    COVERAGE_EVIDENCE = "COVERAGE_EVIDENCE"

@dataclass(frozen=True)
class ImmutableArtifact:
    kind: ArtifactKind
    source_path: Path
    relative_path: str
    environment_id: str
    collector_epoch: str
    collector_run_id: str
    cohort: str
    exchange: str
    stream: str
    market: str
    source_sha256: str
    source_size: int
    source_record_count: int | None
    manifest_path: Path | None
    manifest_sha256: str | None
```

Compression, immutable upload/reuse, remote verification, restore verification,
locking, and cleanup eligibility remain one implementation. Kind-specific
validators run before the common archive stages.

### 7.2 Receipt compatibility

Archive receipt schema v3 uses neutral `source_*` fields plus
`artifact_kind`. RAW v3 receipts require a manifest binding and record count.
Coverage v3 receipts require a valid canonical coverage hash and do not claim a
market record count.

Receipt schema v2 remains readable through an explicit legacy adapter for
historical audit only. Existing v2 receipts are never rewritten. V3 production
artifacts require schema v3.

### 7.3 State-dependent archive and fullscan

Every one of the 76 slots archives its coverage evidence. In addition:

- `DATA_PRESENT` archives and scans its RAW data chain.
- `VERIFIED_ZERO_EVENT` archives and validates only its coverage evidence
  chain; it has no RAW market-data artifact.
- `FAILED` remains durable diagnostic evidence but cannot qualify the cohort.

Fullscan validates market JSONL records only for `DATA_PRESENT`. Coverage scan
validates coverage JSON schema, canonical hash, identity, session proof, receipt,
remote checksum, and restore checksum. It never adds a synthetic record to
scientific record totals.

The strict auditor first constructs the exact 76-slot map, rejects duplicates
and foreign objects, applies each state's predicate, and only then computes the
cohort verdict. For example, 74 `DATA_PRESENT`, 2
`VERIFIED_ZERO_EVENT`, and 0 failed/missing slots may pass; 74, 1, and 1 missing
must fail.

## 8. End-to-end data flow

1. The sealed runtime contract creates the exact 76 feed-slot identities for
   each UTC hour.
2. Each WebSocket connection gets a new session ID and writes subscription and
   liveness control evidence.
3. The tracker increments per-feed/hour event counts while the writer persists
   real market records.
4. The writer registers RAW partitions in the finalization pending index.
5. At hour close, the tracker resolves each slot to a candidate state and
   atomically writes its coverage object. Insufficient proof produces `FAILED`.
6. The archive scheduler validates and archives coverage evidence for all slots
   and RAW data only for `DATA_PRESENT` slots.
7. Terminal RAW receipts transition historical finalization entries to
   `REUSED`; the collector shutdown finalizer handles only entries still
   pending.
8. Fullscan and the strict auditor apply state-dependent rules and enforce the
   exact 76-slot root invariant.
9. Offline contract composition and epoch manifest building bind the normalized
   actual-start evidence and new coverage semantics.

## 9. Failure behavior

The system fails closed on:

- progress-index corruption or an entry/index mismatch;
- manifest, receipt, source, identity, or artifact-kind contradiction;
- missing or duplicate coverage objects;
- any unproven zero-event slot;
- reconnect without fresh subscription confirmation;
- a connection gap during a candidate zero-event cohort;
- writer, queue-loss, or unpersisted-event evidence;
- archive, restore, coverage-scan, or RAW fullscan failure; and
- malformed or ambiguous actual-start evidence.

Failures are persisted with stable reason codes. They are never repaired by
overwriting immutable source evidence, inventing market data, widening a
timeout, or weakening the audit predicate.

## 10. TDD and verification design

Implementation follows RED -> GREEN -> REFACTOR for Root C, Root A, and Root B
in that order. Every new behavior is first demonstrated by a test that fails
for the intended missing behavior.

Focused tests include:

- exact V2 legacy normalization and every required rejection case;
- sealed historical manifest/receipt reuse without opening RAW;
- dirty tail recomputation;
- contradictory terminal receipt failure without overwrite;
- interrupted progress and restart/re-entry;
- 1/10/30 cohort bounded scale with identical dirty tail;
- slow manifest generation bounded by dirty count;
- `DATA_PRESENT` complete chain;
- `VERIFIED_ZERO_EVENT` complete coverage chain;
- unproven zero event, missing slot, duplicate slot, and foreign identity;
- reconnect invalidating old confirmation and later-session reconfirmation;
- a reconnect gap preventing zero-event qualification;
- writer, queue, unpersisted, and fatal-writer failures preventing zero-event;
- Upbit and Binance same-session list-subscription normalization; and
- Bithumb feed-specific snapshot/event confirmation.

Before the remediation PR, all of the following must be freshly green:

- focused suites;
- 1/10/30 scale tests;
- full pytest with zero failures;
- Pyright with zero errors and zero warnings;
- compileall;
- JSON fixture/schema validation; and
- `git diff --check`.

The unchanged baseline on the new worktree produced one transient
`PermissionError` in the existing bounded-supervisor signal test during the
first full run. The same test then passed once alone, passed 20 consecutive
runs, and the unchanged full suite passed `1034 passed, 2 skipped`. This is
recorded as baseline context and is not silently folded into remediation scope.

## 11. Git and release sequence

1. V2 failure evidence remains isolated in PR #5 and its regular merge commit.
2. Root C, Root A, and Root B receive separate logical test+implementation
   commits on `codex/aws-30h-v3-remediation-20260914`.
3. A clean independent review requires `Critical = 0` and `Important = 0`.
4. The remediation PR uses a regular merge commit, followed by post-merge gates
   on authoritative main.
5. Only then is a completely new V3 epoch, run ID, namespace, worktree,
   fingerprint, deterministic archive hash, runtime seal, launch command,
   launch provenance, and wrapper created.
6. V3 prelaunch performs composer and coverage dry runs, scale verification,
   exact guest commit/tree and clean-worktree checks, empty new S3-prefix check,
   zero matching processes/systemd executions, read-only IAM safety checks,
   authoritative-state Terraform plan `0/0/0`, and capacity review.
7. The V3 preparation branch is committed and pushed but not merged.
8. `launch_authorized` remains `false`, `actual_start_time_utc` remains `null`,
   and no systemd service or collection run is started.

CloudTrail IAM history remains `NOT VERIFIABLE` unless an already-authorized
read-only path exists. That gap neither changes V2's failure nor permits IAM
broadening.

## 12. Acceptance decision

The remediation is acceptable only if the exact requested focused, scale,
full-suite, static, syntax, diff, and independent-review gates pass. V3 may be
reported `READY TO AUTHORIZE NEW 30H = YES` only when its independent review has
zero Critical and Important findings and every read-only prelaunch gate passes.

Even in that state, this task stops. `NEW 30H STARTED` must remain `NO` until a
separate explicit human authorization is provided.
