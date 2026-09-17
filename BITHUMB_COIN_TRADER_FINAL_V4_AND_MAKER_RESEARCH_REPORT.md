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
   - 자연 계획 종료 시각(2026-09-16 17:00:00 UTC)으로부터 7.5시간 경과 후 최종 감사 수행.
   - EC2 인스턴스는 실행 중이었으나, 수집 프로세스는 2026-09-15 11:01:37 UTC 이후 중단되어 S3에는 1시간 분량(76개 커버리지 객체, 534 KB)만 업로드되었고 마켓 데이터 원시 파일은 **0건** 존재함을 최종 확인.
   - 최종 판정: **`OVERALL: FAIL`**, 연구 유효성: **`NOT_RESEARCH_USABLE`**.
   - 영구 주석 태그 `archive/aws-v4-final`로 봉인하고, 원격 브랜치를 안전하게 정리 완료.
2. **V2 권위적 30시간 데이터셋 기반 연구 완수**:
   - 연속 30시간 수집된 V2 데이터셋(`aws-validation-30h-20260912-6576f63`, 2,272개 정상 파일)의 18시간 DEV 분할을 기준으로 연구 수행.
   - **테이커(Taker) 연구**: 48개 시나리오 전수 평가 결과, 호가 스프레드와 수수료로 인해 순수익 마이너스 기록 → **`NO EXECUTABLE TAKER CANDIDATE`**.
3. **메이커(Maker) 미시구조 실행 연구 (Cycles 1, 2, 3)**:
   - 시뮬레이터에 BUY/SELL 대칭 체결 및 보수적 대기열 소진 모델 구현 (12개 골든 테스트 100% 통과).
   - 총 70개 사전등록 시험 평가 완료 (`TRIAL_LEDGER.jsonl`).
   - 짧은 취소 지평(<=10초)에서는 미체결 호가의 시장가 긴급 청산으로 인해 전멸(`COST_KILLED`).
   - **XRP(스프레드 ~5.4 bps)에서 20초 취소 지평 및 대기열 배수 0.5 적용 시 순이익 유지 (+1.80 ~ +2.20 bps)** 확인 → **`MARKET_SPECIFIC_CANDIDATE`**로 분류 (단, 보편적 알파가 아닌 광폭 스프레드 마켓 한정 후보).
4. **크로스 익스체인지(Cross-Exchange) 인과성 연구 (X1 ~ X5)**:
   - Binance diff-depth의 `exchange_ts` 결측 문제를 `availability_timestamp_ns` 기반으로 정렬하여 미래 정보 누출(Lookahead) 원천 차단.
   - 총 124개 시험 평가 완료.
   - 바이낸스/업비트의 빗썸 선행 인과성은 통계적으로 강건하게 확인됨 (Pearson IC 최대 `+0.3714`, Spearman IC 최대 `+0.8458`).
   - 그러나 빗썸 호가 스프레드를 극복하는 테이커 실행 후보는 **0건** (`CROSS_EXCHANGE_TAKER_VIABLE = 0`) → **`CAUSAL_LEAD_CONFIRMED_PREDICTIVE_ONLY`**.
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
  - 2026-09-17 00:30 UTC 기준, 계획 시각이 7.5시간 초과하여 자연 런타임이 완전히 만료됨.
- **포렌식 증거 실측**:
  - S3 전체 재귀 인벤토리 분석 (`research-artifacts/v4-authoritative/source/V4_S3_INVENTORY.json`):
    - 총 객체 수: 76개 (모두 10시 구간 커버리지 JSON 파일, 534 KB)
    - 마켓 데이터 원시 압축 파일(`RAW_MARKET_DATA`): **0개**
    - 마지막 S3 업로드 시각: `2026-09-15T11:01:37Z`
- **포렌식 판정**:
  - `PROCESS: FAIL` (1시간 미만 수집 후 중단)
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
- **분할 원칙**: 18h DEV (2026-09-12 11:00 ~ 2026-09-13 04:00 UTC) 구간만 개봉하여 연구 진행. 6h VAL 및 6h TEST 구간은 봉인 유지.

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
- 12개 결정론적 골든 테스트(`tests/test_maker_simulator.py`)를 작성하여 체결, 취소, 수수료, 재고 정리 전반을 100% 검증.

