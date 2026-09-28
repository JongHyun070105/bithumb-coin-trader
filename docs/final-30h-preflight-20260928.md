============================================================
FINAL 30H PREFLIGHT
============================================================

PREVIOUS_30H = FAIL
PREVIOUS_RUNTIME_EXECUTION = FULL 30H COMPLETED
PREVIOUS_FAIL_CAUSE = TERMINAL WITNESS / S3 EVIDENCE CONTRACT

PR22_REVIEW = PARTIAL
WITNESS_BINDING_FIX = PRESENT IN CANDIDATE; E2E NOT VERIFIED

WITNESS_SMOKE_RUN_ID = NOT ISSUED
WITNESS_SMOKE_RESULT = NOT RUN
LOCAL_WITNESS = NOT CREATED
REMOTE_WITNESS = NOT CREATED
WITNESS_HASH_PARITY = NOT VERIFIABLE
AUDITOR_SMOKE_RESULT = NOT RUN

RUNTIME_S3_UPLOAD_PERMISSION = NOT VERIFIABLE
AUDITOR_S3_EXACT_READ_PERMISSION = LAST OBSERVED 403; CURRENT STATE NOT VERIFIABLE

IDEMPOTENCY_FIX = INCLUDED AND LOCALLY TESTED
DEFAULT_LIVE_SAFE = YES; DEFAULT INVOCATION EXITS BEFORE DAEMON STATE/LOCK

NEXT_RUNTIME_COMMIT = 054d43c3a43ef79919ac38bed4fa74d1b0689f53
NEXT_RUNTIME_TREE = 8c872ae7d3353c4d4e8649758266d2ec103dba63

FULL_TESTS = 1704 PASSED, 2 SKIPPED
FOCUSED_TESTS = 199 PASSED
PYRIGHT = 0 ERRORS, 0 WARNINGS, 0 INFORMATIONS
COMPILEALL = PASS
BASH_SYNTAX = PASS
DIFF_CHECK = PASS
DETERMINISTIC_ARTIFACT_TESTS = PASS

PRELAUNCH_GATE = FAIL
NEXT_RUN_ID = NOT ISSUED
NEXT_EPOCH = NOT ISSUED
NEXT_30H_LAUNCH_READY = NO
NEXT_30H_LAUNCHED = NO

ALPHA = UNPROVEN
DATASET_QUALIFIED = NO
RETROSPECTIVE = NOT_STARTED
CANDIDATE_FROZEN = NO
PAPER = NOT_STARTED
LIVE = DISABLED
PRIVATE_API = DISABLED
============================================================

## Decision

NO-GO for a new 30H run. The short terminal-witness smoke was not started because the configured bitcoin-trader-bootstrap AWS session expired and returned: “Your session has expired. Please reauthenticate using 'aws login'.” Runtime-role permissions, auditor exact-object reads, a new identity's S3 prefix, guest capacity/process state, S3 upload/retrieval, byte parity, and live auditor acceptance therefore remain unproven. No long run or IAM mutation occurred.

The operator may reauthenticate locally with:

    aws login --profile bitcoin-trader-bootstrap

Do not send credentials. The already-authorized short smoke can proceed after read-only identity, permission, and freshness checks. A separate exact-identity human GO is still required before any 30H launch.

## Historical run and reconstructed failure chain

| Stage | Input | Expected | Actual previous run | Candidate behavior and test coverage |
|---|---|---|---|---|
| Sealed identity | Run ID aws-validation-observability-30h-run-20260926T135000Z-v3; epoch aws-validation-observability-30h-20260926-20260926T135000Z-v3 | Exact run and epoch carried through terminal evidence | Identity contained the exact values | Launch artifacts bind terminal witness epoch and run ID to the ValidationRunSpec. Artifact and transient-launch tests pass. |
| Launch CLI | Sealed epoch, run ID, bucket, prefix, region, upload enabled | All exact values passed to transient launcher | Launcher did not pass the sealed epoch or S3 target/upload flag | Candidate CLI threads epoch, bucket, prefix, region, and upload flag into TransientLaunchConfig. Tests cover binding and missing values. |
| systemd ExecStopPost | Generated command from transient launcher | Exact witness arguments, no generic fallback | Witness received epoch bitcoin-trader-30h and exact run ID only | Renderer uses the explicit sealed epoch and exact run ID, rejects missing S3 target/region/permission, and constrains path/identifier syntax. Tests cover long IDs, slashes, and rejected shell-sensitive input. |
| terminal_witness.py | systemd environment plus explicit arguments | Local receipt bound to identity; upload required to succeed | Witness stored the generic epoch, s3_key=null, s3_uploaded=false | Candidate requires epoch, run ID, bucket, prefix, region, and upload enablement; it returns nonzero when upload fails. Focused witness tests pass. |
| S3 | Witness payload and runtime EC2 role | Stable exact object written under the sealed prefix | Historical witness reported no upload attempt | Terraform source intends s3:GetObject and s3:PutObject on the temporary validation namespace. No live role simulation or smoke PutObject was possible in this turn. |
| Terminal auditor | Sealed identity and exact S3 witness | Matching run/epoch/key and successful upload | Official verdict FAIL | Offline auditor rejects the historical malformed fixture and accepts the corrected fixture. Acceptance against a real smoke object was not run. |

