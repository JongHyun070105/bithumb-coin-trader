# Incident Record: 72H Soak Launch Attempt 1 — Pre-Start Launch Failure

**Document Version:** 1.0.0  
**Event Timestamp:** `2026-09-05T03:40:00.033Z`  
**Authoritative Commit:** `9532cebc902856d954bf80b51dbe567b543dc8e2`  
**Epoch:** `aws-72h-soak-20260905-8017b83e`  
**Run ID:** `aws-72h-soak-run-20260905T024039Z-8017b83e`  
**Classification:** `PRE_START_LAUNCH_FAILURE`  
*(NOT a 72H Data Quality or Collector Runtime Failure, as no market data collection process ever started)*

---

## 1. Executive Summary

On 2026-09-05 at 03:36:00 UTC, the autonomous preflight JIT verification passed all 12 Hard Gates:
- IAM permissions boundary v5 default active
- S3 temporary & canonical prefixes 100% empty (0 objects)
- Guest code commit `9532cebc902856d954bf80b51dbe567b543dc8e2` clean
- Runtime seal SHA256 & Fingerprint 100% matching
- Chrony Amazon Time Sync synchronized
- All public exchange endpoints (Bithumb, Binance 443, Upbit) reachable
- Root EBS gp3 200 GiB healthy

At the scheduled launch window (`2026-09-05T03:40:00Z`), the launcher dispatched the transient service start command via AWS SSM. The command exited immediately with code 1 before the collector or supervisor process was initialized.

---

## 2. Event Details

- **Attempt Number:** 1
- **Scheduled Launch UTC:** `2026-09-05T03:40:00Z`
- **Dispatched Command:**
  ```bash
  sudo -u bitcoin-trader /var/lib/bitcoin-trader/venv-pre-soak/bin/python \
    /var/lib/bitcoin-trader/72h-soak/aws-72h-soak-20260905-8017b83e/launch_72h.py --launch
  ```
- **Error Output:**
  ```text
  Launching transient service bitcoin-trader-72h-soak-aws-72h-soak-run-20260905T024039Z-8017b83e.service...
  Failed to start transient service unit: Access denied
  ===SSM_EXEC_END=== 1
  ```
- **Exit Code:** `1`
- **Collector Actually Started:** `NO` (transient unit registration rejected by systemd manager)
- **Market Data Written:** `NO` (zero bytes)
- **S3 Objects Written:** `NO` (0 objects)
- **Manual Runtime Intervention:** `NO`

---

## 3. Initial Root Cause Analysis

- **Observation:** `systemd-run` returned `Access denied`.
- **Caller Identity:** The SSM command executed `launch_72h.py` under the unprivileged Unix user `bitcoin-trader` (`sudo -u bitcoin-trader ...`).
- **Target Subsystem:** `systemd-run` attempts to connect to the system-level D-Bus (`org.freedesktop.systemd1`) to create a transient unit in the system manager.
- **Architectural Analysis:** In `src/bithumb_coin_trader/bounded_supervisor.py`, `render_systemd_run` already explicitly includes `--uid=bitcoin-trader`. Therefore, the privilege drop to `bitcoin-trader` is handled by systemd itself when executing the payload. However, creating the transient system unit requires system-manager authorization (invoked via `sudo systemd-run ...`).
- **Preservation Directive:** This failed pre-start attempt is recorded as historical evidence and will not be erased.
