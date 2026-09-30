# Fresh 30H Auditor v2 Preterminal Red Team — 2026-09-30

Run: `aws-validation-observability-30h-run-20260929T055022Z-52f3d272`

Epoch: `aws-validation-observability-30h-20260929-20260929T055022Z-52f3d272`

Runtime: `b4d482363e2f988dad9c6d29053f97e1e4160883` / tree `5c96ed79fee107c1604ee7018621835910221fdf`
Status at time of this review: `IN_PROGRESS`; neither auditor has adjudicated this live run.

## Findings and fixes

1. Python's default JSON parser accepts duplicate keys and keeps the last value. Re-indexing `{"overall_status":"FAIL","overall_status":"PASS"}` could therefore hide a contradictory field. V2 now rejects duplicate keys recursively, rejects `NaN`/`Infinity` and float overflow, and returns `NOT_VERIFIABLE` for malformed evidence.
2. Python treats booleans as integers. `false` could satisfy counter-equals-zero checks. Security-critical counters now require exact `int` types; metric, systemd and finalization regressions cover this.
3. The systemd journal exit line was not tied to parsed `stop_time`. A clean old terminal line could coexist with a later fabricated parsed interval. V2 now binds a single terminal line to parsed stop time within two seconds, requires a single `Starting` line within five seconds of parsed start time, and rejects more than one InvocationID in the full run-window journal for the run-specific collector unit.
4. A reconstructed finalization trace could set `evidence_classification=NATIVE_INSTRUMENTATION_PRESENT` and pass with zero counters. This was reproduced. The pinned b4 runtime has no native finalization-trace emitter, so its v2 finalization check is now always `NOT_VERIFIABLE`; a self-declared native label is not proof. Reconstructed source classification independently remains `NOT_VERIFIABLE`.
5. The implementation treated two different historical terminal-object VersionIds as a failure, although the frozen text only prohibited duplicate terminal version evidence and did not clearly require a one-version history. The contract now accepts a successful unversioned exact-key GET whose returned VersionId matches the receipt bytes; duplicate IDs, a mismatching/missing returned ID, a pinned historical GET, and a latest delete marker fail. Additional distinct historical versions are diagnostic warnings, not count-based failures.
6. The exporter previously copied directly into the destination and could publish a directory after partial work or copy bytes different from the validated source. It now copies in 1 MiB chunks to a marked sibling staging directory, validates digest and source stability, fsyncs data/directories, and atomically renames only after the manifest and index are complete. Existing output directories are rejected. The auditor streams hashes and re-hashes indexed payloads before returning.

`NEW_FALSE_PASS_PATHS_FOUND=4` (duplicate-key ambiguity, boolean/integer confusion, systemd terminal time disconnected from parsed stop time, and reconstructed finalization self-labeling). `NEW_FALSE_PASS_PATHS_FIXED=4`; each has a strict parsing/type or provenance regression. The historical terminal-version-count issue was an overstrict false failure, not a false PASS, and is separately clarified.

## Required false-PASS attack matrix

Every case below ran against synthetic bundles, not the live run. A detected contract violation returns `FAIL`; missing, denied, ambiguous or incomplete evidence returns `NOT_VERIFIABLE`.

