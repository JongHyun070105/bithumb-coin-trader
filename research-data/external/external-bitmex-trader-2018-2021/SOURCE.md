# External BitMEX trader dataset: source and handling

Dataset ID: `external-bitmex-trader-2018-2021`  
Source class: `EXTERNAL_EXPERT_BEHAVIOR_DATASET`  
Research role: `HYPOTHESIS_GENERATION_ONLY`

## Public provenance

- **Original public release:** ["거래내역 공개합니다.", DC Inside Chart Gallery](https://gall.dcinside.com/mgallery/board/view/?id=chartanalysis&no=5051684), posted **2026-09-22 17:46:03 KST** under the displayed name `워뇨띠`.
- **Original download location:** [Google Drive file linked by that post](https://drive.google.com/file/d/1XDwxbriz_kOq44iH-mHcjsYTBklMnMW3/view?usp=sharing), labelled `aoa_public_2021-12-31_with_letter.zip` on the post.
- **Source pages checked:** 2026-09-23. The underlying archive was **not downloaded, opened, or independently verified** while preparing this lane.
- **Publisher's description:** about 1.4 million rows and about 600 MB after extraction, including BitMEX execution records and wallet/account history. [Contemporaneous coverage](https://coinness.com/en/news/1169502) reports stated coverage of **2018-03 through 2021-12**. These numbers and dates remain claims until the actual files are profiled.
- **Publisher's privacy statement:** wallet addresses, transaction IDs, and other identifiers considered unnecessary for checking the trades were reportedly removed. This has not been independently checked.
- **Publisher's usage request:** analysis and program creation were welcomed, while paid resale of derivatives or trading bots based on these records was requested to be avoided. This is a publisher request; no standalone legal license was supplied or inferred here.

**PUBLIC_RELEASE_CONFIRMED:** the release post and direct download link are publicly visible.  
**AUTHOR_IDENTITY_NOT_INDEPENDENTLY_VERIFIED:** the person who posted the archive, the account holder, and the trader commonly known as `워뇨띠` have not been independently linked by this project. Public trade-tape matches can support record authenticity but cannot prove account ownership.

## Local import and governance

The repository bundles no raw rows or archive. Download the file from the original link manually where possible, then run:

```bash
python scripts/prepare_external_bitmex_dataset.py --input /absolute/path/to/aoa_public_2021-12-31_with_letter.zip
```

Use repeated `--input` options if the original release arrives as separate files; import them in a single batch. The helper copies exact bytes into `.external-research-data/external-bitmex-trader-2018-2021/raw/`, extracts safe ZIP members there, hashes each file, profiles CSV/TSV members by streaming, and seals `source-manifest.json`. It refuses conflicting existing bytes or additions to a sealed manifest. Never stage or redistribute the raw files. Preserve the source page, source file hashes, and any future transformation lineage on all derived data. Derived research artifacts should contain aggregates or minimal evidence, not unnecessary raw rows.

`research-data/dataset_registry.json` stores the **scientific role**; the local `source-manifest.json` stores **byte-level source identity**; `verification/schema-profile.json` stores **mutable parser observations**. The existing `research_infra.manifests.ResearchManifest` remains the experiment-result provenance model. Neither the external manifest nor its profile is a prospective market-data receipt.

## Known limits

- Actual CSV schema, row counts, completeness, record authenticity, and balances are unknown until import and verification.
- Reported coverage does not prove that every execution or wallet event in the interval is present.
- BitMEX contract quantity, settlement currency, funding, and realized PnL semantics must be checked per observed instrument before position or equity accounting.
- This dataset can inform behavioral, execution, sizing, risk, and regime questions. It is prohibited as prospective validation, final holdout, candidate-promotion evidence, proof of alpha, or a reason to enable live trading.
- Any resulting hypothesis must later be tested on independently collected prospective project data.

Current scientific state: `ALPHA=UNPROVEN`, `PAPER=NOT_STARTED`, `LIVE=DISABLED`, `PRIVATE_API=DISABLED`.
