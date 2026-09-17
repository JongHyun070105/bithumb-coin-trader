# 크로스 익스체인지 선행-지연 인과성 연구 최종 보고서 (Cross-Exchange Lead-Lag & Causality Report)

- **작성 일시**: 2026-09-17T00:45:00Z
- **데이터셋**: `aws-validation-30h-20260912-6576f63` (V2 DEV 분할, 블록 D1: 2026-09-12 11:00 ~ 16:00 UTC)
- **대상 마켓**: BTC, ETH, XRP, SOL
- **신호 거래소**: Binance, Upbit
- **체결 거래소**: Bithumb (오직 빗썸 단일 체결 원칙 준수)
- **인과성 원칙**: 엄격한 백워드 As-Of (`external_availability_ns <= bithumb_decision_time_ns`), 미래 정보 누출(Lookahead) 0건 검증

---

## 1. 연구 개요 및 가설 체계

본 연구는 바이낸스(Binance) 및 업비트(Upbit)의 글로벌/국내 유동성이 빗썸(Bithumb) 호가 및 가격에 미치는 선행-지연(Lead-Lag) 인과성을 엄격한 이벤트 타임스탬프 기준으로 검증하고, 이를 활용한 전략이 거래 비용(스프레드 + 수수료)을 극복하고 실행 가능한지 평가했습니다.

- **가설 X1 (바이낸스 수익률 선행)**: 바이낸스 단기 가격 변동이 빗썸의 미래 가격 변동을 선행함.
- **가설 X2 (업비트 수익률 선행)**: 업비트 단기 가격 변동이 빗썸의 미래 가격 변동을 선행함.
- **가설 X5 (베이시스 괴리 평균회귀)**: 빗썸과 업비트 간 가격 괴리(Basis Dislocation)가 일정 시간 내 평균회귀함.

---

## 2. 핵심 분석 결과 요약

총 **124개 사전 등록 실험(Trials)**을 평가한 결과는 다음과 같습니다:

| 분류 (Classification) | 평가 건수 | 비율 (%) | 주요 의미 |
| :--- | :---: | :---: | :--- |
| **CAUSAL_LEAD_CONFIRMED_PREDICTIVE_ONLY** | 72 | 58.1% | 통계적 선행 인과성 확증 (IC > 0.05), 단 테이커 실행 불가능 |
| **WEAK_CAUSAL_LEAD** | 36 | 29.0% | 약한 선행성 확인 (0.02 < IC <= 0.05) |
| **NO_CAUSAL_LEAD** | 12 | 9.7% | 유의미한 선행 관계 부재 (IC <= 0.02) |
| **PREDICTIVE_BUT_UNTRADEABLE** | 4 | 3.2% | 베이시스 괴리 역상관 확인되나 스프레드로 거래 불가 |
| **CROSS_EXCHANGE_TAKER_VIABLE** | **0** | **0.0%** | **테이커 실행 가능 후보 전무 (0건)** |

### 상위 정보계수 (Pearson / Spearman IC)
1. **X1-BINANCE-LEAD-SOL-LAG250MS-HZ1S**: Pearson IC `+0.3714`, Spearman IC `+0.7759` (Taker Net: `-0.23 bps`)
2. **X1-BINANCE-LEAD-SOL-LAG100MS-HZ1S**: Pearson IC `+0.2678`, Spearman IC `+0.8458` (Taker Net: `-1.78 bps`)
3. **X1-BINANCE-LEAD-SOL-LAG250MS-HZ5S**: Pearson IC `+0.2574`, Spearman IC `+0.6788` (Taker Net: `-1.94 bps`)
4. **X1-BINANCE-LEAD-BTC-LAG100MS-HZ1S**: Pearson IC `+0.0618`, Spearman IC `+0.8008` (Taker Net: `-1.07 bps`)

---

## 3. 미시구조 경제성 및 테이커 장벽 분석

### 1) 신호 예측력(Predictive Power) vs 실행 경제성(Execution Feasibility)의 괴리
- 바이낸스와 업비트의 100ms ~ 500ms 단기 가격 변동은 빗썸의 1s ~ 5s 미래 수익률과 매우 강한 순위 상관관계(Spearman IC 최대 0.8458)를 보입니다.
- 즉, **"해외/타 거래소가 먼저 움직이고 빗썸이 뒤따라 움직인다"는 정보 흐름 자체는 실재**합니다.

### 2) 왜 테이커(Taker)는 수익을 낼 수 없는가?
- 빗썸의 호가 스프레드는 BTC 기준 약 `1.6 bps`, ETH 약 `2.9 bps`, XRP 약 `5.4 bps`, SOL 약 `4.8 bps` 수준입니다.
- 외부 신호 감지 후 빗썸에서 시장가(Taker)로 즉시 진입할 경우, 지불해야 하는 하프 스프레드(`0.8 ~ 2.7 bps`) 및 거래 수수료(`0.4 bps`)가 선행 신호가 창출하는 기대 총 이익(`0.5 ~ 1.5 bps`)을 완전히 잠식합니다.
- 결과적으로 124개 실험 중 **테이커로 순이익을 기록한 조건은 단 1개도 존재하지 않았습니다 (0 / 124)**.

---

## 4. 메이커(Maker) 전략과의 결합 가능성

- 본 연구 결과는 **"크로스 익스체인지 신호는 단독 테이커 알파가 아니라, 메이커의 역선택 방지(Toxic Flow Avoidance) 및 취소/재호가 가이드 신호로 사용되어야 함"**을 명확히 시사합니다.
- 바이낸스 급변 감지 시 빗썸에 대기 중인 호가를 즉시 취소하거나, 바이낸스 모멘텀 방향과 일치하는 호가에만 대기 순서를 점유하는 하이브리드 메이커 구조가 유일한 타당한 연구 방향입니다.

---

## 5. 결론 및 연구 상태

- **알파 상태 (Scientific Status)**: `ALPHA = UNPROVEN` 유지.
- **크로스 익스체인지 결론**: 해외/타 거래소의 빗썸에 대한 인과적 선행성은 확증되었으나, 독립적인 실행 가능 알파(Executable Alpha)는 입증되지 않음 (`PREDICTIVE_ONLY`).
- **후속 권고**: 메이커 시뮬레이터와 연계하여 외부 신호 기반 호가 취소(Adverse Selection Mitigation) 실험으로 전환.
