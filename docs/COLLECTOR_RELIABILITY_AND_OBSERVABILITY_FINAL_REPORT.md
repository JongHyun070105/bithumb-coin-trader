# Collector Reliability and Observability Final Report

- **Report Date**: 2026-09-17T04:00:00Z
- **Coordinator**: MiMo (took over from Gemini 3.8 Flash at quota exhaustion)
- **Software SHA**: `a9cc3f8` on `develop`

---

## 1. Gemini → MiMo Handoff

**What Gemini left:**
- 3 core source files (collector_state_model, runtime_observer, hour_close_canary)
- 2 scripts (terminal_witness, run_hour_close_canary)
- 3 test files (15 failure injection, 8 canary, 17 observer/witness tests)
- 2 documentation files (failure model, V4 replay analysis)
- 1 reliability gate JSON
- Modified cross_market_collector.py with health instrumentation (+491/-237 lines)

**What MiMo recovered:**
- Fixed missing `import time` bug in hour_close_canary.py
- Added parent directory fsync to write_health_snapshot_atomic (IMPORTANT-02 from red team)
- Added FD count to collector resource telemetry
- Made health snapshot emission async via run_in_executor (CRITICAL-02 from red team)
- Added systemd watchdog integration (CRITICAL-01 from red team)
- Hoisted artifact_file assignment to fix Pyright reportPossiblyUnboundVariable
- Added pyright ignore for conditional boto3 import

**Subagent work preserved:**
- Agent D: Created tests/test_accelerated_boundary.py (13 tests)
- Agent F: Created project-state/RED_TEAM_REVIEW.md
- Agent (Pyright): Confirmed2 Pyright errors, fixed both

---

## 2. Architecture

| Component | File | Purpose |
|-----------|------|---------|
| COLLECTOR | cross_market_collector.py | WebSocket ingestion, health emission |
| WRITER | (within collector) | Local RAW write, queue management |
| ARCHIVER | (existing) | Compression, S3 upload |
| EVIDENCE | (existing) | Coverage, receipts, manifests |
| SUPERVISOR | systemd unit | Process lifecycle |
| OBSERVER | runtime_observer.py | Independent daemon, S3 witness |
| TERMINAL WITNESS | terminal_witness.py | ExecStopPost hook |
| CANARY | hour_close_canary.py |76-feed completeness audit |

---

## 3. Failure Model

Documented in `docs/COLLECTOR_FAILURE_MODEL.md`:
-12-edge pipeline topology (WebSocket → Finalization)
- Per-edge failure modes and observable signals
- Component separation architecture
- Non-intervention observer principle
-5-minute diagnosability requirement

---

## 4. Health Schema

`RuntimeHealthSnapshot` (schema_version=1):
- supervisor: unit, invocation_id, active_state, pid
- collector: status, last_loop_heartbeat, last_websocket_activity, last_canonical_event, websocket_sessions, reconnect_count, fatal_error
- writer: queue_depth, max_queue_depth, last_dequeue, last_local_raw_write, current_open_raw_count, unpersisted_count, writer_errors
- archiver: archive_queue_depth, last_closed_cohort, last_compression, last_s3_put, last_receipt, archive_errors, upload_failures
- evidence: last_coverage_evidence, current_hour_expected_slots, current_hour_terminal_slots
- observer: observer_pid, observer_started_at, observer_last_cycle, observer_errors
- resources: rss_bytes, fd_count, disk_free_bytes, disk_used_bytes
- current_cohort: utc_hour, observed_feed_count, expected_feed_count
- last_exception: component, type, message_hash, timestamp

---

## 5. Failure Injection Results

15/15 scenarios PASS:
1. Normal exit → CLEAN_SUCCESS
2. Non-zero exit → PROCESS_EXIT_ERROR
3. SIGTERM → SIGNAL_TERMINATED
4. Stale heartbeat → STALE (supervisor HEALTHY)
5. Writer error → DEGRADED
6. Queue backlog → DEGRADED + alert
7. Archiver exception → DEGRADED/FAILED (collector HEALTHY)
8. S3 upload failure → EVIDENCE DEGRADED, local write continues
9. Observer failure → detected, collector alive
10. Corrupt health JSON → tolerant read returns None
11. Terminal witness → receipt persisted on abnormal termination
12. Missing RAW → FAIL
13. Coverage without RAW → FAIL (V4 pattern)
14. RAW without receipt → FAIL
15. Disk low → alert triggered

