============================================================
OVERNIGHT PRE-30H + EXPLORATORY RESEARCH REPORT
============================================================

CAPACITY
--------
DISK_FREE_BEFORE = 93,587,300,352 bytes immediately before verified raw-source removal
DISK_FREE_AFTER = 164,606,906,368 bytes immediately after removal
DISK_FREE_CURRENT = 164,599,357,440 bytes at 2026-09-28T13:29:22Z
NET_RECLAIM = 71,019,606,016 bytes (about 66.16 GiB)
ARCHIVES_CREATED = 1; `/var/lib/bitcoin-trader/72h-soak/aws-72h-soak-20260905-8017b83e/raw.tar.zst`; 2,227,736,742 bytes; SHA-256 `6e754657952aaf613838062cd27a20ed0335e81a328f38df84b5fb15126e090c`
ORIGINAL_RAW_REMOVED = only the verified 5,530-file raw payload; historical controls/evidence and `raw/ARCHIVED.txt` pointer preserved
ARCHIVE_SOURCE = epoch `aws-72h-soak-20260905-8017b83e`, run `aws-72h-soak-run-20260905T024039Z-8017b83e`; historical result remains INTERRUPTED, no final manifest flush
RESOURCE_HEADROOM = PASS; conservative 30H growth 41,977,538,393 bytes; projected current free 122,621,819,047 bytes (114.20 GiB); 63,566,018,727 bytes (59.20 GiB) over the unchanged 55 GiB target
EBS = unchanged 300 GiB gp3

30H READINESS
-------------
FINAL_RUNTIME = `b4d482363e2f988dad9c6d29053f97e1e4160883`
FINAL_TREE = `5c96ed79fee107c1604ee7018621835910221fdf`
FINAL_RUN_ID = `aws-validation-observability-30h-run-20260928T125700Z-675937ab`
FINAL_EPOCH = `aws-validation-observability-30h-20260928-20260928T125700Z-675937ab`
CREATED_AT = 2026-09-28T13:01:57Z
WITNESS_E2E = PASS for separate 120-second smoke only; not a 30H run
S3_PATH = smoke receipt SHA parity PASS; new final epoch prefix query returned KeyCount=0 using the existing provisioner. Guest runtime role lacks ListBucket; no IAM change.
IDEMPOTENCY = PASS for modeled `closed_at_utc` retry/restart cases in exact-runtime evidence
DEFAULT_LIVE_SAFE = PASS; LIVE/PAPER/private API remain disabled
GUEST_PROCESS_READY = PASS for fresh identity collision check; no matching process; historical observer PID 1652530 preserved
GUEST_UNIT_READY = PASS for fresh identity collision check; new unit not-found/inactive/dead
ARTIFACT_DETERMINISM = PASS; independent generation preserved identity/runtime/command semantics and deterministic payload hashes
READINESS_WINDOW = PASS; monitored 2026-09-28T13:10:27Z through 13:29:22Z without collector start or material disk/memory/observer/AWS/SSM drift
READINESS_BUNDLE = NOT_VERIFIABLE; one required terminal-auditor dry-run check lacks a separately captured execution record; the 120-second live smoke and malformed-witness tests are not substituted for it
PRELAUNCH_GATE = FAIL; one required check is NOT_VERIFIABLE; follow-up evidence below
NEXT_30H_LAUNCH_READY = NO
NEXT_30H_LAUNCHED = NO

REPOSITORY HYGIENE
------------------
BRANCHES_INSPECTED = 67 local; 20 remote topic branches plus origin/HEAD
WORKTREES_INSPECTED = 26 total; 9 detached
WORKTREES_REMOVED = 0; evidence-bearing, open-PR, dirty, detached, or context-uncertain worktrees retained
REMOTE_BRANCHES_DELETED = 0; no high-confidence deletion candidate
PRS_UPDATED = 1 branch head updated: existing draft PR #23; PR body unchanged
PRS_CLOSED = 0
CLEANUP_CANDIDATES_REMAINING = local stale branches/worktrees remain for manual evidence/context review; no safe automatic cleanup identified
BRANCH_MAP = `docs/repository-branch-map-20260928.md`

