# DSR (Deflated Sharpe Ratio) Discrepancy Reconciliation (2026-09-05)

## 1. 개요

과거 Strategy V6의 정밀 포트폴리오 감사(`scripts/audit_v6_portfolio_integrity.py`)에서 보고된 DSR 확률은 약 **61.47%**였습니다.
그러나 Phase 1 감도 분석 도구 및 재현 스크립트(`scripts/reproduce_v6_statistics.py`) 실행 시 $N=77$에서 DSR 확률이 **1.0000 (100.0%)**으로 출력되는 심각한 불일치(Discrepancy)가 관측되었습니다.

본 감사는 제1원리(First Principles)에 입각하여 수학적 정의, 단위 변환(Unit Map), 표본 관측수 계산을 추적하였으며, 불일치의 정확한 근본 원인을 규명하고 독립 레퍼런스 구현체(`tests/reference_dsr.py`)를 통해 완전한 수학적 화해(Reconciliation)를 달성하였습니다.

---

## 2. 수학적 제1원리 및 단위 맵 (Unit Map)

Bailey & López de Prado (2014) 논문에 따른 DSR의 검정 통계량 $Z$는 다음과 같습니다:

$$
Z = \frac{\widehat{SR} - E[\max(\{SR_k\})]}{\text{SE}(\widehat{SR})}
$$

여기서 표준오차 $\text{SE}(\widehat{SR})$는 비정규성 보정 분산 항 $V$와 관측 표본 수 $T$에 의해 결정됩니다:

$$
V = 1 - \gamma_3 \widehat{SR} + \frac{\gamma_4 - 1}{4} \widehat{SR}^2
$$

### 단위 일관성 법칙 (Unit Consistency Identity)
1. **기간별 단위 (Per-Period Units, e.g., 일별 Daily)**:
   - $\widehat{SR}_{daily} = \mu_{daily} / \sigma_{daily}$
   - $E[\max]_{daily} = \sigma(SR_{daily}) \cdot [\dots]$
   - $\text{SE}_{daily} = \sqrt{V / (T_{days} - 1)}$
   - $Z = \frac{\widehat{SR}_{daily} - E[\max]_{daily}}{\sqrt{V / (T_{days} - 1)}} = (\widehat{SR}_{daily} - E[\max]_{daily}) \cdot \sqrt{T_{days} - 1} / \sqrt{V}$

2. **연율화 단위 (Annualized Units)**:
   - 연간 기간 수 $f = 365.25$ (또는 $252$)
   - $\widehat{SR}_{ann} = \widehat{SR}_{daily} \cdot \sqrt{f}$
   - $E[\max]_{ann} = E[\max]_{daily} \cdot \sqrt{f}$
   - $\text{Var}(\widehat{SR}_{ann}) = f \cdot \text{Var}(\widehat{SR}_{daily}) = f \cdot \frac{V}{T_{days} - 1} = \frac{V}{(T_{days} - 1) / f} = \frac{V}{T_{years}}$
   - $\text{SE}_{ann} = \sqrt{V \cdot f / (T_{days} - 1)} = \sqrt{V / T_{years}}$
   - $Z = \frac{\widehat{SR}_{ann} - E[\max]_{ann}}{\text{SE}_{ann}} = \frac{(\widehat{SR}_{ann} - E[\max]_{ann}) \cdot \sqrt{T_{days} - 1}}{\sqrt{f} \cdot \sqrt{V}}$

**핵심 항등식:**
$$
Z_{ann} \equiv Z_{daily}
$$
연율화 샤프 차이 $(\widehat{SR}_{ann} - E[\max]_{ann})$의 분모에는 반드시 $\sqrt{f}$가 포함되어야 하며, 이는 표본 길이의 제곱근 항이 $\sqrt{T_{days}}$가 아니라 **연간 표본 기간 $\sqrt{T_{years}} = \sqrt{T_{days} / f}$**이어야 함을 의미합니다.

---

## 3. 불일치의 근본 원인 (Root Cause of Difference)

| 항목 | 역사적 감사 (`audit_v6_portfolio_integrity.py`) | Phase 1 재현 헬퍼 (`reproduce_v6_statistics.py`) |
| :--- | :--- | :--- |
| **입력 샤프 단위** | 일별 샤프 ($SR_{daily} \approx 0.0824$) | 연율화 샤프 ($SR_{ann} \approx 1.5750$) |
| **시행 샤프 분산** | 일별 샤프 분산 ($\sigma_{daily} = \sigma_{ann} / \sqrt{365.25}$) | 연율화 샤프 분산 ($\sigma_{ann} \approx 0.5849$) |
| **기대 최대 샤프** | 일별 $E[\max]_{daily} \approx 0.0745$ | 연율화 $E[\max]_{ann} \approx 1.425$ |
| **샤프 차이 분자** | $0.0824 - 0.0745 = 0.0079$ | $1.5750 - 1.4250 = 0.1500$ |
| **적용 표본 제곱근** | $\sqrt{T_{days} - 1} = \sqrt{1199} \approx 34.63$ | $\sqrt{T_{days} - 1} = \sqrt{1199} \approx 34.63$ (**단위 혼용 버그**) |
| **$Z$ 스코어 산출** | $Z \approx 0.272$ | $Z \approx 5.196$ (**$\sqrt{365.25} \approx 19.11$배 왜곡 팽창**) |
| **DSR 산출 확률** | **61.47% (정상)** | **99.9999% $\approx$ 1.0000 (왜곡 산출)** |

