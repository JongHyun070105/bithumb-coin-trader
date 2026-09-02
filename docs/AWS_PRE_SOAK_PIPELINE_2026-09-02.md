# V9.1 AWS pre-soak software validation

Date: 2026-09-02

Scope: local implementation and validation only

AWS writes: **0**

AWS guest changes: **0**

AWS collector: **NOT STARTED**

## Status and boundary

This change prepares the prospective AWS epoch. It does not migrate or archive the closed V9 data-quality-failed epoch. It does not deploy to EC2, call S3, publish CloudWatch metrics, install a service, or start a collector.

The local software gate is implemented. Deployment and fixture-based AWS smoke remain a separate approval gate. A 120-minute smoke and the 72-hour infrastructure soak remain later, separate gates. Infrastructure success does not make data alpha-ready.

## Implementation inventory

| Capability | Before | This change | Evidence |
|---|---|---|---|
| Raw append and schema-v4 manifest | Implemented | Preserved | Existing storage tests |
| Writer fail-closed and durable counters | Implemented | Preserved and hardened snapshot durability | Collector regression tests |
| Active partition identity | In-memory only | Persisted as relative paths in the durable snapshot | Collector snapshot test |
| Streaming zstd | Not implemented | Implemented, level 1 default | Archive E2E and Python 3.9 tests |
| Compressed/decompressed integrity | Not implemented | SHA-256, bytes, and record count | Failure-injection tests |
| Archive/restore/cleanup state | Not implemented | Durable versioned receipt and fail-closed state machine | Archive E2E tests |
| S3 adapter | Not implemented | Instance-profile adapter plus memory/file stores | Mocked S3 contract tests |
| `.jsonl.zst` FULL-SCAN | Not implemented | Transparent streaming input | Raw/zstd equivalence tests |
| CloudWatch publisher | Not implemented | Low-frequency batched publisher | Mocked publisher tests |

## Archive state machine

The one-partition flow is:

`DISCOVERED -> RAW_VERIFIED -> COMPRESSED -> COMPRESSED_VERIFIED -> ARCHIVED -> REMOTE_VERIFIED -> RESTORE_VERIFIED -> CLEANUP_ELIGIBLE -> CLEANED`

Any exception records `FAILED` where possible and keeps raw. A process-level interruption leaves the last durable stage and is safe to retry. A per-partition `flock` prevents two local workers from claiming the same receipt. S3 immutable-key creation uses `If-None-Match: *`; a race winner is accepted only after the normal remote size and SHA-256 checks pass.

Closed-partition selection requires all of the following:

- a partition hour older than the current UTC hour plus the configured grace period;
- no match in the collector's persisted active-file list;
- an unchanged inode, size, and nanosecond mtime across the stability interval;
- a regular non-symlink `.jsonl` under the configured raw root.

The schema-v4 raw manifest SHA-256, byte size, and record count are rechecked before compression. The source is hashed again before upload and immediately before deletion, closing the verify/compress/cleanup TOCTOU window.

## Atomicity and fail-closed cleanup

Compression writes a mode-0600 temporary file, fsyncs it, verifies the temporary zstd stream, then atomically renames it to the final `.jsonl.zst` name and fsyncs the directory. Receipts and metric publisher state use temp-file, fsync, atomic-replace, and directory-fsync semantics.

Cleanup defaults to off. Raw deletion requires an explicit `--cleanup-verified` during finalize or `cleanup --verified-only`, plus durable evidence for raw verification, compressed verification, remote verification, restore verification, and cleanup eligibility. Disk pressure never deletes unverified raw. At or above the configurable critical threshold (default 90%), new archive work fails closed and keeps raw.

## Zstd and FULL-SCAN

The compression candidate is zstd level 1. Compression, decompression, hashing, S3 upload, restore, and JSONL scanning are streaming operations. No full decompressed duplicate is created. Logical lines are bounded to 16 MiB to prevent unbounded buffering.

`scripts/audit_raw_integrity_offline.py` now discovers raw `.jsonl` and compressed `.jsonl.zst`. Corrupt, truncated, concatenated, or trailing-data zstd input is a scan failure, never a pass. The equivalence tests require matching logical SHA, bytes, record totals, valid/invalid JSON, schema, missing-field, non-finite, timestamp, and unknown-market counters.

Operator commands are provided by `scripts/manage_pre_soak_archive.py`:

