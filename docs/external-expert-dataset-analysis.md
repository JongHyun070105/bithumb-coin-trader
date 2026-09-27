# 외부 전문가 데이터셋 정밀 감사, 2차 독립 검증 및 연구 거버넌스 보고서

> **2026-09-27 independent senior audit supersedes the conclusions below.** The earlier sections are retained as historical evidence, not current results. In particular, the asserted $10.57B exposure, 2020-10/11 OLS-CUSUM break and p-value, 52.3%/85.78% annual maker rates, +12.85pp composition effect, absolute position-cycle labels, profitability/portability conclusions, and claimed full reproducibility were not independently supported as stated. See the audit appendix at the end before using any earlier number.

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

---

## 11. Independent Senior Audit — 2026-09-27

### 11.1 Scope, evidence, and disposition

This audit reviewed the PR #17 branch at `c1605a45614292db5772fd3ada2d366f2c4f5cca` and the raw source files independently. The independent claim script uses only Python's CSV reader and `Decimal`; it does not call the production ingester, verifier, contract mapper, wallet reconciler, behavior analyzer, or decomposition code. Separate market and behavior scripts consume public source files and emit ignored artifacts under `.external-research-data/`.

PR #17 is still a Draft. Its current diff from `main` spans 50 commits, 279 files, approximately 50,860 additions and 541 deletions. The scope combines reliability infrastructure, dataset ingestion/reconstruction, audit outputs, research governance, and market-context work. It is too broad for a single substantive review, but this audit did not rewrite its history. Safest future decomposition: (A) ingestion/provenance/DQ, (B) contract and reconstruction semantics, (C) wallet/behavior/statistical audit, (D) market context and joins, (E) research governance/handoff. Create stacked PRs only after agreeing on ownership and dependency boundaries; preserve this Draft and its evidence in the meantime.

### 11.2 Module-by-module code audit

| Area | Status | Audit finding |
|---|---|---|
| Independent verifier | `UNVERIFIED` | It has an independent PyArrow read path, but its baseline is hardcoded/stale, a discrepancy can be forced to `DISCREPANCY_EXPLAINED`, and verdict execution is not bound to source checksums. It is not independent proof of every prior summary. |
| Reproducibility | `TRUSTED_WITH_LIMITATIONS` | Logical hashing now combines Arrow chunks; a pre-existing destination is refused instead of recursively deleted, and CLI output uses a unique child directory. Row-order normalization is useful. Byte hashes remain informational and the clean rebuild covered a sample, not the whole provenance lifecycle. |
| Canonical ingestion | `NEEDS_FIX` | Monetary values are converted through float64 in parts of the path; sorting is batch-local rather than globally guaranteed; invalid timestamps can be skipped without a complete rejection ledger. Do not treat it as an exact canonical accounting ledger yet. |
| DQ | `NEEDS_FIX` | Archive row mismatch is only a warning; one provenance-key check is tautological; the old wallet continuity path did not properly exclude canceled withdrawals and used floating point. Adversarial unit tests do not establish fail-closed behavior for every source-level fault. |
| Contract specifications | `NEEDS_FIX` | ETHUSD USD notional was calculated as contracts × price despite quanto semantics; XRPUSD notional omitted its price factor; prefix fallback can misclassify XBTUSDT; historical contract/fee provenance is incomplete. Independent raw contract counts therefore do not validate the prior USD-equivalent total. |
| Order reconstruction | `TRUSTED_WITH_LIMITATIONS` | Groups observed fills, but cannot recover unfilled/canceled orders or queue state. Mixed order IDs and status history limit behavioral interpretation. |
| Position reconstruction | `TRUSTED_WITH_LIMITATIONS` only with supplied snapshots | Missing per-symbol initial inventory now produces `AMBIGUOUS`/low confidence. Direct flips split into close/open events. This dataset has no independent initial-position snapshot, so absolute OPEN/ADD/REDUCE/CLOSE/FLIP intent remains `NOT_IDENTIFIABLE`; all flat-start labels are conditional. |
| Cycle reconstruction | `TRUSTED_WITH_LIMITATIONS` | Flip events and left-censored initial state are handled more carefully. Average-cost PnL is approximate, funding attribution is incomplete, and there is no starting inventory here. No cycle-performance result from this dataset is high-confidence. |
| Stratified reconstruction audit | `NEEDS_FIX` | The old “100 samples, zero mismatches” check largely checks vocabulary/sign/type or invokes the same production semantics; it does not independently recompute position/cycle state, and its symbol/year strata are not representative. |
| Wallet reconciliation | `TRUSTED_WITH_LIMITATIONS` after fix | Completed-only cash flows exclude canceled withdrawals, unknown statuses are counted, final balance continuity is calculated, and trade PnL anchoring is explicitly `NOT_IDENTIFIABLE`. This validates the raw cashflow identity, not trading profitability. |
| Behavioral / fee production analysis | `NEEDS_FIX` | The old module contains hardcoded monthly ratios, fee/notional assumptions and toy weights. The raw Decimal audit and sensitivity script below supersede its claims; the old production analysis still needs refactoring before use. |
| CUSUM | `NEEDS_FIX` | The result labeled OLS-CUSUM is a simple cumulative-deviation screen on a hardcoded/untraceable series, not recursive-residual Brown–Durbin–Evans OLS-CUSUM. Its reported p-value is unsupported. |
| Oaxaca/composition decomposition | `TRUSTED_WITH_LIMITATIONS` as an identity only | The algebraic identity is correct, but old input rates/weights are not raw-derived. A separate raw fill-count decomposition reproduces the identity and materially changes all three terms. |
| Market context | `TRUSTED_WITH_LIMITATIONS` | The joiner now isolates symbols and excludes same-time bars, and checks finite nonnegative lookback. Acquired data are hourly; no spread/depth/queue/liquidation fields exist, and 170,772 other-instrument fills are unmatched. |
| Research firewall | `TRUSTED_WITH_LIMITATIONS` after fix | External-expert data are blocked from training/promotion and non-finite promotion metrics fail closed. This is a provenance policy gate, not evidence of statistical quality or alpha. |
| `external_behavior.py` | `NEEDS_FIX` | Raw quantity units are mixed across symbols, feature/phase definitions are hardcoded, and several risk/percentile claims cannot be reproduced from a traceable input series. |

