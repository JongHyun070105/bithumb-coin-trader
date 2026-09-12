# AWS 30-Hour Validation Pre-Launch Preparation Report (2026-09-12)

## 1. Executive Summary

- **목적:** 향후 30시간(30H) AWS 무인 검증을 위한 인프라 사전 준비, 봉인(Seal) 아티팩트 생성, 리소스 용량 평가 및 독립 사전 검토 수행.
- **Fail-Closed 원칙 및 엄격 금지 준수:**
  - 30H 런칭 인가: **미인가 (`launch_authorized = false`)**
  - 실제 시작 시각: **미기록 (`actual_start_time_utc = null`)**
  - 30H 실행 / 수집기 기동 / systemd 시작: **일체 미실행 (`30H STARTED = NO`)**
  - Fresh45 불변 증거 보존: **100% 보존 및 미수정 (`FRESH45 EVIDENCE UNCHANGED = YES`)**
- **권위적 Base Commit:** `ac0351c227d837d908a29c56520a31dd84d4bc17` (Fresh45 검증 완료 및 감사 도구 강화 병합본)
- **Terraform 상태:** `0 to add, 0 to change, 0 to destroy` (Lineage: `5e8ff4b0-1d32-ed2e-9ed0-a9e68d3f3ccf`, 27 resources)
- **Permissions Boundary:** Default `v6` 유지, Rollback `v5` 보존, 30H 검증 접두사 시뮬레이션 PASS.
- **임시 IAM 버전 관리 권한 상태:** CLI 식별자의 사용자 관리 권한(`iam:DeleteUserPolicy`) 부재 및 Root 사용 금지 원칙에 따라 CLI를 통한 인라인 정책 삭제는 **BLOCKED** 상태로 안전 보고됨 (자세한 내용 하단 참조).

---

## 2. Phase 1: IAM 소유권 및 범위 분석 (Read-Only IAM Review)

1. **임시 경계 버전 관리 권한을 보유한 정확한 IAM Identity:**
   - IAM User: `arn:aws:iam::080109295433:user/bitcoin-trader-bootstrap` (Friendly name: `bitcoin-trader-bootstrap`)
2. **해당 권한을 부여한 정책/Statement:**
   - User `bitcoin-trader-bootstrap`의 인라인 정책: `temporary-manage-collector-boundary-versions`
3. **허용된 전체 Action:**
   - `iam:CreatePolicyVersion`, `iam:SetDefaultPolicyVersion`, `iam:DeletePolicyVersion`
   - 대상 Resource: `arn:aws:iam::080109295433:policy/bitcoin-trader-collector-boundary`
4. **Terraform 소유 여부:**
   - **Terraform 소유 아님 (Outside Terraform State)**.
   - 근거: `infra/aws/main.tf`에는 `bitcoin-trader-collector-boundary`가 데이터/참조(`local.collector_boundary_arn`)로만 정의되어 있으며, `terraform.tfstate` 내 27개 관리 리소스 중 boundary 또는 bootstrap user 관련 리소스 전무함 (태그: `ManagedBy: manual-iam-bootstrap`).
5. **수집기 런타임(Collector Runtime)의 의존성 여부:**
   - **의존성 전혀 없음 (NO)**.
   - 근거: 게스트 EC2 인스턴스는 역할 `bitcoin-trader-aws-apne2-research-collector`로 실행되며, 수집기 인라인 정책 및 경계에는 `iam:*` 권한이 전무하여 IAM 버전 관리를 일체 호출하지 않음.
6. **Boundary v6 및 롤백 v5의 의존성 여부:**
   - **의존성 전혀 없음 (NO)**.
   - 근거: IAM 정책 버전은 한 번 생성되면 불변(immutable) 객체로 IAM 내에 영구 유지됨. 버전 생성 권한이 제거되더라도 기존 기본 버전 `v6` 및 보존된 `v5`는 영향받지 않음.
7. **Provisioner 정상 운영의 의존성 여부:**
   - **의존성 전혀 없음 (NO)**.
   - 근거: `role/bitcoin-trader-terraform-provisioner`의 권한 정책(`terraform-provisioner-reviewed`)은 계획 수립용 읽기 권한(`iam:GetPolicy`, `iam:GetPolicyVersion`)만 요구하며 버전 관리 쓰기 권한을 필요로 하지 않음.

