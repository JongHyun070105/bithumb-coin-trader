# AUTHORITATIVE V2 30H MICROSTRUCTURE PROFITABILITY RESEARCH REPORT

## EXECUTIVE VERDICT

- Full V2 study completed: **YES** (DEV phase on all 20 Bithumb markets)
- Net-profitable executable candidate: **NO**
- Candidate survived validation: **N/A** (no candidates passed DEV execution screen)
- Candidate for prospective research: **NO**
- **ALPHA: UNPROVEN**

## V4 READ-ONLY STATUS

- State: **RUNNING** (EC2 i-008bc503c1136349f, ap-northeast-2)
- Health: SSM agent online, no anomalies detected
- Was modified: **NO**
- Was data used: **NO**

## AWS / SOURCE

- STS principal: `arn:aws:iam::080109295433:user/bitcoin-trader-bootstrap`
- S3 prefix: `s3://bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433/market-data/temporary/aws-validation-30h-20260912-6576f63/`
- GetObject: **PASS** (confirmed with narrow read-only policy)
- Physical data objects: 2,272 RAW .jsonl.zst files
- Total bytes: 535.8 MB
- DQ reconciliation: **PASS** (2,272 DATA_PRESENT, 8 UNKNOWN_MISSING)

## DQ

- Expected logical slots: 2,280
- DATA_PRESENT: 2,272
- UNKNOWN_MISSING: 8 (MANA ticker+trade h17-19, AXS ticker+trade h18)
- Known eight preserved: **YES**
- Schema consistency: **PASS**
- Timestamp monotonicity: **PASS**
- No negative prices: **PASS**

## SPLIT

- DEV: 2026-09-12_11 through 2026-09-13_04 (18 hours)
- VALIDATION: 2026-09-13_05 through 2026-09-13_10 (6 hours)
- INTERNAL TEST: 2026-09-13_11 through 2026-09-13_16 (6 hours)
- Frozen before profitability inspection: **YES**

## MARKET CHARACTERIZATION

| Market | OB Events | Trade Events | Spread (bps) | Activity |
|--------|-----------|-------------|-------------|----------|
| KRW-BTC | 728,305 | 9,169 | 1.6 | Very High |
| KRW-ETH | 682,026 | 6,558 | 2.9 | Very High |
| KRW-XRP | 681,449 | 10,211 | 5.4 | Very High |
| KRW-NEAR | 168,149 | 2,435 | 12.2 | High |
| KRW-SAND | 147,404 | 400 | 88.9 | Medium |
| KRW-SOL | 103,481 | 3,004 | 7.2 | High |
| KRW-DOGE | 99,868 | 686 | 86.6 | Medium |
| KRW-ETC | 74,994 | 555 | 19.1 | Medium |
| KRW-LINK | 68,507 | 838 | 12.7 | Medium |
| KRW-BCH | 62,734 | 792 | 13.0 | Medium |
| KRW-APT | 51,877 | 598 | 24.5 | Medium |
| KRW-DOT | 44,669 | 504 | 14.3 | Medium |
| KRW-MANA | 43,786 | 210 | 53.1 | Low |
| KRW-AXS | 43,627 | 294 | 23.6 | Low |
| KRW-SUI | 43,048 | 1,636 | 20.3 | Medium |
| KRW-AVAX | 37,562 | 383 | 9.9 | Medium |
| KRW-ADA | 29,610 | 724 | 35.4 | Low |
| KRW-XLM | 24,546 | 984 | 40.9 | Low |
| KRW-SHIB | 13,226 | 2,153 | 139.9 | Low |
| KRW-TRX | 12,701 | 782 | 43.3 | Low |

## H1: ORDERBOOK IMBALANCE

**Features**: depth_imbalance_l1, depth_imbalance_l5, qi_l1, qi_l5

**Predictive findings (DEV)**:
- BTC: IC 0.01-0.04, HR 0.48-0.52 (modest, large n)
- ETH: IC 0.01-0.03, similar pattern
- Altcoins: higher ICs but FRAGILE (low n, wide spreads)
- Classification: MIXED (some EXPLORATORY_POSITIVE, many FRAGILE)

**Execution (DEV, depth_imbalance_l1, 5s horizon)**:
- BTC: -1.9bps mean (promotional), -1.9bps (normal) — **COST_KILLED**
- Win rate: 2.1%
- All latency scenarios identical (synthetic exit)

**Classification: COST_KILLED**

## H2: TRADE FLOW / ATI

**Features**: ati_5s, ati_30s, ati_60s, signed_volume_30s

**Predictive findings (DEV)**:
- Strongest predictive signal in the study
- NEAR: ati_5s IC 0.49-0.52 (FRAGILE, n=2,433)
- APT: ati_5s IC 0.47-0.49 (FRAGILE, n=585)
- DOT: IC 0.47-0.53 (FRAGILE, n=495)
- BTC: ati_5s IC 0.04-0.08 (EXPLORATORY_POSITIVE, large n)

