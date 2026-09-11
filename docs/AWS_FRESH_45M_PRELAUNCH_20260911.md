# AWS Fresh 45-Minute Pre-Launch Preparation Report (2026-09-11)

## 1. Executive Summary

- **Task Purpose:** Merged `main` 커밋 `1976f0f33b0ee1e1483b485c9cb2945fc3bb6f28` 기반의 신규 45분 AWS 무인 검증 패키지 준비, 봉인(Seal), 게스트 배포 및 독립 사전 검토 완료.
- **Strict Prohibition (실행 금지):** 런칭 인가(`launch_authorized=false`), 시작 시각(`actual_start_time_utc=null`), 수집기 실행, systemd 유닛 가동, 스케줄러 실행, 수동 S3 쓰기, 45M/30H 런타임 일체 미실행 (Fail-Closed).
- **Authoritative Main Base:** `1976f0f33b0ee1e1483b485c9cb2945fc3bb6f28` (local == origin/main 100% 일치)
- **Local Full Suite:** **1026 passed, 2 skipped, 0 failed** in 89.91s
- **Focused Archive/Policy Suite:** **76 passed, 0 failed**
- **Terraform Read-Only Gate:** **0 to add, 0 to change, 0 to destroy** (lineage: `5e8ff4b0-1d32-ed2e-9ed0-a9e68d3f3ccf`, 27 resources)
- **IAM Read-Only Gate:** Boundary default `v6`, 신규 validation prefix 기준 `GetObject=ALLOW`, `PutObject=ALLOW`, `ListBucket=DENY`, `DeleteObject=DENY`, canonical/old-72h/other/private DENY. IAM 변경 없음 (**NONE**).
- **New Validation Epoch:** `aws-validation-45m-20260911-1976f0f`
- **New Validation Run ID:** `aws-validation-45m-run-20260911T104000Z-1976f0f`
- **Canonical Config Fingerprint:** `51694e7537fcbec5337d880ff5e75961fe9883559b39d7c6da83714a7da78cb4`
- **Guest Runtime Worktree:** `/var/lib/bitcoin-trader/runtime-worktrees/aws-validation-45m-20260911-1976f0f` (detached HEAD exact, clean, tree/archive SHA 일치)
- **Guest Core Regression Tests:** **102 passed, 0 failed** in isolated Python 3.11 test venv
- **Render-Only Systemd/CLI Verification:** **PASS** (service-type=exec, uid=bitcoin-trader, timing 2700/120/2820/2880, poll 30, grace 600)
- **Identity Cleanliness:** S3 prefix objects = **0**, local output = **ABSENT**, systemd unit executions = **0**, running processes = **0**
- **Independent Pre-Launch Review:** **PASS** (Critical: 0, Important: 0, Minor: 0)
- **Status:** **READY TO AUTHORIZE NEW FRESH 45M: YES / READY TO LAUNCH: YES / FRESH 45M STARTED: NO / 30H STARTED: NO**

---

## 2. Local Recovery & Git Verification

- **Current Branch:** `codex/aws-fresh45-prep-20260911-1976f0f`
- **HEAD Commit:** `1976f0f33b0ee1e1483b485c9cb2945fc3bb6f28`
- **Authoritative main:** `1976f0f33b0ee1e1483b485c9cb2945fc3bb6f28`
- **origin/main:** `1976f0f33b0ee1e1483b485c9cb2945fc3bb6f28` (local == origin/main)
- **Tracked Modifications:** NONE (`git diff --check` CLEAN)
- **Preserved Artifacts:** 기존 untracked `test-results/` 보존 및 비접촉 유지.
- **Remediation Ancestry:** PR #1 merged commit (`5331003`, `ec37173`, `0a6b8d1`, `ae64af2`) 모두 authoritative main의 선조(ancestry)로 확인됨.
- **Local Python Suite:** 1026 passed, 2 skipped, 0 failed (89.91s).

---

## 3. Historical Evidence Immutability

