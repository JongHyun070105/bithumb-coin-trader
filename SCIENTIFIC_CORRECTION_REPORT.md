# 과학적 정정 및 검증 최종 보고서 (Scientific Correction & Verification Report)

- **작성 일시**: 2026-09-17T01:30:00Z
- **작성 주체**: 자율 연구 및 엔지니어링 디렉터 (Autonomous Research & Engineering Director)
- **대상 커밋 기준점**: `08e3ea7` (수정 전)
- **적용 브랜치**: `develop` -> `main` (검증 후 패스트포워드 머지)
- **핵심 원칙**: 엄격한 진실성, 객관적 증거 우선(Fact-First), 통계적 인위성(Artifact) 배제, Fail-Closed 안전 경계 유지

---

## 1. 개요 및 요약 (Executive Summary)

커밋 `08e3ea7` 시점의 코드베이스와 연구 아티팩트에 대한 전면적 과학적 감사(Scientific Correction Pass)를 단행했습니다. 독립된 6개 감사 영역(V4 증거, 메이커 미시구조, 크로스 익스체인지 통계, 실행 모델, 문서/대시보드 정합성, 레드팀 적대적 검토)을 교차 검증하여 발견된 통계 왜곡, 회계 누락, 과장된 용어 및 집계 오류를 완전하게 정정하고 재산출했습니다.

### 핵심 정정 매트릭스

| 영역 | 기존 주장 / 상태 | 문제점 및 결함 | 정정된 과학적 진실 | 재계산 여부 | 추가/수정된 검증 테스트 |
| :--- | :--- | :--- | :--- | :---: | :--- |
| **AWS V4 터미널** | 수집 프로세스 종료가 확정된 것으로 서술 | SSM SendCommand 권한 미부여로 프로세스 직접 확인 불가 | `VALIDATION_OUTCOME_FINALIZED = true`, `COLLECTOR_TERMINAL_PROCESS_DIRECTLY_VERIFIED = false`, `collector_process = NOT_VERIFIABLE (DIRECT_PROCESS_UNVERIFIED)` | N/A (상태 정정) | `test_v4_terminal_semantics_distinction` |
| **메이커 총 실험 수** | 총 23개 실험 | Cycle 1 딕셔너리 키 길이 카운트 버그 ($54+4+12=70$) | 총 70개 사전등록 시험 (`total_trials = 70`) | 예 (원장 동기화) | `test_maker_total_trials_aggregation_equals_70` |
| **메이커 XRP 후보** | `MARKET_SPECIFIC_CANDIDATE` (체결률 0.021, 54회) | 체결률 수치 불일치 ($54/5117=0.01055$), 사후 탐색(Retrospective snooping) 과장 | `RETROSPECTIVE_DEV_MARKET_SPECIFIC_LEAD (NOT VALIDATED)` (체결수 54, 체결률 1.055%, 순이익 +2.20 bps, 낙관적 대기열 q=0.5 가정. 엄격 FIFO 적용 시 17건 급감) | 예 (지표 수정) | `test_maker_best_candidate_exact_metrics` |
| **메이커 시뮬레이터** | 부분 체결 시 미체결 잔여분 소멸 버그 | 부분 체결 후 잔여 미체결분을 버려 포지션 위험 누락 | 부분 체결 발생 시 잔여 미체결분을 시장가 테이커로 강제 청산하여 포지션 위험 완전 반영 | 예 (시뮬레이터 패치) | `test_maker_partial_fill_accounting_no_dropped_position` |
| **크로스 익스체인지 IC** | Spearman IC ~0.80, Pearson IC ~0.06 | 0.0 수익률 구간에 대해 정렬 순서대로 순위를 부여하는 tie-handling 왜곡 | 타이 보정 평균 순위(`rankdata_average`) 적용: 실제 Spearman IC **0.1172 ~ 0.1601** | 예 (전수 재계산) | `test_rankdata_average_tie_handling` |
| **크로스 익스체인지 적중률** | 적중률(Hit Rate) 0.0028 (~0.28%) | 89.8%에 달하는 0.0 무변동 샘플이 분모에 포함되어 발생한 착시 | 0이 아닌 유의미한 변동 표본 대상 방향 적중률(`nonzero_directional_hit_rate`)은 **92.86%** | 예 (진단 추가) | `test_directional_diagnostics_nonzero_hit_rate` |
| **크로스 익스체인지 체결** | 테이커 0/124 체결 가능 (실 호가 시뮬레이션 주장) | 단순 선형 수식(`IC*15 - spread - fee`)의 휴리스틱 스크리닝 프록시에 불과 | `HEURISTIC_ECONOMIC_SCREEN (REAL_FUTURE_BOOK_EXECUTION = NOT RUN)`. 추가로 36개 실 호가 심도 워킹 확인 실행을 수행하여 전원 손실(-11~-18 bps) 실증 | 예 (확인 시뮬레이션 구현 및 실행) | `test_cross_exchange_confirmation_execution_artifact` |
| **인과성 주장** | `CAUSAL_LEAD_CONFIRMED` | 백워드 As-Of 정렬은 미래 누출(Lookahead) 부재를 증명하나 구조적 인과관계를 단정할 수 없음 | `NO_LOOKAHEAD_PREDICTIVE_LEAD` (무누출 예측 선행성) | N/A (표기 정정) | `test_no_lookahead_invariant_preserved` |
| **가설 완성도** | X1~X5 전수 평가 주장 | X1, X2, X5만 평가되었고 X3, X4는 미실행 | 가설 X1, X2, X5 평가 완료, X3 및 X4는 미실행 명시 | N/A (표기 정정) | `test_cross_exchange_hypotheses_completeness` |
| **연구 데이터 범위** | 18h DEV 전체 사용 주장 | 메이커 및 크로스 익스체인지 연구는 DEV 중 Block D1 6시간(11:00~16:59 UTC)만 사용 | `DEV Block D1 (6 hours: 2026-09-12 11:00-16:59 UTC)`로 명확화 | N/A (문서 동기화) | `test_maker_best_candidate_exact_metrics` |