EXPLORATORY RESEARCH
--------------------
DATASETS_INSPECTED = 96 local CSV filenames/sizes only; 4 registry entries as metadata; 1 KRW-BTC daily development prefix profiled
DATA_QUALIFICATION = NO
RESEARCH_ROLE = EXPLORATORY_ONLY
STRATEGIES_EXERCISED = Core70 V6 EMA Pullback + Sat30; Core70 V6 Fast Donchian + Sat30
EXPERIMENTS_RUN = 130 backtest evaluations: 10 strategy/cost; 15 baseline/cost; 100 fixed-seed placebos; 5 EMA chronological fold diagnostics
BASELINES_RUN = cash, buy-and-hold, daily SMA50/200 (each under 5 cost cases)
COST_SCENARIOS = 5 configured fee/slippage cases; no invented queue/latency precision
ENGINE_BUGS_FOUND = 2 provenance/registry defects (source confidence overwrite and blank explicit digest fallback); no PnL-accounting bug confirmed
ENGINE_BUGS_FIXED = 2 with regression coverage; atomic no-clobber experiment manifests and dataset build-manifest binding added
INTERESTING_HYPOTHESES = none promotion-ready; historical EMA composite claim reproduced on one unqualified development slice, while buy-and-hold had higher return and drawdown; fold results were mixed
REJECTED_HYPOTHESES = no stable positive fold outcome; no strategy winner selected
UNSUPPORTED_FAMILIES = order-book/maker queue, trade-stream microstructure, multi-asset execution, measured latency, queue position
PROSPECTIVE_HOLDOUT_CONSUMED = NO; only the first 2,220 CSV rows were read; sealed tail rows read = 0
RESEARCH_REPORT = `docs/exploratory-unqualified-research-20260928.md`

SCIENTIFIC STATE
----------------
ALPHA = UNPROVEN
CANDIDATE_FROZEN = NO
PAPER = NOT_STARTED
LIVE = DISABLED
PRIVATE_API = DISABLED

GIT
---
COMMITS_CREATED = research `a5c78c2d` plus overnight report/map/artifact commits on `codex/final-30h-preflight-20260928`
COMMITS_PUSHED = research branch `a5c78c2d`; final-preflight documentation/artifacts pushed to existing PR #23
PRS_CREATED_OR_UPDATED = #23 branch head updated; none created; PR body not edited
BRANCHES = `research/fix-asof-alignment-20260928`, `codex/final-30h-preflight-20260928`
PROTECTED_TEST_RESULTS = SHA-256 unchanged: `e22df5d0991eb28c09093b1e678b3fa8cd1fab48185d38e67cf79fb6e63ad5ea`

PRIMARY ADVERSARIAL REVIEW
-------------------------
- Archive source deletion followed complete file-hash recheck, archive SHA/integrity/list/compare/sample checks; manifest and `raw/ARCHIVED.txt` pointer preserve the historical source/run identity. Historical status remains INTERRUPTED.
- No active writer or new collector was observed. The only process is preserved historical observer PID 1652530 on the separate Sep-26 failed-run identity; the two exited helpers remain non-blocking.
- Disk, inode, memory, EC2, SSM, exact-ID process/unit/path, and S3-prefix evidence were refreshed after the 15-minute window. Final runtime SHA/tree still match the sealed identity.
- The 120-second witness smoke remains separate from the new identity and is not 30H qualification. New identity launch authorization is false and actual start time is null.
- Strategy signals use prior-close targets executed at the next daily open in `RebalanceBacktester`; reviewed V4/V6 signals are causal rolling/stateful calculations. The holdout tail was not read. This bounded review is not a proof of every research path.
- No branch/worktree deletion was performed; all PR heads and their base dependencies remain reachable.
- Independent read-only research-methodology reviewer timed out at 240 seconds; result is NOT_VERIFIABLE and is not treated as approval.

TRUE_BLOCKERS
1. Required terminal-auditor dry-run evidence is NOT VERIFIABLE; the fail-closed 36-check prelaunch gate therefore must not be treated as PASS.
2. Historical Fresh 30H terminal-contract result remains immutable FAIL; only a separate exact-identity human GO can authorize a new run.
3. Independent methodology reviewer timed out after 240 seconds; independent review is NOT VERIFIABLE.