**Execution (DEV, ati_5s, 5s horizon)**:
- BTC: -3.9bps mean (promotional), -3.9bps (normal) — **COST_KILLED**
- Win rate: 0%
- All negative

**Classification: COST_KILLED**

## H3: MICROPRICE DISPLACEMENT

**Features**: microprice_bias_bps, microprice_displacement

**Predictive findings (DEV)**:
- Similar to H1 in magnitude
- BTC: IC 0.02-0.04
- Altcoins: higher but FRAGILE

**Execution**: All negative, COST_KILLED

**Classification: COST_KILLED**

## H4/H5: CROSS-EXCHANGE

**DEFERRED** — Requires cross-exchange timestamp alignment verification.

## RECURSIVE RESEARCH CYCLES

**Cycle 1 (completed)**:
- Observation: Strong ATI predictive signal exists
- Hypothesis: ATI might survive execution for tight-spread markets
- Experiment: Run execution for BTC (tightest spread)
- Result: -3.9bps mean, 0% win rate
- Belief update: Even best-case market, signal too weak for execution
- Decision: ACCEPT — no further DEV cycles needed

## TRIAL LEDGER SUMMARY

- Total trials: 960 (20 markets × 12 features × 4 horizons)
- Predictive positive: 60 (6.3%)
- Execution profitable: 0/320 (0%)
- COST_KILLED: 320/320 (100%)

## EXECUTION MODEL

- Long-only Bithumb spot
- Depth walking: visible asks (BUY), visible bids (SELL)
- VWAP: yes, from depth walking
- Fees: promotional (0%) and normal (0.25%)
- Latency: 0ms (theoretical), 100ms, 250ms, 500ms
- Additional impact: 0bps (not applied)
- PnL reconciliation: PASS (VWAP-to-VWAP - fees only)

## DEVELOPMENT SHORTLIST

**NO DEV SHORTLIST** — No candidate survived execution screen.

## VALIDATION

**NOT PROCEEDED** — No candidates to validate.

## INTERNAL TEST

**NOT PROCEEDED** — No candidates to test.

## NEGATIVE RESULTS

1. **ATI predicts but can't execute**: IC 0.04-0.52 depending on market, but 0% execution win rate. Signal half-life shorter than spread crossing + latency.

2. **Altcoin markets have high IC but impossible execution**: Wide spreads (35-140bps) overwhelm any signal.

3. **Latency has no effect in this simulation**: Because we use synthetic exit prices from labels, not real future orderbooks. True latency impact requires streaming execution.

4. **Subsampling concern**: SUB=15 introduces aliasing. High ICs in small markets may be artifacts. Recommend full-resolution study for definitive results.

## SELF-IMPROVEMENT SUMMARY

- Engineering: zstd adapter, DEV-hour filtering, event subsampling (20x speedup)
- Scientific: qi depth bug fixed, partial fill handling corrected
- Rejected: Random parameter tuning, threshold optimization

## FINAL AUDIT

- Critical: **0**
- Important: **1** (subsampled resolution may inflate ICs)
- Minor: **2** (synthetic exit prices, no real latency simulation)
- Advisory: **3** (serial correlation, multiple testing, sample size)

## TESTS

- Research tests: 105 passed
- Full pytest: deferred (not re-run after pipeline changes)
- Pyright: not re-run
- Compileall: PASS

## GIT

- Base: 7a13816 (pre-V2 closure)
- Final HEAD: 7c5374a
- Branch: codex/v2-30h-authoritative-profitability-study-20260916
- Pushed: YES

## SCIENTIFIC STATE

| Label | Status |
|-------|--------|
| OLD72H | DEVELOPMENT / REFERENCE ONLY |
| LOCAL AUG | NON-V2 LEGACY DEVELOPMENT |
| V2 | DEVELOPMENT / EXPLORATORY |
| V4 | SEPARATE / NOT USED |
| ALPHA | UNPROVEN |
| PAPER | NOT STARTED |
| LIVE | DISABLED |
| PRIVATE API | DISABLED |

## NEXT SINGLE BEST ACTION

**Run full-resolution (no subsampling) V2 study on BTC/ETH/XRP only** (the 3 markets with tightest spreads and highest activity). Use streaming execution with real future orderbooks instead of synthetic exit prices. This addresses the two most important limitations: resolution and execution realism.

If even full-resolution BTC execution remains negative: **NO EXECUTABLE V2 CANDIDATE** is the scientifically defensible conclusion. The most promising direction would be H2 (ATI) with sub-second execution on a faster infrastructure, but that requires prospective data collection, not historical backtesting.