- 과거 실패 에포크 `aws-validation-45m-20260909-6c9d1758` 및 런 ID `aws-validation-45m-run-20260909T080939Z-6c9d1758`의 모든 증거, 씰, 아티팩트, S3 오염 객체 영구 보존 및 수정/재사용/삭제 금지 준수.
- 과거 로컬 씰 및 증거 디렉터리 변조 없음.

---

## 4. Terraform Read-Only Gate

- **Authoritative State Path:** `/Users/macintosh/Documents/ChatGPT/bitcoin-trader/infra/aws/terraform.tfstate`
- **Lineage:** `5e8ff4b0-1d32-ed2e-9ed0-a9e68d3f3ccf`
- **State Resource Count:** 27
- **State Instance Count:** 29
- **Provisioner Profile:** `bitcoin-trader-provisioner` (비-root assumed-role 체인: `arn:aws:sts::080109295433:assumed-role/bitcoin-trader-terraform-provisioner/codex-preapply-validation`)
- **Plan Command:**
  ```sh
  terraform -chdir=infra/aws plan \
    -state=/Users/macintosh/Documents/ChatGPT/bitcoin-trader/infra/aws/terraform.tfstate \
    -var=ami_id_override=ami-08d82cf148c92fcc3 \
    -var=availability_zone=ap-northeast-2a \
    -var=collector_git_commit=9532cebc902856d954bf80b51dbe567b543dc8e2 \
    -var=collector_epoch=aws-72h-soak-20260905-8017b83e \
    -var=collector_run_id=aws-72h-soak-run-20260905T024039Z-8017b83e \
    -var=collector_config_fingerprint=a023fb5723830c38a7f7d47f2439334fcb44d2c6559939dba7a7cb1c2f88783b \
    -no-color
  ```
- **Plan Result:** **0 to add, 0 to change, 0 to destroy** (`No changes. Your infrastructure matches the configuration.`)
- **Backend Reconfigure:** NO
- **State Migration:** NO
- **Implicit Worktree State:** NO
- **Apply:** NO

---

## 5. IAM Read-Only Gate

- **Collector Role:** `bitcoin-trader-aws-apne2-research-collector`
- **Inline Policy:** `collector-epoch-access`
  - S3 Resource: `arn:aws:s3:::bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433/market-data/temporary/aws-validation-*/*`
  - S3 Actions: `s3:GetObject`, `s3:PutObject`
- **Permissions Boundary:** `arn:aws:iam::080109295433:policy/bitcoin-trader-collector-boundary`
  - Default Version: **v6** (`IsDefaultVersion: true`)
  - Statement 1 (`ValidationArchiveObjects`): `s3:GetObject`, `s3:PutObject` on `market-data/temporary/aws-validation-*/*`
- **Policy Simulation Results:**
  - `s3:GetObject` on `aws-validation-45m-20260911-1976f0f`: **allowed**
  - `s3:PutObject` on `aws-validation-45m-20260911-1976f0f`: **allowed**
  - `s3:ListBucket`: **implicitDeny** (DENY)
  - `s3:DeleteObject`: **implicitDeny** (DENY)
  - `s3:PutObject` on canonical prefix: **implicitDeny** (DENY)
  - `s3:GetObject` on canonical prefix: **implicitDeny** (DENY)
  - `s3:PutObject` on old 72H prefix: **implicitDeny** (DENY)
  - `s3:GetObject` on old 72H prefix: **implicitDeny** (DENY)
  - `s3:PutObject` on unrelated temporary prefix: **implicitDeny** (DENY)
  - `s3:PutObject` on other bucket: **implicitDeny** (DENY)
  - Private/Trading permissions: **implicitDeny** (DENY)
- **IAM Changes:** **NONE**
- **Temporary IAM Permission Decision:**
  - SAFE TO REMOVE: **YES**
  - REMOVED IN THIS TASK: **NO**

---

