# Repository Branch and PR Map — 2026-09-28

## Snapshot

- Main checkout branch: `main` at `0b819eaa7af39b9a30e74ddadef67e8a677fc6be`, 6 commits behind `origin/main` at inspection; its untracked user-owned `.agents/`, `.codex/`, `.commandcode/`, `AGENTS.md`, and `test-results/` were preserved.
- Final-preflight worktree: `codex/final-30h-preflight-20260928` at `ea42d3672df076047048a4429fe5ce17eb092b58` before overnight report updates.
- Branch inventory: 67 local branches; 21 remote refs (20 branches plus `origin/HEAD`); 26 worktrees, 9 detached.
- PR inventory: 23 total, 12 open and 11 merged; 0 closed-unmerged.
- Cleanup actions: 0 worktrees removed, 0 remote branches deleted, 0 PRs updated, 0 PRs closed. This avoids removing evidence-bearing or context-uncertain worktrees and preserves the stacked PR graph.
- No automatic merge or remote cleanup was performed.

## Pull requests

Unique commit count is measured as `git rev-list --count <head SHA> ^<base SHA>` using the live GitHub head/base SHAs returned at inspection. “Recommended action” is a preservation/review disposition, not authorization to merge or close.

| PR | Head branch @ SHA | Base | State | Class | Unique commits | Superseded by / dependency | Purpose / recommended action |
|---:|---|---|---|---|---:|---|---|
| #1 | `codex/fix-s3-archive-missing-key-contract-20260910` @ `53310035` | `main` | MERGED | `HISTORICAL_EVIDENCE` | 5 | —; merged historical PR | fix(archive): remove S3 existence preflight; Merged; preserve release and archive contract history. |
| #2 | `codex/aws-fresh45-prep-20260911-1976f0f` @ `09a2fdaa` | `main` | MERGED | `HISTORICAL_EVIDENCE` | 5 | —; merged historical PR | docs(aws): record successful Fresh45 validation evidence and audit hardening; Merged; preserve Fresh45 evidence. |
| #3 | `codex/support-30h-transient-duration` @ `a245d517` | `main` | MERGED | `HISTORICAL_EVIDENCE` | 2 | —; merged historical PR | fix(aws): support sealed 30h transient validation duration; Merged; preserve duration support. |
| #4 | `codex/fix-bounded-supervisor-publisher-lifecycle-test` @ `b43d6b9b` | `main` | MERGED | `HISTORICAL_EVIDENCE` | 1 | —; merged historical PR | test(supervisor): make publisher lifecycle ordering deterministic; Merged; preserve regression-test history. |
| #5 | `codex/aws-30h-prep-v2-20260912-6576f63` @ `8523684d` | `main` | MERGED | `HISTORICAL_EVIDENCE` | 4 | —; merged historical PR | docs(aws): preserve failed 30h V2 evidence; Merged; preserve failed V2 evidence. |
| #6 | `codex/aws-30h-v3-remediation-20260914` @ `a78f655f` | `main` | MERGED | `HISTORICAL_EVIDENCE` | 39 | —; merged historical PR | Remediate AWS 30H V3 evidence architecture; Merged; preserve V3 remediation lineage. |
| #7 | `codex/aws-30h-v3-preparation-20260915-e9d5d5a` @ `40f014cb` | `main` | MERGED | `HISTORICAL_EVIDENCE` | 1 | —; merged historical PR | Prepare official AWS 30H V3 validation artifacts and seals; Merged; preserve V3 preparation. |
| #8 | `codex/aws-30h-v3-seal-closure-20260915-ac81f94` @ `faa7ad1a` | `main` | MERGED | `HISTORICAL_EVIDENCE` | 1 | —; merged historical PR | Close post-merge AWS 30H V3 runtime seals; Merged; preserve V3 seal closure. |
| #9 | `docs/reliability-status-20260919` @ `a24b1211` | `main` | MERGED | `HISTORICAL_EVIDENCE` | 1 | —; merged historical PR | docs: update collector reliability validation status; Merged; preserve reliability status. |
| #10 | `docs/reliability-history-correction-20260919` @ `252544a9` | `main` | MERGED | `HISTORICAL_EVIDENCE` | 1 | —; merged historical PR | docs: correct reliability validation history; Merged; preserve historical correction. |
| #11 | `docs/validation-evidence-index-20260920` @ `f4091209` | `main` | MERGED | `HISTORICAL_EVIDENCE` | 1 | —; merged historical PR | docs: update validation evidence index; Merged; preserve evidence index. |
| #12 | `gpt/post-30h-integration-20260921` @ `ee6d57c6` | `main` | OPEN | `READY_FOR_REVIEW` | 42 | V2 record retained; later V3 evidence integration is #21 | Integrate Fresh 30H-v2 terminal FAIL audit and archive scheduler fix; Open and non-draft; review terminal FAIL adjudication and tests, do not auto-merge. |
| #13 | `gpt/bithumb-gap-forensics-20260921` @ `e8e6811e` | `gpt/post-30h-integration-20260921` | OPEN, draft | `ACTIVE_REQUIRED` | 3 | Stack dependency: base is #12 | Bithumb 30H gap forensics and reliability hardening; Open stacked gap-forensics branch; preserve base relationship to #12. |
| #14 | `gpt/bithumb-redundancy-hardening-20260921` @ `680a5f4c` | `gpt/bithumb-gap-forensics-20260921` | OPEN, draft | `ACTIVE_REQUIRED` | 2 | Stack dependency: base is #13 | Harden Bithumb collection with active-active redundancy; Open stacked redundancy branch; preserve base relationship to #13. |
| #15 | `gpt/external-bitmex-expert-dataset-prep-20260923` @ `9f3aaca3` | `gpt/fresh-6h-v4r1-failure-fix-20260922` | OPEN, draft | `RESEARCH_ONLY` | 2 | Branch is stacked on #16; related newer external-data work is #17 | research: prepare external BitMEX expert dataset lane (hypothesis-gen only); preserve stack and hypothesis-generation-only boundary. |
| #16 | `gpt/fresh-6h-v4r1-failure-fix-20260922` @ `94b9293e` | `gpt/bithumb-redundancy-hardening-20260921` | OPEN, draft | `ACTIVE_REQUIRED` | 3 | Stack dependency: base is #14; #15 is based on this branch | fix: Fresh 6H-v4r1 runtime remediation (Bithumb dedup + coverage scoping); Open stacked Fresh 6H remediation; preserve base relationship to #14. |
| #17 | `gpt/external-bitmex-prep-20260927` @ `ebe8159a` | `main` | OPEN, draft | `RESEARCH_ONLY` | 71 | No full successor identified; research-only | feat(research): external BitMEX expert dataset deep audit and validation; Open external BitMEX dataset preparation; hypothesis generation only. |
| #18 | `codex/pre-30h-readiness-20260927` @ `bfdab735` | `main` | OPEN, draft | `RESEARCH_ONLY` | 33 | Reliability readiness has newer work in #23; research scope remains distinct | Research: governed backtest, walk-forward, and paper readiness; Open governed research/readiness; paper remains not started. |
| #19 | `fix/autonomous-daemon-default-off-20260928` @ `8249c716` | `main` | OPEN, draft | `ACTIVE_REQUIRED` | 1 | No successor identified; safety work retained | fix: disable autonomous live daemon by default; Open default-live safety change; review and preserve. |
| #20 | `docs/overnight-30h-closure-20260928` @ `9d965848` | `main` | OPEN, draft | `HISTORICAL_EVIDENCE` | 3 | No successor identified; closure evidence retained | docs: record overnight 30h closure checkpoint; Open overnight closure evidence; preserve historical findings. |
| #21 | `codex/post-30h-evidence-integration-20260928` @ `49467ceb` | `main` | OPEN, draft | `HISTORICAL_EVIDENCE` | 39 | No successor identified; historical evidence retained | docs: adjudicate Fresh 30H-v3 and integrate post-run readiness; Open post-30H evidence integration; preserve official FAIL. |
| #22 | `codex/fix-terminal-witness-binding-20260928` @ `49442356` | `fix/closed-hour-restart-idempotency-20260927` | OPEN, draft | `ACTIVE_REQUIRED` | 1 | Base of #23; no successor | fix(validation): bind terminal witness to sealed run; Open terminal witness binding; base of #23. |
| #23 | `codex/final-30h-preflight-20260928` @ `ea42d367` | `codex/fix-terminal-witness-binding-20260928` | OPEN, draft | `ACTIVE_REQUIRED` | 20 | Current preflight continuation; based on #22 | fix(validation): close Fresh 30H witness preflight gates; Current final-preflight branch; no 30H launch authorized. |

