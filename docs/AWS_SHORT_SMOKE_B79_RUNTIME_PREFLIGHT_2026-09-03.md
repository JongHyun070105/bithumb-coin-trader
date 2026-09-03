# AWS short-smoke b79 runtime preflight — 2026-09-03

## Result

- B79 provenance apply: **PASS**
- B79 runtime deployment: **PASS**
- Short-smoke launch preflight: **PASS**
- 45-minute short smoke: **NOT STARTED**
- Status: **READY FOR 45-MIN SHORT-SMOKE EXECUTION APPROVAL**

No collector, WebSocket subscription, metric publisher loop, archive worker,
market-data S3 write, cleanup, systemd service, 120-minute run, 72-hour run,
alpha research, paper trading, or live trading was started.

## Why c965 was blocked and b79 was required

The c965 Amazon Linux preflight completed only 51/52 targeted tests. The
writer-failure shutdown regression failed three isolated runs at approximately
2.01 seconds because teardown waited for the metrics worker's five-second
sleep. Runtime candidate `b79a093ff49e8cce13c80bf806da0777a79969bb`
cancels that worker, awaits cancellation, persists a final metrics snapshot,
and then raises the fail-closed runtime error.

Developer validation before this preflight was 526/526 full tests, 5/5 race
regressions, and compileall PASS.

## Provenance apply

- Runtime commit: `b79a093ff49e8cce13c80bf806da0777a79969bb`
- Config fingerprint:
  `f99543c669496ff97c950445e932a902bff11b03cdd275d9f56d39d212419f67`
- Epoch: `aws-short-smoke-20260902-38cb8a72` — unchanged
- Run ID: `aws-short-smoke-run-20260902T145020Z-38cb8a72` — unchanged
- Execution identity: temporary
  `bitcoin-trader-terraform-provisioner` assumed role, maximum 3600 seconds
- Root Terraform: no
- Fresh plan: `0 add / 2 change / 0 destroy / 0 replace`
- Changed resources: collector role provenance tags and EC2/root-EBS
  provenance tags
- IAM inline archive policy: no change
- Permissions boundary/default version: no change
- AMI, instance type, EBS size/encryption, VPC, subnet, route, security group,
  S3 controls, and CloudWatch alarms: no change
- Exact saved-plan apply: success
- Fresh post-apply provider plan: no changes
- Role, EC2, and root-EBS provenance read-back: match
- State: healthy, 29 addresses, mode 0600, Git-ignored, FileVault on
- Pre/post state backups: SHA equality verified
- Temporary plan: removed

## Effective IAM after apply

- New short-smoke temporary prefix: allow
- New short-smoke canonical prefix: allow
- Old epoch prefixes: deny
- DeleteObject: deny
- Other bucket: deny
- `BitcoinTrader/Collector` metric namespace: allow
- Wrong metric namespace: deny
- Boundary and inline archive prefix: unchanged

The execution runbook below uses only the temporary prefix. Canonical is not
used by the short smoke.

## Guest deployment

- Previous guest SHA: `c965fa08608cf33a81ce29994bdc97b7a6f2d66b`
- Current guest SHA: `b79a093ff49e8cce13c80bf806da0777a79969bb`
- Checkout: exact detached commit
- Guest status: clean
- Python: 3.11.16
- Venv: `/opt/bitcoin-trader/.venv`
- Pip check and required imports: pass
- Compileall: pass
- Config fingerprint recomputation: match
- Runtime config/feeds/paths/archive/metrics validation: pass
- Credential source: EC2 instance profile (`iam-role`)
- Control-plane instance profile role: match
- Static guest AWS credential files: none
- Private Bithumb credential files: none

The resealed config was created in the later evidence/reseal commit, not in the
runtime commit. Only
`infra/aws/seals/aws-short-smoke-20260902.runtime.json` is marked
checkout-local `skip-worktree`; its actual canonical fingerprint is verified
independently above. No application source from the later commit is overlaid.

## Guest validation

- Mandatory targeted subset: 52/52 pass
  - runtime config: 3/3
  - cross-market collector: 11/11
  - offline manifests: 4/4
  - collector metric publisher: 11/11
  - pre-soak archive: 24/24
- Isolated writer-failure race regression: 5/5 pass, approximately 0.008
  seconds per run
- Optional full guest regression: 526/526 pass in 55.416 seconds
- Corrected shutdown sequence: producer cancellation, metrics-task
  cancellation, final synchronous metrics persistence, fail-closed RuntimeError

## Runtime readiness

