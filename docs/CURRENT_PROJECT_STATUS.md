# Bithumb Coin Trader — 현재 프로젝트 상태 (Current Project Status)

- **최종 갱신 일시**: 2026-09-21 01:19 KST (Fresh 30H-v2 보충)
- **작성 주체**: 자율 연구 및 엔지니어링 디렉터 (Autonomous Research & Engineering Director)
- **기준 Git 이력**: Fresh 30H-v2 봉인은 `develop`의 `9615be8`; 사후 감사는 별도 작업 브랜치에서 진행.
- **원격 기준**: `origin/main`, `origin/develop`은 2026-09-21 감사 시점에 분기되어 있으며, 통합 PR은 별도 검토 대상.

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
| **AWS 30h V4**<br>(`20260915-v4`) | 1시간 수집 후 중단<br>(76 오브젝트, 0 원시) | **OVERALL: FAIL** | **부적격**<br>(`NOT_RESEARCH_USABLE`) | 자연 계획 시각(2026-09-16 17:00 UTC) 경과 후 검증 확정(`VALIDATION_OUTCOME_FINALIZED = true`). 76개 오브젝트(커버리지 1시간 534 KB), 원시 데이터 0건. SSM 권한 부재로 프로세스 직접 확인은 불가(`COLLECTOR_TERMINAL_PROCESS_DIRECTLY_VERIFIED = false`, `collector_process = NOT_VERIFIABLE`), 초기 1시간 이후 파이프라인 중단으로 최종 실패 판정. 태그 `archive/aws-v4-final` 보존. |
| **Fresh 30H-v2**<br>(`20260919T095000Z-v2`) | 수집 감독 30시간 완료 | **TECHNICAL: FAIL** | **부적격** | 29개 적격 코호트 중 2 PASS, 1 FAIL(빗썸 60개 피드), 26개 확정 영수증 누락. [터미널 감사](../reliability-artifacts/aws-30h-v2/30H_RUN_AUDIT_REPORT.md). 신규 AWS 실행 잔여 0/10. |

---

## 3. 미시구조 연구 결과 요약 (Research Summary)

### 1) V2 테이커(Taker) 연구
- **대상**: BTC, ETH, XRP
- **평가 시나리오**: 48개 체결 시나리오 (호가 불균형 H1, 미시가격 H3, 레이턴시 0~500ms, 수수료 0~4 bps)
- **결론**: **`NO EXECUTABLE TAKER CANDIDATE`**.
  - 호가 스프레드(1.6 ~ 5.4 bps)와 수수료(0.4 bps)로 인해 모든 테이커 전략이 순손실(`-1.94 ~ -8.78 bps`) 기록.

### 2) 메이커(Maker) 연구 (Cycles 1, 2, 3)
- **시뮬레이터 강화**: BUY/SELL 대칭 지원, 보수적 대기열 소진 모델, 15개 골든 테스트 100% PASS, 부분 체결 후 잔여 미체결분 강제 청산 회계 버그 수정.
- **총 실험 건수**: 70건 (Cycle 1: 54건, Cycle 2: 4건, Cycle 3: 12건).
- **Cycle 1 (베이스라인)**: 54건 전멸 (`COST_KILLED`). 미체결 패시브 청산의 강제 시장가 정리로 손실 증폭.
- **Cycle 2 (신호 고도화)**: OBI >= 0.8 필터링 및 15초 타임아웃. ETH 탈락, XRP 양수이나 체결수 부족 (n=3).
- **Cycle 3 (변별력 검증)**: DEV Block D1(6시간: 2026-09-12 11:00-16:59 UTC) 대상 파라미터 스윕. XRP `MAKER-C3-XRP-Q0.5-C20S-CONS`에서 20초 취소 한도 및 대기열 배수 0.5(낙관적 대기열 가정: 표시 호가의 50%만 앞섬) 조건에서 순이익 유지 (+2.20 bps, 체결수 54건, 체결률 1.055%). 엄격한 FIFO 대기열(`queue_multiplier=1.0`) 적용 시 체결수 17건으로 급감.
- **판정**: **`RETROSPECTIVE_DEV_MARKET_SPECIFIC_LEAD (NOT VALIDATED)`** (동일 6시간 DEV 블록 대상 사후 파라미터 튜닝 결과이며, 홀드아웃 미검증 및 낙관적 대기열 가정에 의존하므로 독립 검증 전까지 실전 승격 불가).

### 3) 크로스 익스체인지(Cross-Exchange) 예측 선행성 연구 (X1, X2, X5)
- **타임스탬프 계약 수정**: Binance diff-depth의 `exchange_ts` 결측 문제를 `availability_timestamp_ns` 보존 및 Nullable 처리로 인과적 누출 완전 차단.
- **총 실험 건수**: 124건 (가설 X1, X2, X5 평가 완료, X3 및 X4는 미실행).
- **예측 선행성 검증**: Binance/Upbit 단기 가격 변동의 빗썸 미래 수익률 선행 예측 관계 확인 (타이 보정 Spearman IC 최대 `+0.12 ~ +0.16`, Pearson IC 최대 `+0.3714`, 0이 아닌 실제 가격 변동 구간 방향 적중률 92.86%, 원시 적중률 ~0.28%는 90% 이상 무변동 구간에 기인). 과거 시점 정렬(`causal_availability_ns`)을 통한 미래 누출 0건(LOOKAHEAD = NONE) 입증.
- **경제성 검증**: 휴리스틱 스크리닝 프록시(`HEURISTIC_ECONOMIC_SCREEN`) 및 실 호가창 심도 워킹(Depth-walking) 확인 실행 결과, 빗썸 스프레드와 테이커 수수료로 인해 전 시나리오 순손실(`-11.41 ~ -17.82 bps`, 승률 0.0%) 기록 → **`COST_KILLED_BY_SPREAD_AND_FEES`**.
- **판정**: **`NO_LOOKAHEAD_PREDICTIVE_LEAD_CONFIRMED (REAL_FUTURE_BOOK_EXECUTION = COST_KILLED)`** (선행 예측성은 실재하나 단독 테이커 진입은 비용 탈락. 향후 메이커 호가 취소 신호로의 설계 연계 연구 필요).

---

## 4. 인프라 및 저장소 상태

- **저장공간 복구 (Storage Recovery)**: 74.8 GB (98.4%) 안전 회수 완료. 현재 저장소 크기 약 890 MB 유지 (`STORAGE_SAFE`).
- **대시보드 & API**:
  - `src/bithumb_coin_trader/dashboard_api.py`: 13개 읽기 전용 엔드포인트 완비 (`/api/status`, `/api/datasets`, `/api/validations`, `/api/research/...` 등).
  - `dashboard/`: React 19 에어갭 대시보드. Typecheck, Oxlint, Vitest (84개 테스트 100% PASS), Vite build 무결점 완료.
- **Git 브랜치 거버넌스**:
  - 원격: `origin/main`, `origin/develop`만 유지.
  - 모든 과거 72h/v2/v3/v4/fresh45/dashboard 작업은 주석 달린 아카이브 태그(`archive/*`)로 봉인.
