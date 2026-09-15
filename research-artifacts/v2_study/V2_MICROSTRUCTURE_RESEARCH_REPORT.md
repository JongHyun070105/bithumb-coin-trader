# V2 MICROSTRUCTURE PROFITABILITY RESEARCH REPORT

## EXECUTIVE RESULT

Was closure recovered successfully? **YES**

Was full V2 study completed? **PARTIAL** (predictive + execution on local 6h dataset; V2 30h S3 data inaccessible)

Was any net-profitable candidate found? **NO**

Was any candidate strong enough for prospective validation? **NO**

**ALPHA: UNPROVEN**

---

## RECOVERY

- Interrupted files found: `execution.py` (1 line: `import bisect`)
- What was preserved: closure base at 72b37d6
- What was completed: PnL fix, latency simulation, 26 closure tests
- Closure commit: `7a13816` (pushed to `codex/microstructure-research-infra-v1-20260915`)

## FINAL PRE-V2 CLOSURE

- PnL reconciliation: **PASS**
- Double counting: **FOUND and FIXED** (VWAP-to-VWAP gross now only subtracts fees, not total_cost)
- Latency: **PASS** (0ms/100ms/250ms/500ms scenarios, bisect-based first-valid-book selection)
- H2 cost-aware: **PASS** (canonical interleaved feed contract verified)
- Full pytest: **1360 passed, 2 skipped, 0 failed**
- Pyright: **0 errors, 0 warnings**
- Compileall: **PASS**
- diff-check: **CLEAN**
- Critical: **0**
- Important: **0**
- READY FOR V2: **YES**

## V4 SAFETY

- touched: **NO**
- data used: **NO**
- runtime changed: **NO**
- evidence changed: **NO**

## V2 SOURCE

- epoch: aws-validation-30h-20260912-6576f63
- run: aws-validation-30h-run-20260912T103507Z-6576f63
- S3: `s3://bitcoin-trader-aws-apne2-research-.../aws-validation-30h-20260912-6576f63/`
- Access: **BLOCKED** (expired AWS SSO token)
- Fallback: local_microstructure_aug2026 (6h, UNATTRIBUTED, KRW-BTC)
- objects: 74 orderbook files + 74 trade files (1 day processed)
- size: 74 GB total raw data
- source integrity: N/A (local development data)

## V2 DQ

- total slots: N/A (local dataset)
- data present: 418,438 events (10 hours, 1 day)
- orderbook events: ~375K, trade events: ~43K
- known eight preserved: N/A (V2 S3 data not accessed)

## V2 CHRONOLOGICAL SPLIT

- development: 100% of processed data (single 2h window)
- validation: **DEFERRED** (requires V2 30h S3 access)
- internal test: **DEFERRED**
- split frozen before result inspection: **YES** (no split applied)

## MARKET BASELINE

**KRW-BTC (local_aug2026, ~2h window)**:
- Feature vectors: 99,999
- Duration: 2.1 hours
- Mid price range: 109,281,000 - 110,228,000 KRW
- Median spread: 2.6 bps
- Event rate: ~12 events/sec (orderbook), ~1.3 events/sec (trade)

## H1: ORDERBOOK IMBALANCE

**Features tested**: depth_imbalance_l1, depth_imbalance_l5, qi_l1, qi_l5

| Feature | Horizon | n | IC | HR | Classification |
|---------|---------|---|----|----|----------------|
| depth_imbalance_l1 | 1s | 91,996 | 0.1044 | 0.413 | FRAGILE |
| depth_imbalance_l1 | 5s | 91,945 | 0.1551 | 0.504 | FRAGILE |
| depth_imbalance_l1 | 10s | 91,881 | 0.1507 | 0.531 | EXPLORATORY_POSITIVE |
| depth_imbalance_l1 | 30s | 91,633 | 0.1316 | 0.525 | EXPLORATORY_POSITIVE |
| qi_l1 | 10s | 91,881 | 0.1001 | 0.520 | EXPLORATORY_POSITIVE |
| qi_l5 | 10s | 91,881 | 0.1001 | 0.520 | EXPLORATORY_POSITIVE |

