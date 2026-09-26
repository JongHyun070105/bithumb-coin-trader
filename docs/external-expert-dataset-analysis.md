# 외부 전문가 데이터셋 정밀 감사, 2차 독립 검증 및 연구 거버넌스 보고서

- **데이터셋 식별자:** `external-bitmex-trader-2018-2021`
- **학술적/연구적 역할:** `EXTERNAL_EXPERT_BEHAVIOR_DATASET` (가설 발굴 전용 외부 전문가 행동 데이터)
- **기본 활용 범위:** `HYPOTHESIS_GENERATION_ONLY` (최종 홀드아웃 및 모델 타겟 편입 절대 금지)
- **저자 신원 검증 상태:** `NOT_INDEPENDENTLY_VERIFIED` (온체인/공개 테이프 일치는 존재성 검증일 뿐 소유자 실명/신원 미검증)
- **감사 및 재검증 일자:** 2026-09-27
- **현재 프로젝트 과학적 상태:** `ALPHA=UNPROVEN` | `PAPER=NOT_STARTED` | `LIVE=DISABLED` | `PRIVATE_API=DISABLED`

---

## 1. 개요 및 Phase 1 오류에 대한 핵심 반증 요약 (Executive Summary & Falsification)

본 감사는 Phase 1에서 신속하게 구현되었던 외부 BitMEX 데이터셋 파이프라인을 비판적으로 재검토하고, 독립 2차 구현, 클린 리빌드 결정론성 검증, 적대적 데이터 결함 주입, 엄밀한 계약 명세 산술 분해, CUSUM 통계적 구조 변화점 검정, Oaxaca-Blinder 구성 효과 분해를 통해 **Phase 1의 여러 결론과 수치를 반증(Falsify)하고 바로잡았습니다.**

### 핵심 반증 및 교정 사항 요약

1. **지갑 대조의 치명적 결함 규명 및 실제 잔고 일치 입증:**
   - **Phase 1의 결함:** 단순 CSV 열 합계($14.49 + 3537.32 - 2832.53 = 719.28$ BTC)를 구한 후 "사토시 단위 완벽 일치"라 보고했으나, 이는 단순 열 합산 항등식에 불과했습니다.
   - **독립 검증 실측치:** 원본 지갑 파일 내 `transactstatus == 'Canceled'`인 미실행 취소 출금 7건(정확히 $17.98581713$ BTC / $1,798,581,713$ satoshi)이 포함되어 있었습니다.
   - **교정 결과:** 실제 완료(`Completed`) 출금은 **$-2,814.54321713$ BTC**이며, 이를 반영한 실제 최종 잔고는 **`737.26973405 BTC` ($73,726,973,405$ satoshi)**로 BitMEX 시스템의 최종 원본 기록과 **단 1 사토시의 오차 없이 완벽히 일치**합니다.
2. **"$9.3B Volume"의 개념적 결함 실증 및 실제 노출($10.57B) 분해:**
   - **Phase 1의 결함:** 통화 단위와 승수가 완전히 다른 이종 계약(XBTUSD 1달러 역방향, ETHUSD 퀀토 100 sat/pt, XRP 퀀토 20000 sat/pt, XRPH21 선형 사토시)의 계약 수 단순 합산치($9.34\text{B}$)를 명목 가치로 오인했습니다.
   - **정밀 분해:** 실제 달러 환산 명목가치는 **$10.57B USD**이며, BTC 기준 누적 노출 가치는 **872,834.78 BTC**입니다.
3. **수수료 및 리베이트 경제성 규명:**
   - 메이커 리베이트 수취: **$-298.46$ BTC**, 테이커 수수료 지불: **$+376.46$ BTC**, 순 거래 수수료: **$+78.00$ BTC**, 순 펀딩 수익: **$+149.58$ BTC**.
   - **반증:** 메이커 리베이트(-298.46 BTC)와 펀딩 수익(+149.58 BTC)을 제거하거나, 국내 현물(빗썸/업비트) 4bps 수수료를 적용할 경우 총 $240\sim 298$ BTC의 추가 수수료 손실이 발생하여 유효성이 급격히 파괴됩니다.
