# Known Unknowns & Epistemic Boundaries

**Document Version:** 1.0.0  
**Effective Date:** 2026-09-05  
**Compliance Standard:** Scientific Integrity Charter & Fail-Closed Epistemology

---

## 1. Core Principle of Intellectual Honesty

In quantitative research, acknowledging the boundary between **verified empirical evidence** and **unverified conjecture** is the single most important safeguard against catastrophic capital loss.

The following inventory details all critical dimensions where current data, tooling, or historical backtests DO NOT provide verifiable proof.

---

## 2. Catalog of Known Unknowns

### 2.1 Live Strategy Alpha (V4, V6, V8, V9)
- **Status:** **`UNKNOWN / ALPHA UNPROVEN`**
- **Evidence:** Historical candle backtests demonstrate favorable risk-adjusted returns (Sharpe $> 1.4$) on past in-sample and holdout data.
- **Epistemic Boundary:** Backtest performance on daily/hourly candles does not prove live execution edge. No live orders have been placed in production. Historical backtest returns may be artifacts of data snooping, regime conditioning, or unaccounted microstructural friction.
- **Classification:** `FROZEN RESEARCH BASELINE — ALPHA UNPROVEN`.

### 2.2 Live Bithumb Order Routing Latency
- **Status:** **`ESTIMATED, NOT EMPIRICALLY MEASURED`**
- **Evidence:** Synthetic log-normal latency distributions (median ~36ms, p99 ~176ms) model AWS Seoul to Bithumb routing.
- **Epistemic Boundary:** No live private order dispatch packets ($t_0 \to t_3$) have been benchmarked from the production EC2 guest under load. True exchange matching engine queue times, TCP retransmission frequencies, and burst throttling thresholds remain unmeasured.

### 2.3 Microstructure Variable Profitability (OFI, ATI, MPQI)
- **Status:** **`UNTESTED ON PROSPECTIVE DATA`**
- **Evidence:** Pre-registered feature definitions and parameter boundaries are sealed in `research/preregistration/microstructure_v1.json`.
- **Epistemic Boundary:** Order Flow Imbalance, Trade Imbalance, and Microprice have not yet been evaluated against the prospective 72-hour dataset. It is unknown whether predictive signals survive 4 bps taker fees and book depth slippage.

### 2.4 Exchange Order Queue Dynamics (L2 vs. L3)
- **Status:** **`INVISIBLE`**
- **Evidence:** The collector records Level 2 order book snapshots (aggregated price and size across 30 levels).
- **Epistemic Boundary:** Level 3 tick-by-tick individual order queues and cancellations are not available from the public Bithumb WebSocket feed. Queue priority, queue position decay, and fill probability for passive limit orders cannot be determined without true L3 market data.

### 2.5 Cross-Exchange Lead-Lag Causality
- **Status:** **`UNVERIFIED CORRELATION`**
- **Evidence:** Binance BTCUSDT futures/spot feeds and Bithumb KRW-BTC feeds are collected concurrently with microsecond timestamps.
- **Epistemic Boundary:** Statistical correlation or Granger causality between Binance price discovery and Bithumb price adjustments does NOT guarantee an executable arbitrage edge. Latency, fee asymmetry, currency conversion risk, and liquidity fragmentation may completely erase apparent cross-exchange alpha.

### 2.6 Effective Multiplicity ($N_{\text{eff}}$) and True DSR
- **Status:** **`NOT IDENTIFIABLE FROM HISTORICAL LEDGER`**
- **Evidence:** The historical research ledger records 77 scalar trial summaries ($N=77$).
- **Epistemic Boundary:** Because the ledger records only summary metrics and lacks the synchronous return time series for all 77 historical candidates, the correlation matrix between trials cannot be computed. $N_{\text{eff}}$ cannot be identified without arbitrary assumptions. Consequently, DSR must be reported across the full sensitivity spectrum $N \in [1, 200]$.

### 2.7 Live 72-Hour Soak Execution State
- **Status:** **`SEALED EXTERNAL EXPERIMENT`**
- **Evidence:** The AWS 72H soak was prepared, verified, and launched autonomously. During this sprint, no live AWS, EC2, SSM, or S3 queries were executed.
- **Epistemic Boundary:** Final dataset completeness, packet loss rates, AWS uptime, and total archived volume remain unknown until the formal post-soak audit is conducted at $T+72\text{h}$.
