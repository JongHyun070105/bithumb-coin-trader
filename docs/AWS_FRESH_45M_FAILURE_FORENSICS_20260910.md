# AWS fresh 45-minute validation failure forensics (2026-09-10)

## Immutable verdict

This report freezes the terminal verdict of the single fresh validation run. It does not repair, rewrite, or requalify any runtime artifact.

| Gate | Verdict |
|---|---|
| PROCESS | PASS |
| ARCHIVE | FAIL |
| EVIDENCE CONTRACT | FAIL |
| OVERALL | FAIL |
| READY FOR 30H REVIEW | NO |

The failed epoch and its local and S3 prefixes are permanently disqualified from research qualification.

## Run identity

- Epoch: `aws-validation-45m-20260909-6c9d1758`
- Run ID: `aws-validation-45m-run-20260909T080939Z-6c9d1758`
- Runtime commit: `6c9d1758381c0efbc77cdcf44679d0a8f4b2deea`
- Runtime fingerprint: `252cb6df7b20e673d72b8414e74a0c38ba36669f21e53eb22e3fa779099c8ce6`
- Actual start: `2026-09-09T13:40:03.259151Z`
- Terminal timestamp: `2026-09-09T14:25:44.727077Z`
- systemd invocation ID: `99cd1b9ed6594e3bae9fce3fb43a0f2d`

Committed launch artifacts:

| Artifact | SHA-256 |
|---|---|
| `infra/aws/seals/aws-validation-45m-20260909-6c9d1758.runtime.json` | `c06404b3ef0be1b65c153dc068c253629c6e2b7881e875673edb5b4e37a1e028` |
| `infra/aws/seals/aws-validation-45m-20260909-6c9d1758.launch-provenance.json` | `288725119d6363d06e5320c977424e3c00d4546256f280c46f4fb37f3b3c5786` |
| `infra/aws/seals/aws-validation-45m-20260909-6c9d1758.launch-command.json` | `62e0da5774671613be4f145cb4547913f61ccaad06f020a16cf66144c3aabc4f` |

## Process evidence

The lifecycle reached `COLLECTING -> FINALIZING -> COMPLETE` naturally. Supervisor status was PASS; collector, metric publisher, and archive scheduler exited 0; `forced_timeout=false`; `external_signal=null`; final manifest flush and final metrics validation succeeded; systemd reported success. Writer errors, queue drops, unpersisted events, zero-byte RAW files, and unexpected restarts were all zero.

Actual manifest timestamps touched two cohorts:

- `2026-09-09_13`: 76 RAW partitions
- `2026-09-09_14`: 76 RAW partitions

Only `2026-09-09_13` was archive-eligible: its close plus the 600-second grace was `2026-09-09T14:10:00Z`, before collection completion. The 14 UTC cohort was not eligible.

## Archive and evidence evidence

For eligible cohort `2026-09-09_13`:

- RAW: 76
- COMPRESSED: 76
- physical receipts: 76
- receipt states: `FAILED=76`
- qualifying terminal receipts: 0
- restore verified: 0
- fullscan reports: 0

The strict auditor returned FAIL. Across both touched cohorts it verified 152 of 152 RAW manifests and found no hash mismatches, duplicate or unexpected feeds, receipt identity failures, wrong-cohort inputs, legacy hour-only artifacts, or zero-byte RAW/compressed artifacts. It recorded 155 blockers: one `NO_EPOCH_MANIFEST`, 76 `RECEIPT_FAILED`, 76 `RECEIPT_INVALID_STATE`, one missing qualifying receipt-cohort coverage blocker, and one missing fullscan-cohort coverage blocker.

## Autonomous archive failure

Receipt failure distribution:

- 75 receipts stopped at last-successful stage `COMPRESSED_VERIFIED` with `ClientError: An error occurred (403) when calling the HeadObject operation: Forbidden`.
- 1 receipt stopped after last-successful stage `ARCHIVED` with `ValueError: remote object size mismatch`.

The actual instance role, `bitcoin-trader-aws-apne2-research-collector`, had matching inline-policy and permissions-boundary scope for the validation prefix. Effective `s3:GetObject` and `s3:PutObject` were allowed; `s3:ListBucket` was denied.

For the 75 uncontaminated missing keys, `ArchiveManager._upload_or_reuse()` called `S3ArchiveStore.exists()` first. `exists()` issued `HeadObject` and interpreted only 404/`NoSuchKey`/`NotFound` as absence. S3 returned 403 for an absent key to this no-ListBucket principal, so the conditional `PutObject` path was never reached.

`failure_stage` is the last state successfully written to the receipt, not the name of the failing AWS operation.

## Manual contamination

An operator diagnostic issued `PutObject(Body=b"test")` against the active validation prefix. The write succeeded. This was an unattended-validation protocol violation and irreversibly contaminated the run.

The resulting current S3 object was observed read-only as:

- key suffix: `2026-09-09/binance/orderbook/binance_orderbook_btcusdt_2026-09-09_13.jsonl.zst`
- content length: 4 bytes
- ETag: `098f6bcd4621d373cade4e832627b4f6`, exactly `MD5(b"test")`
- last modified: `2026-09-09T14:12:30Z`
- version ID: `qmR45PBYZJJOB4aVocG1ATOFj4RQsgwX`
- server-side encryption: `AES256`

The corresponding local compressed artifact was 1,005,239 bytes with SHA-256 `6d5b751fd09d1709a00890ae8ee73caf4620f44beb9c851c8dfcfa5b49c498c6`. The autonomous archive therefore reused the already-present 4-byte object and failed closed on remote size verification.

The contaminated object must not be deleted, repaired, or overwritten. The failed receipts must not be edited, and archive/fullscan must not be rerun to manufacture qualification.

## Root-cause separation

PRIMARY AUTONOMOUS FAILURE: the redundant pre-upload existence probe could not distinguish an absent key under the intended object-only least-privilege policy, because S3 returned 403 without `ListBucket`.

OPERATOR PROTOCOL DEVIATION: YES.

MANUAL EVIDENCE CONTAMINATION: YES.

The contamination explains the first chronological partition's size mismatch. It does not explain the independent 75-partition pre-upload `HeadObject` failure.

## Permanent status

- Fresh 45m process: PASS
- Fresh 45m archive: FAIL
- Fresh 45m evidence contract: FAIL -- manual contamination and archive failure
- Fresh 45m overall: FAIL
- Fresh 45m started again: NO
- 30H started: NO

Science status remains: ALPHA UNPROVEN; PAPER NOT STARTED; LIVE DISABLED; PRIVATE API DISABLED.
