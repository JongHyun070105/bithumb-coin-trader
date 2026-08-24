# Wave 5 sandbox boundary

- Input: public completed 30-minute OHLCV CSV only.
- No Bithumb credentials, account state, MCP orders, Discord, or live configuration.
- No historical LLM score or order-book imbalance without replayable point-in-time data.
- Current local coverage: one market, KRW-BTC. Cross-sectional momentum is therefore
  unavailable and must not receive a synthetic result.
- Evidence: expanding chronological walk-forward, next-open fills, base costs of
  0.25% fee plus 5 bps slippage per fill, and a 2x cost stress.
- Outputs: `result.json` and independently generated `validation.json`.
