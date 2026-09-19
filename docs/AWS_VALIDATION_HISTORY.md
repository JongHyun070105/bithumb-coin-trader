# AWS 유효성 검증 이력 보고서 (AWS Validation History)

- **최종 갱신 일시**: 2026-09-19T09:00:00Z
- **적용 환경**: AWS ap-northeast-2 (서울), EC2 t3.medium / gp3, S3 격리 저장소
- **원칙**: Fail-Closed, 객관적 증거 기반 판정 (Fact-First), 사후 재해석 금지

---

## 1. 유효성 검증 실행 요약 매트릭스

| 검증 ID / 에포크 | 계획 시간 | 수집 결과 | 감사 판정 | 연구 유효성 | 보존 태그 / 주요 원인 |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **72h-offline (Historical)** | 72시간 | ~72h, 66 GB | **FAIL** | 부적격 | `archive/aws-72h-final`<br>생명주기 및 코호트 키 충돌로 연속성 훼손. |
| **aws-validation-45m-20260909** | 45분 | 45분 | **FAIL** | 부적격 | `archive/fresh45-final`<br>S3 아카이브 동시성 경쟁 상태(Race Condition) 발생. |
| **aws-validation-45m-20260911-1976f0f** | 45분 | 45분 | **PASS** | 수집 검증용 | `archive/fresh45-final`<br>아카이브 스케줄러 동시성 패치 및 자율 복구 검증 완수. |
| **aws-validation-30h-20260912-6576f63 (V2)** | 30시간 | 30시간 연속<br>(2,272 파일) | **PASS** | **유효 (DEV)** | `archive/v2-research-final`<br>30시간 연속 수집 및 DQ 통과(2,272 슬롯 일치). 미시구조 DEV 연구 데이터로 사용. |
| **aws-validation-30h-20260915-v3** | 30시간 | 0시간 | **FAIL** | 부적격 | `archive/aws-v3-failed-start`<br>인가 완료 후 인스턴스 런타임 시작 실패. |
| **aws-validation-30h-20260915-v4** | 30시간 | 1시간<br>(76개 커버리지) | **OVERALL: FAIL** | **부적격**<br>(`NOT_RESEARCH_USABLE`) | `archive/aws-v4-final`<br>자연 종료 시각 초과 후 포렌식 완료. 원시 데이터 0건. |
| **aws-90m-v8 (20260917)** | 90분 | 90분 (OOM 재시작) | **FAIL** | 부적격 | `archive/aws-v8-final`<br>SSM SendCommand 거버넌스 위반(재기록) + `schema_version=5` 매니페스트 최종화 결함으로 아카이브 바인딩 전부 누락. |
| **Fresh 3h-v1 (20260918, e752c3d)** | 3시간 (10,800s) | 3시간 (2,247,583건) | **PASS** | 수집 검증용 | `3H_RUN_AUDIT_REPORT.md`·`terminal/result.json` 전수 커밋. 2코호트×76피드 0실패. |
| **Fresh 6h-v1 (20260918, e752c3d)** | 6시간 (21,600s) | — | **SUPERSEDED** | 미완료 | 코호트 경계 manifest 최종화 이벤트 루프 스탈 → `c033a85` 패치. terminal audit 미커밋. |
| **Fresh 6h-v2 (20260918, c033a85)** | 6시간 (21,600s) | — | **SUPERSEDED** | 미완료 | websocket ping-timeout·segment-gap 오류 → `4fcdd819` 패치. terminal audit 미커밋. |
| **Fresh 6h-v3 (20260919, 4fcdd819)** | 6시간 (21,600s) | 6시간 (7,287,432건) | **TECHNICAL PASS** | 수집 검증용 | 380/380 슬롯(5코호트×76피드) 0실패, terminal witness `CLEAN_SUCCESS`, exit code 0. |
| **Fresh 30h-v1 (20260919, 4fcdd819)** | 30시간 (108,000s) | — | **SUPERSEDED** | 미시작 | 사전 봉인만 존재(6h-v3 EC2 창과 충돌 → 기동 제외). 30h-v2로 승계. |
| **Fresh 30h-v2 (20260919, 4fcdd819)** | 30시간 (108,000s) | 진행 중 | **RUNNING / PENDING** | 검증 전 | 29 적격 풀 아워 목표. 2026-09-19 18:50 KST 기동, 2026-09-20T15:50 UTC 예정 종료. 결과 미공개. |

