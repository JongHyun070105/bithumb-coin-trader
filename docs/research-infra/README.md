# Microstructure Research Infrastructure

## Architecture

```
Historical Source
      ↓
Dataset Registry (registry.py)
      ↓
Dataset Adapter (adapters.py)
      ↓
Canonical Event Layer (canonical_events.py)
      ↓
Data Quality / Coverage Layer (dq.py)
      ↓
Time Alignment Layer (time_align.py)
      ↓
Feature Engine (features.py)
      ↓
Label Engine (labels.py)
      ↓
Hypothesis Runner (hypotheses.py + exploratory.py)
      ↓
Execution Simulator (execution.py)
      ↓
Chronological Evaluator (evaluation.py)
      ↓
Research Manifest / Freeze (manifests.py + freeze.py)
      ↓
Candidate Lifecycle Evidence (candidate_registry.py)
      ↓
Fail-closed PAPER Readiness (paper_readiness.py)
      ↓
Human-readable Research Report
```

## Package Layout

```
src/bithumb_coin_trader/research_infra/
├── __init__.py           # Package init
├── registry.py           # Dataset registry with role enforcement
├── adapters.py           # Raw JSONL → CanonicalEvent adapters
├── canonical_events.py   # Canonical event model
├── dq.py                 # Data quality / coverage layer
├── time_align.py         # Time alignment / no-lookahead
├── features.py           # Feature engine (market state, microprice, depth, trade flow)
├── labels.py             # Label engine (future returns, direction)
├── execution.py          # Execution simulator wrapper
├── hypotheses.py         # Hypothesis registry (H1-H5)
├── evaluation.py         # Chronological evaluation framework
├── costs.py              # Explicit Bithumb spot assumptions and cost sensitivity grid
├── definition_registry.py # Append-only, versioned feature/strategy definitions
├── result_schema.py      # Standardized per-fold result contract
├── walk_forward_runner.py# Train-only rolling/expanding evaluation
├── batch.py              # Content-addressed, resumable experiment batches
├── manifests.py          # Research manifest system
├── freeze.py             # Candidate freeze mechanism
├── candidate_registry.py # Append-only, hash-chained lifecycle evidence
├── paper_readiness.py    # Offline evidence verifier; never starts PAPER
├── exploratory.py        # Exploratory research runner
└── cli.py                # CLI entry points

src/bithumb_coin_trader/
├── paper_runtime.py      # Caller-fed local PAPER runtime
├── paper_engine.py       # Decimal portfolio/order lifecycle and accounting
├── paper_journal.py      # SQLite journal, replay, and crash recovery
└── risk_engine.py        # Shared ALLOW/REJECT/HALT policy interface

tests/research_infra/
└── test_research_infra.py  # Core research infrastructure tests

research-data/              # Derived datasets, DQ catalogs, registry
research-artifacts/         # Research reports and manifests
docs/research-infra/        # This documentation
```

Each batch writes an append-only `definition-registry.jsonl` by default under
its output root. Strategy and feature definitions carry a version, source hash,
schema, and definition hash; the experiment identity binds those hashes and
each concrete config hash. Reusing a definition key with changed implementation
is rejected; increment the definition version when implementation changes.
The current feature boundary is completed OHLCV candle history. Four existing
daily candidates are executable through the governed CLI; the broader family
inventory does not imply that every historical strategy has an adapter.

## Dataset Scientific Roles

| Dataset | Role | Exploration | Candidate | Holdout |
|---------|------|-------------|-----------|---------|
| Old72H | DEVELOPMENT_EXPLORATORY | YES | NO | NO |
| V2 | DEVELOPMENT_EXPLORATORY | YES | NO | NO |
| V4 | QUARANTINED | NO | NO | NO |
| Fresh45 | INFRA_VALIDATION_ONLY | YES | NO | NO |

## DQ Semantics

Coverage states:
- `DATA_PRESENT` — Data exists and was observed
- `VERIFIED_ZERO_EVENT` — Immutable evidence proves zero events
- `UNKNOWN_MISSING` — No data and no proof of zero events (MUST exclude)
- `INCOMPLETE` — Partial data
- `CORRUPT` — Data exists but fails validation
- `UNVERIFIED` — Not yet validated
- `UNAVAILABLE` — Source not accessible

**Missing ≠ zero-event.** V2's 8 known missing slots are classified as `UNKNOWN_MISSING`.

## Timestamp Contract

- **Ordering timestamp**: `local_write_timestamp` (when event was persisted)
- **Feature availability**: At or before ordering timestamp
- **Cross-exchange join**: Backward/as-of on ordering timestamp (no forward lookahead)
- **Label construction**: Uses future mid-price observations (separate from features)