## 6. New Validation Identity & Sealed Artifacts

- **Epoch ID:** `aws-validation-45m-20260911-1976f0f`
- **Run ID:** `aws-validation-45m-run-20260911T104000Z-1976f0f`
- **Runtime Code Commit:** `1976f0f33b0ee1e1483b485c9cb2945fc3bb6f28`
- **Git Tree SHA:** `1503b3665296417540443c1fd8210106595d265d`
- **Deterministic Git Archive SHA-256:** `2b0dc6572b9810439b6ec9840c8d924f0b6a72d1976544cbfd8482989beead4f`
- **Canonical Config Fingerprint:** `51694e7537fcbec5337d880ff5e75961fe9883559b39d7c6da83714a7da78cb4`
- **Runtime Seal File:** `infra/aws/seals/aws-validation-45m-20260911-1976f0f.runtime.json`
  - SHA-256: `ae57c0931207fdbcb0f018f2a9d73b73f4eac4e74b0850ed071d920583d3ea41`
- **Launch Command File:** `infra/aws/seals/aws-validation-45m-20260911-1976f0f.launch-command.json`
  - SHA-256: `88b56c7141d11b549a29585204e289ec9a28707b104e728278fb6d55545b42bf`
  - `launch`: false
- **Launch Provenance File:** `infra/aws/seals/aws-validation-45m-20260911-1976f0f.launch-provenance.json`
  - SHA-256: `62ba9f7ea48f395db21eb2f6e28762192fb8dbc8b5accae3e5906d881565d510`
  - `launch_authorized`: false
  - `actual_start_time_utc`: null
- **Feed Universe:** Bithumb 20×3 + Binance 4×2 + Upbit 4×2 = **76** (중복 0, 누락 0)

---

## 7. Guest Runtime Environment & Deployment

- **Target Instance:** `i-008bc503c1136349f` (`bitcoin-trader-aws-apne2-research-collector`)
- **Runtime Worktree:** `/var/lib/bitcoin-trader/runtime-worktrees/aws-validation-45m-20260911-1976f0f`
  - Creation: `git -C /opt/bitcoin-trader fetch origin` 후 detached worktree add
  - Detached HEAD: `1976f0f33b0ee1e1483b485c9cb2945fc3bb6f28` (EXACT MATCH)
  - Tree SHA: `1503b3665296417540443c1fd8210106595d265d` (MATCH)
  - Deterministic Archive SHA: `2b0dc6572b9810439b6ec9840c8d924f0b6a72d1976544cbfd8482989beead4f` (MATCH)
  - Status: **CLEAN** (`nothing to commit, working tree clean`)
  - Ownership: `bitcoin-trader:bitcoin-trader`
  - Historical checkouts: `/opt/bitcoin-trader` 및 구 worktree 일체 불변 보존
- **Launch Artifacts Guest Path:** `/var/lib/bitcoin-trader/launch-artifacts/aws-validation-45m-20260911-1976f0f/`
  - `aws-validation-45m-20260911-1976f0f.runtime.json` (SHA-256 MATCH)
  - `aws-validation-45m-20260911-1976f0f.launch-command.json` (SHA-256 MATCH)
  - `aws-validation-45m-20260911-1976f0f.launch-provenance.json` (SHA-256 MATCH)
- **Guest Core Regression Tests:**
  - Test venv: `/var/lib/bitcoin-trader/runtime-envs/aws-validation-45m-20260911-1976f0f-test-py311` (Python 3.11.16)
  - Results: **102 passed, 0 failed in 29.76s**
  - Scope: bounded supervisor, transient launch, archive scheduler, S3 archive store, pre-soak archive, post-72h remediation, AWS validation archive policy.

---

## 8. Render-Only Verification & Timing Contract

