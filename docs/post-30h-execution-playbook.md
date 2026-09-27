# Day-After-30H Execution Playbook

This runbook begins after Fresh 30H-v3 ends naturally. It does not authorize runtime changes, new validation, private API use, or trading. Keep the original v3 evidence immutable. Stop immediately on any FAIL or `NOT_VERIFIABLE`; never convert a missing artifact into a pass.

## Step 1 — Export and audit terminal evidence

Prepare a **local offline bundle** from the run's already-sealed evidence, in a directory that will not be used for report output. Include every file named by `audit-bundle.json`, both local receipt files and their S3 mirror, the runtime/identity seal, supervisor result, actual-start, systemd terminal properties, terminal witness, collector lifecycle/metrics, redundancy metrics, complete finalization trace, two-time receipt observations, and the evidence hash index. The exporter/operator must preserve source bytes and record the run ID/epoch. This checkout does not yet have a provider-independent S3 exporter; use the project's approved evidence-export procedure and do not SSH/SSM into the collector host for this audit.

Bundle shape (paths are relative to the bundle root):

```json
{
  "schema_version": 1,
  "paths": {
    "identity": "sealed/identity.json",
    "runtime": "sealed/runtime.json",
    "actual_start": "terminal/actual-start.json",
    "result": "terminal/result.json",
    "systemd": "terminal/systemd.json",
    "terminal_witness": "terminal/terminal-witness.json",
    "collector_lifecycle": "terminal/collector-lifecycle.json",
    "collector_metrics": "terminal/collector-metrics.json",
    "redundancy_metrics": "terminal/redundancy-metrics.json",
    "receipt_observations": "terminal/receipt-observations.json",
    "finalization_trace": "terminal/finalization-trace.json",
    "evidence_hash_index": "terminal/evidence-hash-index.json"
  },
  "directories": {
    "local_receipts": "terminal/archive-receipts",
    "s3_receipts": "terminal/s3-receipts"
  }
}
```

Command, from the repository root:

```sh
python scripts/audit_fresh_30h_v3_terminal.py \
  --evidence-dir "$FRESH_30H_V3_OFFLINE_BUNDLE" \
  --output-dir "$FRESH_30H_V3_AUDIT_OUTPUT"
```

Expected: `OVERALL_STATUS=PASS`, plus a JSON and Markdown report in a new timestamped output directory outside the bundle. Exit code `0=PASS`, `1=FAIL`, `2=NOT_VERIFIABLE`, `2` also if audit cannot run. `FAIL` or `NOT_VERIFIABLE` is a STOP; preserve the report and route a read-only evidence review. The auditor checks runtime commit/tree, timestamps/duration, component exits, systemd result/restarts, witness and S3 upload, collector lifecycle/final manifest flush, queue/drops/backpressure/writer/unpersisted counters, active-active config and metrics, every qualifying cohort and feed slot, local/S3 receipt hashes, receipt immutability, evidence hashes, retries/recovery, repeated finalization, `closed_at_utc` and evidence hash differences, and exposure to the known restart-idempotency path. Missing finalization/immutability instrumentation intentionally prevents PASS.

Artifact: `terminal-audit.json`, `terminal-audit.md`. The audit script never writes inside its evidence directory and never connects to AWS or an exchange.

## Step 2 — Make the terminal decision

```sh
python -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["overall_status"]); sys.exit(0 if d["overall_status"] == "PASS" else 1)' \
  "$FRESH_30H_V3_AUDIT_JSON"
```

Expected: `PASS` and exit zero. On `FAIL`, retain the historical result and proceed only to separately scoped remediation/revalidation planning; on `NOT_VERIFIABLE`, collect missing existing evidence read-only if possible. Do not launch or restart a validation run from this playbook. No dataset/candidate work should claim reliability PASS before this step.

## Step 3 — Integrate reliability source after PASS

Start from an explicitly selected `origin/main` SHA in a new worktree. Before applying any PR, inspect `git diff --name-status` and `git diff --stat` and compare source commits with the sealed runtime tree. The current runtime is `22e06b9`; the fix commit `48cfa0a` is its direct child. Do not merge the older #12→#13→#14→#16 chain as a block; compare changes against the v3 source first. Keep #12's Fresh 30H-v2 FAIL. Split dashboard changes and PR #17 external research from the runtime patch. Integrate the finalizer fix only after sealing the run's original terminal report and preserving both its parent and exact commit.

Pre-merge commands in the integration worktree:

```sh
git status --short
git diff --check
git diff --stat "$INTEGRATION_BASE_SHA"..."$INTEGRATION_HEAD_SHA"
git diff --name-status "$INTEGRATION_BASE_SHA"..."$INTEGRATION_HEAD_SHA"
.venv/bin/pytest -q -p no:cacheprovider --basetemp=/tmp/pre-merge-reliability tests/test_closed_hour_finalizer.py tests/test_archive_scheduler.py tests/test_feed_hour_coverage.py tests/test_fresh_6h_v5r1_replay.py
```