The historical adjudication on PR #21 records 108017.072 / 108000 seconds, 29/29 cohorts, 2204/2204 passing slots, zero missing/failed slots, zero restarts, and completed finalization. The auditor still returned FAIL because the witness epoch was bitcoin-trader-30h and the witness had no S3 key or successful upload. The receipt mirror was unavailable and historical exact-object reads returned HTTP 403. The report is docs/post-30h-terminal-adjudication-20260928.md on PR #21's head.

## PR #22 adversarial review

PR #22 is still an open draft at remote head 4944235630cff58573d7348d5b949c546dd11ea8, based on 48cfa0aa21327daa420638d275a3ca5314f6ad49. Its diff binds the terminal witness to explicit epoch, run ID, bucket, prefix, and upload permission; no generic epoch fallback remains in that path. The current local candidate adds follow-up fixes required by the full contract:

- 92d5a98 adds explicit S3 region binding, rejects missing upload inputs, and returns failure when upload did not succeed.
- 7e9b890 allows the 120-second smoke through the sealed artifact CLI and tests deterministic generation.
- d10bb26 adds the fail-closed prelaunch gate.
- d5c27ce rejects missing duration before deadline arithmetic.
- 054d43c adjusts tests for clean changed-scope Pyright.

The PR #22 head alone does not contain the region argument added by 92d5a98, nor the 120-second artifact CLI support added by 7e9b890. The follow-up candidate contains these corrections and passes local tests. It has not been pushed or merged.

The malformed historical-witness regression is test_fresh_30h_v3_terminal_audit.py::test_historical_malformed_witness_is_rejected_and_corrected_contract_passes. Its BEFORE fixture uses epoch bitcoin-trader-30h, null s3_key, and s3_uploaded=false and is rejected. Its AFTER fixture is accepted by the offline auditor. This is fixture evidence, not an S3 E2E smoke result.

## IAM and exact-read findings

Runtime EC2 role and auditor/bootstrap identity are separate principals.

Terraform source in infra/aws/main.tf grants the collector role GetObject and PutObject on the configured temporary archive namespace. The local Terraform state file is absent, and the expired AWS profile prevented checking the live role, permission boundary, bucket policy, or current auditor identity. Runtime upload permission is therefore NOT VERIFIABLE.

The latest repository adjudication records an exact-object HTTP 403 for the bootstrap auditor profile. The current profile could not be rechecked because its session expired. A 403 alone does not identify the cause: for a missing object, HeadObject may return 403 when the caller lacks ListBucket. AWS documents that HeadObject requires s3:GetObject; ListObjectsV2 requires s3:ListBucket. The exact-read audit does not need ListBucket when the object key is known. No live exact GetObject/HeadObject was possible here. See [AWS HeadObject permissions](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html) and [AWS S3 API permission mapping](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-with-s3-policy-actions.html).

No IAM policy was applied or prepared with an invented bucket/run ARN. If a current exact-object read is denied after confirming that the object exists, the narrow candidate action is s3:GetObject on the exact witness/receipt object ARN(s); HeadObject uses the same action. Do not add ListBucket for exact object reads. The authoritative live bucket and fresh identity must be confirmed before writing any exact Resource ARN.

## Idempotency and default-live safety

Commit 48cfa0aa21327daa420638d275a3ca5314f6ad49 is an ancestor of the candidate. The restart-idempotency test crosses a UTC-second boundary and verifies stable closed_at_utc, evidence SHA-256, and receipt checksums for the same frozen observation. Scheduler and archive tests cover retry/restart behavior. These local tests pass; no AWS runtime was started.

