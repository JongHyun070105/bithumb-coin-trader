# Governed local research runner

New single-market, long-only spot research uses the authoritative interface in
`research_infra.backtesting.SpotResearchBacktester`. It applies the shared
`SpotCostScenario` through `RebalanceBacktester`; the full engine comparison and
known limitations are recorded in
[`BACKTEST_AUTHORITY_AND_COST_CONTRACT.md`](BACKTEST_AUTHORITY_AND_COST_CONTRACT.md).
The result record is versioned in `research_infra.result_schema.ExperimentResult`.
Metrics that an engine cannot calculate are `null`; they must not be replaced by
estimates presented as measured values.

## Walk-forward contract

`research_infra.walk_forward_runner.run_walk_forward` supports rolling and
expanding training windows. Each fold calls `fit(training_candles)` once; the
validation partition is never passed to fit. Predictions receive a causal
history ending at the current completed candle. Purge and embargo are applied
by chronological fold generation and checked again at the train/validation
boundary. The runner freezes a canonical fitted-parameter manifest before
validation and rejects changes during evaluation. Each fold is run under every
explicit cost scenario.

Latency greater than zero and unsupported partial-fill assumptions are bound
to each result. Candle-only engines mark those runs `UNSUPPORTED`; they do not
claim to model queue position, intrabar latency, or partial fills.

## Batch input files

The dataset manifest is a JSON object with exactly these fields:

```json
{
  "schema_version": 1,
  "dataset_id": "old72h-candles-v1",
  "dataset_role": "DEVELOPMENT_EXPLORATORY",
  "allowed_for_candidate_selection": true,
  "integrity_status": "PASS",
  "provenance_confidence": "PROVEN",
  "data_path": "candles.csv",
  "data_sha256": "<sha256 of the exact CSV bytes>",
  "candle_count": 1000
}
```

The CSV must use the repository's normalized candle header:
`market,timestamp,open,high,low,close,volume`. The resolved file must remain
under the manifest directory and its exact bytes and candle count are verified.
Only `DEVELOPMENT_EXPLORATORY` data with candidate-selection permission and
`PASS`/`PROVEN` provenance is accepted. Prospective, quarantined, external, and
frozen-holdout roles are rejected.

The hypotheses file contains a bounded list of strategy configurations,
parameter sets, and named explicit costs:

```json
{
  "schema_version": 1,
  "cost_grids": {
    "conservative": [
      {
        "name": "base",
        "maker_fee_bps": 5,
        "taker_fee_bps": 10,
        "slippage_bps": 5,
        "latency_ms": 100,
        "minimum_order_notional": 5000,
        "tick_size": 1,
        "lot_size": 0.00000001,
        "partial_fill_probability": null,
        "partial_fill_status": "UNSUPPORTED"
      },
      {
        "name": "stress",
        "maker_fee_bps": 10,
        "taker_fee_bps": 20,
        "slippage_bps": 20,
        "latency_ms": 500,
        "minimum_order_notional": 5000,
        "tick_size": 1,
        "lot_size": 0.00000001,
        "partial_fill_probability": null,
        "partial_fill_status": "UNSUPPORTED"
      }
    ]
  },
  "experiments": [
    {
      "candidate_family": "trend-hypothesis",
      "strategy_id": "sma_trend",
      "strategy_config": {"mode": "long_flat"},
      "feature_config": {"input": "completed_candles"},
      "parameter_sets": [
        {"lookback_bars": 12, "target_weight": 0.3, "entry_return_threshold": 0.01}
      ],
      "seed": 41
    }
  ]
}
```

Cost values are experiment assumptions, not claims about the current exchange
schedule or market microstructure. The CLI requires at least two distinct cost
configurations and explicit cash and buy-and-hold runs whenever candidate
strategies are present. A seeded `randomized_placebo` strategy is available as
an optional baseline. Baseline comparisons are matched by fold and cost
scenario and report differences without making promotion decisions.

The CLI currently supports only the explicit built-ins `cash`,
`buy_and_hold`, `randomized_placebo`, and `sma_trend`. Existing repository
strategies must be adapted and registered before this command can execute them.

## Command and evidence layout

```bash
python -m bithumb_coin_trader.research_infra.cli research-batch \
  --dataset-manifest research-input/dataset.json \
  --hypotheses research-input/hypotheses.json \
  --cost-grid conservative \
  --walk-forward \
  --folds 5 \
  --window-mode EXPANDING \
  --purge-seconds 3600 \
  --embargo-seconds 3600 \
  --output research-output
```

The command requires a clean committed source revision. Batch, strategy,
parameter, dataset-byte, cost, fold, and seed inputs form content-derived
identities. Each attempt has a write-once manifest, hash-chained fold events,
standard result metrics, and a completion record. An interruption resumes the
latest incomplete attempt by loading its completed folds. A failed attempt is
preserved; `--retry-failed` creates a new attempt directory. Identical completed
inputs are skipped and their prior evidence is checked rather than replaced.
The aggregate report compares strategies with the declared baselines.

This command only reads local candles and writes research artifacts. It does
not start PAPER, call a private endpoint, select a candidate, or enable LIVE.
