============================================================
FINAL 30H DEEP PREFLIGHT
============================================================

AS_OF_UTC = 2026-09-28T09:21:04Z for the latest guest snapshot

FINAL_RUNTIME_COMMIT = b4d482363e2f988dad9c6d29053f97e1e4160883
FINAL_RUNTIME_TREE = 5c96ed79fee107c1604ee7018621835910221fdf

PREFLIGHT_REPOSITORY_COMMIT = 4660e77ea294d7877883886ec52f5fecbb2c9b0f
PREFLIGHT_REPOSITORY_TREE = 87e8825bf292a45071464c909464188bed61053e

EXACT_RUNTIME_FULL_SUITE = PASS
EXACT_RUNTIME_FULL_SUITE_EVIDENCE = b4d4823: 1704 passed, 2 skipped, 181 subtests
FINAL_REPOSITORY_FULL_SUITE = 1712 passed, 2 skipped, 181 subtests at 4660e77
PYRIGHT = PASS; 0 errors, 0 warnings, 0 informations
COMPILEALL = PASS
BASH_SYNTAX = PASS (daemon wrapper and both generated artifact pairs)
DIFF_CHECK = PASS

BOOTSTRAP_REMOTE_READ_REQUIREMENT = CONDITIONAL
AUDITOR_READ_DESIGN = Option C; existing guest collector role, exact GetObject
IAM_CHANGE_REQUIRED = NO for the existing architecture

S3_PREFIX_COLLISION_REQUIREMENT = MANDATORY
S3_COLLISION_SOLUTION = exact-prefix ListObjectsV2 through provisioner; empty

WITNESS_FINAL_SMOKE_RUN_ID = aws-validation-witness-e2e-smoke-run-20260928T060612Z-3aa9f841
WITNESS_FINAL_SMOKE_RESULT = PASS for 120-second E2E witness acceptance
RUNTIME_S3_UPLOAD = PASS
AUDITOR_EXACT_READ = PASS through the guest instance role; bootstrap user denied
HASH_PARITY = PASS; local and remote SHA-256 d761a975880f0d6fd3d7d33a275b40e2b880e31db485da0c757c6a3ac35288d9
LIVE_AUDITOR_ACCEPTANCE = PASS for live-object runtime identity and witness checks

IDEMPOTENCY = PASS for the modeled retry/restart cases below
DEFAULT_LIVE_SAFE = PASS; default entry exits before daemon state or lock

FINAL_RUN_ID = DEFERRED; the 2026-09-28T061814Z candidate is stale
FINAL_EPOCH = DEFERRED; mint and check a fresh identity at authorization time

PRELAUNCH_GATE = FAIL
MANDATORY_GATES = 17 / 20
NEXT_30H_LAUNCH_READY = NO
NEXT_30H_LAUNCHED = NO

ALPHA = UNPROVEN
DATASET_QUALIFIED = NO
RETROSPECTIVE = NOT_STARTED
PAPER = NOT_STARTED
LIVE = DISABLED
PRIVATE_API = DISABLED
============================================================

## Decision

NO-GO. The current software path has a real 120-second witness smoke PASS and
the exact runtime full suite passes. The preflight is not launch-ready because
the conservative 30-hour disk projection leaves only 0.14 GiB above the
existing 50 GiB free-space floor, the new final identity has deliberately not
been minted, and the complete fresh readiness bundle is absent. No cleanup,
IAM change, Terraform change, 30-hour run, or trading action occurred.

The 2026-09-26 30-hour run remains an immutable official FAIL. Its runtime
completed 108017.072 seconds with 29/29 cohorts, 2204/2204 slots, and clean
main-process exits, but the terminal evidence contract failed. None of this
report changes that verdict or reuses that run identity.

The 120-second smoke is terminal-witness acceptance evidence only. It does not
qualify a 30-hour run, feed coverage, alpha, PAPER, or LIVE.

## Exact code and validation

The execution runtime is still `b4d482363e2f988dad9c6d29053f97e1e4160883`,
tree `5c96ed79fee107c1604ee7018621835910221fdf`. The guest runtime worktree
matched that exact SHA/tree and was clean. The only changes after that commit
are the prelaunch evidence gate and regression tests; no collector, observer,
scheduler, finalizer, witness, launcher, trading, S3-target, or auditor-source
file changed after `b4d4823`.

The full suite passed in a clean detached worktree at exact runtime `b4d4823`
with 1704 passed, 2 skipped, and 181 subtests. After the prelaunch-gate and idempotency-test updates, the
full repository suite passed at exact repository commit `4660e77` with 1712
passed, 2 skipped, and 181 subtests. The updated targeted suite passed 64 tests.
Changed-Python-scope Pyright reported zero errors, warnings, and informations.
`compileall`, shell syntax checks, and `git diff --check` passed. The diff from
`b4d4823` to `4660e77` is limited to `scripts/final_30h_prelaunch_gate.py` and
the two test files; the gate excludes itself from the runtime-source diff
because it is prelaunch tooling and is never executed by the collector.

The canonical full-suite command was:

```text
PYTHONPATH=src python3 -m pytest -q
```

`test-results/.last-run.json` in the active checkout retained SHA-256
`e22df5d0991eb28c09093b1e678b3fa8cd1fab48185d38e67cf79fb6e63ad5ea`.
The active `main` checkout and its untracked `.agents/`, `.codex/`,
`.commandcode/`, `AGENTS.md`, and `test-results/` paths were not modified.

## S3 auditor read requirement and design

**Classification: `CONDITIONAL`.** The existing prelaunch gate requires the
auditor to retrieve exact witness and receipt objects, but pre-existing
governance does not name `bitcoin-trader-bootstrap` as the required reader.
The requirement is for an exact-object auditor read; the identity that performs
that read is unspecified. The existing EC2 collector role already has the
read path and can retrieve the real smoke object. The auditor software ran
separately over that object and checked runtime identity and witness binding.
This is software/audit separation, not a separate AWS credential principal.

```text
AUDITOR_READ_DESIGN = Option C: existing guest runtime-role retrieval
IDENTITY = EC2 instance role bitcoin-trader-aws-apne2-research-collector
ACTION = s3:GetObject
RESOURCE_PATTERN = existing temporary validation namespace; exact object was
                   <bucket>/<smoke-prefix>/terminal/terminal-receipt.json
CONDITION = sealed bucket, key, region, epoch, and run ID are checked by the
            auditor against the sealed identity
WHY_REQUIRED = the terminal auditor must retrieve the exact uploaded witness
WHY_LISTBUCKET_NOT_REQUIRED = GetObject uses a known exact key; ListBucket is
                              reserved for the separate empty-prefix collision gate
```

