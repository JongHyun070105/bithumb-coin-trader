# AWS 45-Minute Short-Smoke Failure Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a tested runtime candidate that fixes Binance diagnosis, partition rotation, and bounded launch lifecycle without starting a collector or changing AWS infrastructure.

**Architecture:** A sanitized staged Binance probe isolates network layers. Collector partition activity is derived from feed-path ownership and an injected UTC clock. A Python supervisor owns collector/publisher lifecycles and is launched by a detached transient systemd unit with durable JSON evidence.

**Tech Stack:** Python 3.11+, `asyncio`, `ssl`, `socket`, `websockets`, `unittest`, transient systemd, AWS SSM.

**Spec:** `docs/superpowers/specs/2026-09-03-aws-45m-remediation-design.md`

## Global Constraints

- Preserve the production Binance URL and port until local/AWS staged evidence proves a source change is necessary.
- Never log proxy credentials, AWS metadata, account IDs, access keys, or session tokens.
- `active_partition_files` means only current-hour paths that may receive another write; it is empty after drain.
- Supervisor natural duration is exactly 2700 seconds; systemd may only provide a slightly longer hard ceiling.
- Do not run Terraform, alter IAM/SG/EBS/network, write production archives, clean data, or start a collector.
- Push only `codex/aws-45m-remediation`; do not merge/rebase/cherry-pick main.

---

### Task 1: Staged Binance diagnostic

**Files:**
- Create: `src/bithumb_coin_trader/binance_diagnostic.py`
- Create: `scripts/diagnose_binance_websocket.py`
- Create: `tests/test_binance_diagnostic.py`

**Interfaces:**
- Produces: `sanitize_proxy_url(value: str) -> dict[str, object]`, `collect_proxy_metadata(environ: Mapping[str, str]) -> dict[str, object]`, `async diagnose_symbol(symbol: str, *, proxy_mode: str, timeout: float) -> dict[str, object]`, and JSON CLI output with one attempt per symbol/mode.
- Consumes: the unchanged production Binance host, port, and stream paths from `cross_market_collector.py`.

- [ ] Write tests proving credentials are removed from proxy metadata, DNS candidates carry family/address, stage failures stop at the exact failed stage, and four symbols run in both `auto` and `direct` modes.
- [ ] Run `PYTHONPATH=src python -m unittest tests.test_binance_diagnostic -v`; expect failures because the module does not exist.
- [ ] Implement literal stage records for DNS, TCP, TLS, and WebSocket. Use explicit sockets for address candidates and pass the connected socket to `websockets.connect`; pass `proxy=True` for auto and `proxy=None` for direct without changing the production endpoint.
- [ ] Add a CLI that prints sanitized JSON and returns nonzero unless all requested handshakes succeed.
- [ ] Re-run the focused tests and verify PASS.

### Task 2: Exact active-partition lifecycle

**Files:**
- Modify: `src/bithumb_coin_trader/microstructure_storage.py`
- Modify: `src/bithumb_coin_trader/cross_market_collector.py`
- Modify: `tests/test_cross_market_collector.py`
- Modify: `tests/test_pre_soak_archive.py`

**Interfaces:**
- Produces: injected `utc_now: Callable[[], datetime]`; `_record_partition(feed_key, path)`; `_current_active_partition_files()`; `_all_touched_partition_files`; and `_accepting_partition_writes`.
- Consumes: `RawMicrostructureStorage.append_raw_record(..., write_ts: datetime | None = None) -> Path` so fake-clock tests control the partition hour.

- [ ] Add failing fake-time tests for H writes, H→H+1 rotation, multiple feeds, idle old-hour eviction, post-drain empty activity, complete touched-path manifests, and archive eligibility excluding only current active files.
- [ ] Run the focused collector/archive tests and confirm the append-only implementation fails the new expectations.
- [ ] Inject the UTC clock, track the latest path per feed plus all touched paths, derive current-hour activity, and close activity only after writer drain.
- [ ] Generate manifests from touched paths rather than active paths.
- [ ] Re-run focused tests and verify PASS.

