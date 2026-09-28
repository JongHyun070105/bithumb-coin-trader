# External BitMEX Research Gap Closure — 2026-09-27

## Scope and disposition

This is a local, hypothesis-generation-only research addendum. It does not
establish alpha, profitability, transferable execution skill, or permission for
paper/live trading. Raw external files remain outside Git under the ignored
`.external-research-data/` tree.

The joined hourly-context execution file contains 1,268,435 rows. Its SHA-256
was `1ca8eeae1a77b69203c115b8432c52de15b46e4d6ad59bf368bd7102a09ad185`.
Regime analysis below includes XBTUSD and ETHUSD only, the two symbols with
joined, non-UNKNOWN context regimes. Their rows are 941,007 and 327,428,
respectively. The prior strict as-of join audit reports zero future leakage;
regime interpretation remains conditional on that hourly context and on the
source's classification rules.

## Contract semantics audit

On 2026-09-27, the official BitMEX instrument endpoint was queried for all 46
symbols in the joined execution file; it returned 46 matching records,
including settled contracts. The endpoint documents that it includes settled
and unlisted instruments and exposes fields such as listing, expiry, quote and
settlement currency, position currency, inverse/quanto flags, and multiplier
([Get Instruments](https://docs.bitmex.com/api-explorer/get-instruments)). This
is authoritative symbol-level evidence as returned on that date, but the API
response is not a versioned snapshot of every field over each instrument's full
trading life.

| Instrument family | Source-backed conclusion | Still unverified |
|---|---|---|
| ETHUSD perpetual | BitMEX lists ETHUSD from 2018-08-01 as a quanto perpetual, USD-quoted and XBT-settled. The guide gives `0.000001 XBT` per USD point; the current instrument row reports raw multiplier `100` in settlement subunits. ([Perpetual Contracts Guide](https://www.bitmex.com/app/perpetualContractsGuide), [instrument row](https://www.bitmex.com/api/v1/instrument?symbol=ETHUSD)) | That multiplier and payoff remained unchanged for every 2018–2021 trade; historical fee, tick, and lot schedules. |
| XRPUSD perpetual | BitMEX lists XRPUSD from 2020-02-04 as a quanto perpetual, USD-quoted and XBT-settled. The guide gives `0.0002 XBT` per USD point; the current instrument row reports raw multiplier `20000`. ([Perpetual Contracts Guide](https://www.bitmex.com/app/perpetualContractsGuide), [instrument row](https://www.bitmex.com/api/v1/instrument?symbol=XRPUSD)) | Full-life multiplier continuity and historical fee, tick, and lot schedules. |
| XRPH21 | The official quarterly-futures table identifies XRP/Bitcoin, listing 2020-12-11 and expiry 2021-03-26. The settled instrument row identifies a linear, non-inverse, non-quanto future, XRP position currency, XBT quote and XBt settlement. ([Q2 2021 listings](https://www.bitmex.com/blog/q2-2021-quarterly-futures-listings), [instrument row](https://www.bitmex.com/api/v1/instrument?symbol=XRPH21)) | Historical fee changes during its life and interpretation of the raw fixed-point multiplier field beyond the `positionCurrency` unit. |
| Dated XBT futures | The sampled settled XBT quarterly rows are inverse, USD-quoted, XBt-settled futures. The exact listing, expiry, tick, lot, and returned fee fields are symbol-specific; the registry must not assume one fee rate for every dated contract. ([Get Instruments](https://docs.bitmex.com/api-explorer/get-instruments)) | Whether each returned specification/fee value was constant across all fills for that contract. |
| Other dated altcoin/XBT futures | The returned settled ADA, BCH, EOS, ETH, LTC, TRX, and XRP futures use XBT quote, XBt settlement, token `positionCurrency`, and non-inverse/non-quanto flags. This supports a linear token-against-XBT classification for these records. | Historical continuity for every field and historical fee schedule. Keep the table at per-symbol granularity. |
| XBT7D_U110 | The joined symbol's official instrument row has type `OCECCS`, so it is an option-series instrument, not an inverse future. Prefix fallback on `XBT` would misclassify it. ([instrument row](https://www.bitmex.com/api/v1/instrument?symbol=XBT7D_U110)) | Exact option payoff and scale are not established by the available audit. Do not map it to an inverse-future PnL formula. |
| YFIUSDTZ20 | The official row is a dated future (`FFCCSX`), USDT-quoted, XBt-settled, and marked quanto; it is not a linear USDT perpetual. ([instrument row](https://www.bitmex.com/api/v1/instrument?symbol=YFIUSDTZ20)) | Exact PnL scaling and any within-life specification or fee changes. The existing linear-perpetual/funding classification is unsupported. |

The current contract registry's common maker/taker assumptions must therefore
not be presented as proven historical rates. Settled instrument records show
different fee fields across dated instruments and dates; those are current
instrument-table values, not a complete event-time fee ledger. In particular,
the registry's blanket fee rates and its XBT-prefix fallback are not safe
historical accounting evidence. The historical USD-equivalent turnover and fee
sensitivity remain `UNVERIFIED` pending a per-execution dated contract/fee
mapping. The existing contract/PnL code should be corrected in a separately
scoped PR before those outputs are used.

## Regime-stratified block bootstrap

The reproducible implementation is
[`regime_bootstrap.py`](../src/bithumb_coin_trader/research_infra/regime_bootstrap.py)
with CLI
[`analyze_external_regime_bootstrap.py`](../scripts/analyze_external_regime_bootstrap.py).
The analysis used the joined CSV.GZ above, seed `20260927`, and 5,000
replicates. For each symbol and each single regime dimension (volatility, trend,
volume, funding), it estimates fill-weighted
`AddedLiquidity / (AddedLiquidity + RemovedLiquidity)`. It resamples
non-wrapping seven-calendar-day blocks within each calendar year, preserving
that symbol's observed day range and year mix; unknown-regime fills are excluded and reported.
The intervals are empirical 2.5th/97.5th order statistics.

| Symbol | Dimension | Regime | Maker | Taker | Maker share | 95% year-stratified 7d interval |
|---|---|---|---:|---:|---:|---:|
| ETHUSD | funding | negative | 12,654 | 1,763 | 87.7714% | 64.8726%–95.7980% |
| ETHUSD | funding | positive | 288,783 | 24,228 | 92.2597% | 88.9602%–94.0307% |
| ETHUSD | trend | range | 123,645 | 9,838 | 92.6298% | 89.0827%–93.9982% |
| ETHUSD | trend | down | 122,433 | 10,733 | 91.9401% | 86.2447%–94.8149% |
| ETHUSD | trend | up | 55,231 | 5,387 | 91.1132% | 85.0257%–94.7937% |
| ETHUSD | volatility | high | 190,620 | 16,122 | 92.2019% | 88.0459%–94.6338% |
| ETHUSD | volatility | low | 110,817 | 9,869 | 91.8226% | 86.6008%–93.6688% |
| ETHUSD | volume | high | 220,302 | 17,137 | 92.7826% | 89.1677%–94.5725% |
| ETHUSD | volume | low | 81,135 | 8,854 | 90.1610% | 85.7004%–92.9690% |
| XBTUSD | funding | negative | 162,396 | 144,006 | 53.0010% | 47.0884%–58.5301% |
| XBTUSD | funding | positive | 341,994 | 292,611 | 53.8908% | 50.4745%–56.7492% |
| XBTUSD | trend | range | 211,127 | 206,735 | 50.5255% | 46.2778%–54.6264% |
| XBTUSD | trend | down | 165,659 | 114,752 | 59.0772% | 54.5909%–63.1626% |
| XBTUSD | trend | up | 126,678 | 112,276 | 53.0136% | 48.3109%–56.4600% |
| XBTUSD | volatility | high | 327,662 | 262,359 | 55.5340% | 51.7807%–59.1316% |
| XBTUSD | volatility | low | 176,711 | 174,187 | 50.3596% | 45.3218%–54.6369% |
| XBTUSD | volume | high | 346,562 | 288,417 | 54.5785% | 50.5451%–58.1453% |
| XBTUSD | volume | low | 157,820 | 148,171 | 51.5767% | 47.3878%–55.2084% |

Unknown-regime known-liquidity rows excluded from strata: ETHUSD trend 161;
XBTUSD trend 3,780, volatility 88, volume 37. There were no unknown liquidity
tags among these two symbols. These are descriptive maker/taker fill shares,
not aggressiveness preferences, order intent, fill quality, or a causal regime
effect. Seven-day blocks do not solve longer dependence, data selection,
contract-unit differences, or fee uncertainty. ETH negative-funding intervals
are especially broad. No inference is made for the 170,772 unmatched fills in
other instruments.

The ignored local output was
`regime-stratified-bootstrap-20260927.json`, SHA-256
`7e0669ca1026bc43b3ab31c61e00cee335d057a0c3f7be7ca53c68c8b5d81d5c`.
The raw joined data and generated JSON remain uncommitted.

## Historical spread, depth, and queue data

BitMEX documents `/api/v1/quote` as a best-bid/offer time series with
`bidPrice`, `askPrice`, `bidSize`, `askSize`, timestamps, and date filters
([Get Quotes](https://docs.bitmex.com/api-explorer/get-quote)). Unauthenticated,
small public GET probes returned BBO rows for sampled XBTUSD and ETHUSD hours in
2019, 2020, and 2021; XRPUSD in sampled 2020/2021 hours; and XRPH21 in January
2021. Two 2018 samples through this event-level endpoint returned no rows, but
the separate hourly endpoint `/api/v1/quote/bucketed` returned five hourly BBO
records for XBTUSD on 2018-06-01 and ETHUSD on 2018-09-01; probes also returned
records for XBTUSD in 2019 and XRPH21 in 2021. BitMEX documents that bucketed
quotes provide best bid/offer, including sizes, and timestamp each bucket at
its **end** ([Get Quotes Bucketed](https://docs.bitmex.com/api-explorer/get-quote-bucketed)).
This establishes sampled public BBO coverage as early as 2018, not complete
daily/hourly coverage or guaranteed retention across every symbol's full life.
The event-level and bucketed quote endpoints can support spread and
best-level displayed-size summaries after a separate coverage audit.

The documented `/api/v1/orderBook/L2` endpoint is explicitly the **current**
order book ([Current OrderBook](https://docs.bitmex.com/api-explorer/order-book)).
No historical full-depth snapshot source was established here. The existing
archive contains no quote or L2 source files. Therefore:

- Historical BBO/spread and best-level size: **partially available from sampled
  2018–2021 public API queries; full-period coverage not verified or collected.**
- Historical multi-level depth: **not verified or found in the documented
  public REST history.**
- Queue position, cancellations ahead, and fill probability: **unavailable**
  from these sources; do not infer them from trades or BBO sizes.

No quote rows or order-book depth were fabricated or added to the dataset.

## Scientific state

- `ALPHA = UNPROVEN`
- `PAPER = NOT_STARTED`
- `LIVE = DISABLED`
- `PRIVATE_API = DISABLED`
