# AWS 45-Minute Short-Smoke Failure Remediation — 2026-09-04

## 1. Executive Summary

- Source Remediation: **PASS**
- New Runtime Candidate: **READY**
- 45-Minute Retry: **NOT STARTED**
- 120-Minute Validation: **NOT STARTED**
- 72-Hour Soak: **NOT STARTED**
- Alpha Research: **BLOCKED**
- Paper Trading: **NOT STARTED**
- Live Trading: **DISABLED**

No market collector, WebSocket subscription, CloudWatch metric loop, archive worker, S3 write, raw cleanup, Terraform apply, IAM widening, security group change, or EBS modification was performed.

---

## 2. Failure Investigation & Root Cause Analysis

### A. Failure #1 — Binance WebSocket Handshake Timeout

- **Symptom:** During the 2026-09-03 short-smoke, Bithumb and Upbit connected immediately, while all Binance connections timed out with 0 messages, 18 reconnects, and 19 disconnects.
- **Diagnostic Isolation on AWS Guest:**
  - DNS resolution: **PASS** (resolves multiple valid IPv4 stream addresses).
  - Proxy environment: empty.
  - TCP connect to `stream.binance.com:9443`: **TIMEOUT** (~10s per candidate).
  - TLS handshake: **NOT RUN** (blocked at TCP layer).
  - WebSocket upgrade: **FAILED** (opening handshake timeout).
- **Official Documentation & Port 443 Probe:**
  - Binance Spot WebSocket API officially supports both port `9443` and port `443` on `stream.binance.com`.
  - Probe to `stream.binance.com:443` on AWS guest:
    - DNS IPv4: **PASS** (7 candidates).
    - TCP connect: **7/7 PASS** (~33–35ms).
    - TLS 1.3 handshake: **7/7 PASS** (~43–47ms).
    - 4/4 symbols (`BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`) across `auto` and `direct` modes: **8/8 PASS** (~111–123ms).
    - Production combined stream across `auto` and `direct` modes: **2/2 PASS** (~111–120ms).
- **CLI Parser Default Bug & TDD Fix:**
  - Core diagnostic defaulted to `BINANCE_PORT` (`443`), but `scripts/diagnose_binance_websocket.py` hardcoded `default=9443`.
  - When run on AWS without CLI arguments, it attempted `:9443` and failed.
  - TDD Fix: Added unit tests ensuring single source of truth across `cross_market_collector.BINANCE_WS_URL`, `binance_diagnostic.BINANCE_PORT`, and CLI parser default. Removed literal duplicate `9443`.
  - Retested default CLI locally: **PASS** (10/10 WebSocket attempts PASS on port 443).
  - Retested default CLI on AWS EC2 guest via SSM: **PASS** (10/10 WebSocket attempts PASS on port 443).

### B. Failure #2 — Partition Active Set Tracking

- **Symptom:** After UTC hour rotation (e.g., H -> H+1), previous hour files remained in `active_partition_files`.
- **Root Cause:** `_active_partition_files` was an append-only set tracking every file ever touched, rather than the active set of files currently open for writes.
- **Fix & Invariants:**
  - Injected UTC clock (`utc_now`) for deterministic time progression.
  - Active partition tracking derived from current UTC hour and latest per-feed path.
  - Invariant verified:
    - Files written in hour H are active in hour H.
    - Upon transition to H+1, hour H files become inactive immediately.
    - Idle feeds from earlier hours do not linger in current active set.
    - Post-drain shutdown: `active_partition_files == []`.
    - Archive guard: closed previous hour partitions are eligible; current active partition is strictly excluded.

### C. Failure #3 — SSM Session Disconnect & Process Lifecycle

- **Symptom:** Collector was tied to foreground Session Manager wrapper. SSM inactivity timeout killed the operator shell and terminated the collector prematurely (~20 min vs 45 min planned). SIGINT was also not propagated properly.
- **Approved Solution:** Single Python bounded supervisor + detached transient systemd execution.
- **Architecture:**
  - Detached transient unit: `systemd-run --unit=bitcoin-trader-short-smoke-<run_id>.service --no-block --collect --service-type=exec --uid=bitcoin-trader --setenv=PYTHONPATH=src --property=Restart=no --property=KillMode=mixed --property=RuntimeMaxSec=2760s --property=TimeoutStopSec=55s`.
  - Authoritative supervisor: `BoundedSupervisor` owns collector and optional publisher subprocesses, propagates signals via process group, enforces monotonic duration, validates live/final metrics, verifies final manifest flush, and persists atomic `result.json`.
  - AWS Mini-Smoke Verification:
    - Executed harmless non-market child fixture (`lifecycle_child.py`) supervised by `run_bounded_short_smoke.py` via transient systemd unit.
    - SSM session was closed immediately after launch.
    - Supervisor and child ran to natural bounded completion (10s limit).
    - Reconnected via fresh SSM session: verified unit deactivated cleanly, `mini_result.json` recorded `overall_status: PASS`, `collector_exit_code: 0`, `publisher_started: true`, `publisher_stopped_after_collector: true`, `final_manifest_flush_observed: true`, `final_metrics_valid: true`.

