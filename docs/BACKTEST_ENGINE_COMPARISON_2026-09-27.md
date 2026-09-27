# Backtest and Evaluation Engine Comparison — 2026-09-27

## Decision

New single-market spot research uses this path:

```text
research-batch → generic train-only walk-forward → SpotResearchBacktester
               → RebalanceBacktester → ExperimentResult v1
```

`SpotResearchBacktester` is the authoritative research interface. It requires an
explicit `SpotCostScenario`, and `research-batch` calls it directly. The
underlying `RebalanceBacktester` is retained as the accounting/execution engine
and for compatible historical callers. This is a boundary decision, not a
rewrite or deletion plan. Existing multi-asset and order-book engines remain
specialized until their inputs, accounting, and cost semantics have governed
adapters.

The authoritative candle path is long-only, target-weight, next-open execution.
It charges taker fees, adverse slippage, tick/lot rounding, and minimum notional
from the selected scenario. It does not model visible depth. Latency above zero
and partial-fill probability above zero are reported as unsupported; those runs
cannot be frozen. It does not simulate maker orders. A zero assumption is
explicit configuration and must not be mistaken for a verified exchange rate.

## Engine matrix

| Engine / classification | Data input | Signal model | Order model | Fill model | Position accounting | Fee model | Slippage | Partial fills | Latency | Metrics | Current users | Known results | Test coverage | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `SpotResearchBacktester` → `RebalanceBacktester` / **AUTHORITATIVE** | Chronological single-market OHLCV candles and target weights | Caller supplies causal point-in-time target weight in `[0,1]` | Rebalance to weight at next candle open; final liquidation at last close | Deterministic candle-price approximation; scenario tick, lot, and minimum notional rounding; no depth | Cash and one long spot quantity; equity/cash/quantity curves; fee/slippage totals | Explicit `SpotCostScenario`; current fills are taker | Scenario bps with adverse tick rounding | Probability `0` is explicit; `>0` marks result unsupported | `0` required for completed governed result; `>0` marks unsupported | Return, MDD, exposure, fills, fees, turnover; generic runner adds CAGR, Sharpe/Sortino/Calmar and worst day/week | `research-batch` and generic walk-forward, with explicit registry-backed daily/weekly V3/V4/V4b/V5/V6 and Core+Satellite adapters | No trusted strategy result is established by the engine inventory; synthetic test returns are fixtures | `test_rebalance_backtest.py`, `test_backtest_cost_scenarios.py`, `test_walk_forward_runner.py`, `test_candidate_freeze.py` | **AUTHORITATIVE** interface; underlying engine retained |
| `Backtester` / **LEGACY** | Single-market candles plus `Signal` stream and optional target allocation | External LONG/FLAT (optional SHORT without spot-cost scenario) | Enter/exit at next candle open; gap liquidation and final close paths | Candle open with optional scenario tick/lot/minimum; scenario disallows shorts | One open position, equity curve, closed trade records | Settings fee or explicit scenario taker fee | Settings or scenario adverse bps | Not modeled; scenario partial-fill assumptions are explicitly marked unsupported | Not modeled at candle resolution; scenario latency is marked unsupported | Return, MDD, Sharpe, win rate, trades, exposure, fees, turnover | CLI, V2, wave3, opportunity, win-rate research | Existing reports are historical research only; no result is trusted for current candidate selection | `test_backtest.py`, `test_backtest_cost_scenarios.py`, V2/opportunity/win-rate suites | **LEGACY**; preserve, stop adding new generic candidate workflows |
| `MultiAssetSharedCashBacktester` / **SPECIALIZED** | Per-market candle series and per-market target-weight streams | Caller supplies cross-sectional weights | Sell-first, then buy from shared cash; next-bar market prices; listing/delisting controls | Candle-price approximation; explicit scenario can enforce taker fee, slippage, tick/lot/minimum | Shared cash plus per-market positions, exposure/drift and delisting accounting | Settings fee or explicit scenario taker fee | Settings or scenario adverse bps | Scenario partial fills above zero are unsupported | Scenario latency above zero is unsupported | Return/CAGR, MDD, Sharpe, round trips, fees, realized/target exposure, constraint counters | V8 research and V8.1 robustness | Historical V8/V8.1 outcomes remain exploratory/failed records; no current candidate approval | `test_multi_asset_backtest.py`, V8 suites | **SPECIALIZED**; needs a future governed multi-asset adapter |
| `run_composite_portfolio_backtest` / **ADAPTER** | Single candle stream and component weight streams | Combines Core/Satellite or component targets | Delegates order/rebalance to `RebalanceBacktester` | Same candle approximation as underlying engine | Same cash/one-asset ledger plus component diagnostics | Settings fees; scenario can be passed through engine APIs where caller supports it | Settings or scenario | Underlying engine limits | Underlying engine limits | Composite return and component attribution | Core+Satellite / V6 research | Historical reported `+48.43%` is not trusted; supporting report is absent in this branch | `test_composite_portfolio_backtest.py`, component suites | **ADAPTER / LEGACY**; use only after its caller supplies governed cost assumptions |
| `DeterministicTakerSimulator` / **SPECIALIZED** | One or more order-book snapshots and a market-order request | No strategy; executes a supplied order | Market buy by KRW amount or sell by asset quantity | Sweeps visible L2 levels; can reject or partially fill; optional delayed-book selection | Per-order result only; no portfolio ledger | Request-level explicit fee rate (default exists for compatibility; governed use must override) | Spread, depth, and latency/adverse movement fields | Supported when request allows partial fills | Timestamp/book selection API; caller supplies event stream | Per-order fill/VWAP/cost decomposition | `ResearchExecutionSimulator`, `paper_engine` adapters, tests | No strategy or candidate result by itself | `test_execution_simulator.py`, failure-injection and journal tests | **SPECIALIZED** primitive, not a backtester |
| `ResearchExecutionSimulator` / **SPECIALIZED** | Canonical order-book events | Supplied BUY/SELL/HOLD research signals | Market taker orders; long-only position lifecycle | Delegates to deterministic depth sweep and selects a book after latency | One in-memory long position and simulated-trade ledger | `ExecutionAssumptions.fee_rate` and fee regime; not `SpotCostScenario` | Visible spread/depth plus separately configured extra impact | Configurable at primitive level; event wrapper behavior must be checked per path | Configurable from event stream | Trade PnL, execution-cost decomposition and equity events | Microstructure research | Existing runs are historical hypothesis evidence, not candidate evidence | `test_research_infra.py` execution sections and execution simulator suites | **SPECIALIZED / LEGACY**; distinct assumptions prevent direct interchange |
| `RealisticTakerExecutionSimulator` / **SPECIALIZED** | One order-book snapshot | No strategy | Market order by target KRW notional | Depth sweep with partial/reject state | Per-order only | No fee schedule applied by this class | Spread, VWAP depth impact, latency adjustment | Partial fill supported | Configurable latency | VWAP, size, spread/slippage and rejection | V9/microstructure research | No governed candidate result established; a partial-depth size accumulation defect was fixed and given a focused regression on this branch | `tests/test_microstructure_taker_simulator.py` | **SPECIALIZED**; requires broader semantic validation before candidate reuse |
| `MakerSimulator` / **SPECIALIZED** | Canonical L2 book/trade events | No strategy; supplied passive order | Passive limit order with cancellation and holding horizons | Optimistic/base/conservative queue heuristics; conservative mode needs observed volume beyond queue | Per-order fill state and round-trip helper; not a complete portfolio ledger | Configured maker/taker rates; defaults include zero maker fee | Spread/adverse-selection fields | Partial maker fills represented | Placement/cancel latency assumptions | Fill status, queue delay, adverse selection, net bps | Maker hypothesis research | `MakerSimulatorSpecification` explicitly says validity is not established without L3 queue reconstruction | `test_maker_simulator.py` | **SPECIALIZED**; never treat its optimistic fills as candidate proof |
| Strategy-local evaluators (`live_policy_research`, V2–V8 wrappers, opportunity/win-rate reports) / **ADAPTER or LEGACY** | Vary by strategy module | Strategy-specific | Usually call `Backtester` or `RebalanceBacktester`; some own a policy loop | Inherits engine or has local candle-fill rules | Varies; no common ledger identity | Often `TradingSettings` or local assumptions | Often flat bps | Varies and commonly absent | Varies and commonly absent | Strategy-specific JSON/CSV and metrics | Existing research scripts and historical reports | Keep historic results unchanged; none are automatically candidates | Module-specific suites | **LEGACY / SPECIALIZED** until migrated through the authoritative contract |

## Convergence rules

1. New single-market candle strategies must expose a causal `target_weight`
   adapter and run through `research-batch`; outputs use the standardized
   `ExperimentResult` schema.
2. The current adapter allowlist includes 23 immutable daily/weekly IDs from
   the daily, V3, V4/V4b, V5, V6, and explicit 70/30 Core+Satellite families,
   plus cash, buy-and-hold, and randomized-placebo controls. Other inventoried
   families remain explicitly unsupported by the CLI until their data,
   execution, and accounting contracts are reviewed and adapted.
3. Multi-asset, maker, and order-book research keeps its own specialized engine
   until a governed adapter can preserve its information and execution
   semantics. Do not pretend a daily candle path models queue position or book
   depth.
4. Historical metrics and artifacts stay immutable. Re-run candidates through
   the same governed batch and freeze gates; never promote from the labels in an
   old report.

The authority choice settles an API boundary only. Alpha remains unproven,
PAPER remains not started, and LIVE/private API remain disabled.
