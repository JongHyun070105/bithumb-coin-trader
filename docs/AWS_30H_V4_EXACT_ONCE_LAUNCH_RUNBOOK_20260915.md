# AWS 30H V4 Exact-Once Launch Runbook

## 1. Identity

| Field | Value |
|-------|-------|
| Epoch | `aws-validation-30h-20260915-v4` |
| Run ID | `aws-validation-30h-run-20260915T061253Z-v4` |
| S3 Prefix | `market-data/temporary/aws-validation-30h-20260915-v4` |
| Runtime Commit | `ac81f94f431f5d868d88e10fa784eb0da449264d` |
| Runtime Tree | `5cf28b47cd522df127e3eddada0732ad28e3d371` |
| Config Fingerprint | `4229274b582598bb869aafd4d0c139949559ebffab837546613be651ee60b2fa` |
| Systemd Unit | `bitcoin-trader-30h-aws-validation-30h-run-20260915T061253Z-v4.service` |

---

## 2. Authorization Requirement

**Explicit human authorization is required before launch.**

Opening this runbook, reading preflight checks, or performing read-only verification does NOT constitute authorization.

The authorization lifecycle is:

```
PREPARED_NOT_AUTHORIZED
    ↓ explicit human authorization
AUTHORIZED
    ↓ exactly one launch command submission
    ↓ (identity permanently consumed regardless of outcome)
LAUNCHED / FAILED_TO_START / UNKNOWN
```

Authorization evidence is recorded in:
`/var/lib/bitcoin-trader/launch-artifacts/aws-validation-30h-20260915-v4/aws-validation-30h-20260915-v4.authorization-evidence.json`

---

## 3. Pre-Authorization Preflight

Before authorizing launch, verify ALL of the following:

### 3.1 Guest State
- [ ] Instance `i-008bc503c1136349f` is running
- [ ] Disk >= 50 GiB free (`df -h /`)
- [ ] No collector process running (`ps aux | grep collector | grep -v grep`)
- [ ] No supervisor process running
- [ ] No archive scheduler process running
- [ ] No V4 systemd unit active (`systemctl list-units | grep v4`)

### 3.2 Runtime Identity
- [ ] Runtime worktree HEAD = `ac81f94f431f5d868d88e10fa784eb0da449264d`
  ```bash
  git -C /var/lib/bitcoin-trader/runtime-worktrees/aws-validation-30h-20260915-v4 rev-parse HEAD
  ```
- [ ] Runtime tree = `5cf28b47cd522df127e3eddada0732ad28e3d371`
  ```bash
  git -C /var/lib/bitcoin-trader/runtime-worktrees/aws-validation-30h-20260915-v4 rev-parse 'HEAD^{tree}'
  ```
- [ ] Runtime worktree is clean (`git status --porcelain` returns empty)

### 3.3 Artifact Integrity
- [ ] `launch.sh` exists and SHA256 = `6b5bfa5b47a96d359fc390e06ec26014936e7e96d2000690442c40c1cd356ff1`
  ```bash
  sha256sum /var/lib/bitcoin-trader/launch-artifacts/aws-validation-30h-20260915-v4/launch.sh
  ```
- [ ] `runtime.json` exists and SHA256 = `a4274816ac5405acd07493b33d514e34ad28885cc61f63c92a84b50b4f98c4a4`
- [ ] `launch-command.json` exists and SHA256 = `8b6e7939dd01d028339d4f93be988e6e79fd9f815ac9a70d7c36c98ac9e042fc`

### 3.4 S3 Namespace
- [ ] V4 prefix is empty: `aws s3api list-objects-v2 --bucket <bucket> --prefix market-data/temporary/aws-validation-30h-20260915-v4/ --query KeyCount` returns `0` or `null`

### 3.5 Authorization State
- [ ] `launch_authorized` = false (not yet authorized)
- [ ] `actual_start_time_utc` = null (not yet started)
- [ ] Identity is unconsumed

---

## 4. Authorization

After all preflight checks pass, the human operator authorizes the launch by updating the authorization evidence:

```bash
# Set launch_authorized = true (requires sudo as bitcoin-trader user)
# This is done via the authorization evidence update mechanism.
```

**Authorization is a one-way transition.** Once authorized, the identity cannot be returned to PREPARED_NOT_AUTHORIZED.

---

## 5. Official Launch Command

**THE COMMAND MAY BE SUBMITTED AT MOST ONCE.**

```bash
sudo /var/lib/bitcoin-trader/launch-artifacts/aws-validation-30h-20260915-v4/launch.sh --launch
```

### 5.1 Before Submitting

- [ ] You have completed Section 3 preflight
- [ ] You have completed Section 4 authorization
- [ ] You understand this command can only be submitted ONCE
- [ ] You are prepared to investigate the outcome using read-only evidence (Section 6)

### 5.2 Submission Rules — CRITICAL

| Rule | Description |
|------|-------------|
| **ONE ATTEMPT MAXIMUM** | The launch command may be submitted exactly once. The LAUNCH ATTEMPT COUNT starts at zero and increments to one. It never exceeds one. |
| **NO RETRY ON TIMEOUT** | If the SSM session times out or disconnects after submission, do NOT resubmit. Investigate using read-only evidence (Section 6). |
| **NO RETRY ON AMBIGUITY** | If the response is unclear, incomplete, or missing, do NOT resubmit. Investigate using read-only evidence. |
| **NO RETRY ON FAILURE** | If evidence proves the collector failed to start, record `FAILED_TO_START`. Do NOT resubmit. A new attempt requires a new identity. |
| **NO RETRY ON DISCONNECT** | If the shell disconnects mid-command, do NOT open a new session to resubmit. The systemd transient unit was either created or not — investigate. |

