# AWS collector 45-minute short smoke — 2026-09-03

## Result

- AWS collector 45-minute short smoke: **FAIL**
- 120-minute validation: **NOT STARTED**
- 72-hour soak: **NOT STARTED**
- Alpha: **BLOCKED**
- Paper trading: **NOT STARTED**
- Live trading: **DISABLED**

The run failed closed. It did not reach manifest generation, compression,
archive, restore, or RAW/ZST comparison. No Terraform, IAM, security-group,
network, EBS, canonical-archive, cleanup, or delete operation was performed.

## Seal and launch

- Runtime commit: `b79a093ff49e8cce13c80bf806da0777a79969bb`
- Config fingerprint:
  `f99543c669496ff97c950445e932a902bff11b03cdd275d9f56d39d212419f67`
- Epoch: `aws-short-smoke-20260902-38cb8a72`
- Run ID: `aws-short-smoke-run-20260902T145020Z-38cb8a72`
- Planned window: `2026-09-03 09:40–10:25 UTC` /
  `2026-09-03 18:40–19:25 KST`
- Actual start: `2026-09-03 09:40:00.287690077 UTC` /
  `2026-09-03 18:40:00.289591460 KST`
- Collector PID: `127727`; exactly one process at launch
- Execution user: non-root `bitcoin-trader`
- Launch mode: bounded operator-owned Session Manager foreground wrapper
- Systemd, cron, timer, auto restart, and indefinite daemon: none

Immediately before launch, guest SHA, clean tree, config fingerprint, 20/4/4
feed seal, process count zero, empty isolated runtime paths, absence of static
and private-exchange credentials, EC2 instance-profile source, chrony, disk,
DNS/TLS, security-group ingress, provenance tags, and effective IAM all
matched the approved gate.

## Failure 1 — Binance feed absent

Bithumb and Upbit connected immediately. Every Binance WebSocket opening
handshake timed out. At `09:50:26 UTC`, more than ten minutes after launch,
the durable snapshot reported:

- Binance messages: `0`
- Binance reconnects: `18`
- Binance disconnects: `19`
- Bithumb messages: `49,244`
- Upbit messages: `14,233`
- Writer errors / queue drops / unpersisted: `0 / 0 / 0`

The configured Binance feed was therefore missing. No endpoint, code,
security group, route, IAM policy, or runtime configuration was changed to
work around it.

## Failed shutdown and early termination

A graceful `SIGINT` was requested at `09:50:54.144809529 UTC`. The collector
child inherited an ignored SIGINT disposition from the foreground wrapper's
background job, so it continued running. No second signal, `SIGTERM`, or
`SIGKILL` was sent.

At approximately `10:00:16 UTC`, the operator Session Manager connection hit
its inactivity timeout. The foreground wrapper and collector then disappeared
before the approved 2,700-second duration. The publisher detected the missing
collector PID and ended at `10:00:16.680269852 UTC`.

- Expected natural end: `10:25:00 UTC`
- Last durable metrics write: `10:00:14.528258 UTC`
- Observed runtime: approximately 20 minutes 16 seconds, not 45 minutes
- Collector exit code: not captured
- Graceful final flush: not verified
- End timestamp metadata: absent
- Final manifests: `0`

This is not a natural-duration or graceful-shutdown PASS. A future execution
procedure must address the bounded Session Manager idle-timeout and signal
handling before another run is approved.

## Durable metrics and CloudWatch

The publisher started only after a valid durable snapshot. It held one lock,
was bound to collector PID `127727`, and ran 19 successful cycles at about a
60-second cadence. The first cycle published genuine disk usage; subsequent
cycles published genuine WriterErrors, QueueDrops, and DiskUsedPercent deltas.

- Publisher failures / ops-log errors: `0`
- Last publisher state: writer errors `0`, queue drops `0`
- Final durable collector counters: writer errors `0`, queue drops `0`,
  unpersisted `0`, backpressure `0`
- Final Binance counters: messages `0`, reconnects `33`, disconnects `33`
- Five alarms after publication: all `OK`
- Direct metric datapoint read-back: not authorized to the provisioner role;
  permission was not widened

Alarm recovery only proves the metrics covered by those alarms. It does not
override the missing-feed and early-termination failures.

## Retained RAW evidence

The failed epoch retains 112 RAW JSONL files containing 119,997 records and
232,803,074 logical bytes. A production streaming full scan of every retained
RAW record passed:

- invalid JSON: `0`
- schema mismatch: `0`
- missing required fields: `0`
- non-finite numeric values: `0`
- malformed timestamps: `0`
- unknown markets: `0`
- scan failures: `0`

Actual RAW coverage was:

- Bithumb: `20/20` markets, orderbook/ticker/trade, 92,356 records
- Binance: `0/4` markets, 0 records
- Upbit: `4/4` markets, orderbook/trade, 27,641 records

This retained partial dataset is failure evidence, not research-ready or
alpha-ready data.

## UTC partition evidence

Partition creation crossed the UTC boundary:

- 09 UTC: 68 files, 118,246 records, 229,586,583 bytes
- 10 UTC: 44 files, 1,751 records, 3,216,491 bytes

The last durable `active_partition_files` contained 110 paths: all 68 files
from 09 UTC and 42 files from 10 UTC. The closed first-hour paths therefore
remained marked active instead of only the current hour being active. The
archive guard was not bypassed. This active-set behavior requires a source
fix and a new runtime seal before a future archive validation.

## Archive and cleanup

- Closed-hour manifest generation: not run
- Archive scan/finalize: not run
- Compression / ZST: not run
- S3 temporary objects under this epoch: `0`
- Canonical archive: not used
- Restore verification: not run
- RAW/ZST logical comparison: not run
- Local RAW cleanup: not run
- S3 DeleteObject: not run and still denied

## Post-run infrastructure read-back

- EC2: same instance, running, `t3.medium`
- Root EBS: 100 GiB, gp3, encrypted, in use,
  `delete_on_termination=false`
- Disk / inode use: `4% / 1%`
- Chrony: active; Amazon Time Sync selected; leap status normal
- Security-group ingress: `0`; SSH: none
- S3 controls: Block Public Access true, versioning enabled, SSE-S3,
  TLS-only policy unchanged
- IAM: new temporary prefix PutObject allowed; DeleteObject denied;
  exact CloudWatch namespace allowed; wrong namespace denied
- Terraform: not run

All collector, metric-publisher, and archive-worker process counts were zero
at final read-back. All operator Session Manager sessions were then closed;
active operator sessions were zero.

## Required next gate

Do not reuse this started epoch/run as a clean retry. Before another collector
execution, investigate and fix in the developer repository:

1. persistent Binance WebSocket opening-handshake timeouts;
2. first-hour paths remaining in `active_partition_files` after UTC rotation;
3. bounded operator launch signal handling and Session Manager idle timeout.

Any source fix requires a new runtime commit, config fingerprint, provenance
seal, epoch/run ID, tests, deployment, and separate execution approval. The
current result is **NOT READY FOR 120-MIN VALIDATION PLANNING**.
