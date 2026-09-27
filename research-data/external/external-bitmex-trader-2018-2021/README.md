# External BitMEX Trader Dataset (2018-2021)

- **Dataset ID:** `external-bitmex-trader-2018-2021`
- **Scientific Role:** `EXTERNAL_EXPERT_BEHAVIOR_DATASET`
- **Primary Use:** `HYPOTHESIS_GENERATION_ONLY`
- **Author Identity:** `NOT_INDEPENDENTLY_VERIFIED`
- **Archive SHA-256:** `b6f1dc7aadf8209bf6c99fd516a06c0cabdc77c5f16f9fc8df92fdf1d8d01b9a`
- **Original Filename:** `aoa_public_2021-12-31_with_letter.zip`

---

## 1. Directory Layout & Gitignore Rules

All raw downloads, extracted source CSV files, and heavy derived Parquet partitions are stored strictly in `.external-research-data/` which is **permanently gitignored** (`.gitignore:39`).

```text
.external-research-data/external-bitmex-trader-2018-2021/  [GITIGNORED]
├── raw/                                                  [GITIGNORED - Immutable Raw Source]
│   ├── aoa_public_2021-12-31_with_letter.zip
│   ├── aoa-execution-2018-03-01-2018-12-31.csv
│   ├── aoa-execution-2019-01-01-2020-12-31.csv
│   ├── aoa-execution-2021-01-01-2021-06-30.csv
│   ├── aoa-execution-2021-07-01-2021-12-31.csv
│   ├── aoa-wallet-2018-03-01-2021-12-31.csv
│   └── 90일 서한.txt
├── derived/                                              [GITIGNORED - Parquet & Large Artifacts]
│   ├── parquet/
│   │   ├── execution/year=YYYY/month=MM/symbol=SYM/*.parquet
│   │   ├── funding/year=YYYY/month=MM/*.parquet
│   │   ├── settlement/year=YYYY/*.parquet
│   │   └── wallet/year=YYYY/month=MM/*.parquet
│   ├── derived-manifest.json
│   ├── pipeline-summary.json
│   ├── order-reconstruction-summary.json
│   ├── wallet-reconciliation.json
│   ├── behavior-analysis.json
│   └── external-context-document.json
└── verification/                                         [GITIGNORED - Local Profile & Audit]
    ├── source-manifest.json
    ├── schema-profile.json
    └── data-quality-report.json

research-data/external/external-bitmex-trader-2018-2021/  [TRACKED IN GIT - Schemas & Metadata]
├── README.md                                             (This file)
├── SOURCE.md                                             (Provenance & handling instructions)
├── dataset-spec.json                                     (Machine-readable allowed/prohibited uses)
├── manifest.schema.json                                  (Schema for source-manifest.json)
├── future-features.json                                  (Planned feature taxonomy)
├── hypotheses.json                                       (Registered hypothesis ledger)
└── dashboard-contract.json                               (Row-free dashboard contract)
```

---

## 2. Ingestion & Canonical Tables

The pipeline normalizes the heterogeneous raw files into four canonical tables:

1. **`external_execution`** (1,439,207 rows): Trade execution records (orderid, execid, price, size, side, maker/taker, fee).
2. **`external_funding`** (5,368 rows): 8-hour BitMEX funding payments/receipts.
3. **`external_settlement`** (8 rows): Expiry settlement for quarterly futures.
4. **`external_wallet`** (2,253 valid rows; 2,135 blank rows audited & filtered): Realised PnL, deposits, withdrawals.

### Provenance Tracking
Every row in the canonical tables retains explicit provenance:
`dataset_id`, `source_file`, `source_row_number`, `source_sha256`, and `ingestion_version`.

### Canonical Ordering
Raw CSV dumps contain 4,320 physical timestamp reversals. The canonical pipeline deterministically sorts all events by `transact_time`, `timestamp`, `order_id`, and `execution_id`, preserving both timestamps.

---

## 3. Data Quality Findings Summary

- **Total Execution Rows:** 1,444,583 (Trade: 1,439,207 | Funding: 5,368 | Settlement: 8).
- **Execution ID Uniqueness:** 1,444,583 unique IDs, **0 duplicates** (`PASS`).
- **Timestamp Nulls:** 0 nulls across execution rows (`PASS`).
- **Referential Consistency:** `cumqty + leavesqty == orderqty` holds across 100% of trade records (`PASS`).
- **Anomalies:** 0 price anomalies, 0 negative sizes, 0 invalid fees (`PASS`).
- **Wallet Blank Rows:** 2,135 trailing blank rows safely filtered without forward-filling (`WARNING`).
- **Wallet Reconciliation:** Total deposits (14.49 BTC) + Realised PnL (3,537.32 BTC) - Withdrawals (2,832.53 BTC) = 719.28 BTC final balance, exact to the satoshi (`PASS`).

---

## 4. Research Firewall Rules

- **Allowed:** Behavioral description, sizing heuristics, hypothesis generation, execution regime analysis.
- **Prohibited:** Candidate selection, prospective holdout, final validation gate, claim of alpha, live trading justification.
- **Scientific State:** `ALPHA=UNPROVEN`, `PAPER=NOT_STARTED`, `LIVE=DISABLED`, `PRIVATE_API=DISABLED`.
