# Exploratory Research on Unqualified Data — 2026-09-28

## Classification and scope

```text
DATA_QUALIFICATION = UNQUALIFIED
RESEARCH_ROLE = EXPLORATORY_ONLY
PROMOTION_ELIGIBLE = NO
PROSPECTIVE_HOLDOUT_CONSUMED = NO
ALPHA = UNPROVEN
```

This is a pipeline and strategy-debugging exercise. It is not qualified retrospective evidence, a candidate ranking, an alpha claim, a PAPER result, or LIVE evidence. The exact 2026-09-26 Fresh observability 30H run remains its immutable official FAIL under the terminal evidence contract. No prospective holdout was read.

## Dataset inventory

### Local CSV inventory

The checkout has 96 CSV files under `data/`, totaling 68,552,970 logical bytes; allocated directory size is about 145 MiB. Filenames indicate KRW symbols and nominal candle intervals. The repository candle-fetch tooling targets Bithumb public endpoints, but provenance for each existing file was not independently established. This inventory records names and sizes only except for the one development prefix below; the other files were not opened or parsed, so their exact start/end, row counts, and schema remain UNKNOWN. This keeps sealed or prospective partitions untouched.

| File | Bytes | Filename symbol / interval | Venue / time span / schema |
|---|---:|---|---|
| `data/krw-ada-15m-v8.csv` | 740507 | ADA / 15m | Not profiled; UNKNOWN |
| `data/krw-ada-1d-v71.csv` | 186087 | ADA / 1d | Not profiled; UNKNOWN |
| `data/krw-ada-1h-v8.csv` | 449225 | ADA / 1h | Not profiled; UNKNOWN |
| `data/krw-ada-4h-v71.csv` | 269246 | ADA / 4h | Not profiled; UNKNOWN |
| `data/krw-apt-1d-v71.csv` | 101296 | APT / 1d | Not profiled; UNKNOWN |
| `data/krw-apt-4h-v71.csv` | 270496 | APT / 4h | Not profiled; UNKNOWN |
| `data/krw-avax-15m-v8.csv` | 797380 | AVAX / 15m | Not profiled; UNKNOWN |
| `data/krw-avax-1d-v71.csv` | 140452 | AVAX / 1d | Not profiled; UNKNOWN |
| `data/krw-avax-1h-v8.csv` | 502276 | AVAX / 1h | Not profiled; UNKNOWN |
| `data/krw-avax-4h-v71.csv` | 300656 | AVAX / 4h | Not profiled; UNKNOWN |
| `data/krw-axs-1d-v71.csv` | 153084 | AXS / 1d | Not profiled; UNKNOWN |
| `data/krw-axs-4h-v71.csv` | 280533 | AXS / 4h | Not profiled; UNKNOWN |
| `data/krw-bch-15m-v8.csv` | 822526 | BCH / 15m | Not profiled; UNKNOWN |
| `data/krw-bch-1d-v71.csv` | 204922 | BCH / 1d | Not profiled; UNKNOWN |
| `data/krw-bch-1h-v8.csv` | 498711 | BCH / 1h | Not profiled; UNKNOWN |
| `data/krw-bch-4h-v71.csv` | 293870 | BCH / 4h | Not profiled; UNKNOWN |
| `data/krw-btc-15m-v8.csv` | 909854 | BTC / 15m | Not profiled; UNKNOWN |
| `data/krw-btc-1d-2026-08-24-2400.csv` | 224453 | BTC / 1d | Not profiled; UNKNOWN |
| `data/krw-btc-1d-2400.csv` | 224453 | BTC / 1d | Not profiled; UNKNOWN |
| `data/krw-btc-1d-v7.csv` | 18873 | BTC / 1d | Not profiled; UNKNOWN |
| `data/krw-btc-1d-v71.csv` | 224453 | BTC / 1d | Not profiled; UNKNOWN |
| `data/krw-btc-1h-6000.csv` | 558641 | BTC / 1h | Not profiled; UNKNOWN |
| `data/krw-btc-1h-v7.csv` | 18673 | BTC / 1h | Not profiled; UNKNOWN |
| `data/krw-btc-1h-v8.csv` | 558642 | BTC / 1h | Not profiled; UNKNOWN |
| `data/krw-btc-30m-2026-08-11.csv` | 2815471 | BTC / 30m | Not profiled; UNKNOWN |
| `data/krw-btc-30m-2026-08-12-wave2.csv` | 3726029 | BTC / 30m | Not profiled; UNKNOWN |
| `data/krw-btc-30m-2026-08-13-wave3-45k.csv` | 4183222 | BTC / 30m | Not profiled; UNKNOWN |
| `data/krw-btc-30m-2026-08-13-wave3.csv` | 3730349 | BTC / 30m | Not profiled; UNKNOWN |
| `data/krw-btc-30m-2026-08-14-wave4.csv` | 3744068 | BTC / 30m | Not profiled; UNKNOWN |
| `data/krw-btc-30m-2026-08-24-100002-raw.csv` | 9190488 | BTC / 30m | Not profiled; UNKNOWN |
| `data/krw-btc-30m-2026-08-24-100k.csv` | 9190306 | BTC / 30m | Not profiled; UNKNOWN |
| `data/krw-btc-30m-2026-08-24-winrate.csv` | 4183214 | BTC / 30m | Not profiled; UNKNOWN |
| `data/krw-btc-4h-3000.csv` | 283598 | BTC / 4h | Not profiled; UNKNOWN |
| `data/krw-btc-4h-v71.csv` | 331329 | BTC / 4h | Not profiled; UNKNOWN |
| `data/krw-doge-15m-v8.csv` | 756319 | DOGE / 15m | Not profiled; UNKNOWN |
| `data/krw-doge-1d-2400.csv` | 150620 | DOGE / 1d | Not profiled; UNKNOWN |
| `data/krw-doge-1d-v7.csv` | 15441 | DOGE / 1d | Not profiled; UNKNOWN |
| `data/krw-doge-1d-v71.csv` | 150620 | DOGE / 1d | Not profiled; UNKNOWN |
| `data/krw-doge-1h-6000.csv` | 459869 | DOGE / 1h | Not profiled; UNKNOWN |
| `data/krw-doge-1h-v7.csv` | 15161 | DOGE / 1h | Not profiled; UNKNOWN |
| `data/krw-doge-1h-v8.csv` | 459870 | DOGE / 1h | Not profiled; UNKNOWN |
| `data/krw-doge-4h-3000.csv` | 232666 | DOGE / 4h | Not profiled; UNKNOWN |
| `data/krw-doge-4h-v71.csv` | 271510 | DOGE / 4h | Not profiled; UNKNOWN |
| `data/krw-dot-1d-v71.csv` | 176374 | DOT / 1d | Not profiled; UNKNOWN |
| `data/krw-dot-4h-v71.csv` | 279313 | DOT / 4h | Not profiled; UNKNOWN |
| `data/krw-etc-1d-v71.csv` | 203423 | ETC / 1d | Not profiled; UNKNOWN |
| `data/krw-etc-4h-v71.csv` | 297617 | ETC / 4h | Not profiled; UNKNOWN |
| `data/krw-eth-15m-v8.csv` | 932324 | ETH / 15m | Not profiled; UNKNOWN |
| `data/krw-eth-1d-2026-08-24-2400.csv` | 222824 | ETH / 1d | Not profiled; UNKNOWN |
| `data/krw-eth-1d-2400.csv` | 222824 | ETH / 1d | Not profiled; UNKNOWN |
| `data/krw-eth-1d-v7.csv` | 18718 | ETH / 1d | Not profiled; UNKNOWN |
| `data/krw-eth-1d-v71.csv` | 222824 | ETH / 1d | Not profiled; UNKNOWN |
| `data/krw-eth-1h-6000.csv` | 560250 | ETH / 1h | Not profiled; UNKNOWN |
| `data/krw-eth-1h-v7.csv` | 18719 | ETH / 1h | Not profiled; UNKNOWN |
| `data/krw-eth-1h-v8.csv` | 560252 | ETH / 1h | Not profiled; UNKNOWN |
| `data/krw-eth-4h-3000.csv` | 280201 | ETH / 4h | Not profiled; UNKNOWN |
| `data/krw-eth-4h-v71.csv` | 326919 | ETH / 4h | Not profiled; UNKNOWN |
| `data/krw-link-15m-v8.csv` | 829989 | LINK / 15m | Not profiled; UNKNOWN |
| `data/krw-link-1d-v71.csv` | 204987 | LINK / 1d | Not profiled; UNKNOWN |
| `data/krw-link-1h-v8.csv` | 513393 | LINK / 1h | Not profiled; UNKNOWN |
| `data/krw-link-4h-v71.csv` | 302397 | LINK / 4h | Not profiled; UNKNOWN |
| `data/krw-mana-1d-v71.csv` | 155304 | MANA / 1d | Not profiled; UNKNOWN |
| `data/krw-mana-4h-v71.csv` | 271347 | MANA / 4h | Not profiled; UNKNOWN |
| `data/krw-near-1d-v71.csv` | 69449 | NEAR / 1d | Not profiled; UNKNOWN |
| `data/krw-near-4h-v71.csv` | 287797 | NEAR / 4h | Not profiled; UNKNOWN |
| `data/krw-sand-1d-v71.csv` | 167094 | SAND / 1d | Not profiled; UNKNOWN |
| `data/krw-sand-4h-v71.csv` | 273248 | SAND / 4h | Not profiled; UNKNOWN |
| `data/krw-shib-1d-v71.csv` | 104858 | SHIB / 1d | Not profiled; UNKNOWN |
| `data/krw-shib-4h-v71.csv` | 286639 | SHIB / 4h | Not profiled; UNKNOWN |
| `data/krw-sol-15m-v8.csv` | 847330 | SOL / 15m | Not profiled; UNKNOWN |
| `data/krw-sol-1d-2400.csv` | 158143 | SOL / 1d | Not profiled; UNKNOWN |
| `data/krw-sol-1d-v7.csv` | 17508 | SOL / 1d | Not profiled; UNKNOWN |
| `data/krw-sol-1d-v71.csv` | 158143 | SOL / 1d | Not profiled; UNKNOWN |
| `data/krw-sol-1h-6000.csv` | 515103 | SOL / 1h | Not profiled; UNKNOWN |
| `data/krw-sol-1h-v7.csv` | 17264 | SOL / 1h | Not profiled; UNKNOWN |
| `data/krw-sol-1h-v8.csv` | 515106 | SOL / 1h | Not profiled; UNKNOWN |
| `data/krw-sol-4h-3000.csv` | 260164 | SOL / 4h | Not profiled; UNKNOWN |
| `data/krw-sol-4h-v71.csv` | 303556 | SOL / 4h | Not profiled; UNKNOWN |
| `data/krw-sui-1d-v71.csv` | 97565 | SUI / 1d | Not profiled; UNKNOWN |
| `data/krw-sui-4h-v71.csv` | 284211 | SUI / 4h | Not profiled; UNKNOWN |
| `data/krw-trx-1d-v71.csv` | 183718 | TRX / 1d | Not profiled; UNKNOWN |
| `data/krw-trx-4h-v71.csv` | 263876 | TRX / 4h | Not profiled; UNKNOWN |
| `data/krw-xlm-15m-v8.csv` | 745101 | XLM / 15m | Not profiled; UNKNOWN |
| `data/krw-xlm-1d-v71.csv` | 183588 | XLM / 1d | Not profiled; UNKNOWN |
| `data/krw-xlm-1h-v8.csv` | 450462 | XLM / 1h | Not profiled; UNKNOWN |
| `data/krw-xlm-4h-v71.csv` | 265116 | XLM / 4h | Not profiled; UNKNOWN |
| `data/krw-xrp-15m-v8.csv` | 786704 | XRP / 15m | Not profiled; UNKNOWN |
| `data/krw-xrp-1d-2026-08-24-2400.csv` | 188645 | XRP / 1d | Not profiled; UNKNOWN |
| `data/krw-xrp-1d-2400.csv` | 188645 | XRP / 1d | Not profiled; UNKNOWN |
| `data/krw-xrp-1d-v7.csv` | 16226 | XRP / 1d | Not profiled; UNKNOWN |
| `data/krw-xrp-1d-v71.csv` | 188645 | XRP / 1d | Not profiled; UNKNOWN |
| `data/krw-xrp-1h-6000.csv` | 476389 | XRP / 1h | Not profiled; UNKNOWN |
| `data/krw-xrp-1h-v7.csv` | 15989 | XRP / 1h | Not profiled; UNKNOWN |
| `data/krw-xrp-1h-v8.csv` | 476388 | XRP / 1h | Not profiled; UNKNOWN |
| `data/krw-xrp-4h-3000.csv` | 240322 | XRP / 4h | Not profiled; UNKNOWN |
| `data/krw-xrp-4h-v71.csv` | 280520 | XRP / 4h | Not profiled; UNKNOWN |

