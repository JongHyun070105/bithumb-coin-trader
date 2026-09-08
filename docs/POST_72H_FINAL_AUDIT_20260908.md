# Post-72H Final Audit

## 1. Executive verdict

**AUDIT BLOCKED — AWS SESSION EXPIRED.** Read access could not be established;
IAM read-permission sufficiency was not tested. This is an authentication blocker,
not evidence of a failed collector or an AWS permission-policy defect.

| Classification | Item | Result |
|---|---|---|
| VERDICT | Natural completion | UNKNOWN |
| VERDICT | 72H process | UNKNOWN; final verdict remains pending |
| UNKNOWN | Real deep DQ | NOT RUN / NOT QUALIFIED |
| VERDICT | Qualification | NOT RUN |
| VERDICT | Prospective dataset | NOT CREATED by this audit |
| VERDICT | New holdout seal | NOT CREATED by this audit |
| FACT | Holdout opened | NO |
| VERDICT | Alpha | UNPROVEN |
| VERDICT | Paper | NOT STARTED |
| VERDICT | Live / private API | DISABLED |

The audit stops at Phase 2 before guest inspection. No remote completion evidence
was received. Case D is not established; no root composition, real-DQ run,
qualification, canonicalization, dataset construction or research was attempted.

## 2. Git and toolchain identity

- FACT: fresh `git fetch origin --prune` and `git rev-parse origin/main` confirmed
  `0a45a66324e0e49cbe02db4b47d9529353b5f20d`.
- FACT: isolated branch `codex/post-72h-final-audit-20260908` was created from that
  exact revision. Audit tool code remains at that revision; no tooling patch.
- FACT: `git ls-remote` confirmed protected product branch
  `gemini/dashboard-local-api-ledger-e2e-20260908` at
  `22784e5b51884d41a0e38a1a5d7d659d1da93ce0`. It was not changed or merged by this audit.
- FACT: the main checkout had an unrelated untracked `test-results/` directory.
  It was left untouched. Existing worktrees were not reset, cleaned or removed.
- FACT: clean audit-worktree baseline command `PYTHONPATH=src python3 -m pytest`
  completed with **942 passed, 2 skipped, 0 failed**, in 61.73 seconds.
- UNKNOWN: deployed guest code identity. The expected sealed runtime commit is
  `9532cebc902856d954bf80b51dbe567b543dc8e2`; it has not been bound to the guest.

## 3. Epoch and run identity

These are **FACTS about tracked preparation artifacts**, not verified runtime facts:

| Field | Tracked expected value |
|---|---|
| Epoch | `aws-72h-soak-20260905-8017b83e` |
| Run ID | `aws-72h-soak-run-20260905T024039Z-8017b83e` |
| Runtime fingerprint | `a023fb5723830c38a7f7d47f2439334fcb44d2c6559939dba7a7cb1c2f88783b` |
| Duration | 259200 seconds |
| Feed universe | Bithumb 20×3 + Binance 4×2 + Upbit 4×2 = 76 |
| Cleanup / collector autostart | false / false |
| Compression / archive concurrency | zstd level 1 / 1 |
| Fullscan architecture / concurrency | supervised-detached-setsid / 1 |

The copied seal's byte SHA matches the launch provenance's recorded seal SHA.
Guest, S3 and lifecycle binding remain UNKNOWN.

## 4. Authoritative timing

| Item | Result |
|---|---|
| Actual start / source / source SHA | UNKNOWN / NOT COLLECTED / NOT AVAILABLE |
| Completion / source / source SHA | UNKNOWN / NOT COLLECTED / NOT AVAILABLE |
| Observed lifecycle elapsed | UNKNOWN |
| RAW earliest/latest and coverage duration | NOT INSPECTED |
| Actual-start source deltas | NOT AVAILABLE |
| Configured duration | 259200 seconds in tracked preparation artifacts |

No chat timestamp, expected wall-clock end or provenance `created_at_utc` was used
as actual-start or completion evidence. No `actual_start_evidence` was manufactured.

## 5. Natural completion and authentication evidence

The only attempted AWS service operation was read-only EC2 `DescribeInstances`,
using the existing `bitcoin-trader-provisioner` profile, region `ap-northeast-2`,
and the `Project=bitcoin-trader` tag filter. It returned exit code **255**:

> aws: [ERROR]: Your session has expired. Please reauthenticate using 'aws login'.

No alternate privilege identity was tried. No login, credential change, IAM change
or SSM command was performed. No instance description, process state, journal,
runtime result, metrics, RAW or archive evidence was received.

Systemd state, collector/supervisor/payload activity, exit status, final flush,
restart history, manual intervention and last writes are all **UNKNOWN**.
Neither inactivity nor natural completion is inferred from this error.

## 6. Process gates

| Gate | Evidence available | Result |
|---|---|---|
| Natural bounded completion / full duration | No guest lifecycle evidence | UNKNOWN |
| Collector / publisher / supervisor exit codes | No result | UNKNOWN |
| WriterErrors / QueueDrops / Unpersisted | No runtime metrics | UNKNOWN; not zero |
| Final manifest flush / active partitions | Not inspected | UNKNOWN |
| Runtime commit / fingerprint / epoch binding | Preparation artifacts only | UNKNOWN |
| Restart / prestart / foreign epoch contamination | Not inspected | UNKNOWN |
| Feed completeness | No RAW or manifests inspected | UNKNOWN |
| Ownership / disk thresholds / backlog | Not inspected | UNKNOWN |
| Archive / restore / fullscan | Not inspected | UNKNOWN |
| Reconnect storm / manual intervention | No journal evidence | UNKNOWN |

