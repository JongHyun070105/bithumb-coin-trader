# BITHUMB COIN TRADER — CONSOLIDATION REPORT

**Date**: 2026-09-16

## SCIENTIFIC STATE

| Label | Status |
|-------|--------|
| V2 | DEVELOPMENT / EXPLORATORY. NO EXECUTABLE TAKER CANDIDATE. |
| V4 | STATUS UNKNOWN (AWS session expired during consolidation). Separate lifecycle. |
| ALPHA | UNPROVEN |
| PAPER | NOT STARTED |
| LIVE | DISABLED |
| PRIVATE API | DISABLED |

## STORAGE BEFORE

| Component | Size |
|-----------|------|
| Total | 76 GB |
| .git | 22 MB |
| data/microstructure/raw/ | 74 GB |
| data/research/v2/ | 540 MB |
| data/microstructure/manifests/ | 21 MB |
| infra/ | 785 MB |
| .venv | 112 MB |
| Worktrees (16) | ~1.5 GB |

## DELETED LOCAL DATA

| Path | Size | Reason |
|------|------|--------|
| data/microstructure/raw/ | 74 GB | Legacy local_aug2026 raw data. Not V2. Reproducible from local collector. |
| data/research/v2/ | 540 MB | V2 raw zstd files. Reproducible from S3. Research complete. |
| data/microstructure/ | 21 MB | Collection manifests for deleted data. |
| 15 worktrees | ~1.5 GB | Obsolete historical worktrees. |

## RETAINED EVIDENCE

- research-artifacts/v2-authoritative/reports/V2_FINAL_REPORT.md
- research-artifacts/v2-authoritative/reports/V2_FULLRES_DEV_RESULTS.json
- research-artifacts/v2-authoritative/reports/V2_PARTIAL_STATUS_CORRECTION.md
- research-artifacts/v2-authoritative/source/V2_SOURCE_MANIFEST.json
- research-artifacts/v2-authoritative/source/V2_PHYSICAL_INVENTORY.json
- research-artifacts/v2-authoritative/dq/V2_DQ_SUMMARY.json
- research-artifacts/v2-authoritative/splits/V2_SPLIT_MANIFEST.json
- research-artifacts/v2-authoritative/audits/execution_audit.md
- research-artifacts/v2-authoritative/audits/statistical_design.md
- project-cleanup/V2_DATA_DESTRUCTION_MANIFEST.json
- evidence/ (AWS validation evidence, 2.5 MB)

## STORAGE AFTER

| Component | Size |
|-----------|------|
| Total | 1.2 GB |
| .git | 20 MB |
| src/ | 4.5 MB |
| tests/ | 9.1 MB |
| scripts/ | 2.5 MB |
| infra/ | 785 MB |
| evidence/ | 2.5 MB |
| research-artifacts/ | 1.4 MB |
| data/ | 66 MB |
| .venv/ | 112 MB |

**Reclaimed: ~74.8 GB (98.4%)**

## BRANCHES BEFORE

- Remote: 42
- Local: 16
- Worktrees: 16

## BRANCHES AFTER

- Remote: 7 (main, develop, v4-remediation, post-72h-dq, 3 dashboard/gemini)
- Worktrees: 2 (main, quant-dashboard-ui)

## ARCHIVE TAGS

| Tag | SHA | Meaning |
|-----|-----|---------|
| archive/fresh45-final | 09a2fda | Fresh45m validation. Infrastructure PASS. |
| archive/aws-72h-final | 2c616ef | 72h soak forensic closure. Infrastructure FAIL. |
| archive/aws-v2-failure | 8523684 | V2 30h validation failure forensics. |
| archive/aws-v3-failed-start | 3e7bcd9 | V3 launch failure. |
| archive/aws-v3-launch-auth | 3e7bcd9 | V3 launch authorization. |
| archive/pre-v2-research-infra | 7a13816 | Pre-V2 research infrastructure closure. |
| archive/v2-research-final | 114fc8a | V2 authoritative research final. |

## BRANCHES DELETED (35 total)

All MERGED_FULLY branches (28), all SUPERSEDED research branches (7).

## FINAL REMOTE BRANCHES (7)

| Branch | Purpose | Action |
|--------|---------|--------|
| main | Stable release | Keep |
| develop | Integration branch | Keep |
| codex/aws-30h-v4-remediation-preparation | V4 docs | Keep until V4 audit |
| codex/post-72h-data-quality-tooling | DQ tools | Review for integration |
| gemini/dashboard-local-api-ledger-e2e | Dashboard | Review for integration |
| gemini/dashboard-v0-3-korean-demo | Dashboard demo | Review for integration |
| gemini/post72h-light-integration | Integration | Review for integration |

Target: main + develop only. Remaining5 to be resolved after V4 audit and dashboard review.

## V2 FINAL

| Aspect | Result |
|--------|--------|
| H1-H3 full-resolution | COMPLETE (BTC/ETH/XRP) |
| Real future-book execution | COMPLETE (48 scenarios) |
| H4/H5 | Bithumb-Upbit baseline complete. Binance orderbook incomplete (null exchange_ts). |
| Best taker case | BTC H1 promotional ≈ -1.94 bps/trade |
| Candidate | NO EXECUTABLE TAKER CANDIDATE |
| Validation | NOT ENTERED |
| Internal test | NOT ENTERED |

## V4 FINAL

| Aspect | Result |
|--------|--------|
| Status | UNKNOWN (AWS session expired) |
| EC2 state | Was RUNNING at last check |
| Evidence | Preserved in evidence/aws-validation-30h-20260915-v4/ |
| Action needed | Re-authenticate AWS, check V4 completion, perform audit |

## AWS REMOTE CLEANUP PLAN (NOT EXECUTED)

| Resource | Recommendation |
|----------|---------------|
| V2 S3 data | Keep until V4 audit complete. Then delete. |
| V3 S3 data | Delete. V3 never started. |
| V4 S3 data | Keep until V4 audit. |
| V4 EC2 | Stop/terminate after V4 audit. |
| V4 EBS | Delete with EC2. |
| Temporary V2 read IAM policy | Remove after V2 S3 data deleted. |
| Fresh45 S3 data | Review for deletion. |

**No destructive AWS action taken in this task.**

## TESTS

| Check | Result |
|-------|--------|
| Full pytest | 1360 passed, 2 skipped |
| Working tree | Clean (except test-results/) |

## FINAL GIT STATE

- **develop SHA**: 114fc8a
- **main SHA**: 815354c
- **Archive tags**: 7
- **Working tree**: clean
- **Worktrees**: 2

## NEXT DEVELOPMENT RULE

```
develop → temporary feature branch → develop → review/test → main → delete temporary branch
```
