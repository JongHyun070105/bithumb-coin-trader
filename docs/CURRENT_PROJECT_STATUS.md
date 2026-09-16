# Bithumb Coin Trader — Current Project Status

**Last Updated**: 2026-09-17 (overnight autonomous session)

## Scientific State

| Label | Status |
|-------|--------|
| ALPHA | **UNPROVEN** |
| PAPER | NOT STARTED |
| LIVE | DISABLED |
| PRIVATE API | DISABLED |

## V2 Research (Complete)

**Dataset**: aws-validation-30h-20260912-6576f63 (authoritative V2 30h)
- 2,272 DATA_PRESENT, 8 UNKNOWN_MISSING
- Full-resolution BTC/ETH/XRP study completed
- Real future-book execution (48 scenarios, all negative)

**Result**: **NO EXECUTABLE TAKER CANDIDATE**

| Hypothesis | Best IC | Best Execution | Classification |
|-----------|---------|---------------|----------------|
| H1: Orderbook Imbalance | XRP 0.33 @ 30s | BTC -1.94 bps | COST_KILLED |
| H2: ATI / Trade Flow | 0.24 @ 1s | Not completed | PREDICTIVE_ONLY |
| H3: Microprice | ETH 0.28 @ 30s | -6.17 bps | COST_KILLED |
| H4/H5: Cross-Exchange | IC -0.07 | Not executable | PREDICTIVE_BUT_UNTRADEABLE |

## V4 Validation (In Progress)

**Status**: RUNNING
- EC2 instance: i-008bc503c1136349f (RUNNING)
- SSM: Online
- S3: ~1 hour of coverage visible
- Final verdict: **NOT YET AVAILABLE**

## Architecture

- Collector: Bithumb/Upbit/Binance public data
- Storage: S3 (read-only research access)
- Dashboard: React 19, air-gapped, read-only
- Tests: 1360 passed, 2 skipped

## Repository

- `main`: stable release (fdab66b)
- `develop`: integration branch (active)
- Remote branches: 7 (target: 2)
- Archive tags: 7

## Dashboard

Read-only React dashboard with:
- Project status matrix
- V2 research results (H1-H3, execution, costs)
- V4 validation status
- Safety center (all gates locked)
- Evidence chain verification
- No trading controls

## Next Actions

1. Monitor V4 until natural termination
2. Perform V4 final audit when complete
3. If V4 data usable: register as new dataset, run research
4. Continue dashboard/API integration
5. Clean remaining branches
6. Promote develop → main
