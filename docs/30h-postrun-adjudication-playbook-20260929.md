# Fresh 30H Post-Run Adjudication Playbook

This playbook is prepared before the run's terminal outcome. It is read-only with respect to AWS and the runtime. Do not run adjudication until the exact run has naturally ended. Never infer completion from an observer unit that reports a different unit name.

## Pinned identity and stop boundary

- Run: `aws-validation-observability-30h-run-20260929T055022Z-52f3d272`
- Epoch: `aws-validation-observability-30h-20260929-20260929T055022Z-52f3d272`
- Runtime: `b4d482363e2f988dad9c6d29053f97e1e4160883` / `5c96ed79fee107c1604ee7018621835910221fdf`
- Expected natural body end: about `2026-09-30T15:34:57Z` (`2026-10-01 00:34:57 KST`)
- Exact collector unit: `bitcoin-trader-30h-aws-validation-observability-30h-run-20260929T055022Z-52f3d272.service`
- SSM instance observed during launch: `i-008bc503c1136349f`, region `ap-northeast-2`.
- Data root: `/var/lib/bitcoin-trader/30h-validation/aws-validation-observability-30h-20260929-20260929T055022Z-52f3d272`
- Runtime worktree: `/var/lib/bitcoin-trader/runtime-worktrees/aws-validation-observability-30h-20260929-20260929T055022Z-52f3d272`
- S3 bucket/prefix: `bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433` / `market-data/temporary/aws-validation-observability-30h-20260929-20260929T055022Z-52f3d272`

## 1. Wait and establish terminal state

Wait until after the expected UTC end plus the existing configured finalization allowance. Use read-only SSM `GetCommandInvocation`/guest inspection. Do not start, stop, restart, kill, or relaunch any unit. Before capturing terminal state, require both:

1. The exact collector unit is no longer `active` (it may already be unloaded after the transient unit ends); and
2. Exact-identity `result.json` exists and contains a terminal state with its run ID/epoch matching the pinned identity.

If the unit is absent, do not use `systemctl show` as the sole terminal record. Search the persistent journal using the recorded exact InvocationID. At launch, the candidate InvocationID `41d3722ed54b4c6e88930e5b26f82993` was **not** found in any preserved local artifact and remains `NOT_VERIFIABLE`; independently discover/capture the actual ID from the exact unit before it disappears or from the journal's exact unit/run interval.

## 2. Capture one immutable read-only evidence snapshot

From the instance, copy/read the exact run's `result.json`, `collector-lifecycle.json`, collector metrics, frozen coverage journals, local receipts, and all terminal/finalization files under the exact data/audit roots. Record source path, size, SHA256, capture time, and read errors for every file. A missing file is explicit absence; do not synthesize it.

Export raw JSON journal rows for only the exact run-specific collector unit across its complete run window, retaining the original bytes and hashing them. Do not prefilter by InvocationID: preserve every ID found so a same-name unit recreation is detectable. Acceptance requires exactly one unique InvocationID, one start record aligned to parsed start time, and one terminal event aligned to parsed stop time. Parse and preserve `InvocationID`, `Result`, `ExecMainStatus`, `ExecMainCode`, `MainPID`, start/stop timestamps, elapsed duration, watchdog result, received signal, and restart count. If a field cannot be recovered from durable journal data, mark it missing. Do not broaden to all host logs and then infer unit events.

Keep capture errors distinct: confirmed object absence after an authorized exact lookup is a failure; `AccessDenied`/403, expired AWS credentials, timeout, incomplete listing, and network errors are `NOT_VERIFIABLE`.

## 3. Verify receipts and cohort/feed coverage

Derive the qualifying hour list from frozen-journal observation bounds under the pinned runtime's `FeedHourCoverageTracker` semantics; preserve the opening and ending partial cohorts. For this run's actual `09:34:57Z` start and 108000-second duration, the pre-registered expectation is 29 full UTC cohorts × 76 feeds = 2204 slots. Compare the derived list with every local cohort receipt, every slot receipt, each frozen journal, supervisor result and collector start timestamp. Do not substitute supervisor `result.ended_at` for the collector's last observed event bound.

Inventory receipt types and enforce the frozen durability table in the v2 spec. Qualifying cohort, slot and file receipts marked `BOTH_REQUIRED` must match their exact S3 objects byte-for-byte and SHA256-for-SHA256. Capture a complete paginated read-only S3 prefix listing, bucket/prefix, time, caller identity, list outcome and every object key/version/ETag. An incomplete or denied listing cannot prove parity. Preserve local-only skipped partial-hour receipts and exclude them from required S3 equality. Any unexpected in-scope S3 receipt is a failure unless it matches a predeclared optional type.

