# Fresh 30H-v3 terminal adjudication — 2026-09-28

```text
OFFICIAL_30H_VERDICT = FAIL
RELIABILITY_PHASE = OPEN_FAIL_CLOSED
S3_REMOTE_VERIFICATION_REQUIREMENT = MANDATORY
S3_REMOTE_STATUS = FAIL (terminal witness says not uploaded); receipt mirror = NOT_VERIFIABLE; direct exact-object read = HTTP 403
IDEMPOTENCY_PASS_IMPACT = NOT_VERIFIABLE_UNDER_EXISTING_RULES
IDEMPOTENCY_DEFECT_TRIGGERED = NOT_VERIFIABLE
RELIABILITY_SEAL = NO PASS seal; this FAIL adjudication is hash-bound
DATASET_QUALIFIED = NO (qualification gate not entered)
RETROSPECTIVE_BATCH = NOT RUN
STRATEGIES_TESTED = 0
NO_CANDIDATE_SURVIVED = NOT_ASSESSED (no strategy evaluation ran)
PROSPECTIVE_HOLDOUT_CONSUMED = NO
CANDIDATE_FROZEN = NO
PAPER_READINESS = NOT_READY (terminal gate FAIL; no reviewed candidate evidence)
PAPER = NOT_STARTED
LIVE = DISABLED
PRIVATE_API = DISABLED
ALPHA = UNPROVEN
```

## Exact target and run outcome

| Field | Observed value |
| --- | --- |
| Run ID | `aws-validation-observability-30h-run-20260926T135000Z-v3` |
| Epoch | `aws-validation-observability-30h-20260926-20260926T135000Z-v3` |
| Runtime commit / tree | `22e06b9527798567e185fb0dd41dca3a448f444e` / `a44c9591045f3634cda1066d582927f277b79e2d` |
| Actual start / end | `2026-09-26T13:50:02.235147Z` / `2026-09-27T19:50:19.307611Z` |
| Elapsed / required | `108017.072 / 108000` seconds |
| Cohorts / slots | `29 / 29`; `2204 / 2204` PASS, 0 failed, 0 missing |
| Process results | collector 0; archive scheduler 0; publisher 0; finalization COMPLETE |
| Runtime health | NRestarts 0; final queue 0; unpersisted 0; writer errors 0 |
| Special coverage | both MANA slots recorded `VERIFIED_ZERO_EVENT` |

These successful lifecycle and coverage results do not override the terminal witness gate.

## Pre-existing rules and adjudication

The auditor was already part of the readiness commit `bfe16630e11ab5c425c34ad44e3cb93bd3b31099`; its source SHA-256 is `21440547adff372c3782f69566a5e3d40c991696291b9aa2b48d2f7db4ce649b`. The pre-run playbook (`docs/post-30h-execution-playbook.md`, introduced at `7bef84c216289975f2c97dbd2bbe561af7d90cf8`) says to stop on any `FAIL` or `NOT_VERIFIABLE`, requires both local receipt files and their S3 mirror in the offline bundle, and says missing finalization/immutability instrumentation intentionally prevents PASS.

### S3 gate

`S3_REMOTE_VERIFICATION_REQUIREMENT = MANDATORY`. The existing offline auditor requires a local/S3 receipt mirror comparison and requires the terminal witness to report a successful S3 upload. Its terminal witness predicate requires the sealed epoch and run ID, `CLEAN_SUCCESS`, successful service result, exit status `0`, and `s3_uploaded is True`. The actual complete guest witness had the exact run ID, `service_result=success`, `exit_status=0`, and `terminal_classification=CLEAN_SUCCESS`, but its epoch was `bitcoin-trader-30h`, `s3_key=null`, and `s3_uploaded=false`. That complete witness therefore makes `terminal_witness=FAIL`; it is not merely missing evidence.

The local/S3 receipt mirror equality check could not be evaluated because the exported S3 mirror was unavailable. Exact S3 object reads returned HTTP 403, and `S3_LIST` was not called. The offline code does not require a contemporaneous live `HeadObject` call; it does require the bundled mirror and the witness upload result. The observed `s3_uploaded=false` already fails the required witness check.

### Idempotency gate

`IDEMPOTENCY_PASS_IMPACT = NOT_VERIFIABLE_UNDER_EXISTING_RULES`. The auditor emits seven `NOT_VERIFIABLE` checks when a complete finalization trace is absent, including scheduler/finalizer retries, recovery, duplicate finalization, `closed_at_utc` stability, evidence-hash stability, and restart-idempotency path. With a complete trace it checks those conditions; it does not accept an absent trace as proof of zero retries.

The sealed runtime predates `48cfa0aa21327daa420638d275a3ca5314f6ad49`, which binds `closed_at_utc` to the frozen observation end. Since the trace is incomplete, whether that latent defect was exercised is `NOT_VERIFIABLE`. This uncertainty is independent of the terminal-witness failure; the witness failure is reproducible from the recorded fields and the existing predicate.

### Full-audit boundary

The exact complete terminal witness was evaluated against the pre-existing auditor predicate, and it deterministically fails. A complete offline audit bundle was not available: the local/S3 receipt mirror and complete finalization trace were missing. Therefore this record does not claim that the auditor CLI completed an end-to-end bundle run. The official aggregate is still `FAIL`: the pre-existing aggregator gives any `FAIL` precedence over `NOT_VERIFIABLE`.