| Attack | Result | Evidence/test |
|---|---|---|
| A. Edit evidence and regenerate local inventories | `FAIL` | External capture/seal anchors still reject the edited evidence. |
| B. Rewrite sealed identity/artifact hashes but keep the frozen seal anchor | `FAIL` | Runtime identity hash-chain checks. |
| C. Duplicate a valid identity/runtime payload under another path | `FAIL` | Bundle allowlist/copy-count checks. |
| D. Supply two `identity.json` files | `FAIL` | Bundle scope and duplicate sealed copy checks. |
| E. Supply two `runtime.json` files | `FAIL` | Bundle scope and duplicate sealed copy checks. |
| F. Use `../`, absolute, or nested traversal paths | `FAIL` | Capture-manifest canonical relative-path check. |
| G. Symlink a required file outside the bundle | `FAIL` | No-follow regular-file verification. |
| H. Symlink a required file to another valid bundle file | `FAIL` | Any symlink in the bundle is rejected. |
| I. Substitute a hard link | `FAIL` | Singly linked regular-file requirement. |
| J. Duplicate a capture source entry | `FAIL` | Duplicate capture paths are rejected. |
| K. Omit a capture source while leaving its file present | `FAIL` | Unbound terminal payload check. |
| L. Add undeclared terminal evidence | `FAIL` | Capture completeness check. |
| M. Add a second same-basename sealed identity in another directory | `FAIL` | Bundle scope. |
| N. Claim `s3_uploaded=true` but omit readback metadata or bytes | `NOT_VERIFIABLE` | Upload boolean never substitutes for exact GET evidence. |
| O. Point readback at the wrong bucket/key | `FAIL` | Exact target comparison against sealed identity. |
| P. Use a wrong, duplicated, or historically pinned VersionId | `FAIL` | Readback/version inventory checks; multiple distinct old IDs alone are diagnostic. |
| Q. Supply correct metadata but different response bytes | `FAIL` | Byte, SHA256 and length parity. |
| R. Represent an unavailable object as `AccessDenied` | `NOT_VERIFIABLE` | Typed `ACCESS_DENIED`; never mapped to absence. |
| S. Represent an object as absent without authorized confirmation | `NOT_VERIFIABLE` | Unknown absence remains `ERROR`; confirmed authorized absence is `FAIL`. |
| T. Label reconstructed finalization values as native | `NOT_VERIFIABLE` | Reproduced pre-fix false PASS; fixed with runtime capability guard and regression. |
| U. Give reconstructed finalization a timestamp after terminalization | `NOT_VERIFIABLE` | Reconstructed classification cannot satisfy native evidence. |
| V. Reorder systemd journal timestamps | `FAIL` | Monotonic timestamp order check. |
| W. Cherry-pick duration from conflicting result/metrics/journal fields | `FAIL` or `NOT_VERIFIABLE` when journal evidence is short/missing | Frozen coverage journals alone compute elapsed time; result/metrics only corroborate. |
| X. Omit systemd terminal record but provide success defaults | `NOT_VERIFIABLE` | Requires start and terminal journal records. |
| Y. Use wrong InvocationID or recreate the same unit name | `FAIL` when both invocations appear in full unit-window capture | Every unit-window row must share the captured InvocationID. |
| Z. Substitute observer-unit journal state for collector-unit state | `FAIL` | Raw journal must name the exact `bitcoin-trader-30h-<RUN_ID>.service`; observer diagnostics remain informational. |

Additional parser/type attacks cover duplicate keys for identity, status, hashes, S3, and systemd fields; `true`/`false` versus `1`/`0`; null and empty VersionIds; string ContentLength; huge PIDs; `NaN`, `Infinity`, and `1e999`; duplicate slots; duplicate terminal events; and missing or late evidence. The focused suite has a 3,000-row receipt inventory case.

## Frozen outcome-independent rules

- Duration threshold: sealed runtime `duration_seconds` (the pinned launch used 108000).
- Duration interval: first/last frozen coverage-journal observation only. Metrics start, supervisor bounds, and the exact collector systemd invocation corroborate; optional result/metrics duration fields cannot extend a short journal interval.
- Observer churn: listing-only keys under the exact `<s3_prefix>/observability/` prefix are diagnostic. Required terminal/cohort/slot/file receipt keys are still individually verified. Churn cannot hide changed required bytes; other undeclared keys fail.
- S3 terminal acceptance: unversioned GET of exact `<s3_prefix>/terminal/terminal-receipt.json`, exact bucket/key, HTTP 200, null `requested_version_id`, returned VersionId, ETag, ContentLength, captured byte length, SHA256 and byte equality. `AccessDenied` is not absence. Acceptance uses the current unversioned GET body; older version bodies are not separately captured or compared. Tests cover one and multiple IDs, exact current bytes passing, corrupt current bytes failing even with self-consistent hash/length metadata, duplicate or missing IDs, delete markers, and pinned historical GETs. Historical byte equality is not an acceptance requirement.
- Finalization: reconstructed evidence cannot pass. The current b4 runtime has no native trace emitter, so all seven finalization distinctions remain `NOT_VERIFIABLE` for this run.
- No paths under `.codex`, `.agents`, `.commandcode`, memory directories, `/tmp`, or private workstation roots are required by auditor/exporter behavior. The only test temporary directories use pytest's injected `tmp_path`.

## Schedule and resource behavior

The preterminal schedule is in `30h-current-run-expected-cohorts-20260930.json` and its Markdown counterpart. It uses the captured collector start `2026-09-29T09:34:57.959160Z`, the sealed duration 108000, and the pinned opening-partial rule. Result: opening partial `2026-09-29_09`, 29 qualifying cohorts `2026-09-29_10` through `2026-09-30_14`, ending partial `2026-09-30_15`, 76 feeds per hour and 2204 expected slots. No terminal outcome was used.

