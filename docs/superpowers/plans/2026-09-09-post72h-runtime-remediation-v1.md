# Post-72H Runtime Remediation V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:test-driven-development` for each behavior and `superpowers:verification-before-completion` before any completion claim.

**Goal:** Eliminate date-hour archive collisions, allow natural collector finalization under an outer hard deadline, make scheduler shutdown cooperative, and enforce per-cohort audit evidence without altering the failed historical run.

**Architecture:** A validated `ArchiveCohortId` crosses every official archive boundary. The collector owns collection duration; the supervisor owns a distinct finalization envelope and hard ceiling. The auditor requires exact canonical evidence for every expected archive cohort. Legacy hour-only evidence is diagnostic and non-qualifying.

**Tech Stack:** Python 3, dataclasses, `unittest`/`pytest`, subprocess lifecycle fixtures, local file archive store.

**Spec:** `docs/superpowers/specs/2026-09-09-post72h-runtime-remediation-v1-design.md`

## Global constraints

- Keep `docs/POST_72H_FINAL_DECISION_20260909.md` and all prior 72H evidence unchanged.
- Use strict RED → GREEN for every behavior change; retain command/result evidence in the final report.
- Make no AWS call or write, start no collector, and perform no alpha, paper, live, dashboard, or `main` work.
- Treat hour-only artifacts as `LEGACY / NON-QUALIFYING` diagnostics only.
- Push only `codex/post72h-runtime-remediation-v1-20260909`.
- Plan the next stage as a 45-minute unattended AWS smoke; do not execute it.

## Task 1: Canonical identity

**Files:**
- Create `src/bithumb_coin_trader/archive_cohort.py`
- Create `tests/test_archive_cohort.py`

- [ ] Write tests for canonical rendering/parsing, real-date validation, hour validation, chronological sorting, and partition extraction.
- [ ] Run `PYTHONPATH=src python3 -m pytest tests/test_archive_cohort.py -q`; confirm RED because the type does not exist.
- [ ] Implement frozen ordered `ArchiveCohortId` and explicit legacy diagnostic classification.
- [ ] Rerun the exact test to GREEN and commit.

## Task 2: Scheduler propagation and release-blocking collision test

**Files:**
- Modify `src/bithumb_coin_trader/archive_scheduler.py`
- Modify `tests/test_archive_scheduler.py`

- [ ] Add the two-day completion regression and three-day same-HH scheduler regression using canonical result fields.
- [ ] Run the exact new tests and confirm RED from the hour-only API/state.
- [ ] Replace `EligibleHour` identity fields and `is_hour_completed`/`has_hour_failed` with cohort APIs; propagate canonical pending/target/processed keys.
- [ ] Check the stop event immediately before discovery and before orchestrator launch.
- [ ] Rerun focused scheduler tests to GREEN and commit.

## Task 3: Exact orchestrator and full-scan binding

**Files:**
- Modify `scripts/orchestrate_closed_hour_archive.py`
- Modify `tests/test_archive_concurrency.py`
- Modify `tests/test_full_scan_supervision.py`
- Add or modify focused orchestrator regression tests

- [ ] Add a three-date `05` selection test and two-date full-scan filename collision test.
- [ ] Run exact tests and confirm RED from suffix matching/hour-only names.
- [ ] Accept only `ArchiveCohortId`; select partitions by parsed cohort.
- [ ] Rename new report/log/metadata/scan-result/backlog references to canonical keys and mark legacy discoveries non-qualifying.
- [ ] Rerun focused tests to GREEN and commit.

## Task 4: Auditor per-cohort hard gates

**Files:**
- Modify `scripts/audit_72h_soak.py`
- Modify `tests/test_phase6_1_cohort_oracle.py`
- Modify `tests/test_phase6_crosslayer_regressions.py`
- Modify `tests/test_phase6_runbook_e2e.py` where contract output changes

- [ ] Add 72/72 scan PASS, middle scan missing, wrong-date same-hour, legacy-only, receipt wrong-date, missing receipt, and restore-binding tests.
- [ ] Run each focused group and confirm intended RED.
- [ ] Build exact expected-cohort maps; validate filename and internal identity, epoch/run/fingerprint, receipt identity, and restore fields.
- [ ] Emit `FULLSCAN_COHORT_COVERAGE_INCOMPLETE` and retain `ARCHIVE_RECEIPT_MISSING` as hard blockers.
- [ ] Add adversarial mutations for duplicate/foreign/missing lifecycle evidence and rerun focused tests to GREEN.
- [ ] Commit.

## Task 5: Explicit lifecycle and outer ceiling

**Files:**
- Modify `src/bithumb_coin_trader/bounded_supervisor.py`
- Modify collector entrypoint/configuration files found by code inspection
- Modify `tests/fixtures/lifecycle_child.py`
- Modify `tests/test_bounded_supervisor.py`

- [ ] Add fast fake collector tests for normal finalization, hung finalization, and early nonzero exit.
- [ ] Run each exact test and confirm RED against the shared deadline semantics.
- [ ] Add explicit collection duration, finalization timeout, and derived/validated hard ceiling config.
- [ ] Let the collector own collection expiry; signal only at the outer ceiling; fail immediately on early nonzero exit.
- [ ] Record lifecycle phase evidence and retain existing fail-closed checks.
- [ ] Rerun focused tests to GREEN and commit.

## Task 6: Cooperative scheduler shutdown

**Files:**
- Modify `src/bithumb_coin_trader/archive_scheduler.py`
- Modify `scripts/run_closed_hour_archive_scheduler.py` if required
- Modify `tests/test_archive_scheduler.py`

- [ ] Add idle SIGTERM, poll-wait stop, and no-new-cohort-after-stop tests.
- [ ] Run exact tests and confirm RED for the uncovered boundary.
- [ ] Wake waits with the stop event and prohibit new work after stop; preserve in-progress transaction boundaries.
- [ ] Rerun focused tests to GREEN and commit.

## Task 7: Lifecycle integration and 72H-shaped archive oracle

**Files:**
- Create or modify focused integration tests under `tests/`
- Modify only minimal fixture helpers required by those tests

- [ ] Add a local supervisor + collector + scheduler lifecycle test ending with exit 0, clean scheduler/publisher shutdown, manifest observed, and no forced timeout.
- [ ] Add the release-blocking 73 touched / 72 eligible / 76 feeds synthetic oracle with minimal files.
- [ ] Confirm each test RED before its supporting implementation/fixture change.
- [ ] Run to GREEN and commit.

## Task 8: Documentation and immutable-evidence check

**Files:**
- Modify relevant runtime/runbook documentation
- Create `docs/POST72H_REMEDIATION_V1_REPORT.md`

- [ ] Document canonical paths, lifecycle invariants, cooperative shutdown boundary, and the future AWS 45-minute unattended smoke.
- [ ] Compare historical report/evidence paths against base and record no mutation.
- [ ] Record every focused RED/GREEN command and result.
- [ ] Commit documentation.

## Task 9: Final verification and branch publication

- [ ] Run focused three-day same-HH regression.
- [ ] Run focused lifecycle integration.
- [ ] Run `PYTHONPATH=src python3 -m pytest` and require success.
- [ ] Review `git diff 26afad3845b11ef0404ccfacfed98e4695315b8d --` for scope and historical evidence preservation.
- [ ] Confirm no AWS command or runtime action occurred.
- [ ] Update the final report with exact counts and `READY TO START AWS 45M VALIDATION` or `NOT READY`.
- [ ] Commit the report, push the remediation branch without force, and verify remote SHA plus clean tracking status.
- [ ] Do not merge `main` and do not start AWS validation or a new 72H run.
