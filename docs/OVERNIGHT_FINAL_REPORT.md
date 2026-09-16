# BITHUMB COIN TRADER — OVERNIGHT V4 RESEARCH & PROJECT FINALIZATION

**Final Report**: 2026-09-17
**Session**: Autonomous overnight (multi-phase)

## EXECUTIVE STATE

| Metric | Value |
|--------|-------|
| main SHA | 41c3796 |
| develop SHA | 41c3796 |
| Remote branches | 4 |
| Project size | 1.2 GB |
| Tests | 1371 passed, 2 skipped |
| V4 state | **RUNNING** |

## V4 INFRA AUDIT

| Verdict | Status |
|---------|--------|
| EC2_STATE | **RUNNING** |
| SSM_AGENT | **ONLINE** |
| COLLECTOR_PROCESS | **UNKNOWN** (cannot verify without SSM SendCommand) |
| S3_COVERAGE | 1 hour visible (2026-09-15_10) |
| FINAL_VERDICT | **NOT YET AVAILABLE** |

**V4 is still running.** Cannot perform final audit until natural termination.

**Known issue:** Only 1 hour of S3 coverage visible despite V4 running since 2026-09-15T10:26 UTC. Possible causes:
- Incremental archive behavior
- Finalization not yet performed
- Archive problem
- Collector problem

**DO NOT choose among these until terminal state is proven.**

## ENGINEERING COMPLETED

### 1. Maker Simulator
- New `maker_simulator.py` with Conservative/Base/Optimistic fill models
- Passive BUY limit order evaluation against future events
- Queue-ahead approximation with configurable multiplier
- Cancellation horizon
- Adverse selection measurement
- 9 tests pass

### 2. Binance Adapter Fix
- Handles null `exchange_ts` (Binance diff-depth format)
- Parses `bids`/`asks` arrays directly (not just `orderbook_units`)
- Uses `local_recv_ts` as causal availability when exchange_ts absent
- Verified: 21,071 Binance BTCUSDT events parsed
- 2 regression tests added

### 3. Project State Resolver
- Single source of truth for dashboard/API
- Derives all state from tracked artifacts
- No hardcoded RUNNING/PASS/FAIL values
- Generates `dashboard-data/PROJECT_STATUS.json`

### 4. Dashboard/API Integration
- Updated Overview with V2 result + V4 status
- Updated ResearchLab with V2 study results
- API endpoints: `/api/status`, `/api/v2/summary`, `/api/v4/status`, `/api/evidence`, `/api/datasets`, `/api/safety`
- ErrorBoundary component
- Formatters utility

### 5. Branch Cleanup
- 42 → 4 remote branches
- 7 archive tags created
- Remaining: main, develop, V4-remediation, dashboard-local-api

## V2 REPLICATION STATUS

V2 results preserved on main. Summary:
- H1-H3 full-resolution BTC/ETH/XRP: **COMPLETE**
- Real future-book execution: **COMPLETE** (48 scenarios)
- Best taker: BTC H1 promotional **-1.94 bps**
- **NO EXECUTABLE TAKER CANDIDATE**
- Validation: NOT ENTERED
- Internal test: NOT ENTERED

## SCIENTIFIC STATE

| Label | Status |
|-------|--------|
| ALPHA | **UNPROVEN** |
| PAPER | NOT STARTED |
| LIVE | DISABLED |
| PRIVATE API | DISABLED |
| V2 | NO EXECUTABLE TAKER CANDIDATE |
| V4 | RUNNING — final verdict NOT YET AVAILABLE |

## NEXT SINGLE BEST ACTION

**Monitor V4 until natural termination, then perform immutable final audit.**

If V4 data usable: register dataset, freeze split, run research lifecycle with maker focus.
If V4 data not usable: document failure, use maker/cross-exchange engineering for V5 readiness.