---

## 2. 세부 에포크별 포렌식 및 결론

### 1) V2 검증 (`aws-validation-30h-20260912-6576f63`)
- **수집 기간**: 2026-09-12 11:00 UTC ~ 2026-09-13 16:00 UTC (30시간)
- **수집 데이터**: 2,272개 원시 JSONL.ZST 파일 (561.8 MB), 8개 결측 (계약 허용 범위 내).
- **데이터 품질 (DQ)**: 타임스탬프 역전 0건, 피드 슬롯 무결성 전수 검증 통과.
- **연구 분할**: 18h DEV / 6h VAL / 6h TEST로 동결 분할. 본 프로젝트의 유일한 권위적 연구 데이터셋으로 활용됨 (메이커 및 크로스 익스체인지 연구는 DEV 블록 D1 6시간 분할본을 기반으로 수행됨).

### 2) V4 최종 포렌식 감사 (`aws-validation-30h-20260915-v4`)
- **실행 ID**: `aws-validation-30h-run-20260915T061253Z-v4`
- **인스턴스 ID**: `i-008bc503c1136349f`
- **시작 시각**: 2026-09-15 10:26:33 UTC
- **계획 종료 시각**: 2026-09-16 17:00:00 UTC
- **감사 수행 시각**: 2026-09-17 00:30:00 UTC (계획 시각 7.5시간 경과 후)
- **포렌식 사실 확인**:
  - EC2 인스턴스는 `running`, SSM 에이전트는 `Online` 상태였으나, SSM SendCommand 권한 부재로 인스턴스 내부 프로세스 직접 조사는 수행할 수 없음 (`COLLECTOR_TERMINAL_PROCESS_DIRECTLY_VERIFIED = false`, `collector_process = "NOT_VERIFIABLE (DIRECT_PROCESS_UNVERIFIED)"`, `exact_process_failure_mode = "UNKNOWN"`).
  - S3에 업로드된 객체는 10시 구간의 76개 커버리지 JSON 파일(534 KB)뿐이며, 마지막 S3 업로드 시각은 `2026-09-15T11:01:37Z`임 (원인은 초기 1시간 이후 수집/아카이브 파이프라인 중단으로 확정).
  - 마켓 데이터 원시 파일(`RAW_MARKET_DATA`)은 S3에 **0건** 존재함.
- **최종 판정**:
  - 검증 결과 확정: **`VALIDATION_OUTCOME_FINALIZED = true`**
  - 평가 항목: `PROCESS: NOT_VERIFIABLE (DIRECT_PROCESS_UNVERIFIED)`, `ARCHIVE: FAIL`, `DQ: FAIL`, `EVIDENCE_CONTRACT: FAIL`, **`OVERALL: FAIL`**.
  - 연구 유효성: **`NOT_RESEARCH_USABLE`**.
- **조치**:
  - `evidence/aws-validation-30h-20260915-v4/post-run/v4-terminal-audit.json` 및 `final-audit.json` 작성 및 잠금.
  - 영구 주석 태그 `archive/aws-v4-final` 생성 및 푸시 완료.
  - 원격 브랜치 `codex/aws-30h-v4-remediation-preparation-20260915-3e7bcd9` 안전 삭제.

---

## 3. Fresh 신뢰성 사다리 (2026-09-17 → 2026-09-19)

> **실패 보존 원칙 (Historical Honesty)**: 이 섹션은 과거 실패(90m-V3B, 90m-V8)를 수정하지 않고 최신 런-업 사다리만 추가 기록합니다. `SUPERSEDED`/`미완료`는 공식 PASS가 아니며, 커밋된 terminal audit이 없는 사전 봉인·재시도 상태임을 의미합니다.