### Dataset actually profiled

| Temporary ID | Source | Coverage and records | Integrity / provenance | Role |
|---|---|---|---|---|
| `EXPLORATORY_LOCAL_BTC_DAILY_PREFIX_20260928` | `data/krw-btc-1d-2026-08-24-2400.csv`; one KRW-BTC daily OHLCV CSV | 2,220 development rows; 2020-01-28 15:00 UTC to 2026-02-24 15:00 UTC; 0 duplicate timestamps; every interval 86,400 seconds; close positive; volume non-negative. Dev-prefix bytes 207,517, SHA-256 `60cfdab94d922ae977697a9aaee085dcef3ed13ae1ffa7f9d0a9bcb73cc0c294`. File-size metadata 224,453 bytes. | The CSV does not embed venue/source provenance. Venue attribution is therefore UNKNOWN for this file. The trailing 180 rows were not read; only file-size metadata was observed. | UNQUALIFIED / EXPLORATORY_ONLY |

The runner reads the header and first 2,220 rows only. A test places an invalid value in the sealed tail and verifies it is not parsed. The reported 1,201 “OOS” bars are a chronological diagnostic segment inside the 2,220-row development partition, not the protected prospective holdout.

### Existing registry and historical sources (metadata only)

| Registry/source ID | Known source and scope | Current recorded limitations | Use this session |
|---|---|---|---|
| `old72h` / `aws-72h-soak-20260905-8017b83e` | Historical ~49.87M messages, 73 raw cohorts; historical raw archive is 5,530 files, 70,999,436,579 original logical bytes. | Official result `INTERRUPTED`; no final manifest flush; earlier DQ reports only about 3 date cohorts/228 receipts due hour-key collision. Raw source was removed only after byte/hash/archive verification; verified archive remains on EC2. | Not opened or analyzed for strategy research. |
| `v2` / 2026-09-12 30H | Registry records ~28.59M records, 30 candidate hours, 2,272/2,280 slots. | 8 feed-hours are `UNKNOWN_MISSING`; no immutable proof of zero-event coverage. Integrity is PARTIAL. | Metadata only; not used. |
| `v4` / 2026-09-15 30H | Registry source root is an S3 temporary prefix; collector epoch is recorded. | Registry says QUARANTINED / UNKNOWN. Its “currently running” prose is stale as of this session and was not treated as live state. | Not fetched or used. |
| `fresh45` | Registry describes about 5,396 JSONL files, 3 exchanges, 76 feeds, and an infrastructure-validation role. | Registry integrity is recorded PASS for that bounded infrastructure dataset, not for strategy qualification. Local source root was not found in the metadata-only CSV inventory. | Not parsed. |
| Fresh observability 30H, 2026-09-26 | Current repository report identifies exact historical run. | Immutable official FAIL because terminal evidence contract failed; reliability status does not prove alpha. | Not used as trading evidence. |

