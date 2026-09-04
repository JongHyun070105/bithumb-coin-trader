# AWS 120-Minute Validation Planning, Reseal & Preflight — 2026-09-04

## 1. Executive Summary

- **Task Purpose:** 45분 마켓 수집기 재시도 100% 성공 이후, 차기 **120분 마켓 수집기 검증(`120M Validation`)**을 위한 **Planning / Reseal / Preflight Only** 준비 완료 및 안전 정지.
- **Strict Prohibition (실행 금지):** 이번 task에서 실제 120분 수집기 런칭(`--launch`), WebSocket 수집, CloudWatch 메트릭 루프, S3 신규 쓰기, raw cleanup, alpha, paper, live trading 절대 미실행 (0 Running Process 확인).
- **45M Successful Epoch:** `aws-short-smoke-20260904-f5257d24` (S3 76 객체, 532,847 레코드, 580,779,340 바이트) — **SUCCESS EVIDENCE로 100% 영구 보존**.
- **New 120M Epoch:** `aws-120m-validation-20260904-73d8e43c`
- **New 120M Run ID:** `aws-120m-validation-run-20260904T044655Z-73d8e43c`
- **Runtime Commit:** `5013728aacffcdc1b6faa84c19d8144287e25cde` (불변 유지)
- **New 120M Config Fingerprint:** `870b6a232e811332f90548e03b54e64608ad6b1a6869f2ffe650b19d51ea40eb` (로컬/게스트 교차 일치)
- **IAM Permissions Boundary:** 변경 필요 (**YES**), 현재 버전 수 3개 (`v1`, `v2`, `v3` / 한도 5개), Privileged IAM 승인 대기.
- **Terraform Plan Semantic Review:** **0 to add, 3 to change, 0 to destroy, 0 to replace** (토폴로지 변경 0, 프로비넌스 태그 및 인라인 정책만 갱신).
- **Guest Runtime Preflight:** **PASS** (Git CLEAN, 58/58 단위 테스트 PASS, Binance 443 10/10 PASS, 새 디렉터리 empty, 여유 디스크 97 GiB).
- **Status:** **READY FOR 120-MIN VALIDATION EXECUTION APPROVAL**

---

## 2. 45M Successful Epoch Preservation

- **보존 에포크 ID:** `aws-short-smoke-20260904-f5257d24`
- **보존 런 ID:** `aws-short-smoke-run-20260903T160337Z-f5257d24`
- **검증 실적:**
  - 45분 풀 듀레이션 정상 수집: 2,714.53초 실행 (`duration_limit_seconds: 2700.0`)
  - 종료 코드: Collector 0, Publisher 0, active_partition_files: `[]`
  - 무결성: WriterErrors: 0, QueueDrops: 0, Unpersisted: 0
  - 수집량: 총 532,847건 / 580,779,340 바이트
  - 03 UTC Closed Partition 아카이브/복원/FULL-SCAN: 76개 zstd1 파일 S3 업로드, 100% 무결성 일치 검증 통과 (`CLEANUP_ELIGIBLE: 76`, `FAILED: 0`)
  - 04 UTC 활성 파티션: 원본 RAW 상태로 안전 보존
- **보존 원칙:**
  - 재사용 금지 (Do not reuse)
  - 이름 변경 금지 (Do not rename)
  - 삭제 금지 (Do not delete)
  - 클린업 금지 (Do not cleanup)
  - 덮어쓰기 금지 (Do not overwrite)
  - 로컬 RAW / manifests / compressed / receipts / S3 objects 전수 영구 보존.

---

## 3. Provenance & New 120M Epoch Reseal

- **New Epoch ID:** `aws-120m-validation-20260904-73d8e43c`
- **New Run ID:** `aws-120m-validation-run-20260904T044655Z-73d8e43c` (Authoritative UTC 기반)
- **Runtime Code Commit:** `5013728aacffcdc1b6faa84c19d8144287e25cde` (기존 검증된 코드베이스 불변 유지, 소스 수정 불필요)
- **Runtime Seal File:**
  - 로컬: `infra/aws/seals/aws-120m-validation-20260904.runtime.json`
  - 게스트: `/var/lib/bitcoin-trader/120m-validation/aws-120m-validation-20260904-73d8e43c/aws-120m-validation-20260904.runtime.json` (권한: `0600`, 소유자: `bitcoin-trader:bitcoin-trader`)
