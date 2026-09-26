# Fresh 30H-v2 terminal audit — NO GO

Audit captured 2026-09-21 01:01–01:19 KST, after the natural end at **2026-09-21 00:50:25 KST**. The sealed epoch is `aws-validation-observability-30h-20260919-20260919T095000Z-v2`; run ID is `aws-validation-observability-30h-run-20260919T095000Z-v2`.

**FRESH_30H_V2_TECHNICAL_GATE = FAIL.** The collector supervisor completed its 108,000-second interval successfully, but the authoritative qualifying cohort `2026-09-19_12` finalized as `FAIL` with 60 failed Bithumb feeds. Only 2 of 29 qualifying cohorts have `PASS` receipts. This run is **not eligible for authoritative research, alpha promotion, paper trading, or live trading**.

## Evidence classes and provenance

- **COMMITTED_GIT_EVIDENCE:** [sealed identity](identity.json) fixes commit `4fcdd819366918fa86e5597ed7d2271454d926c7`, tree `68dab6731f52e1aca51237410ea9f7b469869086`, 29 expected qualifying hours, 76 feeds per hour, and the temporary S3 prefix. The launch manifest is launch provenance, not terminal evidence.
- **EXTERNAL_EC2_S3_EVIDENCE:** Exact copied EC2 terminal files, four naturally generated finalized receipts, two full-scan reports, supervisor log, health snapshots, the naturally generated S3 failure object, and a time-specific S3 inventory are under [terminal](terminal/). Twelve static EC2 source files matched their copied SHA-256 at capture. The copied files' byte hashes are in [evidence-sha256-index.json](terminal/evidence-sha256-index.json); [receipt-observation-index.json](terminal/receipt-observation-index.json) enumerates all 29 hours. Health snapshots and S3 inventory describe their capture time and may change at source.
- **NARRATIVE/INFERENCE:** The interpretation below combines those artifacts with read-only systemd, Git, S3, and SSM observations. A missing receipt is not a passing receipt; a likely causal mechanism is not a proven root cause.

## Runtime and collection

The EC2 runtime worktree was clean at the sealed commit and tree during the terminal read. Read-only `systemctl show` returned `inactive/dead`, `Result=success`, `NRestarts=0`, `ExecMainCode=0`, and `ExecMainStatus=0` for the transient supervisor unit. [result.json](terminal/result.json) records 108,000 seconds configured, 108,023.997 seconds elapsed, `full_duration_satisfied=true`, `forced_timeout=false`, no received signal, and collector/scheduler/publisher exit codes of 0. It also records `overall_status=PASS`; that field covers the bounded supervisor lifecycle and **does not override cohort failure**. The collector lifecycle is `COMPLETE`, final manifest flush was observed, and final metrics are valid. Final queue, unpersisted events, and writer errors were all zero. A separate kernel OOM history was not independently audited, so OOM is **NOT_VERIFIABLE** beyond the successful systemd exit.

The local [terminal witness](terminal/terminal-receipt.json) says `CLEAN_SUCCESS`, service result `success`, exit status 0. Its `epoch` is the generic `bitcoin-trader-30h`, rather than the sealed epoch, and `s3_uploaded=false`/`s3_key=null`; the run ID and embedded health identify the run. Treat witness identity binding and S3 terminal-witness presence as gaps, not as a clean all-gates pass.

## Cohort gate and failure forensics

The qualifying window is 2026-09-19 19:00 KST through 2026-09-21 00:00 KST (UTC cohorts `2026-09-19_10` through `2026-09-20_14`). The 18:00–19:00 KST warmup hour `09` was naturally finalized `SKIPPED_NON_QUALIFYING`; the ending partial hour `2026-09-20_15` is excluded by contract.

| UTC cohort | KST hour | Natural finalized receipt | Slot result |
| --- | --- | --- | --- |
| `2026-09-19_10` | 2026-09-19 19:00–20:00 | PASS | 76/76 present; full scan PASS |
| `2026-09-19_11` | 2026-09-19 20:00–21:00 | PASS | 76/76 present; full scan PASS |
| `2026-09-19_12` | 2026-09-19 21:00–22:00 | **FAIL** | 16 present, 60 failed Bithumb slots |
| `2026-09-19_13`–`2026-09-20_14` | Remaining 26 hours | **MISSING** | No finalized receipt; do not infer a slot PASS |

