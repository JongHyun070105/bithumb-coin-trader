# AWS 45-Minute Short-Smoke Retry New-Epoch Reseal Preflight — 2026-09-04

## 1. Executive Summary

- **Interruption Recovery:** **PASS** (중단 전 마지막 명령 `--launch` 부재로 Render Only였음이 live 확인됨)
- **New-Epoch Reseal:** **PASS**
- **IAM Boundary Reconciliation:** **PASS** (`v3` default 적용 완료)
- **Terraform Provenance Apply:** **PASS** (0 add, 3 change, 0 destroy, 0 replace)
- **Post-Apply Provider Plan:** **NO CHANGES**
- **Live Provenance Tags:** **MATCH** (Role, EC2, Root EBS 4종 태그 전수 일치)
- **Effective IAM Verification:** **PASS** (New Allow / Old Deny / Delete Deny / CW Match)
- **Guest Runtime Preflight:** **PASS** (58/58 tests, Binance 443 10/10 PASS)
- **Process State After Preflight:** **COLLECTOR NOT STARTED (0 Running)**
- **New Epoch Market Data:** **NONE (0 Bytes)**
- **Failed Old Epoch:** **PRESERVED** (`aws-short-smoke-20260902-38cb8a72`)
- **Status:** **READY FOR 45-MIN RETRY EXECUTION APPROVAL**

---

## 2. Interruption Recovery & Last Command State

- **확인 명령:** 직전 세션(2026-09-04 01:16 KST)에서 실행된 `scripts/launch_short_smoke_transient.py`
- **검증 결과:** `--launch` 플래그가 전달되지 않아 systemd-run 명령 렌더링(JSON 배열 출력)만 수행되고 실제 유닛 등록 및 프로세스 실행은 전혀 일어나지 않았음을 Live EC2 상에서 확인.
- **Live 프로세스 및 systemd 상태:**
  - `systemctl list-units 'bitcoin-trader*'`: **0 loaded units**
  - `systemctl list-unit-files 'bitcoin-trader*'`: **0 unit files**
  - Collector (`run_cross_market_collector.py`): **0**
  - Publisher (`publish_collector_metrics.py`): **0**
  - Archive Worker (`manage_pre_soak_archive.py`): **0**
  - Bounded Supervisor (`run_bounded_short_smoke.py`): **0**
- **New Epoch 저장소 확인:**
  - 경로: `/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260904-f5257d24/`
  - 하위 디렉터리(`raw`, `manifests`, `compressed`, `archive-receipts`, `logs`)는 존재하나 내용물은 0바이트/빈 상태.
  - `collector_metrics.json`: **ABSENT**
  - `metric-publisher-state.json`: **ABSENT**
  - `result.json`: **ABSENT**
  - 실제 마켓 데이터 레코드: **NONE**

---

## 3. Provenance & Sealed Runtime Candidate

- **Origin Main Commit:** `9c3e96a5ce87e924a0a8072d38a0ea4cb1b2521f`
- **Runtime Code Commit:** `5013728aacffcdc1b6faa84c19d8144287e25cde`
- **Ancestor 검증:** `git merge-base --is-ancestor 5013728... origin/main` -> **PASS**
- **Worktree 상태:** **CLEAN**
- **Runtime Seal File:** `infra/aws/seals/aws-short-smoke-20260904.runtime.json`
- **Execution Launch Mode:** `bounded-transient-systemd`
- **Canonical Config Fingerprint:** `48e5996f86567dfa41ed515de0e96fdb3230001fbc0ac2e0eb5453dad81422a0` (로컬 및 게스트 양쪽에서 재계산 결과 일치)
- **New Epoch:** `aws-short-smoke-20260904-f5257d24`
- **Final Authoritative UTC Run ID:** `aws-short-smoke-run-20260903T160337Z-f5257d24` (이전 KST 오염 `...005600Z...`는 폐기됨)
- **실행 시간:** 2700초 (45분)
- **피드 구성:** Bithumb 20마켓, Binance 4심볼, Upbit 4마켓
- **아카이브 설정:** zstd level 1, worker concurrency 1, grace 600s, cleanup false
- **메트릭 주기:** 60초
- **보존 대상 구 실패 Epoch:** `aws-short-smoke-20260902-38cb8a72` (보존 완료, 삭제/재사용 절대 금지)

---

## 4. Permissions Boundary Reconciliation

- **대상 리소스:** `arn:aws:iam::080109295433:policy/bitcoin-trader-collector-boundary`
- **Before State:**
  - Default Version: `v2`
  - Version Count: 2 (`v1`, `v2`)
  - 허용 Epoch: `aws-short-smoke-20260902-38cb8a72` (OLD failed epoch)
