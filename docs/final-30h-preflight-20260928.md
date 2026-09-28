# FINAL 30H PREFLIGHT

```text
============================================================
FINAL 30H PREFLIGHT
============================================================

PREVIOUS_30H =
FAIL

PREVIOUS_RUNTIME_EXECUTION =
FULL 30H COMPLETED

PREVIOUS_FAIL_CAUSE =
TERMINAL WITNESS / S3 EVIDENCE CONTRACT

PR22_REVIEW =
WITNESS_BINDING_FIX = PASS WITH FOLLOW-UP CORRECTIONS

WITNESS_SMOKE_RUN_ID =
aws-validation-witness-e2e-smoke-run-20260928T052441Z-v1 (PREPARED ONLY)

WITNESS_SMOKE_RESULT =
NOT VERIFIABLE — AWS PROFILE EXPIRED; SMOKE NOT EXECUTED

LOCAL_WITNESS =
NOT CREATED

REMOTE_WITNESS =
NOT UPLOADED OR READ

WITNESS_HASH_PARITY =
NOT VERIFIABLE REMOTELY; LOCAL BYTE-PARITY TEST PASSES

AUDITOR_SMOKE_RESULT =
LOCAL MALFORMED/CORRECTED PREDICATE TESTS PASS; END-TO-END BUNDLE NOT RUN

RUNTIME_S3_UPLOAD_PERMISSION =
SOURCE POLICY DECLARES SCOPED GET/PUT; LIVE ROLE NOT VERIFIABLE

AUDITOR_S3_EXACT_READ_PERMISSION =
NOT VERIFIABLE NOW; PRIOR EXACT READ RETURNED HTTP 403

IDEMPOTENCY_FIX =
INCLUDED; RETRY ACROSS UTC SECOND BOUNDARY PASSES LOCALLY

DEFAULT_LIVE_SAFE =
PASS BY CODE AND REGRESSION TEST; REAL_ORDER_POSSIBLE_BY_DEFAULT = NO

NEXT_RUNTIME_COMMIT =
054d43c3a43ef79919ac38bed4fa74d1b0689f53

NEXT_RUNTIME_TREE =
8c872ae7d3353c4d4e8649758266d2ec103dba63

FULL_TESTS =
1704 PASSED, 2 SKIPPED

PYRIGHT =
0 ERRORS, 0 WARNINGS, 0 INFORMATIONS (ALL CHANGED PYTHON FILES)

PRELAUNCH_GATE =
FAIL

NEXT_RUN_ID =
aws-validation-observability-30h-run-20260928T052441Z-v1 (LOCAL CANDIDATE; FRESHNESS NOT VERIFIED)

NEXT_EPOCH =
aws-validation-observability-30h-20260928-20260928T052441Z-v1 (LOCAL CANDIDATE; FRESHNESS NOT VERIFIED)

NEXT_30H_LAUNCH_READY =
NO

NEXT_30H_LAUNCHED =
NO

ALPHA = UNPROVEN
PAPER = NOT_STARTED
LIVE = DISABLED
PRIVATE_API = DISABLED
============================================================
```

## Scope and authority

This branch is a narrow reliability lineage rooted at historical runtime `22e06b9527798567e185fb0dd41dca3a448f444e`. It includes the closed-hour idempotency fix, PR #22's exact-witness binding work and corrective follow-ups, the default-live safety guard, the exact-target terminal auditor, and the final prelaunch gate. The broad research integration branch was not used as the runtime source.

No AWS or guest mutation was performed. No SSM command, `systemd-run`, S3 write, Terraform operation, IAM edit, private exchange call, order, paper run, or 30H launch was issued. The explicitly named `bitcoin-trader-bootstrap` AWS profile returned `Your session has expired. Please reauthenticate using 'aws login'` (exit 255). I did not start an interactive login or request credentials.

The 120-second smoke was authorized by the mission, but its required real guest `ExecStopPost` path and exact S3 put/get cannot run without that expired profile. Locally generated smoke artifacts and mocked unit tests are not represented as an executed smoke.

## Historical failure chain

The only historical target adjudicated here is run `aws-validation-observability-30h-run-20260926T135000Z-v3`, epoch `aws-validation-observability-30h-20260926-20260926T135000Z-v3`, runtime `22e06b9527798567e185fb0dd41dca3a448f444e`. Its official verdict remains **FAIL**. It ran `108017.072 / 108000` seconds, completed 29/29 cohorts and 2204/2204 slots, exited cleanly, and finalized. Those runtime results do not override the terminal-evidence contract.