- `scan`
- `finalize --raw ...`
- `verify --raw ...`
- `restore --raw ...` (with `verify-restore` retained as an alias)
- `cleanup --raw ... --verified-only`

The default store is local. The S3 adapter cannot be selected without both `--store s3` and `--allow-aws-write`. No such command was run in this phase.

## S3 integrity and IAM contract

The adapter uses AWS SDK checksum support with full-object SHA-256. Hex and base64 representations are converted explicitly. Multipart ETag is never treated as SHA-256. A successful upload response is insufficient: `HeadObject` with checksum mode must return the expected content length and SHA-256, then a streamed `GetObject` restore must reproduce both compressed and raw hashes and the raw byte/record totals. AWS documents both [conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html) and [additional object-integrity checksums](https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity.html); local service-model checks confirmed `IfNoneMatch`, `ChecksumSHA256`, and `ChecksumMode` in both tested SDK environments.

Required production actions:

- S3: `ListBucket`, `GetObject`, `PutObject`, `AbortMultipartUpload`, `ListMultipartUploadParts`.
- CloudWatch: `PutMetricData` constrained to `BitcoinTrader/Collector`.

These actions already exist in the collector role and its permissions boundary. `HeadObject` is authorized by `GetObject`. The current single `PutObject` implementation deliberately fails closed above 5 GiB; it does not require multipart creation or a broader IAM policy.

**NEW IAM ACTION REQUIRED: NONE.** Static AWS credentials are neither accepted nor stored; boto3 uses the instance-profile resolution chain.

## CloudWatch metric semantics

The publisher matches the Terraform alarms exactly:

- namespace: `BitcoinTrader/Collector`;
- dimension: `EnvironmentId`;
- metrics: `WriterErrors`, `QueueDrops`, `DiskUsedPercent`.

`WriterErrors` is the interval delta of exchange writer errors plus unpersisted-event failures. `QueueDrops` is the interval delta of actual dropped events and does not treat backpressure as a drop. `DiskUsedPercent` is measured from the filesystem containing collector storage.

The first valid snapshot establishes the interval baseline and publishes only disk usage; it does not invent zero counter values. Missing, stale, malformed, wrong-process, or collector-off snapshots produce no CloudWatch request, preserving the alarms' `treat_missing_data = breaching` behavior. A valid subsequent interval publishes zero or positive deltas in one batch. Retries are bounded to three and publisher/log failures are isolated from the collector hot path.

CloudWatch alarm infrastructure: **IMPLEMENTED**.

Metric publisher software: **IMPLEMENTED AND LOCALLY TESTED**.

Actual AWS metric publication: **NOT RUN**.

## Python 3.9 and dependency boundary

The whole research package retains `requires-python >=3.11`; lowering it would make an unsupported claim about unrelated research modules. The AWS pre-soak runtime has a separate Python-3.9-compatible dependency lock range in `infra/aws/requirements-pre-soak.txt`.

Validation used Python 3.9.6 with boto3 1.42.97, botocore 1.42.97, websockets 15.0.1, and zstandard 0.25.0. The new pre-soak modules compiled and their dedicated tests passed. Collector construction and durable snapshot creation were also exercised inside a running Python 3.9 asyncio loop. This is not a claim that every offline research module supports Python 3.9.

