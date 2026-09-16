# BRANCH ARCHIVE — 2026-09-16

## Classification Legend

- **MERGED_FULLY**: All commits reachable from main/develop. Safe to delete.
- **UNIQUE_CODE**: Contains code not yet in develop. Needs review/integration.
- **UNIQUE_EVIDENCE**: Contains unique evidence/docs. Preserved via tag.
- **SUPERSEDED**: Superseded by newer work. Safe to delete after tag.
- **ACTIVE**: Currently active branches (main, develop). Never delete.

## Branches

### ACTIVE (keep)
| Branch | Tip | Notes |
|--------|-----|-------|
| main | 815354c | Stable release line |
| develop | 114fc8a | Integration branch (V2 research integrated) |

### MERGED_FULLY (safe to delete after tags)
| Branch | Tip | Tag |
|--------|-----|-----|
| codex/72h-offline-phase2-20260905 | ff1b971 | — |
| codex/72h-offline-phase2-forensic-20260905 | 2c616ef | archive/aws-72h-final |
| codex/72h-offline-phase3-closure-20260906 | 0618734 | — |
| codex/72h-offline-phase4-crosslayer-20260906 | e654f51 | — |
| codex/72h-offline-phase5-postsoak-readiness-20260906 | 753d784 | — |
| codex/72h-offline-phase6-1-forensic-verification-20260906 | 190654e | — |
| codex/72h-offline-phase6-2-evidence-chain-20260906 | 3f4a323 | — |
| codex/72h-offline-phase6-final-contract-20260906 | a9e52e6 | — |
| codex/72h-offline-research-hardening-20260905 | ba89d60 | — |
| codex/aws-30h-prep-v2-20260912-6576f63 | 8523684 | archive/aws-v2-failure |
| codex/aws-30h-v3-preparation-20260915-e9d5d5a | 40f014c | — |
| codex/aws-30h-v3-remediation-20260914 | a78f655 | — |
| codex/aws-30h-v3-seal-closure-20260915-ac81f94 | faa7ad1 | — |
| codex/aws-45m-independent-audit-20260909 | 666a350 | archive/fresh45-final |
| codex/aws-45m-remediation | 9c3e96a | — |
| codex/aws-45m-remediation-validation-20260909 | 270dbe3 | — |
| codex/aws-72h-soak-planning | f23b67b | — |
| codex/aws-fresh45-prep-20260911-1976f0f | 09a2fda | archive/fresh45-final |
| codex/dashboard-v0-2-contract-reconciliation-20260907 | c70f21e | — |
| codex/fix-bounded-supervisor-publisher-lifecycle-test | b43d6b9 | — |
| codex/fix-s3-archive-missing-key-contract-20260910 | 5331003 | — |
| codex/post-72h-final-audit-20260908 | c2aa0d5 | — |
| codex/post72h-final-decision-20260909 | 26afad3 | — |
| codex/post72h-runtime-remediation-v1-20260909 | 4b1c7d4 | — |
| codex/support-30h-transient-duration | a245d51 | — |
| feat/quant-dashboard-ui | e080e14 | — |
| gemini/dashboard-v0-2-evidence-console-20260907 | 6cdea3c | — |
| gemini/post72h-blocked-prep-20260908 | e698e9e | — |
| gemini/post72h-interactive-evidence-20260908 | 31846b7 | — |

### UNIQUE_CODE (review before deleting)
| Branch | Tip | Contains | Action |
|--------|-----|----------|--------|
| codex/aws-30h-v3-launch-authorization-20260915 | 3e7bcd9 | V3 launch docs (2 unique commits) | Tag archive/aws-v3-failed-start |
| codex/aws-30h-v4-remediation-preparation-20260915-3e7bcd9 | 8feb4c5 | V4 launch state docs (8 unique commits) | KEEP until V4 audit |
| codex/microstructure-research-infra-v1-20260915 | 7a13816 | Pre-V2 closure (12 unique commits) | Tag archive/pre-v2-research-infra |
| codex/v2-30h-authoritative-profitability-study-20260916 | 114fc8a | V2 research (19 unique commits, now develop) | SUPERSEDED by develop |
| codex/v2-microstructure-profitability-study-20260916 | a4edae7 | Local substitute V2 study (14 unique) | SUPERSEDED, keep for reference |
| codex/post-72h-data-quality-tooling | acdfa0e | DQ tooling (6 unique commits) | Review for integration |
| gemini/dashboard-local-api-ledger-e2e-20260908 | 22784e5 | Dashboard E2E (23 unique) | Review for dashboard |
| gemini/dashboard-v0-3-korean-demo-20260908 | 2da9363 | Dashboard demo (5 unique) | Review for dashboard |
| gemini/post72h-light-integration-20260908 | 7af600c | Integration code (11 unique) | Review for dashboard |
| codex/aws-30h-prep-20260912-ac0351c | 4463fd8 | AWS prep docs (1 unique) | Tag + delete |
| codex/aws-30h-prep-v1-20260912-00464e5 | 9753a06 | AWS prep v1 (1 unique) | Tag + delete |
| codex/72h-soak-systemd-recovery-20260905 | f1332b5 | Systemd recovery (1 unique) | Tag + delete |

## Deletion Plan

### Phase 1: Fully merged (delete after confirming tags)
Delete all MERGED_FULLY branches (remote).

### Phase 2: Superseded (delete after develop is stable)
- codex/v2-30h-authoritative-profitability-study-20260916 (→ develop)
- codex/v2-microstructure-profitability-study-20260916 (local substitute, superseded)

### Phase 3: Review unique code
- Dashboard branches: review for unique UI features
- codex/aws-30h-v4-remediation-preparation: KEEP until V4 audit
- codex/post-72h-data-quality-tooling: review DQ tools

### Phase 4: Final state
Remote branches: main, develop, (possibly v4-remediation until audit)
