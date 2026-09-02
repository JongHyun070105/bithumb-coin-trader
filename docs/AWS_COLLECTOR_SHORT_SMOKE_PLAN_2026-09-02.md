# V9.1 AWS Collector 45-Minute Short-Smoke Plan — 2026-09-02

## Status and boundary

This document is a planning and provenance-seal artifact. It does not authorize
or perform a Terraform apply, collector launch, WebSocket connection, archive
cleanup, 120-minute smoke, 72-hour soak, alpha research, paper trading, or live
trading.

- Planning status: `READY FOR SHORT-SMOKE PROVENANCE APPLY APPROVAL`
- Runtime software commit: `c965fa08608cf33a81ce29994bdc97b7a6f2d66b`
- Previously deployed guest commit: `0138b879534156f644cef1dcd604c64fc1356df7`
- Guest deployment status for the new runtime commit: `NOT DEPLOYED`
- Environment: `aws-apne2-research`
- Region / AZ: `ap-northeast-2` / `ap-northeast-2a`
- Instance: `t3.medium`, `x86_64`
- Raw schema: `4`
- Clock source: `Amazon Time Sync Service 169.254.169.123`
- Collector auto-start: `NONE`
- Private exchange API: `DISABLED`
- Alpha: `BLOCKED`
- Live trading: `DISABLED`

The runtime software commit is deliberately distinct from the later Git commit
that records this config and evidence document. The latter must never be
reported as the code loaded by the short-smoke process.

## Immutable short-smoke seal

- Collector epoch: `aws-short-smoke-20260902-38cb8a72`
- Collector run ID: `aws-short-smoke-run-20260902T145020Z-38cb8a72`
- Canonical runtime config:
  `infra/aws/seals/aws-short-smoke-20260902.runtime.json`
- Canonical config SHA-256:
  `c1f470b71db0b6b654daeef166fae04b7969bb116f6c0ee695ccc28269a41355`
- Duration: `2700` seconds
- Compression candidate: zstd level 1
- Archive concurrency: 1
- Archive grace: 600 seconds
- Verified cleanup: `OFF`

The fingerprint covers the non-secret config file with sorted JSON keys and
compact separators. The epoch and run ID are launch-seal values and are not
embedded in that reusable config; path and archive locations contain exactly
one `{collector_epoch}` placeholder and are rendered fail-closed at launch.

## Public feed seal

Only public market-data WebSockets are permitted.

- Bithumb (20): `KRW-BTC`, `KRW-ETH`, `KRW-XRP`, `KRW-SOL`, `KRW-DOGE`,
  `KRW-ADA`, `KRW-XLM`, `KRW-LINK`, `KRW-AVAX`, `KRW-BCH`, `KRW-ETC`,
  `KRW-NEAR`, `KRW-SUI`, `KRW-APT`, `KRW-TRX`, `KRW-SHIB`, `KRW-SAND`,
  `KRW-MANA`, `KRW-AXS`, `KRW-DOT`
- Binance (4 internal symbols): `btcusdt`, `ethusdt`, `solusdt`, `xrpusdt`
- Upbit (4): `KRW-BTC`, `KRW-ETH`, `KRW-SOL`, `KRW-XRP`

No key, secret, account holding, private API, or order route is part of this
configuration.

## Isolated guest paths

After rendering the sealed epoch:

- Raw: `/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/raw`
- Manifests: `/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/manifests`
- Compressed: `/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/compressed`
- Receipts: `/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/archive-receipts`
- Durable metrics: `/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/collector_metrics.json`
- Publisher state: `/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/metric-publisher-state.json`
- Logs: `/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/logs`
- Remote archive: `market-data/temporary/aws-short-smoke-20260902-38cb8a72/...`

The remote class is temporary. Canonical archive is not used. Raw deletion is
not permitted.

## Cross-UTC-hour execution window

The first planning candidate is:

- Start: `2026-09-02 15:40:00 UTC` / `2026-09-03 00:40:00 KST`
- UTC boundary: `2026-09-02 16:00:00 UTC`
- Natural bounded end: `2026-09-02 16:25:00 UTC` / `2026-09-03 01:25:00 KST`

This is not a scheduled automation. If provenance apply, deployment, or
preflight cannot finish safely before this window, use the next `HH:40 UTC` to
`HH+1:25 UTC` window without changing the sealed epoch or run ID. Never shorten
the run merely to keep the first candidate time.

