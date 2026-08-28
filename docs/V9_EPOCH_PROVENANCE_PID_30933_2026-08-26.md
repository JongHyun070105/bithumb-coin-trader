# V9 epoch provenance — PID 30933

This record freezes the evidence that identifies the running V9 72-hour epoch separately from the later V9.1 working tree. Corrections must be made in a new superseding record; this file must not be silently rewritten after the final artifact freeze.

## Verified process identity

- PID: `30933`
- OS process start: `2026-08-26 01:19:33 KST` (`2026-08-25 16:19:33 UTC`)
- Continuous 72-hour boundary: `2026-08-29 01:19:33 KST` (`2026-08-28 16:19:33 UTC`)
- Working directory: `/Users/macintosh/Documents/ChatGPT/bitcoin-trader`
- Python executable: `.venv/bin/python`, resolving to Homebrew Python `3.14.6`
- Import path: `PYTHONPATH=src`
- Sanitized launch command: `scripts/run_cross_market_collector.py --bithumb-markets 20`
- Duration argument: absent, therefore indefinite daemon mode
- No credential values are included in this record.

The process start, command, working directory, interpreter, and import path were recovered from the live process metadata. Canonical raw appends were independently observed while this PID remained alive.

## Observed epoch configuration

- Bithumb: 20 markets — `KRW-BTC`, `KRW-ETH`, `KRW-XRP`, `KRW-SOL`, `KRW-DOGE`, `KRW-ADA`, `KRW-XLM`, `KRW-LINK`, `KRW-AVAX`, `KRW-BCH`, `KRW-ETC`, `KRW-NEAR`, `KRW-SUI`, `KRW-APT`, `KRW-TRX`, `KRW-SHIB`, `KRW-SAND`, `KRW-MANA`, `KRW-AXS`, `KRW-DOT`
- Binance: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`, trade and partial-depth orderbook streams
- Upbit: `KRW-BTC`, `KRW-ETH`, `KRW-SOL`, `KRW-XRP`, trade and orderbook streams
- Storage root: `data/microstructure/raw`

The market/stream set is corroborated by the launch script reference snapshot and by partition names present around the PID boundary. Binance orderbook partitions are stored as `UNKNOWN`, which is retained as an official finding for this epoch.

## Git reference snapshot — not a launch-time commit claim

The audit later observed repository HEAD `608521870a31e2579ca310eb90e53c86c861da50`. Its commit time is `2026-08-26 22:36:07 KST`, after PID 30933 started. Therefore it is an immutable Git reference snapshot for reconstructing the subsequently committed V9 source, but it must **not** be described as the verified launch-time HEAD or as direct proof of the exact in-memory source.

- Commit: `608521870a31e2579ca310eb90e53c86c861da50`
- Tree: `65c0f2264844f7f702420c36b3d1791db317f608`

| Reference file at `6085218` | Git blob | SHA-256 of blob content |
|---|---|---|
| `scripts/run_cross_market_collector.py` | `315efe8660b19caf3194440724234dcf431906a9` | `f704ea276e3e611f3a60e05b3db9b4fe96d37ec3beeefdf07c24c2b094537d86` |
| `src/bithumb_coin_trader/cross_market_collector.py` | `9a9814e91dbf644a8ce44575faddbf26f83ca18c` | `8f480a366774981c3f0d083e1ac378591cab0ceabaed3f4263038cc001646fb5` |
| `src/bithumb_coin_trader/microstructure_storage.py` | `7992afea04bc8ac9c72b1f11635748a4b945e36f` | `4fe2b01a6b334fcebc4aed2b728faf049117b659b4828787b21db7d204cab7b4` |
| `src/bithumb_coin_trader/dynamic_universe.py` | `33fa72bf7d24de7231a68807fae462b1b3ea307d` | `b801c34e6296c24e70722ea545a02a23b6fe5262cf631eae85e3007361bba620` |

Exact launch-time in-memory source fingerprint: **NOT DIRECTLY VERIFIABLE**. The collector files were committed after process start and no independently sealed pre-launch source hash has been found. Runtime behavior and raw layout are consistent with the later V9 reference snapshot, but that is corroboration, not byte-for-byte proof.

## Current V9.1 preparation working tree — explicitly not loaded by PID 30933

Captured after the independent remediation review on `2026-08-27 02:36 KST`:

| Current working-tree file | SHA-256 |
|---|---|
| `scripts/run_cross_market_collector.py` | `f704ea276e3e611f3a60e05b3db9b4fe96d37ec3beeefdf07c24c2b094537d86` |
| `src/bithumb_coin_trader/cross_market_collector.py` | `eb71d56d664779a1302576d20bd0def0fb08bfee3de03da01144fe2013244f8e` |
| `src/bithumb_coin_trader/microstructure_storage.py` | `23bc8e7a54e4929fb290f17f051aff03e5a37a90b842a0d0e810c5b0f840048e` |
| `src/bithumb_coin_trader/dynamic_universe.py` | `b801c34e6296c24e70722ea545a02a23b6fe5262cf631eae85e3007361bba620` |

These working-tree hashes are V9.1 preparation evidence only. Python PID 30933 continues executing modules loaded before these edits and must not be attributed the corrected Binance symbol handling, monotonic receive timestamp, durable metrics, manifest extensions, or writer fail-closed behavior.

## Final-audit attribution rule

The final 72-hour report must keep three objects separate:

1. the verified live process identity and observed raw epoch configuration above;
2. the `6085218` Git reference snapshot, explicitly labelled as post-start corroborating provenance;
3. the then-current V9.1 working tree, test evidence, and fingerprints, explicitly labelled as not loaded by PID 30933.

The exact in-memory source must remain `NOT DIRECTLY VERIFIABLE` unless new independent launch-time evidence is recovered. This limitation does not change the exact uninterrupted-process stop boundary.
