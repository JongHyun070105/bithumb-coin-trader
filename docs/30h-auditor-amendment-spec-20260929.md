# Fresh30HTerminalBundle v2 Amendment Specification

AMENDMENT_RULESET = Fresh30HTerminalBundle v2
RUN_ID = aws-validation-observability-30h-run-20260929T055022Z-52f3d272
EPOCH = aws-validation-observability-30h-20260929-20260929T055022Z-52f3d272
RUNTIME_COMMIT = b4d482363e2f988dad9c6d29053f97e1e4160883
RUNTIME_TREE = 5c96ed79fee107c1604ee7018621835910221fdf
ORIGINAL_AUDITOR_SHA256 = 7e5ac931e7d2e1e7c900e56d1ddfa3111c4bb2470dc8f6a9e2d5c53884405b64
RULES_FROZEN_BEFORE_TERMINAL = YES

The run's final outcome was not observed while this specification was authored. The original auditor and its verdict remain immutable and are always reported beside the amended auditor and verdict. These rules apply regardless of the run result.

## Verdict interface

The amended auditor emits exactly one of `PASS`, `FAIL`, or `NOT_VERIFIABLE` as `overall_status`. An authoritative observed contract violation is `FAIL`; missing, denied, ambiguous or weak evidence is `NOT_VERIFIABLE`; `PASS` requires every required check to pass. Any `FAIL` takes precedence over `NOT_VERIFIABLE`. `PASS` does not establish statistical alpha, paper readiness, or live readiness.

## Required bundle files

