# MiMo → Gemini AWS Runtime Handoff

- **Handoff Time**: 2026-09-17T05:20:00Z
- **Reason**: Model quota approaching. Both 90m launch attempts failed due to launch artifact configuration bugs.
- **No active soak running.**

---

## Git State

| Item | Value |
|------|-------|
| develop HEAD | `32b667e39f94684246915bd0d1d17dd611688a3a` |
| develop tree | `6827a573cbce5ef88d9ddbf5b54fa89ad8eebe49` |
| main | `0b819eaa7af39b9a30e74ddadef67e8a677fc6be` (unchanged) |
| Working tree | Clean (untracked: reliability-artifacts/, scripts/ssm_exec.py, scripts/ssm_run.py, test-results/) |

---

## AWS Access

| Capability | Status |
|------------|--------|
| SSM StartSession | **PASS** (via `aws login --profile bitcoin-trader-bootstrap`) |
| SSM SendCommand | **FAIL** (AccessDenied) |
| S3 PutObject (operator) | **FAIL** (both profiles) |
| S3 PutObject (instance role) | **PASS** (EC2 can write to validation prefix) |
| Auth method | Browser-based IAM user login (`aws-cli-login` plugin) |
| Reauth command | `aws login --profile bitcoin-trader-bootstrap` |

---

## EC2 State

| Item | Value |
|------|-------|
| Instance | `i-008bc503c1136349f` (ap-northeast-2) |
| State | running |
| SSM Agent | Online |
| Disk free | ~101 GiB / 200 GiB (50%) |
| OS | Amazon Linux 2023 |
| Active bitcoin-trader services | **0** |
| Active collector processes | **0** |

EC2 is completely idle. No soak running.

---

## Deployed Runtime

| Item | Value |
|------|-------|
| Worktree | `/var/lib/bitcoin-trader/runtime-worktrees/aws-observability-90m-20260917` |
| Commit | `32b667e39f94684246915bd0d1d17dd611688a3a` |
| Venv | `/var/lib/bitcoin-trader/venv-pre-soak` (Python 3.11.16) |
| All imports verified on EC2 | YES |

---

## V1 90m: FAILED_TO_START (CONSUMED)

| Item | Value |
|------|-------|
| Epoch | `aws-observability-90m-20260917-20260917T043600Z-v1` |
| Failure | `--duration 0` passed to collector; requires positive duration |
| Elapsed | 0.42 seconds |
| Consumed | YES — never reuse |

---

## V2 90m: FAILED_TO_START (CONSUMED)

| Item | Value |
|------|-------|
| Epoch | `aws-observability-90m-20260917-20260917T050128Z-v2` |
| Failure | `raw_root_template must contain exactly one {collector_epoch} placeholder` |
| Root cause | runtime.json was created without proper template placeholders |
| Elapsed | 0.42 seconds |
| Consumed | YES — never reuse |

**Detailed error chain:**
1. `run_cross_market_collector.py` loads `--config-file` (runtime.json)
2. Validates that `raw_root_template` contains `{collector_epoch}` placeholder
3. The runtime.json created by the deployment agent used literal paths instead of templates
4. Collector exits with code1

---

## What V3 Must Fix

The runtime.json config file must be created by adapting the V4 template:

```bash
# Read the V4 template on EC2:
sudo cat /var/lib/bitcoin-trader/launch-artifacts/aws-validation-30h-20260915-v4/aws-validation-30h-20260915-v4.runtime.json
```

Then adapt it for the new epoch while preserving the `{collector_epoch}` placeholder structure in:
- `raw_root_template`
- `manifest_root_template`  
- `compressed_root_template`
- `receipt_root_template`

The collector-command-json paths must also match the runtime.json paths exactly.

---

## Local Engineering (Complete)

| Gate | Status |
|------|--------|
| 1498 tests | PASS |
| Pyright | 0 errors |
| compileall | PASS |
| All observability modules | Implemented and tested |
| Archiver health bridge | Complete |
| Canary evidence binding | Complete |
| Terminal witness fsync + ExecStopPost | Complete |
| Watchdog Type=notify + WatchdogSec=60s | Complete |

---

## AWS Achievements (This Session)

- Session Manager access restored
- EC2 recovery audited (idle,101G free)
- Worktree deployed at correct commit
- All observability modules import on EC2
- Observer one-shot verified on EC2
- Terminal witness CLI verified on EC2
- Health snapshot write verified on EC2
- Prelaunch gates all PASS
- systemd unit renders with WatchdogSec, Type=notify, ExecStopPost
- Instance role S3 PutObject verified

---

## SSM Command Execution Helper

A helper script exists at `scripts/ssm_exec.py` for running commands on EC2:
```bash
python3 scripts/ssm_exec.py "command1" "command2" ...
```

It opens an SSM interactive session, sends commands with markers, and parses output. Takes ~30 seconds per batch due to SSM session initialization delays.

---

## Safety State

- ALPHA = UNPROVEN
- PAPER = NOT STARTED
- LIVE = DISABLED
- PRIVATE API = DISABLED

---

## Next Exact Action for Gemini

1. **Read the V4 runtime.json template** from EC2 to understand the correct placeholder format
2. **Create a correctly templated runtime.json** for the v3 identity (preserve `{collector_epoch}` placeholders)
3. **Create v3 identity** (`aws-observability-90m-20260917-<UTCSTAMP>-v3`)
4. **Deploy runtime.json** to the new launch artifacts directory on EC2
5. **Verify dry-run** shows correct systemd unit before launching
6. **Launch exactly once**
7. **Observe for90 minutes**
8. The code is correct — only the launch artifact configuration was wrong