---

## 2. 세부 정정 내역 및 과학적 근거

### 1) AWS V4 터미널 상태 시맨틱 정정
- **기존 문제**:
  - 이전 보고서 및 상태 파일에서 `collector_process = "EXITED_PREMATURELY"` 또는 프로세스가 조기 종료된 것이 확인된 것처럼 기술됨.
- **실제 증거**:
  - `evidence/aws-validation-30h-20260915-v4/post-run/v4-terminal-audit.json` 확인 결과, EC2 인스턴스는 `running`, SSM 에이전트는 `Online`이었으나 AWS SSM SendCommand 권한(`ssm:SendCommand`)이 부여되지 않아 인스턴스 내부 프로세스 목록이나 데몬 종료 코드를 직접 조회할 수 없었음.
- **교정 내용**:
  - `VALIDATION_OUTCOME_FINALIZED = true` (자연 런타임 7.5시간 초과 및 S3 30시간 미수집으로 검증 실패 확정)
  - `COLLECTOR_TERMINAL_PROCESS_DIRECTLY_VERIFIED = false` (프로세스 직접 조회 실패)
  - `collector_process = "NOT_VERIFIABLE (DIRECT_PROCESS_UNVERIFIED)"`
  - `root_cause = "COLLECTION_OR_ARCHIVE_PIPELINE_FAILURE_AFTER_INITIAL_HOUR"`
  - `exact_process_failure_mode = "UNKNOWN"`
  - 최종 판정: `OVERALL: FAIL`, `NOT_RESEARCH_USABLE` 유지.