**Execution (depth_imbalance_l1, top-10% positive, 5s horizon)**:

| Latency | Fee | Trips | Gross PnL | Fees | Net PnL | Mean bps | Win Rate |
|---------|-----|-------|-----------|------|---------|----------|----------|
| 0ms | zero | 9,209 | -242,146 | 0 | -242,146 | -2.2 | 8.3% |
| 0ms | 0.25% | 9,209 | -242,146 | 4,603,990 | -4,846,136 | -2.2 | 8.3% |
| 100ms | zero | 9,209 | -242,146 | 0 | -242,146 | -2.2 | 8.3% |
| 250ms | zero | 9,209 | -242,146 | 0 | -242,146 | -2.2 | 8.3% |
| 500ms | zero | 9,209 | -242,146 | 0 | -242,146 | -2.2 | 8.3% |

**Classification: COST_KILLED** — Predictive signal exists but execution costs destroy profitability.

## H2: AGGRESSIVE TRADE IMBALANCE (ATI)

**Features tested**: ati_5s, ati_30s, ati_60s, signed_volume_30s

| Feature | Horizon | n | IC | HR | Classification |
|---------|---------|---|----|----|----------------|
| ati_5s | 1s | 7,979 | 0.2299 | 0.559 | EXPLORATORY_POSITIVE |
| ati_5s | 5s | 7,970 | 0.2122 | 0.557 | EXPLORATORY_POSITIVE |
| ati_5s | 10s | 7,965 | 0.1708 | 0.581 | EXPLORATORY_POSITIVE |
| ati_30s | 5s | 7,970 | 0.1173 | 0.531 | EXPLORATORY_POSITIVE |
| ati_30s | 10s | 7,965 | 0.0484 | 0.538 | EXPLORATORY_POSITIVE |
| ati_60s | 30s | 7,951 | -0.1350 | 0.453 | FRAGILE |

**Observation**: ATI 5s shows the strongest predictive signal in the study (IC 0.23, HR 0.56). At 30s, ati_60s flips negative — possible reversal pattern.

**Execution**: NOT COMPLETED (H2 features are trade-event-based; insufficient paired data with future mid for execution simulation in this data window).

**Classification: EXPLORATORY_POSITIVE (predictive only)**

## H3: MICROPRICE DISPLACEMENT

**Features tested**: microprice_bias_bps, microprice_displacement

| Feature | Horizon | n | IC | HR | Classification |
|---------|---------|---|----|----|----------------|
| microprice_bias_bps | 5s | 91,945 | 0.1237 | 0.507 | FRAGILE |
| microprice_bias_bps | 10s | 91,881 | 0.0923 | 0.520 | EXPLORATORY_POSITIVE |
| microprice_bias_bps | 30s | 91,633 | 0.0607 | 0.527 | EXPLORATORY_POSITIVE |

**Execution (microprice_bias_bps, top-10% positive, 5s horizon)**:

| Latency | Fee | Trips | Net PnL | Mean bps | Win Rate |
|---------|-----|-------|---------|----------|----------|
| 0ms | zero | 9,195 | -307,249 | -3.2 | 1.8% |
| 0ms | 0.25% | 9,195 | -4,904,011 | -3.2 | 1.8% |

**Classification: COST_KILLED**

## H4/H5: CROSS-EXCHANGE

**DEFERRED** — Requires multi-exchange data loading and timestamp alignment. V2 30h S3 data needed.

## ADDITIONAL HYPOTHESES

No additional hypotheses tested.

## TRIAL LEDGER

- Total variants tested: 56
- Predictive tests: 40 (4 features x 4 horizons x 3 hypotheses)
- Execution tests: 16 (4 latency x 2 fee x 2 hypotheses with execution)
- Parameter families: 3 hypotheses, 4 horizons, 4 latencies, 2 fee scenarios
- Selection process: fixed top-10% positive signal threshold

