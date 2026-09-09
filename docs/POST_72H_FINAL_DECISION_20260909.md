# Post-72H Final Decision

This report supersedes the blocked-state decision in
`docs/POST_72H_FINAL_AUDIT_20260908.md`. It independently evaluates evidence at
`31846b7edbcfc8108e1ecfca792e7c009e1136cb` against the frozen runtime at
`9532cebc902856d954bf80b51dbe567b543dc8e2` and the Phase 6.3 audit rules at
`0a45a66324e0e49cbe02db4b47d9529353b5f20d`.

## 1. Executive verdict

| Classification | Item | Decision |
|---|---|---|
| VERDICT | 72H process | **FAIL — Case B** |
| FACT | Collector data-duration counter | 259200.03 seconds |
| VERDICT | Frozen supervisor PASS | **NOT SATISFIED** |
| VERDICT | Official deep DQ | NOT RUN |
| VERDICT | Qualification | NOT RUN |
| VERDICT | Official prospective alpha dataset | INELIGIBLE |
| VERDICT | Untouched holdout | INELIGIBLE / NOT CREATED |
| VERDICT | Non-qualifying diagnostic DQ | NOT RUN; limited observations only |
| VERDICT | Exploratory engineering use | CONDITIONAL YES |
| VERDICT | Alpha | UNPROVEN |
| VERDICT | Paper | NOT STARTED |
| VERDICT | Live / private API | DISABLED |

The collector retained a large, potentially useful RAW corpus and recorded zero
writer/drop/unpersisted counters. That does not satisfy the frozen unattended
process contract. The supervisor forced termination, did not observe final manifest
flush, and the archive/receipt topology covers only 3 of 72 expected cohorts.

## 2. Git and evidence integrity

- FACT: remote evidence branch
  `gemini/post72h-interactive-evidence-20260908` resolves to
  `31846b7edbcfc8108e1ecfca792e7c009e1136cb`; its parent is
  `e698e9e628033d2df7e683315413ed5412a7dfde`.
- FACT: the range from prior Astra head `c2aa0d5` through evidence head `31846b7`
  adds only audit evidence, manifests, access-preparation documents and handoff
  documents. It changes no runtime, collector, scientific threshold or RAW file.
- FACT: the primary manifest declares 18 artifacts. All 18 local file sizes and
  SHA-256 values match. Manifest byte SHA-256 is
  `cde27656a37557a72fd7ef168030b5405bcf5a8e7b8347bb56f07f99860e110f`.
- FACT: guest-side `sha256sum` values embedded in the raw PTY transcript match the
  reconstructed `result.json`, `collector_metrics.json`, runtime seal and other
  small guest artifacts.
- FACT: the guest runtime seal is byte-identical to the tracked seal, SHA-256
  `cb3dee0331cebed2ede5b43a0092fad0b2aad0989be63f7666d3e6547a66c11c`.
  The tracked launch provenance binds the same seal, epoch, run ID, runtime commit,
  fingerprint and 259200-second duration.
- TOOLING LIMITATION: topology files collected at 15:43–15:44 UTC are later than
  the raw PTY transcript's 15:29 capture, so their exact commands are not all
  independently replayable from that transcript. Their command text and output are
  preserved and their committed bytes match the manifest.
- EVIDENCE AMBIGUITY: an empty root-owned `local-archive-fixture` directory has an
  mtime of 2026-09-08 15:23 UTC, during the evidence session. It is outside RAW and
  does not alter the run verdict, but it contradicts the handoff's broad claim that
  every guest filesystem object was owned by `bitcoin-trader` and leaves the
  session's “zero guest mutation” claim unproven. This report makes no such claim
  for the earlier Gemini session.

## 3. Frozen identity