## Remaining local branches

These branches have no PR row above. “Outside main” counts commits not reachable from the current `origin/main`; this is a comparison aid, not a safe-deletion signal. Branches remain untouched.

| Branch @ SHA | Upstream | Commits outside origin/main | Disposition |
|---|---|---:|---|
| `codex/72h-offline-phase2-20260905` @ `ff1b971` | `origin/codex/72h-offline-research-hardening-20260905` [gone] | 0 | LOCAL_STALE; keep for manual review |
| `codex/72h-offline-phase2-forensic-20260905` @ `2c616ef` | `none`  | 0 | LOCAL_STALE; keep for manual review |
| `codex/72h-offline-phase3-closure-20260906` @ `0618734` | `none`  | 0 | LOCAL_STALE; keep for manual review |
| `codex/72h-offline-phase4-crosslayer-20260906` @ `e654f51` | `none`  | 0 | LOCAL_STALE; keep for manual review |
| `codex/72h-offline-phase5-postsoak-readiness-20260906` @ `753d784` | `none`  | 0 | LOCAL_STALE; keep for manual review |
| `codex/72h-offline-phase6-1-forensic-verification-20260906` @ `190654e` | `none`  | 0 | LOCAL_STALE; keep for manual review |
| `codex/72h-offline-phase6-2-evidence-chain-20260906` @ `3f4a323` | `origin/codex/72h-offline-phase6-2-evidence-chain-20260906` [gone] | 0 | LOCAL_STALE; keep for manual review |
| `codex/72h-offline-phase6-final-contract-20260906` @ `a9e52e6` | `origin/codex/72h-offline-phase6-final-contract-20260906` [gone] | 0 | LOCAL_STALE; keep for manual review |
| `codex/72h-offline-research-hardening-20260905` @ `ba89d60` | `origin/codex/72h-offline-research-hardening-20260905` [gone] | 0 | LOCAL_STALE; keep for manual review |
| `codex/72h-soak-systemd-recovery-20260905` @ `f1332b5` | `origin/codex/72h-soak-systemd-recovery-20260905` [gone] | 1 | LOCAL_STALE; keep for manual review |
| `codex/aws-30h-prep-20260912-ac0351c` @ `4463fd8` | `origin/codex/aws-30h-prep-20260912-ac0351c` [gone] | 1 | LOCAL_STALE; keep for manual review |
| `codex/aws-30h-prep-v1-20260912-00464e5` @ `9753a06` | `origin/codex/aws-30h-prep-v1-20260912-00464e5` [gone] | 1 | LOCAL_STALE; keep for manual review |
| `codex/aws-30h-v3-launch-authorization-20260915` @ `3e7bcd9` | `origin/codex/aws-30h-v3-launch-authorization-20260915` [gone] | 0 | LOCAL_STALE; keep for manual review |
| `codex/aws-45m-independent-audit-20260909` @ `666a350` | `origin/codex/aws-45m-independent-audit-20260909` [gone] | 0 | LOCAL_STALE; keep for manual review |
| `codex/aws-45m-remediation` @ `9c3e96a` | `origin/codex/aws-45m-remediation` [gone] | 0 | LOCAL_STALE; keep for manual review |
| `codex/aws-45m-remediation-validation-20260909` @ `270dbe3` | `none`  | 0 | LOCAL_STALE; keep for manual review |
| `codex/aws-72h-soak-planning` @ `f23b67b` | `none`  | 0 | LOCAL_STALE; keep for manual review |
| `codex/aws-fresh45-prep-20260910-1976f0f` @ `1976f0f` | `none`  | 0 | LOCAL_STALE; keep for manual review |
| `codex/dashboard-v0-2-contract-reconciliation-20260907` @ `c70f21e` | `origin/codex/dashboard-v0-2-contract-reconciliation-20260907` [gone] | 0 | LOCAL_STALE; keep for manual review |
| `codex/microstructure-research-infra-v1-20260915` @ `7a13816` | `none`  | 0 | LOCAL_STALE; keep for manual review |
| `codex/post-30h-integration-20260926` @ `a35f008` | `none`  | 54 | LOCAL_STALE; keep for manual review |
| `codex/post-72h-data-quality-tooling` @ `acdfa0e` | `origin/codex/post-72h-data-quality-tooling` [gone] | 6 | LOCAL_STALE; keep for manual review |
| `codex/post-72h-final-audit-20260908` @ `c2aa0d5` | `origin/codex/post-72h-final-audit-20260908` [gone] | 0 | LOCAL_STALE; keep for manual review |
| `codex/post72h-final-decision-20260909` @ `26afad3` | `origin/codex/post72h-final-decision-20260909` [gone] | 0 | LOCAL_STALE; keep for manual review |
| `codex/post72h-runtime-remediation-v1-20260909` @ `4b1c7d4` | `origin/codex/post72h-runtime-remediation-v1-20260909` [gone] | 0 | LOCAL_STALE; keep for manual review |
| `codex/premerge-phase6-2-main-verification` @ `b73f020` | `origin/main` [behind 158] | 0 | DO_NOT_TOUCH; tracked branch |
| `codex/premerge-phase6-3-dashboard-20260907` @ `0a45a66` | `origin/main` [behind 150] | 0 | DO_NOT_TOUCH; tracked branch |
| `codex/v2-30h-authoritative-profitability-study-20260916` @ `114fc8a` | `origin/codex/v2-30h-authoritative-profitability-study-20260916` [gone] | 0 | LOCAL_STALE; keep for manual review |
| `codex/v2-microstructure-profitability-study-20260916` @ `a4edae7` | `origin/codex/v2-microstructure-profitability-study-20260916` [gone] | 2 | LOCAL_STALE; keep for manual review |
| `develop` @ `9615be8` | `origin/develop`  | 39 | DO_NOT_TOUCH; tracked branch |
| `dry-run/not-for-merge/post30h-integration-20260928` @ `f341f7e` | `origin/main` [ahead 35] | 35 | DO_NOT_TOUCH; tracked branch |
| `feat/quant-dashboard-ui` @ `e080e14` | `origin/feat/quant-dashboard-ui` [gone] | 0 | LOCAL_STALE; keep for manual review |
| `fix/closed-hour-restart-idempotency-20260927` @ `48cfa0a` | `origin/fix/closed-hour-restart-idempotency-20260927`  | 53 | DO_NOT_TOUCH; tracked branch |
| `gemini/dashboard-local-api-ledger-e2e-20260908` @ `22784e5` | `origin/gemini/dashboard-local-api-ledger-e2e-20260908` [gone] | 23 | LOCAL_STALE; keep for manual review |
| `gemini/dashboard-v0-2-evidence-console-20260907` @ `6cdea3c` | `origin/gemini/dashboard-v0-2-evidence-console-20260907` [gone] | 0 | LOCAL_STALE; keep for manual review |
| `gemini/dashboard-v0-3-korean-demo-20260908` @ `2da9363` | `origin/gemini/dashboard-v0-3-korean-demo-20260908` [gone] | 5 | LOCAL_STALE; keep for manual review |
| `gemini/dashboard-v0-3-trading-ui-20260908` @ `0a45a66` | `origin/main` [behind 150] | 0 | DO_NOT_TOUCH; tracked branch |
| `gemini/post72h-blocked-prep-20260908` @ `e698e9e` | `origin/gemini/post72h-blocked-prep-20260908` [gone] | 0 | LOCAL_STALE; keep for manual review |
| `gemini/post72h-interactive-evidence-20260908` @ `31846b7` | `origin/gemini/post72h-interactive-evidence-20260908` [gone] | 0 | LOCAL_STALE; keep for manual review |
| `gemini/post72h-light-integration-20260908` @ `7af600c` | `origin/gemini/post72h-light-integration-20260908` [gone] | 11 | LOCAL_STALE; keep for manual review |
| `gpt/aws-6h-v4-launch-20260921` @ `54d7d81` | `origin/gpt/aws-6h-v4-launch-20260921`  | 50 | DO_NOT_TOUCH; tracked branch |
| `gpt/fresh-6h-v5r1-failure-fix-20260926` @ `de1fc32` | `none`  | 53 | LOCAL_STALE; keep for manual review |
| `gpt/post-30h-v2-closeout-20260921` @ `1b7a499` | `origin/gpt/post-30h-v2-closeout-20260921`  | 41 | DO_NOT_TOUCH; tracked branch |
| `research/bitmex-gap-closure-20260927` @ `b593c18` | `origin/research/bitmex-gap-closure-20260927`  | 1 | DO_NOT_TOUCH; tracked branch |
| `research/fix-asof-alignment-20260928` @ `a5c78c2` | `origin/research/fix-asof-alignment-20260928`  | 3 | DO_NOT_TOUCH; tracked branch |

