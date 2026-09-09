# Post-72H Runtime Remediation V1 Design

**Date:** 2026-09-09  
**Base:** `26afad3845b11ef0404ccfacfed98e4695315b8d`  
**Branch:** `codex/post72h-runtime-remediation-v1-20260909`

## Purpose and frozen boundary

This release fixes deterministic archive identity, shutdown, lifecycle, and audit defects found by the official 72-hour decision. The historical verdict remains immutable: **72H PROCESS FAIL — Case B**, alpha unproven, paper not started, live disabled. Existing receipts, full-scan artifacts, runtime seals, and the final decision report are read-only inputs. This sprint performs code changes and offline tests only. It makes no AWS call, starts no collector, and does not merge to `main`.

The next environment stage is a separately authorized **45-minute unattended AWS smoke**. Completion of this sprint may report only whether the branch is `READY TO START AWS 45M VALIDATION`.

## Canonical archive identity

`ArchiveCohortId` is the sole official identity for a closed UTC hour:

```text
date: YYYY-MM-DD
hour: HH
key:  YYYY-MM-DD_HH
```

The value type validates a real ISO calendar date and an hour from `00` through `23`, sorts chronologically, and renders only the canonical key. It parses canonical keys and partition filenames centrally. Scheduler, orchestrator, full-scan, backlog, receipt lookup, and audit coverage exchange this type or its canonical key. Public APIs use `cohort` names so an hour-only argument cannot appear valid.

Existing hour-only artifacts remain readable only through an explicit legacy diagnostic classifier. They are labelled `LEGACY / NON-QUALIFYING` and never satisfy an official completion or audit gate.

## Archive data flow

The scheduler groups partitions by `ArchiveCohortId`, excludes the active cohort, applies the closure grace, and sorts by the identity. Completion requires every exact-cohort partition receipt plus the exact cohort full-scan report when scans are enabled. Failed and pending state contains canonical keys.

The orchestrator accepts one exact cohort and filters partitions using parsed date and hour fields. It cannot select `*_05.jsonl` across dates. It emits cohort-bound full-scan files such as `full_scan_2026-09-05_05_report.json`; the report body, runner metadata, scan result map, and backlog use the same key. Receipt paths remain partition-derived and therefore date-bound, while cohort extraction and verification are centralized.

A single archive worker processes the oldest eligible cohort. A release-blocking synthetic regression covers three UTC dates with the same recurring hour and adjacent hours. It proves every cohort is processed once, completed cohorts stay completed, and receipts and full scans do not collide.

## Auditor hard gates

The auditor derives expected archive cohorts from the frozen run boundary. For every expected cohort it requires:

1. all expected partition receipts with matching epoch, run ID, partition identity, and restore verification;
2. exactly applicable canonical full-scan evidence with matching internal cohort identity;
3. the existing lifecycle and final-manifest evidence.

One missing or mismatched cohort yields a hard blocker. Missing receipt semantics retain `ARCHIVE_RECEIPT_MISSING`. Missing or substituted scan evidence yields `FULLSCAN_COHORT_COVERAGE_INCOMPLETE`. A legacy hour-only scan is diagnostic evidence and cannot satisfy coverage. Evidence from another date with the same hour, epoch, run ID, or runtime fingerprint is rejected.

## Collector and supervisor lifecycle

The collector owns `collection_duration_seconds`. On expiry it stops accepting new events, drains queued work, flushes and closes writers, writes final metrics and manifests, and exits naturally. Its observable phases are `COLLECTING`, `FINALIZING`, and `COMPLETE`.

The supervisor owns the outer boundary:

```text
hard_ceiling_seconds = collection_duration_seconds + finalization_timeout_seconds
```

The collection deadline does not send a signal. The supervisor waits for natural collector completion during the finalization envelope. A nonzero early exit fails immediately. If finalization exceeds the outer ceiling, the supervisor marks `forced_timeout=true`, sends TERM, escalates if required, and cannot report PASS. Production service runtime limits must remain greater than the supervisor ceiling; this sprint changes no service in AWS.

## Cooperative scheduler shutdown

SIGINT or SIGTERM sets the scheduler stop event. Poll waits wake immediately, and the loop checks the event before discovering or starting a new cohort. An archive operation already inside the transactional pipeline is allowed to finish its safe boundary; it does not receive a fabricated completion state. After that boundary the scheduler exits cleanly. The supervisor grants its normal shutdown grace before escalation.

## Offline validation

Every behavior follows strict TDD: add a focused test, run it and record the intended RED, implement the smallest change, and rerun GREEN. Required release gates are:

- canonical identity and two-day collision regression;
- three-day same-hour scheduler/orchestrator regression;
- canonical full-scan and per-cohort auditor coverage;
- receipt and restore same-hour wrong-date rejection;
- normal, hung-finalization, and early-failure supervisor tests;
- idle/wait/no-new-work scheduler shutdown tests;
- combined lifecycle integration;
- 73 touched / 72 eligible / 76 feeds synthetic archive oracle;
- adversarial audit mutations;
- full `pytest` and lifecycle integration commands.

No test may edit historical evidence. Fixtures live in temporary directories and use local file archive stores only.

## Delivery

Changes are committed in reviewable identity, scheduler/orchestrator, auditor, lifecycle, and documentation groups. Only the remediation branch is pushed. The final report records RED/GREEN evidence, full-suite results, safety boundaries, historical verdict preservation, and readiness for the separate AWS 45-minute validation.