| Field | Verified value |
|---|---|
| Epoch | `aws-72h-soak-20260905-8017b83e` |
| Run ID | `aws-72h-soak-run-20260905T024039Z-8017b83e` |
| Runtime commit | `9532cebc902856d954bf80b51dbe567b543dc8e2` |
| Runtime fingerprint | `a023fb5723830c38a7f7d47f2439334fcb44d2c6559939dba7a7cb1c2f88783b` |
| Duration | 259200 seconds |
| Feed universe | 76 partitions per complete hour |
| Compression | zstd level 1 |
| Cleanup | disabled |
| Archive/fullscan concurrency | 1 / 1 |
| Launch | bounded transient systemd; Restart=no |

The relevant `bounded_supervisor.py` and `archive_scheduler.py` files are
byte-identical between runtime commit `9532ceb` and Phase 6.3 main. Their SHA-256
values are respectively `e93b25de5ed51ac8995566a5dd12da4063253961e220b6f4d046ace5e1181dc2`
and `0ac9578fe7d9a9e9ab8a37895c42b8908aa87e9f33007991a30220a95c91db4a`.

## 4. Authoritative timing

### Actual start

VERDICT: `2026-09-05T05:40:03.207328+00:00`, evidence type
`PROCESS_EXEC_START`.

The source is `collector_metrics.json:collector_started_at`, bound to collector PID
229076, the exact run ID, epoch, runtime commit and runtime fingerprint. Source file
SHA-256 is `5ba0c637e00608a514eec37d1f71b0cd186ecf3c35b5750c0fef34c2636dce9b`.
The derived official evidence artifact validates through
`compose_epoch_contract.validate_actual_start`; its byte SHA-256 is
`a7c99aa34881a9808f57cd4bd3c0092d7c7b0cb058edbd50ec49d59c428342af`.

Cross-checks:

- systemd journal start: 2026-09-05 05:40:02 UTC, second precision;
- supervisor `started_at`: `2026-09-05T05:40:03.058660+00:00`, 0.148668 seconds
  before collector start;
- first captured Bithumb orderbook local receive/write:
  `05:40:03.461272` / `05:40:03.461871`, about 0.254/0.255 seconds after collector
  start;
- first captured trade `exchange_ts` is earlier than process start and is therefore
  not used as actual start.

### Completion

VERDICT: supervisor terminal completion is
`2026-09-08T05:41:33.277630+00:00`, sourced from `result.json`, SHA-256
`33d4fd460e069928464e2cbc0dbeb8e3853db40f65214da0440e972a2cff18cf`.
The systemd journal independently records status 143 and failed exit-code at
05:41:33 UTC.

The collector's data-duration endpoint is distinct: final metrics were written at
`2026-09-08T05:40:03.236533+00:00`. From authoritative collector start this is
259200.029205 seconds. The result records supervisor monotonic elapsed time as
259290.218599 seconds. Final sampled RAW write is not treated as process completion.

## 5. Supervisor decision

The frozen supervisor requires all of the following for PASS: collector exit 0,
no received signal, no forced timeout, full duration, valid final metrics, observed
final manifest flush, no publisher failure, required publisher started, no archive
scheduler failure, and required scheduler started.

| Gate | Actual | Result |
|---|---:|---|
| Collector exit | -15 (SIGTERM) | FAIL |
| Received signal | SIGTERM | FAIL |
| Forced timeout | true | FAIL |
| Full duration | true | PASS |
| Final metrics valid | true | PASS |
| Final manifest flush observed | false | FAIL |
| Publisher exit | 0 | PASS |
| Archive scheduler exit | -9 (SIGKILL) | FAIL |
| Overall status | INTERRUPTED | FAIL |
| systemd terminal result | status 143 / failed exit-code | FAIL |

FACT: the collector did not exit naturally with code 0. At the supervisor deadline,
the collector remained alive through the first 45-second grace. The supervisor set
`forced_timeout=true` and called its own signal-forwarding path with SIGTERM. That
explains `received_signal=SIGTERM` and collector exit -15; the evidence does not
require an external manual stop to explain them. The archive scheduler then failed
to exit within its 45-second grace and was SIGKILLed, producing -9. Total overhead
is therefore approximately two 45-second grace windows.

