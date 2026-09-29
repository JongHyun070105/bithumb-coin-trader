# Original 30H Auditor Reproduction — 2026-09-29

## Scope and provenance

- Runtime source: commit `b4d482363e2f988dad9c6d29053f97e1e4160883`, tree `5c96ed79fee107c1604ee7018621835910221fdf`.
- Original auditor: `scripts/audit_fresh_30h_v3_terminal.py`.
- Original auditor SHA256: `7e5ac931e7d2e1e7c900e56d1ddfa3111c4bb2470dc8f6a9e2d5c53884405b64`.
- Sealed source files were read from the specified preflight evidence worktree. The exact sealed identity and runtime input hashes were `b9940edef0827165ef38a27aaf25bbeebc72ef2a160105d2daa6e93984250b48` and `20f5a4983c18c4e057ec5b0c4175eb3d4f06f5222bdd2fc4eb4c2600ff299951`.
- Reproduction outputs and synthetic bundles are preserved locally under `/private/tmp/fresh30h-auditor-repro-20260930/`; this directory contains the sealed identity copy and is intentionally not committed.

## Cases

| Case | Inputs and procedure | Overall | Failed checks | Not verifiable checks | Raw report SHA256 |
|---|---|---|---|---|---|
| A — original synthetic fixture | Existing `_make_bundle` fixture in `tests/test_fresh_30h_v3_terminal_audit.py`; original auditor defaults. | `PASS` | none | none | `8310530ef53dc607eb00babace6b57c36ab611279cbe29ff1589297c0ed74f17` |
| B — real sealed schema, exact runtime pin | Same synthetic outcome receipts and metrics; substitute exact sealed `identity.json` and runtime JSON; align synthetic run/epoch and 30-hour timestamps to those sealed identifiers; pin expected runtime commit/tree to the sealed identity. | `NOT_VERIFIABLE` | none | `active_active_dedup_and_conflicts`, `qualifying_cohorts_and_feed_counts`, `all_feed_slots_and_receipts` | `b793b7b6c740307c4f1f2cbe36b7826694969e46ceabf3b6109a4297144bb721` |
| B-default — same substitution, original auditor defaults | Same as B, but retain the original auditor's default expected runtime commit/tree. | `FAIL` | `runtime_identity` | same three checks as B | `739e0ea82806d5bfa9af2ddcb6ecdbb3e59438aeffa3ba0d7e5bf11e2629fdf` |

The synthetic receipt set in B remains a two-slot fixture, so this is a schema-adapter reproduction, not a current-run coverage or terminal adjudication. B's input outcome fields were changed only to bind the fixture to the real run/epoch and 108000-second interval. The original auditor correctly reports `NOT_VERIFIABLE` for the three missing expected schemas when identity pins are made exact; without the pin override, its stale hard-coded commit/tree also causes `FAIL`. This narrows Claude's claim: it reproduces at the aggregate contract level only with the exact runtime pinned, while the default invocation produces `FAIL` plus those same `NOT_VERIFIABLE` checks.

The raw reports, both bundles, and their file hashes are retained in the local evidence directory above. These results are synthetic and must never be presented as the original or amended verdict for the live run.
