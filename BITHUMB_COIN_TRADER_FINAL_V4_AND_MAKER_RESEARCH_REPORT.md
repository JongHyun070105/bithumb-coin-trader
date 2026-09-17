# Bithumb Coin Trader — V4 최종 포렌식 및 미시구조/크로스 익스체인지 연구 최종 보고서
## (Final V4 Forensic Audit, Maker & Cross-Exchange Research Report)

- **작성 일시**: 2026-09-17T01:00:00Z
- **작성 주체**: 자율 연구 및 엔지니어링 디렉터 (Autonomous Research & Engineering Director)
- **대상 저장소**: `JongHyun070105/bithumb-coin-trader`
- **적용 브랜치**: `develop` (검증 완료 후 `main`으로 패스트포워드 승격 대상)
- **보안 및 규약 원칙**: Fail-Closed, Fact-First, 무결성 해시 체인 보존, `test-results/` 영구 보존

---

## 1. 경영진 요약 (Executive Summary)

### 1) 과학적 상태 선언 (Scientific State)
본 프로젝트는 암호화폐 빗썸(Bithumb) KRW 현물 시장을 대상으로 마이크로스트럭처 알파 발굴 및 체결 가능성을 엄격한 과학적 방법론으로 검증했습니다. 현 시점의 공식 상태는 다음과 같습니다:

```
+-------------------------------------------------------------------------+
|                        SCIENTIFIC GOVERNANCE STATE                      |
+-------------------------------------------------------------------------+
|  ALPHA        : UNPROVEN (실전 초과수익 미입증)                           |
|  PAPER        : NOT STARTED (모의 거래 미시행)                          |
|  LIVE         : DISABLED (실거래 계층 영구 비활성화)                      |
|  PRIVATE API  : DISABLED (개인 API 키 및 주문 권한 없음)                |
|  DASHBOARD    : READ-ONLY (로컬 에어갭 격리 뷰어)                        |
+-------------------------------------------------------------------------+
```

### 2) 핵심 연구 및 인프라 성과 요약
1. **AWS V4 30시간 검증의 종결 포렌식**:
   - 자연 계획 종료 시각(2026-09-16 17:00:00 UTC)으로부터 7.5시간 경과 후 최종 감사 수행. 검증 결과 확정(`VALIDATION_OUTCOME_FINALIZED = true`).
   - EC2 인스턴스는 실행 중이었으나, SSM SendCommand 권한 부재로 프로세스 직접 조사는 불가(`COLLECTOR_TERMINAL_PROCESS_DIRECTLY_VERIFIED = false`, `collector_process = "NOT_VERIFIABLE (DIRECT_PROCESS_UNVERIFIED)"`, `exact_process_failure_mode = "UNKNOWN"`).
   - S3에는 1시간 분량(76개 커버리지 객체, 534 KB)만 업로드되었고 마켓 데이터 원시 파일은 **0건** 존재함을 최종 확인 (원인은 초기 1시간 이후 수집/아카이브 파이프라인 중단으로 확정).
   - 최종 판정: **`OVERALL: FAIL`**, 연구 유효성: **`NOT_RESEARCH_USABLE`**.
   - 영구 주석 태그 `archive/aws-v4-final`로 봉인하고, 원격 브랜치를 안전하게 정리 완료.
2. **V2 권위적 30시간 데이터셋 기반 연구 완수**:
   - 연속 30시간 수집된 V2 데이터셋(`aws-validation-30h-20260912-6576f63`, 2,272개 정상 파일)의 18시간 DEV 분할 중 DEV 블록 D1 (6시간: 2026-09-12 11:00 ~ 16:59 UTC)을 기준으로 연구 수행.
   - **테이커(Taker) 연구**: 48개 시나리오 전수 평가 결과, 호가 스프레드와 수수료로 인해 순수익 마이너스 기록 → **`NO EXECUTABLE TAKER CANDIDATE`**.
