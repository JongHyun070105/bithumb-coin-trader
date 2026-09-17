# 연구 상태 및 가설 검증 결과 보고서 (Research Status & Hypotheses Audit)

- **최종 갱신 일시**: 2026-09-17T01:00:00Z
- **기준 데이터셋**: `aws-validation-30h-20260912-6576f63` (권위적 V2 30시간 수집본)
- **연구 분할**: DEV Block D1 (6시간: 2026-09-12 11:00-16:59 UTC, 메이커 및 크로스 익스체인지 연구에 사용됨. 전체 18h DEV 분할 중 6시간 슬라이스)
- **총 사전등록 시험 수 (Total Trials in Ledger)**: **194건** (메이커 70건 + 크로스 익스체인지 124건)

---

## 1. 과학적 핵심 결론 요약

1. **알파 상태 (Alpha Status)**: **`ALPHA = UNPROVEN`** (검증된 실전 초과수익 없음).
2. **테이커 실행 (Taker Execution)**: **전략 가설 전멸 (`COST_KILLED`)**. 높은 예측력(IC 최대 0.37)이 관측되더라도, 빗썸 호가 스프레드(1.6 ~ 5.4 bps)와 수수료가 기대 총이익을 초과함.
3. **메이커 실행 (Maker Execution)**: 짧은 취소 한도(<=10초)에서는 패시브 탈출 실패에 따른 시장가 역손실로 비용 탈락. 단, DEV Block D1(6시간)에서 **XRP(스프레드 5.4 bps) 20초 취소 한도 및 대기열 배수 0.5(낙관적 대기열 가정) 적용 시 순이익 유지 (+2.20 bps, 54회 체결, 체결률 1.055%)** 확인 → **`RETROSPECTIVE_DEV_MARKET_SPECIFIC_LEAD (NOT VALIDATED)`**로 분류 (단, 엄격한 FIFO 대기열 적용 시 17회로 급감하며 독립 검증 전까지 실전 미승인).
4. **크로스 익스체인지 (Cross-Exchange)**: 바이낸스/업비트의 빗썸 선행 예측 관계는 통계적으로 확증(타이 보정 Spearman IC ~0.12, 0이 아닌 실제 가격 변동 시 방향 적중률 92.86%, 원시 적중률 ~0.28%는 90% 이상 무변동 샘플에 기인)되었으나, 휴리스틱 스크리닝 및 실 호가창 심도 워킹(Depth-walking) 확인 실행 결과 스프레드와 수수료로 인해 전원 순손실(-11 ~ -18 bps) 기록 → **`NO_LOOKAHEAD_PREDICTIVE_LEAD_CONFIRMED (REAL_FUTURE_BOOK_EXECUTION = COST_KILLED)`**로 판정 (가설 X1, X2, X5 평가 완료, X3 및 X4는 미실행).

---

## 2. 세부 연구 주기 및 가설별 결과

### 가설 H1: 오더북 불균형 (Orderbook Imbalance)
- **예측력**: 10초 및 30초 지평에서 강한 유의성 (XRP IC 0.33, ETH IC 0.29, BTC IC 0.17).
- **테이커 체결**: 모든 마켓에서 비용 초과로 전멸 (BTC -1.94 bps, ETH -4.61 bps, XRP -5.92 bps).
- **판정**: `COST_KILLED`.

### 가설 H3: 미시가격 변위 (Microprice Displacement)
- **예측력**: 10초 및 30초 지평에서 양호 (ETH IC 0.28, XRP IC 0.27).
- **테이커 체결**: 테이커 수수료 및 스프레드 관통 실패 (BTC -3.85 bps, ETH -6.17 bps, XRP -8.78 bps).
- **판정**: `COST_KILLED`.