### Task 3: Bounded supervisor and transient-unit renderer

**Files:**
- Create: `src/bithumb_coin_trader/bounded_supervisor.py`
- Create: `scripts/run_bounded_short_smoke.py`
- Create: `scripts/launch_short_smoke_transient.py`
- Create: `tests/fixtures/lifecycle_child.py`
- Create: `tests/test_bounded_supervisor.py`
- Create: `tests/test_transient_launch.py`

**Interfaces:**
- Produces: `SupervisorConfig`, `BoundedSupervisor.run() -> int`, atomic result schema version 1, and `render_systemd_run(config) -> list[str]`.
- Result fields: `run_id`, `started_at`, `ended_at`, `duration_limit_seconds`, `supervisor_pid`, `collector_pid`, `publisher_pid`, `received_signal`, `collector_exit_code`, `publisher_exit_code`, `publisher_started`, `final_metrics_valid`, `final_manifest_flush_observed`, `overall_status`.

- [ ] Add process-level failing tests for natural expiry, SIGINT, SIGTERM, writer/metrics/manifest markers, publisher gating and stop, durable exit status, and parent-shell disconnect independence using a real fixture child.
- [ ] Add renderer tests for unique safe unit name, `User=bitcoin-trader`, `Restart=no`, `KillMode=mixed`, detached execution, no enable/timer, exact supervisor duration, and a longer systemd ceiling.
- [ ] Run the focused tests and confirm failure before implementation.
- [ ] Implement process-group ownership, monotonic deadline, explicit signal forwarding, metrics validation, publisher ownership, atomic result persistence, and conservative exit-status aggregation.
- [ ] Implement the CLI and transient systemd renderer/launcher; default to render-only and require an explicit launch flag.
- [ ] Re-run focused tests and verify PASS, then repeat scheduling-sensitive tests five times with zero failures.

### Task 4: Integration, AWS bounded validation, seal, and evidence

**Files:**
- Modify: `infra/aws/seals/aws-short-smoke-20260902.runtime.json` only if it is the generic current seal input; otherwise create a new dated seal file without overwriting failed evidence.
- Create: `docs/AWS_45M_SHORT_SMOKE_FAILURE_REMEDIATION_2026-09-03.md`
- Modify: tests covering runtime config if a new seal path is introduced.

**Interfaces:**
- Produces: new runtime commit, canonical SHA-256 config fingerprint, unique `aws-short-smoke-20260903-<suffix>` epoch, matching run ID, and an unapplied IAM epoch-only diff report.

- [ ] Run local four-symbol diagnostic in auto and direct modes and record sanitized stage results.
- [ ] Run `python -m unittest discover -s tests`, `python -m compileall -q src scripts tests`, and `python -m pip check`; require all PASS.
- [ ] Authenticate to AWS only when prompted, verify the expected guest and zero conflicting collector processes, then run the diagnostic over SSM; require 4/4 handshakes without starting the collector.
- [ ] Run a non-market transient lifecycle mini-smoke, disconnect/poll through a new SSM command, and verify the durable result/log files and signal semantics.
- [ ] Generate the new epoch/run ID only after all source/AWS checks pass; preserve the failed seal/evidence and create a new seal file.
- [ ] Compute and verify the canonical config fingerprint; prepare a sanitized epoch-only permissions-boundary/inline-policy diff without applying it.
- [ ] Write remediation evidence with PASS/FAIL/NOT VERIFIABLE distinctions and explicit no-change/no-runtime statements.
- [ ] Run `git diff --check`, secret scan, full tests, compileall, and pip check again.
- [ ] Commit the runtime candidate, push `codex/aws-45m-remediation`, and verify the remote SHA and main remains `9aeb7b5e080d16ec135479fe989974bb2d7f6680`.