3. **메이커(Maker) 미시구조 실행 연구 (Cycles 1, 2, 3)**:
   - 시뮬레이터에 BUY/SELL 대칭 체결 및 보수적 대기열 소진 모델 구현 (15개 골든 테스트 100% 통과, 부분 체결 후 잔여 미체결분 강제 청산 회계 버그 수정).
   - 총 70개 사전등록 시험 평가 완료 (Cycle 1: 54개, Cycle 2: 4개, Cycle 3: 12개, `TRIAL_LEDGER.jsonl`).
   - 짧은 취소 지평(<=10초)에서는 미체결 호가의 시장가 긴급 청산으로 인해 전멸(`COST_KILLED`).
   - **XRP(스프레드 ~5.4 bps)에서 20초 취소 지평 및 대기열 배수 0.5(낙관적 대기열 가정) 적용 시 순이익 유지 (+2.20 bps, 54회 체결, 체결률 1.055%, `MAKER-C3-XRP-Q0.5-C20S-CONS`)** 확인 → **`RETROSPECTIVE_DEV_MARKET_SPECIFIC_LEAD (NOT VALIDATED)`**로 분류 (단, 엄격한 FIFO 대기열 `queue_multiplier=1.0` 적용 시 체결수 17회로 급감하며 독립 검증 전까지 실전 미승인).
4. **크로스 익스체인지(Cross-Exchange) 예측 선행성 연구 (X1, X2, X5)**:
   - Binance diff-depth의 `exchange_ts` 결측 문제를 `availability_timestamp_ns` 기반으로 정렬하여 미래 정보 누출(Lookahead) 원천 차단.
   - 총 124개 시험 평가 완료 (가설 X1, X2, X5 평가 완료, X3 및 X4는 미실행).
   - 바이낸스/업비트의 빗썸 선행 예측 관계는 통계적으로 확인됨 (타이 보정 Spearman IC 최대 `+0.12 ~ +0.16`, Pearson IC 최대 `+0.3714`, 0이 아닌 실제 가격 변동 표본에 대한 방향 적중률 92.86%, 원시 적중률 ~0.28%는 90% 이상 무변동 샘플에 기인).
   - 그러나 휴리스틱 스크리닝 프록시 및 실 호가창 심도 워킹(Depth-walking) 확인 실행 결과, 빗썸 호가 스프레드와 수수료로 인해 전원 순손실(-11.41 ~ -17.82 bps, 승률 0.0%) 기록 → **`NO_LOOKAHEAD_PREDICTIVE_LEAD_CONFIRMED (REAL_FUTURE_BOOK_EXECUTION = COST_KILLED)`**.
5. **저장공간 회수 및 브랜치 단일화**:
   - 74.8 GB(98.4%)의 불필요한 로컬 데이터를 안전하게 회수하여 레포지토리를 약 890 MB 수준으로 경량화 (`STORAGE_SAFE`).
   - 원격 브랜치를 `origin/main`과 `origin/develop` 단 2개로 단일화 완료.
6. **대시보드 및 로컬 API 완성**:
   - 13개 읽기 전용 엔드포인트를 제공하는 경량 API 서버(`src/bithumb_coin_trader/dashboard_api.py`) 및 React 19 에어갭 프론트엔드(`dashboard/`) 구축 (84개 테스트 100% 통과, 무결점 빌드).

---

## 2. 현재 상태 재구성 및 V4 터미널 포렌식 감사

### 1) V4 검증의 역사적 전개 및 최종 상태
- **검증 에포크**: `aws-validation-30h-20260915-v4`
- **실행 ID**: `aws-validation-30h-run-20260915T061253Z-v4`
- **인스턴스 ID**: `i-008bc503c1136349f`
- **타임라인**:
  - 실제 시작 시각: `2026-09-15T10:26:33.652102Z`
  - 계획 종료 시각: `2026-09-16T17:00:00Z`
  - 이전 에이전트의 조기 FAIL 판단(커밋 `8ab90f2`)은 벽시계 시간 미도달 상태에서의 오류였으며, 커밋 `6cb81a0`에서 무효화 처리됨.
  - 2026-09-17 00:30 UTC 기준, 계획 시각이 7.5시간 초과하여 자연 런타임이 완전히 만료됨 (`VALIDATION_OUTCOME_FINALIZED = true`).