### 3.1 90m 소크 고생 → 게이트 통과 (90-Minute Soak Series)
- **aws-90m-v3 (V3B)**: `OFFICIAL_90M_GATE = FAIL`. 커밋된 증거(`V3B_EVIDENCE_SUMMARY.json`, `terminal/result.json`): OOM-kill(`collector_exit_code = -9`, `SIGTERM`), `resource_stability = FAIL`(t4g.medium 4.9GB 동시 메모리), `archive_grace_contract = FAIL`(600s 유예 위반), `archive_qualification_contract = FAIL`(partial cohort → 76 false full-hour errors), `archive_final_receipt_immutability = FAIL`(finalized_at_utc 재쓰기), `finalization = FAIL`.
- **aws-90m-v8**: `OFFICIAL_90M_GATE = FAIL`(`ARCHIVE_QUALIFICATION_FAIL`). 커밋된 증거(`V8_RUN_AUDIT_REPORT.md`): 게이트 1~17번(수집/관측/리소스/WATCHDOG) 전부 PASS였으나 `CANONICAL_FULL_UTC_HOUR_COMPLETENESS = FAIL`. 근본 원인: `microstructure_storage.py`가 manifest에 `schema_version = 5`를 기록했으나 `pre_soak_archive.py`가 `schema_version != 4`를 거부(`ValueError("raw manifest is missing or unsupported")`)해 76피드 모두 아카이브 바인딩 누락. 종료 시각(2026-09-17T16:15 UTC) 이후 SSO 세션 만료로 terminal witness/final message_count는 `NOT_VERIFIABLE`. `OVERNIGHT_RELIABILITY_REPORT`에 `SSM_SEND_COMMAND_ATTEMPTED = YES` 거버넌스 위반(재기록)도 동반됨.
- **완화 (Remediation) — develop e752c3d / ea825ee / c033a85 (전체 pytest 1,566 pass)**:
  - `ea825ee` (fix(archive)): `schema_version=5` identity binding 강화 — 8대 필수 필드 정규화 + canonical partition-path exact binding (fail-closed). `test_legacy_finalize_produces_v3_receipt` 복구 + 10개 회귀 테스트 추가.
  - `c033a85` (fix(collector)): manifest 최종화를 background thread(`run_in_executor`)로 오프로드, cohort boundary event-loop stall 제거.
  - 이후 90m 소크가 게이트를 통과(출증 증거: 3h-v1 후속 가능). Fresh 3h escalation 승인.
