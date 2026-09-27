# PR #17 Decomposition Plan — 2026-09-27

## Snapshot

Read-only GitHub/API inspection on 2026-09-27 found PR #17 open against
`main` at base `39e76ce0befab26e9b1f8cb5b83805ef1ee152ad`, with head
`ebe8159a00d9dc3330c035596acd59a14944043f`. It contains 71 commits and 289
changed files. Commit `cc29e8adb5` begins the external BitMEX research lane:
50 commits precede it, and 21 commits run from it through the current head.

The original PR branch and history were not changed. This document is a
proposed decomposition only; it does not authorize merge, squash, cherry-pick,
or PR creation.

## Current file footprint

| Area | Files |
|---|---:|
| Runtime source | 20 |
| Runtime tests and fixtures | 25 |
| Runtime/operations scripts | 11 |
| External research source | 15 |
| External research tests | 10 |
| External research scripts | 8 |
| Research registries and manifests | 11 |
| Reliability artifacts | 162 |
| Separate evidence records | 2 |
| Project-state reports | 7 |
| Documentation | 16 |
| Root files | 2 |
| **Total** | **289** |

## Proposed review sequence

| Slice | Proposed content | Dependency / merge gate |
|---|---|---|
| R1 — collector and archive reliability | Runtime source, tests, fixtures, and operations scripts (candidate envelope: 56 files); keep each behavior change with its focused regression tests. | First code slice. No AWS launch or runtime mutation as part of review. |
| R2 — validation evidence archive | Split the 162 reliability artifacts, 2 evidence records, and 7 project-state files by immutable validation identity (30H, 3H, 6H, 90m). Keep the original bytes, hashes, failed/superseded identities, and status labels intact. | Follow the source revision each record validates. Never combine separate identities into a synthetic PASS. |
| D1 — BitMEX dataset custody and ingestion | Registry/schema/manifests, source-attribution policy, local input contract, canonical ingestion, reproducibility, DQ, independent verifier, and matching tests/scripts. Raw source archives stay out of Git. | Independent research foundation; offline/public data only. |
| D2 — execution and contract reconstruction | Contract registry, position/cycle reconstruction, stratified audit and their tests. Keep `AMBIGUOUS`/`UNVERIFIED` fail-closed. | Depends on D1. Resolve this audit's XBT option-prefix fallback, `YFIUSDTZ20` family mismatch, and unproven fee assumptions before using monetary or PnL outputs. |
| D3 — market context and descriptive behavior | Public market context acquisition/joining, behavior and fee summaries, analysis tests, and research firewall. Preserve as-of checks and clearly label any external-data result `HYPOTHESIS_GENERATION_ONLY`. | Depends on D1; D2 required before using contract-unit or fee-derived claims. Historical BBO may be collected only with coverage manifests; do not imply full depth or queue evidence. |
| D4 — documentation alignment | Move each reliability or research report with the code/evidence slice it explains; update root README and status docs last. Keep historical reports as dated records with explicit supersession links. | After R1/R2/D1–D3 review; no broad rewrite of prior evidence. |

The six slices above are review boundaries, not an instruction to retain one
large stacked PR. Each can be converted into a narrowly scoped PR after the
owner approves the decomposition. PR #17 remains the unchanged source snapshot
until then.

## Extraction rules

1. Start each new review branch from the then-current authoritative `main`.
   Extract only the intended paths and their required dependencies. Record the
   source commit(s) for traceability; do not force-push or rewrite PR #17.
2. Keep runtime code and tests together. Keep validation evidence grouped by
   its sealed run identity. Never use evidence from one run to qualify another.
3. Keep external raw bytes and derived research tables in the ignored local
   research area. Commit only code, schemas, manifests, and aggregate reports
   whose provenance and limits are clear.
4. Do not combine collector reliability with alpha/profitability claims.
   External trader data stays blocked from candidate promotion and trading.
5. For every slice, run its focused tests first; run broader tests where shared
   interfaces changed. Record command, exit status, commit, and changed files.
   Research slices additionally verify data hashes, deterministic rebuilds,
   no-lookahead, and the firewall's negative cases.
6. If extraction needs a newly constructed patch history, keep the original
   PR #17 history and ref intact. Review the new branch as new commits; never
   treat path extraction as preserving original commit identity.

## Known blockers for a clean split

- The current PR combines 50 pre-research commits with 21 research/follow-up
  commits, and its artifact set spans many historical validation identities.
- Contract metadata from the current official endpoint verifies symbol-level
  structure for the joined 46-symbol file, but not full-life spec changes or
  historical fees. `XBT7D_U110` is an option-series symbol that prefix fallback
  can misclassify; `YFIUSDTZ20` is a dated quanto future rather than a linear
  USDT perpetual. See
  [the research gap closure](external-bitmex-research-gap-closure-20260927.md).
- Historical BBO is publicly queryable in sampled 2018–2021 periods, but full
  coverage has not been collected or audited. Historical full-depth and queue
  evidence remain absent.
- No research result in this PR changes `ALPHA = UNPROVEN`,
  `PAPER = NOT_STARTED`, `LIVE = DISABLED`, or `PRIVATE_API = DISABLED`.