Terraform source `infra/aws/main.tf` grants the collector role
`s3:GetObject` and `s3:PutObject` under the temporary archive namespace. The
role's existing grant covers more than just terminal witnesses, so it is not
a dedicated auditor role. No separate bootstrap role is mandated by the
pre-existing runbook or the gate schema. A future policy for a separately
credentialed bootstrap auditor could grant only `s3:GetObject` on
`arn:aws:s3:::bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433/market-data/temporary/aws-validation-*/terminal/terminal-receipt.json`;
that would not require `s3:ListBucket`. A dedicated read-only role would add
trust and policy changes without satisfying a currently specified need.

The live `bitcoin-trader-bootstrap` identity authenticated as
`arn:aws:iam::080109295433:user/bitcoin-trader-bootstrap`, but its exact
`GetObject` request for the smoke receipt was denied with AWS's message that no
identity-based policy allows `s3:GetObject`. No policy document was changed or
applied. The guest instance role fetched the exact object; its SHA-256 matched
the guest-local witness and caller-provided smoke hash. The auditor returned
`runtime_identity=PASS` and `terminal_witness=PASS` on that remote object,
including exact run ID, epoch, bucket, key, region, `s3_uploaded=true`,
`service_result=success`, and exit status `0`.

`IAM_CHANGE_REQUIRED = NO` for the existing architecture.

```text
POLICY_BEFORE = unchanged; bootstrap effective GetObject is DENY, guest role
                exact GetObject is ALLOW
POLICY_AFTER = unchanged
NEW_ACTIONS = none
NEW_RESOURCES = none
PRIVILEGE_EXPANSION = none
```

The account's bootstrap identity cannot read the full IAM user policy with its
current permissions. No claim about the unseen policy document is made.

## S3 prefix collision gate

**Classification: `MANDATORY`.** The existing V4 runbook §3.4 requires the
validation prefix to be empty before authorization. Local/guest uniqueness
checks alone do not replace that pre-existing requirement. The exact
`aws-validation-observability-30h-20260928-20260928T061814Z-v1/` prefix was
queried read-only using `bitcoin-trader-provisioner`; `KeyCount=null` and no
first key means the prefix is empty. The provisioner could run the exact-prefix
ListObjectsV2 query without an IAM change. `s3:GetObject` for a known key and
`s3:ListBucket` for this collision check are separate actions and identities.

```text
S3_PREFIX_COLLISION_REQUIREMENT = MANDATORY
S3_COLLISION_SOLUTION = provisioner lists only the exact candidate prefix;
                        returned no objects; no reservation marker added
CURRENT_CANDIDATE_COLLISION = PASS
FINAL_FRESH_ID_COLLISION = NOT_VERIFIABLE; final identity is deferred
```

The candidate prefix is now stale. A newly timestamped final epoch must receive
its own S3 prefix query at authorization time. The reservation-marker option
was not added: it would add a second identity state without improving the
existing empty-prefix contract.

Least-privilege caveat: the exact-prefix query used the existing
`bitcoin-trader-provisioner`; the exact request prefix does not prove that the
principal can list only that prefix. The tracked example policy's
`ManageExactArchiveBucket` statement allows `s3:ListBucket` on the exact
archive bucket without an `s3:prefix` condition. The collector boundary example
has an epoch-scoped `s3:prefix` condition, but the collector's active identity
policy in `infra/aws/main.tf` grants object Get/Put only, so that boundary
alone cannot perform the collision query. If policy-level scoping is required,
the durable narrow design is a dedicated read-only checker with
`s3:ListBucket` on the exact archive bucket and `s3:prefix` restricted to
`market-data/temporary/aws-validation-*`, with no object or bucket-management
actions. That would require an explicit IAM policy/trust change; none was
applied. The current empty-prefix gate is observed PASS under the existing
provisioner workflow, while policy-level least privilege for that checker
remains an improvement to authorize separately.

## Guest observers and units

Snapshot at `2026-09-28T09:21:04Z`: root disk use 71%, 1% inode use,
3,354,161,152 available memory bytes, and no swap. Three observer processes
remain. Each has PPID 1 and runs the sealed
`/var/lib/bitcoin-trader/venv-pre-soak/bin/python -m bithumb_coin_trader.runtime_observer`
with its own epoch, run ID, data root, and S3 prefix.

| PID | PPID / start | Worktree / run owner | Epoch and data root | Observer unit | Status |
|---|---|---|---|---|---|
| 1652530 | 1 / 2026-09-26 13:49:59Z | `fresh-6h-v6r1-22e06b9`; official 30H owner `aws-validation-observability-30h-run-20260926T135000Z-v3` | epoch `aws-validation-observability-30h-20260926-20260926T135000Z-v3`; `/var/lib/bitcoin-trader/30h-validation/aws-validation-observability-30h-20260926-20260926T135000Z-v3` | `bitcoin-trader-obs-aws-validation-observability-30h-run-20260926T135000Z-v3.service` | `HISTORICAL_PRESERVE` |
| 1797648 | 1 / 2026-09-28 05:53:44Z | `/var/lib/bitcoin-trader/runtime-worktrees/final-30h-preflight-20260928`; owner `aws-validation-witness-e2e-smoke-run-20260928T054945Z-1e6877af` | epoch `aws-validation-witness-e2e-smoke-20260928T054945Z-1e6877af`; `/var/lib/bitcoin-trader/30h-validation/aws-validation-witness-e2e-smoke-20260928T054945Z-1e6877af` | `bitcoin-trader-obs-aws-validation-witness-e2e-smoke-run-20260928T054945Z-1e6877af.service` | `STALE_SAFE_TO_STOP`; not stopped |
| 1798802 | 1 / 2026-09-28 06:08:45Z | `/var/lib/bitcoin-trader/runtime-worktrees/final-30h-preflight-20260928-b4d4823`; owner `aws-validation-witness-e2e-smoke-run-20260928T060612Z-3aa9f841` | epoch `aws-validation-witness-e2e-smoke-20260928T060612Z-3aa9f841`; `/var/lib/bitcoin-trader/30h-validation/aws-validation-witness-e2e-smoke-20260928T060612Z-3aa9f841` | `bitcoin-trader-obs-aws-validation-witness-e2e-smoke-run-20260928T060612Z-3aa9f841.service` | `STALE_SAFE_TO_STOP`; not stopped |