NEXT HUMAN DECISIONS
1. Review the exact final run ID/epoch and decide separately whether to issue a 30H GO; no launch occurred.
2. Resolve/capture the required terminal-auditor dry-run check before claiming full readiness.
3. Review research PR and methodology when convenient; no alpha/candidate conclusion is made.
============================================================

PRELAUNCH GATE FOLLOW-UP
------------------------
At 2026-09-28T13:44:12Z, ran the actual gate on the committed clean final-preflight checkout and exact final identity:

```text
python3 scripts/final_30h_prelaunch_gate.py \
  --repo-root /Users/macintosh/.codex/worktrees/final-30h-preflight/bitcoin-trader \
  --artifacts-dir reliability-artifacts/aws-30h-final-preflight-20260928/aws-validation-observability-30h-20260928-20260928T125700Z-675937ab \
  --readiness-evidence reliability-artifacts/aws-30h-final-preflight-20260928/aws-validation-observability-30h-20260928-20260928T125700Z-675937ab/readiness-bundle/readiness-evidence.json

PRELAUNCH_GATE=FAIL
- readiness check is not PASS: terminal_auditor_dry_run
exit_status=1
```

All other required checks and evidence references passed gate evaluation. The separate auditor dry-run execution has no captured evidence; the live 120-second smoke and offline malformed-witness regression tests do not replace that required check. The bundle is therefore NOT_VERIFIABLE and `NEXT_30H_LAUNCH_READY=NO`. Full gate output and the exact captured evidence bundle are committed under the final identity's `readiness-bundle/` directory. No launch or authorization state changed.

AUDITOR DRY-RUN FOLLOW-UP
-------------------------
At 2026-09-28T13:47:25Z, ran `audit_fresh_30h_v3_terminal.py` against a deliberately empty temporary evidence directory, with no AWS/S3/runtime/exchange access. It returned `OVERALL_STATUS=NOT_VERIFIABLE`, exit status 2, because `audit-bundle.json` was absent. This confirms fail-closed behavior for missing input only. It does not satisfy `terminal_auditor_dry_run=PASS`; no valid offline audit export was available to run, and the 30H verdict remains untouched. Output files are preserved under the final identity's `readiness-bundle/auditor-dry-run-empty-input/` directory.

AUDITOR DRY-RUN RESOLUTION — MAIN (Sonnet 5) CONTINUATION, 2026-09-28T14:00:43Z
--------------------------------------------------------------------------------
Ground-truth re-verification at session start (new orchestrator turn) confirmed: `main` HEAD unchanged, PR #23 open/draft at `e00b2409` (matches this worktree's prior HEAD, genuinely pushed), EC2 instance `i-008bc503c1136349f` online and SSM-reachable, S3 bucket `bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433` shows the preserved 2026-09-26 observer actively writing minute cadence objects through this session (consistent with "historical observer non-blocking, preserved"), and the protected `test-results/` SHA-256 unchanged (see below). No destructive action, AWS mutation, PAPER/LIVE/private-API action, or 30H launch was taken.

The empty-directory dry run above only exercised the auditor's missing-input path. `scripts/audit_fresh_30h_v3_terminal.py` is offline-only by design (no AWS/exchange access; see its module docstring), and its own test suite (`tests/test_fresh_30h_v3_terminal_audit.py::_make_bundle` / `test_terminal_audit_passes_synthetic_bundle_without_writing_to_evidence`) already defines a trusted, clearly non-production synthetic bundle (`EPOCH=fresh-30h-v3-test`, `RUN_ID=fresh-30h-v3-run-test`) intended to exercise exactly this check. Running the real CLI (not just pytest) against that synthetic bundle:

```text
python3 scripts/audit_fresh_30h_v3_terminal.py \
  --evidence-dir /tmp/dry-run-check/bundle \
  --output-dir /tmp/dry-run-check/output

OVERALL_STATUS=PASS
exit_status=0
```

