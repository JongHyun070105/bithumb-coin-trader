# Microstructure research protocol V1

## Status and scope

This protocol governs research on public Bithumb, Binance, and Upbit microstructure data. Infrastructure validation is not alpha evidence.

- ALPHA UNPROVEN
- PAPER NOT STARTED
- LIVE DISABLED
- PRIVATE API DISABLED

## Infrastructure exit gate

A fresh unattended 45m run must pass process, archive, restore, and evidence-contract gates. Only after independent review may one 30h cross-date soak start. The 30h exit criteria must be frozen before launch and must cover actual UTC rollover, same-hour recurrence on different dates, autonomous repeated archive, resource accumulation, reconnect evidence, and natural finalization. Expected cohorts and evidence counts are derived from actual timestamps. A delay beyond the stated buffer is a failure, not grounds to extend the run. A 30h PASS closes infrastructure validation; no preventive 48h, 72h, or week-long soak is added absent a newly observed defect.

## DEV semantics

Validation data with acceptable DQ may enter DEV because it has already been observed and is not a holdout. Initial DEV work may inspect features, distributions, exploratory predictive metrics and PnL, execution assumptions, and candidate comparisons. DEV results are never described as OOS or confirmatory evidence.

Research order is: DQ; feature pipeline; minimum viable taker simulator; univariate exploration; at least 2-4 weeks of accumulation; interpretable rule baselines; purged time-ordered walk-forward; cost/latency stress; candidate selection; preregistration/freeze; prospective holdout. ML follows adequate data and baseline evidence.

The taker-first simulator uses signal timestamp -> configured latency -> future observed order book -> spread crossing -> depth walk -> execution VWAP -> fees. Initial latency and cost grids are DEV assumptions, not fill calibration. Maker queue modeling is deferred; real fill calibration begins only in paper trading.

## Trial ledger and freeze

Use append-only JSONL for trials and JSON manifests for freezes/holdouts. Do not build a database, service, or dashboard for the initial cycle.

Trial records contain:

`trial_id`, `parent_trial_id`, `created_at`, `features`, `parameters`, `execution_assumptions`, `dataset_cutoff`, `dev_metrics`, `decision`, `freeze_id`.

Threshold/feature changes, candidate-selecting latency assumptions, and freeze attempts are alpha trials. Identical-spec reruns and environment-error retries are operational events, not extra alpha trials. Failed freeze attempts remain in lineage.

Freeze manifests contain:

`freeze_id`, `candidate_trial_id`, `generation_id`, `freeze_timestamp`, `spec_hash`, `dev_cutoff`.

Holdout manifests contain:

`generation_id`, `holdout_start`, `checkpoint_1`, `checkpoint_2`, `status`, `opened_at`, `abort_reason`.

Allowed holdout states are `PLANNED`, `SEALED`, `READY`, `OPENED`, `INCONCLUSIVE`, and `ABORTED`.

## Global sealed epoch

The time window, not an individual strategy, is globally sealed. Candidate A/B/C are frozen against one DEV cutoff and evaluated together when that future epoch opens once. At most one epoch is active. Windows never overlap. Candidates completed while an epoch is sealed enter `NEXT_GENERATION_QUEUE` and may start only after the active epoch ends, in a new non-overlapping future window. This deliberate throughput cost reduces cross-generation contamination.

The existing 4320-hour candle holdout remains `SEALED_4320_HOURS_UNTOUCHED`. It lacks microstructure modalities and is never reused as the V9 holdout.

## Blind readiness and checkpoints

Readiness code may inspect only DQ, elapsed preregistered checkpoints, and independent-entry opportunity sufficiency. It returns only `READY`, `NOT_READY`, or `ABORT`; it does not expose PnL, Sharpe, win rate, drawdown, returns, direction, charts, regimes, or exact outcomes. Avoid exposing exact signal count when a status is sufficient. Checks occur only at preregistered checkpoints.

The independent-event definition, required N, and bootstrap block length are:

**TO BE CALIBRATED ON DEV BEFORE FIRST PROSPECTIVE HOLDOUT.**

All three must be preregistered before sealing the first prospective holdout. Do not invent their values from sealed data. Initial inference uses the trial ledger, purged walk-forward, cost/latency stress, block bootstrap, and DSR; WRC/SPA and PBO are added only if the candidate family becomes large enough to justify them.

## Sealed-data access and leakage

During a sealed epoch, research access to new sealed data is prohibited: no new candidate tests, feature performance, regime analysis, charts, PnL, or future returns. Collection, archive, DQ, integrity, and blind readiness are allowed. Tooling and ideas may be developed only on pre-cutoff DEV.

The seal blocks direct research-data leakage but cannot blind researchers to public price moves, news, social media, or market-wide events. This is a structural limitation. External information must not be used to modify a frozen candidate. If a structural market or exchange change makes modification necessary, abort the epoch, freeze a new specification, and use a new generation.

## Terminal semantics

`INCONCLUSIVE` means the final checkpoint lacks preregistered evidence sufficiency. Do not extend the epoch, open performance results, or interpret it as PASS/FAIL. End the epoch permanently. A retry requires a new `freeze_id`, `generation_id`, non-overlapping future window, and ledger entry, even if the specification is unchanged.

`ABORTED` is outcome-independent invalidation caused by collector defect, critical DQ failure, contamination, structural API/schema change, IAM/archive failure, timestamp corruption, or holdout-access violation. Preserve cause evidence and do not open performance results. Data seen during diagnosis cannot be reused as holdout. The same frozen spec may be tried in a new generation only if the abort was outcome-independent and performance remained unobserved. Record it in the operational abort ledger.

## Paper gate

Paper trading cannot start from infrastructure success or DEV performance. It requires a preregistered frozen candidate, a qualified prospective holdout opened once, adequate outcome-independent readiness, a passing confirmatory evaluation under realistic cost/latency stress, and an explicit separate paper-entry decision. Live trading and private APIs remain outside this protocol and require separate authorization and safety review.