## Remote branch refs

All extant remote refs were retained. A branch without an open PR is not presumed disposable; historical audit references and stack dependencies require manual review.

| Remote ref @ SHA | Current PR / disposition |
|---|---|
| `origin` @ `39e76ce` | no current PR mapping; retain pending ancestry/evidence review |
| `origin/codex/final-30h-preflight-20260928` @ `ea42d36` | PR #23; retain |
| `origin/codex/fix-terminal-witness-binding-20260928` @ `4944235` | PR #22; retain |
| `origin/codex/post-30h-evidence-integration-20260928` @ `49467ce` | PR #21; retain |
| `origin/codex/pre-30h-readiness-20260927` @ `bfdab73` | PR #18; retain |
| `origin/develop` @ `9615be8` | no current PR mapping; retain pending ancestry/evidence review |
| `origin/docs/overnight-30h-closure-20260928` @ `9d96584` | PR #20; retain |
| `origin/fix/autonomous-daemon-default-off-20260928` @ `8249c71` | PR #19; retain |
| `origin/fix/closed-hour-restart-idempotency-20260927` @ `48cfa0a` | no current PR mapping; retain pending ancestry/evidence review |
| `origin/gpt/aws-6h-v4-launch-20260921` @ `54d7d81` | no current PR mapping; retain pending ancestry/evidence review |
| `origin/gpt/bithumb-gap-forensics-20260921` @ `e8e6811` | PR #13; retain |
| `origin/gpt/bithumb-redundancy-hardening-20260921` @ `680a5f4` | PR #14; retain |
| `origin/gpt/external-bitmex-expert-dataset-prep-20260923` @ `9f3aaca` | PR #15; retain |
| `origin/gpt/external-bitmex-prep-20260927` @ `ebe8159` | PR #17; retain |
| `origin/gpt/fresh-6h-v4r1-failure-fix-20260922` @ `94b9293` | PR #16; retain |
| `origin/gpt/fresh-6h-v5r1-failure-fix-20260926` @ `22e06b9` | no current PR mapping; retain pending ancestry/evidence review |
| `origin/gpt/post-30h-integration-20260921` @ `ee6d57c` | PR #12; retain |
| `origin/gpt/post-30h-v2-closeout-20260921` @ `1b7a499` | no current PR mapping; retain pending ancestry/evidence review |
| `origin/main` @ `39e76ce` | no current PR mapping; retain pending ancestry/evidence review |
| `origin/research/bitmex-gap-closure-20260927` @ `b593c18` | no current PR mapping; retain pending ancestry/evidence review |
| `origin/research/fix-asof-alignment-20260928` @ `a5c78c2` | no current PR mapping; retain pending ancestry/evidence review |