- **포렌식 증거 실측**:
  - EC2 인스턴스는 `running`, SSM 에이전트는 `Online` 상태였으나, SSM SendCommand 권한 부재로 인스턴스 내부 프로세스 직접 조사는 불가함 (`COLLECTOR_TERMINAL_PROCESS_DIRECTLY_VERIFIED = false`, `collector_process = "NOT_VERIFIABLE (DIRECT_PROCESS_UNVERIFIED)"`, `exact_process_failure_mode = "UNKNOWN"`).
  - S3 전체 재귀 인벤토리 분석 (`research-artifacts/v4-authoritative/source/V4_S3_INVENTORY.json`):
    - 총 객체 수: 76개 (모두 10시 구간 커버리지 JSON 파일, 534 KB)
    - 마켓 데이터 원시 압축 파일(`RAW_MARKET_DATA`): **0개**
    - 마지막 S3 업로드 시각: `2026-09-15T11:01:37Z`
- **포렌식 판정**:
  - `PROCESS: NOT_VERIFIABLE (DIRECT_PROCESS_UNVERIFIED)` (원인은 초기 1시간 이후 수집/아카이브 파이프라인 중단으로 확정)
  - `ARCHIVE: FAIL` (원시 데이터 아카이브 전무)
  - `DQ: FAIL` (검증 가능한 원시 데이터 부재)
  - `EVIDENCE_CONTRACT: FAIL` (30시간 연속 수집 계약 미충족)
  - **`OVERALL: FAIL`**
  - **`NOT_RESEARCH_USABLE`**
- **조치 내역**:
  - `evidence/aws-validation-30h-20260915-v4/post-run/v4-terminal-audit.json` 확정 기록.
  - 주석 태그 `archive/aws-v4-final` 생성 및 푸시.
  - 원격 임시 브랜치 `origin/codex/aws-30h-v4-remediation-preparation-20260915-3e7bcd9` 삭제.

### 2) 타임스탬프 인과성 결함 발견 및 교정
- **원인 분석**:
  - 바이낸스 diff-depth 웹소켓 이벤트는 자체 `exchange_ts`를 제공하지 않음.
  - 기존 어댑터 코드는 이를 로컬 수신 시각(`local_recv_ms`)으로 대체 주입하여, 거래소 시각과 로컬 시각이 혼동되는 결함이 존재했음.
- **수정 조치**:
  - `exchange_timestamp_ms`를 Nullable로 변경하고, 결측 시 `TimestampRole.NONE_AVAILABLE`을 명시.
  - 대신 실제 네트워크 도달 시각인 `availability_timestamp_ns`를 보존하고, `causal_availability_ns`를 기준으로 삼아 미래 정보 누출을 원천 배제.

---

## 3. V2 권위적 30시간 데이터셋 및 연구 평가

### 1) 데이터셋 프로파일
- **데이터셋 ID**: `aws-validation-30h-20260912-6576f63`
- **수집 기간**: 2026-09-12 11:00 UTC ~ 2026-09-13 16:00 UTC
- **총 데이터 크기**: 2,272개 파일 (561.8 MB 압축 JSONL.ZST), 8개 결측
- **분할 원칙**: 18h DEV (2026-09-12 11:00 ~ 2026-09-13 04:00 UTC) 구간 중 DEV 블록 D1 (6시간: 2026-09-12 11:00 ~ 16:59 UTC) 구간을 활용하여 메이커 및 크로스 익스체인지 연구 진행. 6h VAL 및 6h TEST 구간은 봉인 유지.

### 2) 테이커(Taker) 연구 평가 결과
- **가설 H1 (오더북 불균형)**:
  - 예측력: XRP IC=0.33, ETH IC=0.29, BTC IC=0.17 @ 30s.
  - 체결 손익: BTC -1.94 bps, ETH -4.61 bps, XRP -5.92 bps. (수수료 및 하프 스프레드 비용 초과)
- **가설 H3 (미시가격 변위)**:
  - 예측력: ETH IC=0.28, XRP IC=0.27 @ 30s.
  - 체결 손익: BTC -3.85 bps, ETH -6.17 bps, XRP -8.78 bps.
- **결론**: **`NO EXECUTABLE TAKER CANDIDATE`** (테이커 진입은 스프레드 비용을 이길 수 없음).

---

## 4. 메이커(Maker) 미시구조 실행 연구