- **Candidate Rebuild:**
  - Source: `infra/aws/identity/collector-permissions-boundary.json.example`
  - 치환: Bucket `bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433`, Account `080109295433`, Epoch `aws-short-smoke-20260904-f5257d24`
  - Canonical Candidate SHA-256: `0f7e9b6876dd7cc823174d8b4bf367faced030a22e27b7670dca1aefb10b069c`
- **Access Analyzer 검증:**
  - `aws accessanalyzer validate-policy --policy-type IDENTITY_POLICY`: **findings 0 (ERROR: 0, SECURITY_WARNING: 0)**
- **Semantic Diff (v2 vs Candidate):**
  - ListBucket canonical prefix: `.../canonical/aws-short-smoke-20260904-f5257d24/*`
  - ListBucket temporary prefix: `.../temporary/aws-short-smoke-20260904-f5257d24/*`
  - Canonical Object ARN: `.../canonical/aws-short-smoke-20260904-f5257d24/*`
  - Temporary Object ARN: `.../temporary/aws-short-smoke-20260904-f5257d24/*`
  - Action 추가/삭제: **0 / 0**
  - DeleteObject: **부재 (DENY 유지)**
  - Bucket, Condition, Namespace, SSM, Logs 변경: **0**
- **Privileged IAM Write:**
  - 지침 14 승인 하에 `hermes-aws` (root browser login) 세션으로 일회성 `iam:CreatePolicyVersion` 수행.
  - 결과: 새 버전 `v3` 생성 및 기본 버전 지정 완료 (`IsDefaultVersion: true`).
  - 즉시 Read-Back SHA-256: `0f7e9b6876dd7cc823174d8b4bf367faced030a22e27b7670dca1aefb10b069c` (**MATCHED**).
  - Privileged root 세션 즉시 종료/폐기.

---

## 5. Safe Transition (Safe Blackout)

Boundary를 `v3`(NEW epoch)로 승격하고 Terraform apply(inline NEW epoch)를 실행하기 직전:
- Boundary: NEW epoch 허용
- Inline Policy: OLD epoch 허용
- 교집합 결과:
  - NEW temporary/canonical prefix: **DENY**
  - OLD temporary/canonical prefix: **DENY**
- 수집기 프로세스는 완전히 정지 상태였으므로 이 일시적 Fail-Closed 블랙아웃은 완전히 안전함.

---

## 6. Terraform State Safety & Provenance Apply

- **사전 점검:**
  - `terraform fmt -check`: **PASS**
  - `terraform validate`: **PASS**
  - `terraform.tfstate` 모드: `0600`, FileVault: **ON**
  - Managed/Data Address 수: **29개**
  - Pre-Apply Backup: `infra/aws/terraform.tfstate.pre-apply-20260904T021715Z.backup` (SHA-256: `8b6a48c501ccdba946c39bd57453596ca8d026fe42f16b94c09e168ec578b095` 일치)
- **실행 주체:** 임시 Assumed Role `bitcoin-trader-terraform-provisioner` (Root Terraform 절대 금지).
- **Saved Plan (`tfplan-20260904`):**
  - `0 to add, 3 to change, 0 to destroy, 0 to replace`
  - In-place 변경 3건:
    1. `aws_iam_role.collector` (tags 갱신)
    2. `aws_iam_role_policy.collector` (inline policy epoch prefix 갱신)
    3. `aws_instance.collector` (인스턴스 태그 및 root_block_device 태그 갱신)
  - AMI, 인스턴스 타입, EBS 크기(100 GiB)/암호화, VPC, 서브넷, 라우트, 보안 그룹, S3 버킷 설정, 알람, 경계 연결 유지: **변경 0**
- **Apply 실행:**
  - `terraform apply "tfplan-20260904"` -> **SUCCESS** (`Resources: 0 added, 3 changed, 0 destroyed`).
  - 임시 plan 파일 즉시 삭제.
- **Post-Apply Fresh Plan:**
  - `terraform plan` 결과: **"No changes. Your infrastructure matches the configuration."**
- **Post-Apply Backup:**
  - `infra/aws/terraform.tfstate.post-apply-20260904T022513Z.backup` (SHA-256: `6f3df8793043662b7ab64deea86928534031c48de19c3ae1e91eb3c3c13f68e7` 일치)

---

## 7. Live Provenance Read-Back

실시간 AWS 리소스 메타데이터 조회 결과:

