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

- `bundle-manifest.json`, schema `Fresh30HTerminalBundle`, version 2, exact run ID/epoch/runtime commit/tree, declared relative source paths, evidence classifications, hashes and receipt scopes.
- `sealed/sealed-manifest.json`, `sealed/identity.json`, sealed runtime JSON, `launch-command.json`, `launch.sh`, and `launch-ec2.sh` when present. Each sealed copy is checked against the manifest's official identity hash and artifact hashes; identity's `sealed_artifact_hashes` must agree. Bundle-copy hashes are checked independently. The expected sealed-manifest hash must come from the preserved launch-time evidence, not a hash generated after terminalization.
- `terminal/result.json`, `terminal/collector-lifecycle.json`, terminal local receipt, and collector metrics/lifecycle evidence required by the checks.
- `terminal/systemd-invocation.jsonl` (raw exact journal export scoped to the captured InvocationID) plus parsed `terminal/systemd-terminal.json`. Parsed fields include `Result`, `ExecMainStatus`, `ExecMainCode`, `MainPID`, start time, stop time, runtime duration, watchdog result and received signal. If a required field is absent from the journal, it stays `NOT_VERIFIABLE`; a query against a removed transient unit is not a substitute.
- `terminal/s3-readback/terminal-receipt.json` from exact `GetObject` of the stable terminal key, and `terminal/s3-readback.json` with request time, bucket/key, GET outcome, HTTP/error code, `VersionId`, `ETag`, byte length and SHA256. Exact remote bytes must equal the local terminal receipt bytes. `AccessDenied`/403 and timeout/network errors mean `NOT_VERIFIABLE`; an authorized, confirmed missing key means `FAIL`.
- Local cohort, slot and file receipt trees; the explicit remote readback set for receipt types that require S3. Expected cohort/feed inventory and classifications (`NATIVE`, `RECONSTRUCTED_OBSERVATION`, or `MISSING`) are in the bundle manifest.
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
6. Reject path traversal, absolute paths, symlinks, duplicate normalized paths, duplicate expected slots, duplicate terminal object versions, hash format errors, and unindexed payload files.

## Hour and feed contract

The actual launch command uses direct `collection_duration_seconds=108000`; it does not pass a V3 30-full-hour schedule file. The sealed runtime schedule records `target_full_hours=30`, `qualification_rule=IMMEDIATE`, and `required_qualifying_full_hours=0`. For the run's existing full-hour evidence semantics, skip the touched starting cohort, include only UTC hours wholly contained in `[actual_start, actual_end)`, and never promote the ending partial cohort. For a 108000-second interval beginning at `2026-09-29T09:34:57Z`, this yields start partial `2026-09-29_09`, expected qualifying hours `2026-09-29_10` through `2026-09-30_14` (29), ending partial `2026-09-30_15`, and 29 × 76 = 2204 expected feed slots. A different actual start/end yields a different derived set; no number is hard-coded as a convenience.

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

## Original and amended result fields

The post-run report must keep `ORIGINAL_CONTRACT_VERDICT`, `ORIGINAL_FAILED_CHECKS`, `AMENDED_CONTRACT_VERDICT`, and `AMENDED_FAILED_CHECKS` separate, alongside collector body, finalization, terminal witness, S3 parity, and reliability closure. No verdict is executed from the incomplete live run.