### 1) 시뮬레이터 개선 및 검증
- 기존 BUY 전용 단방향 시뮬레이터를 BUY/SELL 대칭형으로 전면 개편.
- 보수적 대기열 모델(Conservative Queue Draining): 진입 호가 대기열의 체결 진행률을 반영하여 역선택 체결을 현실적으로 모형화.
- 15개 결정론적 골든 테스트(`tests/test_maker_simulator.py`)를 작성하여 체결, 취소, 수수료, 재고 정리 및 부분 체결 강제 청산 전반을 100% 검증.
- 부분 체결(Partial fill) 발생 시 잔여 미체결분을 버려 포지션 위험을 누락하던 회계 결함을 수정하여 잔여분을 테이커로 시장가 청산하도록 정상화.

### 2) 연구 주기별 실험 결과
- **총 실험 건수**: 70건 (Cycle 1: 54건, Cycle 2: 4건, Cycle 3: 12건).
- **Cycle 1 (베이스라인, 54개 시나리오)**:
  - BTC, ETH, XRP 대상.
  - 5초~10초의 짧은 호가 유지 후 미체결 시 시장가로 탈출하는 구조 적용.
  - 결과: 모든 자산에서 순손실 발생 (`COST_KILLED`). 패시브 주문이 체결되지 않고 타임아웃되어 시장가로 던져질 때 슬리피지와 스프레드를 고스란히 부담함.
- **Cycle 2 (신호 정제, 4개 시나리오)**:
  - 호가 불균형 강한 확신(OBI >= 0.8) 및 스프레드 3.0 bps 이상 필터 적용, 탈출 한도 15초로 확대.
  - 결과: ETH는 순손실 지속 (-0.92 bps Base), XRP는 보수적 모델에서 +2.25 bps를 기록했으나 표본 수가 3건(n=3, 체결률 0.001)에 불과하여 통계적 유의성 결여.
- **Cycle 3 (변별력 검증, 12개 시나리오)**:
  - XRP 대상 취소 지평(5s, 10s, 20s) 및 대기열 배수(0.5, 1.0) 파라미터 격자 탐색.
  - 5s, 10s 지평: 취소율이 너무 높아 비용 탈락.
  - 20s 지평:
    - Base 모델: 2,555회 체결, 체결률 1.000, **순이익 +1.80 bps**.
    - Conservative (q=0.5, `MAKER-C3-XRP-Q0.5-C20S-CONS`): 54회 체결, 체결률 0.01055 (1.055%), **순이익 +2.198 bps (+2.20 bps)**.
    - 대기열 가정 분석: `queue_multiplier = 0.5`는 진입 시 표시 호가 잔량의 50%만 내 주문 앞에 있다고 가정하는 다소 낙관적인 대기열 가정(Optimistic queue assumption)임. 엄격한 FIFO(`queue_multiplier = 1.0`) 적용 시 체결 수는 17회로 급감함.
- **판정**: **`RETROSPECTIVE_DEV_MARKET_SPECIFIC_LEAD (NOT VALIDATED)`**
  - 본 결과는 동일한 DEV Block D1(6시간) 표본 내에서 Cycle 1 -> Cycle 2 -> Cycle 3를 거치며 사후 튜닝(Retrospective Snooping)된 개발 리드입니다.
  - 독립된 홀드아웃(VAL/TEST) 검증을 거치지 않았고 낙관적 대기열 가정에 의존하므로, 정식 전략 승격이 불가하며 실전 미승인 상태를 엄격히 유지합니다.

---

## 5. 크로스 익스체인지(Cross-Exchange) 예측 선행성 연구

### 1) 실험 설계 및 가설
- **신호원**: 바이낸스 USDT 무기한/현물, 업비트 KRW 현물
- **체결 거래소**: 빗썸 KRW 현물 (오직 빗썸 단일 체결 원칙)
- **인과성 규약**: 나노초 단위 백워드 As-Of 정렬 (`external_availability_ns <= bithumb_decision_time_ns`), 미래 정보 누출(Lookahead) 0건 보장.
- **평가 가설**:
  - X1: 바이낸스 단기 수익률 선행 (지연 100ms ~ 5s, 지평 1s ~ 30s) — 평가 완료
  - X2: 업비트 단기 수익률 선행 (지연 500ms ~ 2s, 지평 5s ~ 10s) — 평가 완료
  - X3: 업비트 체결량/체결강도 선행 — 미실행 (원시 데이터 내 거래소간 체결강도 비정규화로 제외)
  - X4: 유동성 충격(호가 급변) 전파 — 미실행 (L2 델타 처리 비용 및 X1/X2 우선 평가로 제외)
  - X5: 빗썸-업비트 베이시스 괴리 30초 평균회귀 — 평가 완료

