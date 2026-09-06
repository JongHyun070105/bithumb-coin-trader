# Research Statistics Methodology Audit (2026-09-05)

## 1. 개요 및 배경

본 문서는 `bithumb_coin_trader/research_statistics.py`에 구현된 다중 검정(Multiple-Testing) 통계 프레임워크인 **Deflated Sharpe Ratio (DSR)**, **Combinatorially Symmetric Cross-Validation (CSCV) 기반 Probability of Backtest Overfitting (PBO)**, **White's Reality Check (WRC)**의 수식, 구현, 해석 및 한계점을 엄밀하게 감사(Audit)한 결과입니다.

---

## 2. DSR (Deflated Sharpe Ratio) 수식 검증 및 $N$의 수학적 방향성

### 2.1 수식 체계 (Bailey & López de Prado, 2014)

DSR은 다중 시행(Multiple Trials) 하에서 선택 편향(Selection Bias)과 비정규성(왜도, 첨도)을 통제하여, 관측된 샤프 지수가 단순한 우연(운)에 의해 달성되었을 귀무가설 $H_0: SR \le E[\max(SR_k)]$을 기각할 통계적 확률을 계산합니다.

$$
DSR = \Phi \left( \frac{(\widehat{SR} - E[\max(\{SR_k\})]) \sqrt{T - 1}}{\sqrt{1 - \widehat{\gamma}_3 \widehat{SR} + \frac{\widehat{\gamma}_4 - 1}{4} \widehat{SR}^2}} \right)
$$

여기서 귀무가설의 선택 편향 허들인 기대 최대 샤프 비율 $E[\max(\{SR_k\})]$은 $N$개의 독립적이고 동일한 분포를 따르는 샤프 비율 하에서 다음과 같이 근사됩니다:

$$
E[\max(\{SR_k\})] \approx \sigma_{SR} \left( (1 - \gamma) \Phi^{-1}\left(1 - \frac{1}{N}\right) + \gamma \Phi^{-1}\left(1 - \frac{1}{N \cdot e}\right) \right)
$$
(단, $\gamma \approx 0.577215$는 오일러-마스케로니 상수, $\sigma_{SR}$은 시도된 전략들의 샤프 지수 표준편차)

### 2.2 $N$ (시행 횟수)의 수학적 영향 방향

> [!WARNING]
> **수학적 방향성 교정:**
> 일부 외부 비평이나 기존 논의에서 "유효 시행 횟수 $N_{eff}$가 작아질수록 DSR 페널티가 커진다"는 주장은 **명백히 수학적으로 반대**입니다.

- **독립 시행 횟수 $N$이 증가할수록:**
  - $1 - \frac{1}{N} \to 1$로 수렴하므로, $\Phi^{-1}(1 - 1/N)$는 단조 증가합니다.
  - 따라서 기대 최대 샤프 지수 $E[\max(\{SR_k\})]$ (선택 편향 허들)가 **단조 증가**합니다.
  - 분자인 $(\widehat{SR} - E[\max(SR)])$가 감소하므로, 최종 DSR 확률은 **단조 감소**합니다 (더 가혹한 허들 적용).
- **유효 시행 횟수 $N_{eff}$가 작아질수록 (전략 간 높은 상관성 존재 시):**
  - 탐색한 독립적 차원이 적으므로, 기대할 수 있는 우연의 최대 샤프 지수 허들이 **낮아집니다**.
  - 따라서 동일한 관측 샤프 지수에 대해 DSR 확률은 오히려 **상승(완화)**됩니다.

**중요한 결론:**
이 사실이 기존 V6의 DSR(약 61.47%)이 보수적이거나 통계적으로 완전함을 보증하는 것은 아닙니다. 이는 오직 $N$ 항의 수학적 방향성이 단조 감소 관계임을 증명할 뿐입니다.

### 2.3 실측 감도 분석 (DSR Sensitivity Spectrum)

`scripts/audit_dsr_sensitivity.py`를 통해 다양한 $N$ 값에 따른 DSR 확률 변화를 실측한 결과는 다음과 같습니다:

| Trial Count ($N$) | Observed Sharpe | Expected Max Sharpe $E[\max(SR)]$ | DSR Probability | Selection Penalty Hurdle |
| :--- | :--- | :--- | :--- | :--- |
| **1 (단일 시행)** | 0.1368 | 0.0000 | **98.4588%** | 0.0000 (페널티 없음) |
| **2** | 0.1368 | 0.1824 | 23.6093% | +0.1824 |
| **5** | 0.1368 | 0.4185 | 0.0004% | +0.4185 |
| **10** | 0.1368 | 0.5526 | 0.0000% | +0.5526 |
| **20** | 0.1368 | 0.6670 | 0.0000% | +0.6670 |
| **40** | 0.1368 | 0.7684 | 0.0000% | +0.7684 |
| **77 (원장 기록 수)** | 0.1368 | 0.8553 | 0.0000% | +0.8553 |
| **100** | 0.1368 | 0.8881 | 0.0000% | +0.8881 |

