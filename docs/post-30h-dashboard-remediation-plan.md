# Dashboard & Project State Staleness Audit

**Audited:** 2026-09-23T04:53 UTC  
**Scope:** `project_state.py`, `dashboard_api.py`, `docs/CURRENT_PROJECT_STATUS.md`  
**Action taken:** Audit only — no code changes (unsafe to refactor while v5r1 is running)

---

## Summary of Findings

### 1. `project_state.py` — PARTIALLY STALE

**File:** [`src/bithumb_coin_trader/project_state.py`](file:///Users/macintosh/Documents/ChatGPT/bitcoin-trader/src/bithumb_coin_trader/project_state.py)

| Issue | Location | Severity | Type |
|---|---|---|---|
| `V4State` dataclass has hardcoded `"running"` for `ec2_state` | line 48 | Medium | Hardcoded stale claim |
| `V4State` dataclass has hardcoded `"Online"` for `ssm_agent` | line 49 | Medium | Hardcoded stale claim |
| `_resolve_v4()` unconditionally overwrites final_verdict with `"FAIL"` and root_cause/dataset_research_usability at lines 238–241 | lines 238–241 | Low | Redundant (correct value, but bypasses file-derived state) |
| No `Fresh6HState` or equivalent class for v4r1/v5r1 | — | **High** | Missing state class |
| `_resolve_v4()` only reads v4 (30H) artifacts; no resolver for any 6H run | — | **High** | Missing resolver |
| `resolve_project_state()` has no active-validation slot | lines 298–309 | **High** | Missing live-run awareness |
| `_resolve_storage()` hardcodes `reclaimed_percent=98.4`, `reclaimed_gb=74.8`, `repo_size_mb=890.0` | lines 292–294 | Low | Hardcoded but low-impact |
| `dashboard_mode` field exists but is not populated from a file — may be stale | line 140 | Low | Unclear source |

**Root issue:** No model exists for Fresh 6H validations (v4r1, v5r1, future). The `V4State` class covers only the 30H-v4 run. Any dashboard showing "current validation" will not reflect v5r1 status at all.

### 2. `dashboard_api.py` — CLEAN (evidence-derived)

**File:** [`src/bithumb_coin_trader/dashboard_api.py`](file:///Users/macintosh/Documents/ChatGPT/bitcoin-trader/src/bithumb_coin_trader/dashboard_api.py)

- Header states "No hardcoded values. Strict read-only mode."
- Delegates all state to `resolve_project_state()` — no independent hardcoded values found.
- **Conclusion:** Dashboard API is clean; staleness propagates from `project_state.py`.

### 3. `docs/CURRENT_PROJECT_STATUS.md` — STALE

**File:** [`docs/CURRENT_PROJECT_STATUS.md`](file:///Users/macintosh/Documents/ChatGPT/bitcoin-trader/docs/CURRENT_PROJECT_STATUS.md)

| Issue | Detail |
|---|---|
| Last updated 2026-09-21 01:19 KST | Does not reflect Fresh 6H-v4r1 (FAIL, 2026-09-22) |
| Does not reflect Fresh 6H-v5r1 (currently running, 2026-09-23) |  |
| Does not reflect PR#13 / PR#14 / PR#16 (remediation stacked PRs) | |
| Does not reflect external dataset lane (PR#15) | |
| Validation table ends at Fresh 30H-v2 | |

---

## Post-30H Remediation Plan

> [!IMPORTANT]
> Perform this work **after** Fresh 30H-v3 terminal audit, not before.  
> Do not touch project_state.py while an active AWS validation is running.

### Phase 1: Add Fresh6HState class (after v5r1 PASS audit)

**Target file:** `src/bithumb_coin_trader/project_state.py`

```python
@dataclass
class Fresh6HState:
    epoch: str = ""
    runtime_commit: str = ""
    runtime_tree: str = ""
    t0_utc: str = ""
    ended_at_utc: str = ""
    official_verdict: str = "NOT_RUN"
    go_no_go: str = "NOT_RUN"
    qualifying_cohorts_pass: int = 0
    qualifying_cohorts_fail: int = 0
    conflicting_duplicate_failures: int = 0
    gap_classification_failures: int = 0
    terminal_receipt_epoch_match: bool | None = None
    terminal_receipt_s3_uploaded: bool | None = None
```

Resolver reads from `evidence/{epoch}/terminal-audit.json`.

### Phase 2: Add active-validation awareness

```python
@dataclass 
class ActiveValidationState:
    is_running: bool = False
    epoch: str = ""
    run_id: str = ""
    t0_utc: str = ""
    natural_end_utc: str = ""
    validation_type: str = ""  # "FRESH_6H" | "FRESH_30H"
```

Resolver reads from a well-known lifecycle file path (e.g., `collector-lifecycle.json` if accessible, or a local sentinel file).

### Phase 3: Remove hardcoded V4State fields

Lines 233–241 in `_resolve_v4()`:
- Remove `v4.ec2_state = "running"` (EC2 is shut down after each run)
- Remove `v4.ssm_agent = "Online"` (no longer accurate)
- These should return `"TERMINATED"` or `"N/A"` after run completion

### Phase 4: Update CURRENT_PROJECT_STATUS.md

Add rows for:
- Fresh 6H-v4r1: FAIL (BITHUMB_CONFLICTING_DUPLICATE, SINGLE_SESSION_FINALIZER_GAP)
- Fresh 6H-v5r1: (result after terminal audit)
- Fresh 30H-v3: (after run)

### Phase 5: Test matrix

```
tests/test_project_state.py (new or extend existing):
- test_fresh6h_state_reads_from_terminal_audit()
- test_fresh6h_state_defaults_when_no_audit()
- test_v4_ec2_state_is_not_hardcoded_running()
- test_active_validation_defaults_to_not_running()
- test_resolve_project_state_includes_fresh6h()
```

---

## Safe Immediate Action (before 30H)

The only safe documentation-only correction is adding a staleness notice to `CURRENT_PROJECT_STATUS.md`. This is a no-code change.

**Proposed one-line addition at top of CURRENT_PROJECT_STATUS.md:**
```
> ⚠️ **갱신 필요**: Fresh 6H-v4r1 (FAIL, 2026-09-22) 및 Fresh 6H-v5r1 (실행 중, ~2026-09-23 15:50 KST) 반영 전. 아래 내용은 2026-09-21 기준.
```

This is documentation-only, zero code risk, and safe to commit now.