- **주요 설정 불변식:**
  - `duration_seconds`: 7200 (120분)
  - `launch_mode`: `bounded-transient-systemd`
  - `collector_autostart`: `false`
  - `systemd_enable`: `false`
  - `public_data_only`: `true`
  - `private_api_enabled`: `false`
  - Feeds: Bithumb 20마켓, Binance 4심볼 (443 포트), Upbit 4마켓
  - Archive: temporary remote class, zstd level 1, worker concurrency 1, grace 600s, cleanup false
  - Metrics: publish cadence 60s, namespace `BitcoinTrader/Collector`
- **Canonical Config Fingerprint:**
  - **`870b6a232e811332f90548e03b54e64608ad6b1a6869f2ffe650b19d51ea40eb`**
  - 로컬 및 게스트 양쪽에서 Canonical JSON 직렬화 후 SHA-256 계산 결과 완벽 일치 확인.

---

## 4. IAM Permissions Boundary & Inline Policy Reconciliation Plan

### 4.1 Live Boundary 현황
- **Boundary ARN:** `arn:aws:iam::080109295433:policy/bitcoin-trader-collector-boundary`
- **현재 활성 기본 버전:** `v3` (`IsDefaultVersion: true`, 45M 에포크 허용 중)
- **현재 버전 목록 및 개수:**
  - `v1`: 2026-08-30 생성 (`aws-v91-clean-soak-20260830`)
  - `v2`: 2026-09-03 생성 (`aws-short-smoke-20260902-38cb8a72`)
  - `v3`: 2026-09-04 생성 (`aws-short-smoke-20260904-f5257d24`, 현재 Default)
  - **총 버전 수: 3개** (AWS IAM Policy 버전 한도 5개 대비 2개 여유 공간 확보)
  - 따라서 신규 `v4` 버전 생성 시 기존 구 버전을 삭제할 필요 없음.

### 4.2 Reconciliation Plan
- **Boundary 변경 필요 여부:** **YES** (새 120M 에포크 S3 prefix 허용을 위해 `v4` 필요)
- **Privileged IAM 실행 방침:** Root/browser privileged IAM(`hermes-aws`)은 자동 실행하지 않으며, 향후 **사용자의 명시적 승인 후 일회성 생성**.
- **Candidate Policy JSON:**
  - 템플릿: `infra/aws/identity/collector-permissions-boundary.json.example`
  - Bucket: `bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433`
  - Account: `080109295433`
  - Epoch: `aws-120m-validation-20260904-73d8e43c`
  - Candidate SHA-256: `38265a583d7381d819b85189c604798efafab57aab55466554d287105dfc3e6e`
  - Access Analyzer 사전 검증: **findings 0 (ERROR: 0, SECURITY_WARNING: 0)**
- **Reconciliation 후 권한 교차 매트릭스 (기대값):**
  - NEW 120M Temporary Prefix: **ALLOW**
  - NEW 120M Canonical Prefix: **ALLOW**
  - 45M Success Epoch (`aws-short-smoke-20260904-f5257d24`): **DENY** (Collector Role 쓰기 차단으로 보존)
  - 구 실패 Epochs: **DENY**
  - DeleteObject: **DENY** (영구 차단)
  - CloudWatch 네임스페이스 `BitcoinTrader/Collector`: **ALLOW**
  - CloudWatch 타 네임스페이스: **DENY**

---

## 5. Terraform Semantic Review

`bitcoin-trader-provisioner` 권한 하에 `terraform plan` 사전 시뮬레이션 수행 결과:

- **Plan Command:**
  ```bash
  terraform -chdir=infra/aws plan \
    -var="ami_id_override=ami-08d82cf148c92fcc3" \
    -var="availability_zone=ap-northeast-2a" \
    -var="collector_epoch=aws-120m-validation-20260904-73d8e43c" \
    -var="collector_run_id=aws-120m-validation-run-20260904T044655Z-73d8e43c" \
    -var="collector_git_commit=5013728aacffcdc1b6faa84c19d8144287e25cde" \
    -var="collector_config_fingerprint=870b6a232e811332f90548e03b54e64608ad6b1a6869f2ffe650b19d51ea40eb"
  ```
- **Plan 결과 요약:**
  - **ADD:** **0**
  - **CHANGE:** **3** (`aws_iam_role.collector`, `aws_iam_role_policy.collector`, `aws_instance.collector`)
  - **DESTROY:** **0**
  - **REPLACE:** **0**