## 7. Cohort oracle

RAW buckets, complete hours, partial hours, expected archive cohorts and actual
partition counts are **NOT DERIVED**, because authoritative start/completion are
missing. The 73/72 orientation in the request is not a pass condition.

The reviewed implementation derives touched RAW hours using `[start,end)` and
archive eligibility using `hour_end + 600 seconds <= end`. Feed count 76 is a
tracked expectation, not a measured coverage result.

## 8. Runtime metrics

WriterErrors, QueueDrops, Unpersisted, disk use and backlog: **UNKNOWN**.
Missing telemetry has not been replaced with zero.

## 9. Archive, restore and fullscan

S3 inventory, object byte hashes, restore receipts, fullscan terminal states and
post-completion writes: **NOT INSPECTED**. No uploads, downloads or remote scans ran.

## 10. Data quality

Feed coverage, source/receive/persist timestamps, monotonicity, duplicate/trade-ID
semantics, reconnects, gaps, replay determinism and archive reproducibility are
**NOT RUN / UNKNOWN** for the real epoch. Passing synthetic/offline tests are only
toolchain evidence and do not qualify this dataset.

## 11. Qualification

**NOT RUN.** No official epoch contract/root or root-bound real deep-DQ result was
produced. No source or count was edited to manufacture eligibility.

## 12. Canonicalization

**NOT RUN.** Case D has not been established. No canonical root was created.

## 13. Prospective dataset

**NOT CREATED by this audit.** Remote existence is not claimed either way.

## 14. Holdout seal

**NOT CREATED by this audit. HOLDOUT OPENED: NO.** No strategy or performance query
was run on any holdout, including the pre-existing candle holdout.

## 15. Tooling issues and immutable preflight inventory

**TOOLING LIMITATION (static inspection, not an executed real audit):**
`scripts/generate_72h_final_audit.py` is a legacy August V9 report with hardcoded
historical timing. `scripts/generate_72h_final_report.py` defaults missing counters
to zero and initializes identity gates to PASS. Neither can independently prove
this epoch's process verdict. They were not run against the real epoch. Any needed
repair must follow the requested preserved-input/reproduction/regression protocol;
no silent patch or gate relaxation was made here.

Phase 6.3 contract composition supports only `SYSTEMD_SERVICE_START`,
`PROCESS_EXEC_START` and `FIRST_RAW_RECORD` for actual start. It computes an
**expected** end from start plus duration; that field does not prove completion.
The supervisor result must separately establish natural completion, no received
signal/forced timeout, full duration, valid final metrics and observed flush.

Only a **local preflight inventory**, not a remote 72H evidence snapshot, was sealed:

[`evidence/post72h-final-audit-20260908/preflight-20260908T110157Z/manifest.json`](../evidence/post72h-final-audit-20260908/preflight-20260908T110157Z/manifest.json)

Manifest byte SHA256:
`302d3ee4fe1159182f081a4992141b3bdeaaa875d3514d9ef2f07e9eee8d20cb`.

| Captured file | Exact byte SHA256 |
|---|---|
| baseline-pytest.txt | `01d56b938093796f8f0caf6788a6ba17eda82f78a35b3ced75d9c13bce73fde2` |
| aws-72h-soak-20260905.runtime.json | `cb3dee0331cebed2ede5b43a0092fad0b2aad0989be63f7666d3e6547a66c11c` |
| aws-72h-soak-20260905.launch-provenance.json | `441e8722b82a26304ec04446b01df26b4d4b67e58fd0e4c90adc1faff3073310` |
| aws-read-failure.txt | `d0ef5b4b79e1b0733f4032e590e6768ea8f535998345a8e448c358ab01ab9e41` |
| preflight-identity.json | `c8a8ea8cd0378df8b5cd4a29bc26eae4f927c51b6824adf0568b37d19bcff6f9` |

The manifest records source, size and capture time. The authentication error is
explicitly a verbatim excerpt preserved from tool output, not a guest artifact.
Capture time is not process-start time. Files were created exclusively and made
read-only; any later retrieval must use a separate directory and manifest.

## 16. Scientific interpretation and safety

A successful soak would validate collection/process/data-quality requirements
only. It would not prove predictive alpha, profitability or authorize paper/live
trading. This blocked preflight establishes no such process or data-quality success.

Audit actions: AWS mutations NONE; guest commands NONE; collector start/restart/
stop/kill NONE; RAW changes/deletions NONE; S3 changes/deletions NONE; IAM/Terraform
changes NONE; private API NONE; main merge NONE. These describe this audit's actions,
not an unverified claim about the remote run's entire history.

## 17. Next step

Reauthenticate the existing local AWS login session (without sharing credentials),
then repeat read-only Phase 2 in a new evidence capture directory. Re-check Git and
the existing profile's read access. If collector/supervisor is active, preserve the
observation and stop qualification without intervening. If inactive, require actual
natural-completion evidence before proceeding. No alpha research, paper trading,
dashboard merge or main merge is authorized by this report.

Deferred non-blocking product note requested by the user:
`ASTRA_REVIEW_CANDIDATE: dashboard_snapshot._derive_equity_curve_metrics should reject
or exclude equityCurve observations newer than snapshot.timestamp before deriving
display metrics.` This was recorded only; the protected product branch was not
reviewed or modified for that issue during this audit.
