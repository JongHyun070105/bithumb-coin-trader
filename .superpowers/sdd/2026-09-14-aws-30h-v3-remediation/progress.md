# AWS 30H V3 Remediation — SDD Progress Ledger

**Branch:** `codex/aws-30h-v3-remediation-20260914`
**HEAD at init:** `cbf8d191886bacb604b033e1d862eda5af837621`
**Init time:** 2026-09-14T06:26:00Z

---

## Context Recovery

| Check | Result |
|---|---|
| Worktree path | `/bitcoin-trader-worktrees/aws-30h-v3-remediation-20260914` |
| Branch | `codex/aws-30h-v3-remediation-20260914` ✅ |
| HEAD | `cbf8d191886bacb604b033e1d862eda5af837621` ✅ |
| Last commit content | docs-only: test-results/ → SDD path fix in plan ✅ |
| Production implementation started | NO ✅ |
| `test-results/` present | NOT PRESENT (untracked user dir, not in worktree) ✅ |
| V2 evidence intact | `evidence/aws-validation-30h-20260912-6576f63/actual-start-evidence.json` exists ✅ |
| Plan file | `docs/superpowers/plans/2026-09-14-aws-30h-v3-remediation.md` ✅ |
| Spec file | `docs/superpowers/specs/2026-09-14-aws-30h-v3-remediation-design.md` ✅ |

**CONTEXT RECOVERY: PASS**

---

## Plan Preflight Review

### Shared File / Interface Conflicts

| Task | Files | Conflict |
|---|---|---|
| T1 | `evidence_hashing.py`, `actual_start_evidence.py` (new) | None — isolated modules |
| T2 | `qualification_schedule.py` (new), `bounded_supervisor.py` | T2 modifies supervisor; no T1 overlap |
| T3 | `incremental_finalizer.py` (new), `microstructure_storage.py` | T3/T4 share session_evidence; sequential OK |
| T4 | `session_evidence.py`, `feed_hour_coverage.py` (new) | T3 creates finalizer; T4 depends on it — sequential required |
| T5 | `cross_market_collector.py` | T5 depends on T3+T4 modules — sequential required |
| T6 | `pre_soak_archive.py` | T6 depends on T3 receipt shapes — sequential required |
| T7 | `closed_hour_finalizer.py` (new), `archive_scheduler.py` | T7 depends on T3+T4+T6 — sequential required |
| T8 | `audit_72h_soak.py`, `build_epoch_manifest.py` | T8 depends on T1+T7 — sequential required |
| T9 | `scripts/benchmark_incremental_finalization.py` (new) | Depends on T3 — sequential required |
| T10 | Final verification | No new files |

**Verdict:** No concurrent-task file conflicts. Sequential ordering in plan is correct.

### Global Constraints vs Tasks

| Constraint | Task | ✅/⚠️ |
|---|---|---|
| V2 FAIL IMMUTABLE | All | No V2 rewrite in any task ✅ |
| 76 slots exact | T4 | `feed_hour_coverage.py` implements exactly 76 slots ✅ |
| qualification_start = strictly_next_utc_hour | T2 | `qualification_schedule.py` spec section 6.2 ✅ |
| 30 candidate hours fixed | T2 | sealed in schedule, never re-extended ✅ |
| historical_raw_files_opened=0 at shutdown | T3 | incremental_finalizer bounded approach ✅ |
| heartbeat 10s/10s/30s | T5 | session_evidence.py must match auditor sealed values ✅ |
| canonical JSON single helper | T1 | evidence_hashing.py sole implementation ✅ |
| Pyright 0 errors/warnings changed paths | All | each task runs pyright on changed paths ✅ |
| test-results/ never touched | T9 | benchmark output → .superpowers/sdd/... ✅ |
| No AWS/IAM/Terraform | All | local only ✅ |

### Self-Contradictions / Spec-Plan Conflicts

| Item | Status |
|---|---|
| `collection_duration_seconds=108000` in V2 fixture vs sealed `required_qualifying_full_hours=30` | V2 fixture is read-only legacy. V3 uses `required_qualifying_full_hours=30`. No contradiction — different schemas. ✅ |
| T1 Step 2 test expects `captured_at_utc == "2026-09-12T11:24:51Z"` from legacy fixture `observed_post_launch_utc` | Fixture confirmed: `observed_post_launch_utc = "2026-09-12T11:24:51Z"`. ✅ |
| `validate_actual_start` in compose_epoch_contract uses `runtime_commit` and `runtime_fingerprint` (canonical-v2 names) but legacy fixture uses `runtime_code_commit` | This is exactly Root Cause C. T1 fixes by delegating to `normalize_actual_start_evidence`. ✅ RULING: Current compose_epoch_contract.validate_actual_start is broken for V2 legacy fixture. T1 remediates. |
| Plan Step 2 test checks `payload["runtime_commit"]` but fixture uses `runtime_code_commit` | Test reads fixture and calls `expected_identity(payload)` which must extract from legacy names. Implementer must confirm `expected_identity` uses `runtime_code_commit`. ✅ Flagged for implementer. |