---

## 3. Test & Validation Evidence

- **Full Test Suite:** **546 / 546 PASS** (`uv run python -m unittest discover -s tests`, ~27.7s).
- **Compileall:** **PASS** (`python -m compileall -q src scripts tests`).
- **Pip Check:** **PASS** (`python -m pip check` -> "No broken requirements found").
- **Sensitive Test Repeat:** **5 / 5 consecutive PASS** on 59 process/lifecycle/partition/archive tests with zero flakiness.

---

## 4. AWS Security & Infrastructure Posture

- **Caller Identity:**
  - Bootstrap: `arn:aws:iam::080109295433:user/bitcoin-trader-bootstrap` (validated via STS).
  - Provisioner assumed role: `arn:aws:sts::080109295433:assumed-role/bitcoin-trader-terraform-provisioner/codex-preapply-validation` (validated via STS).
- **Static Credentials:** **NONE** (no access keys created or exported).
- **SSM Access:** Interactive Session Manager only; SendCommand remains DENY.
- **Terraform Apply:** **NOT RUN**.
- **IAM / Boundary:** **NOT CHANGED**.
- **Security Group:** **NOT CHANGED** (Ingress: 0, SSH: None).
- **EBS Storage:** **NOT CHANGED** (100 GiB gp3).

---

## 5. New Sealed Runtime Candidate

Failed epoch and run ID are preserved for evidence and never reused.

- **Previous Failed Epoch:** `aws-short-smoke-20260902-38cb8a72` (PRESERVED)
- **Previous Failed Run ID:** `aws-short-smoke-run-20260902T145020Z-38cb8a72` (PRESERVED)
- **Previous Failed Config Fingerprint:** `f99543c669496ff97c950445e932a902bff11b03cdd275d9f56d39d212419f67` (PRESERVED)

### New Candidate Provenance:
- **Runtime Code Commit:** `5013728aacffcdc1b6faa84c19d8144287e25cde`
- **Runtime Seal File:** `infra/aws/seals/aws-short-smoke-20260904.runtime.json`
- **Config Fingerprint:** `c9ed3b2fecce2c4a54497269f9ee7a54ca8f94b81a5ae40ba4592a66abdc4211`
- **New Epoch:** `aws-short-smoke-20260904-f5257d24`
- **New Run ID:** `aws-short-smoke-run-20260904T005600Z-f5257d24`

---

## 6. Future IAM Epoch Diff (Preparation Only — Not Applied)

When approved for reseal, the temporary/canonical archive prefix substitution will be:

```diff
--- collector-epoch-access (current)
+++ collector-epoch-access (future plan)
@@ -10,8 +10,8 @@
       "s3:prefix": [
-        "market-data/canonical/aws-short-smoke-20260902-38cb8a72/*",
-        "market-data/temporary/aws-short-smoke-20260902-38cb8a72/*"
+        "market-data/canonical/aws-short-smoke-20260904-f5257d24/*",
+        "market-data/temporary/aws-short-smoke-20260904-f5257d24/*"
       ]
@@ -25,8 +25,8 @@
     "resources": [
-      "arn:aws:s3:::bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433/market-data/canonical/aws-short-smoke-20260902-38cb8a72/*",
-      "arn:aws:s3:::bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433/market-data/temporary/aws-short-smoke-20260902-38cb8a72/*"
+      "arn:aws:s3:::bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433/market-data/canonical/aws-short-smoke-20260904-f5257d24/*",
+      "arn:aws:s3:::bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433/market-data/temporary/aws-short-smoke-20260904-f5257d24/*"
     ]
```

- Action widening: **NONE**
- DeleteObject: **DENY**
- Application Status: **NOT APPLIED** (plan-review only)

---

## 7. Gate Decision

- **BINANCE REMEDIATION:** **PASS**
- **PARTITION REMEDIATION:** **PASS**
- **PROCESS REMEDIATION:** **PASS**
- **SOURCE REMEDIATION:** **PASS**
- **NEW RUNTIME CANDIDATE:** **READY**
- **45M RETRY:** **NOT STARTED**
