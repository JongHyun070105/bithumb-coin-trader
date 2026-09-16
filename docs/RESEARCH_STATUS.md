# Research Status

## V2 30-Hour Authoritative Study

**Status**: COMPLETE (development/exploratory phase)

### Dataset
- Epoch: aws-validation-30h-20260912-6576f63
- Source: S3 (2,272 objects, 535.8 MB compressed)
- DQ: 2272 DATA_PRESENT, 8 UNKNOWN_MISSING
- Split: 18h DEV / 6h VAL / 6h TEST (frozen before profitability inspection)

### H1: Orderbook Imbalance
- **Predictive**: EXPLORATORY_POSITIVE for 10s/30s horizons
  - XRP: IC=0.333 @ 30s (strongest)
  - ETH: IC=0.294 @ 30s
  - BTC: IC=0.173 @ 30s
- **Execution**: COST_KILLED
  - BTC: -1.94 bps mean (best case, promotional)
  - ETH: -4.61 bps mean
  - XRP: -5.92 bps mean
- **Classification**: COST_KILLED

### H2: Aggressive Trade Imbalance (ATI)
- **Predictive**: EXPLORATORY_POSITIVE
  - ati_5s IC=0.23 @ 1s (strongest H2 feature)
  - ati_5s IC=0.19 @ 30s
- **Execution**: NOT COMPLETED (insufficient paired data in current implementation)
- **Classification**: PREDICTIVE_ONLY

### H3: Microprice Displacement
- **Predictive**: EXPLORATORY_POSITIVE for 10s/30s
  - ETH: IC=0.277 @ 30s
  - XRP: IC=0.274 @ 30s
  - BTC: IC=0.100 @ 30s
- **Execution**: COST_KILLED
  - BTC: -3.85 bps
  - ETH: -6.17 bps
  - XRP: -8.78 bps
- **Classification**: COST_KILLED

### H4/H5: Cross-Exchange
- **Bithumb-Upbit basis**: mean -0.48 bps, std 3.59 bps
- **Predictive**: Weak mean-reversion (IC=-0.07), unexecutable
- **Binance**: INCOMPLETE (null exchange_ts rejected by adapter)
- **Classification**: PREDICTIVE_BUT_UNTRADEABLE / INCOMPLETE

### Engineering Fixes Applied
- `get_pnl_summary` O(n²) → O(n) (was timing out at 72K trades)
- Execution pre-computed book indices (3+ hours → 17 minutes)
- SHIB negative-price orderbook bug fixed
- qi_l1/qi_l3/qi_l5 depth computation bug fixed
- zstd decompression support added to adapter
- Partial SELL fill residual tracking fixed

### Key Finding
Predictive signals exist (IC up to 0.33) but taker execution costs (1.9-8.8 bps per trade) systematically exceed the exploitable edge. Even zero-fee promotional conditions cannot make taker execution profitable.

## V4 Validation

**Status**: RUNNING (final verdict NOT YET AVAILABLE)
- Started: 2026-09-15T10:26:33 UTC
- Planned stop: 2026-09-16T17:00:00 UTC
- S3 observation: ~1 hour of coverage currently visible
- Possible causes for limited visibility: incremental archive, finalization pending, collector issue

## Future Research Directions

1. **Maker/Passive execution**: Post passive orders on predicted side to capture spread
2. **Cross-exchange**: Fix Binance adapter (null exchange_ts), test lead-lag signals
3. **Regime analysis**: Test signals conditioned on spread/volatility/depth
4. **V4 prospective data**: If V4 produces usable data, run same H1-H3 with prospective validation