The two smoke supervisors exited successfully after their bounded runs. The
054945 smoke receipt says `s3_uploaded=false`; the 060612 smoke receipt says
`s3_uploaded=true` and is the accepted witness run. Neither observer shares
the proposed 30H candidate's run ID, epoch, data root, unit name, or S3 prefix.
None is needed by the next run. The first observer is retained because it is
attached to the historical failed 30H run and continues to emit historical
health observations.

Cause: the launcher starts a detached observer with `Restart=no` and
`RuntimeMaxSec=infinity`; `RuntimeObserver.run_forever()` runs until SIGINT or
SIGTERM and does not exit when the bounded supervisor exits. This is a
persistent observer lifecycle leak, not an unidentified/manual process. It
does not conflict with an identity-scoped next run. The prelaunch gate now
checks exact run/epoch conflicts, not global observer absence.

No cleanup is required to prevent a collision with the current candidate. If
an operator later chooses to stop the two stale smoke observers, these are the
exact reversible actions:

| Target | Why stale / evidence | Command | Reversibility | Historical evidence impact |
|---|---|---|---|---|
| PID 1797648 / `bitcoin-trader-obs-aws-validation-witness-e2e-smoke-run-20260928T054945Z-1e6877af.service` | Its bounded smoke supervisor exited; the epoch/run are not the next run identity. Receipt records `s3_uploaded=false`. | `sudo systemctl stop -- bitcoin-trader-obs-aws-validation-witness-e2e-smoke-run-20260928T054945Z-1e6877af.service` | Start the same unit if future health publishing is needed. | Stops future observer output only; keeps receipt, data root, logs, and S3 objects. |
| PID 1798802 / `bitcoin-trader-obs-aws-validation-witness-e2e-smoke-run-20260928T060612Z-3aa9f841.service` | Its bounded smoke supervisor exited; the epoch/run are not the next run identity. Receipt and remote witness are preserved. | `sudo systemctl stop -- bitcoin-trader-obs-aws-validation-witness-e2e-smoke-run-20260928T060612Z-3aa9f841.service` | Start the same unit if future health publishing is needed. | Stops future observer output only; keeps receipt, data root, logs, and S3 objects. |

Neither stop was run. Remote service stops require the authorized operational
workflow. The historical 30H observer is not included in the optional plan.

Two historical launcher units remain `LoadState=loaded`,
`ActiveState=active`, `SubState=exited`, both with `Result=success`,
`MainPID=0`, `Restart=no`, and `RuntimeMaxUSec=infinity`:

| Unit | Run ID / epoch from artifact path | Start / stop (age at snapshot) | Dependencies | Classification |
|---|---|---|---|---|
| `bitcoin-trader-launch-fresh-30h-v3.service` | run `aws-validation-observability-30h-run-20260926T135000Z-v3`; epoch `aws-validation-observability-30h-20260926-20260926T135000Z-v3` | Sep 26 13:43:52Z / 13:50:02Z (43h 37m at snapshot) | `system.slice`, `sysinit.target`; no Wants | `NON_BLOCKING_HISTORICAL` |
| `bitcoin-trader-launch-fresh-6h-v6r1.service` | run `aws-validation-observability-6h-run-20260925T185000Z-v6r1`; epoch `aws-validation-observability-6h-20260925-20260925T185000Z-v6r1` | Sep 25 18:20:00Z / 18:50:02Z (63h 01m at snapshot) | `system.slice`, `sysinit.target`; no Wants | `NON_BLOCKING_HISTORICAL` |

Run IDs and epochs above are read from their launch-artifact paths; those
values are not explicit systemd unit properties.

Both ExecStart commands point to their historical `launch-ec2.sh --launch`
artifacts and exited 0. These are completed launcher helpers, not active
collectors/supervisors. Their evidence and units were not reset or removed.
The current candidate unit name is
`bitcoin-trader-30h-aws-validation-observability-30h-run-20260928T061814Z-v1.service`;
no unit with that candidate identity was present. Final-unit collision must be
rechecked for the new identity.

```text
GUEST_PROCESS_READY = PASS for the stale 061814Z candidate only
GUEST_UNIT_READY = PASS for the stale 061814Z candidate only
FINAL_IDENTITY_PROCESS_READY = NOT VERIFIABLE; no final identity selected
FINAL_IDENTITY_UNIT_READY = NOT VERIFIABLE; no final identity selected
```

## Resource headroom

Latest guest values at 09:21:04Z:

```text
filesystem size = 322,042,834,944 bytes
used = 226,226,184,192 bytes (71%)
free = 95,816,650,752 bytes = 89.24 GiB
inodes = 157,280,240 total; 206,359 used (1%)
available memory = 3,354,161,152 bytes = 3.12 GiB
swap = 0
```

The 2026-09-26 30H evidence root used 33,198,192,742 bytes. Its breakdown was
27,743,197,919 raw; 3,652,433,484 quarantine; 1,031,892,362 coverage;
750,700,984 compressed archives; 11,418,464 archive receipts; 4,557,556
manifests; 3,670,070 finalization progress; 278,914 logs; and small health /
terminal files. The raw files' maximum single UTC-hour size was 1,217,418,119
bytes. The previous successful 30H root used 23,176,503,335 bytes. The failed
Sep 26 run is the larger observed total and supplies the conservative basis.
Temporary publication files were not itemized as a separate retained-size
category; their transient peak is not independently known. The non-raw total
is computed as run-root total minus raw data, and the added 5 GiB sizing buffer
covers model uncertainty rather than asserting an observed temporary-file peak.

```text
observed total run bytes = 33,198,192,742
observed raw peak bytes/hour = 1,217,418,119
observed non-raw run bytes = 5,454,994,823
conservative 30H projection = peak raw/hour * 30 + non-raw
                             = 41,977,538,393 bytes (39.09 GiB)
projected free = 53,839,112,359 bytes (50.14 GiB)
existing runbook floor = 50 GiB
margin above existing floor = 152,021,159 bytes (0.14 GiB)
preflight buffer = 5 GiB
shortfall against floor + buffer = 5,216,687,961 bytes (4.86 GiB)
```

The existing runbook's 50 GiB check is a pre-run free-space floor. Current
free space is 89.24 GiB, so that check passes. The conservative projection
leaves 50.14 GiB after the 30H run, only 0.14 GiB above a 50 GiB post-run
reserve. The improved prelaunch gate requires that post-run reserve plus an
additional 5 GiB sizing buffer. This is a stricter preflight margin, not a
change to the historical runbook. `DISK_HEADROOM=FAIL` against that explicit
margin. No evidence was deleted. A future operator must provide at least
4.86 GiB more measured pre-run free space and recalculate using a fresh
snapshot; any EBS resize requires separate approval.