- SSM: online; sessions connected and closed cleanly
- chronyd: active
- selected source: Amazon Time Sync `169.254.169.123`
- leap status: normal
- observed system offset: about 1.5 microseconds slow
- disk: 100 GiB XFS, 3% used, 1% inode use
- security-group ingress: 0
- SSH/public dashboard/trading port: none
- S3: private, Block Public Access, versioning, SSE-S3, TLS-only
- CloudWatch alarms: five, unchanged; no synthetic metric was sent
- DNS/TLS: Bithumb, Binance, Upbit, S3, CloudWatch, and SSM pass
- WebSocket upgrade/subscription: not performed

The isolated epoch `raw`, `manifests`, `compressed`, `archive-receipts`, and
`logs` directories are mode 0750, owned by `bitcoin-trader`, and empty.
`collector_metrics.json` and `metric-publisher-state.json` are absent.

## Validated collector command — do not execute without separate approval

Runtime validation code was invoked directly with these values and returned
PASS. The collector command itself was not executed.

```bash
cd /opt/bitcoin-trader
PYTHONPATH=src .venv/bin/python scripts/run_cross_market_collector.py \
  --bithumb-markets 20 \
  --duration 2700 \
  --config-file infra/aws/seals/aws-short-smoke-20260902.runtime.json \
  --storage-base-dir /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/raw \
  --environment-id aws-apne2-research \
  --collector-epoch aws-short-smoke-20260902-38cb8a72 \
  --run-id aws-short-smoke-run-20260902T145020Z-38cb8a72 \
  --config-fingerprint f99543c669496ff97c950445e932a902bff11b03cdd275d9f56d39d212419f67 \
  --runtime-commit b79a093ff49e8cce13c80bf806da0777a79969bb
```

An approved execution must run as non-root `bitcoin-trader`, record PID,
start time and exit code, remain foreground/operator bounded, allow natural
2700-second exit, and install no systemd/cron/timer restart path.

## Metric sidecar plan — not started

Only after the approved collector PID exists and the durable metrics JSON is
valid, a single PID-bound supervisor may invoke this one-shot command every
approximately 60 seconds. It must hold a single-publisher lock, record every
nonzero exit, never synthesize a snapshot, and exit when the collector PID
does.

```bash
cd /opt/bitcoin-trader
PYTHONPATH=src .venv/bin/python scripts/publish_collector_metrics.py \
  --environment-id aws-apne2-research \
  --region ap-northeast-2 \
  --metrics-path /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/collector_metrics.json \
  --state-path /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/metric-publisher-state.json \
  --storage-path /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72 \
  --ops-log /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/logs/metric-publisher-ops.jsonl
```

## Archive plan — not started

At least 600 seconds after the UTC boundary, first generate closed-hour
manifests with explicit roots:

```bash
cd /opt/bitcoin-trader
PYTHONPATH=src .venv/bin/python scripts/generate_offline_manifests.py \
  --raw-root /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/raw \
  --manifest-root /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/manifests \
  --collector-git-commit b79a093ff49e8cce13c80bf806da0777a79969bb
```

Then run `manage_pre_soak_archive.py scan` with these common arguments:

```bash
--raw-root /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/raw
--manifest-root /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/manifests
--compressed-root /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/compressed
--receipt-root /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/archive-receipts
--metrics-path /var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/collector_metrics.json
--environment-id aws-apne2-research
--run-id aws-short-smoke-run-20260902T145020Z-38cb8a72
--collector-epoch aws-short-smoke-20260902-38cb8a72
--remote-prefix market-data/temporary/aws-short-smoke-20260902-38cb8a72
--compression-level 1
--grace-seconds 600
```

For each eligible closed-hour raw file, `finalize` adds `--raw`, `--store s3`,
the operator-supplied bucket, and `--allow-aws-write`; it never adds
`--cleanup-verified`. `verify` and `verify-restore` follow before any later
retention decision. Concurrency remains one. Durable
`active_partition_files` from the exact metrics path excludes the current
partition. None of these commands was executed in this preflight.

## Candidate execution window

- Candidate start: `2026-09-03 06:40 UTC` / `2026-09-03 15:40 KST`
- Candidate end: `2026-09-03 07:25 UTC` / `2026-09-03 16:25 KST`

This is reference-only, not a schedule or authorization. If it passes before
the operator returns, select another future `HH:40 UTC` to `HH+1:25 UTC`
window. Epoch and run ID remain unchanged because no collector has started.

## Final stop state

- Guest runtime: exact b79, clean
- Config fingerprint: exact f995
- Collector process: 0
- WebSocket collection: not started
- Metric loop: not started
- Archive worker: not started
- Runtime market data: none
- SSM session: closed
- Alpha: blocked
- Paper: not started
- Live: disabled

No account ID, public IP, bucket name, full sensitive ARN, credential, token,
MFA material, password, role unique ID, or IMDS credential payload is recorded.