Commits d6a6f31 and ddb96e9 are included. autonomous_trader.main checks BITHUMB_LIVE_TRADING before acquiring the daemon lock or loading/saving daemon state. The default-invocation test verifies exit before the lock, and the macOS wrapper test verifies it defaults to false. bash -n passes. REAL_ORDER_POSSIBLE_BY_DEFAULT = NO.

## Candidate source differential

OLD_RUNTIME = 22e06b9527798567e185fb0dd41dca3a448f444e
NEXT_RUNTIME = 054d43c3a43ef79919ac38bed4fa74d1b0689f53
NEXT_RUNTIME_TREE = 8c872ae7d3353c4d4e8649758266d2ec103dba63

Changed files from OLD_RUNTIME: 19 total. Runtime/safety files are scripts/autonomous_trader.py, scripts/run_daemon_macos.sh, scripts/terminal_witness.py, scripts/launch_short_smoke_transient.py, src/bithumb_coin_trader/bounded_supervisor.py, src/bithumb_coin_trader/closed_hour_finalizer.py, and src/bithumb_coin_trader/launch_artifacts.py. Launch/auditor tooling adds scripts/generate_launch_artifacts.py, scripts/validate_launch_artifacts.py, scripts/audit_fresh_30h_v3_terminal.py, and scripts/final_30h_prelaunch_gate.py. The remaining changed paths are regression tests for those behaviors. No strategy, research, dashboard, or external-dataset source file is in this differential.

The actual 30H sealed artifacts were not generated because no fresh identity could be proven against AWS/local evidence. Unit tests generate two temporary artifact sets and verify deterministic runtime/launch artifacts while excluding documented seal timestamps.

## Verification evidence

- Focused exact-candidate suite: 199 passed.
- Full exact-candidate suite: 1704 passed, 2 skipped.
- Pyright across changed Python source and test paths: 0 errors, 0 warnings, 0 informations.
- compileall: PASS.
- bash -n scripts/run_daemon_macos.sh: PASS.
- git diff --check from OLD_RUNTIME to NEXT_RUNTIME: PASS.
- Artifact generator/validator CLI smoke test with 120 seconds and deterministic 30H artifact unit test: PASS.
- Historical malformed-witness rejection and corrected-fixture acceptance: PASS.
- scripts/final_30h_prelaunch_gate.py with a missing-artifact input: PRELAUNCH_GATE=FAIL, exit 1. This only verifies the missing-input fail-closed path; there is no actual next-run identity/readiness bundle to evaluate.
- Protected test-results/.last-run.json SHA-256 remained e22df5d0991eb28c09093b1e678b3fa8cd1fab48185d38e67cf79fb6e63ad5ea.
- No runtime, IAM, S3, Terraform, exchange, paper/live, or 30H launch mutation occurred.

The gate currently consumes a readiness JSON bundle. It checks for PASS statuses and non-empty evidence_ref strings but does not read/hash those referenced evidence objects. It also checks only smoke identity prefixes and self-reported hash fields, not a sealed smoke artifact binding. Treat its PASS as dependent on trusted, separately verified evidence collection. It requires launch authorization to pass, so it does not produce a technical-ready/pre-GO result.

## GO / NO-GO matrix

| Gate | Result | Evidence |
|---|---|---|
| PR #22 exact epoch binding | PASS | Explicit sealed epoch flows to ExecStopPost; no generic epoch fallback. |
| Exact run-ID binding | PASS | Launch artifacts and witness arguments are identity-bound; regression tests pass. |
| Runtime witness upload | NOT VERIFIABLE | Terraform source grants intended actions; real runtime PutObject smoke not run. |
| Auditor exact read | NO PASS — last observed 403; current state unknown | AWS session expired; no current GetObject/HeadObject evidence. |
| S3 hash parity | NOT VERIFIABLE | No real smoke object/local witness pair exists. |
| Malformed-witness regression | PASS | Historical generic-epoch/null-key/false-upload fixture rejected. |
| Corrected witness accepted | PASS | Offline corrected-contract fixture accepted. |
| closed_at_utc retry idempotency | PASS | Boundary-clock retry has stable timestamp/hash/checksum. |
| Default-live safety | PASS | Main guard and wrapper default-off tests pass. |
| Full suite | PASS | 1704 passed, 2 skipped at exact candidate SHA. |
| Pyright | PASS | Changed source and test scope: zero errors/warnings/informations. |
| Launch artifact determinism | PASS | Generator tests compare repeated artifacts excluding timestamp/nonce fields. |
| Disk/process readiness | NOT VERIFIABLE | No fresh AWS/SSM session for guest inspection. |
| Fresh run identity | NOT VERIFIABLE | No 30H identity issued; remote/local uniqueness not checked. |
| Terminal auditor dry-run | NOT VERIFIABLE | Offline fixture suite passed; no actual smoke bundle/object to audit. |