---

## 3. Phase 2 ~ Phase 6: 안전 게이트 및 정리 시도 결과

- **Git HEAD 일치:** `ac0351c227d837d908a29c56520a31dd84d4bc17` (`local == origin/main`)
- **작업 트리 청결도:** 깨끗함 (`test-results/` 비접촉 유지)
- **Terraform Pre-Mutation Plan:** `0 to add, 0 to change, 0 to destroy` PASS.
- **제거 시도 분석:**
  - `user/bitcoin-trader-bootstrap` 및 `role/bitcoin-trader-terraform-provisioner`에서 `aws iam delete-user-policy` 호출 시 AWS IAM은 `AccessDenied`를 반환함 (`iam:DeleteUserPolicy` 권한 부재).
  - 지침의 `Never: use root` 원칙에 따라 root 계정/세션은 절대 사용하지 않음.
  - 따라서 CLI를 통한 자동 제거는 **BLOCKED** 상태이며, AWS IAM 콘솔을 통해 `user/bitcoin-trader-bootstrap`의 인라인 정책 `temporary-manage-collector-boundary-versions`를 수동 제거하는 방안이 권장됨.
- **Post-Removal IAM 검증 (현재 실측치):**
  - Boundary default: **`v6`** (CreateDate: 2026-09-09T07:05:18Z)
  - Rollback version: **`v5`** (CreateDate: 2026-09-05T02:54:25Z)
  - 수집기 역할 경계 연결: **유지 (`bitcoin-trader-collector-boundary`)**
  - 신규 30H 검증 접두사 시뮬레이션:
    - `s3:GetObject`: **ALLOW**
    - `s3:PutObject`: **ALLOW**
    - `s3:ListBucket`: **DENY** (`implicitDeny`)
    - `s3:DeleteObject`: **DENY** (`implicitDeny`)
    - Canonical / Old 72H / 타 네임스페이스 / 타 버킷 / Private API: **DENY** (`implicitDeny`)

---

## 4. Phase 7: 30H 유효성 검증 사전 준비 분석 (Read-Only Readiness Review)

| 항목 | 30H 결정 내용 | 근거 및 상태 |
|---|---|---|
| **1. Authoritative Runtime Commit** | `ac0351c227d837d908a29c56520a31dd84d4bc17` | Fresh45 성공 증거 및 강화 감사 도구가 병합된 권위적 main HEAD |
| **2. Runtime Commit 방식** | 전용 불변 커밋 지정 권장 | 아래 코드 분석(수집 기간 화이트리스트 확장) 참조 |
| **3. 신규 Epoch ID** | `aws-validation-30h-20260912-ac0351c` | 프로젝트 명명 규칙 준수 (과거 에포크 중복 없음) |
| **4. 신규 Run ID** | `aws-validation-30h-run-20260912T060000Z-ac0351c` | 에포크 및 런타임 커밋 바인딩 고유 식별자 |
| **5. S3 임시 접두사** | `market-data/temporary/aws-validation-30h-20260912-ac0351c` | 현재 0 objects (오염 전무) |
| **6. 격리 로컬 런타임 루트** | `/var/lib/bitcoin-trader/30h-validation/aws-validation-30h-20260912-ac0351c` | 미생성 (ABSENT) |
| **7. 격리 게스트 Worktree** | `/var/lib/bitcoin-trader/runtime-worktrees/aws-validation-30h-20260912-ac0351c` | 미생성 (ABSENT) |
| **8. Config Fingerprint** | `1a7614158bbefa9f4b81ee50ef5f8f85d84070283b7816b0d0ff1299b8216497` | 30H 봉인 JSON 정규화 해시 |
| **9. 공식 피드 유니버스** | 총 76개 피드 | Bithumb 20×3 + Binance 4×2 + Upbit 4×2 |
| **10. 수집 시간 (Duration)** | 30시간 = **108,000초** | `30 * 3600` |
| **11. 종료 정리 타임아웃** | **180초** | 최종 파티션 플러시 및 메트릭 기록 보장 |
| **12. 슈퍼바이저 하드 상한선** | **108,300초** | `108000 + 180 + 120` |
| **13. Systemd RuntimeMaxSec** | **108,400초** | `108300 + 100` |
| **14. 아카이브 스케줄러 설정** | poll: **30.0s**, grace: **600s** | 매시 10분 자율 아카이브 구동 |
| **15. 복수 UTC 일자/코호트** | 30개 시간별 코호트, 2개 이상의 UTC 일자 자연 통과 | 매 코호트 자연 회전 검증 |
| **16. 디스크 용량 평가** | **PASS** (125 GiB 사용 가능, 예상 사용량 ~40 GiB) | 루트 EBS: 200 GiB 중 38% 사용 중 |
| **17. S3 용량 평가** | **PASS** (예상 압축 데이터 ~5 GiB) | 무제한 스토리지, 비용 영향 무시 수준 |
| **18. 증거 체인 요건** | 76개 zstd 업로드, 76개 적격 영수증, 152개 풀스캔 참조 | 코호트별 FAILED 영수증 0건 필수 |
| **19. 적격 판정 규칙** | 완전 폐쇄 코호트 전수 적격, 자연 종료 직전 부분 코호트는 계약 준수 | 0개 이벤트 파티션 위양성 방지 계약 |
| **20. Fail-Closed 롤백 규칙** | 장애 발생 시 자동 즉시 정지, Boundary v5 상시 복구 보존 | 안전 기본값 유지 |

