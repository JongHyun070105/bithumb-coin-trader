# Red Team Review: Collector Reliability & Observability Architecture

**Date**: 2026-09-17  
**Scope**: collector_state_model.py, runtime_observer.py, hour_close_canary.py, terminal_witness.py, cross_market_collector.py (health instrumentation diff)  
**Methodology**: Independent adversarial code audit. Each finding cites exact file paths, line numbers, and code paths.  

---

## Executive Summary

The observability architecture demonstrates strong defensive design in isolation: atomic writes, tolerant deserialization, non-intervention observer, and fail-closed canary logic. However, cross-referencing the V4 Failure Replay Analysis document's claims against the **actual implemented code** reveals **3 CRITICAL gaps** where the architecture does not yet match its documented safety guarantees. Most critically, the systemd Watchdog fencing described in the V4 analysis as a key defense is **not implemented in the code**.

| Severity | Count | Summary |
|----------|-------|---------|
| CRITICAL | 3 | Watchdog not implemented; health write blocks event loop; canary lacks true dual-binding |
| IMPORTANT | 4 | Canary S3 failure exception propagation; observer staleness gap; terminal witness observability gap; no health state archival |
| MINOR | 5 | Module import in hot path; resource metric inaccuracy; observer EVIDENCE overwrite; no archive queue monitoring; fd_count fallback |

---

## Question-by-Question Analysis

### Q1. Could observer failure affect collector?

**Classification: MINOR**

