# Microstructure Research Preregistration V1

**Document Version:** 1.0.0  
**Registration ID:** `prereg-microstructure-20260905-v1`  
**Created At (UTC):** `2026-09-05T03:35:00Z`  
**Authoritative Software Commit:** `9532cebc902856d954bf80b51dbe567b543dc8e2`  
**Registration Status:** `FROZEN_BEFORE_DATA_INSPECTION`  
**Companion Machine-Readable Specification:** [microstructure_v1.json](file:///Users/macintosh/Documents/ChatGPT/bitcoin-trader/research/preregistration/microstructure_v1.json)

---

## 1. Executive Summary & Objective

This document formalizes the scientific preregistration of the first microstructure research cycle (`CYCLE-01`) for Bithumb public market data collected during the AWS 72-Hour Soak (`aws-72h-soak-20260905-8017b83e`).

Under strict scientific integrity protocols:
- **No data inspection, screening, or backtesting has taken place prior to this registration.**
- All hypothesis formulation, feature family definitions, parameter search boundaries, trial budgets, stop rules, and economic execution hurdles are sealed in advance.
- The 72-hour dataset is divided into strictly insulated temporal segments: Discovery, Validation, Embargo, and Sealed Prospective Holdout.

---

## 2. Primary Research Question

> **Can predeclared public-market microstructure variables provide economically executable predictive information for short-horizon Bithumb returns after accounting for exchange fees, spread crossing, multi-level order book depth consumption, execution slippage, and latency haircuts?**

---

## 3. Universe & Multiplicity Scope

1. **Exchange:** Bithumb Korea (`KRW` pairs).
2. **Primary Discovery Instrument:** `KRW-BTC`.
   - All primary parameter discovery and feature evaluation are strictly confined to `KRW-BTC`.
3. **Replication Universe:** The remaining 19 high-liquidity Bithumb markets are **strictly quarantined** as out-of-sample cross-sectional replication targets. No feature mining or parameter tuning is permitted on non-BTC pairs during Cycle 1.
4. **External Reference Stream:** `BINANCE:BTCUSDT` (used strictly as an exogenous reference signal for lead-lag analysis, subject to strict timestamp synchronization).

---

## 4. Predeclared Feature Families & Parameter Budget

To prevent p-hacking and uncontrolled multiplicity, only **3 feature families** with a maximum of **3 predefined parameterizations each** are permitted in Cycle 1. The total trial budget for Cycle 1 is strictly capped at $N_{\text{max}} = 9$.

### Family 1: Order Flow Imbalance (OFI)
- **Mathematical Definition:**
  $$\text{OFI}_t = \Delta \text{BidSize}_t \cdot \mathbb{I}(\text{Bid}_t \ge \text{Bid}_{t-1}) - \Delta \text{AskSize}_t \cdot \mathbb{I}(\text{Ask}_t \le \text{Ask}_{t-1})$$
- **Order Book Depth:** Top 5 Levels.
- **Pre-Registered Parameterizations:**
  - `OFI_W10S`: Window = 10s, Sampling = 500ms, Horizon = 10s.
  - `OFI_W30S`: Window = 30s, Sampling = 1000ms, Horizon = 30s.
  - `OFI_W60S`: Window = 60s, Sampling = 1000ms, Horizon = 60s.

### Family 2: Aggressive Trade Imbalance (ATI)
- **Mathematical Definition:**
  $$\text{ATI}_t = \frac{V_{\text{buy}, t} - V_{\text{sell}, t}}{V_{\text{buy}, t} + V_{\text{sell}, t} + \epsilon}$$
- **Order Book Depth:** Level 1 (Aggressor Trades).
- **Pre-Registered Parameterizations:**
  - `ATI_W15S`: Window = 15s, Sampling = 500ms, Horizon = 15s.
  - `ATI_W45S`: Window = 45s, Sampling = 1000ms, Horizon = 45s.
  - `ATI_W90S`: Window = 90s, Sampling = 1000ms, Horizon = 90s.

### Family 3: Microprice & Queue Imbalance (MPQI)
- **Mathematical Definition:**
  $$P_{\text{micro}, t} = \frac{Q_{\text{bid}, t} \cdot P_{\text{ask}, t} + Q_{\text{ask}, t} \cdot P_{\text{bid}, t}}{Q_{\text{bid}, t} + Q_{\text{ask}, t}}$$
  $$\text{MPQI}_t = P_{\text{micro}, t} - P_{\text{mid}, t}$$
- **Order Book Depth:** Levels 1 to 5.
- **Pre-Registered Parameterizations:**
  - `MPQI_L1`: Top 1 Level, Sampling = 500ms, Horizon = 10s.
  - `MPQI_L3`: Top 3 Levels, Sampling = 500ms, Horizon = 20s.
  - `MPQI_L5`: Top 5 Levels, Sampling = 1000ms, Horizon = 30s.

---

## 5. Temporal Partitioning & Holdout Insulation

The continuous 72-hour soak window is partitioned temporally to prevent lookahead and contamination:

| Segment | Duration | Offset Range | Access Permission |
| :--- | :--- | :--- | :--- |
| **Discovery Window** | 24 Hours | Hour 00:00 to 24:00 | Unrestricted exploratory analysis within pre-declared parameter budget ($N \le 9$). |
| **Validation Window** | 24 Hours | Hour 24:00 to 48:00 | Single-pass validation of candidate hypotheses. No iterative parameter tuning. |
| **Purged Embargo** | 2 Hours | Hour 48:00 to 50:00 | Complete quarantine to purge serial autocorrelation and filter memory. |
| **Sealed Prospective Holdout** | 22 Hours | Hour 50:00 to 72:00 | **CRYPTOGRAPHICALLY SEALED.** Never read during research exploration. |

### Holdout Unlock Protocol
Holdout data can ONLY be unlocked if:
1. Candidate hypothesis/strategy code is committed to Git with a permanent SHA.
2. The trial has passed all validation hurdles with positive net PnL and deflated Sharpe ratio criteria.
3. A formal unlock checklist is signed in `docs/` before unsealing the holdout partition.

---

## 6. Realistic Economic Execution Hurdles

Any statistical signal is considered **REJECTED (DEAD)** if it fails the following execution constraints:

1. **Exchange Fee:** Minimum 4.0 bps (0.04%) per side (0.08% round-trip) taker fee.
2. **Spread Crossing:** All entries and exits must execute as taker crossing the prevailing top-of-book spread. Mid-price or passive fill assumptions are strictly prohibited for taker evaluation.
3. **Multi-Level Order Book Walk:** Orders exceeding top-of-book depth must walk the order book down/up to full volume fill, computing volume-weighted average execution price (VWAP).
4. **Latency Haircut Scenarios:**
   - Base Case: 50 ms round-trip execution delay.
   - Stress Case 1: 100 ms execution delay.
   - Stress Case 2: 250 ms execution delay.
   - Severe Congestion Case: 500 ms execution delay.
5. **Net Performance Hurdle:**
   - Net Sharpe Ratio $> 1.5$ after all fees, spread crossing, depth slippage, and latency haircuts.
   - Maximum Drawdown $< 10\%$.
   - Net cumulative PnL strictly positive across all latency scenarios up to 250 ms.

---

## 7. Trial Budgeting & Stop Rules

- **Trial Counting Policy:** Every execution of a backtest, correlation analysis, or predictive regression using any subset of the 72H soak data counts as 1 trial in the immutable research ledger (`evidence/research/trial_ledger_frozen_20260905.jsonl`).
- **Stop Rule:**
  - If all 9 preregistered parameterizations fail the Discovery or Validation hurdles, **Cycle 1 is terminated immediately as a FAILED EXPERIMENT.**
  - Post-hoc "tweaking" of windows, depths, or filtering thresholds within Cycle 1 is strictly forbidden.
  - A maximum of 2 cycles are permitted. If Cycle 2 fails, a mandatory **NO-GO** is declared, freezing research until new independent prospective data is gathered.