| 리소스 | CollectorCommit | ConfigFingerprint | CollectorEpoch | CollectorRunId | 판정 |
|---|---|---|---|---|---|
| **IAM Role** | `5013728aacffcdc1b6faa84c19d8144287e25cde` | `48e5996f86567dfa41ed515de0e96fdb3230001fbc0ac2e0eb5453dad81422a0` | `aws-short-smoke-20260904-f5257d24` | `aws-short-smoke-run-20260903T160337Z-f5257d24` | **MATCH** |
| **EC2 Instance** | `5013728aacffcdc1b6faa84c19d8144287e25cde` | `48e5996f86567dfa41ed515de0e96fdb3230001fbc0ac2e0eb5453dad81422a0` | `aws-short-smoke-20260904-f5257d24` | `aws-short-smoke-run-20260903T160337Z-f5257d24` | **MATCH** |
| **Root EBS Volume** | `5013728aacffcdc1b6faa84c19d8144287e25cde` | `48e5996f86567dfa41ed515de0e96fdb3230001fbc0ac2e0eb5453dad81422a0` | `aws-short-smoke-20260904-f5257d24` | `aws-short-smoke-run-20260903T160337Z-f5257d24` | **MATCH** |

---

## 8. Effective IAM Post-Apply Simulation

`aws iam simulate-principal-policy`를 통한 Collector Role 실제 권한 교차 검증:

