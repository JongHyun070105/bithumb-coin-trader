# AWS short-smoke runtime deployment preflight — 2026-09-03

## Result

- Runtime deployment of sealed commit
  `c965fa08608cf33a81ce29994bdc97b7a6f2d66b`: **PASS**
- Launch preflight: **BLOCKED**
- 45-minute collector execution: **NOT STARTED**
- Collector, WebSocket, metric publisher, and archive worker: **NOT STARTED**
- Alpha: **BLOCKED**
- Paper: **NOT STARTED**
- Live trading: **DISABLED**

The launch gate stopped because a targeted collector shutdown test failed
deterministically on the Amazon Linux guest. No exchange connection, S3 object
write, metric publication, archive operation, Terraform operation, IAM change,
or infrastructure change was performed.

## Developer and operator baseline

- Developer baseline before the source fix:
  `deac4ecf16bfe52227f6aeec2360da64119ec263`
- Developer HEAD and `origin/main`: matched; worktree clean
- Operator identity: temporary
  `bitcoin-trader-terraform-provisioner` assumed-role session
- Session maximum: 3600 seconds
- Root: not used
- AWS credential environment fallback: none
- SSM managed node: online; connection succeeded

## Guest deployment

- Previous guest SHA:
  `0138b879534156f644cef1dcd604c64fc1356df7`
- Deployed guest SHA:
  `c965fa08608cf33a81ce29994bdc97b7a6f2d66b`
- Guest checkout: detached at exact runtime commit
- Guest application tree: clean
- Checkout owner: `bitcoin-trader`
- Seal provenance: the runtime code commit intentionally predates the later
  planning commit containing the seal JSON. The one reviewed seal file was
  deployed from planning commit
  `165d8157d25cc87518a1fa967a2788ed26518dee` and excluded by its exact path in
  checkout-local `.git/info/exclude`; no application source was overlaid.
- Python: 3.11.16
- Virtual environment: rebuilt at `/opt/bitcoin-trader/.venv`
- `pip check`: pass
- Required boto3, websockets, zstandard, collector, storage, archive, and metric
  publisher imports: pass
- Guest AWS SDK credential source: EC2 instance profile (`iam-role`)
- Guest static AWS config/credential files: none
- Private API/Bithumb credential files: none found

## Original c965 seal

- Epoch: `aws-short-smoke-20260902-38cb8a72`
- Run ID: `aws-short-smoke-run-20260902T145020Z-38cb8a72`
- Config fingerprint:
  `c1f470b71db0b6b654daeef166fae04b7969bb116f6c0ee695ccc28269a41355`
- Canonical fingerprint recomputation: match
- Core config semantics and public-only flags: match
- Collector autostart/systemd enable: false/false
- Duration: 2700 seconds
- Cleanup: disabled
- Public feed seal: present in the deployed config

## Isolated runtime paths

The following paths were created with owner `bitcoin-trader`, mode `0750`:

- `/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/raw`
- `/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/manifests`
- `/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/compressed`
- `/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/archive-receipts`
- `/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260902-38cb8a72/logs`

All five directories remained empty. `collector_metrics.json` and
`metric-publisher-state.json` remained absent. No old fixture or market data was
copied or deleted.

## Targeted test failure

Guest targeted results before stopping (the five discovered files contained
53 tests; the earlier 51/52 shorthand was an arithmetic error):

- `test_short_smoke_runtime_config.py`: 3/3 pass
- `test_cross_market_collector.py`: 10/11 pass, 1 error
- `test_offline_manifests.py`: 4/4 pass
- `test_collector_metrics_publisher.py`: 11/11 pass
- `test_pre_soak_archive.py`: 24/24 pass
- Total: 52/53 pass

Failing test:
`test_writer_failure_cancels_producer_blocked_on_full_queue`.

The exact test was rerun three times on the guest and failed three times at
approximately 2.01 seconds. The writer failure correctly stopped producers,
but `run_collector()` waited for the metrics worker while it was inside a
five-second sleep. This made fail-closed shutdown scheduling-dependent and
violated the bounded targeted test.

## Developer source correction

The guest was not patched. The developer repository now cancels the metrics
worker during collector teardown and then persists one final metrics snapshot
synchronously. This preserves final evidence while avoiding the five-second
shutdown race.

- New runtime candidate commit:
  `b79a093ff49e8cce13c80bf806da0777a79969bb`
- Race regression test: 5/5 pass locally
- Collector tests: 11/11 pass
- Metric publisher tests: 11/11 pass
- Full suite: 526/526 pass
- Compileall: pass
- New runtime commit push: `origin/main` verified

## Resealed developer candidate

The repository seal was updated for the new runtime candidate, but it was not
deployed to the guest and was not applied to AWS provenance.

- Runtime candidate:
  `b79a093ff49e8cce13c80bf806da0777a79969bb`
- Epoch/run ID: unchanged
- New config fingerprint:
  `f99543c669496ff97c950445e932a902bff11b03cdd275d9f56d39d212419f67`
- Runtime config validation: pass
- Feed config validation: match

Live role, EC2, and EBS provenance still identify c965 and the previous config
fingerprint. Deploying or launching the new candidate would therefore require
a separately reviewed provenance update. No Terraform or IAM change was made
in this task.

## Fail-closed stop state

- Guest SHA: c965, clean
- Collector process: 0
- Metric publisher process: 0
- Archive worker process: 0
- Collector systemd unit/timer: none
- Collector cron: unavailable/not installed; no collector cron files found
- Runtime data: prepared but empty
- SSM session: closed cleanly
- Execution window: not selected because launch preflight is blocked
- Collector command: not approved and not executed
- Metric sidecar plan: blocked behind corrected-runtime provenance
- Archive plan: blocked behind corrected-runtime provenance

The next gate is not collector execution. It is review and application of the
new runtime commit/config provenance, followed by a fresh guest deployment and
complete launch preflight. Only after that passes may a future
`HH:40 UTC` to `HH+1:25 UTC` execution window be selected and separately
approved.

No account ID, public IP, bucket name, full sensitive ARN, credential, session
token, MFA material, password, role unique ID, or IMDS credential payload is
recorded here.