- **Semantic Change 항목:**
  1. `aws_iam_role.collector`: 태그 `CollectorEpoch`, `CollectorRunId`, `ConfigFingerprint` 갱신 (in-place update)
  2. `aws_iam_role_policy.collector`: 인라인 정책 내 S3 prefix를 `aws-120m-validation-20260904-73d8e43c`로 갱신 (in-place update)
  3. `aws_instance.collector`: 인스턴스 태그 및 Root EBS 태그 `CollectorEpoch`, `CollectorRunId`, `ConfigFingerprint` 갱신 (in-place update)
- **Topology 불변 검증:**
  - AMI: `ami-08d82cf148c92fcc3` (불변)
  - Instance Type: `t3.medium` (불변)
  - Root EBS: 100 GiB gp3 (불변)
  - VPC / Subnet / Security Group: 불변
  - S3 Bucket Controls (PublicAccessBlock, Encryption, Policy): 불변
  - CloudWatch Metric Alarms: 불변

---

## 6. Guest Runtime Preflight & Diagnostic Results

EC2 게스트 인스턴스 (`i-008bc503c1136349f`, ap-northeast-2a) 대상 라이브 점검:

1. **소프트웨어 및 저장소 무결성:**
   - Working Directory: `/var/lib/bitcoin-trader/remediation-650adc8`
   - Git HEAD: `5013728aacffcdc1b6faa84c19d8144287e25cde` (MATCH)
   - Git Tree: **CLEAN** (Untracked: 0, Modified: 0)
   - Python 런타임: `Python 3.11.16` (venv: `/var/lib/bitcoin-trader/venv-pre-soak`)
2. **타겟 단위 테스트 스위트:**
   - **58 / 58 PASS (2.548s)**
   - `test_cross_market_collector.py`: PASS
   - `test_bounded_supervisor.py`: PASS
   - `test_transient_launch.py`: PASS
   - `test_binance_diagnostic.py`: PASS
   - `test_pre_soak_archive.py`: PASS
   - `test_short_smoke_runtime_config.py`: PASS
3. **Binance WebSocket 443 포트 종합 진단 (`diagnose_binance_websocket.py`):**
   - DNS IPv4 조회: 8개 IP 정상 반환
   - TCP 443 Handshake: 8/8 PASS (~32ms)
   - TLS 1.3 Handshake: 8/8 PASS (~43ms)
   - 4개 심볼 (BTC, ETH, SOL, XRP) & 프로덕션 복합 스트림: **10/10 WebSocket PASS (~110-123ms)**
   - 코드베이스 내 9443 엔드포인트: **0 (전무)**
4. **New 120M Epoch 저장소 상태:**
   - 기본 경로: `/var/lib/bitcoin-trader/120m-validation/aws-120m-validation-20260904-73d8e43c/`
   - 하위 디렉터리 파일 개수:
     - `raw`: **0**
     - `manifests`: **0**
     - `compressed`: **0**
     - `archive-receipts`: **0**
     - `logs`: **0**
   - 상태 파일:
     - `collector_metrics.json`: **ABSENT**
     - `result.json`: **ABSENT**
     - `metric-publisher-state.json`: **ABSENT**
5. **EBS 디스크 용량 추정 및 여유 공간 점검:**
   - 마운트: `/dev/nvme0n1p1` (`/`)
   - 전체 용량: `100 GiB`, 사용량: `3.7 GiB`, **가용 용량: `97 GiB` (사용률 4%)**
   - 45분 실측 데이터량: ~580.8 MB
   - 120분 선형 추정 데이터량: 약 1.55 GB raw + 0.05 GB zstd = **~1.6 GB**
   - 가용 공간(97 GiB) 대비 예상 점유율: **~1.6% (극히 여유로움)**
   - 결론: EBS resize 불필요.

---

## 7. 120M Execution & Archive Validation Window Design

### 7.1 실행 윈도우 설계 (`cross_utc_hour_required: true`)
120분 실행은 최소 2회의 UTC 정시 경계 회전(Cross-Hour Rotation)을 통과하도록 `HH:40 UTC → HH+2:40 UTC` 패턴으로 설계:

- **후보 윈도우 1:** `05:40 UTC ~ 07:40 UTC` (`14:40 KST ~ 16:40 KST`)
  - 경계 1: `06:00 UTC` 통과
  - 경계 2: `07:00 UTC` 통과