- **Render-Only Execution:** **PASS** (로컬 및 게스트 양쪽에서 무인 렌더링 검증 완료, 프로세스 미실행)
- **Rendered Unit:** `bitcoin-trader-short-smoke-aws-validation-45m-run-20260911T104000Z-1976f0f.service`
- **Contract Timing Values:**
  - Collection Duration: **2700s** (45분)
  - Finalization Timeout: **120s**
  - Supervisor Hard Ceiling: **2820s**
  - Systemd RuntimeMaxSec: **2880s**
  - Archive Scheduler Poll: **30.0s**
  - Archive Grace: **600s**
- **Lifecycle Semantics:**
  - `collection (2700) < ceiling (2820) < systemd (2880)`
  - `TimeoutStopSec=55s`, `KillMode=mixed`, `Restart=no`
  - Clean shutdown & flush observed contract 보장

---

## 9. Merged Archive Remediation Verification

런타임 커밋 `1976f0f33b0ee1e1483b485c9cb2945fc3bb6f28`의 아카이브 구현체 검증 완료:
1. **No `exists()` preflight:** 업로드 전 `HeadObject` 사전 조회 호출 전무 확인.
2. **Exact 409 retry:** `409 Conflict` 및 `ConditionalRequestConflict` 발생 시 최대 3회 bounded retry 및 새 스트림 사용.
3. **Exact 412 reuse:** `412 PreconditionFailed` 발생 시 권위적 `HeadObject` 검증 후 일치 시 안전 재사용.
4. **Malformed response preservation:** AWS 응답이 불완전하거나 비정상일 경우 원본 예외 보존.
5. **Streamed restore verification:** 원격 복원 검증 스트리밍 방식 불변 유지.

---

## 10. Cleanliness & Pre-Authorization Proof

| 항목 | 요구사항 | 실측값 | 판정 |
|---|---|---|---|
| New S3 Prefix Objects | 0 | **0** | **PASS** |
| New Local Output Directory | Absent / Unused | **ABSENT** | **PASS** |
| New Systemd Unit Executions | 0 | **0** (Unit not found) | **PASS** |
| New Run Processes | 0 | **0** (NO_RUNNING_PROCESS) | **PASS** |
| actual_start_time_utc | null | **null** | **PASS** |
| launch_authorized | false | **false** | **PASS** |

---

## 11. Independent Pre-Launch Review

- **Identity Uniqueness:** PASS (신규 에포크 및 런 ID 중복 없음)
- **Exact Runtime Commit:** PASS (`1976f0f33b0ee1e1483b485c9cb2945fc3bb6f28`)
- **Feed Universe:** PASS (76개 피드 정확히 일치)
- **Seal & Fingerprint:** PASS (로컬/게스트 해시 및 핑거프린트 100% 일치)
- **Terraform Plan:** PASS (lineage 동일, 27 resources, 0 add / 0 change / 0 destroy)
- **IAM Policy Scope:** PASS (Boundary v6, S3 Get/Put ALLOW, List/Delete DENY, 타 네임스페이스 DENY)
- **S3 Prefix & Local Output Emptiness:** PASS (오염 0)
- **Launch Command:** PASS (render-only 검증 통과)
- **Guest Worktree:** PASS (detached, clean, tests 102/102 PASS)
- **Archive Remediation:** PASS (no exists, 409 retry, 412 reuse, error preservation)

| 구분 | 건수 |
|---|---|
| **Critical Findings** | **0** |
| **Important Findings** | **0** |
| **Minor Findings** | **0** |
| **PRE-LAUNCH REVIEW VERDICT** | **PASS** |

---

## 12. Next Gate & Scientific Safety

- **READY TO AUTHORIZE NEW FRESH 45M:** **YES**
- **launch_authorized:** `false` (운영자 명시적 승인 대기)
- **READY TO LAUNCH:** **YES**
- **FRESH 45M STARTED:** **NO**
- **30H STARTED:** **NO**

- **Scientific Stance:**
  - ALPHA: **UNPROVEN**
  - PAPER: **NOT STARTED**
  - LIVE: **DISABLED**
  - PRIVATE API: **DISABLED**
