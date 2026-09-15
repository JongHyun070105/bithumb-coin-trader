# AWS 30H V3 Official Validation Preparation Design Specification

- **Date:** 2026-09-15
- **Status:** APPROVED FOR PREPARATION (Execution/Launch strictly pending human authorization)
- **Target Epoch Model:** V3 Strictly-Next Full-Hour Qualification (30 cohorts × 76 feeds = 2,280 slots)
- **Worktree:** `codex/aws-30h-v3-preparation-20260915-e9d5d5a`
- **Base Commit:** `e9d5d5a91f2505603291419e8c3d5a1e448df205`

---

## 1. Executive Summary & Purpose

Following the successful completion and regular merge of AWS 30H V3 Remediation (PR #6, commit `e9d5d5a`), this document establishes the formal preparation protocol for the new official AWS 30H V3 validation run.

The primary objective is to complete all pre-flight inspections, cryptographic seals, contract parameter derivations, identity allocations, and configuration validations up to:

```text
READY TO AUTHORIZE NEW 30H: YES
LAUNCH AUTHORIZED: FALSE
NEW 30H STARTED: NO
```

No collector, supervisor, or systemd process shall be initiated during this preparation phase.

---

## 2. Immutable Scientific Invariants

1. **Failure State Preservation:**
   - V2 30H run (`aws-validation-30h-20260912-6576f63`) remains permanently `FAIL — IMMUTABLE`.
   - V2 RAW data (21.05 GiB) and compressed data (0.52 GiB) are classified as `KEEP_RESEARCH` and preserved indefinitely.
   - Historical 72H soak data (`aws-72h-soak-20260905-8017b83e`, 66.12 GiB RAW) is classified as `KEEP_RESEARCH` (governed by microstructure preregistration `CYCLE-01`).
   - All manifests, receipts, logs, and failure artifacts across all epochs are classified as `KEEP_EVIDENCE`.
   - Total files deleted = 0, total bytes deleted = 0, total S3 objects deleted = 0.
2. **Exact Coverage Slot Math:**
   - Sealed Feed Universe: exactly 76 feeds.
   - Qualifying Candidate Hours: exactly 30 full UTC hours.
   - Total Required Coverage Slots: $76 \times 30 = 2,280$ slots.
   - Slot Status Allowed: `DATA_PRESENT`, `VERIFIED_ZERO_EVENT`, `FAILED`.
   - Scientific Pass Condition:
     $$\text{DATA\_PRESENT} + \text{VERIFIED\_ZERO\_EVENT} = 76 \quad (\forall \text{ candidate hour } h \in [1, 30])$$
     $$\text{FAILED} = 0, \quad \text{MISSING} = 0, \quad \text{DUPLICATE} = 0, \quad \text{FOREIGN} = 0$$
   - Zero-event evidence must NOT create zero-byte or synthetic RAW market files.
3. **Timing & Qualification Schedule:**
   - $\text{qualification\_start\_utc} = \text{strictly\_next\_utc\_hour}(\text{actual\_start\_utc})$.
   - Even if actual start occurs at an exact integer hour second (`HH:00:00Z`), qualification starts at `(HH+1):00:00Z`.
   - Candidate cohort count is fixed at 30. No shifting of qualification start, no 31st replacement hour.
   - Maximum collection window: $111,600$ seconds (31 hours).
4. **Monotonic Deadline:**
   - Single conversion: $\text{monotonic\_deadline} = \text{start\_monotonic} + (\text{stop\_utc} - \text{start\_utc})$.
   - NTP or wall-clock adjustments never alter the monotonic deadline.
5. **Session & Liveness Thresholds:**
   - Heartbeat probe interval: 10 seconds.
   - Heartbeat timeout: 10 seconds.
   - Maximum accepted liveness gap: 30 seconds ($>30\text{s} \implies \text{FAIL}$).
   - WebSocket reconnects create a new immutable session identity; subscription proofs cannot be reused across connections.
6. **Finalization Invariants & Ordering:**
   - $\text{historical\_raw\_files\_opened} = 0$ and $\text{historical\_raw\_bytes_read} = 0$.
   - Claim discipline: `bounded dirty-tail behavior demonstrated` (no ungrounded asymptotic $O(1)$ claim).
   - Ordering for `DATA_PRESENT`:
     $$\text{RAW persisted} \to \text{RAW manifest} \to \text{RAW archive} \to \text{RAW receipt} \to \text{RAW restore verify} \to \text{count/hash check} \to \text{coverage artifact} \to \text{coverage archive} \to \text{coverage receipt} \to \text{coverage restore verify}$$
   - Ordering for `VERIFIED_ZERO_EVENT`:
     $$\text{observation proof} \to \text{coverage evidence} \to \text{coverage archive} \to \text{coverage receipt} \to \text{coverage restore verify}$$

---

## 3. Feed Universe Specification (76 Feeds)

The canonical feed universe is derived deterministically from the public exchange configurations:

1. **Bithumb (60 Feeds):**
   - 20 KRW Markets: BTC, ETH, XRP, SOL, DOGE, ADA, XLM, LINK, AVAX, BCH, ETC, NEAR, SUI, APT, TRX, SHIB, SAND, MANA, AXS, DOT.
   - 3 Feed Types per Market: `orderbook`, `trade`, `ticker`.
   - Total: $20 \times 3 = 60$ feeds.
2. **Binance Spot (8 Feeds):**
   - 4 USDT Symbols: `btcusdt`, `ethusdt`, `solusdt`, `xrpusdt`.
   - 2 Feed Types per Symbol: `orderbook`, `trade`.
   - Total: $4 \times 2 = 8$ feeds.
3. **Upbit (8 Feeds):**
   - 4 KRW Markets: `KRW-BTC`, `KRW-ETH`, `KRW-SOL`, `KRW-XRP`.
   - 2 Feed Types per Market: `orderbook`, `trade`.
   - Total: $4 \times 2 = 8$ feeds.

**Total Universe:** $60 + 8 + 8 = 76$ feeds.
Deterministically sorted and sealed via `canonical_sha256`.

---

## 4. Timeout Architecture & Scientific Justification

In V2, the timeout relationship was broken: fixed 108,000s duration with 108,300s supervisor ceiling and 108,400s systemd ceiling, leaving zero margin for cross-hour qualification and finalization.

In V3, the timeouts are mathematically grounded in measured benchmarks:

1. **Measured Performance (from Task 9 Benchmark):**
   - Dirty-tail finalization for 1 cohort (76 slots): $p_{50} \approx 5.5\text{ ms}, p_{95} < 15\text{ ms}$.
   - Single cohort full-scan verification: $\sim 2\text{--}5\text{ s}$.
   - S3 zstd compression, upload, and restore verification: $\sim 15\text{--}30\text{ s}$.
   - Worst-case expected dirty-tail finalization under load: $< 60\text{ s}$.
2. **Timeout Parameters:**
   - `finalization_timeout_seconds`: **180 seconds** (3x margin over worst-case).
   - `shutdown_grace_seconds`: **45 seconds** (grace period for queue flushing).
   - Maximum Collection Window: **111,600 seconds** (31 hours).
   - `supervisor_hard_ceiling_seconds`:
     $$111,600\text{s} + 180\text{s} + 45\text{s} = \mathbf{111,825\text{ seconds}}$$
   - `systemd_runtime_max_seconds`:
     $$111,825\text{s} + 75\text{s} \text{ (outer safety margin)} = \mathbf{111,900\text{ seconds}}$$

---

## 5. Official V3 Identity Allocation

The official V3 run identity must be globally unique and separated from all prior runs:

- **Collector Epoch:** `aws-validation-30h-20260915-v3`
- **Collector Run ID:** `aws-validation-30h-run-20260915T013000Z-v3`
- **S3 Archive Prefix:** `market-data/temporary/aws-validation-30h-20260915-v3`
- **Guest Runtime Worktree:** `/var/lib/bitcoin-trader/runtime-worktrees/aws-validation-30h-20260915-v3`
- **Guest Local Runtime Root:** `/var/lib/bitcoin-trader/30h-validation/aws-validation-30h-20260915-v3`
- **Systemd Unit Name:** `bitcoin-trader-30h-aws-validation-30h-run-20260915T013000Z-v3.service`
- **Authorization State:** `launch_authorized = false`, `actual_start_time_utc = null`.

---

## 6. Preflight Gates & Acceptance Criteria

| Gate | Requirement | Method |
|---|---|---|
| **EC2 Identity** | `i-008bc503c1136349f`, t3.medium, ap-northeast-2a, running | AWS EC2 describe API & SSM |
| **Storage Gate** | Available free disk space $\ge 50.0\text{ GiB}$ | `statvfs` / `df -h` on `/` |
| **Process State** | 0 collectors, 0 supervisors, 0 archive schedulers | `pgrep` / `ps aux` on EC2 |
| **IAM Safety** | Boundary `v6`, public data only, no trading APIs | IAM API read-only |
| **Terraform Plan** | 0 to add, 0 to change, 0 to destroy | Read-only Terraform plan |
| **S3 Namespace** | `KeyCount == 0` for new official prefix | S3 ListObjectsV2 |
| **Static Analysis** | Pyright 0 errors, compileall PASS, diff check CLEAN | Local venv checks |
| **Reviews** | Critical 0, Important 0 | Independent Subagent Review |