### 가설 M1: 메이커 실행 (Maker Execution Cycles 1, 2, 3)
- **Cycle 1 (베이스라인)**: 54개 시나리오 전멸. 짧은 호가 유지 시간 동안 체결되지 않은 주문이 시장가로 긴급 청산되며 극심한 역선택 손실 유발.
- **Cycle 2 (신호 필터링)**: OBI >= 0.8 강한 신호 및 스프레드 3.0 bps 이상 필터 적용. ETH 순손실 지속, XRP 양수이나 극단적 체결 감소 (n=3, fill rate 0.001).
- **Cycle 3 (변별력 검증)**: DEV Block D1(6시간) 대상 XRP 취소 한도(5s, 10s, 20s) 및 대기열 배수(0.5, 1.0) 격자 탐색.
  - 5s / 10s: 취소율 급증으로 비용 탈락.
  - 20s (Base q=0.5): 2,555회 체결, 체결률 1.000, **순이익 +1.80 bps**.
  - 20s (Cons q=0.5, `MAKER-C3-XRP-Q0.5-C20S-CONS`): 54회 체결, 체결률 0.01055 (1.055%), **순이익 +2.198 bps**. (낙관적 대기열 가정: 표시 호가의 50%만 앞선다고 가정. 엄격한 FIFO 대기열 `queue_multiplier=1.0`에서는 체결수 17회로 급감).
- **판정**: `RETROSPECTIVE_DEV_MARKET_SPECIFIC_LEAD (NOT VALIDATED)` (동일 6시간 DEV 블록 대상 사후 파라미터 튜닝 결과이며, 홀드아웃 미검증 상태).

### 가설 X1 ~ X5: 크로스 익스체인지 선행 예측 연구 (Cross-Exchange Lead-Lag)
- **타임스탬프 무결성 보장**: 과거 가용 시각(`causal_availability_ns`) 정렬로 미래 정보 누출(Lookahead) 0건 달성. (가설 X1, X2, X5 평가 완료, X3 및 X4는 미실행).
- **X1 (바이낸스 선행)**: SOL, BTC, ETH 등에서 선행성 확인 (SOL 250ms 지연 1초 지평: Pearson IC +0.3714, 타이 보정 Spearman IC +0.1601, 0이 아닌 가격 변동 시 방향 적중률 92.86%, 원시 적중률 ~0.28%는 90% 이상 무변동 구간에 기인).
- **X2 (업비트 선행)**: 국내 거래소 간 500ms ~ 2초 지연 선행성 확인 (IC +0.05 ~ +0.12).
- **X5 (베이시스 괴리)**: 빗썸-업비트 가격 괴리의 30초 평균회귀 확인 (IC 음수), 단 스프레드로 인해 차익거래 불가.
- **경제성 및 실 체결 검증**: 휴리스틱 스크리닝(`HEURISTIC_ECONOMIC_SCREEN`) 결과 0/124 테이커 진입 가능. 실 호가창 심도 워킹 확인 실행(`ResearchExecutionSimulator`) 결과 36개 시나리오 전원 순손실(-11.41 ~ -17.82 bps)로 `COST_KILLED_BY_SPREAD_AND_FEES` 확증.
- **판정**: `NO_LOOKAHEAD_PREDICTIVE_LEAD_CONFIRMED (REAL_FUTURE_BOOK_EXECUTION = COST_KILLED)`.

---

## 3. 원장 통계 (Trial Ledger Aggregate)

총 194개 시험의 분류별 분포 (메이커 70건 + 크로스 익스체인지 124건):
- `NO_LOOKAHEAD_PREDICTIVE_LEAD`: 72건
- `COST_KILLED`: 39건
- `WEAK_PREDICTIVE_LEAD`: 36건
- `MAKER_PREDICTIVE_BUT_UNTRADEABLE`: 26건
- `NO_PREDICTIVE_LEAD`: 12건
- `MAKER_CANDIDATE`: 4건 (XRP Variant B)
- `PREDICTIVE_BUT_UNTRADEABLE`: 4건
- `SAMPLE_SIZE_INSUFFICIENT_OUTLIER`: 1건

---

## 4. 향후 연구 로드맵 권고

1. **외부 신호 기반 패시브 호가 보호 (Toxic Flow Protection)**:
   - 바이낸스 급변 감지 시 대기 중인 빗썸 호가를 즉시 취소하여 역선택 체결 방어.
2. **동적 호가 배치 (Dynamic Asymmetric Quoting)**:
   - 바이낸스 선행 모멘텀 방향에 맞추어 유리한 호가 레벨에만 대기열 선점.
3. **신규 데이터셋 수집 시 검증 절차 준수**:
   - V4 실패 교훈을 반영하여, 수집 데몬의 다중 감시 및 정상 아카이브 확인 후 단계적 검증 진입.