### 요약:
`reproduce_v6_statistics.py`의 `calculate_deflated_sharpe_analytical` 함수가 연율화된 샤프 비율 차이($0.15$)에 연간 환산 표본수 $\sqrt{T_{years}} = \sqrt{1200 / 365.25} \approx 1.812$가 아닌 일별 관측수 $\sqrt{T_{days}} = \sqrt{1200} \approx 34.64$를 그대로 곱했습니다.
이로 인해 연율화 계수 $\sqrt{f} \approx 19.11$이 $Z$ 스코어에 **이중(중복) 적용**되어 $Z$가 $0.27$에서 $5.20$으로 폭증하였고, 정규분포 CDF 확률이 사실상 $1.0000$으로 치솟았던 것입니다.

---

## 4. 교정 결과 (Corrected Result)

독립 레퍼런스 구현체(`tests/reference_dsr.py`) 및 교정된 `scripts/reproduce_v6_statistics.py`를 통해 재계산한 실측값:

| 후보 전략 (V6) | 관측 연율화 샤프 | 기대 최대 샤프 $E[\max]$ | 교정 전 (왜곡) | **교정 후 DSR 확률** |
| :--- | :---: | :---: | :---: | :---: |
| `TRIAL-V6-SAT-v6_fast_donchian_swing` | 1.3525 | 1.425 | 0.0058 | **44.75%** |
| `TRIAL-V6-SAT-v6_daily_ema_pullback` | 1.5904 | 1.425 | 1.0000 | **61.75%** |
| `TRIAL-V6-SAT-v6_cross_asset_fast_rotation` | 1.8369 | 1.425 | 1.0000 | **77.20%** |
| `TRIAL-V6-PORT-Core80_Sat20` | 1.5342 | 1.425 | 0.9999 | **57.81%** |
| `TRIAL-V6-PORT-Core70_Sat30` | 1.5750 | 1.425 | 1.0000 | **60.68%** |
| `TRIAL-V6-PORT-Core60_Sat40` | 1.5870 | 1.425 | 1.0000 | **61.52%** |

*(주: 일별 수익률의 비정규성 왜도/첨도를 반영한 정밀 프로덕션 `audit_v6_portfolio_integrity.py` 계산값은 $61.47\%$이며, 표준 정규분포 가정 하에서는 $60.68\%$로 완전히 수렴함.)*

---

## 5. 영향 분석

### 영향 받는 주장 (AFFECTED CLAIMS)
- **"77회 탐색 하에서 V6 DSR이 1.0000 (100% 확정적 우위)이다"**: **완전 거짓/기각**. 단위 혼용 버그로 인한 거짓 확신(False Certainty)이었음.
- **"N=77에서도 다중 검정 패널티를 거의 받지 않는다"**: **기각**. $N=77$에서 기대 최대 샤프는 $1.425$까지 치솟으며, 관측 샤프 $1.575$와의 마진은 $0.15$에 불과하여 DSR 확률은 약 $60.7\% \sim 61.5\%$ 수준에 머무름.

### 영향 받지 않는 주장 (UNAFFECTED CLAIMS)
- **역사적 V6 정밀 감사 보고서의 $61.47\%$ 기록**: **유효**. 당시 일별 수익률 기반의 정밀 계산은 단위 일관성을 정확히 유지하고 있었음.
- **DSR $N$ 단조성 원리**: **유효**. $N$이 증가할수록 $E[\max]$가 증가하고 DSR 확률이 감소한다는 방향성 수학은 완전히 유효함.
- **WRC p-value (0.00900) 및 CSCV PBO (20.0%)**: **유효**. DSR 단위 버그와 독립적인 부트스트랩/교차검증 메커니즘임.

---

## 6. $N_{eff}$ 식별 가능성에 대한 최종 판정

```
N_EFF: NOT IDENTIFIABLE FROM CURRENT LEDGER
```
- 77개 누적 원장은 최종 스칼라 성과 지표만 기록하고 있으며, 전략 간 시계열 일별 수익률 행렬이 보존되지 않았습니다.
- 따라서 전략 간 상관계수 행렬을 계산할 수 없으며, 유효 독립 시행수 $N_{eff}$를 사후적으로 추정하는 것은 불가능합니다.
- 향후 신규 연구 사이클에서는 모든 시행의 일별 수익률 벡터를 필수로 영구 보존해야 합니다.