The most consequential fixes in this audit are: removing destructive rebuild behavior, fail-closing on absent starting position, splitting flips and marking left-censored cycles, filtering wallet cash flows by completed status, and blocking external-expert training/promotion including NaN metrics. The incorrect contract-notional code and hardcoded legacy analysis remain unresolved and block treating PR #17 as scientifically ready.

### 11.3 Independent raw re-computation of reported claims

| Claim | Independent result | Disposition |
|---|---:|---|
| Wallet final balance | `737.26973405 BTC` (73,726,973,405 sat) | `MATCH`. Recomputed as completed deposits + realized PnL − completed withdrawals; equals the final logged balance. |
| Canceled withdrawals | 7 events; `17.98581713 BTC` absolute | `MATCH`. Excluded from completed cash flow. |
| XBTUSD contracts | 23,574,456,258 absolute `lastqty` | Reported 9,258,603,804 does not match raw gross fills. |
| ETHUSD contracts | 170,343,814 absolute `lastqty` | Reported 13,777,628 does not match raw gross fills. |
| XRPUSD contracts | 40,789,838 absolute `lastqty` | Reported 29,413,562 does not match raw gross fills. |
| Aggregate USD-equivalent exposure | `NOT VERIFIED` | Reported ≈$10.57B is not reproducible from the asserted component contract counts. XBTUSD alone is $23.57B gross contract turnover at $1/contract; ETH/XRP require correct historical quanto terms and dated specs. Gross turnover is not position exposure or PnL. |
| Maker rebate / taker fees / net trading fees | −298.46242759 / +376.46255730 / +78.00012971 BTC | Raw `execcomm` values reproduce rounded claims. |
| Funding | −14,957,544,093 sat in signed `execcomm`; +149.57544093 BTC credit if negative denotes income | Rounded claim matches under that sign convention. It is not USD-comparable without dated conversion and position attribution. |
| Annual maker ratio, fill weighted | 2018 49.1124%; 2019 45.0248%; 2020 46.9184%; 2021 85.8912% | 2020's stated 52.3% is wrong under the raw known-liquidity fill denominator. 2021's 85.78% is close but not exact. |
| 2020→2021 composition | total +38.9728pp = within-symbol +25.2979pp + composition +17.9529pp + interaction −4.2780pp | Raw XBTUSD/ETHUSD/OTHER fill-count decomposition. Prior +22.40pp / +12.85pp / −1.77pp is not supported by these raw inputs. Identity error < 1e−15. |
| Structural break | No uniquely supported breakpoint | The exact 2020-10/11 OLS-CUSUM p<0.001 claim is unsupported; see §11.5. |

Raw executions include 1,439,207 Trade rows, 5,368 Funding rows and 8 Settlement rows. Trade timestamps span 2018-03-05 through 2021-12-24. These are raw-CSV/Decimal results in `.external-research-data/external-bitmex-market-context-2018-2021/derived/independent-raw-claims-audit.json`; the source archive and derived raw data remain untracked.

### 11.4 Historical public market data acquired and cross-checked

