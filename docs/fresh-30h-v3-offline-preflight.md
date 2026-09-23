# Fresh 30H-v3 Offline Preflight Pack

**Status:** OFFLINE ONLY — T0 UNBOUND  
**Prepared:** 2026-09-23T04:49 UTC (local 13:49 KST)  
**Runtime candidate:** `94b9293e24a39ede9929ffaf6245fb442a646e61`  
**Tree:** `005d75feed56606d70349143599c98f1c192c4f0`  
**Branch:** `gpt/fresh-6h-v4r1-failure-fix-20260922` (origin pushed, PR#16 open)

> [!IMPORTANT]
> **T0 is UNBOUND.** This document must not be used to launch a 30H run until:
> 1. Fresh 6H-v5r1 terminal audit returns **PASS** (natural end ~2026-09-23 15:50 KST).
> 2. A human explicitly issues Fresh 30H-v3 authorization.
> 3. All PASS gates below are cleared.

---

## Identity Templates

```
EPOCH_TEMPLATE     = aws-validation-fresh-30h-{DATE}T{HHMMSS}Z-v3
RUN_ID_TEMPLATE    = aws-validation-fresh-30h-run-{DATE}T{HHMMSS}Z-v3
S3_PREFIX_TEMPLATE = market-data/temporary/aws-validation-fresh-30h-{DATE}T{HHMMSS}Z-v3
```

> These must be instantiated at T0 (authorization moment), not before.  
> Never reuse an epoch from any prior run (v1/v2/v3/v4/v4r1).

---

## Duration & Timing Contract (reuse from v4 template)

| Parameter | Value | Source |
|---|---|---|
| `required_qualifying_full_hours` | **30** | `infra/aws/seals/aws-validation-30h-20260915-v4.timing-contract.json` |
| `duration_seconds` | **108 000** | Task requirement |
| `maximum_collection_window_seconds` | **108 000** | duration |
| `finalization_timeout_seconds` | **180** | v4 benchmark |
| `supervisor_hard_ceiling_seconds` | 108 000 + 180 + 45 = **108 225** | formula |
| `systemd_runtime_max_seconds` | 108 225 + 75 = **108 300** | formula |
| `qualification_start_policy` | `strictly_next_utc_hour(actual_start_utc)` | timing contract |
| `expected_candidate_cohorts` | **30** | 30 qualifying full hours |
| `expected_slots_per_cohort` | **76** | feed universe |
| `total_expected_coverage_slots` | **2 280** | 30 × 76 |

> **Note:** v4 used `maximum_collection_window_seconds=111600` (31h window for 30h qualifying hours).  
> v3 uses `108000` (exact 30h) per task requirement. Adjust if strictly-next-hour boundary analysis shows risk.

---

## Expected Cohort Calculation

Qualification starts at the strictly-next UTC hour boundary after `actual_start_utc`.

```
T0           = <to be filled at launch>
actual_start ≈ T0 + ~4s (collector startup overhead, observed in v4r1)
qual_start   = ceil_hour(actual_start)   # next full UTC hour
qual_end     = qual_start + 30h
cohort_hours = [qual_start_H, qual_start_H+1, ..., qual_start_H+29]  # 30 cohorts
```

**Each cohort must produce:**
- 76 coverage slots (Bithumb 60 + Binance 8 + Upbit 8)
- 76 raw files (one per feed)
- One immutable archive receipt with SHA-256

**Total expected at PASS:**
- 30 PASS receipts
- 2 280 coverage slots, all PASS
- 2 280 raw files, all non-empty

---

## Feed Universe (76 feeds — unchanged from v4)

From `infra/aws/seals/aws-validation-30h-20260915-v4.feed-universe.json`:

- Bithumb: 20 markets × 3 feed types = **60**
- Binance: 4 symbols × 2 feed types = **8**
- Upbit: 4 markets × 2 feed types = **8**
- **Total: 76**

Feed fingerprint must match v4 seal SHA-256 = `52adb4e5f7dc06cbc85ae1926c043ffd44dd47b86f70c09e248c16568e428500`  
(or recompute from `94b9293` tree if code changed).

---

## S3 Archive Convention

```
s3://{BUCKET}/market-data/temporary/{EPOCH}/
  {COHORT_UTC_HOUR}/
    raw/{feed}.jsonl.zst
    coverage/{feed}.json
  archive-receipts/{COHORT_UTC_HOUR}.json
```

**Bucket:** `bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433`  
**Prefix guard:** S3 prefix MUST be empty at T0 before launch. Verified by pre-arm attestation, not direct read.

---

## Artifact Generation Checklist (LOCAL, before authorization)

These can be prepared offline now. They must be re-verified immediately before launch.

- [ ] **A1** Compute feed universe hash from `94b9293` tree:
  ```
  .venv/bin/python -c "
  from bithumb_coin_trader.canonical_market_data import canonical_feeds
  import hashlib, json
  feeds = canonical_feeds()
  h = hashlib.sha256(json.dumps(feeds, sort_keys=True).encode()).hexdigest()
  print(h)
  "
  ```
  Expected to match `52adb4e5...` if feed code is unchanged.

- [ ] **A2** Verify runtime commit is still `94b9293`:
  ```
  git rev-parse HEAD  # must be cc29e8a on external branch
  git rev-parse gpt/fresh-6h-v4r1-failure-fix-20260922  # must be 94b9293
  ```

- [ ] **A3** Verify tree:
  ```
  git rev-parse gpt/fresh-6h-v4r1-failure-fix-20260922^{tree}  # must be 005d75f...
  ```

- [ ] **A4** Run local gate (no AWS):
  ```
  .venv/bin/python -m pytest tests/ -q --tb=short
  ```
  Must pass 1671+ tests, 0 failures.

- [ ] **A5** Pyright clean on changed files:
  ```
  .venv/bin/pyright src/
  ```

- [ ] **A6** Prepare `infra/aws/seals/aws-validation-fresh-30h-{DATE}-v3.timing-contract.json`  
  (copy v4 template, update epoch, adjust window to 108000).

- [ ] **A7** Prepare `infra/aws/seals/aws-validation-fresh-30h-{DATE}-v3.feed-universe.json`  
  (copy v4, update epoch, recompute fingerprint from A1).

- [ ] **A8** Prepare `infra/aws/seals/aws-validation-fresh-30h-{DATE}-v3.heartbeat-contract.json`  
  (copy v4: probe 10s, timeout 10s, max gap 30s).

- [ ] **A9** Prepare launch-command.json template (epoch/run-id left as `{TO_BE_FILLED}`).

- [ ] **A10** Verify `test-results/.last-run.json` is unchanged from `Sep 8 14:33:39 2026, 45 bytes`.

---

## T1 / T2 Observer Plan

```
T1 = First qualifying cohort receipt appears in S3 (expected ~qual_start + 1h + finalization_time)
T2 = All 30 qualifying cohort receipts appear (expected ~qual_end + finalization_time)
```

### T1 Observer Actions
1. Pull first archive receipt from S3 (read-only).
2. Verify receipt epoch matches sealed epoch.
3. Verify receipt SHA-256 is immutable (record hash).
4. Verify no gaps in first cohort (76/76 slots PASS).
5. If any BITHUMB_CONFLICTING_DUPLICATE appears: **stop and escalate immediately** (v4r1 regression).
6. If any COLLECTION_GAP on Bithumb: check redundancy state (v4r1 root cause 3 — union-aware finalizer must handle).
7. Record T1 observation to `evidence/fresh-30h-v3-{EPOCH}/t1-observation.json`.

### T2 Observer Actions
1. Pull all 30 receipts from S3.
2. Verify 30 × 76 = 2 280 slots all PASS.
3. Verify no receipt epoch mismatch.
4. Record T2 observation to `evidence/fresh-30h-v3-{EPOCH}/t2-observation.json`.
5. Run terminal audit script.
6. Seal `terminal-audit.json` and SHA-256.

---

## Terminal Audit Contract

Must record at minimum:
- `schema_version`
- `audit_observed_at_utc`
- `official_verdict` (PASS / FAIL)
- `go_no_go` (GO / NO_GO)
- `identity` (epoch, run_id, runtime_commit, runtime_tree)
- `launch` (launch_number, t0_utc, consumed)
- `terminal` (ended_at_utc, full_duration_satisfied, service_result, nrestarts, receipt SHA-256s)
- `qualifying_cohorts` (expected 30, receipts 30, cohort_pass 30, cohort_fail 0)
- `root_causes` (any new issues)
- `protected_state` (alpha, paper, live, private_api, aws_launches_used)

---

## PASS Gates

All must be satisfied before declaring Fresh 30H-v3 PASS:

| Gate | Requirement |
|---|---|
| G1 | `full_duration_satisfied = true` |
| G2 | `qualifying_cohorts.cohort_pass = 30` |
| G3 | `qualifying_cohorts.cohort_fail = 0` |
| G4 | `terminal.nrestarts = 0` |
| G5 | `terminal.service_result = "success"` |
| G6 | `terminal.exit_status = 0` |
| G7 | All receipts: `t1_t2_immutable = true` |
| G8 | `BITHUMB_CONFLICTING_DUPLICATE` failures = 0 (v4r1 regression check) |
| G9 | `SINGLE_SESSION_FINALIZER_GAP_CLASSIFICATION` gaps = 0 (v4r1 root cause 3 check) |
| G10 | `terminal_receipt_epoch_match = true` (v4r1 launch-control finding fix) |
| G11 | `terminal_receipt_s3_uploaded = true` (v4r1 launch-control finding fix) |

> Gates G8–G11 are specific regressions from the v4r1 failure. Failure on any of these requires immediate NO_GO and root cause analysis before any further launch.

---

## Post-6H Authorization Steps (after Fresh 6H-v5r1 PASS)

Execute in order, no skipping:

1. **Read and record** v5r1 terminal audit from `evidence/...v5r1/terminal-audit.json`.
2. **Verify** all v5r1 PASS gates are met (especially G8, G9, G10, G11 above applied to 6H).
3. **Run** full local test gate: `pytest` must pass, pyright clean.
4. **Verify** launch budget: current consumed count vs authorized ceiling.
5. **Freeze** v3 identity templates (instantiate epoch/run-id from actual T0 timestamp).
6. **Seal** artifacts A6–A9 with final values.
7. **Human authorization:** explicit written GO for Fresh 30H-v3.
8. **Launch** (T0 = moment of authorization, not before).

---

## Unresolved Root Cause from v4r1

> [!WARNING]
> `SINGLE_SESSION_FINALIZER_GAP_CLASSIFICATION` was confirmed in the v4r1 audit but is **NOT resolved** in runtime candidate `94b9293`.  
> The union-aware finalizer fix is deferred to post-30H remediation.  
> This means if a Bithumb primary reconnect occurs during 30H-v3, the secondary session may preserve union continuity but the gap will still be classified as `COLLECTION_GAP`, potentially causing slot failures.  
> **Monitor T1 closely for this pattern.** If observed, consider NO_GO.

---

## Scientific State (immutable for this document)

```
ALPHA        = UNPROVEN
PAPER        = NOT_STARTED
LIVE         = DISABLED
PRIVATE_API  = DISABLED
T0           = UNBOUND
```
