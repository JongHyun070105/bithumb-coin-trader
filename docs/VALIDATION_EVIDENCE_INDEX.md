# Validation Evidence Index

## 1. 개요 (Overview)

본 문서는 `bitcoin-trader` 프로젝트의 수집기, 아카이버, 인프라 오케스트레이션 및 무결성 검증 이력을 시간순으로 집대성한 공식 증적 색인(Authoritative Validation Evidence Index)이다.
모든 검증 결과는 사후 수정이나 완화 없이 원천 판정을 엄격히 유지한다.

---

## 2. 검증 단계별 공식 증적 기록 (Validation Milestones)

| 검증 마일스톤 | 대상 환경 | 주요 커밋 / 런타임 | 최종 판정 | 핵심 증적 및 문서 링크 |
| :--- | :--- | :--- | :--- | :--- |
| **V9 Local 72H Soak** | 로컬 격리 환경 | PID 30933 / v9 baseline | **PASS** | [docs/STRATEGY_V9_72H_SOAK_AUDIT_REPORT_2026-08-29.md](file:///Users/macintosh/Documents/ChatGPT/bitcoin-trader/docs/STRATEGY_V9_72H_SOAK_AUDIT_REPORT_2026-08-29.md) |
| **V9.1 Local Stabilization** | 로컬 격리 환경 | v9.1 branch | **PASS** | [docs/V9_1_STABILIZATION_VALIDATION_2026-08-29.md](file:///Users/macintosh/Documents/ChatGPT/bitcoin-trader/docs/V9_1_STABILIZATION_VALIDATION_2026-08-29.md) |
| **AWS Pre-Soak Smoke** | AWS EC2 (t3.medium) | `i-008bc503c1136349f` | **PASS** | [docs/AWS_PRE_SOAK_DEPLOYMENT_SMOKE_2026-09-02.md](file:///Users/macintosh/Documents/ChatGPT/bitcoin-trader/docs/AWS_PRE_SOAK_DEPLOYMENT_SMOKE_2026-09-02.md) |
| **AWS 45M Short Smoke** | AWS EC2 (t3.medium) | `aws-short-smoke-20260904-b79...` | **PASS** | [docs/AWS_45M_RETRY_NEW_EPOCH_RESEAL_PREFLIGHT_2026-09-04.md](file:///Users/macintosh/Documents/ChatGPT/bitcoin-trader/docs/AWS_45M_RETRY_NEW_EPOCH_RESEAL_PREFLIGHT_2026-09-04.md) |
| **AWS 120M Validation** | AWS EC2 (t3.medium) | `aws-120m-soak-20260904-7a91176b` | **PASS WITH OPERATIONAL DEVIATION** | [docs/AWS_120M_VALIDATION_PLANNING_RESEAL_PREFLIGHT_2026-09-04.md](file:///Users/macintosh/Documents/ChatGPT/bitcoin-trader/docs/AWS_120M_VALIDATION_PLANNING_RESEAL_PREFLIGHT_2026-09-04.md) |
| **Post-120M Hardening** | 로컬 / AWS EC2 | `f98abcabbda45bc673702c7a66344a4dcff7299c` | **PASS** | 커널 flock 기반 전역 감독, detached setsid 타임아웃, PID 재사용 방지 및 원격 main 푸시 완료 |
| **72H Autonomous Preparation** | AWS EC2 (t3.medium) | `9532cebc902856d954bf80b51dbe567b543dc8e2` | **PASS** | EBS 200GiB 온라인 확장, XFS 파일시스템 확장, 게스트 런타임 배포, 디렉토리 권한 격리 완결 |
| **Current 72H Soak** | AWS EC2 (t3.medium) | `aws-72h-soak-20260904-43e79055` | **NOT STARTED / LAUNCH BLOCKED** | IAM Permissions Boundary v5 수동 관리자 승인 대기로 인한 엄격 Hard Gate 차단 (DO NOT LAUNCH 유지) |

---

## 3. 핵심 아티팩트 및 증적 상세 (Artifact Provenance)

### 3.1 120M Operational Deviation 내역
1. **05 UTC Archive 권한 충돌**:
   - 잔여 root:root / 0600 파일로 인해 비특권 사용자 접근 불가 발생 -> 운영자 chown 개입 후 재실행.
   - 사후 조치: 권한 화해 로직 및 비특권 격리 부트스트랩 스크립트 작성 완료.
2. **06 UTC Full-Scan 실행 경로 미완료**:
   - 아카이브 실행 경로에서 풀스캔 완료 전 수집기 종료 -> 고립된 transient systemd 서비스로 수동 재실행 후 최종 PASS 보고서 도출.
   - 사후 조치: 커널 flock 기반 `scripts/orchestrate_closed_hour_archive.py` 전역 감독 하드닝 적용.

### 3.2 72H 준비 및 하드 게이트 차단 상세
- **EBS 볼륨 확장**:
  - `vol-0d46ca4af0d463549` (100 GiB -> 200 GiB gp3) In-place 확장 완료.
  - 게스트 XFS 파일시스템 200G (194G 가용, 4% 사용률) 온라인 확장 성공.
- **차단 블로커 (Launch Blocker)**:
  - `IAM_BOUNDARY_V5`: `bitcoin-trader-provisioner` 및 `bootstrap` 역할의 바운더리 정책 버전 생성 권한 거부 (`AccessDenied`).
  - Permissions Boundary 정책(`v4`)이 여전히 120M 에포크 경로로 한정되어 있어 72H 에포크(`aws-72h-soak-20260904-43e79055`) S3 쓰기 거부됨.
  - 관리자/Root 수동 승인 전까지 수집기 기동을 차단하는 Fail-Closed 안전 규칙 엄격 준수.