The resumable acquisition script fetched **92 checksummed official Binance Public Data hourly archives** (BTCUSDT and ETHUSDT, each month from 2018-03 through 2021-12), **120 BitMEX hourly bucket pages** (XBTUSD and ETHUSD), and **25 BitMEX funding-history pages**. The tracked per-artifact ledger [historical-source-manifest.json](../research-data/external/external-bitmex-market-context-2018-2021/historical-source-manifest.json) records provider, endpoint/dataset, instrument, month/coverage, frequency, download UTC time, archive and publisher checksum, and limitations; the tracked ledgers use [source-manifest.schema.json](../research-data/external/external-bitmex-market-context-2018-2021/source-manifest.schema.json). Required fields, UTC timestamps, and SHA-256 shapes were checked directly; the local environment lacks `jsonschema` for a formal schema-validation run. Raw artifacts remain gitignored. Binance's official repository describes monthly klines and `.CHECKSUM` files and notes archives can be corrected; BitMEX documents bucket timestamps as interval ends and bucket open as the previous bucket close ([Binance Public Data](https://github.com/binance/binance-public-data/blob/master/README.md), [BitMEX Trade Bucketed](https://docs.bitmex.com/api-explorer/get-trade-bucketed)).

Canonical execution-aligned series is BitMEX hourly data; Binance spot is an independent cross-check/fallback, never silently mixed into the canonical series. BTCUSDT and ETHUSDT use USDT while BitMEX XBTUSD/ETHUSD use USD; the measured cross-venue differences include market basis and USDT/USD differences, so they are not pure data-error rates.

| Instrument | BitMEX hourly rows / missing from 33,648-hour grid | Binance rows / missing from grid | Aligned | Absolute close difference median / p95 / max |
|---|---:|---:|---:|---:|
| XBTUSD vs BTCUSDT | 33,648 / 0 | 33,561 / 87 | 33,561 | 10.12 / 118.13 / 1,022.98 bps |
| ETHUSD vs ETHUSDT | 29,942 / 3,706 (leading coverage gap; none internal after first bar) | 33,561 / 87 | 29,874 | 15.91 / 122.36 / 923.40 bps |

Volume differences are not expected to match: BitMEX `foreignNotional` is USD-equivalent contract turnover, while Binance quote-asset volume is USDT spot turnover. Their median BitMEX/Binance ratios were 3.91 for BTC and 1.36 for ETH; this is venue turnover, not price-series disagreement. Acquired features include hourly OHLCV/contract volume, rolling returns, realized volatility, ATR/range, trend and volume regimes, and latest as-of funding sign. Historical spread, depth, queue position, liquidation flow, and order-flow imbalance were unavailable and are not imputed.

A separate public-only current spot snapshot was acquired on 2026-09-27 at 04:38 UTC from Bithumb and Upbit for KRW-BTC/KRW-ETH: nine HTTP 200 responses covering market lists, order books, 200 hourly candles per market, and Upbit orderbook instruments. Its checksummed per-response ledger is committed as [current-spot-20260927-source-manifest.json](../research-data/external/external-bitmex-market-context-2018-2021/current-spot-20260927-source-manifest.json); the response bodies and derived snapshot remain gitignored under `.external-research-data/.../current-spot/20260927T043803.759584Z/`. An independent pass verified **246/246** historical/current manifest entries against the downloaded raw bytes. The 200 hourly bars span 2026-09-18 21:00 through 2026-09-27 04:00 UTC with zero internal gaps. This is a point-in-time spot sample, not a persistent 2026 market characterization.

| KRW pair | Bithumb 7d RV / spread / top-5 bid-ask depth / 200h turnover | Upbit 7d RV / spread / top-5 bid-ask depth / 200h turnover | Cross-venue close difference |
|---|---|---|---|
| BTC | 4.337% / 0.087 bps / ₩30.39M–₩36.98M / ₩357.38B | 4.427% / 6.437 bps / ₩7.72M–₩16.84M / ₩1.154T | 200 aligned bars; median 2.879 bps, p95 9.512 bps |
| ETH | 4.776% / 2.723 bps / ₩521.82M–₩1.272B / ₩316.29B | 4.871% / 2.724 bps / ₩499.56M–₩837.08M / ₩865.88B | 200 aligned bars; median 2.765 bps, p95 8.485 bps |

The current single 7-day realized volatility is below the rolling 2018–2021 BitMEX 7-day distribution's 10th percentile for both assets: XBTUSD historical p10/median/p90 = 4.731%/8.529%/15.772% across 33,480 overlapping windows; ETHUSD = 6.753%/11.012%/19.148% across 29,774 windows. These rolling windows use the square root of the sum of 168 hourly squared log returns and are dependent; current venue, currency, and period differ, so this is context only, not a regime inference. The BTC top-of-book spread also moved from 0.174 to 0.087 bps on Bithumb and 1.304 to 6.437 bps on Upbit across two snapshots about five minutes apart, underscoring that the single book observation is not a stable venue property. Upbit's public orderbook policy endpoint reports ₩1,000 tick size for both sampled pairs. Bithumb tick size, minimum order/lot, and account fee schedule remain unknown here; no private endpoint was called.

### 11.5 As-of join, regimes, intent, and behavior

