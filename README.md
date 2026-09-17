# Bithumb Coin Trader (빗썸 코인 트레이더)

빗썸 KRW 현물 시장용 **정량 미시구조 연구·검증·안전 실행 프레임워크**입니다. 엄격한 사전등록 가설 검증과 거래소 체결 비용을 감안한 실증 시뮬레이션을 수행하며, 검증을 통과하지 못한 전략은 실전에 승격하지 않습니다.

---

## 1. 현재 프로젝트 상태 (Current State)

| 지표 | 현재 상태 | 상세 설명 |
| :--- | :---: | :--- |
| **알파 상태 (ALPHA)** | **UNPROVEN** | 검증된 실전 초과수익 없음. 모든 테이커 전략은 비용 탈락(`COST_KILLED`). |
| **모의 거래 (PAPER)** | **NOT STARTED** | 승인된 알파 전략 부재로 인한 진입 대기. |
| **실거래 (LIVE)** | **DISABLED** | Fail-Closed 안전 원칙에 따라 신규 진입 원천 차단. |
| **개인 API (PRIVATE)** | **DISABLED** | API 키 입력 및 주문 실행 계층 영구 비활성화. |
| **대시보드 모드** | **READ_ONLY** | 로컬 격리형 오프라인 증거 뷰어 (포트 8787). |

---

## 2. 권위적 연구 및 검증 요약

### 1) V2 30시간 권위적 연구 (`aws-validation-30h-20260912-6576f63`)
- **데이터셋**: 30시간 연속 수집 (2,272개 파일, 타임스탬프 역전 0건).
- **테이커(Taker) 실행**: 48개 시나리오 전수 평가 결과, 호가 스프레드(1.6 ~ 5.4 bps)와 수수료로 인해 순손실 기록 → **`NO EXECUTABLE TAKER CANDIDATE`**.

### 2) 메이커(Maker) 미시구조 연구 (Cycles 1, 2, 3)
- **실험 규모**: 70개 사전등록 시험 평가 완료 (Cycle 1: 54개, Cycle 2: 4개, Cycle 3: 12개, `TRIAL_LEDGER.jsonl`).
- **결론**: 짧은 취소 한도(<=10s)에서는 강제 시장가 청산으로 전멸(`COST_KILLED`). 단, DEV Block D1(6시간)에서 **XRP(스프레드 5.4 bps) 20초 취소 한도 및 대기열 배수 0.5(낙관적 대기열 가정) 적용 시 순이익 유지 (+2.20 bps, 54회 체결, 체결률 1.055%)** 확인 → **`RETROSPECTIVE_DEV_MARKET_SPECIFIC_LEAD (NOT VALIDATED)`**로 분류 (단, 엄격한 FIFO 대기열 `queue_multiplier=1.0` 적용 시 체결수 17회로 급감하며 독립 검증 전까지 실전 미승인).

### 3) 크로스 익스체인지(Cross-Exchange) 예측 선행성 연구 (X1, X2, X5)
- **실험 규모**: 124개 시험 평가 완료 (X1, X2, X5 평가 완료, X3 및 X4는 미실행). 엄격한 나노초 가용 시각(`causal_availability_ns`) 정렬로 미래 누출 0건(LOOKAHEAD = NONE) 검증.
- **결론**: Binance/Upbit의 빗썸 선행 예측 관계 확인(타이 보정 Spearman IC ~0.12, 0이 아닌 가격 변동 시 방향 적중률 92.9%, 원시 적중률 ~0.28%는 90% 이상 무변동 샘플에 기인). 그러나 휴리스틱 스크리닝 및 실 호가창 심도 워킹(Depth-walking) 확인 실행 결과 스프레드와 테이커 수수료로 인해 순손실(-11 ~ -18 bps) 기록 → **`NO_LOOKAHEAD_PREDICTIVE_LEAD_CONFIRMED (REAL_FUTURE_BOOK_EXECUTION = COST_KILLED)`**.

