# AWS 30H V3 Official Validation Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute authoritative preparation for the new official AWS 30H V3 validation run, establishing complete cryptographic seals, timing/coverage contracts, preflight verification gates, and launch artifacts up to the exact pre-launch authorization boundary (`READY TO AUTHORIZE NEW 30H: YES`, `LAUNCH AUTHORIZED: FALSE`).

**Architecture:** Derive canonical 76-feed universe, timing schedule (strictly-next boundary, 30 candidate cohorts = 2,280 slots, 111,600s window), monotonic deadline logic, and heartbeat parameters. Seal configuration artifacts into `infra/aws/seals/` for a completely unique V3 identity. Validate EC2 hardware/storage, IAM runtime boundaries, and Terraform drift (0/0/0) via read-only inspection. Complete whole-branch review, PR merge to main, post-merge runtime seal, and stop before launch.

**Tech Stack:** Python 3.14 / 3.11, AWS EC2 / SSM (`i-008bc503c1136349f`), AWS S3, IAM, Terraform, systemd transient unit, Pyright, pytest.

**Spec:** `docs/superpowers/specs/2026-09-15-aws-30h-v3-preparation-design.md`

## Global Constraints

- **ABSOLUTELY ZERO LAUNCH SIDE EFFECTS**: `NEW 30H STARTED: NO`, `LAUNCH AUTHORIZED: FALSE`. No collector, supervisor, or systemd start.
- **ZERO DELETIONS**: `FILES DELETED = 0`, `BYTES DELETED = 0`, `S3 OBJECTS DELETED = 0`. Preserve V2 30H and old 72H soak as `KEEP_RESEARCH`.
- **ZERO S3 MUTATIONS**: No diagnostic `PutObject` or official prefix writes.
- **IDENTITY UNIQUENESS**: Never reuse V0/V1/V2 epochs, run IDs, or prefixes.
- **AUTHORITATIVE GATES**:
  - Storage Gate: $\ge 50\text{ GiB}$ free (actual: 102.62 GiB).
  - Terraform Gate: 0 to add, 0 to change, 0 to destroy.
  - IAM Gate: Minimal permissions, boundary `v6`, public data only.
- **POST-MERGE RUNTIME SEAL**: Code commit and tree hashes must be derived from the authoritative post-merge main commit, not intermediate branch commits.

---

### Task 1: Fresh EC2 / AWS Infrastructure Inspection & Storage Gate Re-verification
- [x] 1.1 Query AWS API for authoritative EC2 instance `i-008bc503c1136349f`: state `running`, type `t3.medium`, AZ `ap-northeast-2a`, attached volume `vol-0d46ca4af0d463549` (gp3, 200 GiB).
- [x] 1.2 Execute fresh read-only commands on EC2 via SSM: `hostname`, `whoami`, `date -u`, `df -h`, `df -T`, `lsblk`, `lsblk -f`, `findmnt`.
- [x] 1.3 Verify zero active collector/supervisor/scheduler processes (`RUNNING COLLECTOR: NO`).
- [x] 1.4 Validate Storage Gate: free disk space on `/` $\ge 50\text{ GiB}$ (PASS).

### Task 2: IAM Runtime Safety & Terraform Read-Only Gate
- [x] 2.1 Inspect attached IAM permissions boundary (`arn:aws:iam::080109295433:policy/bitcoin-trader-collector-boundary`) and role.
- [x] 2.2 Verify policy permits only public market data collection and temporary S3 archive operations; no trading, account balances, or private endpoints.
- [x] 2.3 Run read-only `terraform plan` against authoritative state: verify 0 to add, 0 to change, 0 to destroy.

### Task 3: Canonical Feed Universe Calculation & Seal (76 Feeds)
- [x] 3.1 Derive the 76 canonical feeds directly from runtime code:
  - Bithumb: 20 markets × 3 feeds = 60
  - Binance: 4 symbols × 2 feeds = 8
  - Upbit: 4 markets × 2 feeds = 8
- [x] 3.2 Verify deterministic sorting and compute `feed_universe_hash` using `canonical_sha256`.
- [x] 3.3 Create `infra/aws/seals/aws-validation-30h-20260915-v3.feed-universe.json`.