- **후보 윈도우 2:** `06:40 UTC ~ 08:40 UTC` (`15:40 KST ~ 17:40 KST`)
  - 경계 1: `07:00 UTC` 통과
  - 경계 2: `08:00 UTC` 통과

### 7.2 구간별 아카이브 검증 전략
- **1st Partial Hour (예: 05:40 ~ 06:00 UTC, 20분간 수집):**
  - `06:00 UTC` 경계 회전 시 파티션 닫힘(closed).
  - +600s 안정성 유예 기간(grace period) 경과 후(`06:10 UTC 이후`) 아카이브 검증 수행.
  - 검증 항목: manifest, zstd1 압축, temporary S3 업로드, If-None-Match 조건부 쓰기, HEAD ChecksumSHA256/Content-Length/VersionId, streaming restore, raw SHA256 일치성, FULL-SCAN.
- **2nd Middle Full Hour (예: 06:00 ~ 07:00 UTC, 60분간 수집):**
  - `07:00 UTC` 경계 회전 시 파티션 닫힘(closed).
  - +600s 안정성 유예 기간 경과 후(`07:10 UTC 이후`) 동일 아카이브 검증 수행.
- **3rd Partial Hour (예: 07:00 ~ 07:40 UTC, 40분간 수집):**
  - 런 진행 중에는 활성(active) 상태 유지 -> **아카이브 대상에서 엄격히 제외**.
  - `07:40 UTC` 수집기 자연 종료 시점까지 RAW 상태 유지 및 종료 시점에 안전 보존.
- **클린업 방침:** Cleanup OFF (`cleanup_enabled: false`), DeleteObject 권한 부재(DENY) 유지.

---

## 8. Process Supervision & Transient Launch Render Specification

향후 별도 사용자 승인 시 게스트에서 일회성으로 등록될 systemd transient 유닛 규격:

- **Unit Name:** `bitcoin-trader-120m-aws-120m-validation-run-20260904T044655Z-73d8e43c.service`
- **Supervisor Parameters:**
  - `RuntimeMaxSec`: `7260s` (수집 7200초 + 셧다운 마진 60초)
  - `TimeoutStopSec`: `55s`
  - `Restart`: `no`
  - `KillMode`: `mixed`
  - `systemd_enable`: `false` (부팅 시 자동 시작 절대 금지)
  - 타이머 / 크론 / 루프 서비스: **전무 (0)**
- **하위 프로세스 라이프사이클:**
  - Collector: `duration: 7200` 자연 종료
  - Publisher: Collector 라이프사이클에 완전 종속되어 Collector 종료 후 최종 상태 전송 후 동반 종료.

---

## 9. Current Process & Market Data State (Idle Verification)

- **Collector 프로세스:** **0 (NOT STARTED)**
- **Publisher 프로세스:** **0 (NOT STARTED)**
- **Archive Worker 프로세스:** **0 (NOT STARTED)**
- **Supervisor 프로세스:** **0 (NOT STARTED)**
- **Active Systemd Units:** **0 loaded units**
- **New Epoch Market Records:** **NONE (0 Files / 0 Bytes)**
- **S3 New Epoch Objects:** **NONE (0 Objects)**
- **45M Success Epoch:** **PRESERVED**

---

## 10. Gate Decision & Safety Posture

| 항목 | 현재 판정 | 비고 |
|---|---|---|
| 45M Short-Smoke Retry | **PASS** | 532,847 레코드, 03 UTC Closed Archive 76개 완벽 검증 및 보존 |
| 120M Reseal | **PASS** | 새 에포크/런 ID 생성, 120M Seal 배포, Fingerprint 일치 |
| 120M IAM/Provenance Plan | **PASS** | Boundary v4 계획 수립, Terraform Plan 0 Add / 3 Change / 0 Destroy 확인 |
| 120M Guest Preflight | **PASS** | Git CLEAN, 58/58 Tests PASS, Binance 443 10/10 PASS, Empty Dirs |
| 120M Execution | **NOT STARTED** | 사용자 명시적 승인 대기 중 |
| 72H Soak | **NOT STARTED** | 120M 완료 전 착수 금지 |
| Alpha | **BLOCKED** | 비활성화 |
| Paper | **NOT STARTED** | 비활성화 |
| Live | **DISABLED** | 비활성화 |
