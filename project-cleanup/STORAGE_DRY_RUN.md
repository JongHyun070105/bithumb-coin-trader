# STORAGE DRY RUN — DELETION PLAN

## Dry-Run Table

| PATH | SIZE | CLASS | DELETE? | WHY | REPRODUCIBLE? | EVIDENCE RETAINED? |
|------|------|-------|---------|-----|---------------|-------------------|
| data/microstructure/raw/ | 74 GB | DELETE_AFTER_RESEARCH | YES | Legacy local_aug2026 raw data. NOT V2. Not used in final research. | YES (local collector) | YES (manifests retained separately) |
| data/research/v2/ | 540 MB | DELETE_AFTER_RESEARCH | YES | V2 raw zstd files. Research complete. Reproducible from S3. | YES (S3 source) | YES (V2_DATA_DESTRUCTION_MANIFEST.json) |
| data/microstructure/manifests/ | 21 MB | DELETE_AFTER_RESEARCH | YES | Collection manifests for legacy data | YES | N/A |
| data/microstructure/quarantine/ | 0 B | DELETE_AFTER_RESEARCH | YES | Empty directory | N/A | N/A |
| data/microstructure/orderbook/ | 304 KB | DELETE_AFTER_RESEARCH | YES | Empty/legacy | N/A | N/A |
| data/microstructure/ticker/ | 12 KB | DELETE_AFTER_RESEARCH | YES | Empty/legacy | N/A | N/A |
| data/microstructure/trade/ | 8 KB | DELETE_AFTER_RESEARCH | YES | Empty/legacy | N/A | N/A |
| project-cleanup/ | <1 MB | KEEP_SMALL_EVIDENCE | NO | Cleanup documentation | N/A | N/A |

## Expected Reclaim

- data/microstructure/raw/: **74 GB**
- data/research/v2/: **540 MB**
- data/microstructure/manifests/: **21 MB**
- Other data/microstructure/: **~1 MB**
- **TOTAL: ~74.5 GB**

## Not Deleted (intentionally)

- .git/ (22 MB) — repository data
- .venv/ (112 MB) — Python environment, regenerable
- src/ — source code
- tests/ — test suite
- scripts/ — research scripts
- infra/ (785 MB) — infrastructure code
- evidence/ (2.5 MB) — AWS validation evidence
- research-artifacts/ (1.4 MB) — final research reports
- test-results/ — user-owned, NEVER touch
- dashboard/ — project UI

## Worktrees (separate cleanup)

16 worktrees, ~1.5 GB total. Will be removed after branch consolidation.