### Task 4: Timing, Deadline, and Heartbeat Contract Seal
- [x] 4.1 Define timing contract:
  - `qualification_start_utc = strictly_next_utc_hour(actual_start_utc)`
  - candidate cohorts = exactly 30
  - candidate stop = qualification_start + 30h
  - maximum collection window = 111,600s
- [x] 4.2 Define monotonic deadline invariant: single conversion at launch, immutable under NTP adjustments.
- [x] 4.3 Define heartbeat contract: probe 10s, timeout 10s, max gap 30s (>30s FAIL).
- [x] 4.4 Create `infra/aws/seals/aws-validation-30h-20260915-v3.timing-contract.json` and `infra/aws/seals/aws-validation-30h-20260915-v3.heartbeat-contract.json`.

### Task 5: Evidence-Based Finalization Timeout & Ceiling Justification
- [x] 5.1 Review Task 9 benchmark metrics (`aws-30h-v3-finalization-scale.json`): p50 ~ 5.5ms, p95 < 15ms dirty tail.
- [x] 5.2 Formulate mathematical relationship:
  - `finalization_timeout_seconds` = 180s
  - `supervisor_hard_ceiling_seconds` = 111,600s + 180s + 45s = 111,825s
  - `systemd_runtime_max_seconds` = 111,825s + 75s = 111,900s
- [x] 5.3 Document justification in prelaunch documentation.

### Task 6: Generate Unique V3 Official Identity & Namespace Emptiness Gate
- [x] 6.1 Allocate official V3 identity:
  - Epoch: `aws-validation-30h-20260915-v3`
  - Run ID: `aws-validation-30h-run-20260915T013000Z-v3`
  - Archive Prefix: `market-data/temporary/aws-validation-30h-20260915-v3`
  - Unit: `bitcoin-trader-30h-aws-validation-30h-run-20260915T013000Z-v3.service`
  - Root: `/var/lib/bitcoin-trader/30h-validation/aws-validation-30h-20260915-v3`
- [x] 6.2 Query S3 bucket `bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433` for prefix: confirm `KeyCount = 0` (empty namespace).

### Task 7: Authoritative Launch Artifacts Generation & Validation
- [x] 7.1 Author `infra/aws/seals/aws-validation-30h-20260915-v3.runtime.json`.
- [x] 7.2 Author `infra/aws/seals/aws-validation-30h-20260915-v3.launch-command.json`.
- [x] 7.3 Author `infra/aws/seals/aws-validation-30h-20260915-v3.launch-provenance.json`.
- [x] 7.4 Author `infra/aws/seals/aws-validation-30h-20260915-v3.launch-wrapper.sh`.
- [x] 7.5 Author `infra/aws/seals/aws-validation-30h-20260915-v3.authorization-evidence.json` (`launch_authorized: false`, `actual_start_time_utc: null`).
- [x] 7.6 Validate `launch_short_smoke_transient.py` render output matches sealed command without `--launch`.

### Task 8: Comprehensive Prelaunch & Seal Documentation
- [x] 8.1 Author `docs/AWS_30H_PRELAUNCH_20260915_V3.md`.
- [x] 8.2 Incorporate all verdicts, tables, identities, and gates into documentation.

### Task 9: Local Verification & Independent Review
- [x] 9.1 Run Pyright on all changed paths: 0 errors, 0 warnings.
- [x] 9.2 Run `compileall` and `git diff --check`.
- [x] 9.3 Run pytest on core suite.
- [x] 9.4 Dispatch independent review subagent: verify Critical = 0, Important = 0.

### Task 10: PR Creation & Regular Merge to main
- [ ] 10.1 Commit changes and push `codex/aws-30h-v3-preparation-20260915-e9d5d5a`.
- [ ] 10.2 Create PR on GitHub.
- [ ] 10.3 Verify CI checks and perform Regular Merge.
- [ ] 10.4 Fast-forward authoritative local main worktree to merge commit.

### Task 11: Post-Merge Authoritative Runtime Seal & Final Gate
- [ ] 11.1 On fresh authoritative main, calculate:
  - `runtime_code_commit`, `runtime_git_tree`, `runtime_config_fingerprint`
  - All sealed artifact hashes.
- [ ] 11.2 Verify official state non-consumption:
  - 0 S3 objects written, 0 collector processes started, 0 systemd units started, 0 actual-start evidence.
- [ ] 11.3 Output final structured report and STOP.