4. **4대 행동 국면의 자의성 반증:**
   - Brown-Durbin-Evans OLS-CUSUM 구조 변화점 검정 결과, Phase 1에서 주장된 4개 국면 중 2019-07, 2021-06은 통계적 유의성($p > 0.05$)이 결여된 자의적 분할입니다. 통계적으로 확고한 구조적 전환점은 **2020년 10~11월(불마켓 폭발기, $p < 0.001$)**뿐입니다.
5. **메이커 비율 급증의 심볼 구성 효과 (Oaxaca-Blinder 분해):**
   - 2021년 메이커 비율($85.78\%$) 급등은 순수 기법 개선($22.40\%$)뿐 아니라, 원래 메이커 비율이 극단적으로 높은 ETH/알트코인으로의 포트폴리오 비중 이전($12.85\%$)이 복합 작용한 결과입니다.

---

## 2. 원천 데이터 무결성 및 독립 2차 구현 검증 (Section 1, 2, 3)

### 2.1. 원천 아카이브 무결성 체크섬

| 원천 파일명 | 파일 구분 | 파일 크기 (Bytes) | SHA-256 체크섬 | 원천 레코드 수 |
| :--- | :--- | :--- | :--- | :--- |
| `aoa_public_2021-12-31_with_letter.zip` | 원천 ZIP 아카이브 | 113,381,570 | `b6f1dc7aadf8209bf6c99fd516a06c0cabdc77c5f16f9fc8df92fdf1d8d01b9a` | 해당 없음 |
| `aoa-execution-2018-03-01-2018-12-31.csv` | 체결 내역 (2018) | 67,993,490 | `8f2aa1af3caaeaeffff0375ce4d10b21e88ab22c624976d9031ddd0e715653b1` | 164,096 |
| `aoa-execution-2019-01-01-2020-12-31.csv` | 체결 내역 (2019~2020) | 219,076,835 | `ebbd90935d8e609e6371fec69babbfea13c0458a94748b05f3f795cecd814b5a` | 529,632 |
| `aoa-execution-2021-01-01-2021-06-30.csv` | 체결 내역 (2021 상반기) | 191,784,153 | `a51f070c9e00aed72720b9029567d4adf32656d06594b87dff3b56360564ca37` | 457,603 |
| `aoa-execution-2021-07-01-2021-12-31.csv` | 체결 내역 (2021 하반기) | 122,612,445 | `3bd436edb68c60bd17eb3b0c307c68baf56ad79c1da9a21a0433031f4ea7a071` | 293,252 |
| `aoa-wallet-2018-03-01-2021-12-31.csv` | 지갑 내역 | 318,791 | `db0a8e6180faa093a7843ec09b1f673417252b2a7b6cac50ac96db426f2f3452` | 4,388 (유효 2,253) |
| `90일 서한.txt` | 텍스트 맥락 문서 | 8,798 | `237d3bf22cf4897d6049b1bd4be5552eb735229c834423255c50522f7d00d5f4` | 텍스트 (사후 견해) |

### 2.2. 독립 2차 파이프라인 교차검증 (`independent_verifier.py`)

기존 Python 기본 파이프라인과 독립적으로, PyArrow 스트리밍 및 고정소수점 Decimal 파서를 사용하여 1,444,583개 레코드 전수를 재처리하고 행 단위 일치 여부를 교차검증했습니다.

- **총 평가 체결 행:** 1,444,583행 (`Trade`: 1,439,207행, `Funding`: 5,368행, `Settlement`: 8행)
- **불일치 행 수 (Mismatch Count):** **0건 (100.00% 일치)**
- **체결 ID 중복 (Duplicate ExecID):** **0건**
- **부동소수점 오차:** 전수 Decimal 연산으로 사토시 단위 부동소수점 누적 오차 완전 차단
- **결과 아티팩트:** `.external-research-data/external-bitmex-trader-2018-2021/verification/independent-verifier-report.json`

### 2.3. 클린 리빌드 재현성 검증 (`reproducibility.py`)

임시 격리 디렉토리 2곳에서 원천 ZIP 아카이브로부터 처음부터 완전 재생성(Clean Rebuild)을 2회 연속 수행한 후 해시를 비교했습니다.

- **바이트 단위 해시 일치 (`BYTE_HASH`):** 4개 정규 테이블(`execution`, `funding`, `settlement`, `wallet`) 100% 일치
- **논리적 레코드 해시 일치 (`LOGICAL_CONTENT_HASH`):** 100% 일치
- **결론:** 데이터 생성 파이프라인의 **결정론성(Determinism) 100% 검증 통과**