DERIVED: the collector and supervisor were both configured for 259200 seconds. The
collector timer starts after process initialization, and final queue drain/manifest
generation occurs after that timer. The supervisor's equal deadline and finite
grace can expire while the collector is still finalizing. This is a
**RUNTIME ORCHESTRATION DEFECT**. It explains the observed termination path, but it
does not create a post-hoc PASS exception.

## 6. RAW and archive cohort oracle

Using the authoritative collector start and collector data-duration endpoint, the
frozen functions derive:

| Cohort class | Expected / observed |
|---|---|
| Touched RAW hours | expected 73, observed 73; `20260905-05` through `20260908-05` |
| Fully complete RAW hours | 71; `20260905-06` through `20260908-04` |
| Archive-eligible hours after 600s grace | expected 72; `20260905-05` through `20260908-04` |
| Actual compressed/receipt cohorts | 3: `20260905-05`, `20260906-05`, `20260907-05` |
| Missing archive cohorts | **69** |
| Expected partition receipts | 72 × 76 = 5472 |
| Actual receipt files | 228 = 3 × 76 |
| Missing expected partition receipts | **5244** |

The 5530 non-empty RAW files are real evidence of retained volume. They do not prove
feed completeness: per-cohort counts range from 72 to 77 rather than uniformly 76,
and the captured topology does not preserve the per-feed/per-cohort matrix needed
to classify every deficit or duplicate.

The frozen auditor requires an archive receipt for every expected cohort and emits
`ARCHIVE_RECEIPT_MISSING` for absent cohorts. Sixty-nine expected cohorts lack
receipts, so this hard gate fails even though RAW remains locally available.

Only `full_scan_05_report.json` is present. Its nested integrity totals say PASS for
360356 records, 152 files and 76 compressed files. Its identity is hour-only, so it
cannot bind separate date+hour cohorts. The cohort oracle expects 72 hourly
full-scan cohorts and a terminal fullscan requirement. Evidence for the other 71
cohorts is absent. The current auditor only checks that at least one full-scan report
exists before suppressing `FULLSCAN_EVIDENCE_MISSING`; this is a tooling limitation,
not evidence of full cohort coverage.

Restore verification cannot be independently established for all expected cohorts.
The summary reports 228 receipts in `CLEANUP_ELIGIBLE`, but individual receipt bytes
were not captured and 5244 expected receipts do not exist.

## 7. Archive scheduler root cause

CONFIRMED DEFECT, high confidence:

- `discover_eligible_hours` groups by `(date_str, hour_str)`;
- `is_hour_completed` accepts only `hour_str` and searches every date for the same
  `_HH.jsonl` suffix;
- `has_hour_failed` and full-scan report names are also keyed only by HH;
- `run_once` passes only `target.hour_str` to the orchestrator;
- the orchestrator filters all dates by `_HH.jsonl` and launches a single
  `full_scan_HH_report.json`.

This collapses different UTC dates into one mutable completion identity. The real
evidence—three archived cohorts, all hour 05, plus one hour-only full-scan report—is
consistent with this defect. The available evidence does not include a complete
archive scheduler operation log, so the date-key bug is proven while attribution
of every skipped 06–04 cohort to that single mechanism remains medium confidence.

## 8. Official process verdict

**72H PROCESS: FAIL — CASE B.**

Hard blockers:

- `SUPERVISOR_NOT_PASS`: actual status `INTERRUPTED`;
- `FORCED_TIMEOUT`: true;
- `COLLECTOR_EXIT_NONZERO`: -15;
- `FINAL_MANIFEST_FLUSH_NOT_OBSERVED`: false;
- `ARCHIVE_SCHEDULER_EXIT_NONZERO`: -9;
- `ARCHIVE_RECEIPT_MISSING`: 69 expected cohorts / 5244 expected partition receipts;
- `FULLSCAN_COHORT_COVERAGE_INCOMPLETE`: 1 hour-only report cannot bind 72 date-hour cohorts;
- `RESTORE_EVIDENCE_INCOMPLETE`: expected cohort coverage is absent.

