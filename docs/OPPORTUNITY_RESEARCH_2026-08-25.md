# KRW-BTC profit-first opportunity research

## Why this protocol exists

The earlier 70% win-rate study correctly rejected every candidate under its
stated mission, but that mission structurally penalized low-frequency trend
systems. Profitability depends on net expectancy, payoff asymmetry, costs, and
drawdown—not win rate alone. This study preserves the execution and holdout
safety rules while using a staged profit-first gate.

## Agent council synthesis

- Trend review identified turnover and the roughly 0.60% modeled round-trip
  cost as the main failure mode. It prioritized completed-4h Donchian and slow
  momentum over 30-minute EMA/VWAP churn.
- Mean-reversion review rejected ordinary RSI/Bollinger reclaim variants and
  proposed rare shock-rebound events. Both frozen shock candidates failed
  double-cost testing here.
- Regime/meta review proposed a nested cross-fit selector, but it was deferred
  because a fixed candle-only candidate cannot honestly reproduce train-only
  thresholds, purging, and live order-book inputs.
- Gate review removed the universal 70% win-rate and Wilson conditions while
  retaining closed-bar, next-open, real cost, gap reset, drawdown, fold,
  bootstrap, and one-time holdout controls.

## Data and geometry

- Raw observations: 100,002
- Rejected exchange anomaly timestamps: `2024-01-04T03:04:00Z` and
  `2024-01-04T03:58:00Z`
- Selected aligned observations: 100,000, from 2020-12-04 through 2026-08-24
- Development: first 96,000
- Expanding tests: initial 48,000 plus six 8,000-candle test windows
- Sealed holdout: final 4,000; opened once for one finalist
- Base cost: 0.25% fee + 5 bps slippage per fill
- Stress cost: 0.50% fee + 10 bps slippage per fill

## Development result

The best deduplicated family candidate was `profit_donchian_4h_70_30`:

- base return: **+47.02%**
- double-cost return: **+33.90%**
- 31 completed trades
- win rate: 54.84%
- profit factor: 2.17
- maximum drawdown: 10.74%
- positive active folds: 4/6
- largest winning-trade contribution: 18.8%
- trade-block bootstrap `P(net > 0)`: 96.7%

This is development evidence, not an expected future return. Nine correlated
Donchian variants were all counted as trials, and only one representative was
allowed into the holdout.

The shared backtester liquidates and suppresses a stale position after source
gaps. The legacy interval mapper can still carry indicator history across
omitted incomplete 4-hour buckets. Independent replay found 14 premature
post-gap entries in the out-of-sample region, including one after only 11
source bars when 560 were required. This invalidates the development finalist
and the already-opened holdout for paper or live promotion. The immutable
original report is not rewritten; the correction and source binding are stored
in `reports/krw-btc-opportunity-post-selection-audit-2026-08-25.json`.

## One-time holdout result

The 4,000-candle holdout was **inconclusive**:

- base marked-equity return: +2.02%
- double-cost marked-equity return: +1.10%
- only 2 normal closed trades, both losing
- one still-open position was force-liquidated for final equity accounting
- maximum drawdown: 5.14%

The positive marked return was dominated by the forced final liquidation, so
it is not treated as a passed trade sample. The one-time ledger prevents
reopening or tuning against this period. The selected research candidate
therefore remains `cash`.

## Next evidence required

Keep live entries disabled. Start a new protocol that resets interval-indicator
state after every source gap and requires a fresh 560-source-bar warm-up. It
must use a new development/validation split and fresh prospective evidence;
the opened holdout in this report cannot be reused. Record actual spread,
IOC/FOK fill feasibility, drift from the modeled next-open price, and gap/event
vetoes. A separate regime/meta round may start only with purged nested
cross-fitting and a cumulative trial ledger.
