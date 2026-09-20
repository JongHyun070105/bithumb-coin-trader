# Validation Evidence Index

## 1. 개요 (Overview)

본 문서는 `bitcoin-trader` 프로젝트의 수집기, 아카이버, 인프라 오케스트레이션 및 무결성 검증 이력을 시간순으로 집대성한 공식 증적 색인(Authoritative Validation Evidence Index)이다.
모든 검증 결과는 사후 수정이나 완화 없이 원천 판정을 엄격히 유지한다.
증적 권위 모델(§4)·증적 클래스(§5)는 각 증적이 무엇을 증명하고 무엇을 증명하지 않는지를 정의한다. 현재 활성 검증은 Fresh 신뢰성 사다리(§6)이며, 여기서 30H-v2는 RUNNING/PENDING(미검증)이다. 인프라 수집 신뢰성은 연구 유효성이나 거래 알파(profitable alpha)를 증명하지 않는다.

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

> ※ 위 표는 **V4-era(2026-09-02~09-04) 캠페인**의 기록이다. 2026-09-04 기준 `Current 72H Soak`은 IAM boundary 블로커로 인해 발사되지 않았다. 현재 활성 캠페인은 아래 §6 Fresh 신뢰성 사다리이며, 활성 런은 `Fresh 30H-v2 (RUNNING / PENDING)`이다.

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

---

## 4. 증적 권위 모델 (Evidence Authority Model)

검증 판정은 증적 규칙 기반으로 하며, **낮은 권위 소스는 높은 권위 증적과 충돌할 경우 결코 대체할 수 없다**.

| 순위 | 증적 유형 | 권위 이유 |
| :--- | :--- | :--- |
| 1 | Immutable terminal/runtime evidence (`result.json` + SHA-verified immutability hashes, time-separated cohort receipt snapshots) | 실행 종료 시점의 원천 상태를 체크섬으로 고정 |
| 2 | Finalized cohort receipts + time-separated immutability hashes (`cohort_<hour>_finalized.json` 1차/2차 관측 SHA256 비교) | 코호트별 바인딩·불변성 입증 |
| 3 | Canonical archived RAW / coverage evidence on S3 (raw JSONL.ZST, coverage.json) | 실제 수집된 데이터 존재 |
| 4 | Committed post-run audit reports (`*_RUN_AUDIT_REPORT.md`, `*_EVIDENCE_SUMMARY.json` in `reliability-artifacts/`) | 변하기 쉬운 진술도 Git에 커밋되어 추적 가능 |
| 5 | Sealed launch identity/configuration artifacts (`identity.json`, `sealed-manifest.json`) | **구성/계획**만 증명 — 실행/합격 증명 아님 |
| 6 | Human / agent narrative (handoff JSON, overnight 보고서) | 근거 없는 PENDING→PASS 전환 금지 |

> **핵심 원칙**: sealed launch artifact는 실행/종료/합격을 증명하지 않는다. 3h-v1처럼 terminal 증거가 Git에 커밋된 경우를 제외하고, 6h-v3/30h-v2의 terminal/runtime 증거는 현재 Git에 커밋되지 않고 EC2/S3 런타임 증거에 보관된다.

---

## 5. 증적 클래스 (Evidence Classes)

| 클래스 | 무엇을 증명한다 | 무엇을 증명하지 않는다 |
| :--- | :--- | :--- |
| `SEALED_IDENTITY` | 런타임 커밋/트리, S3 prefix 사전 공백, pre-launch gate(EBS, 권한, 이전 프로세스 종료) | 실행 시작/수집/종료/합격 |
| `START_EVIDENCE` | observer/collector/scheduler 시작 시각, systemd unit 활성 | 지속적 건강, 코호트 완료, terminal |
| `RUNTIME_HEALTH` | RSS, watchdog NRestarts, resource stability, queue depth | 코호트 finalize, receipt 불변성, natural termination |
| `RAW_DATA` | raw market-data 파일 존재(수집량) | 정합성/아카이브/불변성 |
| `COVERAGE` | 피드별 coverage.json (시간/피드 커버리지) | raw 데이터의 무결성/checksum |
| `FINALIZED_COHORT_RECEIPT` | `status`, `data_present_count`, `failed_count`, `finalized_at_utc` | receipt가 후에 rewrite 되었는지(별도 검증 필요) |
| `RECEIPT_IMMUTABILITY` | 1차/2차 관측 간 SHA256 동일성 | receipt가 처음부터 올바졌는지(origin 검증 필요) |
| `TERMINAL_WITNESS` | `result.json`(exit codes, `overall_status`, `ended_at`, `full_duration_satisfied`, `final_manifest_flush_observed`, `final_metrics_valid`) | profitability/alpha/research validity |
| `POST_RUN_AUDIT` | 커밋된 `*_RUN_AUDIT_REPORT.md`(인간 판독 가능한 종합) | 자체 권위(표준 1~4가 우선) |
| `NARRATIVE_ONLY` | handoff JSON, overnight 보고서(agent 진술) | 증거(표준 1~4가 우선) |