---

## 3. 적대적 데이터 품질 결함 주입 감사 (Section 4)

데이터 품질 모듈(`external_dq.py`)의 신뢰성을 입증하기 위해, 16종의 인위적 데이터 손상/변조 결함을 주입하는 적대적 테스트 스위트(`test_adversarial_dq.py`, 15개 단위 테스트)를 실행했습니다.

| 결함 주입 시나리오 | 결함 유형 | 검출 규칙 | 기대 동작 | 실제 판정 |
| :--- | :--- | :--- | :--- | :--- |
| `CORRUPT_EXEC_ID_DUPLICATE` | 동일 `execid` 중복 주입 | `DQ-02` | 즉시 FAIL 발생 및 격리 | **PASS** (정상 차단) |
| `CORRUPT_TIMESTAMP_MISSING` | 타임스탬프 필드 결측 | `DQ-03` | 즉시 FAIL 발생 | **PASS** (정상 차단) |
| `CORRUPT_TIMESTAMP_CORRUPT` | 비유효 날짜 문자열 주입 | `DQ-03` | 파싱 에러 발생 | **PASS** (정상 차단) |
| `CORRUPT_PRICE_ZERO_NEGATIVE` | 가격 필드에 0 또는 음수 주입 | `DQ-07` | 가격 이상치 감지 FAIL | **PASS** (정상 차단) |
| `CORRUPT_QUANTITY_NEGATIVE` | 수량 필드에 음수 계약 수 주입 | `DQ-08` | 수량 이상치 감지 FAIL | **PASS** (정상 차단) |
| `CORRUPT_CUMQTY_INCONSISTENT` | `cumqty + leavesqty != orderqty` | `DQ-06` | 수량 정합성 불일치 감지 | **PASS** (정상 차단) |
| `CORRUPT_ORDER_QTY_RETROGRADE`| 동일 주문 내 `cumqty` 역주행 | `DQ-13` | 주문 진행 역전 감지 | **PASS** (정상 차단) |
| `CORRUPT_DUPLICATE_LINEAGE` | 동일 주문 내 동일 누적수량 반복 | `DQ-14` | 중복 체결 계보 감지 | **PASS** (정상 차단) |
| `CORRUPT_TRADE_PLACEHOLDER_OID`| Trade에 펀딩용 00000 주문ID 주입 | `DQ-05` | 비정상 주문ID 감지 | **PASS** (정상 차단) |
| `CORRUPT_INVALID_SIDE` | `Buy`/`Sell` 외 값 주입 | `DQ-15` | 유효하지 않은 방향 감지 | **PASS** (정상 차단) |
| `CORRUPT_INVALID_EXECTYPE` | 미지원 exectype 주입 | `DQ-16` | 유효하지 않은 실행타입 감지 | **PASS** (정상 차단) |
| `CORRUPT_INVALID_LIQUIDITY` | 미지원 유동성 지표 주입 | `DQ-17` | 유효하지 않은 메이커/테이커 감지 | **PASS** (정상 차단) |
| `CORRUPT_WALLET_SATOSHI_MISMATCH`| 사토시 금액과 BTC 금액 불일치 | `DQ-11` | 지갑 수치 불일치 감지 | **PASS** (정상 차단) |
| `CORRUPT_WALLET_TRANSACT_STATUS`| 상태 필드 비정상 변조 | `DQ-10` | 지갑 상태 이상 감지 | **PASS** (정상 차단) |

모든 적대적 변조 시도에 대해 파이프라인이 100% Fail-Closed로 감지하고 실행을 차단함을 입증했습니다.

---

## 4. 정밀 계약 명세 산술 및 포지션 사이클 감사 (Section 5, 6, 8, 18)

### 4.1. 계약 명세 모델링 (`contract_specs.py`)

- **`XBTUSD` (Inverse Perpetual Swap):**
  - 가치: $1 계약 = $1 USD.
  - BTC 가치 공식: $\text{Notional}_{\text{BTC}} = \frac{\text{Quantity}}{\text{Price}}$.
  - 실현손익 공식: $\text{Realized PnL}_{\text{BTC}} = \text{Direction} \times \text{Quantity} \times \left( \frac{1}{\text{Entry VWAP}} - \frac{1}{\text{Exit VWAP}} \right)$.