| Chain stage | Expected | Actual in historical run | Corrected behavior | Coverage |
|---|---|---|---|---|
| Sealed runtime config | Exact epoch, run ID, S3 bucket/prefix/region, upload permission | Config had the sealed identity; run-end hook did not receive all of it | Artifact validator binds run ID, epoch, bucket, prefix, region, and required upload flag | Launch artifact and validator regression tests |
| Transient launch CLI | Forward the exact sealed identity without defaults | Old path left epoch to the generic unit prefix | CLI requires exact epoch/run ID and exact S3 target; absent fields fail before systemd invocation | Transient-launch tests, including missing-field and unsafe-value cases |
| Generated systemd unit / `ExecStopPost` | Exact sealed epoch/run ID and exact S3 target | Witness epoch became `bitcoin-trader-30h`; only data directory and run ID were forwarded | `ExecStopPost` includes exact epoch, run ID, bucket, prefix, region, and explicit write permission; shell-sensitive values are rejected | Rendered unit argument tests, long-epoch/slash/quoting tests |
| `terminal_witness.py` | Write exact identity and upload required terminal receipt | Witness had correct run ID but generic epoch, `s3_key=null`, `s3_uploaded=false` | No generic epoch fallback; upload target and permission are mandatory; client region is explicit; upload failure exits non-zero | Witness tests for missing flags, upload failure, region, and identity binding |
| S3 upload and local receipt | Successful remote stable object with bytes matching local receipt | No upload was attempted successfully; remote receipt parity was unavailable | Stable receipt payload is written locally before upload and uses the same bytes; failed PUT leaves false/null state and returns failure | Mock S3 test compares uploaded payload bytes to local bytes; remote parity remains unverified |
| Terminal auditor | Exact run/epoch/key/bucket/prefix/region and successful upload | The complete witness violates the pre-existing terminal predicate and deterministically fails | Auditor now checks the exact expected key and S3 target; historical malformed fixture fails and corrected fixture passes | Auditor regression suite; complete live bundle remains unavailable |

The recorded witness had `service_result=success`, `exit_status=0`, and `CLEAN_SUCCESS`, but its epoch was `bitcoin-trader-30h`, `s3_key=null`, and `s3_uploaded=false`. That is a deterministic terminal contract failure. A full offline audit bundle was not available: the receipt mirror and complete finalization trace were absent. Historical exact S3 read returned HTTP 403, and S3 listing was not called. The 2026-09-15 V3 failure is separate and unchanged.

## PR #22 adversarial review and corrective result

PR #22 is still an open Draft at `4944235630cff58573d7348d5b949c546dd11ea8`, based on `fix/closed-hour-restart-idempotency-20260927`; it has no reported CI checks. The review confirms its core epoch/run binding correction, but found additional ways the next run could fail: a disabled-upload configuration remained possible, region was implicit, upload failure could still produce CLI success, and the receipt was rewritten after the uploaded bytes were formed. Those gaps were corrected in this branch.

The new renderer and artifact validator require `SEALED_EPOCH == EXECSTOPPOST_EPOCH == WITNESS_EPOCH == AUDITOR_EXPECTED_EPOCH` and the corresponding exact run ID. They do not fall back to a unit-name epoch. The generated terminal witness config binds bucket, prefix, region, and `allow_s3_write=true`; the auditor validates the exact stable key and target. Upload exceptions yield a nonzero result. The final local receipt is byte-equal to the body sent to the stable S3 key under the successful-upload path.

## IAM and S3 boundary

The repository Terraform source declares the runtime role actions `s3:GetObject` and `s3:PutObject` on `${bucket ARN}/market-data/temporary/aws-validation-*/*`. The candidate prefix `market-data/temporary/<epoch>` is in that intended namespace. This is source configuration only; the live role attachment, permissions boundary, bucket policy, and effective runtime decision were not inspected with valid AWS credentials.