For terminal witness, treat `s3_uploaded=true` as an untrusted claim until an unversioned GET of the exact stable terminal key succeeds. Record `requested_version_id: null`, the returned `VersionId`, ETag, ContentLength, byte length, SHA256, exact bucket/key, HTTP status and request provenance. Save response bytes and compare them exactly with local `terminal-receipt.json`. Distinct historical versions are diagnostic; duplicate version-ID entries fail. A latest delete marker contradicting the successful unversioned GET fails. Read-only repeat observations are required before asserting immutability; one observation is only a baseline.

## 4. Preserve pre-terminal trust anchors

Use `sealed-manifest.json` from the prelaunch evidence commit `cce61af5b2e2fa6c2404a3555ca55177ceef8dbc`; verify the file equals that committed Git blob before using its SHA256. Its audit-time SHA256 is `dbb0afcba47f804b0fa555f3babbfc0491f5b763ba90546d332ff861aafb4de1`. This digest was computed during the offline audit but is derived from a pre-run committed object; do not claim it was a standalone SHA256 logged at launch. Verify identity bytes against the manifest, then each launch artifact hash and each copied bundle file. Preserve the prelaunch Git commit ID and remote ref as the external anchor.

## 5. Export and run both auditors

Use the explicit-source exporter only after the terminal snapshot is complete. It refuses missing sources and any existing output directory, stages under a clearly marked incomplete name, verifies copied hashes and source stability, then publishes by atomic rename. Create `terminal/capture-manifest.json` over every captured terminal file (path, size, SHA256, source and capture timestamp), verify its inputs, then preserve its SHA256 outside the bundle before export (commit/push the capture record or retain it in the independent audit log). Pin run ID, epoch, runtime commit/tree, the externally anchored sealed-manifest SHA256, and this separate capture-manifest SHA256 on the command line. Do not invoke the exporter against a partial live run.

Run the original V3 auditor using its original evidence layout and pinned commit/tree arguments. Preserve the original JSON/Markdown/stdout and hashes without editing or replacing them. Run `scripts/audit_fresh_30h_terminal_v2.py` on the v2 bundle and keep its JSON/stdout separately. If the original cannot be assembled due missing required inputs, report its result as `NOT_VERIFIABLE`; do not use the v2 schema as a substitute for original output.

Report `ORIGINAL_CONTRACT_VERDICT` and `AMENDED_CONTRACT_VERDICT` as separate fields, with separate failed/unknown checks. Keep collector body, finalization, terminal witness, S3 parity and reliability closure separate. Reconstructed observations stay labeled `RECONSTRUCTED_OBSERVATION`; they never satisfy native instrumentation requirements.

## 6. Existing tooling and exact invocation shapes

The deterministic offline tools are:

- `scripts/export_fresh_30h_terminal_bundle.py`: explicit source-to-destination copy and v2 manifest/index creation; no AWS calls.
- `scripts/audit_fresh_30h_terminal_v2.py`: offline v2 audit, outputs exactly `PASS`, `FAIL`, or `NOT_VERIFIABLE`.
- The original `scripts/audit_fresh_30h_v3_terminal.py`: preserved unchanged and remains the original-contract authority.

Example amended invocation after completion (source paths are intentionally explicit and must be filled from the completed snapshot):

```sh
python3 scripts/export_fresh_30h_terminal_bundle.py \
  --output-dir ./postrun-bundle \
  --run-id aws-validation-observability-30h-run-20260929T055022Z-52f3d272 \
  --epoch aws-validation-observability-30h-20260929-20260929T055022Z-52f3d272 \
  --runtime-commit b4d482363e2f988dad9c6d29053f97e1e4160883 \
  --runtime-tree 5c96ed79fee107c1604ee7018621835910221fdf \
  --sealed-manifest-sha256 dbb0afcba47f804b0fa555f3babbfc0491f5b763ba90546d332ff861aafb4de1 \
  --capture-manifest-sha256 <digest-preserved-outside-the-bundle> \
  --source sealed/sealed-manifest.json=/path/to/preserved/sealed-manifest.json \
  --source sealed/identity.json=/path/to/preserved/identity.json \
  --source sealed/runtime.json=/path/to/preserved/runtime.json \
  --source terminal/capture-manifest.json=/path/to/captured/terminal/capture-manifest.json
```

Repeat `--source DEST=PATH` for every required v2 payload listed in `REQUIRED_PAYLOADS`, plus every file listed in the capture manifest. The above is a shape only and is deliberately incomplete; the exporter must reject it. Never copy live or incomplete evidence into a bundle that could be mistaken for terminal.

## 7. Stop conditions and verdicts

Stop collection after a complete, hashed snapshot and exact remote readbacks. Do not write probes, receipts or evidence to the run's S3 prefix. A missing native finalization trace remains `NOT_VERIFIABLE` under the frozen amended contract even if all body/receipt evidence passes. No result establishes alpha, dataset qualification, paper readiness, live readiness, or private API authorization.