---

## 6. 현재 신뢰성 사다리 (Current Reliability Ladder, Fresh era 2026-09-17 → 2026-09-19)

표준 규칙: `SUPERSEDED`/`승계`는 판정이 아니다. 원래 FAIL은 그대로 보존하며, remediation은 별도 기술한다.

| Run | 기술 판정 (Technical) | 거버넌스 (Governance) | 런타임 커밋 | Git 증적 | External 증적 (EC2/S3) | 증적 완비도 | 비고 |
| :--- | :---: | :---: | :--- | :--- | :--- | :--- | :--- |
| **Fresh 3H-v1** (e752c3d) | PASS | Clean | `e752c3d` | `3H_RUN_AUDIT_REPORT.md`+`terminal/result.json`+sealed | — | FULL | 2,247,583건, 2코호트×76피드 0실패, exit 0, NRestarts 0, terminal committed in Git |
| **Fresh 6H-v1** (e752c3d) | FAIL | — | `e752c3d` | sealed manifest only (`aws-6h-v1/`) | runtime result/cohort receipts | LAUNCH_ONLY | 4/5 코호트 PASS, 최종 코호트 실패 → `c033a85` 완화. terminal audit 미커밋 |
| **Fresh 6H-v2** (c033a85) | FAIL | — | `c033a85` | sealed manifest only (`aws-6h-v2/`) | runtime result/cohort receipts | LAUNCH_ONLY | 최종 코호트 8개 Binance 피드 `COLLECTION_GAP`/`LATE_CONFIRMATION`, receipt FAIL → `4fcdd819` 완화. terminal audit 미커밋 |
| **Fresh 6H-v3** (4fcdd819) | TECHNICAL PASS | Clean | `4fcdd819` | sealed manifest only (`aws-6h-v3/`) | result.json, cohort receipts, immutability hashes, metrics (S3 temporary) | GIT_CONFIG_ONLY | 5/5 코호트, 380/380 슬롯, 0 failed feeds, 7,287,432건, `CLEAN_SUCCESS`, exit 0, `archive_settled_utc=2026-09-19T08:13:00Z` |
| **Fresh 30H-v1** (4fcdd819) | PRECOLLECTOR_GATING_ABORT | — | `4fcdd819` | sealed manifest only (`aws-30h-v1/`) | — | LAUNCH_CONFIG_ONLY | #8 attempt consumed, T0 이전 기동 중단. `OBSERVER/COLLECTOR/SCHEDULER = NO`, `LOCAL_DATA_ROOT=ABSENT`, `S3_PREFIX_OBJECT_COUNT=0` |
| **Fresh 30H-v2** (4fcdd819) | RUNNING / PENDING | — | `4fcdd819` | sealed manifest only (`aws-30h-v2/`) | runtime metrics + terminal (진행 중, S3) | LAUNCHED_RUNNING | T0 `2026-09-19T09:50 UTC`, 29 코호트, 108,000s(30h) 수집 중. 현재 코호트 결과 미공개 |