- `bundle-manifest.json`, schema `Fresh30HTerminalBundle`, version 2, exact run ID/epoch/runtime commit/tree, declared relative source paths, evidence classifications, hashes and receipt scopes, plus the external capture-manifest SHA256.
- `sealed/sealed-manifest.json`, `sealed/identity.json`, sealed runtime JSON, `launch-command.json`, `launch.sh`, and `launch-ec2.sh` when present. Each sealed copy is checked against the manifest's official identity hash and artifact hashes; identity's `sealed_artifact_hashes` must agree. Bundle-copy hashes are checked independently. The expected sealed-manifest hash must come from the preserved launch-time evidence, not a hash generated after terminalization.
- `terminal/result.json`, `terminal/collector-lifecycle.json`, terminal local receipt, and collector metrics/lifecycle evidence required by the checks.
- `terminal/systemd-invocation.jsonl` (raw journal rows for the exact run-specific collector unit across the collector's full run window) plus parsed `terminal/systemd-terminal.json`. Preserve every invocation ID found in that window; more than one means the unit was restarted/recreated and fails. Parsed fields include `InvocationID`, `Result`, `ExecMainStatus`, `ExecMainCode`, `MainPID`, start time, stop time, runtime duration, watchdog result and received signal. If a required field is absent from the journal, it stays `NOT_VERIFIABLE`; a query against a removed transient unit is not a substitute.
- `terminal/s3-readback/terminal-receipt.json` from an unversioned exact-key `GetObject` of the stable terminal key, and `terminal/s3-readback.json` with request time, bucket/key, GET outcome, HTTP/error code, returned `VersionId`, `ETag`, `ContentLength` and SHA256. Capture `requested_version_id: null` to prove the lookup was for the key's current object. Exact remote bytes must equal the local terminal receipt bytes. `AccessDenied`/403 and timeout/network errors mean `NOT_VERIFIABLE`; an authorized, confirmed missing key means `FAIL`.
- Local cohort, slot and file receipt trees; the explicit remote readback set for receipt types that require S3. Expected cohort/feed inventory and classifications (`NATIVE`, `RECONSTRUCTED_OBSERVATION`, or `MISSING`) are in the bundle manifest.
- `terminal/cohorts.json` and `terminal/receipt-inventory.json`, using the normalized v2 feed-slot and typed durability schemas implemented by `scripts/audit_fresh_30h_terminal_v2.py`. The cohort summary must preserve and hash-link every frozen source journal, including opening/ending partial cohorts.
- `terminal/finalization-trace.json`, with an explicit source classification. A reconstructed trace cannot satisfy a requirement for native instrumentation.
- `terminal/capture-manifest.json`, schema `Fresh30HTerminalCapture` v1. It binds every terminal payload other than itself and the generated hash index by relative path, byte length and SHA256, and records the run/epoch/runtime identity. The auditor requires its SHA256 as a separate command-line anchor. Preserve that digest outside the exported bundle (for example, commit/push a small capture record before export); do not compute a replacement digest from the bundle during adjudication.
- `terminal/evidence-hash-index.json`, listing every bundle payload file except the index itself, with normalized relative path, size and SHA256; unlisted/extra files are rejected or classified by the versioned allowlist.

## Optional files

- Timestamped terminal receipt and its remote object, if produced. Local presence is required when the runtime writes it; a remote timestamped copy is optional under current code.
- Explicit skipped-partial-hour cohort receipt, when emitted. It is local-only and excluded from qualifying S3 mirror parity.
- Full-health snapshots (including a T+15 snapshot) and observer telemetry when available. A missing informational snapshot is not a terminal failure unless a pre-existing contract requires it.
- A finalization trace only when sourced from native runtime instrumentation. Any derived trace must be named `RECONSTRUCTED_OBSERVATION`, cite its immutable source records, and cannot satisfy a native-trace requirement.

## Hash and trust rules

1. Verify the preserved official sealed-manifest hash from launch-time evidence before trusting its `identity_sha256`.
2. Verify exact identity bytes against that hash.
3. Verify every sealed artifact against the sealed-manifest hash and identity's `sealed_artifact_hashes`; verify runtime commit/tree against exact pinned values.
4. Verify each sealed file copy in the terminal bundle against its original source hash.
5. Hash runtime evidence and all bundle payloads, but never treat a regenerated local index as a trust root. The index is completeness/tamper detection only. Runtime receipt claims require exact S3 readback/VersionId or the scoped systemd journal source. A local-only mutable result with no independent provenance cannot produce PASS.
6. Reject path traversal, absolute paths, symlinks, duplicate normalized paths, duplicate expected slots, duplicate version-ID entries for the terminal key, hash format errors, and unindexed payload files. Distinct historical versions are not by themselves an acceptance failure.
7. Verify the externally anchored capture manifest before trusting terminal result, journal, S3 readback, receipt, or normalized cohort evidence. Each terminal file, including every frozen source journal and every local/remote receipt copy, must match its capture-manifest size and SHA256. A missing source is `NOT_VERIFIABLE`; a source changed after capture or an unbound extra terminal payload is `FAIL`.

The amended auditor requires independent command-line anchors for run ID, epoch, runtime commit/tree, the SHA256 of the preserved prelaunch `sealed-manifest.json`, and the SHA256 of the preserved post-run capture manifest. Values inside `bundle-manifest.json` are consistency fields and never trust roots. The exporter validates explicit source files against these anchors before creating the bundle. It refuses incomplete required payload sets, unsafe or duplicate destinations, mismatched sealed identity/artifact hashes, mismatched terminal source hashes, symlink or hard-linked sources, and any existing output directory. It copies through a marked sibling staging directory, checks copied hashes and source stability, fsyncs, then publishes by rename. A regenerated local index does not authenticate an edit: the auditor rechecks both external anchors, identity/artifact hashes, the complete terminal source inventory, payload inventory, and receipt readback fields.

Missing required evidence is `NOT_VERIFIABLE`. Contradictory identity/hash, unexpected receipt, duplicate slot, wrong cohort, confirmed authorized 404 or byte mismatch is `FAIL`. A denied or expired S3 request is `NOT_VERIFIABLE`, never evidence of absence. S3 states are represented distinctly as `PRESENT`, `ABSENT`, `ACCESS_DENIED`, `NOT_CHECKED`, or `ERROR`; only a confirmed authorized absence is `FAIL`.

### Terminal version and observer-churn contract

Acceptance requires a successful, unversioned `GetObject` for exactly `<identity.s3_prefix>/terminal/terminal-receipt.json`, the expected bucket, HTTP 200, a nonempty returned `VersionId`, `ETag`, request ID/caller/time, exact `ContentLength`, and body SHA256/bytes equal to the captured local terminal receipt. `requested_version_id` must be null. A pinned request for an older version cannot establish that the stable key currently resolves to the receipt. A confirmed authorized 404 is `FAIL`; access denied, an incomplete required object listing, or unavailable readback provenance is `NOT_VERIFIABLE`.

Version history is separated from that acceptance check. The returned `VersionId` must occur exactly once in a supplied `version_ids` inventory; duplicate IDs or a returned ID absent from the inventory are `FAIL`. More than one distinct historical VersionId is a diagnostic warning and does not fail by count alone. If captured `latest_delete_marker` is true, that contradicts a successful unversioned GET and is `FAIL`. A delete marker that causes the unversioned GET to return a confirmed 404 is already handled as `FAIL`. A delete marker in an older history position is diagnostic. The preterminal spec prohibited duplicate terminal version evidence, but did not preregister that only one distinct historical version may ever exist; the auditor therefore does not impose that count.

Observer churn is allowed only for listing-only keys below the exact `<identity.s3_prefix>/observability/` prefix. Newer `latest.json` and minute-object versions do not affect acceptance. Required terminal, cohort, slot, and file receipt keys are still independently checked for their exact bytes and returned versions. Churn cannot conceal malformed or mismatched bytes for any required key. Any other undeclared key remains `FAIL`.

### Parsing, types, and source precedence

Every v2 JSON input uses strict parsing: duplicate object keys and nonstandard `NaN`/`Infinity` constants are rejected. Security-relevant counters require exact integers (`bool` is not accepted as an integer), timestamp/string fields require their declared types, and malformed evidence cannot default into success.

Duration has one source hierarchy. The sealed runtime `duration_seconds` is the frozen threshold. Collector start and end come from the first and last frozen coverage-journal observation; the resulting interval is the sole duration calculation. Collector metrics start, supervisor result bounds, and exact systemd InvocationID start/stop bounds only corroborate it. Optional duration numbers in result or metrics never replace or extend the journal-derived interval. No source is selected because it alone reaches the threshold.

Systemd acceptance requires all run-window journal rows for the exact run-specific collector unit to share the parsed InvocationID, nondecreasing valid timestamps, exactly one `Starting` record within five seconds of parsed `start_time`, and exactly one main-process terminal record within two seconds of parsed `stop_time`. Parsed `Result`/`ExecMainCode`/`ExecMainStatus` must match the terminal line; exit must be zero, with no restart and a matching parsed interval. Capturing the full unit window means a same-name unit recreation adds a second InvocationID and fails. A journal missing either boundary is partial and `NOT_VERIFIABLE`; post-run `systemctl` defaults or observer-unit state cannot substitute.

Finalization trace fields can satisfy `PASS` only when the pinned runtime is independently known to emit the versioned native trace, and the trace is not classified as reconstructed. A `NATIVE_INSTRUMENTATION_PRESENT` string alone is not provenance. The pinned current runtime `b4d482363e2f988dad9c6d29053f97e1e4160883` has no native finalization-trace emitter, so its finalization check is always `NOT_VERIFIABLE`; all seven reconstructed checks remain unresolved, even when reconstructed values appear clean or were captured after terminalization.

## Hour and feed contract

The actual launch command uses direct `collection_duration_seconds=108000`; it does not pass a V3 30-full-hour schedule file. The sealed runtime schedule records `target_full_hours=30`, `qualification_rule=IMMEDIATE`, and `required_qualifying_full_hours=0`. The collector's `FeedHourCoverageTracker` always labels its opening cohort `TOUCHED_PARTIAL`, including a start exactly at `:00`; shutdown emits the ending cohort, which may be a zero-duration partial at an exact boundary. Derive the observed interval from frozen journals (`observation_start_utc` on the opening cohort and `observation_end_utc` on the ending cohort), label the aggregation `RECONSTRUCTED_OBSERVATION`, and cross-check the start against `collector_metrics.collector_started_at` plus supervisor/systemd bounds. Do not use `result.ended_at` as the collector end; it is written after child shutdown/finalization. For 108000 seconds beginning at `2026-09-29T09:34:57Z`, producer semantics yield start partial `2026-09-29_09`, qualifying hours `2026-09-29_10` through `2026-09-30_14` (29), end partial `2026-09-30_15`, and 29 × 76 = 2204 expected feed slots. The same 29 full hours result from starts at `:00`, `:01`, `:34`, and `:59`; the contract derives this by skipping the producer's opening partial cohort, not by hard-coding a convenient count. A different actual start/end yields a different set.

The 76-feed set is checked against the exact pinned runtime implementation: Bithumb 20 markets × 3 streams, Binance 4 symbols × 2 streams, Upbit 4 markets × 2 streams. It is labeled `RECONSTRUCTED_OBSERVATION` because the sealed identity stores `feed_count` but not a feed array.

## Receipt durability matrix

| Receipt type | Durability | Required parity scope |
|---|---|---|
| `COHORT_RECEIPT` for qualifying full hours | `BOTH_REQUIRED` | Exact local/remote object and hash; one per expected cohort |
| `SLOT_RECEIPT` for coverage slots | `BOTH_REQUIRED` | Exact local/remote object and hash; one per expected cohort/feed |
| `FILE_RECEIPT` for raw/coverage artifacts with an S3 remote key | `BOTH_REQUIRED` | Compare each local receipt to its named remote mirror/version |
| `SKIPPED_PARTIAL_HOUR_RECEIPT` | `LOCAL_ONLY` | Preserve and classify; exclude from required S3 equality set |
| Stable `TERMINAL_RECEIPT` | `BOTH_REQUIRED` | Exact stable-key GET, equal bytes/SHA256, captured unique VersionId |
| `TIMESTAMPED_TERMINAL_RECEIPT` | `OPTIONAL` | Preserve local copy; remote mirror optional under current runtime |
| `OTHER` operational snapshots/logs | `OPTIONAL` | Hash and list source; do not treat as a cohort/slot receipt |

An unexpected in-scope S3 receipt is `FAIL`; a known optional timestamped terminal receipt is allowlisted. A missing required remote object or differing bytes is `FAIL`. 403/AccessDenied, network timeout, expired credentials, or incomplete version listing is `NOT_VERIFIABLE`.

## Finalization and shutdown rules

- Seven original finalization checks remain distinct: scheduler retries, finalizer retries, recovery invocations, duplicate finalization, `closed_at_utc` stability, evidence-hash stability, and restart/idempotency-path exposure.
- The current runtime does not emit the full structured trace. Receipts, write-ahead state and logs can reconstruct only what they directly preserve; reconstructed observations are never native instrumentation. If any required distinction cannot be proved, the relevant check remains `NOT_VERIFIABLE`.
- Publisher `-15` is accepted only when `publisher_stopped_after_collector=true`, collector exit is zero, no supervisor signal/timeout occurred, and the final supervisor result is otherwise successful. `-15` before collection completion, exit 1, or unexpected kill remains `FAIL`.
- `terminal_witness.s3_uploaded` is not proof of upload. For the current run, require exact stable-key GET, bytes/hash parity and VersionId. Future code must not persist `s3_uploaded=true` before `put_object` returns; a local false negative after process kill is acceptable, a false positive is not.
- Normal publisher and archive-scheduler `-15` are accepted only with their respective `*_stopped_after_collector=true`, collector exit 0, `overall_status=PASS`, no received supervisor signal, and no forced timeout. Other negative exits and nonzero codes fail.
- Observer unit mismatch is an informational diagnostic only; it cannot establish collector health. The launch command currently names a `bitcoin-trader-transient-...service` observer for the 108000-second run, so observer-derived fields remain untrusted until bound to the correct unit.
- Receipt immutability needs at least two separate read-only observations for every qualifying cohort receipt, with equal SHA256 and VersionId. The declared observation set must equal the qualifying cohort-receipt set. One capture records a baseline only; missing observations remain `NOT_VERIFIABLE`.

## Original and amended result fields

The post-run report must keep `ORIGINAL_CONTRACT_VERDICT`, `ORIGINAL_FAILED_CHECKS`, `AMENDED_CONTRACT_VERDICT`, and `AMENDED_FAILED_CHECKS` separate, alongside collector body, finalization, terminal witness, S3 parity, and reliability closure. No verdict is executed from the incomplete live run.