| 평가 항목 | 대상 Action 및 Resource | 시뮬레이션 결과 | 기대값 |
|---|---|---|---|
| NEW Temporary Prefix | `s3:PutObject`, `s3:GetObject` (temporary/new-epoch/*) | **allowed** | ALLOW |
| NEW Canonical Prefix | `s3:PutObject`, `s3:GetObject` (canonical/new-epoch/*) | **allowed** | ALLOW |
| OLD Failed Temporary Prefix | `s3:PutObject`, `s3:GetObject` (temporary/old-epoch/*) | **implicitDeny** | DENY |
| OLD Failed Canonical Prefix | `s3:PutObject`, `s3:GetObject` (canonical/old-epoch/*) | **implicitDeny** | DENY |
| DeleteObject 권한 | `s3:DeleteObject` (임의 prefix) | **implicitDeny** | DENY |
| 타 S3 버킷 접근 | `s3:PutObject` (unrelated-bucket) | **implicitDeny** | DENY |
| CloudWatch 정확한 네임스페이스 | `cloudwatch:PutMetricData` (`BitcoinTrader/Collector`) | **allowed** | ALLOW |
| CloudWatch 잘못된 네임스페이스 | `cloudwatch:PutMetricData` (`WrongNamespace`) | **implicitDeny** | DENY |

---

## 9. Guest Runtime Final Preflight & Binance 443 Validation

- **Guest 환경:** Amazon Linux EC2 (`i-008bc503c1136349f`), checkout `/var/lib/bitcoin-trader/remediation-650adc8` (HEAD: `5013728...`, clean).
- **Python / Pip / Compileall:** Python 3.11.16, broken requirements 0, compileall PASS.
- **타겟 단위 테스트:** **58 / 58 PASS** (2.549s).
  - Cross-market collector: PASS
  - Bounded supervisor: PASS
  - Transient launch: PASS
  - Binance diagnostic: PASS
  - Pre-soak archive: PASS
  - Runtime config: PASS
- **파티션 회전 및 종료 불변식 검증:**
  - `test_active_partitions_rotate_for_multiple_feeds_and_idle_feeds`: PASS
  - `test_shutdown_drain_persists_empty_active_set_and_keeps_all_manifests`: PASS (`active_partition_files == []` 보장)
- **아카이브 활성 가드:** 25/25 PASS (진행 중인 현재 파티션 제외 및 닫힌 이전 파티션 아카이브 대상화).
- **Binance WebSocket 공식 443 포트 진단 (`diagnose_binance_websocket.py`):**
  - DNS IPv4: 7개 IP 정상 확인
  - TCP Connect (443): 7/7 PASS (~32ms)
  - TLS 1.3 Handshake: 7/7 PASS (~43ms)
  - 4개 심볼(BTC, ETH, SOL, XRP) & 프로덕션 결합 스트림 (auto/direct): **10/10 WebSocket PASS** (~112-125ms)
  - 프로덕션 코드 내 9443 엔드포인트: **NONE (전무)**

---

## 10. Transient Unit & Full Supervisor Command Render

실제 향후 실행 시 사용할 완전한 커맨드 렌더링 검증 완료 (미실행):

```json
[
  "systemd-run",
  "--unit=bitcoin-trader-short-smoke-aws-short-smoke-run-20260903T160337Z-f5257d24.service",
  "--no-block",
  "--collect",
  "--service-type=exec",
  "--uid=bitcoin-trader",
  "--setenv=PYTHONPATH=src",
  "--property=Restart=no",
  "--property=KillMode=mixed",
  "--property=RuntimeMaxSec=2760s",
  "--property=TimeoutStopSec=55s",
  "--working-directory=/var/lib/bitcoin-trader/remediation-650adc8",
  "--",
  "/var/lib/bitcoin-trader/venv-pre-soak/bin/python",
  "scripts/run_bounded_short_smoke.py",
  "--run-id",
  "aws-short-smoke-run-20260903T160337Z-f5257d24",
  "--duration-seconds",
  "2700",
  "--collector-command-json",
  "["/var/lib/bitcoin-trader/venv-pre-soak/bin/python", "scripts/run_cross_market_collector.py", "--bithumb-markets", "20", "--duration", "2700", "--config-file", "/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260904-f5257d24/aws-short-smoke-20260904.runtime.json", "--storage-base-dir", "/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260904-f5257d24/raw", "--environment-id", "aws-apne2-research", "--collector-epoch", "aws-short-smoke-20260904-f5257d24", "--run-id", "aws-short-smoke-run-20260903T160337Z-f5257d24", "--config-fingerprint", "48e5996f86567dfa41ed515de0e96fdb3230001fbc0ac2e0eb5453dad81422a0", "--runtime-commit", "5013728aacffcdc1b6faa84c19d8144287e25cde", "--lifecycle-status-path", "/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260904-f5257d24/collector-lifecycle.json"]",
  "--publisher-command-json",
  "["/var/lib/bitcoin-trader/venv-pre-soak/bin/python", "scripts/publish_collector_metrics.py", "--environment-id", "aws-apne2-research", "--region", "ap-northeast-2", "--metrics-path", "/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260904-f5257d24/collector_metrics.json", "--state-path", "/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260904-f5257d24/metric-publisher-state.json", "--storage-path", "/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260904-f5257d24/raw", "--ops-log", "/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260904-f5257d24/logs/metric-publisher-ops.jsonl"]",
  "--metrics-path",
  "/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260904-f5257d24/collector_metrics.json",
  "--collector-lifecycle-path",
  "/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260904-f5257d24/collector-lifecycle.json",
  "--result-path",
  "/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260904-f5257d24/result.json",
  "--log-path",
  "/var/lib/bitcoin-trader/short-smoke/aws-short-smoke-20260904-f5257d24/logs/supervisor.log",
  "--publisher-interval-seconds",
  "60",
  "--shutdown-grace-seconds",
  "45.0",
  "--require-full-duration"
]
```

- No enable, no timer, no cron.
- Restart=no, KillMode=mixed, RuntimeMaxSec=2760s, TimeoutStopSec=55s.

---

## 11. Post-Preflight Live Process & Storage State

- Collector: **0 (NOT STARTED)**
- WebSocket Collector: **0 (NOT STARTED)**
- Metric Publisher: **0 (NOT STARTED)**
- Archive Worker: **0 (NOT STARTED)**
- Actual Retry Transient Unit: **NOT RUNNING**
- New Epoch Raw Records: **NONE (0 Files)**
- S3 New Epoch Archive: **NONE (0 Objects)**
- Failed Old Epoch: **PRESERVED**

---

## 12. Candidate Execution Window (Future)

45분 숏스모크는 UTC 정시 경계 회전(`cross_utc_hour_required: true`)을 포함해야 하므로 `HH:40 UTC -> HH+1:25 UTC` 구간에서 실행되어야 합니다:

- **현재 시각:** `2026-09-04 02:26 UTC` (`11:26 KST`)
- **후보 윈도우 1:** `02:40 UTC ~ 03:25 UTC` (`11:40 KST ~ 12:25 KST`)
- **후보 윈도우 2:** `03:40 UTC ~ 04:25 UTC` (`12:40 KST ~ 13:25 KST`)

*(현재 작업에서는 절대 실행하지 않으며, 향후 별도 사용자 승인 시에만 실행됩니다.)*

---

## 13. Gate Decision & Next Phase

- **NEW EPOCH RESEAL:** **PASS**
- **IAM/PROVENANCE RECONCILIATION:** **PASS**
- **GUEST RETRY PREFLIGHT:** **PASS**
- **45M RETRY:** **NOT STARTED**
- **120M:** **NOT STARTED**
- **72H:** **NOT STARTED**
- **ALPHA:** **BLOCKED**
- **PAPER:** **NOT STARTED**
- **LIVE:** **DISABLED**