## Strategies and experiments

Used the repository’s `RebalanceBacktester`, existing V6 daily EMA Pullback and Fast Donchian strategy functions, and configured fee/slippage regimes. The strategy composition applies the documented Core 70% and Satellite 30% factors; the historical 48.43% claim is reproduced approximately but is not validated by this run.

| Strategy | Cost case | Total return | CAGR | Max drawdown | Sharpe | Round trips |
|---|---|---:|---:|---:|---:|---:|
| Core70 V6 Daily EMA Pullback + Sat30 | zero fee / 5 bps slip | 48.429% | 12.773% | 5.425% | 1.575 | 22 |
| same | normal: 0.25% fee / 5 bps | 46.723% | 12.377% | 5.416% | 1.534 | 22 |
| same | 2x: 0.50% fee / 10 bps | 44.211% | 11.788% | 5.405% | 1.469 | 22 |
| same | 3x: 0.75% fee / 15 bps | 41.741% | 11.202% | 5.659% | 1.404 | 22 |
| Core70 V6 Fast Donchian + Sat30 | normal: 0.25% fee / 5 bps | 43.801% | 11.691% | 5.416% | 1.490 | 20 |
| same | 3x: 0.75% fee / 15 bps | 39.213% | 10.594% | 5.608% | 1.369 | 20 |
| Buy-and-hold KRW-BTC | normal: 0.25% fee / 5 bps | 302.218% | 52.751% | 47.260% | 1.272 | 1 |
| Daily SMA 50/200 | normal: 0.25% fee / 5 bps | 131.514% | 29.113% | 35.359% | 0.927 | 4 |
| Cash | all configured cases | 0% | 0% | 0% | 0 | 0 |

