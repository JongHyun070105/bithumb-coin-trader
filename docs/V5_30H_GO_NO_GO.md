# V5 30-Hour Go/No-Go Verdict

- **Decision Date**: 2026-09-17T08:00:00Z
- **Decision Authority**: Autonomous Collector Reliability & Observability Engineering Director (MiMo)
- **Software SHA**: (pending commit on develop)
- **Branch**: `develop`

---

## Verdict: NO_GO (AWS Deployment Blocked — SSM Access Required)

### Reason

All local engineering gaps are closed. However, the EC2 instance is accessible **only via SSM Session Manager** (SSH disabled, no ingress). Both `ssm:StartSession` and `ssm:SendCommand` are denied for the available IAM profiles. Code deployment to EC2 requires human-assisted SSM session.

### Minimum Human Action

Open an interactive SSM session to `i-008bc503c1136349f` (ap-northeast-2) via AWS Console or an authorized IAM principal, then execute the deployment commands documented below.

**No IAM policy changes are required** if the human uses an already-authorized principal (e.g., the root account or an admin role with SSM access).

---

## Local Engineering Gate Status (Corrected Semantics)

| Gate | Status | Classification |
|------|--------|----------------|
| local_engineering_complete | **PASS** | All components implemented, tested, type-checked |
| local_failure_injection_pass | **PASS** | 15/15 failure injection scenarios |
| accelerated_boundary_test_pass | **PASS** | 13/13 boundary tests (3 hour transitions, day rollover) |
| observer_local_isolation_verified | **PASS** | Non-intervention, S3 fail-safe, self-observation |
| terminal_witness_local_verified | **PASS** | Classification, receipt persistence, parent dir fsync, ExecStopPost wiring |
| canary_local_verified | **PASS** | 76-feed contract, VERIFIED_ZERO, V4 detection, non-mutation |
| canary_binding_verified | **PASS** | SHA-256 cross-reference, receipt binding, DATA_PRESENT_WITHOUT_BINDING detection |
| archiver_instrumentation_verified | **PASS** | Scheduler→sidecar→observer bridge, all 8 fields populated |
| watchdog_local_configured | **PASS** | Type=notify, WatchdogSec=60s, NotifyAccess=main, READY=1 |
| aws_runtime_observer_verified | **UNVERIFIED** | Requires 90m soak |
| aws_runtime_terminal_witness_verified | **UNVERIFIED** | Requires 90m soak |
| aws_runtime_canary_verified | **UNVERIFIED** | Requires 90m soak |
| aws_90m_soak | **NOT_RUN** | Blocked by SSM access |
| aws_3h_soak | **NOT_RUN** | Depends on 90m |
| aws_6h_soak | **NOT_RUN** | Depends on 3h |
| deployment_path | **BLOCKED_SSM_ACCESS** | SSM StartSession + SendCommand denied |
| v5_30h_go_no_go | **NOT_DECIDED** | Requires AWS soak evidence |

---

## Test Results

| Metric | Value |
|--------|-------|
| Full test suite | 1498 pass, 0 fail, 2 skip |
| Pyright | 0 errors, 0 warnings |
| compileall | clean |
| git diff --check | clean |
| Secret scan | clean |

---

## What Changed This Session

1. **ArchiverHealth**: Was a ghost dataclass (all defaults). Now populated by archive scheduler via sidecar health file, read by observer.
2. **Canary binding**: Was existence-only. Now verifies SHA-256 checksums between coverage claims and RAW files, cross-references receipt checksums.
3. **Terminal witness fsync**: Parent directory fsync added to write_receipt_atomic.
4. **systemd integration**: WatchdogSec=60s, Type=notify, NotifyAccess=main, ExecStopPost added to render_systemd_run.
5. **Gate semantics**: Separated LOCAL_VERIFIED from AWS_UNVERIFIED to prevent overclaiming.

---

## V4 Replay Re-Assessment

With all local fixes applied, if V4 happened:
- **Detection**: Observer marks COLLECTOR=STALE within ~45 seconds
- **Termination**: systemd WatchdogSec=60s kills stalled process (when unit is configured with Type=notify)
- **Evidence**: Terminal witness captures exit via ExecStopPost
- **Audit**: Canary detects coverage-without-raw and checksum mismatches
- **Archiver**: Scheduler health sidecar shows archive progress or stalling

**Gap**: Watchdog termination requires the systemd unit to be launched with the new `render_systemd_run()` configuration. Old unit templates without WatchdogSec will not benefit.

---

## BLOCKED_DEPLOYMENT_AUTHORIZATION

### Existing Paths Investigated

| Path | Exists | Authorized | Notes |
|------|--------|-----------|-------|
| SSM StartSession (interactive) | YES | **NO** — AccessDenied for provisioner role | Primary deployment method per all docs |
| SSM SendCommand | YES | **NO** — AccessDenied for provisioner role | Would enable non-interactive deployment |
| SSH | NO | N/A | Security group has zero ingress rules; deliberately disabled |
| GitHub Actions CI/CD | NO | N/A | No .github/workflows/ directory exists |
| CodeDeploy | NO | N/A | Not configured |
| S3 PutObject (staging) | YES | **NO** — Explicit deny for provisioner role | Could stage artifacts for EC2 pull |
| EC2 git pull from remote | YES | **UNKNOWN** — EC2 may have GitHub access | Could work if EC2 has git credentials |

### Minimum Required Capability

**Single capability**: `ssm:StartSession` for the provisioner role on `i-008bc503c1136349f`

This enables interactive SSM sessions (the established deployment method). No SendCommand, no S3 PutObject, no IAM broadening beyond this one action.

### Alternative: EC2 Git Pull

If the EC2 instance has network access to GitHub and git credentials, deployment could work by:
1. Push code to `develop` branch (done)
2. Human opens SSM session (Console or authorized principal)
3. On EC2: `cd /var/lib/bitcoin-trader && git fetch origin && git worktree add ...`

This avoids any new IAM permissions if the human uses the AWS Console's "Connect → Session Manager" feature (which uses the EC2 instance role, not the provisioner role).

---

## Next Single Best Action

**Human opens an SSM session to `i-008bc503c1136349f` via AWS Console** (Connect → Session Manager) and executes the deployment sequence. The AWS Console uses the EC2 instance's own IAM role for SSM, which is already authorized.