## Historical failure replay matrix

| Failure | Historical cause | Fix commit(s) | Regression evidence | Candidate result / residual |
|---|---|---|---|---|
| 6H-v1 finalization stall | synchronous manifest finalization blocked the collector event loop at a cohort boundary | `c033a85` | `test_cohort_boundary_finalization_runs_in_background_without_blocking_loop` | PASS; full suite. Long-run scheduling still needs a real run. |
| 6H-v2 late confirmation / Binance gap | websocket ping timeout and confirmation/segment-gap handling were too brittle | `4fcdd81` | `test_late_confirmation_fails`, `test_mid_hour_reconnect_segment_not_late_confirmation`, reconnect fault-injection tests | PASS; full suite. External venue outages can still create real coverage gaps. |
| 30H-v2 Bithumb feed gaps and scheduler HOL | reconnect/heartbeat gaps plus finalized failed cohorts blocking later work | `4fcdd81`, `1b7a499` | `test_finalized_failure_does_not_block_later_frozen_journal`, later-cohort and Bithumb fault-injection tests | PASS; full suite. No claim that future market feeds cannot gap. |
| 6H-v4r1 timestamp false conflict | timestamp-only trade copies and ticker timestamp collisions were misclassified | `94b9293` | `test_replay_04_07_08_timestamp_only_trade_copies_are_duplicates`, `test_replay_05_ticker_timestamp_collision_retains_both_states` | PASS; replay suite. |
| 6H-v5r1 stream_type false conflict | SNAPSHOT and REALTIME copies of the same semantic trade conflicted | `22e06b9` | `test_v5r1_trade_snapshot_vs_realtime_is_duplicate_not_conflict`, semantic-difference control | PASS; replay suite. |
| closed_at_utc retry idempotency | retry timestamps could vary and change coverage evidence identity | `48cfa0a` | expanded cases described below | PASS for modeled cases; no 30H qualification implied. |
| 30H-v3 terminal witness epoch/S3 failure | generic epoch and missing exact uploaded receipt caused official terminal-contract FAIL | `4944235`, `92d5a98`, `b4d4823` | witness binding/fail-closed tests, offline malformed-witness rejection, live 060612 smoke | PASS for short smoke chain; full 30H auditor remains unrun. |

These fix commits are ancestors of the b4 runtime. `bithumb_redundancy.py`,
feed coverage semantics, heartbeat/reconnect logic, archive scheduler, and
receipt lifecycle source have no runtime-source delta from the previous
operational runtime `22e06b9527798567e185fb0dd41dca3a448f444e` to `b4d4823`.
Regression rows were run in the exact runtime suite and current repository
suite.

## closed_at_utc adversarial results

The updated `tests/test_closed_hour_finalizer.py` covers:

- same logical cohort finalized twice at a seconds boundary and at
  `12:59:59Z -> 13:00:00Z` minute/hour rollover;
- a reconstructed `FixtureBundle`, progress store, archive store, and frozen
  journal on retry (process-restart simulation, not an OS process kill);
- identical `closed_at_utc`, evidence SHA-256, coverage bytes, remote checksums,
  and unchanged bytes of already-existing raw/coverage receipts;
- scheduler duplicate invocation: second run returns `IDLE` and does not
  process a completed cohort again;
- evidence present / receipt absent: retry rebuilds a receipt with the same
  artifact identity and checksums; only verification timestamps are new;
- receipt present / evidence absent: retry recreates identical evidence and
  reuses the existing receipt byte-for-byte;
- injected `os.replace` failure during coverage publication: previous target
  bytes survive, partial temp file is removed, and a retry produces the same
  evidence bytes.

```text
same frozen observation -> stable observation_end_utc closed_at_utc
same frozen observation -> stable coverage evidence identity and hash
existing terminal receipt -> byte-for-byte unchanged on retry
```

These local tests do not inject a partial write into every receipt state
transition. The separate 30H run remains necessary to observe actual
long-duration scheduling and finalization.

## Live witness smoke and generated systemd unit

Smoke identity:

```text
RUN_ID = aws-validation-witness-e2e-smoke-run-20260928T060612Z-3aa9f841
EPOCH = aws-validation-witness-e2e-smoke-20260928T060612Z-3aa9f841
RUNTIME_COMMIT = b4d482363e2f988dad9c6d29053f97e1e4160883
RUNTIME_TREE = 5c96ed79fee107c1604ee7018621835910221fdf
duration = 120 seconds; observed supervisor elapsed = 122.724904 seconds
process exit = 0; ExecStopPost executed
exact epoch / run ID = PASS
local witness / upload = PASS; s3_uploaded=true
remote exact receipt = PASS
local SHA-256 = d761a975880f0d6fd3d7d33a275b40e2b880e31db485da0c757c6a3ac35288d9
remote SHA-256 = d761a975880f0d6fd3d7d33a275b40e2b880e31db485da0c757c6a3ac35288d9
runtime-role GetObject = PASS
auditor runtime_identity = PASS; terminal_witness = PASS
```

The auditor fetched the exact remote S3 object with the guest instance role,
then ran its identity and witness acceptance functions on the sealed smoke
identity, runtime config, supervisor result, and fetched object. This proves
live object acceptance through Option C. The 30H cohort/slot and full
duration checks are not expected to pass on a 120-second smoke.

For the locally generated 30H candidate `aws-validation-observability-30h-run-20260928T061814Z-v1`,
the actual `render_systemd_run()` argv (not source-only inspection) was:

```text
UNIT = bitcoin-trader-30h-aws-validation-observability-30h-run-20260928T061814Z-v1.service
PYTHON_BINARY = /var/lib/bitcoin-trader/venv-pre-soak/bin/python
SCRIPT = /var/lib/bitcoin-trader/runtime-worktrees/final-30h-preflight-20260928-b4d4823/scripts/terminal_witness.py
EPOCH = aws-validation-observability-30h-20260928-20260928T061814Z-v1
RUN_ID = aws-validation-observability-30h-run-20260928T061814Z-v1
S3_BUCKET = bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433
S3_PREFIX = market-data/temporary/aws-validation-observability-30h-20260928-20260928T061814Z-v1
S3_REGION = ap-northeast-2
UPLOAD_ENABLED = true
```

Exact generated `ExecStopPost` property:

```text
--property=ExecStopPost=/var/lib/bitcoin-trader/venv-pre-soak/bin/python /var/lib/bitcoin-trader/runtime-worktrees/final-30h-preflight-20260928-b4d4823/scripts/terminal_witness.py --data-dir=/var/lib/bitcoin-trader/30h-validation/aws-validation-observability-30h-20260928-20260928T061814Z-v1 --epoch=aws-validation-observability-30h-20260928-20260928T061814Z-v1 --run-id=aws-validation-observability-30h-run-20260928T061814Z-v1 --s3-bucket=bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433 --s3-prefix=market-data/temporary/aws-validation-observability-30h-20260928-20260928T061814Z-v1 --s3-region=ap-northeast-2 --allow-s3-write
```

The `systemd-run` property was passed as one CLI argument; systemd parses its
embedded command into the executable and arguments. Paths contain no spaces or
shell-sensitive characters. Renderer tests cover long IDs, hyphenated paths,
and rejected shell metacharacters. The candidate launch duration is 108000
seconds, hard ceiling 108180, and systemd maximum 108240. No rendered command
was launched.

## Sealed Python environment

```text
collector = /var/lib/bitcoin-trader/venv-pre-soak/bin/python
observer = /var/lib/bitcoin-trader/venv-pre-soak/bin/python
scheduler = /var/lib/bitcoin-trader/venv-pre-soak/bin/python
terminal witness = /var/lib/bitcoin-trader/venv-pre-soak/bin/python
auditor = /var/lib/bitcoin-trader/venv-pre-soak/bin/python
Python = 3.11.16
websockets = 15.0.1; zstandard = 0.25.0; boto3 = 1.43.86
runtime import path = candidate runtime worktree src
```

Generated collector, publisher, scheduler, observer, and witness commands use
the sealed interpreter. `launch-ec2.sh` exports the runtime `src` path; the
systemd unit sets `PYTHONPATH=src` with the exact worktree as its working
directory. The actual smoke ran ExecStopPost in this venv and successfully
uploaded its receipt. This removes the prior system-Python/sealed-Python
mismatch for the witness path.

## Artifact determinism and source minimality

Two independent generations of the same local-only 30H candidate produced the
same semantic identity, run ID, epoch, runtime commit/tree, duration 108000,
target 30 full hours, config fingerprint `2b9ab0dfff…`, raw-root template,
witness config, and S3 target. Runtime config, launch command, launch scripts,
and manifest artifact hashes matched. The only semantic-independent byte
differences were `identity.json.sealed_at_utc` and the derived identity hash in
`sealed-manifest.json`.

The generated raw root contains exactly one `{collector_epoch}` placeholder.
Both launch artifact sets validated. `bash -n` passed on both generated
`launch.sh` / `launch-ec2.sh` pairs. These files were generated under a local
temporary test directory and were not submitted or launched.

```text
OLD_RUNTIME = 22e06b9527798567e185fb0dd41dca3a448f444e
CANDIDATE_RUNTIME = b4d482363e2f988dad9c6d29053f97e1e4160883

RUNTIME_CORE = unchanged; no collector/redundancy/coverage-source delta
FINALIZER = closed_hour_finalizer.py; closed_at_utc pinned to observation end
WITNESS = terminal_witness.py; exact run binding, sealed Python, fail-closed S3
LAUNCHER = bounded_supervisor.py, launch_artifacts.py, transient launcher
AUDITOR = audit_fresh_30h_v3_terminal.py and its offline evidence contract
SAFETY = autonomous_trader.py default-live guard and wrapper default-off
TEST_ONLY = regression tests, including current 30H gate/idempotency tests
DOC_ONLY = docs/final-30h-preflight-20260928.md
UNRELATED = zero
```

No research integration, dashboard, external dataset, or strategy change was
dragged into this runtime. Bithumb active-active collection, dedup identity,
timestamp semantics, heartbeat policy, reconnect behavior, scheduler ordering,
and terminal receipt behavior are covered by the current full suite; their
runtime source is unchanged from `22e06b9`.

`REAL_ORDER_POSSIBLE_BY_DEFAULT = NO`. The default daemon test verifies exit
before lock/state side effects, and the wrapper defaults
`BITHUMB_LIVE_TRADING=false`. The sealed runtime says private API disabled,
PAPER not started, LIVE disabled, and public data only. This does not claim
orders are impossible if a separately enabled live path is deliberately used.

## Fail-closed prelaunch command

The gate is at `scripts/final_30h_prelaunch_gate.py`. It now requires hash-bound
local evidence files under the readiness-evidence directory, rejects path
traversal/symlinks, binds suite and Pyright evidence to runtime commit/tree,
requires `target_full_hours=30`, validates exact 30H duration and artifact
bindings, requires exact run/epoch scoped process and unit conflict counts,
checks bucket/prefix/principal for the empty-prefix claim, verifies the exact
terminal receipt key and matching valid SHA-256 values, and computes disk
projection from the measured worst hourly raw size plus observed non-raw
growth. Passing this technical-readiness command does not authorize or start a
run; authorization must remain `PREPARED_NOT_AUTHORIZED` until a separate
exact-identity human GO.

The command was exercised with a current timestamp but an intentionally
incomplete evidence bundle and returned machine-readable `PRELAUNCH_GATE=FAIL`
with exit status 1. The real final identity and fresh 15-minute readiness
bundle do not yet exist. Full current counts:

```text
MANDATORY_GATES_PASS = 17 / 20
PASS = 17
FAIL = 2 (disk buffer; incomplete prelaunch readiness)
NOT VERIFIABLE = 1 (final run identity/collision set not selected)
PRELAUNCH_GATE = FAIL
```