MANDATORY_GATES_PASS = 9 / 15
NEXT_30H_LAUNCH_READY = NO
NEXT_30H_LAUNCHED = NO

## Residual risks

1. Live runtime-role PutObject and auditor GetObject permissions, bucket policy, and permission boundary remain time-specific unknowns.
2. The complete systemd ExecStopPost → witness → S3 upload → exact retrieval → byte parity → auditor acceptance path has not run under a fresh smoke identity.
3. Guest disk/inode/memory/process/unit/mount readiness and S3-prefix freshness remain unverified.
4. The readiness-bundle gate relies on references that it does not verify and cannot pass before the exact authorization artifact is updated.

## Delivery state

COMMITS = 48cfa0a, 4944235, d6a6f31, 92d5a98, ddb96e9, cd57ca5, 4f73b80, 7e9b890, d10bb26, d5c27ce, 054d43c, 0775ccb (report)
PRS = #21 OPEN/DRAFT, #22 OPEN/DRAFT at the recorded remote heads above
LOCAL_PREFLIGHT_BRANCH = codex/final-30h-preflight-20260928; pushed to origin at 0775ccb. The tested runtime source remains 054d43c3a43ef79919ac38bed4fa74d1b0689f53; 0775ccb changes only this report.
USER_ACTION_REQUIRED = Reauthenticate bitcoin-trader-bootstrap locally. Then verify current exact-object access and collect fresh guest/AWS readiness evidence. Do not share credentials. Keep 30H stopped until the complete witness smoke passes and a separate exact-identity GO is provided.
============================================================

## Final console result

```text
============================================================
FINAL 30H GO / NO-GO
============================================================

HISTORICAL_RUN_VERDICT = FAIL
HISTORICAL_RUNTIME_COMPLETED = YES

FAILURE_CLASS = TERMINAL_EVIDENCE_CONTRACT
ROOT_CAUSE = ExecStopPost used generic epoch bitcoin-trader-30h and did not successfully upload the exact witness to S3.

CORRECTIVE_PR = #22 plus follow-up candidate on codex/final-30h-preflight-20260928
CORRECTIVE_HEAD = PR #22 4944235630cff58573d7348d5b949c546dd11ea8; follow-up candidate 054d43c3a43ef79919ac38bed4fa74d1b0689f53

WITNESS_E2E = NOT RUN
S3_UPLOAD = NOT VERIFIABLE
S3_EXACT_READ = NOT VERIFIABLE; last recorded exact read returned 403
HASH_PARITY = NOT VERIFIABLE
AUDITOR_ACCEPTANCE = offline malformed/corrected fixtures PASS; real smoke NOT RUN

CLOSED_AT_UTC_IDEMPOTENCY = PASS LOCALLY
DEFAULT_LIVE_SAFE = PASS LOCALLY

FULL_TESTS = 1704 passed, 2 skipped
NEW_REGRESSIONS = 199 passed

NEXT_RUNTIME_COMMIT = 054d43c3a43ef79919ac38bed4fa74d1b0689f53
NEXT_RUNTIME_TREE = 8c872ae7d3353c4d4e8649758266d2ec103dba63

NEXT_RUN_ID = NOT ISSUED
NEXT_EPOCH = NOT ISSUED

MANDATORY_GATES_PASS = 9 / 15
NEXT_30H_LAUNCH_READY = NO
NEXT_30H_LAUNCHED = NO

RESIDUAL_RISKS = Live IAM/S3 permissions, real smoke, exact-read/parity, guest readiness, and fresh identity remain unverified.
COMMITS = 48cfa0a, 4944235, d6a6f31, 92d5a98, ddb96e9, cd57ca5, 4f73b80, 7e9b890, d10bb26, d5c27ce, 054d43c, 0775ccb
PRS = #21 OPEN/DRAFT; #22 OPEN/DRAFT; preflight branch pushed, no PR created

USER_ACTION_REQUIRED = Reauthenticate bitcoin-trader-bootstrap locally; then complete the fresh short witness smoke and request separate exact-identity GO before 30H.
============================================================
```
