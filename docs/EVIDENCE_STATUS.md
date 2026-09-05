# Research Evidence & Artifact Status Inventory

**Document Version:** 1.0.0  
**Effective Date:** 2026-09-05  
**Authoritative Base Commit:** `3c33818c3627760f1eee61b551c4ab740891c9ac`  
**Research Sprint Branch:** `codex/72h-offline-research-hardening-20260905`

---

## 1. Classification Taxonomy

All repository artifacts are partitioned into three immutable epistemological tiers:

1. **`FROZEN EVIDENCE`**: Historical ledgers, acceptance criteria, and pre-freeze manifests that are cryptographically sealed and must NEVER be modified.
2. **`ACTIVE RESEARCH TOOLING`**: Validated simulators, audit scripts, unit tests, and governance tools hardened during the offline sprint.
3. **`EXTERNAL / SEALED EXPERIMENT`**: The live AWS 72-Hour Soak (`aws-72h-soak-20260905-8017b83e`), completely isolated and untouched.

---

## 2. Inventory of Frozen Evidence

| Artifact Path | SHA-256 Hash | Status | Description |
| :--- | :--- | :--- | :--- |
| `docs/72H_ACCEPTANCE_CRITERIA_2026-09-05.md` | `7ecfa609b4b142112c32843503ca055504d746910c94fa0baa32e9d46c260cac` | **FROZEN** | Pre-freeze acceptance criteria sealed prior to soak completion |
| `research/preregistration/microstructure_v1.json` | `d61a2e8d8f5bf18940a4027d6fb8cf58cf745f72f0cd30e7cb1eca12f6e590f3` | **FROZEN** | Pre-registered Cycle 1 feature families, budgets ($N \le 9$), and hurdles |
| `evidence/research/trial_ledger_frozen_20260905.jsonl` | `4b019ef6112b229c150fc25b23f5e264ff1171116fd39baaa4662c8deed5f90e` | **FROZEN** | Sanitized historical research ledger (77 trials, N=77 baseline) |
| `evidence/research/trial_ledger_frozen_20260905.manifest.json` | Verified Match | **FROZEN** | Cryptographic metadata manifest for the frozen trial ledger |

---

## 3. Inventory of Active Research Tooling

| Component | Path | Verification Suite | Status |
| :--- | :--- | :--- | :--- |
| **Backtester Oracle** | `src/bithumb_coin_trader/backtest.py` | `tests/test_backtester_oracle.py` | **16/16 PASS** (Oracle Families A~O) |
| **Taker Execution Simulator** | `src/bithumb_coin_trader/execution_simulator.py` | `tests/test_execution_simulator.py` | **15/15 PASS** (Depth, VWAP, Latency) |
| **DSR Sensitivity Auditor** | `scripts/audit_dsr_sensitivity.py` | `tests/test_research_statistics.py` | **6/6 PASS** ($N \in [1, 200]$ spectrum) |
| **Trial Ledger Auditor** | `scripts/audit_trial_ledger_provenance.py` | CLI Execution | **PASS** (77/77 records validated) |
| **V6 Reproducibility Tool** | `scripts/reproduce_v6_statistics.py` | CLI Execution | **PASS** (Exact metrics verified) |
| **Governance Validator** | `scripts/validate_research_governance.py` | `tests/test_research_governance.py` | **5/5 PASS** (Budget & Holdout gate) |
| **Latency Measurement Tool** | `scripts/measure_execution_latency.py` | `tests/test_latency_protocol.py` | **3/3 PASS** (Synthetic CDF & Fail-closed) |
| **Post-Soak Deep Auditor** | `scripts/audit_72h_soak.py` | `tests/test_post_soak_audit_tooling.py` | **5/5 PASS** (76 feeds, timestamps, dups) |
| **Replay & Restore Verifier** | `scripts/verify_soak_reproducibility.py` | `tests/test_72h_synthetic_chaos.py` | **4/4 PASS** (Zstandard bitwise check) |
| **Streaming Benchmark** | `scripts/benchmark_audit_throughput.py` | CLI Execution | **PASS** (>100 MB/s, <30 MB RAM) |

---

## 4. Current Test Suite Status

- Total Automated Tests: **638+ Tests**
- Test Framework: `pytest-8.4.2` (Python 3.14.6)
- Test Outcome: **100% PASS (0 Failures, 0 Errors)**
- Code Coverage Areas: Oracle invariants, causality / lookahead sentinels, order book depth sweep, synthetic chaos, property invariants, statistical monotonicity.