Expected: clean isolated worktree, reviewed exact file list, zero diff whitespace errors, all focused reliability tests pass. Stop if the integration changes the sealed v3 evidence or if any failure is unexplained. Preserve a before/after evidence hash inventory and attach the exact runtime and fix SHAs to the review.

Post-merge commands:

```sh
git diff --check
.venv/bin/pytest -q -p no:cacheprovider --basetemp=/tmp/post-merge-reliability tests/test_closed_hour_finalizer.py tests/test_archive_scheduler.py tests/test_feed_hour_coverage.py tests/test_fresh_6h_v5r1_replay.py
git status --short
```

Expected: focused regression suite passes and worktree is clean after committing. Any new integration branch/PR should have a source-only reliability commit and an append-only terminal evidence commit; never rewrite the historical FAIL/PASS record.

## Step 4 — Build the current public dataset

The readiness branch now has an offline `research build` implementation that streams supported local JSONL inputs into canonical JSONL and a source/output-hash manifest. It does not establish DQ or dataset eligibility. First select the exact qualifying 30H-derived public data and update a dataset registration with immutable run/epoch, source roots, time bounds, feed universe, schema version, raw-file hashes, and DQ role. Never register runtime validation data as a candidate holdout. Run raw integrity and DQ tools against an **exported local copy** only; emit separate manifests and preserve source bytes.

Command, using only a local export selected after the terminal audit PASS:

```sh
python -m bithumb_coin_trader.research_infra.cli build \
  --dataset "$QUALIFIED_PUBLIC_DATASET_ID" \
  --data-root "$FRESH_30H_V3_LOCAL_RAW_COPY" \
  --output-dir "$NEW_CANONICAL_BUILD_DIR"
```

Expected: `events.jsonl` and `manifest.json` under a new output directory, with source-file SHA-256s, canonical event SHA-256, code revision and `build_status=BUILT_DQ_NOT_RUN`. The builder refuses holdout-role inputs, empty/unadaptable sources, output inside the raw tree and an existing output directory. It re-hashes sources after streaming and refuses publication if source bytes changed during the build. This is only canonicalization: `DATA_READY` remains false until the separate DQ build/report passes and is bound to the same source/build hashes.

Expected artifact: content-hashed source manifest, canonical dataset manifest, DQ catalog and exclusion counts. Any missing slot, ambiguous source attribution, schema conflict, hash mismatch, or DQ failure stops downstream research.

## Step 5 — Run a retrospective batch

Freeze the hypothesis list and cost assumptions before starting. For each run, bind dataset hash, code commit, full strategy/config/feature hashes, random seed, chronological range and result manifest. Run only the post-DQ development data. Compare cash and buy-and-hold where meaningful, plus prespecified placebo/randomized baselines. Existing strategy scripts are separate and do not provide resumable manifest-hash deduplication as a unified batch runner.

Command: **no generic batch command exists yet**. Do not substitute an individual `run_strategy_v*` command and label it the full governed batch. Until the batch runner exists, execute a reviewed finite matrix in a separate worktree with unique output directories and capture failures as failed trials.

Expected artifacts: immutable `experiment_id`, manifest, `results.json`, `metrics.json`, logs, run status including failures, and comparison table. Same canonical inputs must produce the same logical outputs or identify the source of controlled nondeterminism.

## Step 6 — Test robustness

Run the registered cost scenarios, rolling and expanding chronological folds, label-horizon purging, required embargo, parameter sensitivity frozen per fold, leave-one-asset-out/regime/time-slice checks, and placebo baselines. `SpotCostScenario` and `conservative_sensitivity_grid` now validate explicit spot fees, slippage, latency, minimum notional, tick/lot size, and partial-fill semantics; strategy/backtest engines do not yet all consume this model. `create_chronological_folds` accepts separate `purge_s` and `embargo_s` buffers and rejects unsorted timestamps or invalid windows; this produces safe fold boundaries, but it does not by itself enforce train-only feature fitting or aggregate a generic strategy runner. Reports must include per-fold and aggregate net return, CAGR, drawdown, Sharpe/Sortino, Calmar, profit factor, win rate, expectancy, turnover/trades, exposure, tail/worst-period loss, fees and slippage. Acceptance values must be independently set before inspecting the final evaluation set; if no project threshold exists, report the metric vector without fabricating one.

Command: existing family-specific walk-forward/cost scripts are available, but no generic all-hypothesis robustness command. A missing fold, future-fit path, unresolved fill semantics, or strategy result that depends on an unverified zero-fee assumption is a stop condition.

## Step 7 — Freeze a candidate