Current [Boto3 installation documentation](https://docs.aws.amazon.com/boto3/latest/guide/quickstart.html) requires Python 3.10 or later, and the Python 3.9 validation emits the SDK's deprecation warning. Therefore Python 3.9 is a compatibility result, not the recommended long-lived AWS runtime. The approved deployment smoke should create a Python 3.11 venv; falling back to 3.9 requires an explicit exception and an SDK lifecycle review.

## Local benchmark

Scope: **LOCAL MEASURED ONLY**, synthetic highly repetitive fixture, 50,000 records, zstd level 1.

| Measurement | Result |
|---|---:|
| Raw bytes | 14,038,890 |
| Compressed bytes | 82,490 |
| Ratio | 0.5876% |
| Compression time | 0.006545 s |
| Compression throughput | 2,045.48 MiB/s |
| Decompression time | 0.002412 s |
| Decompression throughput | 5,550.42 MiB/s |
| Process max RSS | 46,153,728 platform units on macOS |

The synthetic ratio is not representative of market traffic. Historical Mac samples were approximately 3.5-4.2% and remain planning evidence only. Neither throughput result is AWS performance evidence; Amazon Linux must be rebenchmarked during the approved smoke.

## Local validation evidence

- Python compileall: PASS.
- Python full regression: 521 passed (baseline 478; net +43 tests).
- Python 3.9 pre-soak unittest set: 43 passed; collector initialization/snapshot and all three operator CLI imports: PASS.
- pip dependency check: PASS.
- Dashboard: typecheck, lint, 4 tests, production build, and `npm audit` with 0 vulnerabilities: PASS.
- Terraform: recursive fmt, backend-disabled read-only-lock init, and validate: PASS. No plan or apply was run in this phase.
- Trivy: the same five reviewed findings, no new finding: unrestricted HTTPS egress accepted for dynamic public exchange/AWS endpoints; SSE-S3 and AWS-managed CloudWatch encryption accepted; VPC flow logs and S3 access logging remain optional hardening.
- Changed-file secret scan: no AWS access key, session token, private key, or account-specific credential pattern found.
- `git diff --check`: PASS.

The local filesystem was measured at approximately 92.5% used during validation, above the production default critical threshold. Archive E2E tests therefore use an explicit 99.9% fixture threshold and separately inject the exact 90% critical condition to prove that new work stops while raw remains. The AWS deployment must retain the reviewed 90% default.

## 100 GiB hot-buffer capacity

Planning inputs are 24 GiB/day uncompressed, an approximately 2.3 GiB OS/application footprint observed previously, and a conservative 4.2% compressed ratio. The pipeline keeps raw plus compressed data while cleanup is disabled and does not create a full restored duplicate.

| Threshold | Approximate raw capacity including 4.2% compressed copy and 2.3 GiB base | Time at 24 GiB/day |
|---|---:|---:|
| 70% warning | 65.0 GiB | 2.71 days |
| 80% high | 74.6 GiB | 3.11 days |
| 90% critical | 84.2 GiB | 3.51 days |

A 72-hour collection is approximately 72 GiB raw + 3.0 GiB compressed + 2.3 GiB base = 77.3 GiB. It fits narrowly below 80% under these assumptions. Five days without verified cleanup is approximately 127.3 GiB and does not fit.

Therefore 100 GiB is a hot buffer, not a five-day uncompressed archive. Before an actual soak, choose one reviewed option: enable verified-only cleanup after S3 restore verification, shorten the retained local window, or expand EBS. Cleanup remains off until separately approved.

## Deployment and short-smoke plan — not executed

The next separately approved gate is:

1. Deploy the exact tested Git commit to the existing instance.
2. Create a Python 3.11 venv from `infra/aws/requirements-pre-soak.txt`; keep the collector off. Python 3.9 remains a tested compatibility fallback, not the preferred supported SDK runtime.
3. Seal environment, epoch, run, schema, architecture, clock, config fingerprint, and path settings in a non-secret environment file owned by the service user.
4. Run local fixture compression, decompression, receipt, raw/zstd FULL-SCAN, and restore checks with cleanup off.
5. Upload one explicitly named temporary fixture object to the exact epoch prefix; verify HEAD checksum/version and streamed restore; do not delete it automatically.
6. Publish one controlled CloudWatch metric batch and verify the exact namespace/dimension; do not run a periodic publisher while the collector is off.
7. Verify filesystem, chrony, DNS, public exchange connectivity, and SSM; collector remains off through this checkpoint.
8. Only after a separate collector-smoke approval, run a short public-WebSocket collection, stop gracefully, archive finalized output, FULL-SCAN raw/zstd, and prove raw remains.

Future scheduling should keep compression/S3 work out of the WebSocket writer path. Start with a single one-shot archive worker and a low-frequency metric publisher. Any systemd collector enable/start remains an explicit later approval; no unit was installed or enabled in this phase.

## Remaining gates

- **PRE-DEPLOY:** install and fixture-test this exact commit on Amazon Linux; no collector start.
- **PRE-SOAK:** approve a capacity/verified-cleanup policy and validate actual S3/CloudWatch paths, Python runtime, zstd performance, time sync, and graceful stop.
- **PRE-ALPHA:** pass the clean AWS infrastructure soak and FULL-SCAN; then collect a separate prospective research dataset.
- **PRE-LIVE:** alpha, sealed holdout, paper, reconciliation, and explicit live approval remain mandatory.

Alpha: **BLOCKED**

Paper trading: **NOT STARTED**

Live trading: **DISABLED**
