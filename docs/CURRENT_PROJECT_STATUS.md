# Bithumb Coin Trader — 현재 프로젝트 상태 (Current Project Status)

- **최종 갱신 일시**: 2026-09-17T01:00:00Z
- **작성 주체**: 자율 연구 및 엔지니어링 디렉터 (Autonomous Research & Engineering Director)
- **Git HEAD**: `develop` 브랜치
- **원격 브랜치**: `origin/main`, `origin/develop` (모든 기능 브랜치 정리 및 태그 아카이브 완료)

---

## 1. 과학적 상태 요약 (Scientific State)

| 항목 | 현재 상태 | 설명 및 운영 경계 |
| :--- | :---: | :--- |
| **ALPHA** | **UNPROVEN** | 입증된 실전 초과수익(Alpha) 부재. 모든 전략 가설은 비용 반영 후 음수이거나 특정 자산 한정 후보 단계임. |
| **PAPER TRADING** | **NOT STARTED** | 실시간 모의 거래 미시행 (승인된 알파 전략 부재로 인한 진입 대기). |
| **LIVE TRADING** | **DISABLED** | 실거래 주문 계층 완전 비활성화. Fail-Closed 원칙에 따라 신규 주문 전송 원천 차단. |
| **PRIVATE API** | **DISABLED** | 거래소 개인 API 키 주입 및 주문 권한 영구 비활성화. |

---

## 2. 데이터셋 및 검증 현황 (Datasets & Validations)

| 데이터셋 / 검증 실행 | 수집 기간 / 크기 | 상태 및 판정 | 연구 유효성 | 핵심 결론 |
| :--- | :---: | :---: | :---: | :--- |
| **Historical 72h Soak** | ~72시간 (66 GB) | **FAIL** | 부적격 | 생명주기 및 코호트 키 충돌로 비연속 수집됨. 개발 참고용으로만 격리. |
| **AWS 45m Validation** | 45분 | **FAIL** | 부적격 | 아카이브 동시성 경쟁 상태(Race Condition) 감지. |
| **AWS Fresh 45m** | 45분 | **PASS** | 수집 검증 전용 | 인프라 및 수집 파이프라인 자율 복구 및 동시성 패치 검증 완료. |
| **Authoritative V2 30h**<br>(`20260912-6576f63`) | 30시간 연속<br>(2,272 오브젝트, 535.8 MB) | **PASS** | **유효 (DEV 연구)** | 2,272 정상 수집, 8 누락. 18h DEV 분할을 통한 가설 연구 및 체결 시뮬레이션 완수. |
| **AWS 30h V3**<br>(`20260915-v3`) | 0시간 | **FAIL** | 부적격 | 기동 인가 단계 후 프로세스 시작 실패. |
| **AWS 30h V4**<br>(`20260915-v4`) | 1시간 수집 후 중단<br>(76 오브젝트, 0 원시) | **OVERALL: FAIL** | **부적격**<br>(`NOT_RESEARCH_USABLE`) | 자연 계획 시각(2026-09-16 17:00 UTC) 7.5시간 초과 후 최종 감사. 원시 데이터 부재로 최종 실패 판정. 태그 `archive/aws-v4-final` 보존. |

---

## 3. 미시구조 연구 결과 요약 (Research Summary)

### 1) V2 테이커(Taker) 연구
- **대상**: BTC, ETH, XRP
- **평가 시나리오**: 48개 체결 시나리오 (호가 불균형 H1, 미시가격 H3, 레이턴시 0~500ms, 수수료 0~4 bps)
- **결론**: **`NO EXECUTABLE TAKER CANDIDATE`**.
  - 호가 스프레드(1.6 ~ 5.4 bps)와 수수료(0.4 bps)로 인해 모든 테이커 전략이 순손실(`-1.94 ~ -8.78 bps`) 기록.

### 2) 메이커(Maker) 연구 (Cycles 1, 2, 3)
- **시뮬레이터 강화**: BUY/SELL 대칭 지원, 보수적 대기열 소진 모델, 12개 골든 테스트 100% PASS.
- **총 실험 건수**: 70건
- **Cycle 1 (베이스라인)**: 54건 전멸 (`COST_KILLED`). 미체결 패시브 청산의 강제 시장가 정리로 손실 증폭.
- **Cycle 2 (신호 고도화)**: OBI >= 0.8 필터링 및 15초 타임아웃. ETH 탈락, XRP 양수이나 체결수 부족 (n=3).
- **Cycle 3 (변별력 검증)**: XRP 대상 파라미터 스윕. 20초 취소 한도 및 대기열 배수 0.5 조건에서 순이익 유지 (+1.80 bps Base, +2.20 bps Cons).
- **판정**: **`MARKET_SPECIFIC_CANDIDATE`** (XRP 5.4 bps 광폭 스프레드 한정 후보, 보편적 알파 아님).

### 3) 크로스 익스체인지(Cross-Exchange) 인과성 연구 (X1 ~ X5)
- **타임스탬프 계약 수정**: Binance diff-depth의 `exchange_ts` 결측 문제를 `availability_timestamp_ns` 보존 및 Nullable 처리로 인과적 누출 완전 차단.
- **총 실험 건수**: 124건
- **인과성 검증**: Binance/Upbit 단기 가격 변동의 빗썸 미래 수익률 선행성 확증 (Pearson IC 최대 `+0.3714`, Spearman IC 최대 `+0.8458`).
- **경제성 검증**: 테이커 실행 가능 후보 **0건** (`CROSS_EXCHANGE_TAKER_VIABLE = 0`). 빗썸 스프레드가 기대 예측 이익을 초과하여 테이커로는 거래 불가.
- **판정**: **`CAUSAL_LEAD_CONFIRMED_PREDICTIVE_ONLY`** (정보 흐름은 실재하나, 단독 테이커가 아닌 메이커 호가 취소 신호로 활용 권고).

---

## 4. 인프라 및 저장소 상태

- **저장공간 복구 (Storage Recovery)**: 74.8 GB (98.4%) 안전 회수 완료. 현재 저장소 크기 약 890 MB 유지 (`STORAGE_SAFE`).
- **대시보드 & API**:
  - `src/bithumb_coin_trader/dashboard_api.py`: 13개 읽기 전용 엔드포인트 완비 (`/api/status`, `/api/datasets`, `/api/validations`, `/api/research/...` 등).
  - `dashboard/`: React 19 에어갭 대시보드. Typecheck, Oxlint, Vitest (84개 테스트 100% PASS), Vite build 무결점 완료.
- **Git 브랜치 거버넌스**:
  - 원격: `origin/main`, `origin/develop`만 유지.
  - 모든 과거 72h/v2/v3/v4/fresh45/dashboard 작업은 주석 달린 아카이브 태그(`archive/*`)로 봉인.