The join covers **1,268,435 of 1,439,207** Trade fills; **170,772** fills in other instruments remain unmatched. XBTUSD joined 941,007/941,007 and ETHUSD 327,428/327,428. The feature join uses a completed context bar with `context_timestamp < execution_timestamp` (stricter than `<=`, excluding same-time bars). Independent full-file recheck: `FUTURE_LEAKAGE_COUNT=0`; maximum context age 3,599.992 seconds, median 1,683.284 seconds, p95 3,393.010 seconds. The audit is in `execution-context-join-audit.json`. Hourly features cannot resolve the requested ±5m/±15m event windows.

Regime rules use prior observations only: 24-hour realized volatility, 14-bar ATR percentage, 24-hour return/trend, prior 168 observed hourly buckets for volatility/volume thresholds (minimum warmup 120), and the latest funding rate available by the completed bar. Entry counts are small: high-vol 217 maker / 402 taker, low-vol 144 / 169; negative-funding 105 / 312, positive-funding 260 / 271. These are descriptive fill counts under assumed-flat intent reconstruction, not causal evidence that volatility, trend, or funding changes aggressiveness.

Position state supports OPEN_LONG, ADD_LONG, REDUCE_LONG, CLOSE_LONG, OPEN_SHORT, ADD_SHORT, REDUCE_SHORT, CLOSE_SHORT, flips, and AMBIGUOUS. However, with no independent initial inventory, absolute intent is `NOT_IDENTIFIABLE`; observed “ENTRY/EXIT/ADD/REDUCE/FLIP” results below are conditional on starting flat. Complete conditional reconstruction has 3,176 cycles and 9 open at the terminal boundary, but **0 HIGH_CONFIDENCE cycles**. Consequently, high-confidence event study and cycle-only attribution are `NOT_IDENTIFIABLE`.

| Conditional descriptive result | Estimate |
|---|---:|
| Entry taker share | 57.384% |
| Close-only exit taker share | 57.228% |
| Exit minus entry taker share | −0.156pp |
| XBTUSD fill-to-fill adverse-add proxy / favorable-or-flat | 76,483 / 390,916 |
| ETHUSD fill-to-fill adverse-add proxy / favorable-or-flat | 5,551 / 153,847 |

The entry/exit difference does not support a claim that exits are more taker aggressive. Fill-to-fill direction is a crude prior-fill reference, not an observed market path or account PnL. Maker rates rose within both primary symbols (XBTUSD 45.10%→71.19%; ETHUSD 70.99%→95.05%, 2020→2021), so the raw aggregate change is not solely composition. Still, the leave-one-year-out annual maker-share slope is −0.01097/year when 2021 is omitted: the apparent multiyear increase is driven by 2021. No queue data or unfilled orders identify passive execution skill.

### 11.6 Uncertainty, structural-break methods, robustness, and falsification

Uncertainty estimates use seeded 7-day moving blocks over active UTC days (500 bootstrap replicates; median metrics 250), not iid resampling. The full year-by-year estimates and 95% block intervals for maker/taker share, fills/order, median contract order size, conditional maker-at-entry, conditional maker-at-close, per-active-day fee contribution, per-calendar-day funding contribution, and conditional cycle duration are stored in `execution-behavior-audit-v3.json`. Illustrative 2021 results: maker 85.89% [83.13, 88.06], taker 14.11% [12.06, 16.82]; fills/order 189.78 [155.33, 226.02]; conditional maker at entry 28.57% [15.94, 40.91] and close 73.24% [60.56, 85.06]; median cycle duration XBT 55,875s [37,211, 133,545], ETH 70,603s [21,246, 196,941] (conditional sample sizes 125 and 64); maker fee contribution −0.273 BTC/active day [−0.344, −0.206], taker contribution +0.187 [0.149, 0.231]; signed funding credit +0.477 BTC/calendar day [0.194, 0.789]. Sparse entry/exit and cycle samples yield broad intervals. Fee/funding intervals describe cash flows and are not return estimates.

These intervals are stratified by calendar year, not by robust market regime. Regime-specific block intervals and leave-one-year-out fits for assumed-flat intent labels were not treated as valid confirmation: the initial-position ambiguity affects those labels directly. The annual aggregate maker-share leave-one-year-out and two-symbol replication are the only completed LOO-style robustness checks. A 2026 Bithumb/Upbit public snapshot narrows contemporaneous spread/depth/volatility observations but does not establish persistent venue conditions; historical execution transfer remains unknown where comparable evidence is absent.

The old “OLS-CUSUM” result fails review: its series is hardcoded/untraceable, its implementation is not recursive-residual OLS-CUSUM, and monthly proportions have variable denominators, serial dependence, and likely overdispersion. There are only 46 observed months (beginning March 2018), not the previously reported 43-month series. Re-analysis used raw monthly maker fills over known-liquidity fills, descriptive CUSUM and CUSUMSQ screens (no p-values), rolling three-month divergence, and exact dynamic-programming binomial piecewise-constant segmentation across minimum segment lengths and BIC-style penalties. This is not a validated Bai–Perron or `ruptures` PELT implementation. Results:

- `ROBUST_BREAKS = []`.
- `METHOD_DEPENDENT_BREAKS` include 2020-10 with a 6-month minimum segment, 2020-12 with a 3-month minimum, and many dates in other segments. CUSUM screen's maximum is before 2020-11; CUSUMSQ's maximum is before 2020-03; rolling 3-month divergence is largest at 2020-04 (56.44% vs 26.10%).
- `UNSUPPORTED_BREAKS`: the single 2020-10/11 OLS-CUSUM break with p<0.001. No unique breakpoint is identified. Trend vs breaks, serial dependence, heteroskedasticity/overdispersion, multiple testing, and short monthly sample all remain unresolved.

Adversarial robustness findings:

- **Leave one year out:** omitting 2021 reverses the annual maker-share slope; bubble-era dominance is material.
- **Symbol holdout-style check:** ETHUSD and XBTUSD both rise from 2020 to 2021, but this is descriptive replication, not a formal holdout.
- **Outlier/weight sensitivity:** overall fill-weighted maker share is 67.16%, equal-active-day 57.57%. XBTUSD is 53.60% by fills, 59.12% equal-order and 54.03% log-size weighted; removing the largest 1% or 0.1% by a tied-size cutoff barely changes it. ETHUSD is 92.06% by fills but 71.35% equal-order, a large weighting sensitivity. No high-confidence-cycle-only result exists.
- **Placebo:** approximate 999-draw shuffle of conditional entry/close labels inside symbol × volatility × funding-sign blocks gives observed exit-minus-entry maker share +0.310pp, approximate p=0.889, null 95% interval [−4.355pp, +4.097pp]. Normal-approximation hypergeometric draws and fill shuffling do not preserve temporal clusters; treat this as exploratory, not an inferential p-value.
- **Falsification:** symbol composition changes part but not all of the aggregate maker rise; removing 2021 removes the positive time slope; equal-order weighting changes ETH materially; conditional entry/exit effect is near zero and placebo-like; no order book exists to test passive queue skill; and fee/rebate removal is large relative to plausible execution-cost differences. No directional or execution hypothesis survives as a validated edge.

### 11.7 Fee/rebate sensitivity and domain-transfer assessment

An independent Decimal grid evaluates five schedules (maker/taker −2.5/7.5, 0/7.5, 1/4, 4/4, 10/10 bps), 50/75/100% of recorded maker/taker turnover assumed filled, and 0/1/3/5 bps additional slippage per side: 60 combinations. BTC/ETH recorded gross USD-equivalent turnover proxies are approximately $23.574B XBTUSD and $3.939B ETHUSD, with the quanto conversion limitations above. Under historical-like fees and 100% observed turnover, transaction cost ranges from a $169.7k credit at zero added slippage to $2.582M cost at 1bp, $8.084M at 3bps, and $13.587M at 5bps. At 4/4bps and zero additional slippage, cost is $11.005M. These are cost sensitivities only; they do not reconstruct PnL, fill probability, borrowing costs, or a Bithumb forecast. Removing funding is separately +149.575 BTC historical cashflow credit and cannot be netted into USD without dated conversion and position attribution.

| Dimension | BitMEX derivatives, 2018–2021 | Korean BTC spot in 2026 | Transfer classification |
|---|---|---|---|
| Fees/rebates | Observed maker rebate and taker fee; historical rate/model must be tied to contract and date | Per-market/account fee schedule can change; order-chance endpoint is private and was not called | `STRUCTURALLY_NON_TRANSFERABLE` for historical rebate economics; current rate `UNKNOWN` |
| Shorting / leverage | Inverse/quanto derivatives and leverage; contract payoff differs by instrument | Spot holdings do not create a native short or the same leverage/payoff | `STRUCTURALLY_NON_TRANSFERABLE` |
| Funding | Perpetual funding cashflows exist and were observed | Spot has no perpetual funding transfer | `STRUCTURALLY_NON_TRANSFERABLE` |
| Contract payoff | XBT inverse and ETH/XRP quanto conversion; historical contract specs needed | KRW-denominated spot units with ordinary asset ownership | `STRUCTURALLY_NON_TRANSFERABLE` for sizing/payoff; instrument-specific research required |
| Spread and depth | Historical BitMEX book snapshots/queue state absent; fills alone do not reveal these | One synchronized public snapshot measured spread and top-five KRW depth for both pairs; it is time-local | `UNKNOWN` for historical behavior transfer |
| Tick and lot size | Historical contract increments require dated contract specs | Upbit current tick was ₩1,000 for both pairs; Bithumb tick and both venues' minimum/lot constraints were not established | `UNKNOWN` |
| Latency and partial fills | Source has realized fills and coarse order metadata only | Venue/network/API path differs; no request/acknowledgement or queue telemetry collected | `UNKNOWN` |
| Participant composition | No participant-level labels in the external fills | Current venue participant mix was not measured and may differ by market | `UNKNOWN` |
| Market efficiency | No counterfactual order-book execution or independent price-efficiency estimate | No synchronized current Bithumb/Upbit study in this audit | `UNKNOWN` |
| Volatility | BitMEX/Binance BTC/ETH hourly history acquired for 2018–2021; BTC/ETH historical weekly p10/median/p90 shown above | One synchronized latest 200-hour KRW-spot window acquired; observed 7d RV was below historical BitMEX p10 | `TRANSFERABLE_WITH_ADAPTATION` as a measurement concept only; no behavior transfer inferred |
| Liquidity | BitMEX contract-volume history exists; it is not historical book depth | Current snapshot includes venue-specific KRW quote turnover and top-five depth; not comparable to derivatives turnover or historical books | `UNKNOWN` for cross-era execution transfer |
| Cross-exchange arbitrage | BTCUSDT/ETHUSDT comparison exists but no USDT/USD adjustment | 200 synchronized Bithumb/Upbit KRW hourly closes measured; a price difference alone is not an executable arbitrage | `TRANSFERABLE_WITH_ADAPTATION` as a research measurement concept only |
| Regulation and market environment | Historical derivatives product and venue rules are not fully versioned in this research dataset | Current Korean spot rules and regulatory environment were not independently assessed for this audit | `UNKNOWN` |
| Regime-conditioned passive/aggressive response | Coarse conditional counts; no robust relation established | Requires new local public data, frozen rules, and independent validation | `UNKNOWN` |