## Hypotheses

| ID | Description | Feature | Target | Expected |
|----|-------------|---------|--------|----------|
| H1 | Orderbook imbalance predicts direction | depth_imbalance_l1, qi_l5 | 5s return | positive |
| H2 | Trade-flow imbalance predicts continuation | ati_30s, signed_volume_30s | 10s return | positive |
| H3 | Microprice displacement predicts movement | microprice_bias_bps | 5s return | positive |
| H4 | Binance/Upbit lead Bithumb | cross_exchange_return_diff | 10s return | positive |
| H5 | Cross-exchange basis shocks revert | cross_exchange_basis | 30s return | uncertain |

## Execution Simulator

- Fee regimes: live_zero_fee (0%/5bps), normal_fee (0.25%/5bps), stress_2x, stress_3x
- Taker execution with depth walking
- Passive fills disabled by default (conservative)
- All assumptions stored in manifests

## Candidate Freeze

When a hypothesis shows promising exploratory results:
1. Freeze: hypothesis, features, parameters, execution assumptions, code commit
2. Generate SHA-256 freeze hash
3. Future holdout must match frozen definition exactly
4. Any deviation detected and reported

## Future V4 Integration

After V4 completes and receives infrastructure PASS:
1. Update V4 role from QUARANTINED to PROSPECTIVE_RESEARCH
2. Run same pipeline: adapter → DQ → features → labels → evaluation
3. Compare against frozen candidates from Old72H/V2 exploration
4. No code changes needed — same pipeline handles all datasets

## CLI

```bash
# Register datasets
python -m bithumb_coin_trader.research_infra.cli datasets register

# List datasets
python -m bithumb_coin_trader.research_infra.cli datasets list

# Build DQ catalog
python -m bithumb_coin_trader.research_infra.cli dq build --dataset fresh45

# DQ report
python -m bithumb_coin_trader.research_infra.cli dq report --dataset fresh45

# List hypotheses
python -m bithumb_coin_trader.research_infra.cli hypotheses list

# Build canonical data
python -m bithumb_coin_trader.research_infra.cli build \
  --dataset "$DATASET_ID" \
  --data-root "$OFFLINE_RAW_DATA_ROOT" \
  --output-dir "$NEW_OUTPUT_DIRECTORY"

# Verify an assembled, hash-bound PAPER readiness bundle (does not start PAPER)
python -m bithumb_coin_trader.research_infra.cli paper-readiness \
  --evidence-dir "$PAPER_READINESS_EVIDENCE_DIR" \
  --output-dir "$NEW_PAPER_READINESS_REPORT_DIR"

# Generate report
python -m bithumb_coin_trader.research_infra.cli report
```

The build writes `events.jsonl` and a source/output-hash `manifest.json`. It refuses final holdouts and existing output directories. Its status is `BUILT_DQ_NOT_RUN`; run the DQ build/report separately before treating the dataset as research-ready.

`CandidateRegistry` persists local append-only JSONL state with hash-chained events for the required lifecycle and evidence gates. External hypothesis-generation-only data may be recorded in retrospective research, but it cannot advance to robustness or candidate promotion. `candidate-freeze` now verifies the complete batch, exact dataset-manifest/content bindings, result hashes, candidate lifecycle evidence, baseline/placebo comparisons, fold coverage, and cost sensitivity before writing an immutable freeze artifact and appending `FROZEN`. It only accepts the four governed daily strategy adapters currently wired to `research-batch`; it does not select or promote a candidate. The readiness checker verifies the complete chain through `FROZEN`, binds it to the candidate freeze hash and research report, and reports `PAPER_ELIGIBLE` only when every evidence section passes. These APIs do not execute, authorize, or start PAPER.

New single-market candle experiments enter through `research_infra.backtesting.SpotResearchBacktester` and its `RebalanceBacktester` engine. `research-batch` uses this path directly. It charges configured taker fees, adverse slippage, minimum notional, tick rounding, and lot rounding. It executes at the next candle open and has no order-book depth; positive latency or partial-fill assumptions are returned as `UNSUPPORTED`, so freeze rejects those runs. Maker fills are not modeled by this candle path. `SpotCostScenario` still requires explicit maker/taker fee, slippage, latency, minimum notional, tick/lot size, and partial-fill assumptions. `conservative_sensitivity_grid` creates a deterministic Cartesian product from caller-supplied non-negative fee/slippage additions and latency values no lower than baseline; it does not retrieve current exchange parameters. See [the engine comparison and authority decision](../BACKTEST_ENGINE_COMPARISON_2026-09-27.md) before using a specialized legacy evaluator.

