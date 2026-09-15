# Research Correction Note

## Date: 2026-09-15

## Corrections Made

### 1. Local 74GB Dataset Lineage (ISSUE A)

**Previous claim**: Local 74GB data was "Fresh45" (45-minute validation reference data).

**Reality**: The local data spans Aug 25-28, 2026 with NO provenance metadata:
- No `collector_epoch` in manifests
- No `collector_run_id` in manifests
- No receipt files
- `git_commit=HEAD` (not a specific commit)
- Predates ALL documented AWS validation runs (Sep 2+)

**Correction**: Renamed to `local_microstructure_aug2026` with `provenance_confidence=UNATTRIBUTED`. Cannot be called Fresh45 without evidence.

### 2. V2 DQ Count (ISSUE B)

**Previous claim**: V2 had "5396 DATA_PRESENT + 8 UNKNOWN_MISSING".

**Reality**: V2 authoritative universe is 30 hours x 76 feeds = 2280 slots. The 5396 number came from counting unrelated local files.

**Correction**: Built `build_v2_authoritative_dq_catalog()` that constructs the correct 2280-slot universe:
- 2272 DATA_PRESENT
- 8 UNKNOWN_MISSING (the known eight)
- Total = 2280

### 3. File-Size DQ Proxy (ISSUE C)

**Previous claim**: `file_size > 0` established `DATA_PRESENT`.

**Reality**: A non-empty file does not prove parseability, correct schema, expected feed/market, or valid event count.

**Correction**: Added intermediate DQ states (`PHYSICAL_FILE_PRESENT`, `PARSE_VALID`, `MANIFEST_BOUND`, `COHORT_VALID`). File-size check now only establishes `PHYSICAL_FILE_PRESENT`, which requires exclusion.

### 4. Fast-Parser Bypass (ISSUE D)

**Previous claim**: H1 IC=0.132, H3 IC=0.119, H2 IC=0.018 from "direct fast parser".

**Reality**: These bypassed the canonical pipeline and are UNVERIFIED.

**Correction**: Fixed canonical pipeline performance (3,467 events/sec, 14s per market-hour). Reran through official pipeline. Previous numbers do NOT match official results.

### 5. Premature Candidate Freeze (ISSUE E)

**Previous claim**: "READY TO FREEZE A CANDIDATE = YES" based on single-hour, single-market, unattributed data.

**Reality**: Insufficient evidence.

**Correction**: "READY TO FREEZE A CANDIDATE = NO".

## Official Pipeline Results (6 hours, KRW-BTC)

| Hypothesis | n | IC | HR | Classification |
|------------|---|-----|------|----------------|
| H1 L1→30s | 240,655 | 0.097 | 51.4% | FRAGILE |
| H3 bias→30s | 240,655 | 0.059 | 51.6% | FRAGILE |
| H1 L5→30s | 240,655 | 0.058 | 51.6% | FRAGILE |
| H1 L1→5s | 243,695 | 0.111 | 47.7% | FRAGILE |
| H3 bias→5s | 243,695 | 0.088 | 48.9% | FRAGILE |
| H1 L1→1s | 244,317 | 0.057 | 34.7% | FRAGILE |

**Key observations**:
- IC is positive but HR is near or below 50% (mean reversion at short horizons)
- Quantile returns show monotonic pattern (Q1 negative, Q5 positive)
- Effect is real but too small for profitable execution after costs
- 1s horizon shows strong mean reversion (HR ~35%)

## Scientific State

- ALPHA: UNPROVEN
- PAPER: NOT STARTED
- LIVE: DISABLED
- PRIVATE API: DISABLED
- READY TO FREEZE: NO