## First failing component and root cause

The terminal witness was written at the run end (`2026-09-27T19:50:19.307611Z`); the first recorded adjudication failure time is `2026-09-27T19:50:19.534811Z`. The failing component is the systemd `ExecStopPost` terminal-witness hook.

In runtime commit `22e06b9`, the transient supervisor used the generic systemd unit prefix `bitcoin-trader-30h` as the witness `--epoch` and passed only `--data-dir` and `--run-id`. It did not pass the sealed epoch, S3 bucket/prefix, or `--allow-s3-write`. The witness therefore wrote a mismatched epoch and, by its default, reported no S3 upload. Static reproduction is deterministic. This is launch-argument wiring, not a collection or duration failure, and it is not evidence that the separate finalizer idempotency defect triggered.

## Evidence hashes observed on the guest

| Artifact | SHA-256 |
| --- | --- |
| identity | `96e24b4fcb95481ce44b9cbf39bc437e4ba570f508db747136915056271c6565` |
| sealed manifest | `4e1c21c1a37cb5550452467ba7fd74fdf0edfff924b46316da8c967e956ec9f6` |
| runtime JSON | `1a8d3e1800b8659cd541721cd0abdbef4968982cb379c7437c59f1eeae40a9b9` |
| terminal witness | `e855270dbb283cbc2f83ba3890dae4ba79ab41698ebbd3af24f83a73282908a9` |
| result | `c5c7ffec541a06bcfce9730ff82511bf160f9a004347d172d26c8b5e4a26fe98` |
| finalization summary | `9e21348646398ba53c9d43d899254e3e7349910849ad6cf8d070f2459fb950be` |

The guest observer was still active with S3 writes enabled at the time of the read-only audit. No runtime, AWS, S3, IAM, or Terraform mutation was performed here. Historical 2026-09-15 V3 remains its own `FAIL` record and was not reinterpreted. The earlier local manual-PASS report at `a35f00828c763746660e5d96d096223f9505daa4` remains preserved; this stricter, pre-existing terminal audit supersedes its conclusion for this exact run.

## Integration disposition

The integration branch carries the PR #18 readiness work, the `48cfa0a` finalizer idempotency fix as a distinct source commit, PR #19's default-live guard, and only the bounded BitMEX descriptive bootstrap slice. The broad PR #17 branch is not merged wholesale. No historical receipt or test-result file was edited.

- `closed_at_utc` fix: `INTEGRATE_NOW` as reliability source; it does not rewrite runtime `22e06b9` or this run's verdict.
- Readiness/PAPER gates: integrate; `PAPER` remains not started.
- Default-live guard: integrate; validated on the integration branch, with `REAL_ORDER_POSSIBLE_BY_DEFAULT=NO` for default invocation on that branch.
- BitMEX bounded descriptive bootstrap: `INTEGRATE_NOW` with `RESEARCH_ONLY` / `HYPOTHESIS_GENERATION_ONLY` role; no external dataset was imported or batch-tested.
- PR #17 inherited reliability/artifact history: `SUPERSEDED`; preserve historical records and do not merge wholesale.
- PR #17 provenance/custody, importer, and data-quality work: `DEFER` pending reliability closure and a source-bound local dataset.
- PR #17 contract/position reconstruction: `DEFER` because contract semantics and fee evidence remain unresolved.
- PR #17 market-context acquisition/as-of joins: `RESEARCH_ONLY` after reliability and provenance gates; not candidate evidence.
- PR #17 broad documentation: `DEFER`; no slice classified `DROP` because historical material remains preserved.

## Dataset and research lane

No current-public dataset was registered or qualified: `DATASET_ID=null`, `DATASET_QUALIFIED=NO`, DQ checks `NOT_RUN`. The pre-existing playbook permits this lane only after a terminal PASS. Accordingly, zero strategy families were assessed as eligible, zero strategies or experiments were tested, and no result is classified `REJECTED`, `WEAK`, `REQUIRES_MORE_EVIDENCE`, or `ELIGIBLE_FOR_USER_REVIEW`. `NO_CANDIDATE_SURVIVED` is `NOT_ASSESSED`, not a negative strategy finding. The historical Core + Satellite return claim remains `HISTORICAL_CLAIM_RETEST_REQUIRED`.

`PAPER_READINESS=NOT_READY`: the terminal reliability gate failed and no candidate evidence exists. No source dataset, S3 permission scope, or data-quality claim was changed or inferred. This PR does not start PAPER or public prospective observation.

Integration verification at commit `3119c4db1d7236ebb553c28373d2035e1f32bbee`: full suite `1629 passed, 2 skipped`; changed-scope Pyright had `0 errors, 0 warnings, 0 informations`; compileall and `git diff --check` passed. The test run did not rewrite or target `test-results/.last-run.json`.

## Next gates

1. Review and merge the separately prepared terminal-witness argument-binding fix only after source review; it does not alter this historical run.
2. Do not claim reliability closure or qualify a dataset from this `FAIL`. A new validation requires separate explicit human GO and a fresh identity; do not reuse or retry this consumed run.
3. Keep candidate count at zero, do not consume a prospective holdout, and keep PAPER/LIVE/private APIs disabled. After a separately authorized validation earns a terminal PASS, resume source-bound current-public DQ and governed retrospective research.