Only a candidate supported by the governed retrospective/robustness evidence can be frozen. Record candidate ID, hypothesis origin, dataset/source hashes, code revision, parameters, features, date boundaries, cost/latency assumptions, seed, metrics, baselines, and the evaluation/acceptance rules. External BitMEX/AOA material can only be tagged `HYPOTHESIS_GENERATION_ONLY`; it cannot meet a promotion gate.

Command: lifecycle evidence can be recorded through `research_infra.candidate_registry.CandidateRegistry` in code, and the readiness bundle verifier checks the complete hash chain through `FROZEN`. There is still no universal `candidate-freeze` CLI and family candidate paths are not wired to the registry. Do not call an existing per-family `PAPER_CANDIDATE` label a final approval. If the central event chain and its research/freeze hashes do not verify, stop and report `CANDIDATE_FROZEN=false`.

Artifact: candidate freeze manifest plus hash and reviewed lifecycle transition record. No final prospective data is opened to prepare this artifact.

## Step 8 — Paper readiness

Prepare an evidence directory containing `paper-readiness-bundle.json`. The bundle points to seven JSON artifacts (`dataset`, `research`, `candidate`, `risk`, `execution`, `observability`, and `security`), each with a path relative to the evidence directory and an exact SHA-256. Dataset and research artifacts must cross-bind by manifest hash; the frozen candidate must bind the research report and include a verified hash chain through `FROZEN`; external-only roles fail. The command validates event-file bytes, DQ, experiment reproducibility/cost/walk-forward/placebo evidence, freeze hash, complete risk limits, restart/idempotency/cancel controls, required PAPER metrics, a zero-secret scan, and explicit private/LIVE disablement.

Command:

```sh
python -m bithumb_coin_trader.research_infra.cli paper-readiness \
  --evidence-dir "$PAPER_READINESS_EVIDENCE_DIR" \
  --output-dir "$NEW_PAPER_READINESS_REPORT_DIR"
```

Expected artifacts: `paper-readiness.json` and `paper-readiness.md`, with every check reported `PASS`, `FAIL`, or `NOT_VERIFIABLE`. Exit `0` means all required conditions passed; `1` means at least one explicit gate failed; `2` means evidence is missing/unverifiable or the report cannot be written. Today there is no production readiness bundle, so the command remains fail-closed. It never starts PAPER. The existing `readiness.py` evaluates **live** readiness and demands API-key presence plus a read-only account probe; do not invoke it for paper readiness or configure credentials to make it pass.

## Step 9 — Start prospective PAPER

`PAPER = NOT_STARTED` remains until a separately approved candidate is actually run prospectively. The project has no unified supported paper-start command spanning the persistent daily ledger and event-driven order engine. Do not start paper in this preparation workflow. Before a future start, require a separate explicit authorization, public market data only, a fresh state directory, frozen manifest hash, explicit cost/risk configuration and no private keys.

Command: **not available as a governed prospective start command**. A future command must print the candidate/dataset/config hashes and selected simulator before starting; reject LIVE/private flags and fail if the paper journal is not recoverable. Expected artifacts include orders, acknowledgements, partial/full fills, cancellations, positions, cash, realized/unrealized PnL, fees, risk decisions, errors and restart reconciliation. `paper-readiness` only verifies evidence and never starts paper. `paper_engine.py` validates event/order bindings, rejects duplicate or overfilled events and rolls back multi-level fills atomically. `PaperEventJournal` provides a local SQLite snapshot/event ledger with atomic commits, persisted idempotency, checksum verification and restart recovery; it has not yet been integrated with the public market-data pipeline, stale-order reconciliation, risk, or a governed start command. Persistent daily `paper.py` still does not model partial order events.

## Step 10 — Monitor PAPER

Monitor strategy state, data freshness, signals, orders, fills, positions, cash/PnL/fees/drawdown, each risk gate, process restarts and errors. Use the existing dashboard/API after wiring it to the chosen paper journal; do not build a parallel monitoring stack. Stop on stale input, accounting mismatch, duplicate/out-of-order event divergence, untracked order, risk breach, or restart replay mismatch. Preserve all reports/logs for audit.

Command: dashboard paper views are not yet connected to a single durable prospective order journal. Expected artifact: timestamped monitoring report tied to candidate, dataset, execution and risk hashes. Any manual reconciliation is a stop, not a silent state correction.

## Immediate post-30H pipeline and September-end milestone

Target sequence: `terminal-audit → reliability integration → dataset build/DQ → governed retrospective batch → robustness → candidate freeze → paper-readiness → separately authorized paper-start → monitoring`. The September-end engineering milestone is not reached until reliability evidence is sealed, research build/batch/freeze gates are executable, one paper engine is restart-safe, risk/observability are wired, and live/private remains disabled. This checkpoint adds the offline terminal auditor, multi-asset/composite backtest guards, stable experiment identity, purged walk-forward boundaries, atomic/idempotent paper fills and a durable local paper journal. The integrated dataset, batch, candidate, risk, monitoring and readiness/start nodes remain engineering blockers above.