The bounded batch command consumes immutable dataset manifests and hypotheses, creates content-derived run IDs, writes per-attempt manifests/logs/metrics, resumes completed folds, and requires explicit `--retry-failed` for failed attempts. Example:

```bash
python -m bithumb_coin_trader.research_infra.cli research-batch \
  --dataset-manifest "$DATASET_MANIFEST" \
  --hypotheses "$HYPOTHESES" \
  --cost-grid conservative \
  --walk-forward \
  --folds 5 \
  --window-mode EXPANDING \
  --purge-seconds "$PURGE_SECONDS" \
  --embargo-seconds "$EMBARGO_SECONDS" \
  --output "$NEW_RESEARCH_OUTPUT"
```

After a candidate has already reached `CANDIDATE` with lifecycle evidence bound to the batch, the freeze operation is:

```bash
python -m bithumb_coin_trader.research_infra.cli candidate-freeze \
  --experiment "$EXPERIMENT_ID" \
  --research-root "$RESEARCH_OUTPUT" \
  --candidate-registry "$CANDIDATE_REGISTRY" \
  --output "$RESEARCH_OUTPUT/frozen-candidates/$CANDIDATE_ID.json"
```

Freeze requires ordered `base`, `conservative`, `stress`, and `extreme` tiers; completed folds with no unsupported execution semantics; positive net return on every fold at conservative through extreme tiers; and complete cash, buy-and-hold, and randomized-placebo comparisons. This is a deliberately restrictive engineering gate, not proof of alpha. Current candidate-selection acceptance criteria still need a reviewed lifecycle decision before the command can freeze anything. The command only writes local research evidence and never starts PAPER.

## Local PAPER engine and post-30H orchestration

`PaperRuntime` accepts caller-fed normalized public candles and visible order-book
snapshots. It verifies a frozen candidate, applies the shared risk engine, and
uses conservative visible-depth taker fills with explicit costs, fees, latency,
minimum notional, tick/lot rules, and a declared depth-partial policy. Event,
order, fill, account, and recovery state are checksum-bound in a local SQLite
journal. Maker orders and probabilistic partial fills are unsupported. A durable
sidecar halt latch survives restart and requires explicit recovery acknowledgement.
This library does not start a feed process or PAPER.

`reliability-seal` writes a new local seal only from a terminal-audit PASS and
binds it to the exact report bytes. `paper-start` currently checks the seal,
candidate freeze, and readiness bundle, then prints `PAPER=NOT_STARTED`; it does
not construct or start a runtime. `scripts/post30h_orchestrator.py` runs the
terminal audit, reliability seal, verified source-integration and dataset-DQ
receipts, research batch, evidence-only candidate report, existing candidate
freeze, and PAPER readiness in fail-stop order. It never merges source, selects
a candidate, connects to cloud services, or starts PAPER. Inspect required
inputs with `python scripts/post30h_orchestrator.py --help`; do not invoke it
until post-30H evidence and the reviewer receipts are available.

The isolated synthetic throughput check is:

```bash
PYTHONPATH=src .venv/bin/python scripts/benchmark_research_pipeline.py \
  --batch-size 10 --candles 1000 --folds 5 \
  --output /tmp/research-pipeline-benchmark.json
```

It times one backtest, one walk-forward grid, an `N`-experiment batch, and
recovery from a crash after atomic accounting. It uses temporary synthetic
candidates/data only, requires a clean committed checkout, and makes no
performance assertions. Its output is diagnostic and is not research evidence.

## Tests

```bash
python -m pytest tests/research_infra/ -v
```

Core tests cover:
- Dataset role enforcement (V4 quarantine, exploration-only)
- V2 known missing slots (UNKNOWN_MISSING)
- Missing ≠ zero-event invariant
- Timestamp ordering
- No-lookahead as-of joins
- Feature windows (no future data)
- Label horizon behavior
- DQ exclusion
- Execution assumptions
- Chronological splits (no random splits)
- Manifest reproducibility
- Candidate freeze integrity
- V4 quarantine gate

## Scientific State

- **Old72H**: DEVELOPMENT / EXPLORATORY ONLY
- **V2**: DEVELOPMENT / EXPLORATORY ONLY
- **V4**: RUNNING / QUARANTINED — DO NOT USE
- **ALPHA**: UNPROVEN
- **PAPER**: NOT STARTED
- **LIVE**: DISABLED
- **PRIVATE API**: DISABLED
