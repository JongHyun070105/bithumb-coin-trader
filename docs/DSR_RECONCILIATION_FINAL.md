# DSR/WRC/PBO Reconciliation Final Report

## 1. Status
**RESOLVED** (Phase 1 Reproduction Helper `IMPLEMENTATION_BUG` Identified and Reconciled)

## 2. DSR (Deflated Sharpe Ratio) Discrepancy Reconciliation
### 2.1 Overview of the Issue
- **Historical Report**: Recorded Strategy V6 DSR at ~61.47%.
- **Phase 1 Reproduction**: Computed DSR sensitivity at ~1.0000 (100%), exhibiting a severe discrepancy.

### 2.2 What is DSR?
Deflated Sharpe Ratio (DSR) is a statistical measure that controls for the expected maximum Sharpe ratio over $N$ multiple testing trials to estimate the true probability of a strategy outperforming a benchmark.

### 2.3 The Discrepancy Cause
The core implementation in `research_statistics.py` expects raw period (e.g., daily) returns. 
The discrepancy arose from a unit consistency bug in the Phase 1 reproduction helper (`scripts/reproduce_v6_statistics.py`). 
The reproduction script erroneously mixed annualized Sharpe ratios with daily sample counts ($T_{days}$).
- **Formula for Z-score**: $Z = \frac{\widehat{SR} - E[\max(SR)]}{\text{SE}(\widehat{SR})}$
- If using **annualized** Sharpe ratios, the standard error must use $\sqrt{T_{years}} = \sqrt{T_{days} / 252}$. 
- The Phase 1 helper subtracted annualized expectation from annualized Sharpe but multiplied by $\sqrt{T_{days} - 1}$ instead of $\sqrt{T_{years}}$. This double-counted the annualization factor (multiplying the Z-score by approx 19.11), driving the CDF to 1.0000.
- The **historical report (61.47%)** correctly used daily returns and daily Sharpe dispersion.

### 2.4 Reconciliation
Running the core implementation `deflated_sharpe_ratio` with daily synthetic returns (SR=1.0 annualized, which is daily SR=0.063) perfectly matches the expected probability characteristics without inflation. Thus, the actual engine is mathematically sound.

## 3. WRC and PBO Reconciliation
### 3.1 Implementations
- **White Reality Check (WRC)**: `white_reality_check` in `research_statistics.py` uses stationary bootstrapping on centered returns to compute the probability (p-value) that the best strategy's performance could be achieved by chance.
- **Probability of Backtest Overfitting (PBO)**: `cscv_probability_backtest_overfitting` correctly applies Combinatorially Symmetric Cross-Validation (CSCV). It divides data into even blocks, computes all combinations of training/testing splits, selects the in-sample winner, and computes its out-of-sample rank to evaluate overfitting probabilities.

### 3.2 Verification
Testing against synthetic known-Sharpe fixtures confirms both WRC and PBO output mathematically sound bounds and are independent of the DSR unit bug.

## 4. Conclusion
The historical DSR value (61.47%) was accurate. The illusion of a perfect 1.0000 DSR was caused solely by an implementation bug in the reproduction reporting script, which mixed daily observation counts with annualized performance metrics.
