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
├── manifests.py          # Research manifest system
├── freeze.py             # Candidate freeze mechanism
├── candidate_registry.py # Append-only, hash-chained lifecycle evidence
├── paper_readiness.py    # Offline evidence verifier; never starts PAPER
├── exploratory.py        # Exploratory research runner
└── cli.py                # CLI entry points

tests/research_infra/
└── test_research_infra.py  # Core research infrastructure tests

research-data/              # Derived datasets, DQ catalogs, registry
research-artifacts/         # Research reports and manifests
docs/research-infra/        # This documentation
```

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

`CandidateRegistry` persists local append-only JSONL state with hash-chained events for the required lifecycle and evidence gates. External hypothesis-generation-only data may be recorded in retrospective research, but it cannot advance to robustness or candidate promotion. The readiness checker verifies the complete chain through `FROZEN`, binds it to the candidate freeze hash and research report, and reports `PAPER_ELIGIBLE` only when every evidence section passes. These APIs do not execute, authorize, or start PAPER. Candidate-generation families are not yet universally wired to the registry.

`SpotCostScenario` requires explicit maker/taker fee, slippage, latency, minimum notional, tick/lot size, and partial-fill assumptions. `conservative_sensitivity_grid` creates a deterministic Cartesian product from caller-supplied non-negative fee/slippage additions and latency values no lower than baseline. It does not fetch current exchange fees or model order-book depth, tick rounding, or fill probabilities; execution/backtest paths still need to consume the scenario before its assumptions affect results.

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