## Worktrees

No worktree met the full safe-removal proof during this snapshot. Open-PR branches remain needed for review; nine detached worktrees and several local-only branches have unclear or evidence-bearing purpose; dirty worktrees were preserved.

| Path | Branch / state | Worktree status | Disposition |
|---|---|---|---|
| `/Users/macintosh/Documents/ChatGPT/bitcoin-trader` | `main` | dirty (8 entries) | DO_NOT_TOUCH; primary user checkout |
| `/private/tmp/bitcoin-trader-main-origin` | `DETACHED` | clean | DO_NOT_TOUCH; detached/evidence purpose unresolved |
| `/private/tmp/bitcoin-trader-readiness-20260927` | `codex/pre-30h-readiness-20260927` | clean | Retain; branch/worktree context not fully retired |
| `/private/tmp/bitcoin-trader-research-gap-closure` | `research/bitmex-gap-closure-20260927` | clean | Retain; branch/worktree context not fully retired |
| `/private/tmp/bitcoin-trader-restart-idempotency-fix` | `fix/closed-hour-restart-idempotency-20260927` | clean | Retain; branch/worktree context not fully retired |
| `/private/tmp/bitcoin-trader-runtime-22e06b9` | `codex/fix-terminal-witness-binding-20260928` | clean | Retain; branch/worktree context not fully retired |
| `/private/tmp/btc-asof-alignment-20260928` | `research/fix-asof-alignment-20260928` | clean | Retain; branch/worktree context not fully retired |
| `/private/tmp/btc-live-gate-20260928` | `fix/autonomous-daemon-default-off-20260928` | clean | Retain; branch/worktree context not fully retired |
| `/private/tmp/btc-main-baseline-20260928` | `DETACHED` | clean | DO_NOT_TOUCH; detached/evidence purpose unresolved |
| `/private/tmp/btc-overnight-report-20260928` | `docs/overnight-30h-closure-20260928` | clean | Retain; branch/worktree context not fully retired |
| `/private/tmp/btc-post30h-dryrun-20260928` | `codex/post-30h-evidence-integration-20260928` | clean | Retain; branch/worktree context not fully retired |
| `/private/tmp/final-30h-preflight-054d43c` | `DETACHED` | clean | DO_NOT_TOUCH; detached/evidence purpose unresolved |
| `/private/tmp/final-30h-preflight-4f73b8` | `DETACHED` | clean | DO_NOT_TOUCH; detached/evidence purpose unresolved |
| `/private/tmp/final-30h-preflight-7e9b8` | `DETACHED` | clean | DO_NOT_TOUCH; detached/evidence purpose unresolved |
| `/private/tmp/final-30h-preflight-d10bb26` | `DETACHED` | clean | DO_NOT_TOUCH; detached/evidence purpose unresolved |
| `/private/tmp/final-30h-preflight-d5c27` | `DETACHED` | clean | DO_NOT_TOUCH; detached/evidence purpose unresolved |
| `/Users/macintosh/.codex/worktrees/9fcb/bitcoin-trader` | `DETACHED` | clean | DO_NOT_TOUCH; detached/evidence purpose unresolved |
| `/Users/macintosh/.codex/worktrees/final-30h-preflight/bitcoin-trader` | `codex/final-30h-preflight-20260928` | dirty (7 entries) | ACTIVE_REQUIRED; this task |
| `/Users/macintosh/.codex/worktrees/post-30h-integration/bitcoin-trader` | `codex/post-30h-integration-20260926` | clean | Retain; branch/worktree context not fully retired |
| `/Users/macintosh/.codex/worktrees/quant-dashboard-ui-bitcoin-trader` | `feat/quant-dashboard-ui` | clean | Retain; branch/worktree context not fully retired |
| `/Users/macintosh/Documents/ChatGPT/bitcoin-trader-worktrees/aws-6h-v4-launch-20260921` | `gpt/aws-6h-v4-launch-20260921` | clean | Retain; branch/worktree context not fully retired |
| `/Users/macintosh/Documents/ChatGPT/bitcoin-trader-worktrees/aws-6h-v4-runtime-20260921` | `DETACHED` | clean | DO_NOT_TOUCH; detached/evidence purpose unresolved |
| `/Users/macintosh/Documents/ChatGPT/bitcoin-trader-worktrees/docs-correction` | `docs/reliability-history-correction-20260919` | clean | Retain; branch/worktree context not fully retired |
| `/Users/macintosh/Documents/ChatGPT/bitcoin-trader-worktrees/docs-reliability` | `docs/reliability-status-20260919` | dirty (2 entries) | Retain; branch/worktree context not fully retired |
| `/Users/macintosh/Documents/ChatGPT/bitcoin-trader-worktrees/docs-validation` | `docs/validation-evidence-index-20260920` | clean | Retain; branch/worktree context not fully retired |
| `/Users/macintosh/Documents/ChatGPT/bitcoin-trader-worktrees/external-bitmex-prep-20260927` | `gpt/external-bitmex-prep-20260927` | clean | Retain; branch/worktree context not fully retired |

## Cleanup recommendation

- No worktree removal is recommended on current evidence. In particular, preserve the primary main checkout, dirty user documentation worktree, detached worktrees with validation evidence, all open PR worktrees, and current preflight/research branches.
- No remote branch deletion candidate is high-confidence: every open-PR head is retained; refs without a PR mapping need ancestry/evidence review first.
- Do not merge `main` automatically. PR #23 remains draft and based on #22; PR #22 is itself based on the closed-hour idempotency branch.
- The exact final 30H identity is prepared, not authorized. Launch remains `NO` pending an exact-identity human GO.