Cost assumptions came from repository configuration: zero-fee/5 bps slip; zero-fee/15 bps slip; 0.25%/5 bps; 0.50%/10 bps; 0.75%/15 bps. No unsupported queue-position or latency precision was introduced. “Zero fee” is a scenario label only; no live trading occurred.

### Placebos and chronological folds

- 100 randomized placebo experiments: 2 composites × 5 cost cases × 10 seeds. Both strategies use the same fixed seeds (20260928–20260937); each shuffled target sequence preserves the observed target-weight histogram. Under normal costs, EMA placebo median was -22.589% (range -34.939% to -6.098%); Fast Donchian median was -22.549% (range -31.677% to -6.244%). This is not a statistical significance test: shuffling weights does not model market dependence.
- Five sequential one-year test folds with a five-day embargo were exercised for EMA under normal costs. Returns were -1.598%, -3.943%, +18.682%, +25.006%, and -1.978%. The mixed outcomes are pipeline diagnostics on development data. There was no label-based purge test because these fixed daily signal strategies have no separately built label series in this run.
- No broad parameter grid or parameter winner was selected. No candidate is frozen.

## Engine coverage and findings

- Exercised the current `RebalanceBacktester`, configured fee regimes, cash/buy-and-hold/SMA baselines, fixed strategy registry functions, chronological folds, `DatasetRegistry`, `HypothesisRegistry`, `ResearchManifest`, and the existing `GovernedExperimentRunner` crash/recovery tests.
- The target suite passed: 184 tests. Pyright reported 0 errors, 0 warnings, 0 informations. `git diff --check` passed. Research commit: `a5c78c2d9c3c4e72760342e55430fbecfb49fbf9`.
- Reproducibility fixes: `DatasetRegistry.update_role` now preserves source provenance confidence and rejects an explicitly blank build-manifest digest. Candidate/prospective/holdout roles require a SHA-256 manifest binding. `ResearchManifest` save is atomic and no-clobber by default, binds an input-derived experiment ID, and verifies fingerprint/ID on load. New `DatasetBuildManifest` binds source runs/hashes, build-code SHA, schema, time range, venue/symbols, record counts, and DQ result, with content fingerprints and no-clobber save.
- Regression suite includes crash-before-ledger recovery, crash-after-ledger recovery, concurrent duplicate reservation, manifest substitution rejection, hash-chain verification, and duplicate experiment protection; this session reran `tests/test_experiment_runner.py` within the 184-test target.
- No backtester PnL/accounting defect was confirmed in this bounded run. The fixes above are reproducibility/provenance issues, not evidence that any strategy is profitable.
- Current repository does not provide `SpotResearchBacktester`, `SpotCostScenario`, separate `StrategyRegistry`/`FeatureRegistry`, or an `ExperimentResult v1` type. The compatible rebalance API and `FEE_REGIMES` were used. No multi-asset, trades stream, order book, maker queue, or latency model was exercised.