### 4) AWS V4 30시간 검증 (`aws-validation-30h-20260915-v4`)
- **터미널 포렌식**: 계획 종료 시각(2026-09-16 17:00 UTC) 이후 검증 결과 확정(`VALIDATION_OUTCOME_FINALIZED = true`). 10시 구간 76개 커버리지(534 KB)만 존재하고 원시 데이터가 전무함. SSM 권한 부재로 프로세스 직접 확인은 불가(`COLLECTOR_TERMINAL_PROCESS_DIRECTLY_VERIFIED = false`, `collector_process = NOT_VERIFIABLE`)하며, 원인은 초기 1시간 이후 수집/아카이브 파이프라인 중단으로 판정.
- **최종 판정**: **`OVERALL: FAIL`**, **`NOT_RESEARCH_USABLE`** (태그 `archive/aws-v4-final` 보존).

---

## 3. 핵심 아키텍처 및 모듈 구성

- **연구 인프라 (`src/bithumb_coin_trader/research_infra/`)**:
  - `canonical_events.py`: 거래소 발생 시각과 로컬 가용 시각을 분리하여 미래 정보 누출 원천 차단.
  - `adapters.py`: 빗썸, 바이낸스, 업비트 원시 데이터를 캐노니컬 이벤트로 무손실 정규화.
  - `maker_simulator.py`: 패시브 대기열 소진 및 BUY/SELL 대칭 시뮬레이터 (골든 테스트 100% PASS).
- **상태 공급원 및 API (`src/bithumb_coin_trader/`)**:
  - `project_state.py`: 파일시스템 아티팩트로부터 현재 상태를 실시간 도출하는 단일 진실 공급원 (Single Source of Truth).
  - `dashboard_api.py`: 포트 8787 경량 읽기 전용 HTTP 서버 (13개 엔드포인트 완비).
- **에어갭 대시보드 (`dashboard/`)**:
  - React 19 + TypeScript + Vite 기반의 완전 격리형 오프라인 증거 및 연구 콘솔.

---

## 4. 상세 문서 안내 (Documentation)

- [현재 프로젝트 상태 (Current Project Status)](docs/CURRENT_PROJECT_STATUS.md)
- [정량 연구 및 가설 감사 보고서 (Research Status)](docs/RESEARCH_STATUS.md)
- [AWS 유효성 검증 이력 (AWS Validation History)](docs/AWS_VALIDATION_HISTORY.md)
- [시스템 아키텍처 (Architecture)](docs/ARCHITECTURE.md)
- [데이터 생명주기 및 거버넌스 (Data Lifecycle)](docs/DATA_LIFECYCLE.md)

---

## 5. 실행 및 검증 방법

### 1) 환경 설정
```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cd dashboard && npm install && cd ..
```

### 2) 전체 테스트 실행
```bash
# Python 단위 및 회귀 테스트 (1,300+ 테스트)
.venv/bin/pytest tests/

# 대시보드 테스트 및 빌드 검증
cd dashboard
npm run typecheck
npm run lint
npm run test
npm run build
cd ..
```

### 3) 로컬 대시보드 및 API 실행
```bash
# 1. 대시보드 API 서버 시작 (포트 8787)
.venv/bin/python -m bithumb_coin_trader.dashboard_api

# 2. 프론트엔드 대시보드 실행
cd dashboard && npm run dev
```

---

## 6. 안전 및 거버넌스 원칙

- **Fail-Closed**: 모든 불확실한 상태에서 신규 거래 진입은 원천 차단됩니다.
- **Fact-First**: 추측이나 사후적 재해석을 배제하고 오직 추적된 증거와 체크섬만을 사실로 인정합니다.
- **Protected Test Results**: `test-results/` 디렉터리는 영구 보호 구역으로 절대 수정, 스테이징, 삭제하지 않습니다.