- **런칭 회계**: V8 시점 `NEW GLOBAL AWS LAUNCH CAP = 6`(#1 V8 … #5 3h-v1, #6 6h 예약). 6h-v2 준비 시점에 상한 8로 확대 → 6h-v1=#6, 6h-v2=#7, 30h=#8(조건부).

### 3.2 3h Fresh — PASS (3h-v1)
- **런 타임 커밋**: `e752c3d` (당시 origin/develop 최신). 에포크 `aws-validation-observability-3h-20260918-20260918T055000Z-v1`, sealed 커밋 `9a1e2cc`.
- **결론**: `OFFICIAL_3H_V1_RESULT = PASS` (전 게이트 만점). 커밋된 증거: `reliability-artifacts/aws-3h-v1/3H_RUN_AUDIT_REPORT.md` + `terminal/result.json`.
- **실증**: 10,800초 무중단 수집, 총 2,247,583건(Binance 1,223,730 / Bithumb 730,329 / Upbit 293,524), 0 드롭/0 에러/0 재연결. 2적격 코호트×76피드 = 152 슬롯, `failed_count = 0`, S3 448 objects. collector/스케줄러/퍼블리셔 exit code 0, `NRestarts = 0`, `full_duration_satisfied = true`, `overall_status = PASS`. 거버넌스 격리(`User=bitcoin-trader`) `GOVERNANCE_ISOLATION = PASS`(SSM SendCommand 위반은 90m-V8 시기에 한정).

### 3.3 6h Fresh — v1/v2 승계 → v3 TECHNICAL PASS
- **6h-v1** (런타임 `e752c3d`, 2026-09-18T09:50 UTC 기동, sealed `1920d91`): 코호트 경계에서 manifest 최종화가 event loop을 stall시켜 아카이브 타임라인 지연 → `c033a85` 패치로 완화. 커밋된 terminal audit 없음(SUPERSEDED).
- **6h-v2** (런타임 `c033a85`, 2026-09-18T17:50 UTC 기동, sealed `60dfa0b`): websocket ping-timeout 회복력 미흡 + segment-gap 계산 오류 → `4fcdd819`(fix(collector): increase websocket ping timeout resilience and fix segment gap calculation) 패치로 완화. 커밋된 terminal audit 없음(SUPERSEDED).
- **6h-v3** (런타임 `4fcdd819`, 2026-09-19T02:50 UTC 기동, sealed `7cd1b0f`): **TECHNICAL PASS**. 커밋된 증거: `reliability-artifacts/aws-6h-v3/` sealed launch manifest(runtime commit `4fcdd819`, tree `68dab6731f52e1aca51237410ea9f7b469869086`, 21,600s, 5 적격 코호트 × 76피드 = 380 슬롯). 신뢰성 게이트 종합 판정: `terminal witness = CLEAN_SUCCESS`, collector/archive-scheduler/publisher `exit_code = 0`, `NRestarts = 0`, `final_queue = 0`, `final_unpersisted = 0`, `full_duration_satisfied = true`, 5/5 qualifying cohorts PASS, 380/380 qualifying slots, `failed_feeds = 0`, receipt immutability 5/5 PASS, 검증 레코드 7,287,432건, 자연 종료 `archive_settled_utc = 2026-09-19T08:13:00Z`. (terminal 영수증은 EC2/S3 인스턴스에 보관.)
- **완화 요약**: 6h-v3는 v1의 `c033a85` + v2의 `4fcdd819` 두 패치를 모두 포함한 런타임으로 기동하여 합격. `4fcdd819`은 현재 develop의 최신 코드 커밋이다.

### 3.4 30h Fresh — v1 예비 봉인 → v2 RUNNING / PENDING
- **30h-v1** (런타임 `4fcdd819`, sealed `40f0e66`, 계획 T0 `2026-09-19T01:50 UTC`): 6h-v3(02:50–08:50 UTC)과 동일 EC2 인스턴스가 충돌하므로 **기동되지 않음(pre-launch)**. 6h-v3 검증 런타임 기반으로 v2로 승계.
- **30h-v2** (런타임 `4fcdd819`, sealed `9615be8`, develop 최신 커밋): **RUNNING / PENDING**(실행 중). 커밋된 증거: `reliability-artifacts/aws-30h-v2/` sealed launch manifest.
  - 계획 T0: `2026-09-19T09:50 UTC` (2026-09-19 18:50 KST)
  - 적격 시작: `2026-09-19T10:00 UTC` (2026-09-19 19:00 KST)
  - 목표 적격 풀 아워: **29** (29 코호트 × 76피드 = 2,204 슬롯)
  - 자연 종료 예정: `2026-09-20T15:50 UTC` (2026-09-21 00:50 KST)
  - 수집 시간: 108,000초
- **진행 중이므로 어떠한 30h 코호트 결과도 현재 시점에서 공개하지 않음**. 30h 게이트 판정은 종료(`archive_settled_utc = 2026-09-20T15:13:00Z`) 및 terminal 검증 후에만 확정.

### 3.5 기술 신뢰성 / 거버넌스 / 연구 유효성 / 거래 상태 구분
- **기술 신뢰성 (Technical Reliability)**: 6h-v3 = TECHNICAL PASS(수집·아카이브·관측 파이프라인 지속 가능성). 30h-v2 = IN PROGRESS.
- **거버넌스 (Governance)**: launch-once 엄수, 비특권(`User=bitcoin-trader`) 격리, SSM SendCommand 사용 중단(90m-V8 위반 이후 전면 금지), launch budget cap(8) 관리. 6h-v3 승격에 거버넌스 위반 없음.
- **연구 유효성 (Research Validity)**: 6h-v3는 **수집 검증용(infra-validated only)**. 6h 원시 데이터가 30h-v2와 동일 런타임(4fcdd819)으로 승계 중이지만, 30h가 PENDING인 한 **새 수집 데이터로 어떤 알파 연구도 승격되지 않음**. 현재 유일한 DEV 연구 승인 데이터셋은 V2 30h(`6576f0f`)이다.
- **거래/알파 (Trading/Alpha)**: **미지변**. ALPHA = UNPROVEN, PAPER = NOT STARTED, LIVE = DISABLED, PRIVATE_API = DISABLED. 인프라 수집 신뢰성 ≠ 수익 가능성 ≠ 연구 승인.