produced a genuine, offline, non-mutating execution record (20 checks, `overall_status=PASS`), preserved under the final identity's `readiness-bundle/auditor-dry-run-synthetic-bundle/` directory (`terminal-audit.json` SHA-256 `8310530ef53dc607eb00babace6b57c36ab611279cbe29ff1589297c0ed74f17`). This resolves what a valid `terminal_auditor_dry_run` execution looks like.

This does **not** flip `PRELAUNCH_GATE` to PASS. `scripts/final_30h_prelaunch_gate.py` requires every one of the 36 `REQUIRED_CHECKS` to share a single `captured_at_utc` inside a 15-minute freshness window (`_check_readiness`), and most of the remaining checks (`collector_identity_conflict_clear`, guest process/unit checks, `disk_capacity`, `runtime_commit_on_guest`, `runtime_full_suite`, etc.) require live queries against the EC2 guest. This session's AWS credentials (profile `bitcoin-trader-provisioner`, role `bitcoin-trader-terraform-provisioner/codex-preapply-validation`) returned `AccessDeniedException` on `ssm:SendCommand` and `ssm:GetCommandInvocation` against `i-008bc503c1136349f`. No other profile was tried and no IAM change was made — broadening this role's permissions is an infrastructure/security-adjacent change outside this session's authorization.

NEW_BLOCKER: a full, fresh `readiness-evidence.json` (and therefore an actual `PRELAUNCH_GATE=PASS` attempt) requires either (a) a human operator with working guest command execution access to personally run the 15-minute fresh collection including this synthetic-bundle dry-run pattern, or (b) an explicit, human-authorized grant of scoped read-only `ssm:SendCommand`/`ssm:GetCommandInvocation` to a role usable by future sessions. Neither happened this session. `PRELAUNCH_GATE` remains `FAIL`, `READINESS_BUNDLE` remains `NOT_VERIFIABLE`, and `NEXT_30H_LAUNCH_READY` remains `NO`.


POST-UPDATE RECHECK — 2026-09-29
================================
This addendum records the current post-update checks. Earlier statements above remain historical snapshots; this section supersedes them only where it explicitly reports a later observation. No 30H run was launched.

ORCHESTRATOR
------------
ORCHESTRATOR_VERSION = 7.0.2 (installed 2026-09-28T23:10:12+09:00; phase1_supervisor=6.2.0; phase3_orchestrator=6.2.0)
ORCHESTRATOR_HEALTH = HEALTHY; all 9 provider states reported healthy, no unhealthy/cooldown provider state
QUEUE_HEALTH = EMPTY / HEALTHY
DELEGATE_HEALTH = HEALTHY; 19 historical records, 0 RUNNING/QUEUED/STARTING
AVAILABLE_MODELS = 9 reported healthy: Claude Opus, Claude Sonnet, Command Code, Codex, Gemini high/medium/low, Opus, Sonnet
RUNNING_WORKERS = 0; no matching orchestrator-owned worker process found; no process was stopped

GIT AND PR STATE
----------------
ROOT_MAIN = `main` at `0b819eaa7af39b9a30e74ddadef67e8a677fc6be`; `origin/main` is `39e76ce0befab26e9b1f8cb5b83805ef1ee152ad`; root checkout is 6 commits behind and has five preserved untracked entries (`.agents/`, `.codex/`, `.commandcode/`, `AGENTS.md`, `test-results/`).
RELIABILITY_BRANCH = `codex/final-30h-preflight-20260928`
RELIABILITY_HEAD = `9bff7b09309d90b3842348151584b943181d642f`
RELIABILITY_PUSHED = YES; live origin head matched at inspection
PR23 = OPEN, DRAFT; head matched `9bff7b09309d90b3842348151584b943181d642f`; base remains PR #22 at `4944235630cff58573d7348d5b949c546dd11ea8`; 22 unique commits relative to that base. PR body unchanged; no merge.
RESEARCH_BRANCH = `research/fix-asof-alignment-20260928`
RESEARCH_HEAD = `a5c78c2d9c3c4e72760342e55430fbecfb49fbf9`, clean and matching origin; no PR found.
RESEARCH_TESTS = 119 passed in 0.29s across `tests/research_infra/test_dataset_manifest.py`, `tests/research_infra/test_research_infra.py`, and `tests/test_unqualified_research_runner.py`; no holdout or AWS/external data used. `git diff --check HEAD^ HEAD` passed.
WORKTREE_MAP = refreshed copy at `docs/repository-branch-map-20260929.md`; original 2026-09-28 snapshot retained. No branch/worktree deletion or merge performed.

