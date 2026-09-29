# Producer-to-Auditor Schema Map — 2026-09-29

The machine-readable, per-check field map is `30h-auditor-schema-map-20260929.json`. It records every original auditor check, expected and observed schema, producer source, whether evidence is native or reconstructed, verdict behavior, and exactly one mismatch class from the preregistered taxonomy.

## Verified schema findings

- The original auditor expects runtime commit/tree `22e06b9` / `a44c959`; the sealed run identity is commit/tree `b4d4823` / `5c96ed7`. The original defaults therefore fail `runtime_identity`; pinning the exact run lets that check pass.
- The runtime config stores Bithumb redundancy as `bithumb_redundancy.mode=ACTIVE_ACTIVE` and `physical_connections=2`. The auditor expects flat booleans/counts (or a different nested object), so it reports `NOT_VERIFIABLE` despite equivalent sealed settings.
- The sealed identity has `feed_count=76` but no qualifying cohort list or feed-universe list. The runtime config uses a duration-only `IMMEDIATE` schedule with zero explicitly required full hours. The pinned runtime source defines the 76-feed universe and labels cohorts `QUALIFYING_FULL_HOUR` or `TOUCHED_PARTIAL`; the cohort list must be reconstructed from actual start/end and then checked against per-feed receipts. The reconstruction remains explicitly labeled as such.
- The archive scheduler writes a qualifying cohort summary locally and attempts a remote copy. Partial/non-qualifying cohort summaries are written locally and return before the qualifying remote upload. Raw/coverage file receipts carry remote keys, checksums and version IDs. Whole-tree local/S3 parity is therefore the wrong scope.
- The runtime does not emit the original auditor's complete finalization event trace. Write-ahead progress, journals, logs and receipts can answer some questions weakly, but must not be relabeled as native trace events.
- `terminal_witness.py` sets `s3_uploaded=true` and writes those bytes before the stable `put_object` completes. A live terminal PASS must be independently verified by exact S3 GET, byte/hash parity and a captured `VersionId`.
- The original evidence hash index proves only that listed files match listed hashes. It does not prove completeness or authenticity if an editor changes a file and regenerates the index.
- The publisher supervisor accepts `-SIGTERM` only when it stops the publisher after collector completion; the original auditor requires all component exits to equal zero.

## Source evidence

- Runtime identity/config: sealed `identity.json`, `sealed-manifest.json`, and `aws-validation-observability-30h-20260929-20260929T055022Z-52f3d272.runtime.json` in the specified evidence worktree.
- Full-hour labeling and feed set: `src/bithumb_coin_trader/feed_hour_coverage.py` and `src/bithumb_coin_trader/closed_hour_finalizer.py` at runtime commit `b4d4823`.
- Cohort/slot durability: `scripts/orchestrate_closed_hour_archive.py` and `src/bithumb_coin_trader/pre_soak_archive.py` at runtime commit `b4d4823`.
- Witness upload ordering: `scripts/terminal_witness.py:225-263` at runtime commit `b4d4823`.
- Supervisor exits: `src/bithumb_coin_trader/bounded_supervisor.py:484-563` at runtime commit `b4d4823`.

No current run terminal verdict is included here. Missing trace/readback evidence remains `NOT_VERIFIABLE` until the run naturally completes and the exact post-run artifacts are captured.