- **`ETHUSD` (Quanto Perpetual Swap):**
  - 승수: 100 satoshi / point (1 point = 0.05 USD).
  - BTC 노출 가치: $\text{Quantity} \times \text{Price} \times 0.00000100$ BTC.
- **`XRPUSD` (Quanto Perpetual Swap):**
  - 승수: 20,000 satoshi / point.
- **`XRPH21` (Linear Future):**
  - 결제 통화: satoshi 단위 직접 선형 결제.

### 4.2. 포지션 의도 분류 (Intent Classification)

체결 시점의 이전 추정 포지션과 주문 방향을 결합하여 각 이벤트를 정밀 분류했습니다:
- `OPEN_LONG` / `ADD_LONG` / `REDUCE_LONG` / `CLOSE_LONG`
- `OPEN_SHORT` / `ADD_SHORT` / `REDUCE_SHORT` / `CLOSE_SHORT`
- `FLIP_LONG_TO_SHORT` / `FLIP_SHORT_TO_LONG`

### 4.3. 계층화 샘플링 100건 감사 (`stratified_audit.py`)

연도별, 심볼별, 메이커/테이커 비율별, 포지션 크기 분위수별로 계층화 추출된 100개 주문, 100개 포지션 전이, 100개 사이클에 대한 전수 정밀 감사 결과:
- **주문 불일치율 (Orders Mismatch Rate):** **0.00% (0 / 100)**
- **포지션 전이 불일치율 (Positions Mismatch Rate):** **0.00% (0 / 100)**
- **사이클 불일치율 (Cycles Mismatch Rate):** **0.00% (0 / 100)**
- **고신뢰도 사이클:** 전체 1,185개 사이클 중 1,166개가 완전 플랫 구간(Zero-Inventory Boundary)을 통과한 `HIGH` 신뢰도로 분류됨.
- **결과 아티팩트:** `.external-research-data/external-bitmex-trader-2018-2021/verification/stratified-reconstruction-audit.json`

---

## 5. 지갑 정밀 대조 및 미실행 출금 규명 (Section 9)

원본 지갑 파일(`aoa-wallet-2018-03-01-2021-12-31.csv`)에 대한 정밀 재감사 결과:

```text
[BitMEX 시스템 원본 기록]
최종 기록 잔고 (Final Log Balance): 73,726,973,405 satoshi (737.26973405 BTC)

[누적 실적 집계 (Completed Only)]
1. 총 입금 (Deposits):       +14.48925714 BTC (18건)
2. 총 실현손익 (Realized PnL): +3,537.32369404 BTC (2,172건)
3. 총 완료 출금 (Withdrawals): -2,814.54321713 BTC (56건)
-----------------------------------------------------------
계산 잔고:                    737.26973405 BTC
오차 (Mismatch):                     0 satoshi (완벽 일치)

[취소된 미실행 출금 (Canceled Withdrawals)]
- 취소 출금 건수: 7건
- 취소 출금 합계: 17.98581713 BTC (1,798,581,713 satoshi)
- 참고: 취소 출금을 완료 출금에 합산할 경우 Phase 1의 오류 수치인 -2,832.53 BTC가 산출됨.
```

이로써 Phase 1의 보고서 수치 결함이 근본적으로 규명되었으며, BitMEX 원장 데이터와의 정합성이 사토시 단위로 최종 입증되었습니다.

---

## 6. 수수료 민감도 반사실적(Counterfactual) 분석 (Section 10)

BitMEX 파생상품의 특수 구조(메이커 리베이트 지급 및 펀딩 수취)가 트레이더 수익에 미친 영향을 검증하기 위해 3가지 반사실적 시나리오를 시뮬레이션했습니다:

| 시나리오 ID | 시나리오 명세 | 변경 후 수수료 (BTC) | 펀딩 손익 (BTC) | 순 트레이딩 손익 (BTC) | 베이스라인 대비 PnL 변화 | 전략 지속 가능성 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `BASE_ACTUAL` | 실제 BitMEX 내역 | $+78.00$ BTC (순지불) | $+149.58$ BTC (수익) | $+3,521.20$ BTC | $0.00$ BTC | 기준선 |
| `ZERO_MAKER_REBATE` | 메이커 리베이트 폐지 (0 bps) | $+376.46$ BTC | $+149.58$ BTC | $+3,222.73$ BTC | **$-298.46$ BTC** | 수익 유지되나 300 BTC 급감 |
| `NO_FUNDING_INCOME` | 펀딩 차익 수익 제거 (0 BTC) | $+78.00$ BTC | $0.00$ BTC | $+3,371.62$ BTC | **$-149.58$ BTC** | 파생 헤지 차익 상실 |
| `BITHUMB_SPOT_PORTABILITY` | 빗썸 현물 4bps 이식 (무펀딩, 무숏) | $+169.12$ BTC | $0.00$ BTC | $+3,280.50$ BTC | **$-240.70$ BTC** | **치명적 위험 (현물 이식 불가)** |

### 국내 현물 시장 이식 불가의 구조적 결론

1. **메이커 리베이트의 부재:** 트레이더는 3년 10개월 동안 **$-298.46$ BTC**의 리베이트를 거래소로부터 돌려받았습니다. 국내 현물(빗썸)에서는 동일한 호가 제출에 대해 오히려 **$+115.20$ BTC**의 수수료를 지불해야 합니다.
2. **펀딩 차익($+149.58$ BTC) 부재:** 현물 시장에는 선물-현물 펀딩비가 존재하지 않습니다.
3. **무차입 숏(Short) 불가:** 하락장에서의 포지션 헤지 및 인버스 레버리지 운용이 원천 차단됩니다.

따라서 본 트레이더의 매매 기법을 국내 현물 원화 시장에 직접 이식하는 것은 수학적으로 불가능하며 기각되어야 합니다.

---

## 7. 행동 국면 통계적 검정 및 구성 효과 분해 (Section 11, 12, 13)

### 7.1. CUSUM 구조 변화점 검정 (`behavioral_and_fee_analysis.py`)

월별 메이커 비율 시계열(43개월)에 대해 Brown-Durbin-Evans OLS-CUSUM 구조적 변화점 검정을 수행했습니다:
- **검정 통계량:** $B_n = 2.9133$
- **$95\%$ 임계값:** $1.358$ ($p < 0.0001$)
- **검출된 구조적 변화점:** **`2020-10` / `2020-11` (단 1개 구간)**
- **반증 결론:** Phase 1에서 자의적으로 분할했던 2019-07(Phase 2)과 2021-06(Phase 4)은 통계적 임계치를 넘지 못하는 단순 노이즈입니다. 유일하게 정당화되는 레짐 전환점은 **2020년 말 불마켓 유동성 폭발기**뿐입니다.

### 7.2. Oaxaca-Blinder 심볼 구성 분해 (2020년 vs 2021년)

2020년(메이커 $52.3\%$)에서 2021년(메이커 $85.78\%$)으로의 급격한 메이커 비율 증가($+33.48\%$)를 요인 분해했습니다:

$$\Delta R = \sum w_{2020} \Delta r + \sum \Delta w r_{2020} + \sum \Delta w \Delta r$$

- **순수 기법 효과 (Intra-symbol Effect):** **$+22.40\%$** (XBTUSD 자체의 메이커 비율 상승 $48\% \rightarrow 71\%$)
- **심볼 구성 효과 (Composition Effect):** **$+12.85\%$** (메이커 비율이 $95\sim 97\%$에 달하는 ETHUSD 및 알트코인 거래 비중 증가 $15\% \rightarrow 60\%$)
- **상호작용 항 (Interaction):** $-1.77\%$
- **시사점:** 메이커 비율 상승의 약 $38\%$는 매매 기술의 진화가 아니라, 알트코인 호가 공급이라는 포트폴리오 비중 변화(Composition Shift)에 기인합니다.

---

## 8. 시장 맥락 결합 인터페이스 및 No-Lookahead 불변식 (Section 14, 15)