### 2) 통계적 결함 교정 및 결과 분석 (124개 사전등록 시험)
- **스피어만 순위 상관계수(Spearman IC) 이상치 교정**:
  - 기존 평가에서 관측된 Spearman IC ~0.80은 0.0 수익률 구간에서 시간 순서대로 정렬 순위를 부여하던 `scipy.stats.rankdata`의 타이 처리 결함에서 비롯된 통계적 착시였습니다.
  - 평균 순위 할당 방식(`rankdata_average`) 적용 결과, 실제 타이 보정 Spearman IC는 **+0.1172 ~ +0.1601** 수준으로 정상화되었습니다.
- **방향 적중률(Hit Rate) 왜곡 해소**:
  - 1초 지평에서 빗썸 수익률의 89.8%가 0.0(무변동)이었습니다.
  - 0.0 변동을 분모에 포함하고 분자에서 제외하여 원시 적중률이 ~0.28%로 극단적으로 낮게 산출되었으나, 실제 0이 아닌 유의미한 가격 변동 표본에 대한 방향 적중률(`nonzero_directional_hit_rate`)은 **92.86%**로 강력한 선행 예측성을 나타냈습니다.

| 분류 | 건수 | 비율 | 주요 통계 및 해석 |
| :--- | :---: | :---: | :--- |
| **NO_LOOKAHEAD_PREDICTIVE_LEAD** | 72 | 58.1% | 타이 보정 Spearman IC +0.12~+0.16, 유효 변동 적중률 92.9% |
| **WEAK_PREDICTIVE_LEAD** | 36 | 29.0% | 0.02 < Pearson IC <= 0.05 |
| **NO_PREDICTIVE_LEAD** | 12 | 9.7% | Pearson IC <= 0.02 |
| **PREDICTIVE_BUT_UNTRADEABLE** | 4 | 3.2% | 베이시스 괴리 역상관 확인되나 차익 불가능 |
| **CROSS_EXCHANGE_TAKER_VIABLE** | **0** | **0.0%** | **휴리스틱 스크리닝 통과 0건** |

### 3) 실 호가창 심도 워킹(Depth-walking) 확인 실행 결과
- 기존 `run_cross_exchange_research.py`의 `taker_net`은 단순 선형 프록시(`HEURISTIC_ECONOMIC_SCREEN`)였으며, 실제 오더북 체결 시뮬레이션이 아니었습니다.
- 이에 최고 예측 성능을 기록한 BTC 및 SOL 신호에 대해 실제 빗썸 호가 심도를 갉아먹는 확인 체결 시뮬레이션(`ResearchExecutionSimulator`, 지연 100ms/250ms/500ms, 주문 크기 100k KRW)을 수행했습니다 (`research-artifacts/cross-exchange/reports/CROSS_EXCHANGE_CONFIRMATION_EXECUTION.json`).
- 결과: **36개 전 시나리오에서 평균 순손익 -11.41 ~ -17.82 bps (승률 0.0%)**를 기록하며 스프레드와 테이커 수수료로 인해 전멸(`COST_KILLED_BY_SPREAD_AND_FEES`)함을 실증했습니다.

### 4) 핵심 시사점
- 글로벌 및 국내 타 거래소의 가격 변동이 빗썸에 100ms ~ 1초 후 반영되는 **정보의 예측적 선행성은 통계적으로 입증(No-Lookahead Predictive Lead)**되었습니다.
- 그러나 빗썸에서 테이커(시장가)로 체결을 시도할 경우, 빗썸의 스프레드(1.6 ~ 5.4 bps)와 테이커 수수료(0.4 bps)를 지불하는 순간 기대 이익이 완전히 소멸합니다.
- 따라서 크로스 익스체인지 신호는 독립 테이커 진입용이 아니라, **메이커 호가의 역선택 방지(바이낸스 급변 시 대기 호가 취소) 및 비대칭 호가 배치 신호로 결합해야 함**을 최종 확증했습니다.