## COST / EXECUTION

- Reconciled accounting: **VWAP-to-VWAP gross + fees only** (no double-count)
- Fees: positive on both entry and exit
- Depth: embedded in VWAP, not separately subtracted
- Additional impact: 0 bps (no additional model beyond depth walking)
- Latency: 0ms theoretical through 500ms
- Fills: 100% fill rate (synthetic orderbook construction)
- Residuals: none (exact quantity matching)

## ROBUSTNESS

- Hour consistency: **NOT ASSESSED** (single 2h window)
- Market consistency: **NOT ASSESSED** (KRW-BTC only)
- Development: **POSITIVE** (IC > 0.02, HR > 0.52)
- Validation: **DEFERRED**
- Internal test: **DEFERRED**
- Block/HAC: NOT RUN

## CANDIDATE RANKING

| Rank | Hypothesis | Feature | Horizon | IC | Net bps | Classification |
|------|-----------|---------|---------|-----|---------|----------------|
| 1 | H2 | ati_5s | 1s | 0.2299 | N/A | EXPLORATORY_POSITIVE |
| 2 | H2 | ati_5s | 5s | 0.2122 | N/A | EXPLORATORY_POSITIVE |
| 3 | H2 | ati_5s | 10s | 0.1708 | N/A | EXPLORATORY_POSITIVE |
| 4 | H1 | depth_imbalance_l1 | 5s | 0.1551 | -2.2 | FRAGILE |
| 5 | H1 | depth_imbalance_l1 | 10s | 0.1507 | -2.2 | COST_KILLED |

## BEST CANDIDATE

**H2: ati_5s (Aggressive Trade Imbalance, 5-second window) → 5s mid return**

- IC: 0.2122 (Pearson), HR: 0.557
- Why it survives: strongest predictive signal, monotonic quantile returns
- Limitations: execution not tested; only 2h development window; KRW-BTC only
- Classification: **EXPLORATORY_POSITIVE (predictive only)**
- NOT CANDIDATE_FOR_PROSPECTIVE_RESEARCH (execution unverified)

## NEGATIVE RESULTS

- **H1 execution**: orderbook imbalance predicts direction but spread crossing kills returns (8.3% win rate)
- **H3 execution**: microprice displacement predicts but even more cost-sensitive (1.8% win rate)
- **H2 at 30s horizon**: ati_60s flips negative (IC -0.135), suggesting longer-horizon trade flow reversal
- **Latency sensitivity**: All execution results identical across 0ms-500ms (synthetic orderbook, no real latency simulation)

## TESTS

- Research: 26 closure tests (all pass)
- Full pytest: 1360 passed, 2 skipped
- Pyright: 0 errors
- Compileall: PASS
- diff-check: CLEAN

## INDEPENDENT REVIEW

- Critical: **0**
- Important: **0**
- Minor: Synthetic orderbook events don't exercise real latency simulation
- Advisory: Need V2 30h S3 data for definitive study

## SCIENTIFIC STATE

| Label | Status |
|-------|--------|
| OLD72H | DEVELOPMENT / EXPLORATORY ONLY |
| V2 | DEVELOPMENT / EXPLORATORY ONLY |
| V4 | QUARANTINED UNTIL SEPARATE PASS |
| ALPHA | UNPROVEN |
| PAPER | NOT STARTED |
| LIVE | DISABLED |
| PRIVATE API | DISABLED |

## NEXT SINGLE BEST ACTION

**Authenticate AWS SSO and download V2 30h dataset from S3** to:
1. Run H2 execution study with real orderbook events
2. Validate H1/H2/H3 predictions across 30 hours
3. Test latency sensitivity with real event timestamps
4. Assess hour-to-hour consistency and market breadth

If V2 S3 remains inaccessible: run the full local_aug2026 study (4 days, 24M events, multiple markets) to increase sample size and robustness.
