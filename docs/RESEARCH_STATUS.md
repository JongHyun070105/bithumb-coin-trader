# 연구 상태 및 가설 검증 결과 보고서 (Research Status & Hypotheses Audit)

- **최종 갱신 일시**: 2026-09-17T01:00:00Z
- **기준 데이터셋**: `aws-validation-30h-20260912-6576f63` (권위적 V2 30시간 수집본)
- **연구 분할**: 18h DEV / 6h VAL / 6h TEST (사전 동결, DEV 구간만 평가)
- **총 사전등록 시험 수 (Total Trials in Ledger)**: **194건**

---

## 1. 과학적 핵심 결론 요약

1. **알파 상태 (Alpha Status)**: **`ALPHA = UNPROVEN`** (검증된 실전 초과수익 없음).
2. **테이커 실행 (Taker Execution)**: **전략 가설 전멸 (`COST_KILLED`)**. 높은 예측력(IC 최대 0.37)이 관측되더라도, 빗썸 호가 스프레드(1.6 ~ 5.4 bps)와 수수료가 기대 총이익을 초과함.
3. **메이커 실행 (Maker Execution)**: 짧은 취소 한도(<=10초)에서는 패시브 탈출 실패에 따른 시장가 역손실로 비용 탈락. 단, **XRP(스프레드 5.4 bps)에서 20초 취소 한도 및 대기열 배수 0.5 적용 시 순이익 유지 (+1.80 ~ +2.20 bps)** 확인 → **`MARKET_SPECIFIC_CANDIDATE`**로 분류.
4. **크로스 익스체인지 (Cross-Exchange)**: 바이낸스/업비트의 빗썸 선행 인과성은 통계적으로 매우 강건하게 확증되었으나(Pearson IC 최대 0.3714, Spearman IC 최대 0.8458), 단독 테이커 진입은 불가능하여 **`CAUSAL_LEAD_CONFIRMED_PREDICTIVE_ONLY`**로 판정.

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
- **Cycle 3 (변별력 검증)**: XRP 대상 취소 한도(5s, 10s, 20s) 및 대기열 배수(0.5, 1.0) 격자 탐색.
  - 5s / 10s: 취소율 급증으로 비용 탈락.
  - 20s (Base): 2,555회 체결, 체결률 1.000, **순이익 +1.80 bps**.
  - 20s (Cons q=0.5): 54회 체결, 체결률 0.021, **순이익 +2.20 bps**.
- **판정**: `MARKET_SPECIFIC_CANDIDATE` (XRP 광폭 스프레드 한정 유효).

### 가설 X1 ~ X5: 크로스 익스체인지 선행 인과성 (Cross-Exchange Lead-Lag)
- **타임스탬프 무결성 보장**: Binance diff-depth 이벤트의 `exchange_ts` 결측 문제를 `availability_timestamp_ns` 기반으로 정렬하여 미래 정보 누출(Lookahead) 0건 달성.
- **X1 (바이낸스 선행)**: SOL, BTC, ETH 등에서 강한 선행성 확증 (SOL 250ms 지연 1초 지평: Pearson IC +0.3714, Spearman IC +0.7759).
- **X2 (업비트 선행)**: 국내 거래소 간 500ms ~ 2초 지연 선행성 확인 (IC +0.05 ~ +0.12).
- **X5 (베이시스 괴리)**: 빗썸-업비트 가격 괴리의 30초 평균회귀 확인 (IC 음수), 단 스프레드로 인해 차익거래 불가.
- **판정**: `CAUSAL_LEAD_CONFIRMED_PREDICTIVE_ONLY`.

---

## 3. 원장 통계 (Trial Ledger Aggregate)

총 194개 시험의 분류별 분포:
- `CAUSAL_LEAD_CONFIRMED_PREDICTIVE_ONLY`: 72건
- `COST_KILLED`: 39건
- `WEAK_CAUSAL_LEAD`: 36건
- `MAKER_PREDICTIVE_BUT_UNTRADEABLE`: 26건
- `NO_CAUSAL_LEAD`: 12건
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