---

## 6. Post-Launch Evidence Collection (Read-Only)

After the single launch submission, determine the outcome using ONLY read-only evidence.

**DO NOT submit the launch command again.**

### 6.1 Systemd Unit State
```bash
systemctl status bitcoin-trader-30h-aws-validation-30h-run-20260915T061253Z-v4.service
```

### 6.2 Process State
```bash
ps aux | grep -E 'collector|supervisor|archive' | grep -v grep
```

### 6.3 System Journal
```bash
journalctl -u bitcoin-trader-30h-aws-validation-30h-run-20260915T061253Z-v4.service -n 100
```

### 6.4 Collector Lifecycle
```bash
cat /var/lib/bitcoin-trader/30h-validation/aws-validation-30h-20260915-v4/collector-lifecycle.json
```

### 6.5 Actual-Start Evidence
Check for actual-start evidence in the evidence directory.

### 6.6 Data Root
```bash
ls -la /var/lib/bitcoin-trader/30h-validation/aws-validation-30h-20260915-v4/raw/
```

---

## 7. Outcome Classification

| Evidence | Classification | Action |
|----------|---------------|--------|
| Collector process running, data flowing | **RUNNING** | Continue normal monitoring |
| Collector exited immediately, error in journal | **FAILED_TO_START** | Record failure. Identity consumed. Do NOT retry. |
| Unit exists but collector exited after partial data | **PARTIAL_FAILURE** | Record failure. Identity consumed. Do NOT retry. |
| No unit, no process, no data | **UNKNOWN** | Record ambiguity. Identity consumed. Do NOT retry. Do NOT assume failure or success. |

**In all cases: the identity is consumed after the single launch submission.**

If another launch attempt is needed for any reason, a COMPLETELY NEW identity (new epoch, new run ID, new seals) is required.

---

## 8. Identity Reuse Prohibition

After the launch command is submitted (Section 5):

- This identity (`aws-validation-30h-20260915-v4` / `aws-validation-30h-run-20260915T061253Z-v4`) is permanently consumed.
- No replacement launch attempt is permitted for this identity.
- No replacement 31st hour is permitted.
- No retroactive evidence repair is permitted.
- If the collector fails and another attempt is needed, generate a new V5+ identity through the full preparation/sealing/authorization cycle.

---

## 9. Post-Launch State

After launch:

| Field | Value |
|-------|-------|
| V4 | RUNNING (or FAILED_TO_START / UNKNOWN) |
| INFRA VALIDATION CLOSED | NO |
| ALPHA | UNPROVEN |
| PAPER | NOT STARTED |
| LIVE | DISABLED |
| PRIVATE API | DISABLED |

A successful process start is NOT a validation PASS. The 30-hour collection must complete with full evidence before any conclusion.

---

## 10. Artifact Locations

| Artifact | Guest Path |
|----------|-----------|
| Runtime worktree | `/var/lib/bitcoin-trader/runtime-worktrees/aws-validation-30h-20260915-v4` |
| Launch entry point | `/var/lib/bitcoin-trader/launch-artifacts/aws-validation-30h-20260915-v4/launch.sh` |
| Launch command | `/var/lib/bitcoin-trader/launch-artifacts/aws-validation-30h-20260915-v4/aws-validation-30h-20260915-v4.launch-command.json` |
| Runtime config | `/var/lib/bitcoin-trader/launch-artifacts/aws-validation-30h-20260915-v4/aws-validation-30h-20260915-v4.runtime.json` |
| Authorization evidence | `/var/lib/bitcoin-trader/launch-artifacts/aws-validation-30h-20260915-v4/aws-validation-30h-20260915-v4.authorization-evidence.json` |
| Data root | `/var/lib/bitcoin-trader/30h-validation/aws-validation-30h-20260915-v4` |
| Qualification schedule | `/var/lib/bitcoin-trader/30h-validation/aws-validation-30h-20260915-v4/qualification_schedule.json` |

---

## 11. Seal Provenance

Sealed artifacts are committed in the repository at:
`infra/aws/seals/aws-validation-30h-20260915-v4.*`

| Seal | SHA256 |
|------|--------|
| runtime.json | `a4274816ac5405acd07493b33d514e34ad28885cc61f63c92a84b50b4f98c4a4` |
| launch-command.json | `8b6e7939dd01d028339d4f93be988e6e79fd9f815ac9a70d7c36c98ac9e042fc` |
| launch-wrapper.sh / launch.sh | `6b5bfa5b47a96d359fc390e06ec26014936e7e96d2000690442c40c1cd356ff1` |
| authorization-evidence.json | `39cd3c0bd06634e5834194d56397e8fffc03244a69083fd573774e57387b563e` |
| feed-universe.json | `52adb4e5f7dc06cbc85ae1926c043ffd44dd47b86f70c09e248c16568e428500` |
| timing-contract.json | `adfcc4fc24d8cda14a8935a48a90683b56907c955f62437b326bb46905000284` |
| heartbeat-contract.json | `e8d6ebfa15425daecfbefd482dea129a43d79b3444db67e958701c4ecc0ad929` |
| launch-provenance.json | `a333f5c2661e889709610a3411d918771a414daba87d4938ed15deea4be861e8` |