The `17 / 20` count above uses these 20 mission-level gates (it is not the
prelaunch command's 36 individual readiness checks):

| # | Mission gate | Result and scope |
|---:|---|---|
| 1 | Exact-runtime full suite | PASS at `b4d4823` |
| 2 | Final repository full suite | PASS at `4660e77` |
| 3 | Pyright, compileall, shell syntax, diff check | PASS |
| 4 | Witness end-to-end smoke | PASS for the 120-second smoke |
| 5 | Runtime S3 upload | PASS for the 060612 smoke |
| 6 | Auditor exact object read | PASS via guest runtime role |
| 7 | Local/remote hash parity | PASS for the 060612 smoke |
| 8 | Live auditor identity and witness acceptance | PASS for the smoke object |
| 9 | S3 prefix collision | PASS for the now-stale `061814Z-v1` candidate |
| 10 | Observer identity conflict | PASS for that candidate; 3 unrelated observers remain |
| 11 | Unit/data-root/artifact-path conflict | PASS for that candidate |
| 12 | `closed_at_utc` retry idempotency | PASS for the modeled cases |
| 13 | Default-live safety | PASS |
| 14 | Historical reliability regression matrix | PASS in suite/replay evidence |
| 15 | Artifact semantic determinism | PASS across two generations |
| 16 | Sealed Python environment | PASS for generated commands and smoke witness |
| 17 | Runtime source minimality | PASS; no unrelated runtime delta |
| 18 | Conservative 30-hour disk headroom | FAIL; short by 4.86 GiB against floor plus buffer |
| 19 | Fresh final identity and collision set | NOT VERIFIABLE; identity deferred |
| 20 | Complete fresh 15-minute readiness bundle | FAIL; no current bundle, gate returns FAIL |

## Final identity and authorization boundary

The generated but unlaunched `061814Z-v1` identity passed its guest path/unit
collision check and its S3 prefix was empty at the latest exact-prefix query.
It is now stale. It must not be copied into the final readiness bundle or
reused. No newer final run ID or epoch has been issued, preserving identity
space until launch authorization is actually requested.

At the future authorization window, generate one fresh identity and verify all
four guest paths (unit, data root, launch-artifact root, audit root), local
evidence directory, and exact S3 prefix. Re-run the prelaunch gate with new
hash-bound evidence. Keep its authorization evidence false/unstarted until the
separate exact-identity GO is supplied.

## Self-consistency challenge

An independent read-only Phase 3 review was requested for candidate commit
`4660e77`; the worker timed out after 1,200 seconds and returned no findings.
Treat that review as `NOT VERIFIABLE`, not as an independent PASS. The batch
collector has no Phase 3 batch for this one-worker delegation.

Direct repository cross-check at the pushed report head confirmed that the
only post-runtime code files are `scripts/final_30h_prelaunch_gate.py`,
`tests/test_closed_hour_finalizer.py`, and
`tests/test_final_30h_prelaunch_gate.py`; all later commits are documentation
only. `b4d4823^{tree}` still equals the sealed runtime tree. The report keeps
bootstrap GetObject denial separate from the guest role's live exact read,
marks the final identity stale/deferred, names the observers and historical
units, and leaves the conservative capacity and fresh-readiness gates failed.
No direct cross-check finding changes the NO-GO result.

## Delivery state and actions required

```text
RUNTIME_AND_PREFLIGHT_COMMITS = 6220b75, 4660e77 (on top of b4d4823)
REPORT_INTRODUCTION_COMMIT = acc62a1
PRS = #23 OPEN/DRAFT; report pushed, no merge performed
LOCAL_BRANCH = codex/final-30h-preflight-20260928
IAM_ACTION_REQUIRED = NO
OPERATIONAL_ACTION_REQUIRED = obtain at least 4.86 GiB additional measured
                             free-space margin (approved EBS expansion or
                             explicitly authorized cleanup), then remeasure
USER_ACTION_REQUIRED = decide/authorize the capacity action; later provide
                       separate exact-identity GO before any 30H launch
```

No EBS resize, cleanup, IAM change, additional S3 write, or 30-hour launch was
performed while completing this report. The previously documented 120-second
smoke did upload its terminal witness. Do not remove observer history or
evidence to make the capacity check pass.

============================================================
FINAL 30H DEEP GO / NO-GO
============================================================

FINAL_RUNTIME = b4d482363e2f988dad9c6d29053f97e1e4160883
FINAL_TREE = 5c96ed79fee107c1604ee7018621835910221fdf

EXACT_RUNTIME_FULL_SUITE = PASS; 1704 passed, 2 skipped, 181 subtests
LATEST_REPOSITORY_SUITE = PASS; 1712 passed, 2 skipped, 181 subtests at 4660e77
PYRIGHT = PASS

WITNESS_E2E = PASS for the 120-second smoke only
RUNTIME_S3_UPLOAD = PASS
AUDITOR_EXACT_READ = PASS via the guest runtime role; bootstrap identity DENIED
HASH_PARITY = PASS
LIVE_AUDITOR = PASS for exact smoke identity and terminal-witness checks

S3_COLLISION_GATE = PASS for stale 061814Z candidate; final fresh ID UNKNOWN
OBSERVER_STATE = 1 historical preserve; 2 stale smoke observers, isolated
HISTORICAL_UNIT_STATE = 2 loaded active/exited success helpers; non-blocking

GUEST_PROCESS_READY = PASS for stale candidate only
GUEST_UNIT_READY = PASS for stale candidate only
CLOSED_AT_UTC = PASS for tested retries and restarts
DEFAULT_LIVE_SAFE = PASS

RESOURCE_HEADROOM = FAIL conservative buffer; short by 4.86 GiB
HISTORICAL_REGRESSION_MATRIX = PASS (unit/replay evidence; not a new 30H run)

FINAL_RUN_ID = DEFERRED
FINAL_EPOCH = DEFERRED
PRELAUNCH_GATE = FAIL
MANDATORY_GATES_PASS = 17 / 20

NEXT_30H_LAUNCH_READY = NO
NEXT_30H_LAUNCHED = NO

RESIDUAL_RISKS =
1. Current disk clears the old pre-run floor, but projected post-run free space is only 0.14 GiB above a 50 GiB reserve and misses the additional 5 GiB margin by 4.86 GiB.
2. Three long-lived observers remain; none shares the next run identity, but two stale units can be stopped only through authorized operations.
3. No fresh final identity or 15-minute readiness bundle exists; bootstrap credentials cannot read the object, though the existing runtime-role path passes.

IAM_ACTION_REQUIRED = NO for the existing architecture
OPERATIONAL_ACTION_REQUIRED = secure >=4.86 GiB additional free-space margin and remeasure
RUNTIME_AND_PREFLIGHT_COMMITS = 6220b75, 4660e77
REPORT_INTRODUCTION_COMMIT = acc62a1
PRS = #23 OPEN/DRAFT; report pushed, no merge performed
USER_ACTION_REQUIRED = capacity-action authorization, then separate exact-identity GO before 30H
============================================================

## Operational continuation — 2026-09-28

This continuation preserves the preceding report as a point-in-time record. It
does not alter the official historical 30H FAIL or authorize a new run.

### Fresh guest resource sample

Read-only guest evidence was collected through an SSM interactive session at
`2026-09-28T10:33:05Z`. The root volume remains the encrypted 300 GiB `gp3`
volume `vol-0d46ca4af0d463549`, attached to `i-008bc503c1136349f`. Its root
filesystem is XFS on `/dev/nvme0n1p1`; XFS has no ext4-style reserved-block
pool. No volume modification was in progress.

```text
DISK_TOTAL = 322,042,834,944 bytes (300 GiB EBS volume)
DISK_USED = 226,226,307,072 bytes
DISK_FREE = 95,816,527,872 bytes = 89.236095 GiB
INODES_FREE = 157,073,850
AVAILABLE_MEMORY = 3,414,827,008 bytes = 3.180 GiB
SWAP = 0
```

The prior source evidence remains the conservative growth basis: maximum
observed raw data of `1,217,418,119` bytes/hour, multiplied by 30 hours, plus
`5,454,994,823` bytes of non-raw run data. This yields `41,977,538,393` bytes
(`39.094629 GiB`) of projected run growth. The observed Sep 26 run used
`33,198,192,742` bytes and the preceding successful 30H run used
`23,176,503,335` bytes; neither is deleted or changed.

```text
RUNBOOK_HARD_FLOOR = 50 GiB free before run, per V4 runbook §3.1
PREFLIGHT_EXTRA_MARGIN = 5 GiB beyond the modeled 50 GiB post-run reserve
TARGET_POSTRUN_FREE = 55 GiB
RECOMMENDED_FREE_BEFORE_RUN = 101,033,338,713 bytes = 94.094629 GiB
PROJECTED_POSTRUN_FREE = 53,838,989,479 bytes = 50.141466 GiB
SHORTFALL = 5,216,810,841 bytes = 4.858534 GiB
RESOURCE_HEADROOM = FAIL
```

The legacy pre-run 50 GiB check passes. The stricter post-run reserve plus
buffer check fails; neither threshold was lowered.

### Capacity remediation assessment

The following bounded candidates were measured. Paths containing receipts,
witnesses, historical runs, or unclassified temporary files are preserved.

| Path | Size | Owner / purpose | Historical evidence | Safe to remove now? | Assessment |
|---|---:|---|---|---|---|
| `/tmp` | 229,376 bytes | OS temporary files; per-file owners not classified | Unknown | No | Not enough space; no files individually approved for deletion. |
| `/var/tmp` | 16,384 bytes | OS temporary files; per-file owners not classified | Unknown | No | Not enough space; no files individually approved for deletion. |
| `/var/cache/dnf` | 182,214,656 bytes | DNF package cache and metadata | No run evidence identified | No action taken | Potentially rebuildable, but only about 0.170 GiB; insufficient and remote cleanup still requires authorization. |
| `/var/lib/bitcoin-trader/30h-validation/aws-validation-witness-e2e-smoke-20260928T054945Z-1e6877af` | 41,516,834 bytes | Owner run `aws-validation-witness-e2e-smoke-run-20260928T054945Z-1e6877af`; 120-second witness smoke | Yes; local receipt SHA-256 `11f3108420c724e437aa53fed7061368fb4c3f0972658d31cb2b10d136017b6e` | No | Preserve smoke receipt and data; far below shortfall. |
| `/var/lib/bitcoin-trader/30h-validation/aws-validation-witness-e2e-smoke-20260928T060612Z-3aa9f841` | 43,486,012 bytes | Owner run `aws-validation-witness-e2e-smoke-run-20260928T060612Z-3aa9f841`; accepted 120-second witness E2E | Yes; receipt SHA-256 `d761a975880f0d6fd3d7d33a275b40e2b880e31db485da0c757c6a3ac35288d9` | No | Preserve the local receipt and remote-witness evidence; far below shortfall. |
| Sep 26 30H evidence root | 33,198,192,742 bytes | Historical 30H run `aws-validation-observability-30h-run-20260926T135000Z-v3` | Yes; official historical FAIL | No | Immutable reliability evidence. |
| Prior successful 30H evidence root | 23,176,503,335 bytes | Prior successful 30H run | Yes; historical reliability evidence | No | Preserve. |

The measured temporary/cache candidates are insufficient. No archived run
evidence or test results were removed. The cleanest bounded capacity action is
to increase the existing root EBS volume by the minimum whole-GiB amount that
clears the measured shortfall: `300 GiB → 305 GiB` (`+5 GiB`). At current
measurements this projects `55.141466 GiB` after the modeled run, about
`0.141466 GiB` above the 55 GiB target. This small remaining allowance must be
rechecked against a fresh measurement after any authorized expansion; do not
reuse this projection as the final resource gate.

The root partition is `/dev/nvme0n1p1`; `growpart` and `xfs_growfs` are already
installed. If separately authorized, the capacity-only procedure is:

```bash
aws ec2 modify-volume \
  --volume-id vol-0d46ca4af0d463549 \
  --size 305 \
  --region ap-northeast-2 \
  --profile bitcoin-trader-provisioner

# Wait for the EBS volume modification to reach optimizing/completed, then:
sudo growpart /dev/nvme0n1 1
sudo xfs_growfs -d /
df -B1 /
df -i /
```

This is a proposal only. No snapshot, EBS resize, partition change, filesystem
change, or cache cleanup was performed. The project infrastructure gate
requires explicit human GO for AWS mutation, and EBS volume size cannot be
decreased after expansion. AWS documents the volume modification flow and the
subsequent partition/XFS growth procedure in its [EBS volume modification
guide](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-modify-volume.html)
and [Linux filesystem expansion guide](https://docs.aws.amazon.com/ebs/latest/userguide/recognize-expanded-volume-linux.html).

### Observer and guest state

The two exact smoke observers requested for resolution were stopped with
`systemctl stop` at `2026-09-28T10:24:00Z`. Both received SIGTERM (15), logged
their graceful shutdown, and were deactivated successfully at `10:24:01Z`.
Their units now have `MainPID=0`, `ActiveState=inactive`, `SubState=dead`,
`Result=success`, and `Restart=no`. No `kill -9` was used. Both smoke roots and
receipts remain present with the hashes and sizes above; their last health-file
updates were before the stop.

Before stopping, each smoke observer had an HTTPS socket in `CLOSE_WAIT`; no
established socket was observed. No collector, supervisor, archive scheduler,
publisher, finalizer, or terminal-witness process was running after the stop.
The only remaining runtime process is the historical observer below.

```text
HISTORICAL_OBSERVER = NON_BLOCKING_PRESERVED
PID = 1652530; PPID = 1
RUN_ID = aws-validation-observability-30h-run-20260926T135000Z-v3
EPOCH = aws-validation-observability-30h-20260926-20260926T135000Z-v3
PURPOSE = continue health observation for the historical Sep 26 30H run
WHY_STILL_RUNNING = detached observer lifecycle has no run-duration bound
EXPECTED_LIFETIME = unbounded until SIGINT/SIGTERM or an authorized stop
INTERFERENCE_WITH_NEW_RUN = separate data root, unit name, run ID, epoch, and S3 prefix;
                            small shared-disk/network activity only
```

Its systemd unit is active/running with `Restart=no` and
`RuntimeMaxUSec=infinity`. Preserve it with the historical run evidence. The
two historical launch helper units remain loaded, `active/exited`,
`Result=success`, `MainPID=0`, and `Restart=no`; they remain
`NON_BLOCKING_HISTORICAL` and were not reset.

The guest's general writer-process baseline is idle, and the only active
observer is isolated to the historical identity. Exact new-target process and
unit collision checks remain `NOT VERIFIABLE` until the final identity exists.

### Exact-identity gate, readiness window, and review

The stale `aws-validation-observability-30h-run-20260928T061814Z-v1` identity
was not reused. Capacity remains below target, so no new run ID or epoch was
minted and no final 30H artifacts were generated. Consequently, final
run/epoch freshness, all path collisions, the exact S3-prefix collision gate,
artifact determinism for the final identity, and its one-shot fail-closed
prelaunch gate are still pending. The 15-minute readiness window was not
started because the resource gate is still failing; an observation before the
capacity decision would not qualify the final environment.

The retry of the independent read-only review timed out after 600 seconds and
returned no findings. It is `NOT VERIFIABLE`, not PASS. Primary self-review
confirmed: `b4d4823^{tree}` is still
`5c96ed79fee107c1604ee7018621835910221fdf`; the exact-runtime suite remains
`1704 passed, 2 skipped, 181 subtests`; the repository suite at `4660e77`
remains `1712 passed, 2 skipped, 181 subtests`; the diff after `4660e77` is
documentation only. The witness CLI rejects missing exact epoch/run ID and
requires explicit exact S3 settings; its internal helper's snapshot inference
is not reachable through the CLI without those required arguments. The gate
binds runtime commit/tree, 108000-second duration, 30 full hours, run/epoch,
witness target, S3 target, and disabled private/PAPER/LIVE states. No new
concrete software defect was found in this bounded self-check; the review is
still not independent.

```text
FINAL_RUN_ID = DEFERRED
FINAL_EPOCH = DEFERRED
FINAL_ARTIFACTS = NOT CREATED
RESOURCE_HEADROOM = FAIL
GUEST_PROCESS_READY = PASS for the empty new-writer baseline only
GUEST_UNIT_READY = PASS for the idle baseline only
FINAL_TARGET_COLLISIONS = NOT VERIFIABLE until identity creation
READINESS_WINDOW = NOT STARTED; 15 minutes required after capacity remediation
READINESS_BUNDLE = NOT CREATED
INDEPENDENT_REVIEW = NOT VERIFIABLE
MANDATORY_GATES_PASS = 17 / 20 under the prior matrix; not rescored for a fresh identity
PRELAUNCH_GATE = NO-GO: resource gate fails; final-identity gate not run
NEXT_30H_LAUNCH_READY = NO
NEXT_30H_LAUNCHED = NO
```

Only the capacity approval is being requested now. After approved capacity
remediation and fresh measurement, continue with a newly generated identity,
all exact collision checks, deterministic final artifacts, final gate, and a
new 15-minute readiness window. The long 30H launch still needs a separate
explicit GO for that final exact identity.

## Genuine fresh 36-check result — 2026-09-29

This addendum records the first genuine fresh readiness bundle. It supersedes
earlier statements that a complete bundle was absent, and does not change any
historical runtime or terminal verdict.

```text
RUN_ID = aws-validation-observability-30h-run-20260928T125700Z-675937ab
EPOCH = aws-validation-observability-30h-20260928-20260928T125700Z-675937ab
IDENTITY_STATUS = RETIRED_PRELAUNCH
REUSE_ALLOWED = NO

CHECKS_TOTAL = 36
CHECKS_PASS = 31
CHECKS_FAIL = 5
CHECKS_NOT_VERIFIABLE = 0

REAL_COLLECTION_SPAN_SECONDS = 407
OLDEST_EVIDENCE_AGE_AT_GATE = 652.537 seconds
TIMESTAMPS_REWRITTEN = NO

PRELAUNCH_GATE = FAIL
NEXT_30H_LAUNCH_READY = NO
NEXT_30H_LAUNCHED = NO
```

The original 43-file bundle is preserved at
`reliability-artifacts/aws-30h-final-preflight-20260929/aws-validation-observability-30h-20260928-20260928T125700Z-675937ab/fresh-readiness/`.
All 42 entries in `SHA256SUMS.txt` verified against their files. The manifest,
gate output, evidence JSON, and captured observation timestamps are retained as
collected; the evidence bundle was not edited or retimestamped.

The five failures are the three remediation lanes for the next identity:

```text
LANE A — auditor exact-object read path
  auditor_get_exact_witness = FAIL
  auditor_get_exact_receipt = FAIL
  auditor_head_exact_objects = FAIL
  Observed identity: bitcoin-trader-terraform-provisioner session role.
  The prior smoke evidence and this report's established Option C design use
  the guest runtime role for exact retrieval; do not broaden provisioner IAM.

LANE B — configured canonical guest runtime worktree
  runtime_commit_on_guest = FAIL
  The identity-configured path was absent. The collected evidence records an
  alternate worktree at the exact sealed runtime commit/tree, clean.

LANE C — contaminated proposed S3 prefix
  s3_prefix_not_reused = FAIL
  The exact prefix already contains `.iam-probe`. Retire this identity; keep
  that object and do not reuse or clean the prefix.
```

The runtime remains commit `b4d482363e2f988dad9c6d29053f97e1e4160883`, tree
`5c96ed79fee107c1604ee7018621835910221fdf`. No runtime source, IAM policy,
AWS resource, or S3 object was changed to record this result. A different exact
identity is required after the three remediation paths are prepared. No 30-hour
run was launched; a separate exact-identity human GO remains mandatory.

```text
ALPHA = UNPROVEN
DATASET_QUALIFIED = NO
CANDIDATE_FROZEN = NO
PAPER = NOT_STARTED
LIVE = DISABLED
PRIVATE_API = DISABLED
PROSPECTIVE_HOLDOUT_CONSUMED = NO
```