## Pre-launch gates

All gates must be rechecked immediately before a separately approved launch:

1. `HEAD` and `origin/main` contain the runtime commit and the later seal
   artifact commit; the deployed application checkout itself is pinned to
   runtime commit `c965fa08608cf33a81ce29994bdc97b7a6f2d66b`.
2. The config fingerprint recomputes exactly to the sealed SHA-256.
3. Terraform provenance/prefix update has separate explicit approval and has
   completed in-place without replacement.
4. EC2 and SSM are healthy; chrony tracks Amazon Time Sync.
5. Exactly zero collector processes and zero collector auto-start units/timers
   exist before launch.
6. Guest AWS credentials resolve only from the instance profile. Static keys
   and Bithumb credentials are absent.
7. Disk, inode, DNS, TLS, S3, and CloudWatch preflights pass.
8. Live IAM permits only the sealed temporary/canonical epoch prefixes; the
   runbook uses only the temporary prefix.

Any mismatch is fail-closed. Do not widen IAM, networking, or storage policy in
the launch step.

## Launch command template — do not execute in this planning phase

Run as the non-root service user from a checkout pinned to the runtime commit:

```bash
PYTHONPATH=src .venv/bin/python scripts/run_cross_market_collector.py \
  --bithumb-markets 20 \
  --duration 2700 \
  --config-file infra/aws/seals/aws-short-smoke-20260902.runtime.json \
  --storage-base-dir /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/raw \
  --environment-id aws-apne2-research \
  --collector-epoch aws-short-smoke-20260902-38cb8a72 \
  --run-id aws-short-smoke-run-20260902T145020Z-38cb8a72 \
  --config-fingerprint c1f470b71db0b6b654daeef166fae04b7969bb116f6c0ee695ccc28269a41355 \
  --runtime-commit c965fa08608cf33a81ce29994bdc97b7a6f2d66b
```

The operator records the PID immediately after launch. The command must run as
a bounded foreground/operator-owned process; it must not be installed or
enabled as an auto-start service during the short smoke.

## Metrics sidecar plan

The production publisher is a separate bounded loop at 60-second cadence. It
starts only after the collector PID and durable metrics file are verified, and
stops when that PID exits. Each invocation is one fail-closed publish attempt:

```bash
PYTHONPATH=src .venv/bin/python scripts/publish_collector_metrics.py \
  --environment-id aws-apne2-research \
  --region ap-northeast-2 \
  --metrics-path /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/collector_metrics.json \
  --state-path /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/metric-publisher-state.json \
  --storage-path /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72 \
  --ops-log /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/logs/metric-publisher-ops.jsonl
```

The supervisor logic must be PID-bound, preserve each nonzero exit as evidence,
and never synthesize zero counters. CloudWatch alarms are not considered
operational unless the publisher produces verified live metric data.

## Closed-hour manifest and archive sequence

At least 600 seconds after the UTC boundary, and while the second-hour
partition remains active:

1. Reconfirm the collector PID is unchanged and durable metrics name every
   active partition.
2. Generate/repair manifests only for closed UTC-hour files with the explicit
   epoch roots and exact runtime commit:

```bash
PYTHONPATH=src .venv/bin/python scripts/generate_offline_manifests.py \
  --raw-root /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/raw \
  --manifest-root /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/manifests \
  --collector-git-commit c965fa08608cf33a81ce29994bdc97b7a6f2d66b
```

3. Run `manage_pre_soak_archive.py scan` with the same raw, manifest,
   compressed, receipt, and metrics paths, `--grace-seconds 600`, and the
   sealed temporary prefix.
4. Confirm every eligible file belongs to the first, closed UTC hour and no
   path appears in durable `active_partition_files`.
5. Finalize eligible files one at a time with `--store s3` and the explicit
   `--allow-aws-write` guard. Do not pass `--cleanup-verified`.
6. Verify compressed SHA, S3 checksum/version/HEAD, streamed restore,
   decompression, restored raw SHA/bytes/records, and durable receipt.
7. Keep both local raw and S3 objects. Object delete and local cleanup remain
   forbidden.

Any manifest error, active-file overlap, conditional write conflict, checksum
disagreement, restore disagreement, or receipt failure stops the archive lane
without stopping or modifying the collector.

## Acceptance criteria

### Process and boundary

