# Research sandbox boundary

- Inputs: public completed Bithumb 30-minute OHLCV CSV and repository source.
- External research: public official documentation, upstream source, papers,
  and license metadata only.
- Forbidden inputs: Bithumb credentials, balances, account state, private
  orders, Discord secrets, fabricated order-book history, fabricated news, or
  LLM-generated historical labels.
- Execution: research-only LONG/FLAT signals, next-bar-open fills, no
  pyramiding, no mutation of live strategy/configuration/LaunchAgent state.
- Development: the last 4,000 candles stay sealed until candidate definitions,
  thresholds, and ranking are frozen. The default runner never opens them.
  An explicit holdout run atomically creates a one-time ledger before any
  holdout metric is computed; an existing or crash-state ledger blocks every
  rerun. Failed candidates and rejection reasons remain in the artifact.
- Validation mode: mission validator script. Completion requires an independently
  generated validation artifact with dataset hash, candidate manifest hash,
  recomputed metrics, gate decisions, and live-entry-off evidence.
- Outputs:
  - `.omx/specs/autoresearch-winrate70/result.json`
  - `.omx/specs/autoresearch-winrate70/validation.json`
  - `.omx/specs/autoresearch-winrate70/holdout-ledger.json` (only if opened)
  - `reports/krw-btc-winrate70-research-2026-08-24.json`

No API key or live order call is allowed anywhere in this research sandbox.