Positive observations do not erase these blockers: full duration is true, final
metrics are valid, writer errors/queue drops/unpersisted events are zero, publisher
exit is zero, disk use is 37%, and the journal shows one service start.

## 9. Data-quality boundary and salvage decision

Official deep DQ and qualification are **NOT RUN**. The Phase 6.3 official flow
requires a sealed/root-bound input chain, and Case B stops official qualification.
No contract, epoch root, canonical root, prospective dataset or holdout was created.

NON-QUALIFYING DIAGNOSTIC DQ is also **NOT RUN** because the evidence branch contains
topology summaries and small artifacts, not the 55+ GB RAW corpus required by the
existing auditor. No lenient result was manufactured. Limited diagnostic facts are:
5530 non-empty RAW files, 73 touched hours, final counters of zero for writer errors,
queue drops, unpersisted events, trade sequence gaps, trade duplicates and malformed
quarantine, and five total reconnects across exchanges. These are useful signals,
not a DQ PASS.

| Proposed use | Decision | Boundary |
|---|---|---|
| Official prospective alpha qualification | **NO** | Case B and missing sealed archive/fullscan evidence |
| Untouched official holdout | **NO** | Dataset is not qualified; no holdout may be opened or created as official |
| Exploratory/development research | **CONDITIONAL YES** | Label non-qualifying; do not cite as prospective validation or profitability evidence |
| Simulator/feature implementation tests | **YES** | Engineering fixtures and behavior tests only; document incomplete cohort/archive guarantees |
| Diagnostic DQ/RCA | **YES, future** | Run on immutable RAW copy with existing diagnostic tooling; cannot promote eligibility |

## 10. Minimum remediation before another official soak

1. Carry a canonical `(date, hour)` cohort ID through scheduler eligibility,
   completion, failure state, orchestrator filtering, receipt lookup, backlog metrics,
   lock metadata and full-scan report naming.
2. Add a three-day regression reproducing same-HH collisions and assert all expected
   date-hour cohorts are archived exactly once.
3. Separate collector data duration from supervisor deadline. Give the collector an
   explicit finalization envelope, or let the supervisor wait for a separately
   evidenced natural completion before escalating. Preserve a hard outer systemd
   ceiling.
4. Add a lifecycle test in which production-duration semantics end with collector
   exit 0, no received signal, `forced_timeout=false`, final manifest observed and
   exact frozen PASS status.
5. Make archive scheduler shutdown interruptible and test SIGTERM completion within
   grace so -9 cannot be accepted silently.
6. Bind fullscan evidence to date-hour cohorts and make the official auditor verify
   every expected cohort, not merely the presence of one report.

**NEW OFFICIAL SOAK REQUIRED: YES.** The existing run cannot be repaired into an
official unattended PASS. The retained RAW may support remediation and diagnostic
engineering, but it cannot retroactively satisfy the failed lifecycle and archive
gates.

## 11. Safety

This final-decision audit performed no AWS mutation, StartSession, SendCommand,
collector/service action, RAW change, archive repair, S3 write/delete, IAM/Terraform
change, private API access, holdout inspection, strategy research, paper/live
trading, dashboard merge or main merge. No new soak was started.

## 12. Verification

- `PYTHONPATH=src python3 -m pytest`: 942 passed, 2 skipped in 62.19 seconds.
- `compose_epoch_contract.validate_actual_start`: derived actual-start evidence accepted.
- deterministic scheduler reproduction: a completed `2026-09-05_05` changes from
  complete to incomplete when an unreceipted `2026-09-06_05` file appears;
  eligibility then returns both date cohorts under the same hour-only state.
- `git diff --check`: clean for this decision branch's changes.