### 2) 연구 주기별 실험 결과
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
    - Conservative (q=0.5) 모델: 54회 체결, 체결률 0.021, **순이익 +2.20 bps**.
- **판정**: **`MARKET_SPECIFIC_CANDIDATE`**
  - XRP처럼 스프레드가 충분히 넓고(약 5.4 bps) 호가 잔량이 두터운 마켓에서, 취소 한도를 20초 이상으로 부여하여 패시브 체결 기회를 충분히 확보할 때만 제한적으로 유효함. BTC/ETH 등 좁은 스프레드 자산에는 적용 불가.

---

## 5. 크로스 익스체인지(Cross-Exchange) 인과성 연구

### 1) 실험 설계 및 가설
- **신호원**: 바이낸스 USDT 무기한/현물, 업비트 KRW 현물
- **체결 거래소**: 빗썸 KRW 현물 (오직 빗썸 단일 체결 원칙)
- **인과성 규약**: 나노초 단위 백워드 As-Of 정렬 (`external_availability_ns <= bithumb_decision_time_ns`), 미래 정보 누출 0건 보장.
- **평가 가설**:
  - X1: 바이낸스 단기 수익률 선행 (지연 100ms ~ 5s, 지평 1s ~ 30s)
  - X2: 업비트 단기 수익률 선행 (지연 500ms ~ 2s, 지평 5s ~ 10s)
  - X5: 빗썸-업비트 베이시스 괴리 30초 평균회귀

### 2) 실험 결과 분석 (124개 사전등록 시험)
| 분류 | 건수 | 비율 | 주요 통계 |
| :--- | :---: | :---: | :--- |
| **CAUSAL_LEAD_CONFIRMED_PREDICTIVE_ONLY** | 72 | 58.1% | Pearson IC 최대 +0.3714, Spearman IC 최대 +0.8458 |
| **WEAK_CAUSAL_LEAD** | 36 | 29.0% | 0.02 < Pearson IC <= 0.05 |
| **NO_CAUSAL_LEAD** | 12 | 9.7% | Pearson IC <= 0.02 |
| **PREDICTIVE_BUT_UNTRADEABLE** | 4 | 3.2% | 베이시스 괴리 역상관 확인되나 차익 불가능 |
| **CROSS_EXCHANGE_TAKER_VIABLE** | **0** | **0.0%** | **테이커 실행 가능 후보 0건** |

### 3) 핵심 시사점
- 글로벌 및 국내 타 거래소의 가격 변동이 빗썸에 100ms ~ 1초 후 유의미하게 반영되는 **정보의 인과적 선행성은 통계적으로 확실히 존재**합니다.
- 그러나 신호 포착 후 빗썸에서 테이커(시장가)로 체결을 시도할 경우, 빗썸의 스프레드(1.6 ~ 5.4 bps)를 지불하는 순간 기대 초과이익이 완전히 소멸합니다.
- 따라서 크로스 익스체인지 신호는 독립적인 테이커 진입용이 아니라, **메이커 호가의 역선택 방지(급변 감지 시 패시브 주문 긴급 취소) 및 비대칭 호가 배치 신호로 결합해야 함**을 입증했습니다.

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
- [x] `cross_exchange_research_completed_or_blocked`: 참 (X1-X5 완료, 124건 원장 기록)
- [x] `dashboard_integrated`: 참 (React 19 빌드/테스트 완료)
- [x] `api_integrated`: 참 (13개 엔드포인트 완비 및 테스트 통과)
- [x] `legacy_branches_reviewed`: 참 (원격 2개 브랜치로 단일화)
- [x] `final_tests_passed`: 참 (전체 파이썬 및 프론트엔드 테스트 통과)
- [x] `main_promoted`: 참 (검증 후 develop → main 패스트포워드 머지)

### 2) 최종 제언 및 실전 권고
1. **현재 알파 부재 확인**: 테이커 알파는 존재하지 않으며, 메이커 전략 또한 XRP 1개 종목의 특정 파라미터 조합에서만 양수 마진을 확인했으므로, 실전 트레이딩 활성화는 절대 불가하며 **`LIVE = DISABLED`**를 엄격히 유지해야 합니다.
2. **향후 연구 방향**: 크로스 익스체인지 신호를 메이커의 패시브 호가 취소 및 역선택 방어(Adverse Selection Mitigation) 신호로 결합하는 하이브리드 연구를 권고합니다.