## Adversarial limits and next qualified rerun

- A daily OHLCV candle series cannot validate fills, trades, queue position, websocket gaps, order-book depth, multi-asset portfolio constraints, or market impact. Input venue/source attribution for the selected CSV is not independently bound to a file manifest.
- All return figures can be wrong under different data lineage, candle semantics, execution assumptions, cost treatment, or selection history. The bull-market buy-and-hold baseline outperformed the composites on this slice.
- The internal fold and placebo outputs cannot be used to pick a strategy or tune against a final holdout. `DatasetBuildManifest.dq_result` supports `PASS`, `FAIL`, `PARTIAL`, and `UNKNOWN`; a future candidate promotion must require a verified manifest artifact and PASS DQ through the governed workflow.
- After a future official reliability PASS: build a content-bound dataset manifest, run governed DQ, preregister features/labels/costs/folds/embargo/acceptance thresholds, then run the batch with recovery verification, fixed baselines, costs, and robustness on development data. Keep a separate sealed prospective holdout unopened until all choices are frozen.

```text
DATA_QUALIFICATION = UNQUALIFIED
RESEARCH_ROLE = EXPLORATORY_ONLY
PROMOTION_ELIGIBLE = NO
PROSPECTIVE_HOLDOUT_CONSUMED = NO
ALPHA = UNPROVEN
CANDIDATE_FROZEN = NO
PAPER = NOT_STARTED
LIVE = DISABLED
PRIVATE_API = DISABLED
```