### 2) 메이커(Maker) 연구 집계 버그 및 지표 정정
- **기존 문제**:
  - `MAKER_RESULTS.json`에서 `total_trials: 23`으로 보고됨.
  - Cycle 1 파일이 딕셔너리(`{"scenarios_evaluated": 54, "results": [...]}`) 구조였는데, 취합 스크립트가 `len(cycle1_dict)`를 호출하여 딕셔너리 키 개수(7)를 더함 ($7 + 4 + 12 = 23$).
  - XRP 최적 시험(`MAKER-C3-XRP-Q0.5-C20S-CONS`)의 체결률이 `0.021`로 잘못 기록됨 (실제: 54 fills / 5,117 signals = `0.010553`, 약 1.055%).
  - `queue_multiplier = 0.5`가 보수적 규칙으로만 서술되어, 표시 호가의 50%만 앞선다고 가정하는 낙관적 대기열 편향이 누락됨.
  - 동일한 6시간 DEV 블록에 대해 Cycle 1 -> 2 -> 3를 반복하며 사후 튜닝된 결과를 정식 전략 후보(`CANDIDATE`)로 과장 분류함.
- **교정 내용**:
  - `total_trials: 70` ($54 + 4 + 12 = 70$)으로 정정.
  - XRP 지표: 54회 체결, 체결률 0.01055 (1.055%), 순이익 +2.198 bps (+2.20 bps).
  - 대기열 가정 분리: `queue_multiplier = 0.5`는 낙관적 대기열 가정(`OPTIMISTIC_QUEUE_POSITION`)이며, 엄격한 FIFO(`queue_multiplier = 1.0`) 적용 시 체결수가 17회로 급감함을 명시.
  - 분류 하향: `RETROSPECTIVE_DEV_MARKET_SPECIFIC_LEAD (NOT VALIDATED)`. 홀드아웃(VAL/TEST) 검증 전까지 실전 미승인.
  - `simulate_variant_b` 부분 체결 시 잔여 미체결분을 시장가 테이커로 강제 청산하여 포지션 잔여 위험 회계 버그 수정.

### 3) 크로스 익스체인지 통계 이상치 해소 및 정밀 진단
- **기존 문제**:
  - Pearson IC는 ~0.06 수준인데 Spearman IC가 ~0.80에 달하고, 반면 Hit Rate는 ~0.003에 불과한 기현상이 관측됨.
  - 원인 분석:
    1. 빗썸 1초 수익률의 89.8%, 바이낸스 피처의 99.5%가 0.0(무변동)이었음.
    2. `scipy.stats.rankdata`를 단순 모방한 구현에서 동점(0.0) 표본에 대해 정렬 순서(시간 순서)대로 1, 2, 3... 순위를 매김. 동일 시간대에 발생한 양 거래소의 0.0 표본들이 시간 인덱스 순서로 완벽하게 정렬되면서 가짜 순위 상관관계(~0.80)를 유발함.
    3. `compute_hit_rate`는 0.0 수익률을 분모에 포함하되 분자(정답)에서는 배제하여 적중률이 0.28%로 폭락함.
- **교정 내용**:
  - `rankdata_average`: 동점 발생 시 해당 동점들이 차지하는 순위의 평균값을 균등 할당하는 평균 순위 알고리즘 구현.
  - 재계산 결과: 실제 Spearman IC는 **+0.1172 ~ +0.1601**로 정상화됨.
  - `compute_directional_diagnostics`: 0이 아닌 실제 가격 변동 표본에 대한 방향 적중률(`nonzero_directional_hit_rate`)을 분리 측정. 측정 결과 **92.86%**로 선행 신호의 유효한 방향성 예측력을 확인.
  - 용어 정정: `CAUSAL_LEAD` -> `NO_LOOKAHEAD_PREDICTIVE_LEAD`.