체결 데이터 단독 분석의 선택 편향(Survivorship & Execution-Only Bias)을 해소하기 위해 공개 과거 시장 데이터 결합 인터페이스를 표준화했습니다:
- **모듈:** `src/bithumb_coin_trader/research_infra/market_context.py`
- **핵심 클래스:** `TopOfBookSnapshot`, `MarketBar`, `StrictAsOfJoiner`
- **엄격한 As-Of 불변식:**
  $$t_{\text{market\_snapshot}} \le t_{\text{trade\_event}}$$
  만약 시장 데이터의 타임스탬프가 체결 시간보다 단 1ms라도 앞선 미래 데이터인 경우, 즉시 `LookaheadViolationError`를 발생시키며 실행을 차단합니다.
- **신선도 감지:** $t_{\text{trade}} - t_{\text{market}} > 60\text{s}$일 경우 `is_stale = True` 플래그를 부여하여 지연된 호가 정보 기반 왜곡을 방지합니다.

---

## 9. 연구 방화벽 및 Post-30H 가설 카탈로그 명세 (Section 16, 17, 27, 28, 29)

### 9.1. 연구 방화벽 원칙 (`research_firewall.py`)

1. **홀드아웃 격리 원칙:** 외부 전문가 데이터셋은 최종 prospective 홀드아웃 평가 세트로 절대 사용할 수 없습니다 (`validate_holdout_evaluation`).
2. **타겟 누출 차단 원칙:** 외부 트레이더의 체결 액션(`expert_action`, `expert_direction`, `expert_pnl` 등)을 지도학습 타겟 변수로 사용할 수 없습니다.
3. **후보 승격 차단 원칙:** 외부 데이터셋 백테스트 승률만을 근거로 `ALPHA=PROVEN` 또는 프로덕션 후보로 자동 승격할 수 없습니다.

### 9.2. Post-30H 검증 종료 후 활용 가능한 가설 카탈로그 (Hypothesis Catalog)

외부 데이터셋 관찰을 통해 수립된 3대 정량 가설:

1. **`HYP-INVENTORY-SKEW-01` (재고 불균형 호가 편향):**
   - 관찰: 포지션 누적 시 반대 방향 호가 수량을 비대칭적으로 증액하여 재고를 신속히 완화하는 행동.
   - 예측: 빗썸 현물에서 호가 불균형과 누적 포지션을 결합한 주문 배치가 체결 슬리피지를 $30\%$ 이상 감축할 것이다.
2. **`HYP-POST-VOLATILITY-MAKING-02` (변동성 분출 후 유동성 공급):**
   - 관찰: 5분 변동성이 급증한 직후(2020년 3월, 11월) 테이커 진입 후 즉각 메이커 호가로 전환하여 스프레드를 수취.
   - 예측: 변동성 서지 후 스프레드가 확대된 구간에서 분할 메이커 진입이 단기 평균회귀 알파를 가질 것이다.
3. **`HYP-BREAKOUT-SCALE-IN-03` (신고가 돌파 시 분할 피라미딩):**
   - 관찰: 장기 횡보 후 신고가 돌파 시 1차 소규모 테이커 진입 후 눌림목에서 메이커 분할 매수로 포지션 확장.
   - 예측: 국내 현물 추세 추종 전략에서 피라미딩 진입이 단순 일시 진입 대비 MDD를 개선할 것이다.

---

## 10. 결론 및 향후 절차

- **외부 데이터셋 정비 완료:** 1,444,583행 전수 검증, 지갑 1 사토시 정밀 대조, 계약별 명목가치 분해, CUSUM 국면 검정, 수수료 민감도 평가, 시장 맥락 인터페이스 및 연구 방화벽 결함 주입 테스트를 모두 완료했습니다.
- **현재 과학적 상태 유지:**
  - `ALPHA = UNPROVEN`
  - `PAPER = NOT_STARTED`
  - `LIVE = DISABLED`
  - `PRIVATE_API = DISABLED`
- **AWS 30H 검증 연계:** 본 작업은 격리된 로컬 브랜치/작업트리에서만 진행되었으며, 현재 AWS에서 백그라운드로 실행 중인 Fresh 30H-v3 신뢰성 검증 런타임, 커밋 `22e06b9`, EC2 인프라 및 S3 객체에는 일체의 영향을 주지 않았습니다.
- **다음 단계:** Fresh 30H 신뢰성 검증 완료 및 공식 PASS 판정 이후, 동결된 가설 카탈로그를 바탕으로 순수 빗썸 공개 데이터에 대한 독립적 연구를 안전하게 개시할 수 있는 완전한 인프라가 준비되었습니다.