**Finding**: The observer and collector run as separate processes and communicate only through the filesystem (read-only observer → collector's `health/latest.json`). There are no shared locks, mutexes, or IPC channels.

**Evidence**:
- `runtime_observer.py` L363-364: Observer reads `collector_health_path = self.config.data_dir / "health" / "latest.json"` via `read_health_snapshot()` — a pure read operation.
- `collector_state_model.py` L209-225: `read_health_snapshot()` only calls `path.read_text()` and `json.loads()`. No write locks are acquired.
- `runtime_observer.py` L380-382: Observer writes only to its own path `health/observer_latest.json`, which the collector never reads.

**Edge Case**: If observer consumes excessive CPU/memory on the same EC2 instance, it could starve the collector via OS-level resource contention. The V4 analysis recommends cgroup limits (`CPUQuota=10%`, `MemoryMax=128M`), but **no cgroup properties are set** in `render_systemd_run()` (`bounded_supervisor.py` L142-157). Neither is there a separate systemd unit definition for the observer in the codebase — it would need one to apply cgroup isolation.

---

### Q2. Could collector failure kill observer?

**Classification: MINOR**

**Finding**: Robust. The observer's deserialization is fault-tolerant by design.

**Evidence**:
- `collector_state_model.py` L209-225: `read_health_snapshot()` catches `(OSError, json.JSONDecodeError, TypeError, KeyError)` and returns `None`.
- `runtime_observer.py` L181-186: When snapshot is `None`, observer creates a fresh snapshot with `UNKNOWN` state rather than crashing.
- `runtime_observer.py` L411-416: The `run_forever()` loop catches all `Exception` in the cycle, increments `observer_errors`, and continues.

**Edge Case**: A collector crash that leaves a health file with `observed_at` set to the past will cause the observer to correctly evaluate it as `STALE` (not crash), as the observer overrides the timestamp at `runtime_observer.py` L189: `eval_snapshot.observed_at = now.isoformat()`.

---

### Q3. Could S3 failure kill collector?

**Classification: CRITICAL (Canary S3 failure) / MINOR (Collector itself)**

**Finding**: The collector itself does **not** perform S3 operations. S3 uploads happen in the observer and canary, which are separate processes. However, the **canary raises an unhandled exception on S3 failure**, which could crash a scheduled canary runner.

**Evidence**:
- Collector: No S3/boto3 imports in `cross_market_collector.py`. Health snapshots are local-only.
- Observer: `runtime_observer.py` L290-350: `_upload_to_s3_fail_safe()` catches S3 exceptions, marks `EVIDENCE=DEGRADED`, logs locally. **Does not propagate exceptions.** ✓
- **Canary**: `hour_close_canary.py` L610-629: When `upload_s3=True` and S3 put fails:
  ```python
  except Exception as exc:
      raise HourCloseCanaryError(f"Failed to upload canary artifact to S3 s3://{self.s3_bucket}/{s3_key}: {exc}") from exc
  ```
  This **propagates the exception** to the caller. While the local artifact is written at L607 before this point, the `HourCloseCanaryReport` object (created at L585-602) is constructed BEFORE the S3 upload attempt. If the caller wraps this in a try/except, the report is lost. If the caller doesn't handle it, the scheduled canary task crashes.
- Terminal witness: `terminal_witness.py` L208-209: S3 failure is caught and logged. Does not propagate. ✓

**Edge Case (CRITICAL)**: If S3 is down for an extended period and the canary is invoked by a scheduler, every canary invocation will raise `HourCloseCanaryError`. If the scheduler treats this as a fatal error, it could stop running canaries entirely, creating a blind spot in coverage verification. The local artifact IS written before the S3 attempt, but there's no mechanism to retry the S3 upload separately.

---

### Q4. Could health state be half-written?

**Classification: MINOR (addressed)**

**Finding**: The atomic write implementation is strong and includes parent directory fsync.

**Evidence**:
- `collector_state_model.py` L173-206: `write_health_snapshot_atomic()`:
  1. L184: `tempfile.mkstemp()` in same directory → ensures same filesystem for atomic rename
  2. L187-189: `f.write(payload)` → `f.flush()` → `os.fsync(f.fileno())` — data durability
  3. L190: `os.replace(temp_path, path)` — POSIX atomic rename (same mountpoint)
  4. L192-198: Parent directory fsync — crash safety for metadata
  5. L200-206: Cleanup temp file on exception

- `hour_close_canary.py` L126-145: `_atomic_write_json()` uses similar pattern with `os.open(O_WRONLY|O_CREAT|O_TRUNC)` → `os.write()` → `os.fsync()` → `os.replace()`. Also includes parent fsync via `_fsync_file()`.

- `terminal_witness.py` L65-86: `write_receipt_atomic()` also uses `tempfile.mkstemp` → `f.flush()` → `os.fsync()` → `os.replace()`. **Note**: Does NOT fsync the parent directory (unlike the other two implementations).

**Finding Detail**: The parent directory fsync is missing in `terminal_witness.py` L65-86, while present in `collector_state_model.py` L192-198 and `hour_close_canary.py` L115-123. This is a minor inconsistency — under a kernel panic, the terminal receipt directory entry could be lost.

---

### Q5. Could stale data look healthy?

**Classification: IMPORTANT**

**Finding**: The observer correctly overrides stale timestamps. However, there is a gap in the stale detection logic for the `DEGRADED` fallback case.

**Evidence**:
- `runtime_observer.py` L225-243: Observer evaluates collector state:
  ```python
  dt_heartbeat = _parse_utc_iso(eval_snapshot.collector.last_loop_heartbeat)
  if dt_heartbeat is None:
      # UNKNOWN or STALE
  else:
      heartbeat_age = (now - dt_heartbeat).total_seconds()
      if heartbeat_age > self.config.stale_heartbeat_threshold_seconds:
          eval_snapshot.collector.status = ComponentHealthState.STALE.value
      else:
          # Heartbeat is fresh — evaluate CONNECTED status
          if eval_snapshot.collector.reconnect_count > 10:
              eval_snapshot.collector.status = ComponentHealthState.DEGRADED.value
          else:
              eval_snapshot.collector.status = ComponentHealthState.HEALTHY.value
  ```
  The observer NEVER trusts the collector's self-reported `status` field — it always recomputes based on timestamps and session state. ✓

- `cross_market_collector.py` L455: Collector updates `self._last_loop_heartbeat = now_iso` at the START of `emit_health_snapshot()`. This means the heartbeat is set to "now" at the time of snapshot emission, not at the time of the last successful event loop iteration.

**Gap (IMPORTANT)**: The `_last_loop_heartbeat` is set at the beginning of `emit_health_snapshot()` (L455), not at the point where the event loop is confirmed to be making progress. If the event loop is stalled but `_health_worker` somehow gets scheduled (unlikely in asyncio but theoretically possible if there's a brief GIL release), the heartbeat would appear fresh while the loop is actually stuck. In practice, asyncio's cooperative scheduling means `_health_worker` can only run when other coroutines yield, so this scenario requires the stall to happen exactly during an `await` in the websocket loops.

---

### Q6. Could quiet market look like dead collector?

**Classification: MINOR (well-handled)**

**Finding**: The architecture correctly separates loop heartbeat from canonical event timestamp.

**Evidence**:
- `cross_market_collector.py` L455: `_last_loop_heartbeat` is updated every time `emit_health_snapshot()` is called (every 10s via `_health_worker`)
- `cross_market_collector.py` L364-366: `_last_canonical_event` and `last_websocket_activity[exchange]` are updated only when actual data is received in `_enqueue()`
- `runtime_observer.py` L224-243: Observer checks `last_loop_heartbeat` freshness (not `last_canonical_event`) for STALE detection. If heartbeat is fresh, the collector is HEALTHY regardless of whether canonical events are flowing.
- `cross_market_collector.py` L955 (diff): Ping/pong heartbeat also updates `last_websocket_activity[exchange]` even without trade data, providing a secondary liveness signal during quiet markets.

**Assessment**: A quiet market with no trades still has websocket ping/pong traffic updating `last_websocket_activity`, and the event loop continues running updating `last_loop_heartbeat`. The observer correctly distinguishes this from a dead collector.

---

### Q7. Could observer mutate collector data?

**Classification: MINOR (well-handled)**

**Finding**: The observer never writes to the collector's health path or any raw data paths.

**Evidence**:
- `runtime_observer.py` L363-364: Reads `data_dir / "health" / "latest.json"` (collector's file)
- `runtime_observer.py` L380-382: Writes to `data_dir / "health" / "observer_latest.json"` (separate file)
- `runtime_observer.py` L14: Docstring: "NON-INTERVENING: NEVER restarts collector, modifies data, or deletes files."
- Observer has no `os.unlink`, `os.rename`, `shutil.move`, `subprocess.run(["systemctl", ...])` calls that could modify collector state or restart services.

**Edge Case**: The observer writes `observer_latest.json` to the same `health/` directory as the collector's `latest.json`. On a filesystem with very low inode limits or restrictive quotas, this could theoretically exhaust the directory. This is extremely unlikely in production.

---

### Q8. Could terminal witness fail silently?

**Classification: IMPORTANT**

**Finding**: The terminal witness has a two-tier write strategy (local first, then S3), which is sound. However, the exit code handling has a gap, and there's no external monitoring of whether the terminal witness actually ran.

**Evidence**:
- `terminal_witness.py` L162-166: Local receipt written first (always attempted):
  ```python
  write_receipt_atomic(terminal_receipt_path, receipt)
  try:
      write_receipt_atomic(timestamped_receipt_path, receipt)
  except Exception as exc:
      logger.warning("Could not write timestamped receipt: %s", exc)
  ```
  If the timestamped receipt fails, only a warning is logged — the primary receipt still succeeds.

- `terminal_witness.py` L254-270: `main()` catches all exceptions:
  ```python
  try:
      receipt = record_terminal_receipt(...)
      print(json.dumps(receipt, indent=2))
      return 0
  except Exception as exc:
      print(f"FATAL: terminal witness failed: {exc}", file=sys.stderr)
      return 1
  ```

- `terminal_witness.py` L168-209: S3 upload failure is caught and logged (L208-209), does not propagate.

**Gap (IMPORTANT)**: If `ExecStopPost` itself fails (e.g., Python not found, import error, permission denied), the systemd unit will still complete — `ExecStopPost` failures do not affect the service result code. There is **no external monitoring** that verifies the terminal witness ran successfully. If the witness script has a syntax error or a missing dependency, the receipt will never be written and nobody will know. The bounded supervisor (`bounded_supervisor.py` L266-399) does not check for the existence of a terminal receipt after collector shutdown.

**Gap (IMPORTANT)**: The `terminal_witness.py` L65-86 `write_receipt_atomic()` does NOT fsync the parent directory (unlike `write_health_snapshot_atomic` in `collector_state_model.py` L192-198). Under a kernel panic during service termination, the receipt's directory entry could be lost.

---

### Q9. Could canary fabricate coverage?

**Classification: CRITICAL**

**Finding**: The V4 Failure Replay Analysis document (Q9) claims the canary performs "dual-artifact binding verification" including S3 existence checks, byte-size checks, and SHA-256 checksum matching. **The actual canary code does NOT implement this.** The canary only checks file existence and non-emptiness on the local filesystem.

**Evidence**:
- `hour_close_canary.py` L306-464: `_evaluate_feed()` method:
  - L315-337: Coverage check — loads `coverage.json`, checks `coverage_state in ("DATA_PRESENT", "VERIFIED_ZERO_EVENT")`. Does NOT extract or verify `data_artifact_binding`, `raw_sha256`, or cross-reference raw file paths from coverage.
  - L346-348: Raw check — `raw_path.is_file() and raw_path.stat().st_size > 0`. Only checks existence and non-emptiness. Does NOT verify that this raw file matches what the coverage file references.
  - L380-382: Compressed check — same pattern: `is_file() and st_size > 0`.
  - L388-448: Receipt check — checks receipt state and `restore_verified_at` field presence.

- The canary does detect the V4 failure pattern at L543:
  ```python
  if coverage_terminal_count > 0 and raw_data_present_count == 0 and non_zero_expected > 0:
      observations.append("HIGH_SEVERITY_OBSERVATION: RAW data missing (0/76) ...")
  ```
  This catches the case where coverage exists but raw files are completely absent. ✓

**Critical Gap**: The canary does NOT verify that the raw file referenced inside the coverage evidence (`data_artifact_binding.raw_relative_path`) actually exists at that exact path, nor does it verify checksums. A scenario where:
1. Coverage says "feed X has DATA_PRESENT, raw at path Y with sha256 Z"
2. A different (wrong) raw file exists at a different path that the canary happens to find via candidate path matching
3. The canary would report `raw_terminal=True` based on finding the wrong file

This is a weaker guarantee than the V4 document describes. The V4 document claims "cryptographic bidirectional binding" but the implementation only performs independent existence checks.

**Mitigating Factor**: The canary is a post-hoc auditor that runs after the collection window closes. The primary data integrity comes from the collector's own write path, not the canary. The canary's role is detecting gross failures (0/76 raw, missing coverage), which it does correctly.

---

### Q10. Could archive lag grow without alert?

**Classification: IMPORTANT**

**Finding**: The canary computes `archive_lag_seconds` but has no threshold-based alerting mechanism within the code itself. Lag monitoring depends entirely on external scheduling and inspection of the canary report.

**Evidence**:
- `hour_close_canary.py` L522-534: Archive lag computation:
  ```python
  if all_terminal and completion_timestamps:
      latest_completion = max(completion_timestamps)
      archive_lag_seconds = max(0.0, (latest_completion - cohort_close).total_seconds())
  else:
      archive_lag_seconds = max(0.0, (cur_now - cohort_close).total_seconds())
  ```
  When not all terminal, lag grows with wall-clock time. ✓

- `hour_close_canary.py` L536-580: The canary does NOT have a lag-specific alert threshold. It reports DEGRADED or FAIL based on feed counts, not lag. A cohort with 76/76 feeds terminal but 2-hour archive lag would be reported as "PASS" with the lag value buried in the report.

- `collector_state_model.py` L74-83: `ArchiverHealth` has `archive_queue_depth`, `last_s3_put`, `archive_errors`, `upload_failures` fields. But **no code in the codebase sets alerting thresholds** on these values. The observer evaluates archiver health at `runtime_observer.py` L258-265, but only checks error counts, not queue depth growth rate or absolute lag.

**Gap (IMPORTANT)**: There is no mechanism that triggers an alert when `archive_queue_depth > 0` for an extended period, or when `archive_lag_seconds` exceeds a threshold. The canary's grace period (default 600s / 10 minutes) provides a natural upper bound on when the canary CAN run, but nothing prevents the archive lag from growing beyond the canary's inspection window if the canary is not invoked on schedule.

---

### Q11. Could active systemd process be totally stalled?

**Classification: CRITICAL**

**Finding**: The V4 Failure Replay Analysis document (Q11) describes systemd Watchdog fencing (`WatchdogSec=60s` + `sd_notify("WATCHDOG=1")`) as the primary defense against zombie processes. **This is NOT implemented in the actual code.**

**Evidence**:
- `bounded_supervisor.py` L142-157: `render_systemd_run()` returns these systemd properties:
  ```python
  "--property=Restart=no",
  "--property=KillMode=mixed",
  f"--property=RuntimeMaxSec={config.systemd_runtime_max_seconds}s",
  "--property=TimeoutStopSec=55s",
  ```
  **No `WatchdogSec` property is set.** There is no `--property=WatchdogSec=60s` anywhere in the codebase.

- `cross_market_collector.py`: Grep for `sd_notify`, `watchdog`, `WATCHDOG`, `sdnotify` returns **zero matches**. The collector has no code to send watchdog heartbeats to systemd.

- `bounded_supervisor.py` L213-228: `_live_metrics_valid()` provides a software-level staleness check on collector metrics (checks `written_at` age within 30s), but this runs in the supervisor process, not in systemd. If the supervisor itself is stalled, this check never runs.

**Critical Impact**: If the collector's asyncio event loop stalls (GIL deadlock at C level, infinite loop in a synchronous extension, TCP socket hang in `websockets` library internals), the following happens:
1. Systemd shows the service as `active (running)` indefinitely (no WatchdogSec to expire)
2. The supervisor's `_live_metrics_valid()` check may or may not catch it (depends on whether the supervisor's poll loop is still running)
3. The observer would detect `last_loop_heartbeat` staleness after ~45s and report `STALE`, but **cannot kill the process** (non-intervention principle)
4. The only termination mechanism is `RuntimeMaxSec` (111900s = ~31 hours), which is far too long

**This is exactly the V4 failure mode.** The V4 analysis document describes this as "defense successful (SECURE)" but the code does not implement the described defense.

---

### Q12. Would this system have diagnosed V4?

**Classification: CRITICAL (partial implementation gap)**

**Finding**: The current code would **partially** diagnose V4, but with significant delays and incomplete evidence compared to what the V4 Failure Replay Analysis claims.

**Evidence-based Timeline Reconstruction**:

Assuming the V4 failure (collector loop stalls at 11:01:37 UTC) with the current code:

| Time (UTC) | Event | What the code actually does |
|---|---|---|
| 11:01:37 | Collector loop stalls | `_last_loop_heartbeat` frozen at ~11:01:30 (last `emit_health_snapshot()` call) |
| 11:01:47 | Next health snapshot | **Never emitted** — `_health_worker` is also stalled because it runs in the same event loop |
| 11:02:22 | Observer polls | Reads `latest.json`, sees `observed_at` = 11:01:30, age = 52s > 30s → evaluates `collector.status = STALE` ✓ |
| 11:02:22 | Observer uploads to S3 | S3 `observability/minute/20260915T110222Z.json` published with `collector.status=STALE` ✓ |
| 11:02:37 | 60s since stall | **No watchdog kill** (WatchdogSec not configured). Collector process remains `active (running)` |
| 11:03:37 | 120s since stall | Observer continues reporting STALE. No process termination. |
| ... | RuntimeMaxSec (31h) | Eventually systemd kills the process. Terminal witness fires. |

**Key Gaps vs. V4 Analysis Claims**:
1. **No 60s watchdog kill**: The V4 analysis claims "11:02:37 UTC: systemd Watchdog timer expires → collector force killed." This does NOT happen with the current code.
2. **No terminal witness at failure time**: Without a watchdog kill or supervisor intervention, the terminal witness never fires during the stall. The process remains active for up to 31 hours.
3. **Observer correctly detects STALE**: The observer would correctly mark the collector as STALE within ~45s. ✓
4. **Hour-close canary would eventually detect missing data**: But only after the 10-minute grace period following hour close, which could be up to 70 minutes after the stall begins.

**Conclusion**: The system would **detect** the V4 failure (via observer STALE detection) within ~45 seconds, but would **NOT terminate** the stalled process for up to 31 hours. The V4 analysis claims 3-minute full diagnosis including process termination — this is not achievable without WatchdogSec implementation.

---

## Consolidated Findings

### CRITICAL (3 findings)

#### CRITICAL-01: systemd Watchdog Not Implemented Despite V4 Analysis Claims
- **Files**: `bounded_supervisor.py` L142-157, `cross_market_collector.py` (entire file — no sd_notify)
- **Impact**: Zombie collector processes can persist for up to 31 hours (`RuntimeMaxSec=111900s`) without being killed. This is the exact V4 failure mode.
- **Required Fix**: Add `--property=WatchdogSec=60s` to `render_systemd_run()`. Add `sd_notify("WATCHDOG=1")` calls in the collector's `_health_worker` or main event loop iteration, gated on confirmed forward progress (data receipt or successful queue dequeue).

#### CRITICAL-02: Health Snapshot Write Blocks asyncio Event Loop
- **File**: `cross_market_collector.py` L607-620 (`_health_worker`), L451-605 (`emit_health_snapshot`)
- **Impact**: `emit_health_snapshot()` is synchronous and calls `write_health_snapshot_atomic()` which performs `os.fsync()`. While executing, the entire asyncio event loop is blocked — all websocket recv loops, heartbeat loops, and writer worker are paused. If the underlying filesystem (EBS) has high latency, this could cause the websocket 30-second recv timeout to fire, triggering unnecessary reconnections.
- **Evidence**: `_health_worker` L610: `self.emit_health_snapshot()` — no `await`, runs synchronously in the event loop. `collector_state_model.py` L189: `os.fsync(f.fileno())` — blocks until kernel flushes to disk.
- **Required Fix**: Run `write_health_snapshot_atomic()` in an executor: `await asyncio.get_event_loop().run_in_executor(None, write_health_snapshot_atomic, path, snapshot)` or use `asyncio.to_thread()`.

#### CRITICAL-03: Canary Does Not Implement Dual-Artifact Binding Verification
- **File**: `hour_close_canary.py` L306-464 (`_evaluate_feed`)
- **Impact**: The canary checks for raw file existence and coverage file existence independently, but does NOT cross-reference the coverage evidence's embedded `data_artifact_binding` to verify that the correct raw file exists with matching SHA-256. The V4 Failure Replay Analysis Q9 claims "cryptographic bidirectional binding" — this is not implemented.
- **Evidence**: L315-337 checks `coverage_state` only. L346-348 checks `raw_path.is_file() and raw_path.stat().st_size > 0` — no path cross-reference or checksum verification.
- **Required Fix**: After finding both coverage and raw files, extract `data_artifact_binding` from the coverage evidence, verify the referenced `raw_relative_path` matches the found raw file, and compute/compare SHA-256 checksums.

### IMPORTANT (4 findings)

#### IMPORTANT-01: Canary S3 Failure Propagates Exception
- **File**: `hour_close_canary.py` L628-629
- **Impact**: If S3 is unavailable, `inspect_cohort()` raises `HourCloseCanaryError` instead of returning the report with `s3_uploaded=False`. A scheduled canary runner that doesn't handle this exception will crash, losing the report.
- **Fix**: Catch S3 exceptions, set `s3_uploaded=False` on the report, log the error, and return the report normally. Do not raise.

#### IMPORTANT-02: No Monitoring of Terminal Witness Execution
- **File**: `bounded_supervisor.py` L266-399, `terminal_witness.py`
- **Impact**: If `ExecStopPost` fails (missing Python, import error, permission issue), no evidence of the failure exists. The bounded supervisor does not verify that a terminal receipt was written after collector shutdown.
- **Fix**: After stopping the collector process, the bounded supervisor should check for the existence of `terminal/terminal-receipt.json` and log a warning if absent.

#### IMPORTANT-03: No Archive Lag Threshold Alerting
- **File**: `hour_close_canary.py` L522-580, `runtime_observer.py` L258-265
- **Impact**: Archive lag can grow without triggering any alert. The canary reports the lag value but has no threshold. The observer checks error counts but not queue depth growth rate.
- **Fix**: Add a lag threshold check in the canary (e.g., `archive_lag_seconds > 1800` → add explicit observation) and in the observer's archiver evaluation (queue depth > 0 for > 5 minutes → DEGRADED).

#### IMPORTANT-04: Parent Directory fsync Missing in Terminal Witness
- **File**: `terminal_witness.py` L65-86 (`write_receipt_atomic`)
- **Impact**: Under kernel panic during service termination, the receipt's directory entry could be lost, unlike the health snapshot and canary artifacts which DO fsync the parent directory.
- **Fix**: Add parent directory fsync after `os.replace()`, matching the pattern in `collector_state_model.py` L192-198.

### MINOR (5 findings)

#### MINOR-01: Module Imports in Hot Path
- **File**: `cross_market_collector.py` L534-535 (`import resource, import sys`), L547 (`import shutil`), L560 (`import resource as _resource`)
- **Impact**: `emit_health_snapshot()` is called every 10 seconds. While Python caches module imports after first load, the import lookup still has overhead. These should be moved to top-level imports.

#### MINOR-02: Resource Metric Inaccuracy on macOS
- **File**: `cross_market_collector.py` L537-540
- **Impact**: `resource.getrusage(RUSAGE_SELF).ru_maxrss` returns bytes on macOS but kilobytes on Linux. The code correctly handles this (`if sys.platform == "darwin": rss_bytes = int(ru.ru_maxrss)` else `* 1024`), but `ru_maxrss` is a HIGH WATER MARK, not current RSS. The metric name `rss_bytes` implies current usage.

#### MINOR-03: Observer Evidence Status Overwrite
- **File**: `runtime_observer.py` L268-269
- **Impact**: Evidence evaluation unconditionally sets `HEALTHY` if status is `UNKNOWN` or empty, without checking actual evidence delivery status. This means if the canary hasn't run yet, evidence is reported as `HEALTHY` rather than `UNKNOWN`.

#### MINOR-04: No Archive Queue Depth Monitoring in Observer
- **File**: `runtime_observer.py` L258-265
- **Impact**: The observer evaluates archiver health based on error/failure counts but does not evaluate `archive_queue_depth`. A growing queue with zero errors would not trigger any status change.

#### MINOR-05: fd_count Fallback Returns RLIMIT Instead of Current Count
- **File**: `cross_market_collector.py` L554-562
- **Impact**: On non-Linux systems (macOS, where `/proc` doesn't exist), `fd_count` is set to `resource.getrlimit(RLIMIT_NOFILE)[0]` — the maximum allowed file descriptors, not the current count. This is misleading.

---

## Comparison: V4 Failure Replay Analysis Claims vs. Actual Code

| V4 Analysis Claim | Actual Code Status | Gap? |
|---|---|---|
| Observer reads health file read-only | ✅ Implemented | No |
| read_health_snapshot tolerates corruption | ✅ Implemented | No |
| Atomic write via temp + fsync + rename | ✅ Implemented | No |
| Observer recomputes status from timestamps | ✅ Implemented | No |
| Quiet market ≠ dead collector | ✅ Implemented | No |
| Observer never mutates collector data | ✅ Implemented | No |
| Terminal witness local-first write | ✅ Implemented | No |
| Canary detects V4 pattern (coverage without raw) | ✅ Implemented | No |
| **systemd WatchdogSec=60s fencing** | ❌ NOT IMPLEMENTED | **YES — CRITICAL** |
| **sd_notify("WATCHDOG=1") in collector** | ❌ NOT IMPLEMENTED | **YES — CRITICAL** |
| **Dual-artifact binding with checksum verification** | ❌ NOT IMPLEMENTED | **YES — CRITICAL** |
| Canary S3 failure handled gracefully | ❌ Raises exception | **YES — IMPORTANT** |
| Archive lag threshold alerting | ❌ No threshold | **YES — IMPORTANT** |
| Terminal witness parent dir fsync | ❌ Missing | **YES — MINOR** |

---

## Recommendations (Priority Order)

1. **Implement WatchdogSec** (`bounded_supervisor.py`): Add `--property=WatchdogSec=60s` to `render_systemd_run()`. Add `sd_notify("WATCHDOG=1")` in the collector's `_health_worker` after confirming forward progress (successful health snapshot emission with non-stale data).

2. **Make health snapshot async** (`cross_market_collector.py`): Move `write_health_snapshot_atomic()` call into `asyncio.to_thread()` or `run_in_executor()` to avoid blocking the event loop during fsync.

3. **Implement true dual-artifact binding in canary** (`hour_close_canary.py`): After finding coverage and raw files, extract `data_artifact_binding` from coverage JSON, cross-reference paths, and verify SHA-256 checksums.

4. **Fix canary S3 error handling** (`hour_close_canary.py`): Catch `HourCloseCanaryError` from S3 upload, log it, set `s3_uploaded=False`, and return the report normally.

5. **Add terminal witness verification** (`bounded_supervisor.py`): After collector shutdown, check for terminal receipt existence and log warning if absent.

6. **Add parent directory fsync** (`terminal_witness.py`): Match the pattern in `collector_state_model.py`.

---

*This review was conducted as an independent adversarial audit. No source files were modified.*