*(주: 위 표는 표준적인 250 관측치 일별 수익률 및 샤프 표준편차 0.6 하에서 계산된 감도 예시이며, `tests/test_research_statistics.py`의 `test_deflated_sharpe_monotonicity_across_n_spectrum`을 통해 전 구간 단조성이 검증되었습니다.)*

---

## 3. 유효 시행 횟수 ($N_{eff}$) 식별 가능성 감사

### 3.1 원장(Ledger) 데이터 경계
현재 저장소에 추적되거나 로컬에 보존된 연구 원장(`reports/research_trial_ledger.jsonl`)은 각 시행의 스칼라 지표(최종 수익률, 샤프 비율, MDD, 파라미터)만을 기록하고 있습니다.

### 3.2 결론: $N_{eff}$ 식별 불가
- 77개 시행 간의 진정한 상관관계 행렬을 계산하려면, 77개 전략의 **시계열 정렬 수익률 행렬 (Aligned Return Matrix, $T \times 77$)**이 필수적입니다.
- 스칼라 샤프 지수만으로는 전략 간 수익률 공분산이나 고유치 참여율(Eigenvalue Participation Ratio)을 추정할 수 없습니다.
- 임의의 평균 상관계수(예: $\rho=0.5$)를 가정하여 단일 $N_{eff}$ 값을 도출하는 것은 사후적 추측에 불과합니다.

따라서 본 감사는 다음과 같이 공식 판정합니다:
```
N_EFF: NOT IDENTIFIABLE FROM CURRENT LEDGER
```

---

## 4. CSCV 기반 PBO (Probability of Backtest Overfitting) 감사 및 해석

### 4.1 구현 방식
`cscv_probability_backtest_overfitting` 함수는 시계열을 $S=8$개 블록으로 균등 분할한 뒤, $\binom{8}{4} = 70$개의 조합(Train 4블록, Test 4블록)에 대해 In-Sample 최적 전략을 선정하고, 해당 전략의 Out-of-Sample 상대 순위(Rank Fraction)를 평가합니다.

### 4.2 올바른 학술적 해석
- **PBO의 엄밀한 정의:**
  "CSCV의 70개 교차 분할 구성 중, 인샘플(IS)에서 1위로 선택된 전략이 아웃오브샘플(OOS) 순위에서 하위 50% (중앙값 이하)로 전락하는 조합의 비율."
- **금지되는 과장 해석:**
  - "PBO가 20%이므로 이 전략이 가짜일 확률이 20%이다" (X - 베이지안 사후 과적합 확률이 아님).
  - "80% 확률로 실전에서 수익이 난다" (X).

---

## 5. White's Reality Check (WRC) 구현 및 해석 감사

### 5.1 구현 방식
- 후보 전략들의 벤치마크 초과 수익률 행렬을 평균 0으로 중심화(Centering)하여 귀무가설 $H_0: \max_k E[r_k - r_b] \le 0$을 구성.
- Politis & Romano (1994)의 정구간 부트스트랩(Stationary Bootstrap)을 적용하여 시계열 자기상관성을 보존.

### 5.2 올바른 표현 규격
- **금지:** "수익 결과가 우연에 기인할 확률이 0.9%이다."
- **권장:** "사전 선언된 후보군과 정상 부트스트랩(2,000회, 블록 길이 7) 하에서 White's Reality Check는 $p = 0.009$를 산출하였다."

---

## 6. 연구 베이스라인 분류 및 게이트 재정의

기존 V4/V6 전략군에 대한 공식 지위를 다음과 같이 재분류합니다:

```
[CURRENT STATUS]
V4/V6 CANDIDATE: FROZEN RESEARCH BASELINE
ALPHA STATUS: UNPROVEN
```

### 6.1 게이트 분리 원칙
1. **탐색/스크리닝 게이트 (Research Continuation Gate):**
   - 과거 완화된 기준(DSR $\ge 50\%$)은 연구 가치가 있는 후보를 다음 단계로 넘기기 위한 내부 스크리닝 기준이었을 뿐이며, 과학적 입증 기준이 아님.
2. **배포/실전 후보 게이트 (Deployment Candidate Gate):**
   - 실전 배포 또는 확정 알파로 인정받기 위해서는 단일 지표(예: DSR $\ge 95\%$)만으로 불충분하며, 다음 요건을 모두 충족해야 함:
     - 사전 등록(Preregistration)
     - 손대지 않은 전향적(Prospective) 검증 데이터셋에서의 성과
     - 보수적 호가창 체결 시뮬레이션(슬리피지, 수수료, 깊이 전수 반영) 생존
     - 다중 검정 통제
     - 포워드 안정성(Forward Stability)
