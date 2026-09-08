# Dashboard v0.3 implementation plan

Base: `0a45a66324e0e49cbe02db4b47d9529353b5f20d`, freshly fetched origin/main on 2026-09-08.
Branch: `gemini/dashboard-v0-3-trading-ui-20260908`.

## Product decisions

- Default landing: Dashboard. Primary navigation: Dashboard, Portfolio, Positions, Trades, Performance. Bot Status and System are secondary. Research & Evidence is a deliberately opened Advanced area with internal navigation.
- 212px quiet sidebar, compact persistent PAPER OFF / LIVE DISABLED strip, large financial values, a 62/38 equity-chart/summary split, compact position and trade tables. Neutral graphite surfaces, blue chart, signed green/red results.
- Normal mode contains no invented balances, returns, trades, or zero counts. An explicit preview button enables persistent DEMO DATA labeling; exiting or reloading returns to no data.
- Trading preview and evidence imports use separate state. Loading evidence cannot create trading performance or change execution status. Legacy evidence links open inside Advanced.
- Future data contract supports loading, absent, synthetic, authoritative, and error states. Today return requires an authoritative trading-day opening equity, cash flow, timestamp and timezone; an incomplete baseline never becomes a computed real return.
- Use native accessible SVG for the three small chart families (equity/return, signed daily bars, drawdown), avoiding another dependency. Controls select actual data windows. Keyboard and pointer inspection share the same point readout.

## Public UX references inspected before implementation

- [FreqUI](https://www.freqtrade.io/en/stable/freq-ui/): compact bot performance and trade views, wallet balance history, careful distinction between recorded and reconstructed balances. Adapt hierarchy and density only; no execution controls.
- [TradingView Portfolios](https://www.tradingview.com/support/solutions/43000760937-tradingview-portfolios-track-your-assets-know-your-trades/): portfolio value/return toggle, timeframes, holdings and transactions as separate everyday views.
- [Hummingbot Instances](https://hummingbot.org/dashboard/instances/): net PnL amount/percentage alongside concise instance and error state. Adapt read-only status only.

## Execution and acceptance

1. Preserve baseline evidence suite (84 passing) and all evidence classification/hash/DQ/actual-start logic.
2. Add nullable trading contract and deterministic demo with accounting consistency tests.
3. Build shared trading components, charts, five trading pages, Bot Status and System; move original pages under Advanced.
4. Add interaction tests for no-data/demo/loading/error/real supplied-state rendering, navigation, filters/timeframes, no execution controls, and evidence/trading separation.
5. Run npm install, typecheck, lint, full dashboard tests, build, diff check and source network/control scans.
6. Inspect 1440, 1280, 1024 and 390px in a real browser, including mobile menu keyboard behavior. Capture no-data Dashboard, demo Dashboard and demo Performance; compare against v0.2 capture.
7. Document data adapter boundary and results. Commit logical frontend-only changes, push feature branch and verify remote SHA. Leave main unmerged for visual approval.

No AWS, running soak/process, market/private/account API, raw/holdout data, backend or execution inspection is part of this work.
