# AWS Fresh 45-Minute Validation Preparation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prepare, seal, deploy, and independently review a new 45-minute AWS validation package from merged `main` commit `1976f0f33b0ee1e1483b485c9cb2945fc3bb6f28` without authorizing or launching any runtime process.

**Architecture:** Repository artifacts are created on `codex/aws-fresh45-prep-20260911-1976f0f`; AWS checks are read-only except for creating an isolated service-owned guest Git worktree. The existing explicit local Terraform state remains authoritative, IAM remains unchanged, and the new S3 namespace is only inspected for emptiness. Launch provenance stays fail-closed with `launch_authorized=false` and `actual_start_time_utc=null`.

**Tech Stack:** Python 3.11+, pytest, Pyright, Terraform, AWS CLI/SSM, Git detached worktrees, transient systemd render-only tooling.

**Spec:** `/Users/macintosh/.codex/attachments/814ae61c-9074-4eb5-beb2-a18993f7ea0d/pasted-text.txt`

## Global constraints

- Never touch `/Users/macintosh/Documents/ChatGPT/bitcoin-trader/test-results/`.
- Never modify, repair, delete, requalify, or reuse the failed `aws-validation-45m-20260909-6c9d1758` run or any of its local/S3 evidence.
- Do not change IAM, run Terraform apply, write S3 objects, launch systemd, start collector/publisher/scheduler, or start the 45-minute/30-hour validations.
- Preserve public-data-only operation: alpha unproven, paper disabled, live disabled, private API disabled.

### Task 1: Verify merged main and local implementation

**Files:**
- Read: `src/bithumb_coin_trader/pre_soak_archive.py`
- Read: `tests/test_pre_soak_archive.py`
- Read: `tests/test_s3_archive_store.py`

- [ ] Prove local base and `origin/main` both equal `1976f0f33b0ee1e1483b485c9cb2945fc3bb6f28`, the remediation commits are ancestors, and only the pre-existing untracked `test-results/` is present.
- [ ] Run the full Python suite, focused archive/S3 tests, compileall, `git diff --check`, and the affected Pyright invocation; stop on any failure or unexplained count drift.
- [ ] Verify the runtime commit implements no preflight HeadObject, bounded exact 409 retry, exact 412 reuse, fresh streams, fail-closed wrong-object validation, original malformed exception preservation, and streamed restore verification.

### Task 2: Prove historical evidence immutability

**Files:**
- Read: `infra/aws/seals/aws-validation-45m-20260909-6c9d1758.runtime.json`
- Read: `infra/aws/seals/aws-validation-45m-20260909-6c9d1758.launch-command.json`
- Read: `infra/aws/seals/aws-validation-45m-20260909-6c9d1758.launch-provenance.json`
- Read: `docs/AWS_FRESH_45M_FAILURE_FORENSICS_20260911.md`

- [ ] Recompute repository file/tree hashes and compare with preserved report hashes where available.
- [ ] Use read-only guest/S3 queries to prove the failed epoch artifacts and contaminated object remain present and unmodified; do not issue any write or cleanup request.

### Task 3: Verify Terraform and IAM gates read-only

**Files:**
- Read: `infra/aws/terraform.tfstate`
- Read: `infra/aws/main.tf`
- Read: `infra/aws/identity/collector-permissions-boundary-validation.json.example`
- Read: `docs/TERRAFORM_LOCAL_STATE_WORKTREE_RUNBOOK.md`

- [ ] Verify the explicit authoritative state lineage and resource/address count, ensure no concurrent Terraform operation, and run a plan with `-state=/Users/macintosh/Documents/ChatGPT/bitcoin-trader/infra/aws/terraform.tfstate`; require 0 add, 0 change, 0 destroy.
- [ ] Read the actual EC2 role, its inline policy, collector boundary default/version history, and exact temporary bootstrap policy without exercising version-management actions.
- [ ] Simulate the new validation prefix: require GetObject/PutObject ALLOW and ListBucket/DeleteObject/canonical/old-72H/unrelated-temporary/other-bucket/private-service DENY.

### Task 4: Create and seal the new repository identity

**Files:**
- Create: `infra/aws/seals/aws-validation-45m-20260911-1976f0f.runtime.json`
- Create: `infra/aws/seals/aws-validation-45m-20260911-1976f0f.launch-command.json`
- Create: `infra/aws/seals/aws-validation-45m-20260911-1976f0f.launch-provenance.json`
- Create: `docs/AWS_FRESH_45M_PRELAUNCH_20260911.md`

- [ ] After all local gates pass, use epoch `aws-validation-45m-20260911-1976f0f` and a unique timestamped run ID; prove neither identity exists in repository, guest paths, process/unit history, or S3.
- [ ] Seal exact 2700/120/2820/2880 timing, 30-second scheduler poll, 600-second grace, cleanup false, cross-hour required, and exact feed identities for Bithumb 60 / Binance 8 / Upbit 8.
- [ ] Compute the canonical JSON fingerprint, runtime-seal SHA-256, `1976f0f...` tree SHA, and deterministic Git archive SHA-256.
- [ ] Create the exact launch-command JSON with `launch=false`; create provenance with `launch_authorized=false` and `actual_start_time_utc=null`.
- [ ] Render the transient systemd command without executing it and validate service user, working directory, PYTHONPATH, Python, nested CLI arguments, shutdown semantics, and all identity/path bindings.

### Task 5: Prepare isolated guest runtime and verify it

**Files:**
- Deploy read-only source commit into: `/var/lib/bitcoin-trader/runtime-worktrees/aws-validation-45m-20260911-1976f0f`
- Deploy launch artifacts into: `/var/lib/bitcoin-trader/launch-artifacts/aws-validation-45m-20260911-1976f0f`

- [ ] Through approved non-root SSM, fetch the normal repository remote and create a new service-owned detached worktree at exact commit `1976f0f...`; do not alter `/opt/bitcoin-trader`, historical checkouts, or historical worktrees.
- [ ] Prove exact detached HEAD, clean status, tree/archive/seal/fingerprint matches, and imports resolve from the new worktree `src`.
- [ ] Create an isolated production-compatible test environment and run the core supervisor/transient/archive/S3/post-72H/IAM policy regression set; record the exact result.
- [ ] Prove the new local output is absent/unused, the new S3 prefix has zero objects, the new systemd unit has zero executions, and no new-run process exists.

### Task 6: Commit, push, and independently review

**Files:**
- Modify only the new plan, seal, launch, provenance, and prelaunch report files listed above.

- [ ] Run final JSON/schema checks, focused regression tests, compileall, Pyright, `git diff --check`, and a secret scan of the proposed diff.
- [ ] Commit and push `codex/aws-fresh45-prep-20260911-1976f0f`; verify local/remote SHA equality and retain `main` unchanged.
- [ ] Request an independent pre-launch review covering identity, commit/worktree provenance, hashes, feed set, launch rendering, IAM, Terraform 0/0/0, namespace/output emptiness, timing, scheduler contract, active-prefix write prohibition, and the merged S3 remediation behavior.
- [ ] Stop on any Critical or Important finding. Otherwise report readiness while keeping authorization false and every runtime NOT STARTED.