- Exactly one unchanged collector PID for the bounded run.
- Natural duration is at least 2700 seconds with graceful final flush.
- Raw partitions exist on both sides of the UTC boundary for every expected
  exchange/stream combination that produced events; absence is reported and
  never silently converted to zero.
- No collector service/timer is enabled after exit.

### Identity, timestamp, and durability

- Binance order-book records contain the correct symbol identity; `UNKNOWN`
  is a failure.
- Wall receive timestamps and monotonic receive timestamps are present and
  parseable; monotonic reversals are zero.
- Duplicate trade IDs, exchange/local timestamp outliers, reconnects, queue
  drops, backpressure, writer errors, and unpersisted counts are measured from
  durable evidence. `NOT VERIFIABLE` remains distinct from zero.
- Durable metrics match environment, epoch, run ID, config fingerprint, and
  runtime commit.

### Archive and integrity

- Only the first closed hour is archived while the collector is live.
- Active second-hour files are excluded.
- Manifest coverage and raw SHA verification pass for all closed partitions.
- zstd, compressed SHA, decompression, S3 checksum, restore SHA, and receipt
  verification pass.
- Final raw and `.zst` FULL-SCAN results are logically equivalent.
- Corrupt compressed input fails closed.
- Raw cleanup is not run.

### Monitoring and shutdown

- Publisher cadence evidence is approximately 60 seconds without a parallel
  duplicate publisher.
- WriterErrors, QueueDrops, and DiskUsedPercent are sourced from genuine
  durable collector state, not diagnostic zeros.
- Disk remains below the warning threshold, or the run is failed closed.
- After natural exit, final manifests and a bounded FULL-SCAN complete; no
  process is restarted automatically.

Passing this short smoke validates only the bounded AWS runtime path. It does
not imply 120-minute readiness, 72-hour readiness, alpha readiness, paper
readiness, or live readiness.

## Capacity decision

- 45-minute smoke: 100 GiB is adequate with substantial headroom; expected raw
  volume is under 1 GiB at the measured historical rate.
- 72-hour cleanup-off soak: expand to 150 GiB before launch. The prior 100 GiB
  estimate approaches the 80% high-water threshold once OS, staging, raw, and
  compressed evidence coexist.
- Five-day cleanup-off run: use a 200 GiB class volume or approve a separately
  verified retention/cleanup policy. Do not rely on optimistic compression to
  protect active raw.

No resize is included in this short-smoke provenance plan.

## Terraform plan evidence

Validation used the temporary assumed role
`bitcoin-trader-terraform-provisioner`; no root/default credential fallback was
present.

- Terraform fmt: PASS
- Terraform init (`-backend=false -lockfile=readonly`): PASS
- Terraform validate: PASS
- Pinned AMI: `ami-08d82cf148c92fcc3`, available, Amazon Linux/UNIX, x86_64,
  EBS/HVM
- Fresh provider-backed plan: `0 add / 3 change / 0 destroy / 0 replace`
- In-place updates:
  - collector IAM role provenance tags
  - collector epoch S3 prefix inline policy
  - EC2/root-volume provenance tags
- No-op: VPC, subnet, routing, security group, AMI, instance type, EBS size and
  encryption, S3 controls, CloudWatch logs/alarms, permissions boundary
- Security-group ingress remains 0; SSH and public dashboard/trading ports
  remain absent.

Access Analyzer returned zero errors and zero security warnings for the planned
collector policy. Live role simulation confirmed the existing epoch prefix is
allowed, the not-yet-applied new prefix is denied, other buckets are denied,
DeleteObject is denied, the exact CloudWatch namespace is allowed, and a wrong
namespace is denied. `SimulateCustomPolicy` for the not-yet-live document is
not authorized to the provisioner role, so that planned-policy simulation is
`NOT VERIFIED`; permission was not widened. Static statement comparison shows
only the two epoch-prefix resources change and the action sets remain
unchanged.

The plan file was a temporary review artifact. Terraform apply was not run.

## Remaining approval gates

1. Approve only the three in-place Terraform provenance/prefix updates.
2. Deploy and re-verify runtime commit `c965fa08608cf33a81ce29994bdc97b7a6f2d66b`.
3. Re-run all pre-launch gates and select a future cross-hour window.
4. Obtain separate approval to launch the bounded 45-minute collector smoke.

Current terminal state: `READY FOR SHORT-SMOKE PROVENANCE APPLY APPROVAL`.
