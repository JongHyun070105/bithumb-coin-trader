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

Freeze the hypothesis list and cost assumptions before starting. The generic `research-batch` command runs a bounded strategy/parameter/cost/fold matrix over post-DQ development data. Its identity binds dataset bytes, code revision, versioned strategy/feature definitions and configs, seed, costs, and fold settings. It resumes completed folds, preserves attempts, and retries failures only with explicit `--retry-failed`. It compares cash, buy-and-hold, and randomized placebo controls. Do not substitute an individual `run_strategy_v*` command and label it a governed batch.

Expected artifacts: immutable batch/experiment IDs, attempt manifests, append-only fold/event logs, standardized metrics/results, completion hashes, baseline comparisons, and an aggregate report. Research execution remains offline and never consumes a final holdout.

## Step 6 — Test robustness

The generic runner supports rolling and expanding windows, purge/embargo, train-only fitting, frozen per-fold parameters, and standardized metrics. It executes explicit `SpotCostScenario` configurations and requires cost sensitivity and baseline/placebo evidence before candidate freeze. Freeze rejects incomplete folds, unsupported execution semantics, missing cost tiers, or non-positive conservative-through-extreme fold results. A missing fold, future-fit path, unresolved fill semantics, or an unverified zero-cost assumption remains a stop condition.

## Step 7 — Freeze a candidate

Only a candidate supported by the governed retrospective/robustness evidence can be frozen. Record candidate ID, hypothesis origin, dataset/source hashes, code revision, parameters, features, date boundaries, cost/latency assumptions, seed, metrics, baselines, and the evaluation/acceptance rules. External BitMEX/AOA material can only be tagged `HYPOTHESIS_GENERATION_ONLY`; it cannot meet a promotion gate.

The `candidate-freeze` CLI verifies experiment and dataset bindings, definition/config hashes, cost sensitivity, folds, baseline/placebo comparisons, and the append-only lifecycle evidence. It only freezes a candidate already selected through the registry; it does not select or promote one. Four daily strategy adapters currently run through the governed pipeline. Other inventoried families remain mapped and require adapters/retests. Do not call an existing per-family `PAPER_CANDIDATE` label a final approval.

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

`PAPER = NOT_STARTED` remains until a separately approved candidate is actually run prospectively. `PaperRuntime` joins a frozen daily strategy, normalized public candles/books, risk checks, conservative visible-depth taker fills, decimal accounting, and a durable SQLite journal with replay recovery. `paper-start` can process a finite caller-supplied normalized public-event JSONL file after every readiness gate passes; it does not start a websocket source or retrieve data. The command has not been invoked. Do not start PAPER in this preparation workflow.

The runtime requires explicit positive taker fee, slippage, and latency assumptions, tick/lot/minimum notional, and a declared depth-partial policy. Maker orders and probabilistic partial fills are unsupported. A durable sidecar halt latch survives a stale journal snapshot; recovery requires explicit acknowledgement. Start preflight binds a terminal PASS to its exact audit bytes and checks the candidate/readiness gates. A public websocket-to-normalized-event supervisor, provider provenance binding, and a process monitor remain engineering items.

## Step 10 — Monitor PAPER

`PaperRuntime.metrics()` exposes structured data age, strategy state, signals, orders, fills, rejections, positions, cash/reserves, equity, PnL, fees, slippage, turnover, drawdown, risk reasons, halt, journal health, restart count, and fill semantics. Event outcomes and runtime state are checksum-bound in SQLite. Existing dashboard/API views are not yet wired to this journal; dashboard integration remains engineering work. Stop on stale input, accounting mismatch, duplicate/out-of-order event divergence, untracked order, risk breach, or restart replay mismatch.

## Immediate post-30H pipeline and September-end milestone

Target sequence: `terminal-audit → reliability seal → reviewed source integration → qualified local dataset → governed retrospective batch → candidate report → explicit candidate selection/freeze → paper-readiness`. `scripts/post30h_orchestrator.py` runs this fail-stop sequence after reviewer-produced integration and dataset-qualification receipts are supplied. It invokes repository-owned offline commands only, stops on every nonzero result, and never invokes PAPER start. A websocket feed supervisor and dashboard integration remain SAFE_NOW engineering work. No terminal 30H result or candidate evidence is asserted by these tools.