AWS AUTHENTICATION AND ACCESS
-----------------------------
The two harmless, explicitly profiled STS calls at approximately 2026-09-29 06:36 KST each returned `Your session has expired. Please reauthenticate using 'aws login'.` No new AWS inspection command was issued after that result; an attempt to close the old SSM stream returned a broken pipe.
AWS_BOOTSTRAP_PROFILE = `bitcoin-trader-bootstrap`; auth method is AWS CLI login-session cache; AUTH_STATUS = EXPIRED
AWS_PROVISIONER_PROFILE = `bitcoin-trader-provisioner`; role chaining from bootstrap via `bitcoin-trader-terraform-provisioner`; AUTH_STATUS = EXPIRED
AWS_LOGIN_CACHE = PRESENT at persistent `~/.aws/login/cache`; cache contents were not read or exposed
AWS_STATIC_PROFILE_CREDENTIAL_FIELDS = absent; no credential or profile mutation
AWS_LOGIN_REAUTH_REQUIRED = YES, bootstrap profile only: `aws login --profile bitcoin-trader-bootstrap`
AUTHORIZATION_DENIALS = GetCommandInvocation was explicitly AccessDenied when authentication was valid earlier; SendCommand simulation was inconclusive (`implicitDeny` with required context missing). These are not authentication-expiry signals.
GUEST_ACCESS_PATH = Interactive SSM StartSession succeeded earlier for `i-008bc503c1136349f`; the session later closed with a broken pipe. Current availability is NOT VERIFIABLE until login is restored.
STARTSESSION_PERMISSION = PASS at last valid-session check; current permission not rechecked
SENDCOMMAND_PERMISSION = NOT VERIFIABLE; no need established because interactive StartSession worked and the repository's `scripts/ssm_exec.py` uses that route
GETCOMMANDINVOCATION_PERMISSION = DENIED at last valid-session check
IAM_CHANGE_REQUIRED = NO; do not broaden IAM and do not use another profile to route around the intended role

AWS CLI login sessions use cached temporary credentials and refresh while the full login session is valid; AWS documents the default persistent cache as `~/.aws/login/cache` and requires `aws login` after full-session expiry ([AWS CLI sign-in](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html)). No static IAM credentials were created or recommended. The EC2 validation design attaches an instance profile/runtime role; the guest's earlier explicit STS identity was `assumed-role/bitcoin-trader-aws-apne2-research-collector/i-008bc503c1136349f`. Therefore expiry of the local control-plane login by itself does not expire the already-running guest role ([AWS CLI credential provider chain](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-authentication.html)). No 30H runtime was launched.

PROTECTED EVIDENCE
------------------
`test-results/.last-run.json` SHA-256 = `e22df5d0991eb28c09093b1e678b3fa8cd1fab48185d38e67cf79fb6e63ad5ea` (verified unchanged). File and attached immutable snapshot were not modified.

RUNTIME, IDENTITY, AND CAPACITY
-------------------------------
FINAL_RUNTIME = `b4d482363e2f988dad9c6d29053f97e1e4160883`
FINAL_TREE = `5c96ed79fee107c1604ee7018621835910221fdf`
The guest's shared base runtime path matched these values at the earlier valid-session inspection. Durable committed evidence supports the exact-runtime full-suite PASS (1,704 passed, 2 skipped, 181 subtests), witness E2E PASS, runtime S3 upload and exact-object read PASS, hash parity PASS, terminal auditor synthetic dry-run PASS, closed_at_utc idempotency PASS, and default-live safety PASS. This is reliability evidence only, not 30H completion, trading alpha, or production qualification.

