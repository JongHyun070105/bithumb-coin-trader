# DSR/WRC/PBO Reconciliation Final Report

## 1. Status
**RESOLVED** (Phase 1 Reproduction Helper `IMPLEMENTATION_BUG` Identified and Reconciled)

## 2. DSR (Deflated Sharpe Ratio) Discrepancy Reconciliation
### 2.1 Overview of the Issue
- **Historical Report**: Recorded Strategy V6 DSR at ~61.47% (0.6147).
- **Phase 1 Reproduction**: Computed DSR sensitivity at ~1.0000 (100%), exhibiting a severe discrepancy.

### 2.2 What is DSR?
Deflated Sharpe Ratio (DSR) is a statistical measure that controls for the expected maximum Sharpe ratio over $N$ multiple testing trials to estimate the true probability of a strategy outperforming a benchmark.

### 2.3 The Discrepancy Cause & Periodicity Reconcilation (P11 / P11.1)
The core implementation in `research_statistics.py` expects raw period (e.g., daily) returns. 
The discrepancy arose from a unit consistency bug in the Phase 1 reproduction helper (`scripts/reproduce_v6_statistics.py`). 
The reproduction script erroneously mixed annualized Sharpe ratios with daily sample counts ($T_{days}$).

- **Formula for Z-score**: $Z = \frac{\widehat{SR} - E[\max(SR)]}{\text{SE}(\widehat{SR})}$
- **Periodicity Clarification (252 vs 365.25)**:
  - Traditional equities trade ~252 business days per year ($\sqrt{252} \approx 15.8745$).
  - Crypto trades continuously 24/7/365.25 calendar days ($\sqrt{365.25} \approx 19.1115$).
  - A previous draft mentioned $T_{days} / 252$ in text while citing the ~19.11 multiplier. That was an editorial contradiction: $\sqrt{252} \neq 19.11$; rather $\sqrt{365.25} = 19.1115$.
  - In Phase 4, we strictly standardize on **one source of periodicity**: `periods_per_year = 365.25` for crypto calendar daily series.
- **Root Cause**:
  - The Phase 1 helper subtracted annualized expectation from annualized Sharpe but multiplied by $\sqrt{T_{days} - 1}$ instead of $\sqrt{T_{years}} = \frac{\sqrt{T_{days} - 1}}{\sqrt{365.25}}$.
  - This effectively multiplied the Z-score by $\sqrt{365.25} \approx 19.11$, erroneously driving the CDF from ~0.61 to 1.0000.

### 2.4 Reconciliation & Raw Input Evidence Classification (P11.2 / P11.3)
- **Analytical Summary Reconciliation**:
  - Using the frozen ledger summary Sharpe distribution ($N=77, \sigma_{SR}=0.5849$) and $T=1200$, `TRIAL-V6-PORT-Core60_Sat40` yields DSR = 0.6152, matching the historical 61.47% reference within $\Delta = 0.0005$ tolerance.
- **Input Evidence Status**:
  - The frozen ledger `evidence/research/trial_ledger_frozen_20260905.jsonl` provides summary metrics (`observed_sharpe`, `total_return`, `maximum_drawdown`), not raw daily return series.
  - Therefore, while analytical summary reconciliation is **RESOLVED**, raw per-bar return reproducibility remains **INCONCLUSIVE_INPUT_EVIDENCE** because raw per-bar return vectors are not stored in the frozen ledger.
- **Reference vs Production Agreement**:
  - Validated via automated unit test: when fed identical raw return vectors, the production `deflated_sharpe_ratio` and independent reference implementation agree within machine epsilon ($< 10^{-6}$).

## 3. WRC and PBO Reconciliation
### 3.1 Implementations
- **White Reality Check (WRC)**: `white_reality_check` in `research_statistics.py` uses stationary bootstrapping on centered returns to compute the probability (p-value) that the best strategy's performance could be achieved by chance.
- **Probability of Backtest Overfitting (PBO)**: `cscv_probability_backtest_overfitting` correctly applies Combinatorially Symmetric Cross-Validation (CSCV). It divides data into even blocks, computes all combinations of training/testing splits, selects the in-sample winner, and computes its out-of-sample rank to evaluate overfitting probabilities.

### 3.2 Verification
Testing against synthetic known-Sharpe fixtures confirms both WRC and PBO output mathematically sound bounds and are independent of the DSR unit bug.

## 4. Conclusion
The historical DSR value (~61.47%) is analytically replicated (0.6152, error < 0.0005) under crypto 365.25 calendar annualization. The previous contradiction between 252 and 19.11 is resolved: 19.11 is $\sqrt{365.25}$. Raw per-bar return data remains classified as `INCONCLUSIVE_INPUT_EVIDENCE` due to ledger schema limitations.
