# BITHUMB COIN TRADER — OVERNIGHT V4 RESEARCH & INTEGRATION REPORT

## MAIN PROMOTION 1

- Before SHA: 815354c (main, V3 seal closure)
- After SHA: fdab66b (main, V2 research + project consolidation)
- Tests: 1360 passed, 2 skipped

## V4 INFRA FINAL

| Verdict | Status |
|---------|--------|
| PROCESS | **NOT YET AVAILABLE** (V4 still RUNNING) |
| ARCHIVE | NOT YET AVAILABLE |
| DQ | NOT YET AVAILABLE |
| EVIDENCE CONTRACT | NOT YET AVAILABLE |
| OVERALL | NOT YET AVAILABLE |

**V4 was NOT prematurely classified as FAIL.** A premature FAIL verdict committed in 8ab90f2 was immediately corrected.

**Current V4 state:**
- EC2: RUNNING (instance i-008bc503c1136349f)
- SSM: Online
- S3 coverage: 1 hour visible (2026-09-15_10)
- S3 raw data: 0 objects
- Possible causes: incremental archive, finalization pending, collector issue
- Final verdict requires natural process termination

## V4 DATASET

- Identity: aws-validation-30h-20260915-v4
- Run: aws-validation-30h-run-20260915T061253Z-v4
- Actual start: 2026-09-15T10:26:33.652102Z
- Planned stop: 2026-09-16T17:00:00Z
- Research usability: **NOT YET DETERMINED** (awaiting process termination)

## V2 REPLICATION

V2 research completed in prior session. Results preserved on main:

| Aspect | Result |
|--------|--------|
| H1-H3 full-resolution | COMPLETE (BTC/ETH/XRP) |
| Real future-book execution | COMPLETE (48 scenarios) |
| Best taker | BTC H1 promotional -1.94 bps |
| Candidate | NO EXECUTABLE TAKER CANDIDATE |
| Validation | NOT ENTERED |
| Internal test | NOT ENTERED |

## NEW RESEARCH

Not started — awaiting V4 data availability. V4 data is needed for new research lifecycle.

## DASHBOARD

Updated pages:
- **Overview**: V2 result card, V4 status card, current project state
- **ResearchLab**: V2 30h study results, feature family breakdown, execution summary, V4 status
- API: `/api/status`, `/api/v2/research`, `/api/v4/status`, `/api/evidence`

Dashboard remains:
- READ-ONLY
- Air-gapped (zero network calls in frontend)
- No trading controls
- No private API

## BRANCH CLEANUP

**Before**: 42 remote branches, 16 worktrees, 0 tags
**After**: 7 remote branches, 2 worktrees, 7 archive tags

Remaining remote branches:
- `main` — stable release
- `develop` — integration
- `codex/aws-30h-v4-remediation-preparation` — V4 docs (keep until audit)
- `codex/post-72h-data-quality-tooling` — DQ tools (review)
- 3 gemini branches — dashboard review

## MAIN PROMOTION 2

- Before SHA: fdab66b
- After SHA: 9b6729e
- Tests: 1360 passed, 2 skipped
- Includes: dashboard updates, API, V4 preliminary observation, documentation

## STORAGE

| Metric | Value |
|--------|-------|
| Before | 76 GB |
| After | 1.2 GB |
| Reclaimed | 74.8 GB (98.4%) |

## SCIENTIFIC STATE

| Label | Status |
|-------|--------|
| ALPHA | UNPROVEN |
| PAPER | NOT STARTED |
| LIVE | DISABLED |
| PRIVATE API | DISABLED |
| V2 | DEVELOPMENT / EXPLORATORY. NO EXECUTABLE TAKER CANDIDATE. |
| V4 | RUNNING — final verdict NOT YET AVAILABLE |

## BLOCKERS

**V4 process still running.** Cannot perform final V4 audit until natural termination. No action needed — just monitor at low frequency.

## NEXT SINGLE BEST ACTION

**Wait for V4 process to reach natural terminal state, then perform immutable final audit.**

If V4 data becomes usable: register new dataset, freeze split, run research lifecycle.
If V4 data is not usable: document failure, recommend next validation attempt.