### Rulings

- **RULING-001** (2026-09-14): T1 step 2 test `expected_identity(payload)` must extract `runtime_code_commit` (not `runtime_commit`) from V2 legacy fixture to build the `ActualStartIdentity`. Spec §4.3 is authoritative. Recorded in ledger.

### Critical/Important Defects

None found. Plan is implementable as written.

---

## Task Status

| Task | Status | Commit |
|---|---|---|
| Task 1: Canonical Hashing + Actual-Start Normalization | ✅ COMPLETE | `3bf4105` |
| Task 2: Strictly-Next Schedule + Monotonic Stop | ✅ COMPLETE | `ae2427f` |
| Task 3: Durable Incremental Finalization + WAL | ✅ COMPLETE | `cd269fb` |
| Task 4: Owning Sessions + Feed-Hour Coverage | ✅ COMPLETE | `a256bb6` |
| Task 5: Collector Confirmation, Heartbeats, Writer Fences | ✅ COMPLETE | `b831358` → test fixes `1275085`, `2c5e81e` |
| Task 6: Generic Immutable Archive + Receipt v3 | ⏳ IN PROGRESS | — |
| Task 7: Ordered Closed-Hour Finalization + Scheduler | ⬜ PENDING | — |
| Task 8: V3 Contract, Epoch Manifest, Exact-Slot Audit | ⬜ PENDING | — |
| Task 9: Bounded Scale + Cross-Layer Gate | ⬜ PENDING | — |
| Task 10: Full Verification + Independent Review | ⬜ PENDING | — |

---

## Review Log

- **Task 1** (Commit `3bf4105`):
  - Spec Compliance: PASS (after C1 or-chain removal, I1 missing keys, I2 duplicate fields)
  - Code Quality: APPROVED (I1 legacy mismatch test, I2 bad captured_at test, I3 file_sha256 test, M1-M3 import cleanups, M5 EVIDENCE_KIND constant; M4 deferred)
- **Task 2** (Commit `ae2427f`):
  - Spec Compliance: PASS (after C1 supervisor runtime schedule creation, C2 transient launcher V3 window without 108000, I1-I5 hard ceiling/deadline_recomputed/ran_long_enough/load_schedule ordering/runtime test, M1-M2 typing/assertion)
  - Code Quality: APPROVED (after I1 fd leak fix, I2 atomic save_schedule tempfile/cleanup, M1-M3 clean imports/validation/types, A3 load_schedule test)
- **Task 3** (Commit `cd269fb`):
  - Spec Compliance: PASS (FinalizationProgressStore, WAL transaction log, crash reconciliation across all boundaries, bounded incremental finalizer, schema-v5 identity manifests)
  - Code Quality: APPROVED (I1 receipt source path validation, I2 corrupt receipt fail-closed, I3 mark_complete generation safety, I4 path escape & symlink tests, A1 deterministic receipt lookup)
- **Task 4** (Commit `a256bb6`):
  - Spec Compliance: PASS (after interval scoping of disconnects/heartbeats, TOUCHED_PARTIAL opening hour, null timestamp enforcement for verified zero events)
  - Code Quality: APPROVED (after unused import cleanups in feed_hour_coverage.py and test_session_evidence.py)
- **Task 5** (Commits `b831358`, `1275085`, `2c5e81e`):
  - Spec Compliance: PASS (C1 per-session Bithumb confirmation set + idempotent guard; C2 frozen journal persistence via save_frozen_journal/load_frozen_journal; I1 Upbit SUBSCRIPTION_SET_MISMATCH; I2 stale stream close_session in all 3 loops; I3 recv confirmation loops; M1 heartbeat sort)
  - Code Quality: APPROVED (I1 Binance+Upbit stale-stream tests added; M1 explicit actual_start_utc in test_writer_clock_regression_raises; all 99 tests pass; 0 pyright errors/warnings)


---

## Final Status

*(populated after Task 10)*