| Capability | Minimum relevant action | Current conclusion |
|---|---|---|
| Runtime PUT of terminal witness/receipt | `s3:PutObject` on the exact temporary epoch object namespace | Source policy declares scoped PUT; actual EC2 role permission is NOT VERIFIABLE |
| Runtime GET of required objects | `s3:GetObject` on the exact temporary epoch object namespace | Source policy declares scoped GET; actual EC2 role permission is NOT VERIFIABLE |
| Auditor GET of exact witness and receipt | `s3:GetObject` on those exact object ARNs | Historical exact read returned HTTP 403; current bootstrap profile is expired, so current capability is NOT VERIFIABLE |
| Auditor HEAD of exact object | `s3:GetObject` | AWS documents that `HeadObject` requires `s3:GetObject`; live capability is NOT VERIFIABLE ([AWS `HeadObject`](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html)) |
| `ListBucket` | Not used by the existing offline auditor | Not required by the audit design. Lack of List permission can make a missing-key `GetObject` return 403 instead of 404, so the historical 403 alone does not establish whether the object was missing or access was denied ([AWS `GetObject`](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html)) |

No IAM policy diff is proposed: the source already declares the narrow temporary-namespace GET/PUT actions, while the actual effective policies are unknown. No administrator grant, broad bucket access, Terraform apply, or blind permission edit was performed.

## Idempotency, trading safety, and scientific state

The finalizer fix from `48cfa0aa21327daa420638d275a3ca5314f6ad49` is included. The regression test retries the same logical frozen observation while a patched clock crosses a UTC second boundary and confirms stable `closed_at_utc`, evidence hash, and receipt checksums. Existing incremental-finalizer fault injection covers intent, entry, pending, and summary transaction boundaries. It does not prove actual guest process-crash behavior after every S3 receipt boundary; that remains unverified.

The default-live guard exits before acquiring the daemon lock unless `BITHUMB_LIVE_TRADING=true`; its regression test asserts the lock is not acquired. The macOS wrapper no longer sets live mode by default and falls back to simulation. No authenticated endpoint was contacted. `REAL_ORDER_POSSIBLE_BY_DEFAULT=NO` is a source/test conclusion, not a production runtime observation.

Scientific/trading status remains `ALPHA=UNPROVEN`, `DATASET_QUALIFIED=NO`, `RETROSPECTIVE=NOT_STARTED`, `CANDIDATE_FROZEN=NO`, `PAPER=NOT_STARTED`, `LIVE=DISABLED`, and `PRIVATE_API=DISABLED`. This mission did not begin alpha research.

## Runtime differential

`OLD_RUNTIME=22e06b9527798567e185fb0dd41dca3a448f444e`  
`NEXT_RUNTIME=054d43c3a43ef79919ac38bed4fa74d1b0689f53`  
`NEXT_RUNTIME_TREE=8c872ae7d3353c4d4e8649758266d2ec103dba63`

| Category | Changed files | Run impact |
|---|---|---|
| Runtime/finalization | `src/bithumb_coin_trader/closed_hour_finalizer.py`, `src/bithumb_coin_trader/bounded_supervisor.py`, `src/bithumb_coin_trader/launch_artifacts.py` | Stable closed-hour evidence on retry; strict finite systemd launch and sealed artifact binding |
| Launch | `scripts/generate_launch_artifacts.py`, `scripts/launch_short_smoke_transient.py`, `scripts/validate_launch_artifacts.py` | 120-second identity is restricted to a dedicated witness smoke; exact 30H launch artifact validation |
| Witness | `scripts/terminal_witness.py` | Exact identity and S3 target, explicit region, fail-closed upload, byte-stable stable receipt |
| Trading safety | `scripts/autonomous_trader.py`, `scripts/run_daemon_macos.sh` | Default live execution disabled before side effects |
| Evidence gates | `scripts/audit_fresh_30h_v3_terminal.py`, `scripts/final_30h_prelaunch_gate.py` | Exact terminal audit plus one-shot fail-closed local/evidence-bundle gate |
| Regression tests | `tests/test_autonomous_trader_safety.py`, `tests/test_closed_hour_finalizer.py`, `tests/test_final_30h_prelaunch_gate.py`, `tests/test_fresh_30h_v3_terminal_audit.py`, `tests/test_launch_artifacts.py`, `tests/test_observer_and_witness.py`, `tests/test_transient_launch.py`, `tests/test_witness_watchdog_fixes.py` | Covers changed contracts and safety rules |
| Unrelated runtime/research files | None | No research, strategy, dashboard, or BitMEX source changes |

This is reliability-only source scope. The offline auditor is included because it is a required terminal-contract gate; unrelated PR #21 research/bootstrap integration was not imported.

