# AWS pre-soak deployment and fixture smoke — 2026-09-02

## Scope and safety boundary

This evidence records deployment of the reviewed pre-soak software and a synthetic-only fixture smoke on the existing AWS collector instance. It does not authorize or record a collector start, market WebSocket connection, Terraform apply, IAM change, cleanup, 120-minute run, 72-hour soak, alpha research, paper trading, or live trading.

- Deployed software commit: `0138b879534156f644cef1dcd604c64fc1356df7`
- Operator session: dedicated login identity to temporary provisioner role
- Root execution: not used
- Guest credential source: EC2 instance profile; control-plane role and IMDS role name matched exactly
- Static AWS credentials: none
- Synthetic fixture only; no exchange or account data used

## Deployment and runtime

| Check | Result |
|---|---|
| Exact guest commit | PASS |
| Guest Git tree | CLEAN |
| Service user | `bitcoin-trader` non-root user |
| Python | 3.11.16 |
| Venv | `/var/lib/bitcoin-trader/venv-pre-soak` |
| Reviewed dependency install | PASS |
| `pip check` | PASS |
| Pre-soak tests | 43 PASS |
| Operator CLI imports | PASS |
| Collector process/service/timer/cron | NONE / NOT RUNNING |

The initial test invocation was made outside the repository and could not resolve the test package. Re-running the same test set from `/opt/bitcoin-trader` passed 43/43; this was an invocation-directory error, not a software failure.

The first S3 CLI attempt stopped before Python or an AWS API call because the interactive shell could not create the redirected result file under the service-user directory. No receipt or logical S3 write existed. The output target was corrected, after which the production archive command completed once. Exactly one fixture object exists under the authorized smoke prefix.

## Local synthetic fixture pipeline

The schema-v4 fixture remained outside the Git worktree. Cleanup was disabled.

| Check | Result |
|---|---|
| Raw manifest, SHA-256, bytes, records | PASS |
| Raw bytes / records | 538,890 / 2,000 |
| Zstandard level 1 | PASS; 4,912 bytes |
| Decompression and local restore | PASS |
| RAW/ZST FULL-SCAN | LOGICALLY EQUIVALENT |
| Corrupted ZST | FAIL-CLOSED |
| Raw cleanup | NOT RUN; raw retained |

## AWS synthetic Zstandard benchmark

These measurements are from a synthetic fixture on the current Amazon Linux 2023 `t3.medium`. They are not market-data compression or throughput claims.

| Measurement | Result |
|---|---:|
| Raw bytes | 14,038,890 |
| Compressed bytes | 82,490 |
| Ratio | 0.5876% |
| Compression | 0.009717 s / 1,377.89 MiB/s |
| Decompression | 0.012028 s / 1,113.10 MiB/s |
| Process observation | 0.37 s elapsed, 99% CPU, 37,024 KiB max RSS |

## S3 temporary fixture

The production `S3ArchiveStore` and `ArchivePipeline` code path was used with both explicit guards: `--store s3` and `--allow-aws-write`. The destination was the live-authorized temporary archive prefix, isolated below `pre-soak-smoke/<fixture-id>`. The canonical prefix was not used. The existing sealed infrastructure epoch was used only to satisfy the live IAM contract; it is not a new collector or research epoch.

| Check | Result |
|---|---|
| Live temporary-prefix contract | VERIFIED |
| Fixture objects created | 1 |
| Conditional request | `If-None-Match: *` verified in production path |
| HEAD and content length | SUCCESS / MATCH |
| `ChecksumSHA256` | MATCH after base64-to-digest conversion |
| Versioning | Version ID observed |
| Streamed GetObject restore | SUCCESS |
| Remote compressed SHA | MATCH |
| Restored raw SHA, bytes, records | MATCH |
| Durable receipt | `CLEANUP_ELIGIBLE`; not `CLEANED` |
| Object delete / local raw cleanup | NOT RUN / NOT RUN |
| Fixture object | RETAINED as tiny temporary evidence |

No bucket name, account identifier, object key, ARN, credential, or session identifier is recorded here.

## CloudWatch diagnostic path

One direct diagnostic datum was sent with the instance profile:

- Namespace: `BitcoinTrader/Collector`
- Metric: `PreSoakSmoke`
- Dimension: `EnvironmentId=aws-apne2-research`
- Value/unit: `1 Count`
- PutMetricData: SUCCESS (one datum)
- Remote operator read-back: NOT VERIFIED — existing operator read permission is absent

The production publisher was not started. With the collector snapshot missing, its bounded guest invocation returned `published=false` with zero publish attempts. No synthetic `WriterErrors`, `QueueDrops`, or `DiskUsedPercent` values were sent. The five existing alarms remain unchanged and in fail-closed `ALARM` state.

## Runtime and infrastructure read-back

| Check | Result |
|---|---|
| EC2 | running, `t3.medium`, `x86_64` |
| Root filesystem | 100 GiB XFS; 2.3 GiB before / 2.7 GiB after; 3% used |
| Inodes | 1% used |
| EBS | 100 GiB gp3, encrypted, `delete_on_termination=false` |
| Security group ingress / SSH | 0 / none |
| SSM | Online |
| Chrony | active; Amazon Time Sync selected; leap normal |
| DNS/TLS | S3, CloudWatch, SSM, GitHub, and PyPI PASS |
| S3 controls | private BPA all true, versioning enabled, SSE-S3, TLS-only policy |
| Collector permissions boundary | MATCHED reviewed template |
| CloudWatch alarms | 5, unchanged |
| NAT Gateway / VPC endpoint / EIP | 0 / 0 / 0 |
| Terraform apply / IAM change | NOT RUN / 0 |
| New application infrastructure resources | 0 |

The only intended AWS data-plane changes were the one retained synthetic S3 fixture and one diagnostic CloudWatch datum.

## Provenance and remaining gates

- Infrastructure provenance tags: **STALE-FOR-RUNTIME**. They predate the deployed software commit and were not changed in this smoke.
- Fixture ID: temporary smoke evidence only.
- Actual collector run ID: NOT CREATED.
- Actual collector epoch and config fingerprint: NOT SEALED.
- Collector and production metric/archive loops: NOT STARTED.
- 100 GiB capacity policy: PENDING. The cleanup-off 72-hour planning estimate remains about 77.3 GiB, leaving narrow headroom below the 80% high threshold.

Before any collector short smoke, seal a dedicated software commit, epoch, run ID, config fingerprint, IAM archive prefix, and infrastructure provenance. Before a 72-hour run, prefer fail-closed verified-only rolling cleanup after remote checksum and restore verification, with a modest EBS expansion considered as additional operational headroom. Neither action is approved or activated by this smoke.

## Gate result

- AWS pre-soak software deployment: PASS
- Python 3.11 runtime: PASS
- Local archive/FULL-SCAN fixture: PASS
- S3 fixture path: PASS
- CloudWatch write path: PASS; operator read-back not permitted
- Runtime/SSM/infrastructure: PASS
- Collector short smoke: NOT STARTED; ready for planning only
- 120-minute run / 72-hour soak: NOT STARTED
- Alpha: BLOCKED
- Paper: NOT STARTED
- Live: DISABLED

**READY FOR AWS COLLECTOR SHORT-SMOKE PLANNING**