Official Bithumb docs expose public market list, candles, trades, ticker, and orderbook endpoints; its per-market order-chance endpoint returns fees/constraints but requires JWT/private API and was deliberately not used. Upbit documents market-specific price ticks and KRW orderbook aggregation ([Bithumb public API reference](https://apidocs.bithumb.com/reference/api-%EB%A0%88%ED%8D%BC%EB%9F%B0%EC%8A%A4), [Bithumb order chance](https://apidocs.bithumb.com/reference/%EC%A3%BC%EB%AC%B8-%EA%B0%80%EB%8A%A5-%EC%A0%95%EB%B3%B4), [Upbit orderbook policy](https://docs.upbit.com/kr/reference/list-orderbook-instruments)). Current fee, Bithumb tick, lot/minimum constraints, borrowing, and account-specific rules remain `UNKNOWN`; do not reuse 4bps as a verified current schedule.

### 11.8 Hypothesis catalog and disposition

This catalog is for future hypothesis registration only. Context-conditioned counts are available only for XBTUSD/ETHUSD and rely on flat-start intent. Every falsification status is intentionally conservative.

#### `HYP-PASSIVE-ENTRY-REGIME-01`

- **DESCRIPTION:** Entry liquidity choice varies with pre-entry volatility, trend, or funding state.
- **SOURCE_OBSERVATION:** Conditional OPEN fills have 57.384% taker share; observed regime counts include high-vol 217 maker/402 taker, low-vol 144/169, negative funding 105/312, positive funding 260/271.
- **MARKET_CONTEXT_EVIDENCE:** 1h completed bars, prior-only volatility/trend thresholds, and as-of BitMEX funding sign; no spread, depth, or queue position.
- **CONTRADICTING_EVIDENCE:** Small conditional entry sample; labels assume flat start; symbol/year composition may explain rates; absolute entry intent is unverified.
- **ROBUSTNESS:** `WEAK`; no entry-specific LOO or regime-block CI is treated as valid confirmation.
- **FEE_DEPENDENCE:** High/unknown; no matched fill-probability and fee-adjusted outcome.
- **DOMAIN_TRANSFER_RISK:** `UNKNOWN` for Bithumb/Upbit spot; passive queue behavior is venue-specific.
- **FALSIFICATION_RULE:** Reject if a frozen passive-entry rule has nonpositive net fill-adjusted outcome versus time/symbol/regime-matched control after fees, queue fills, and slippage.
- **MINIMUM_FUTURE_DATA:** Point-in-time bid/ask/depth, all order acknowledgements and partial fills, signed initial position, and frozen cost model.
- **DISPOSITION:** `WEAK`; no surviving edge claim.

#### `HYP-AGGRESSIVE-EXIT-01`

- **DESCRIPTION:** Exits use taker liquidity more often than entries.
- **SOURCE_OBSERVATION:** Conditional exit taker share 57.228% vs entry 57.384% (difference −0.156pp).
- **MARKET_CONTEXT_EVIDENCE:** 1h vol/trend/funding strata; placebo shuffles entry/close labels within symbol × volatility × funding blocks.
- **CONTRADICTING_EVIDENCE:** Difference points the other way; placebo observed maker difference +0.310pp, approximate p=0.889, null interval [−4.355,+4.097]pp; labels and temporal dependence limit inference.
- **ROBUSTNESS:** `REJECTED` for the directional claim that exits are more taker aggressive.
- **FEE_DEPENDENCE:** High for any economic implication; taker costs and exit slippage are not offset by identified PnL.
- **DOMAIN_TRANSFER_RISK:** `UNKNOWN`; spot exit mechanics and inventory constraints differ.
- **FALSIFICATION_RULE:** Replicate with independently snapshotted transitions; reject if paired block interval includes zero or matched-stratum relation disappears.
- **MINIMUM_FUTURE_DATA:** Signed position snapshots, explicit order intent, full order/fill/queue history, synchronized market context.
- **DISPOSITION:** `REJECTED` as currently stated.

#### `HYP-INVENTORY-SCALE-ADVERSE-01`

- **DESCRIPTION:** Adds occur after adverse movement from the current position's entry basis.
- **SOURCE_OBSERVATION:** Sequential fill-to-fill proxy marks 76,483 XBTUSD and 5,551 ETHUSD additions adverse, vs 390,916 and 153,847 favorable-or-flat.
- **MARKET_CONTEXT_EVIDENCE:** Hourly context is joined, but the proxy uses prior fill price rather than market return over a defined horizon.
- **CONTRADICTING_EVIDENCE:** No initial inventory, position basis, or path-to-add measurement; contract sizes and symbols are heterogeneous.
- **ROBUSTNESS:** `UNIDENTIFIABLE`; raw proxy counts do not establish inventory scaling behavior.
- **FEE_DEPENDENCE:** Unknown; repeated execution cost and funding could dominate any sizing effect.
- **DOMAIN_TRANSFER_RISK:** `UNKNOWN`; derivatives inventory/leverage differs from spot holdings.
- **FALSIFICATION_RULE:** Reject if adverse-add rate is no greater than a symbol × volatility × trend matched placebo after using marked-to-market position basis.
- **MINIMUM_FUTURE_DATA:** Timestamped position snapshots, fills and funding attribution, point-in-time returns, frozen adverse-move horizon.
- **DISPOSITION:** `UNIDENTIFIABLE`.

#### `HYP-MAKER-RISE-2021-01`

- **DESCRIPTION:** Maker execution share rose over time within the two priority instruments.
- **SOURCE_OBSERVATION:** 2020→2021 XBTUSD maker rate 45.10%→71.19%; ETHUSD 70.99%→95.05%.
- **MARKET_CONTEXT_EVIDENCE:** Hourly BTC/ETH OHLCV and coarse volatility/trend/funding regimes exist; order-book and fill-quality evidence does not.
- **CONTRADICTING_EVIDENCE:** Omitting 2021 reverses the aggregate annual slope; ETH equal-order share (71.35%) differs materially from fill-weighted share (92.06%); no queue or execution-quality outcome.
- **ROBUSTNESS:** `WEAK`; symbol replication is descriptive and not a formal holdout; 2021 is influential.
- **FEE_DEPENDENCE:** High for any profitability claim; historical maker rebate contributes −298.46 BTC in signed fees, but fill share alone is not skill.
- **DOMAIN_TRANSFER_RISK:** `STRUCTURALLY_NON_TRANSFERABLE` for historical rebate economics; passive behavior is `UNKNOWN` across venues.
- **FALSIFICATION_RULE:** Reject execution-skill interpretation if multi-year order-level queue-adjusted fill quality fails to improve after removing rebate and charging target-venue costs.
- **MINIMUM_FUTURE_DATA:** Multi-year venue-matched book snapshots, order submissions/cancellations/partial fills, and independently measured fee/slippage schedule.
- **DISPOSITION:** `WEAK` descriptive behavior, not skill.

#### `HYP-FUNDING-HOLD-DURATION-01`

- **DESCRIPTION:** Funding state changes holding duration or position exposure.
- **SOURCE_OBSERVATION:** Signed funding `execcomm` implies +149.57544093 BTC credit over the recorded period.
- **MARKET_CONTEXT_EVIDENCE:** Historical funding observations are available as-of hourly bars, but there is no verified position snapshot to attribute each payment to a cycle.
- **CONTRADICTING_EVIDENCE:** No independently verified position/funding-to-cycle mapping; no high-confidence cycles. Spot has no perpetual funding transfer.
- **ROBUSTNESS:** `UNIDENTIFIABLE` for duration/exposure relation.
- **FEE_DEPENDENCE:** Funding is the hypothesis itself; removal must be modeled separately from trade fees.
- **DOMAIN_TRANSFER_RISK:** `STRUCTURALLY_NON_TRANSFERABLE` to spot funding economics.
- **FALSIFICATION_RULE:** Reject if risk-adjusted duration/exposure does not differ across predeclared funding states when positions are independently observed.
- **MINIMUM_FUTURE_DATA:** Signed position snapshots at funding boundaries, funding ledger mapping, and predeclared duration/outcome metrics.
- **DISPOSITION:** `UNIDENTIFIABLE`.

#### `HYP-CROSS-VENUE-DIVERGENCE-01`

- **DESCRIPTION:** Point-in-time cross-venue price divergence predicts a measurable venue/market response.
- **SOURCE_OBSERVATION:** Independent hourly BTC/ETH prices exist; XBTUSD-vs-BTCUSDT and ETHUSD-vs-ETHUSDT close-difference medians were 10.12 and 15.91 bps; current Bithumb-vs-Upbit KRW medians were 2.879 and 2.765 bps across 200 hours.
- **MARKET_CONTEXT_EVIDENCE:** Historical USD-vs-USDT hourly cross-check plus current synchronized KRW BTC/ETH hourly candles and a one-time pair of public orderbooks.
- **CONTRADICTING_EVIDENCE:** Hourly data are too coarse for execution timing; historical USDT/USD basis is unadjusted; a measured price difference does not establish an executable response or arbitrage.
- **ROBUSTNESS:** `WEAK` as a data-collection hypothesis only; no predictive relation survives because none was established.
- **FEE_DEPENDENCE:** High/unknown for an arbitrage interpretation due fees, latency, FX and slippage.
- **DOMAIN_TRANSFER_RISK:** `TRANSFERABLE_WITH_ADAPTATION` only as a measurement concept; trading behavior remains unknown.
- **FALSIFICATION_RULE:** Reject if a frozen divergence measure has no out-of-sample response after venue costs, FX/basis, and latency.
- **MINIMUM_FUTURE_DATA:** Synchronized KRW/USD/USDT quotes, venue order books, timestamp uncertainty, and frozen basis/fee model.
- **DISPOSITION:** `WEAK`; not a candidate or alpha claim.

No listed hypothesis is `SURVIVES_FALSIFICATION`. Counts by current disposition: 0 survives, 3 weak/descriptive, 1 rejected as a specific claim, 2 unidentifiable. These are not candidate strategies, alpha, or proof of transfer.

### 11.9 Validation, artifact policy, and post-30H handoff

- Targeted tests after fixes: 34 passed. Focused Pyright on changed/new source and scripts: 0 diagnostics.
- Canonical full test command is `pytest`. The system-Python 3.14 attempt could not collect because that interpreter lacks PyArrow. The final full rerun after the current public-snapshot downloader used `/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest -q`: **1,731 passed, 2 skipped, 0 failed in 152.69s**. An earlier pre-final run had one host process-group `PermissionError` in `test_bounded_supervisor`; the final rerun passed that test.
- Full-project Pyright (`src tests scripts`) on `origin/main` analyzed 329 files and reported **596 errors**. On PR #17 it analyzed 393 files and reported **614 errors**. An exact normalized diagnostic comparison matched all 596 baseline errors and found **18 PR-branch-added diagnostics**: 8 in `scripts/ssm_exec.py`, 9 in `research_infra/independent_verifier.py` (PyArrow symbol stubs), and 1 in `research_infra/stratified_audit.py` (Decimal/float subtraction). No baseline-only diagnostics were found. None of the 18 are in files changed/added by this audit (0 audit-touch-set diagnostics). Pyright outputs: `/tmp/external-bitmex-main-pyright.json`, `/tmp/external-bitmex-full-pyright-final2.json`. Scoped Pyright on changed/new source and scripts reported 0 diagnostics. `python3 -m compileall -f src scripts` passed.
- `git diff --check` and final raw-data tracking checks must pass before delivery. Raw market/execution data, joined rows, and audit JSON stay gitignored. No raw data are tracked by this change.
- No AWS, validation runtime, EC2 worktree, collector, scheduler, observer, S3 validation prefix, Launch #14, thresholds, or runtime commit `22e06b9` was touched. AWS validation mutation remains `NONE`.

After authoritative Fresh 30H-v3 PASS, the safe handoff order is: (1) seal reliability evidence and confirm exact authoritative revision; (2) integrate research infrastructure through the agreed small PR stack; (3) extend and refresh the checksummed public Bithumb/Upbit context without private endpoints beyond the initial 200-hour snapshot; (4) register frozen, falsifiable hypotheses with costs/metrics/acceptance thresholds; (5) run retrospective research, select only governed candidates, freeze all logic and assumptions, then begin a separate prospective holdout. Do not consume the future holdout during this handoff.

### 11.10 Corrected current state

```text
wallet final balance = 737.26973405 BTC (independent raw match)
contract totals = reported counts NOT VERIFIED; raw gross counts differ materially
aggregate USD-equivalent exposure ≈ $10.57B = NOT VERIFIED
maker/taker/net trading fee and funding totals = rounded values match raw execcomm
2020 maker ratio = 46.9184%; 2021 maker ratio = 85.8912%
raw 2020→2021 composition contribution = +17.9529pp (not +12.85pp)
robust structural break = none; 2020-10/11 OLS-CUSUM claim unsupported
position intent and high-confidence event study = NOT_IDENTIFIABLE
execution skill vs direction/PnL attribution = NOT_IDENTIFIABLE
market context join = 1,268,435 joined; 170,772 unmatched; future leakage = 0
raw data tracked = NO
30H runtime touched = NO
AWS validation mutation = NONE
ALPHA = UNPROVEN
PAPER = NOT_STARTED
LIVE = DISABLED
PRIVATE_API = DISABLED
```