### 4) 크로스 익스체인지 실행 모델 한계 규명 및 확인 체결 실험
- **기존 문제**:
  - `run_cross_exchange_research.py`의 `taker_net`은 `p_ic * 15.0 - spread - 0.4`라는 단순 선형 수식이었음.
  - 이를 두고 "124개 실험에서 테이커 실행 가능 후보 0건"이라고 보고한 것은 실제 호가창 체결 시뮬레이션을 수행한 것처럼 오도할 소지가 있었음.
- **교정 내용**:
  - 기존 결과를 `HEURISTIC_ECONOMIC_SCREEN (REAL_FUTURE_BOOK_EXECUTION = NOT RUN)`으로 명확히 규정.
  - **실 호가창 심도 워킹 확인 실행 (`run_cross_exchange_confirmation_execution.py`) 구현 및 실행**:
    - 최고 예측 성능을 기록한 BTC 및 SOL 신호에 대해 실제 빗썸 오더북 심도를 워킹하는 체결 시뮬레이션 수행 (`ResearchExecutionSimulator`, 지연 100/250/500ms, 주문 100,000 KRW, 36개 시나리오).
    - 결과 아티팩트: `research-artifacts/cross-exchange/reports/CROSS_EXCHANGE_CONFIRMATION_EXECUTION.json`
    - 실증 결과: 36개 전 시나리오에서 평균 순손익 -11.41 ~ -17.82 bps (승률 0.0%) 기록. 호가 스프레드, 슬리피지 및 수수료로 인해 단독 테이커 진입은 전멸(`COST_KILLED_BY_SPREAD_AND_FEES`)함을 완벽히 증명.

### 5) 가설 범위 및 데이터 슬라이스 명확화
- **기존 문제**:
  - 가설 X1~X5가 모두 완료되었다고 기술되었으나, 실제 스크립트에서는 X1, X2, X5만 수행되었음.
  - 연구가 V2 18h DEV 전체를 사용한 것처럼 기술되었으나, 실제 메이커 및 크로스 익스체인지 연구는 DEV의 첫 6시간 구간인 Block D1(2026-09-12 11:00 ~ 16:59 UTC)을 사용함.
- **교정 내용**:
  - 가설 명시: `X1, X2, X5 평가 완료 (X3, X4는 미실행)`.
  - 데이터 슬라이스 명시: `DEV Block D1 (6 hours: 2026-09-12 11:00-16:59 UTC)`.

---

## 3. 검증 결과 및 게이트 상태

- **Python 단위 및 회귀 테스트**:
  - 신규 `tests/test_scientific_corrections.py` 9개 테스트 100% 통과.
  - `tests/test_maker_simulator.py` 15개 골든 및 무결성 테스트 100% 통과.
  - `tests/test_dashboard_api.py` 17개 라우팅 및 상태 테스트 100% 통과.
- **정적 분석 및 컴파일**:
  - `python3 -m compileall src tests scripts` 무결점 완료.
- **프론트엔드 에어갭 대시보드 (`dashboard/`)**:
  - TypeScript Typecheck: 0 errors.
  - Vitest: 84개 테스트 100% PASS.
  - Vite build: 무결점 완료.
- **영구 보존 구역 준수**:
  - `test-results/`는 일절 수정, 스테이징, 삭제하지 않고 안전하게 보존됨.

---

## 4. 최종 과학적 거버넌스 선언

1. **`ALPHA = UNPROVEN`**:
   - 실전에 즉시 투입 가능한 초과수익 알파는 입증되지 않았습니다.
2. **`LIVE = DISABLED` / `PAPER = NOT STARTED`**:
   - 실거래 및 모의 거래 계층은 Fail-Closed 원칙에 따라 영구 비활성화 상태를 유지합니다.
3. **후속 연구 로드맵**:
   - 크로스 익스체인지의 유효한 선행 정보(Nonzero Hit Rate 92.86%)를 메이커 전략의 역선택 방어(외부 가격 급변 시 호가 즉시 취소)로 결합하는 하이브리드 전략 연구를 권고합니다.