Exporter copies and hashes in 1 MiB chunks and bounds its preflight JSON reads at 64 MiB. Auditor hashes in 1 MiB chunks and bounds each JSON document at 64 MiB; decoded-object memory can exceed source size. Receipt processing is linear in inventory size apart from deterministic sorting. The 3000-row synthetic inventory completed in the focused suite; no real raw market data was copied for this resource check. Peak memory was not separately measured.

## Reproducibility, dry run, and independent review

- Focused v2 tests: `133 passed` on the current worktree.
- Original b4 auditor: `15 passed`; SHA256 is still `7e5ac931e7d2e1e7c900e56d1ddfa3111c4bb2470dc8f6a9e2d5c53884405b64`.
- Full repo suite after the final code/test additions: `1845 passed, 2 skipped, 181 subtests passed`.
- No configured lint or type-check tool is declared in the repository configuration; `git diff --check` passed.
- Exporter determinism and exporter-to-auditor end-to-end behavior are exercised by synthetic tests: valid fixture `PASS`, edited evidence `FAIL`, missing/ambiguous evidence `NOT_VERIFIABLE`. Export interruption at copy/manifest/index leaves only a marked incomplete stage. Repeated audit calls return identical report objects for unchanged input.
- Synthetic postrun procedure dry run: the original V3 test suite builds its own original-layout fixture and exercises report writing with `PASS`, `FAIL`, and `NOT_VERIFIABLE`; the v2 CLI test builds a capture manifest, invokes the actual exporter CLI, audits the v2 bundle through the auditor CLI, and exercises `PASS`, `FAIL`, and `NOT_VERIFIABLE`. The final-report layout keeps `ORIGINAL_CONTRACT_VERDICT` and `AMENDED_CONTRACT_VERDICT` side by side. These are separate schema-specific synthetic fixtures, not one live-run adjudication; the live capture/export/adjudication steps remain deferred until terminalization.
- Clean reproduction from detached pushed commit `6dee108c20eea70bc4885c6ce9f0680af43ca105`: `133` v2 tests collected and `133 passed`; the checkout was clean before and after. Synthetic CLI coverage exercised exporter and auditor `PASS`, `FAIL`, and `NOT_VERIFIABLE`, and both command help paths ran. The schema map, spec, and schedule table were present; the 29 cohort / 2204 slot arithmetic rechecked from the recorded start, duration, and producer rule. No `.codex`, `.agents`, `.commandcode`, memory, `/tmp`, private-user-path, or session-output dependency was found in the production auditor/exporter/spec/schema/test files.
- The schedule JSON now records the exact values extracted from all four source artifacts and their SHA256 values. All four source hashes were rechecked against the preflight evidence snapshot. The start-time health and independent observer snapshots are not packaged in the clean audit checkout, so a future clean reader can recompute the schedule from the recorded values but cannot recompute those two source-file hashes without the original snapshot. This is a provenance-availability limitation, not an auditor/exporter runtime dependency.
- Independent second review used one `gemini-low` consult restricted to the v2 spec, auditor, exporter and tests. It identified duplicate JSON-key and bool/int counter risks, plus a lower-confidence hardlink concern. The first two were reproduced as false-PASS paths and fixed; hardlink substitution is explicitly rejected by the single-link check. This is one advisory review, not semantic proof; local regression evidence is authoritative.

## Live run and patch isolation

One read-only local STS check at 2026-09-30 20:16 KST returned `InvalidClientTokenId`. No login was attempted again. No current AWS/guest snapshot, current cohort result, live resource projection, or fresh reconnect count is available. The latest saved local health snapshot is stale (`2026-09-29T11:40:48Z`): it reported collector active, all three exchanges connected, queue 0, writer errors 0, Bithumb reconnect count 2, and observer/collector unit mismatch. Do not treat it as current evidence.

| Patch | Current run has it | Next runtime only | Reason |
|---|---:|---:|---|
| V2 auditor/exporter hardening in this audit branch | No evidence it was deployed; this session did not deploy | No runtime code change | Offline audit-only tools; AWS authentication unavailable for remote confirmation. |
| Native finalization event stream | No | Yes | b4 has no native trace emitter; current check stays `NOT_VERIFIABLE`. |
| Observer unit binding repair | No | Yes | Existing observer unit mismatch remains diagnostic; live observer was not modified. |
| Terminal witness upload-order repair | No | Yes | Current witness behavior is unchanged; v2 requires independent exact-key readback. |

This session made no AWS/IAM/runtime/observer/S3 changes, did not restart or stop the collector, and did not adjudicate the live run. `CURRENT_30H_STATUS=IN_PROGRESS`, `RELIABILITY_CLOSED=NO`, `ALPHA=UNPROVEN`, `LIVE=DISABLED`, `PRIVATE_API=DISABLED`.