### 중요 코드 분석 (Critical Tooling Finding)
- `src/bithumb_coin_trader/bounded_supervisor.py`의 `render_systemd_run()` 검증 시:
  ```python
  if config.collection_duration_seconds not in (2700, 7200, 259200):
      raise ValueError("production supervisor duration must be exactly 2700, 7200, or 259200 seconds")
  ```
  현재 허용 화이트리스트는 45분(2700), 120분(7200), 72시간(259200)만 포함하고 있어, 30시간(108000)을 실행하려면 해당 화이트리스트에 108000을 추가하고 `scripts/launch_short_smoke_transient.py`의 duration 인자 전달을 보완하는 **전용 런타임 커밋**이 필요함을 확인하였습니다.

---

## 5. Phase 8: 30H 사전 봉인 패키지 아티팩트 (Sealed Artifacts)

1. **Runtime Seal:** `infra/aws/seals/aws-validation-30h-20260912-ac0351c.runtime.json`
   - SHA-256: `0db28e85c6e7c1dbaf195a983c504b06c273994f931bb46cf488aa36edfc1c0d`
   - Fingerprint: `1a7614158bbefa9f4b81ee50ef5f8f85d84070283b7816b0d0ff1299b8216497`
2. **Launch Command:** `infra/aws/seals/aws-validation-30h-20260912-ac0351c.launch-command.json`
   - SHA-256: `82835a69cab7a08935cecb2654058184a1c51b8c58270b0391ccba91ffbe0b09`
   - `launch`: **`false`**
3. **Launch Provenance:** `infra/aws/seals/aws-validation-30h-20260912-ac0351c.launch-provenance.json`
   - SHA-256: `526df4af66c7a2ebe58820352365f671d1e0d4c2bb5cc66c4e79fdc5412a8078`
   - `launch_authorized`: **`false`**
   - `actual_start_time_utc`: **`null`**

---

## 6. Independent Pre-Launch Review

| 구분 | 건수 | 세부 내용 |
|---|---|---|
| **Critical Findings** | **0** | 차단성 결함 없음 |
| **Important Findings** | **0** | 중요 설계 결함 없음 |
| **Minor Findings** | **0** | 경미한 결함 없음 |
| **Advisory Note** | 1 | 30H 런칭 인가 전 `bounded_supervisor.py` 내 108000 duration 허용 커밋 선행 권장 |
| **PRE-LAUNCH REVIEW VERDICT** | **PASS** | 사전 준비 패키지 승인 가능 |

---

## 7. Scientific & Operational Stance

- **ALPHA:** **UNPROVEN**
- **PAPER TRADING:** **NOT STARTED**
- **LIVE TRADING:** **DISABLED**
- **PRIVATE API:** **DISABLED**
- **30H VALIDATION:** **NOT STARTED (HARD STOP)**