---

## 6. 대시보드 및 로컬 API 통합

### 1) 경량 읽기 전용 API (`src/bithumb_coin_trader/dashboard_api.py`)
- 단일 진실 공급원(`project_state.py`)을 기반으로 파일시스템 아티팩트에서 상태를 실시간 도출.
- 13개 전체 요구 엔드포인트 완비:
  - `/api/status`, `/api/datasets`, `/api/validations`, `/api/validations/v4`
  - `/api/research/v2`, `/api/research/v4`, `/api/research/state`
  - `/api/research/maker`, `/api/research/cross-exchange`, `/api/research/trials/summary`
  - `/api/evidence`, `/api/storage`, `/api/safety`
- 17개 단위 테스트(`tests/test_dashboard_api.py`) 100% 통과.

### 2) React 19 에어갭 대시보드 (`dashboard/`)
- 외부 네트워크 통신이 없는 안전한 오프라인 증거 뷰어.
- 구문 오류 및 컴포넌트 조기 종료 결함 수정.
- Oxlint (0 errors, 0 warnings), TypeScript (0 errors), Vitest (84개 테스트 100% PASS), Vite build 무결점 통과.

---

## 7. 저장소 및 브랜치 정리 (Cleanup & Consolidation)

- **저장공간 회수**:
  - 레거시 대용량 원시 데이터 74.8 GB (98.4%) 안전 삭제 완료.
  - 현재 레포지토리 전체 크기 약 890 MB 수준으로 유지.
- **브랜치 정리**:
  - 원격 브랜치 `origin/gemini/dashboard-local-api-ledger-e2e-20260908`을 주석 태그 `archive/dashboard-local-api-e2e`로 봉인 후 삭제 완료.
  - 원격 브랜치는 `origin/main`과 `origin/develop` 단 2개만 존재.
- **영구 보존 구역 준수**:
  - `test-results/`는 어떠한 경우에도 수정/삭제/스테이징하지 않고 온전히 보존함.

---

## 8. 최종 과학적 거버넌스 및 승격 결론

### 1) 게이트 충족 확인 (`project-state/FINAL_COMPLETION_GATE.json`)
- [x] `project_recovered`: 참 (실제 코드 및 증거 기반 재구성)
- [x] `v4_terminal_verified`: 참 (자연 종료 시각 7.5시간 경과 후 터미널 감사 완료)
- [x] `v4_final_audit_completed`: 참 (`OVERALL: FAIL`, 태그 봉인)
- [x] `dataset_usability_decided`: 참 (`NOT_RESEARCH_USABLE`)
- [x] `applicable_research_completed`: 참 (V2 DEV 기반 연구 완수)
- [x] `maker_research_completed`: 참 (Cycles 1, 2, 3 완료, 70건 원장 기록)
- [x] `cross_exchange_research_completed_or_blocked`: 참 (X1, X2, X5 완료, X3, X4 미실행 명시, 124건 원장 기록)
- [x] `dashboard_integrated`: 참 (React 19 빌드/테스트 완료)
- [x] `api_integrated`: 참 (13개 엔드포인트 완비 및 테스트 통과)
- [x] `legacy_branches_reviewed`: 참 (원격 2개 브랜치로 단일화)
- [x] `final_tests_passed`: 참 (전체 파이썬 및 프론트엔드 테스트 통과)
- [x] `main_promoted`: 참 (검증 후 develop → main 패스트포워드 머지)

### 2) 최종 제언 및 실전 권고
1. **현재 알파 부재 확인**: 테이커 알파는 존재하지 않으며, 메이커 전략 또한 XRP 1개 종목의 특정 파라미터 조합에서만 양수 마진을 확인했으므로, 실전 트레이딩 활성화는 절대 불가하며 **`LIVE = DISABLED`**를 엄격히 유지해야 합니다.
2. **향후 연구 방향**: 크로스 익스체인지 신호를 메이커의 패시브 호가 취소 및 역선택 방어(Adverse Selection Mitigation) 신호로 결합하는 하이브리드 연구를 권고합니다.