## Full tests and sealed artifacts

Verification ran on exact commit `054d43c3a43ef79919ac38bed4fa74d1b0689f53` and tree `8c872ae7d3353c4d4e8649758266d2ec103dba63`:

```text
FULL_SUITE = 1704 passed, 2 skipped (151.78s)
FOCUSED_RELIABILITY_SUITE = 199 passed (6.23s)
PYRIGHT_CHANGED_PYTHON_SCOPE = 0 errors, 0 warnings, 0 informations
COMPILEALL = PASS
GIT_DIFF_CHECK = PASS
WORKTREE = CLEAN AT TESTED COMMIT
PROTECTED test-results/.last-run.json SHA256 = e22df5d0991eb28c09093b1e678b3fa8cd1fab48185d38e67cf79fb6e63ad5ea (unchanged)
```

The candidate 30H and smoke artifacts were each generated twice under `/private/tmp/btc-final-30h-preflight-20260928-052441/{30h-a,30h-b,smoke-a,smoke-b}` and passed the production artifact validator. Runtime JSON, launch command, launch scripts, and authorization evidence matched byte-for-byte between regenerations. `identity.json` and `sealed-manifest.json` differ only in the documented seal timestamp and dependent identity hash; those fields were normalized before comparison.

Candidate 30H identity:

```text
RUN_ID = aws-validation-observability-30h-run-20260928T052441Z-v1
EPOCH = aws-validation-observability-30h-20260928-20260928T052441Z-v1
DURATION = 108000 seconds
S3_BUCKET = bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433
S3_PREFIX = market-data/temporary/aws-validation-observability-30h-20260928-20260928T052441Z-v1
S3_REGION = ap-northeast-2
PRIVATE_API = disabled; PAPER = not started; LIVE = disabled
AUTHORIZATION = PREPARED_NOT_AUTHORIZED
```

This is a local candidate, not a freshness claim. Remote run/epoch/prefix absence and local guest evidence-directory absence were not verified. Do not launch or reuse it without fresh identity checks; generate a new timestamped identity after the credential and readiness blockers are resolved.

Selected first-generation artifact SHA-256 values:

| Artifact | SHA-256 |
|---|---|
| Runtime JSON | `7159189b226fd39781f764868e3f85f7244563b4497dcbc6fc56f7ece3cea6e0` |
| `launch-command.json` | `73fcbd901af0d07240f97bbb253a155ab625b1ea0cc1ebf2e2d654a0f355b622` |
| `launch-ec2.sh` | `552ce1750dffda566046266a797fbc4f88cdcdbdc73a22cfd4ce6adb5d801d00` |
| `launch.sh` | `0fe1dd06779ed72bcbd558faeadaffa331c80e1188a6adea9e3837664645ef01` |
| `authorization-evidence.json` | `11742d14ce837479a44920bd41d5162f49e9b95bc917e467363b463ea1afb7c8` |

The prelaunch command is:

```bash
python scripts/final_30h_prelaunch_gate.py \
  --repo-root <clean-runtime-checkout> \
  --artifacts-dir <sealed-30h-artifact-directory> \
  --readiness-evidence <fresh-read-only-readiness-bundle.json>
```

It checks sealed artifacts/source identity, a separate exact-identity authorization artifact, the smoke result, and every required read-only readiness assertion. It does not call AWS itself; its evidence bundle must contain fresh, reviewable results for IAM/S3, guest capacity/process/unit state, identity absence, safety state, and auditor checks. Missing or `NOT_VERIFIABLE` values fail closed. The run here returned `PRELAUNCH_GATE=FAIL` for the unauthorized candidate and unavailable AWS/guest/smoke evidence.

## Required GO / NO-GO matrix

