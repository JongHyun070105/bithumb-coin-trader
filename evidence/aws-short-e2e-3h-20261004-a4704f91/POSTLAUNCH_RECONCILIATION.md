# Short E2E schedule reconciliation

Observed 2026-10-04 05:51 UTC. This addendum preserves the pre-launch record and
documents a schedule-field discrepancy discovered after the run had started.

`PRELAUNCH_GATE.md` records qualifying cohorts `_06` and `_07`, with partial
cohorts `_05` and `_08`. The sealed launch artifacts and live collector instead
bind this run to qualifying cohorts `_05` and `_06`, partial cohorts `_04` and
`_07`, and a 04:45 UTC start:

- Exact unit start: `2026-10-04 04:45:01 UTC`.
- Collector `collector_started_at`: `2026-10-04T04:45:02.555858+00:00`.
- Sealed identity/runtime: `planned_start_utc=2026-10-04T04:45:00+00:00`,
  `warmup_duration_seconds=900`, `qualification_start_utc=2026-10-04T05:00:00+00:00`,
  `qualifying_cohorts=[2026-10-04_05, 2026-10-04_06]`,
  `cohort_closure_utc=2026-10-04T07:00:00+00:00`,
  `partial_start_cohort=2026-10-04_04`, and `partial_end_cohort=2026-10-04_07`.
- Sealed identity hash: `3f50b35ed7e93e8b556ae699afecf3f18d97e869955fbdf058e5cecd07d88b9c`.
- Sealed runtime config hash: `9a5b6c32638d6acec7c1a1b1f994504d5ee6672db671cff1776a379dec167536`.
- Sealed manifest hash: `d4132ce4475a20c2c98efc447533c03e16b00349ff538c199179d69a119a3558`.

The sealed identity and runtime configuration are authoritative for this run;
the cohort list in the pre-launch narrative is inaccurate. At the 05:45 UTC
health sample, the collector remained active and wrote current-hour (05) market
partitions. The run was not stopped or restarted. Final receipt scope must be
adjudicated against the sealed 05/06 slot universe and Frozen V2 contract.

The runtime was launched from commit `a4704f91c6ee6844e0a1664ce559e53b8420211b`
and tree `e5d803efad9d38f7facf634b96d3e19501511fb9`. Later local evidence-capture
review changes do not alter that running worktree or its sealed artifacts.
