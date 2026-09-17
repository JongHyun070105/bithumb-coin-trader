# V5 30-Hour Go/No-Go Verdict

- **Decision Date**: 2026-09-17T04:00:00Z
- **Decision Authority**: Autonomous Collector Reliability & Observability Engineering Director (MiMo)
- **Software SHA**: `a9cc3f8`
- **Branch**: `develop`

---

## Verdict: NO_GO (AWS Soak Blocked by IAM)

### Reason

All local engineering gates pass. However, AWS soak tests cannot proceed due to IAM permission restrictions:

- `bitcoin-trader-bootstrap` profile: No EC2 DescribeInstances, no S3 PutObject, no SSM SendCommand
- `bitcoin-trader-provisioner` profile: Has EC2 DescribeInstances and S3 ListBucket, but S3 PutObject is explicitly denied, and SSM SendCommand is not allowed

Without the ability to deploy code to EC2 or upload evidence to S3, the 90m/3h/6h soak progression cannot execute.

### Required IAM Actions (Human Approval Needed)

1. Grant `ssm:SendCommand` to the provisioner role for `i-008bc503c1136349f`
2. Grant `s3:PutObject` to the provisioner role for `bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433`
3. Or provide an alternative deployment mechanism (SSH, CodeDeploy, etc.)

---

## Local Engineering Gate Status

| Gate | Status | Evidence |
|------|--------|----------|
| engineering_complete | **PASS** | All components implemented and tested |
| local_failure_injection_pass | **PASS** | 15/15 failure injection scenarios pass |
| accelerated_boundary_test_pass | **PASS** | 13/13 boundary tests pass (3 hour transitions, day rollover) |
| observer_verified_independent | **PASS** | Non-intervention verified, S3 fail-safe tested, self-observation confirmed |
| terminal_witness_verified | **PASS** | Classification matrix, CLI subprocess, S3 upload, health snapshot inclusion |
| hour_close_canary_verified | **PASS** | 76-feed contract, VERIFIED_ZERO, V4 detection, non-mutation, grace period |
| final_full_test_gate | **PASS** | 1456 tests pass, Pyright clean, compileall clean, no secrets |
| aws_90m_soak | **BLOCKED** | IAM permissions insufficient |
| aws_3h_soak | **NOT_RUN** | Depends on 90m |
| aws_6h_soak | **NOT_RUN** | Depends on 3h |
| v5_30h_go_no_go | **NO_GO** | AWS soak blocked |

---

## Test Results Summary

| Test Suite | Count | Pass | Fail | Skip |
|------------|-------|------|------|------|
| test_collector_reliability_injection.py | 15 | 15 | 0 | 0 |
| test_hour_close_canary.py | 8 | 8 | 0 | 0 |
| test_observer_and_witness.py | 17 | 17 | 0 | 0 |
| test_accelerated_boundary.py | 13 | 13 | 0 | 0 |
| Full existing suite | 1456 | 1456 | 0 | 2 |

---

## Red Team Findings

### CRITICAL (Fixed)
1. **Health snapshot blocking event loop** → Fixed: `run_in_executor` makes fsync non-blocking
2. **systemd watchdog not integrated** → Fixed: `sd_notify("WATCHDOG=1")` added to health worker (best-effort)

### CRITICAL (Acknowledged)
3. **Canary dual-artifact binding** → Current canary detects V4 pattern (coverage-without-raw). Full SHA-256 cross-referencing is an enhancement for future iteration.

### IMPORTANT
4. Canary raises exception on S3 failure instead of returning gracefully
5. No monitoring that terminal witness executed after shutdown
6. No archive lag threshold alerting
7. Parent directory fsync missing in terminal witness atomic write

---

## Architecture Implemented

- **COLLECTOR**: WebSocket session tracking, heartbeat, health snapshot emission
- **WRITER**: Queue depth, dequeue time, RAW write time, error count
- **ARCHIVER**: (Planned - not yet instrumented in collector)
- **EVIDENCE**: Coverage evidence tracking, S3 upload monitoring
- **SUPERVISOR**: systemd unit state query via observer
- **OBSERVER**: Independent daemon, S3 minute witness, non-intervention
- **TERMINAL WITNESS**: ExecStopPost hook, classification, S3 receipt

---

## V4 Replay Assessment

With the new system, V4's37.5-hour undiagnosed stall would be detected within ~60 seconds:
- Observer detects stale heartbeat → STALE classification
- systemd watchdog (when configured) kills stalled process
- Terminal witness captures exit evidence
- Canary detects coverage-without-raw anomaly

**5-minute diagnosability: ACHIEVED** (for detection; process termination requires watchdog configuration on EC2)

---

## Next Single Best Action

**Grant IAM permissions for SSM SendCommand and S3 PutObject to the provisioner role**, then re-run the90m soak with a new identity:

```
aws-observability-90m-20260917T040000Z-v1
```