- **Fresh 3H-v1** (런타임 `e752c3d`, tree `f91bc6abcb4b6efae652481405e4ce2893c6238a`, sealed `9a1e2cc`): 10,800s 무중단 수집, 2,247,583건(Binance 1,223,730 / Bithumb 730,329 / Upbit 293,524), 2적격 코호트×76피드=152슬롯 0실패, S3 448 objects. collector/스케줄러/퍼블리셔 exit code 0, `NRestarts = 0`, `full_duration_satisfied = true`, `overall_status = PASS`. 거버넌스 격리(`User=bitcoin-trader`) PASS. Git에 terminal audit + result.json 커밋 존재(FULL).
- **Fresh 6H-v1/v2**: 각각 sealed manifest만 Git에 존재. 6H-v1은 코호트 경계 manifest 최종화 이벤트 루프 스탈(`c033a85` 완화), 6H-v2는 websocket ping-timeout·segment-gap 오류(`4fcdd819` 완화), 최종적으로 6H-v1/6H-v2는 **FAIL**로 영구 보존.
- **Fresh 6H-v3** (런타임 `4fcdd819366918fa86e5597ed7d2271454d926c7`, tree `68dab6731f52e1aca51237410ea9f7b469869086`, sealed `7cd1b0f`): 21,600s, 5 적격 코호트×76피드=380슬롯, 0 failed feeds, receipt immutability 5/5, terminal witness `CLEAN_SUCCESS`, exit code 0, `NRestarts = 0`, `full_duration_satisfied = true`, 검증 레코드 7,287,432건. Git에는 sealed launch manifest만 존재하고, terminal/runtime 증거는 EC2/S3(`market-data/temporary/aws-observability-6h-…-v3/`)에 보관됨. `4fcdd819`는 현재 develop의 최신 **코드** 커밋이다(이후 아티팩트 봉인 커밋만 추가 존재).
- **Fresh 30H-v2**: 동일 검증 런타임(`4fcdd819`) 재사용. 29 적격 풀 아워(29코호트×76피드=2,204슬롯), 108,000초(30h) 수집 중 (T0 `2026-09-19T09:50 UTC`). **PENDING**(미검증): terminal/runtime 증거는 EC2/S3에 보관 중이며, 종료 및 terminal 검증을 완료한 뒤에야 종합 합격(technical PASS) 결과를 보고한다. 현재 코호트 결과는 공개하지 않는다.

---

## 7. 런칭 회계 (Launch Accounting)

- **현재 최종 (final authorization)**:
  - `GLOBAL_NEW_AWS_LAUNCH_CAP = 10`
  - `GLOBAL_NEW_AWS_LAUNCHES_USED = 10 / 10`
  - `GLOBAL_NEW_AWS_LAUNCHES_REMAINING = 0`
  - #9 = Fresh 6H-v3 (TECHNICAL PASS)
  - #10 = Fresh 30H-v2 (RUNNING / PENDING)
  - #11 없음
- **역사적(참고 only, 현재 상한 아님)**: V8 시점 cap=6(#1 V8 … #5 3H-v1, #6 6h 예약); 6h-v2 준비 시점 cap=8(6h-v1=#6, 6h-v2=#7, 30h=#8 조건부). 6H-v1(#6)·6H-v2(#7)·30H-v1(#8)·6H-v3(#9)·30H-v2(#10) 순서로 소진.

---

## 8. 증적 해석 규칙 (Evidence Interpretation Rules)

하나의 증적 단계가 그 위 단계를 암묵하지 않는다:

- `SEALED != STARTED` — launch manifest는 기동을 증명하지 않는다.
- `SEALED != TERMINAL_PASS` — launch manifest는 종료나 종합 합격(technical PASS)을 증명하지 않는다(6H-v3는 sealed launch manifest만 Git에 커밋되고, terminal 증거는 별도로 EC2/S3에 보관됨).
- `STARTED != HEALTHY` — 프로세스 시작은 지속적 수집 건전성을 증명하지 않는다.
- `HEALTHY != COHORT_PASS` — 리소스 안정성은 코호트 아카이브 최종화를 증명하지 않는다.
- `COHORT_PASS != TERMINAL_PASS` — 개별 코호트 합격은 전체 terminal 종료를 증명하지 않는다.
- `INFRA_PASS != ALPHA` — 인프라 수집 신뢰성은 수익 알파를 증명하지 않는다.
- `ALPHA_RESEARCH != PAPER`
- `PAPER != LIVE`
- `PENDING != PASS` — 30H-v2는 PENDING이며, 종료·terminal 검증 전까지 PASS로 표기하지 않는다.
- `FAIL ≠ SUPERSEDED` — 6H-v1/6H-v2는 기계적으로 SUPERSEDED로 재작성하지 않는다. 원래 FAIL 진단을 보존하고, remediation을 별도로 기술한다.

---

## 9. 현재 안전 상태 (Current Safety State)

- **ALPHA** = **UNPROVEN** (검증된 실전 초과수익 없음)
- **PAPER** = **NOT_STARTED** (승인된 알파 전략 부재)
- **LIVE** = **DISABLED** (실거래 원천 차단, Fail-Closed)
- **PRIVATE_API** = **DISABLED** (계좌/키 접근 전무, 공개 시장 데이터 수집기만 운영)

인프라 수집 신뢰성(6H-v3 TECHNICAL PASS)은 알파 수익 가능성을 의미하지 않으며, Fresh 30H-v2가 PENDING인 한 어떠한 알파 연구도 새 수집 데이터로 승격되지 않는다.