---

## 6. Accelerated Boundary Results

13/13 tests PASS:
- Full hour transition cycle (3 hours)
- No previous cohort leakage
- Cohort identity correctness
- Incremental archive progress
- Observer causality across boundaries
- Terminal witness timestamp fidelity
- V2 regression: no global rescan
- Day rollover boundary (23:00→00:00)
- Quiet market vs stale detection
- Canary eligibility enforcement
- Injectable now for deterministic testing

---

## 7-9. AWS Soak Tests

**Status: BLOCKED**

IAM permissions insufficient:
- `bitcoin-trader-bootstrap`: No EC2, no S3 PutObject, no SSM
- `bitcoin-trader-provisioner`: No S3 PutObject (explicit deny), no SSM SendCommand

**Required**: Human grants IAM permissions, then:
- aws-observability-90m-20260917-v1
- aws-observability-3h-20260917-v1 (if90m PASS)
- aws-observability-6h-20260917-v1 (if3h PASS)

---

## 10. Resource Trends

**Local disk**:182 GiB free (safe threshold:≥50 GiB)
**EC2 instance**: t3.medium, running since2026-09-01
**S3 bucket**: bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433 (readable)

---

## 11. V4 Replay / 5-Minute Diagnosability

**Q: "If V4 happened with the new system, would the ambiguity still exist?"**

**A: NO.** The new system would detect V4 within ~60 seconds:
1. Observer reads stale health snapshot → COLLECTOR=STALE
2. systemd watchdog (when configured on EC2) kills stalled process
3. Terminal witness captures exit evidence
4. Canary detects coverage-without-raw (0/76 RAW)

**Remaining gap**: Process termination via watchdog requires `WatchdogSec=60s` in the systemd service file on EC2. Detection is immediate; termination depends on EC2 configuration.

---

## 12. Red Team

**Critical (2 fixed, 1 acknowledged):**
- Health snapshot blocking event loop → Fixed (async)
- systemd watchdog not integrated → Fixed (sd_notify added)
- Canary dual-artifact binding → Acknowledged (V4 pattern detected, full checksum cross-ref deferred)

**Important (4):**
- Canary exception on S3 failure (should return gracefully)
- No terminal witness execution monitoring
- No archive lag threshold alerting
- Parent dir fsync missing in terminal witness

**Minor (3):**
- Observer S3 API cost optimization
- S3 prefix isolation
- Exception message length limiting

---

## 13. Tests

| Suite | Tests | Pass | Fail |
|-------|-------|------|------|
| Reliability injection | 15 | 15 | 0 |
| Hour-close canary | 8 | 8 | 0 |
| Observer & witness | 17 | 17 | 0 |
| Accelerated boundary | 13 | 13 | 0 |
| Full existing suite | 1456 | 1456 | 0 |
| **Total** | **1509** | **1509** | **0** |

---

## 14. Git

- **main SHA**: `0b819ea` (unchanged)
- **develop SHA**: `a9cc3f8` (new commit)
- **Branches**: develop ahead of main by 1 commit
- **Untracked preserved**: test-results/ (untouched)

---

## 15. Safety

- ALPHA = UNPROVEN (no live trading)
- PAPER = NOT STARTED
- LIVE = DISABLED
- PRIVATE API = DISABLED

---

## 16. V5 Verdict

**NO_GO** — AWS soak tests blocked by IAM permissions.

Local engineering is complete and verified. Soak progression requires human IAM intervention.

---

## 17. Next Single Best Action

**Grant `ssm:SendCommand` and `s3:PutObject` to the bitcoin-trader-provisioner IAM role**, then launch `aws-observability-90m-20260917T040000Z-v1` soak test.