| Gate | Result | Evidence |
|---|---|---|
| PR #22 exact epoch binding | PASS | Source review plus exact systemd/witness/auditor regression coverage |
| Exact run-ID binding | PASS | Source review and launch/auditor tests bind sealed run ID end-to-end |
| Runtime witness upload | NOT VERIFIABLE | Upload failure/success behavior tested locally; real guest role and smoke PUT unavailable |
| Auditor exact read | NOT VERIFIABLE | Historical exact read was HTTP 403; current named profile expired |
| S3 hash parity | NOT VERIFIABLE | Mock PUT body equals local bytes; no remote object exists from this smoke |
| Malformed-witness regression | PASS | Historical generic epoch, null key, and false-upload fixture is rejected |
| Corrected witness accepted | PASS | Correct exact-key/bucket/prefix/region fixture is accepted |
| `closed_at_utc` retry idempotency | PASS | Same observation retried across UTC second boundary preserves time/hash/receipt checksums |
| Default-live safety | PASS | Main exits before lock on absent opt-in; wrapper default is simulation; tests pass |
| Full suite | PASS | 1704 passed, 2 skipped on exact proposed commit |
| Pyright | PASS | All changed Python files: 0 errors, 0 warnings, 0 informations |
| Launch artifact deterministic | PASS | Exact 30H and smoke artifacts regenerated twice; only documented timestamps/dependent hash differ |
| Disk/process readiness | NOT VERIFIABLE | No valid SSM/AWS session for read-only guest checks |
| Run identity fresh | NOT VERIFIABLE | Candidate remote RUN_ID/EPOCH/S3-prefix and guest path absence were not queried |
| Terminal auditor dry-run | NOT VERIFIABLE | Local predicate regressions pass; complete exact remote evidence bundle was unavailable |

```text
MANDATORY_GATES_PASS = 8 / 15
NEXT_30H_LAUNCH_READY = NO
NEXT_30H_LAUNCHED = NO
```

## Final console result

```text
============================================================
FINAL 30H GO / NO-GO
============================================================

HISTORICAL_RUN_VERDICT = FAIL
HISTORICAL_RUNTIME_COMPLETED = YES

FAILURE_CLASS =
TERMINAL_EVIDENCE_CONTRACT

ROOT_CAUSE =
1. The old ExecStopPost used generic unit prefix bitcoin-trader-30h as witness epoch instead of the sealed epoch.
2. It did not receive the exact S3 upload target/permission, yielding s3_key=null and s3_uploaded=false.

CORRECTIVE_PR =
Draft follow-up PR based on PR #22 (to be attached after push)
CORRECTIVE_HEAD =
054d43c3a43ef79919ac38bed4fa74d1b0689f53

WITNESS_E2E = NOT VERIFIABLE (not executed)
S3_UPLOAD = NOT VERIFIABLE (real runtime role unavailable)
S3_EXACT_READ = NOT VERIFIABLE (named profile expired; historical exact read was 403)
HASH_PARITY = NOT VERIFIABLE remotely; local exact-byte regression passes
AUDITOR_ACCEPTANCE = local malformed/corrected predicate tests pass; live bundle not run

CLOSED_AT_UTC_IDEMPOTENCY = PASS locally
DEFAULT_LIVE_SAFE = PASS by code and regression test

FULL_TESTS = 1704 passed, 2 skipped
NEW_REGRESSIONS = 199 focused tests passed

NEXT_RUNTIME_COMMIT = 054d43c3a43ef79919ac38bed4fa74d1b0689f53
NEXT_RUNTIME_TREE = 8c872ae7d3353c4d4e8649758266d2ec103dba63

NEXT_RUN_ID = aws-validation-observability-30h-run-20260928T052441Z-v1 (unverified candidate)
NEXT_EPOCH = aws-validation-observability-30h-20260928-20260928T052441Z-v1 (unverified candidate)

MANDATORY_GATES_PASS = 8 / 15
NEXT_30H_LAUNCH_READY = NO
NEXT_30H_LAUNCHED = NO

RESIDUAL_RISKS =
1. No real 120-second guest-to-ExecStopPost-to-S3-to-auditor smoke has passed.
2. Live EC2 role, bootstrap exact-read access, disk/process/unit state, and candidate identity freshness are unverified.
3. Exchange feed/network faults and uninstrumented guest crash points remain operational risks; no alpha or profitability claim follows.

COMMITS =
92d5a98, d6a6f31, ddb96e9, cd57ca5, 4f73b80, 7e9b890, d10bb26, d5c27ce, 054d43c
PRS =
PR #19 open Draft; PR #21 open Draft; PR #22 open Draft; follow-up Draft PR to be attached

USER_ACTION_REQUIRED =
Reauthenticate the named AWS profile locally; then run a newly timestamped short witness smoke and repeat read-only S3/guest/identity checks. Request a separate explicit GO only after every mandatory gate passes.
============================================================
```

The historical `test-results/.last-run.json` remains untouched. No historical PASS/FAIL evidence was rewritten, no alpha research started, and no run was launched.