FINAL_RUN_ID = `aws-validation-observability-30h-run-20260928T125700Z-675937ab` — PROPOSED_ONLY
FINAL_EPOCH = `aws-validation-observability-30h-20260928-20260928T125700Z-675937ab` — PROPOSED_ONLY
The launch artifact code has no maximum age rule for this identity; its readiness capture must be within 900 seconds. The earlier guest check showed launch authorization false, actual start time null, candidate-specific paths absent, and no collision under the candidate prefix. Those identity/collision facts are now stale and must be rechecked after login. Do not mint a replacement or reuse any identity if it has been consumed.

Last fresh capacity sample (now stale; collected before current login expiry): filesystem total 322,042,834,944 B, used 157,444,415,488 B, free 164,598,419,456 B; inode total 157,280,240, used 200,839, free 157,079,401; MemTotal 3,926,536 kB, MemAvailable 3,407,256 kB, swap 0. The same conservative model remains 1,217,418,119 B/hour raw peak × 30 plus 5,454,994,823 B non-raw = 41,977,538,393 B expected growth. That sample projected 122,620,881,063 B free (about 114.2 GiB), approximately 59.2 GiB over the 55 GiB target. It was a PASS at its capture time, but is not current evidence.
RESOURCE_HEADROOM = NOT VERIFIABLE now; current filesystem, inode, and memory readings are required in the fresh window.

READINESS GATE AND DECISION
---------------------------
READINESS_WINDOW = NOT STARTED; both profiles are expired, so no coherent 15-minute evidence capture was attempted.
The last stored official gate run at 2026-09-28T13:44:12Z was FAIL: 35/36 checks passed and `terminal_auditor_dry_run` was NOT_VERIFIABLE. The valid offline synthetic audit was separately run at 2026-09-28T14:00:43Z and returned PASS with 20 checks; committed `terminal-audit.json` SHA-256 is `8310530ef53dc607eb00babace6b57c36ab611279cbe29ff1589297c0ed74f17`.
AUDITOR_DRY_RUN = PASS (synthetic offline bundle only)
REAL_30H_TERMINAL_AUDIT = NOT RUN
CURRENT_GATE_EVIDENCE = stale; 0 current checks accepted as fresh
CHECKS_PASS = 0 current/fresh; last stored snapshot had 35 PASS
CHECKS_TOTAL = 36
CHECKS_FAIL = 0 current/fresh; last stored gate result was FAIL
CHECKS_NOT_VERIFIABLE = 36 current/fresh due to no current 15-minute bundle
PRELAUNCH_GATE = NOT VERIFIABLE currently; last recorded gate result remains FAIL
NEXT_30H_LAUNCH_READY = NO
NEXT_30H_LAUNCHED = NO

After the user restores the named bootstrap login, recheck both named identities and obtain current guest access. If StartSession remains available, collect the repository-controlled guest and AWS evidence through the approved interactive path and run all freshness-sensitive observations plus the authoritative 36-check gate in one 15-minute window. Do not treat a partial bundle as PASS. A separate exact-identity human GO remains required even if a later gate passes.

SCIENTIFIC STATE
----------------
DATASET_QUALIFIED = NO
RESEARCH_ROLE = EXPLORATORY_ONLY
PROMOTION_ELIGIBLE = NO
PROSPECTIVE_HOLDOUT_CONSUMED = NO
ALPHA = UNPROVEN
CANDIDATE_FROZEN = NO
PAPER = NOT_STARTED
LIVE = DISABLED
PRIVATE_API = DISABLED

TRUE_BLOCKERS
-------------
1. Both AWS named profiles report EXPIRED; bootstrap login must be restored before any current AWS/guest verification.
2. Capacity, candidate-prefix collision state, guest process/unit state, and all 36 freshness-sensitive checks are stale or unavailable until one coherent 15-minute collection is run.
3. The last official 36-check result remains FAIL; current PRELAUNCH_GATE is NOT VERIFIABLE, and readiness is NO. No launch is authorized by this session.

USER_ACTION_REQUIRED = Run `aws login --profile bitcoin-trader-bootstrap` in the local authenticated AWS CLI environment, then resume the read-only recheck. Do not provide or configure static keys.