Thus **qualifying cohorts PASS = 2/29** and **independently completed qualifying slots PASS = 152/2,204**. The failure receipt was finalized at 2026-09-19 22:10:17 KST. The matching [S3 failure evidence](terminal/archive-failures/2026-09-19_12/failure_2026-09-19_12.json) reports `COLLECTION_GAP` and `HEARTBEAT_GAP_EXCEEDED` for all 20 Bithumb markets across orderbook, ticker, and trade. The frozen `12` journal remains on EC2 (read-only observed SHA-256 `482a50a6d9939a12a095bf12a237b8a162e23844414f85e72e12f912a62f9c02`); representative BTC orderbook observations show data on both sides of the hour but a 12:24:56–12:25:06 UTC disconnected interval. Supervisor logs show a heartbeat ping timeout at 12:24:48 UTC, a 30-second stale-stream reconnect at 12:24:56 UTC, and reconnection at 12:25:06 UTC. This aligns with the receipt's failure reasons but does not establish the ultimate network or exchange cause. Thresholds and the FAIL receipt were not changed.

The scheduler's old logic treated a finalized FAIL as incomplete, always selected the oldest incomplete hour, and therefore kept reselecting `12` while later hours accumulated. The final observer health recorded backlog 26, `archive_errors=1`, and `upload_failures=60`; this matches the 26 missing receipts. The local scheduler fix skips only a matching, valid, finalized `FAIL` during discovery, leaving its failed status visible and permitting later hours to be selected. It was **not applied to the historical EC2 runtime**, and no manual finalizer was used.

## S3, immutability, and resource limits

The read-only [S3 inventory](terminal/s3-inventory.json) at 2026-09-21 01:11 KST contains 2,212 objects (70,888,194 bytes) under the sealed temporary prefix: 168 raw compressed files (`10`: 76, `11`: 76, `12`: 16), 228 coverage objects (76 each for `10`, `11`, `12`), one archive failure object, one failed cohort receipt, and 1,814 observability objects. The two passing hours' full scans each checked 152 files with zero scan failures. No S3 presence or canonical binding can be asserted for the 26 missing finalized hours. Direct provisioner `HeadObject`/`GetObject` returned 403; the permitted EC2 instance role enabled read-only object retrieval. No promotion or write was performed.

No genuine time-separated T1 SHA-256 observation for any of the 29 qualifying finalized receipts was found. The [index](terminal/receipt-observation-index.json) records retrospective T2 byte hashes for `10`, `11`, and `12`, and marks **immutability PASS = 0/29; NOT_VERIFIABLE = 29/29**. The failure object's internal `receipt_checksum` is not substituted for a prior byte-level SHA-256 observation.

Seven exact S3 minute snapshots in [resources](terminal/resources/) span the start, roughly 6-hour intervals, and the end. Collector-reported RSS rose from 105.2 MB at ~6h to 189.4 MB at the end, while FD count stayed at 10 in those sampled points; disk used rose from 144.4 GB to 168.3 GB, and archive backlog from 2 to 26. The metric derives from `ru_maxrss` (peak usage), so these samples neither prove a current-RSS leak nor prove memory stability. Separate observer, supervisor, and scheduler RSS time series were not available in the preserved sample set. Final writer queue and unpersisted count were zero. No broad resource-stability PASS is claimed.

## Governance and next action

This terminal audit used read-only Git, AWS identity/list/read APIs, and SSM `StartSession` to read EC2 state as `bitcoin-trader`; it did not use `SendCommand`, create a new AWS launch, mutate IAM/Terraform/S3/runtime, run a manual finalizer, regenerate a receipt, access private exchange APIs, or change thresholds. CloudTrail `lookup-events` was denied, so an **account-wide historical absence of prohibited actions is NOT_VERIFIABLE**. The observer service was still running when checked after collector termination; no reviewed cleanup contract was identified and it was not stopped.

The global launch counter remains **10/10 used, 0 remaining**. Preserve this failed epoch without repair or reinterpretation. The next engineering step is to review the local scheduler fix and the Bithumb heartbeat/stale-stream failure path, then require a separately governed validation plan before any future launch. Research readiness is FAIL for this fresh run; `ALPHA=UNPROVEN`, `PAPER=NOT_STARTED`, `LIVE=DISABLED`, `PRIVATE_API=DISABLED`, and no candidate or prospective holdout was launched.
