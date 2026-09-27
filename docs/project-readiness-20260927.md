# Project Readiness Inventory — 2026-09-27

## Scope and evidence boundary

This is a source and Git inventory, not a 30H result or an alpha claim. The working tree for this audit is `codex/pre-30h-readiness-20260927` at `origin/main` SHA `39e76ce0befab26e9b1f8cb5b83805ef1ee152ad`. The Fresh 30H-v3 runtime revision is `22e06b9527798567e185fb0dd41dca3a448f444e`, tree `a44c9591045f3634cda1066d582927f277b79e2d`. No live runtime, AWS, S3, exchange, or prospective-holdout reads were made for this sprint. Repository history, local branches/worktrees, PR metadata, source, and existing tests were inspected read-only.

The initiating checkout is separately dirty: it has unrelated untracked `.agents/`, `.codex/`, `.commandcode/`, `AGENTS.md`, and protected `test-results/`. None were modified. Its local `main` is at `0b819ea`, six commits behind `origin/main` at `39e76ce`; use an explicitly chosen remote base for later integration rather than assuming local `main` is current.

## Architecture pipeline

| Stage | Implementation | Status | Evidence and remaining boundary |
|---|---|---|---|
| Public exchange ingestion | `bithumb_websocket.py`, `cross_market_collector.py`, `microstructure_collector.py`; public feeds | PARTIAL | Collection, bounded queue, redundancy, and reconnect code exist with fault-injection tests. Natural Fresh 30H-v3 completion and terminal receipts are still required to establish runtime reliability. |
| Raw persistence | `microstructure_storage.py`, archive/finalizer modules, `scripts/orchestrate_closed_hour_archive.py` | PARTIAL | Durable cohort/receipt machinery exists. Actual v3 writer counters, receipt equality, and finalization trace are not available until post-run evidence is exported. |
| Canonical data | `canonical_market_data.py`, `research_infra/adapters.py`, new `research_infra/build.py` | PARTIAL | Readiness branch now builds local canonical JSONL plus raw/output hashes atomically and refuses holdout inputs or overwrite; DQ is explicitly `NOT_RUN`, and the actual current-public source still depends on post-30H evidence. |
| Data quality | `data_quality_flags.py`, `research_infra/dq.py`, `research-data/dq/*` | PARTIAL | Catalog and gating code exist. Catalogs are tied to older run IDs and must be regenerated from a sealed 30H PASS dataset. |
| Feature generation | `research_infra/features.py`, `microstructure_features.py` | PARTIAL | Feature engine/specifications exist; no centralized `FeatureRegistry` or universal feature-definition hash boundary exists. |
| Research datasets | `research_infra/registry.py`, `research-data/dataset_registry.json` | PARTIAL | Dataset roles/provenance/source roots and access checks exist. Registration is not an append-only external registry and does not itself verify source bytes against every fingerprint. |
| Experiment runner | `experiment_runner.py`, `research_infra/manifests.py` | PARTIAL | Preregistration, durable trial reservations, atomic ledger writes, hash chain, and holdout lifecycle exist. `ResearchManifest` uses a random ID/time; no complete deterministic experiment-output directory contract or single `ExperimentRegistry` is enforced across runners. |
| Strategy evaluation | `backtest.py`, `rebalance_backtest.py`, `multi_asset_backtest.py`, `execution_simulator.py`, maker/taker simulators | PARTIAL | Several valid scenario-specific engines exist; semantics/costs differ and no universal authoritative engine exists. Input validation was tightened in this sprint for the multi-asset and composite paths. |
| Candidate registry | Candidate factory modules and family research scripts; readiness branch adds `research_infra/candidate_registry.py` | PARTIAL | Append-only local JSONL lifecycle registry has hash-chained events, ordered evidence gates, an external-data firewall, and synthetic tamper tests. Family candidate factories and existing CLI promotion paths do not yet use it; no universal `StrategyRegistry`. |
| Candidate freeze | `research_infra/freeze.py` (`FrozenCandidate`) | PARTIAL | Frozen definition has a canonical hash and holdout consistency checks. It is not wired as a mandatory transition gate across all candidate-generation paths. |
| Prospective holdout | `research_infra/experiment_runner.py`, `ResearchManifest`, holdout integrity checks | PARTIAL | State gates and frozen manifests exist; no single sealed `ProspectiveHoldoutManifest` plus operator command spans all datasets/runners. Do not inspect future reserved data. |
| Paper execution | `paper.py`, `paper_engine.py`, `paper_journal.py`, `order_transport.py` | PARTIAL | `paper.py` is a persistent deterministic daily-candle ledger; `paper_engine.py` models order states and partial fills; the readiness-branch SQLite journal atomically persists event keys and snapshots. These are not yet one approved prospective pipeline with public-feed reconciliation and readiness gate. |
| Live execution | `execution.py`, `order_transport.py`, `scripts/execute_live_trader.py` | BLOCKED | Private/live transport is disabled by default and fail-closed transport tests exist. Live/private API remains disabled and out of scope. |

The corresponding edge statuses are: public ingestion → raw persistence PARTIAL; raw → canonical PARTIAL; canonical → DQ PARTIAL; DQ → feature generation PARTIAL; features → research datasets PARTIAL; datasets → experiment runner PARTIAL; experiment → strategy evaluation PARTIAL; evaluation → candidate registry PARTIAL (central ledger added, family integrations missing); candidate → freeze PARTIAL; freeze → prospective holdout PARTIAL; holdout → paper PARTIAL; paper → live BLOCKED by scientific and authorization gates.

## Cross-cutting inventory

| COMPONENT | PURPOSE | CURRENT_IMPLEMENTATION | BRANCH_OF_TRUTH | TEST_COVERAGE | KNOWN_DEFECTS | DUPLICATION | STALE_CODE | MISSING_PIECES | POST_30H_DEPENDENCY | PRIORITY |
|---|---|---|---|---|---|---|---|---|---|---|
| Reliability validation | Prove finite collection and archive behavior | Collector, archive scheduler, observer, canary, witness, terminal artifacts | Runtime `22e06b9`; preserved lineage ref `gpt/fresh-6h-v5r1-failure-fix-20260926`; source base `origin/main` | Extensive collector/archive/replay/fault injection suites | v3 actual terminal state unknown; known retry-sensitive `closed_at_utc` issue exists in this runtime | Older V1/V2/V4/V5 artifacts and handoff docs overlap; each remains historical | Status docs on main discuss older 72H/30H attempts | Offline v3 terminal evidence audit; post-run adjudication | WAIT_FOR_30H | P0 |
| Terminal auditor | Read-only PASS/FAIL/NOT_VERIFIABLE decision from offline export | Added `scripts/audit_fresh_30h_v3_terminal.py` in this sprint | Readiness branch; expected runtime commit/tree pinned | Synthetic complete, missing, and fault bundles | v3 input export is not available yet; absent telemetry intentionally yields NOT_VERIFIABLE | Does not replace historical auditors | None | Operator must export the complete bundle after natural completion | SAFE_NOW tool; WAIT_FOR_30H evidence | P0 |
| Closed-hour retry fix | Stabilize finalizer receipt identity on retry/restart | Fix exists at `48cfa0aa21327daa420638d275a3ca5314f6ad49`, parent exactly runtime `22e06b9` | `fix/closed-hour-restart-idempotency-20260927` | Focused repeat-finalization regression in fix worktree | Not in running runtime; do not merge or backport before preserving terminal evidence | No | None | Integrate only after v3 audit and separate replay | WAIT_FOR_30H | P0 |
| Bithumb daily backtest | Daily long-only spot target allocation | `RebalanceBacktester`; next-open execution; configurable fee/slippage through `TradingSettings` | `origin/main` | `tests/test_rebalance_backtest.py`, oracle and candidate suites | No latency, partial fills, tick/lot, intrabar ambiguity, or daily-entry cap in rebalance evaluator | Overlaps signal-based `Backtester` intentionally by API | No known dead implementation established | Explicit execution/cost semantics and shared model | SAFE_NOW audit; implement behind a separate scoped change | P0 |
| Multi-asset V8 backtest | Shared cash, long-only cross-sectional portfolio | `MultiAssetSharedCashBacktester`; sell-first then buys; missing-bar cache and point-in-time market metadata | `origin/main` | No dedicated test module before this sprint; added `tests/test_multi_asset_backtest.py` | Duplicate timestamp keys previously overwrote silently; weights and market-key mapping were unchecked; missing bars are intentionally not fillable | Duplicated depth/execution logic exists in some microstructure simulators | No source marked stale solely by age | Full accounting/oracle matrix, round trips, date/listing bias, constraints | SAFE_NOW input guard done; full P0 correctness audit remains | P0 |
| Composite V6 evaluator | Combine core/satellite target streams | `composite_portfolio_backtest.py` delegates execution to `RebalanceBacktester` | `origin/main` | No dedicated suite before this sprint; added `tests/test_composite_portfolio_backtest.py` | Invalid component streams could cancel into a seemingly valid sum or be capped | Reuses daily engine; not an independent fill engine | No | Component-level and portfolio-level cost diagnostics | SAFE_NOW input guard done | P1 |
| Cost scenarios | Compare fee/slippage sensitivity | `fee_regimes.py` plus readiness branch `research_infra/costs.py` explicit `SpotCostScenario` and conservative grid | Readiness branch model; existing engines retain their settings | Fee-regime suites plus new cost-model and readiness-contract tests | Main paths still use flat `fee_rate` and symmetric slippage; source schedules are not verified by this static model | `research_infra/execution.py`, maker/taker simulators and settings still have separate runtime fields | “live_zero_fee” names are event assumptions, not timeless truth | Migrate reports/backtest paths to selected scenario; model partial fills or mark unsupported; verify exchange increments/fee schedule | SAFE_NOW shared config/validation/grid added; data-derived values and engine wiring remain | P0 |
| Walk-forward | Chronological train/test evaluation | `research_infra/evaluation.py` chronological expanding/rolling fold builder; nested family-specific waves | Readiness branch adds explicit label-horizon purge buffer and timestamp/parameter validation | Existing chronological evaluation tests plus new adversarial boundary tests | Fold builder returns time windows only; no universal train-only feature-fit/predict runner, per-fold metric aggregation, or closure-based lookahead isolation | Multiple project-specific folds | None proven stale | Bind purge horizon to labels, add train-only fit contract and aggregate/per-fold reports | SAFE_NOW boundary hardening added; generic runner remains | P1 |
| Hypothesis registry | Govern falsifiable research questions and provenance | `research_infra/hypotheses.py` has five typed H1–H5 microstructure hypotheses; strategy families keep local catalogs | Core registry on `origin/main`; external BitMEX/AOA hypotheses remain on isolated branches | Hypothesis shape and registry tests | In-memory status changes have no append-only evidence record; the defaults do not collect all V2–V8, opportunity, win-rate, maker or external hypotheses | Candidate/factory catalogs repeat hypotheses | Older experiment/docs contain unindexed ideas | Complete source inventory with origin, data requirements, confounders and falsification rules; external entries tagged `HYPOTHESIS_GENERATION_ONLY` | SAFE_NOW using source only; do not import external data | P1 |
| Dataset registry | Dataset identity and scientific access roles | Frozen registration record, role checks, duplicate ID checks; JSON snapshot | `origin/main` | `tests/research_infra/test_research_infra.py` | Not a global immutable registry; fingerprints are metadata unless checked by a pipeline | `experiment_runner.DatasetRole` is another role model | Registry includes historical run labels and needs post-30H registration | Artifact-byte verification and a bound current-public dataset manifest | WAIT_FOR_30H for source | P0 |
| Experiment registry/reproducibility | Reproduce experiment from all inputs | `experiment_runner.py` ledger/reservations; `ResearchManifest` fingerprint; optional reproducibility module is in PR #17 branch | Main has partial implementation; readiness branch adds a content-derived logical experiment ID | Main ledger and manifest tests plus new identity tests; PR #17 tests only on its branch | The full manifest fingerprint remains run-specific; code revision may be `unknown`; incomplete inputs are flagged; result dir/hash contract is not universal | Research manifests and trial ledger track overlapping IDs | No | Results/metrics/logs, deterministic rerun checker, and batch runner | SAFE_NOW identity support added; data-dependent run remains later | P0 |
| Candidate governance | Control research lifecycle and evidence-based promotion | Freeze object, family validators, CLI promotion values, per-wave policies; readiness branch adds append-only `CandidateRegistry` lifecycle and event-chain verification in paper readiness | Readiness branch | Candidate, wave, freeze, validator, registry-order/tamper, external-firewall and readiness binding tests | Promotion functions differ by family; one local CLI can report `PAPER_CANDIDATE` from retrospective gates; new registry is not mandatory across candidate-generation paths | Candidate/factory modules proliferate across V2–V8 and win-rate/opportunity lanes | Wire all promotion paths to the central lifecycle; add a universal candidate-freeze command | SAFE_NOW registry/checker added; broad integration remains | P0 |
| Prospective holdout | Preserve an untouched final evaluation set | Holdout lifecycle in experiment runner; `FrozenCandidate` comparison | `origin/main` | Holdout-gate and freeze tests | Multiple overlapping “quasi-OOS”, sealed and holdout conventions; one manifest type does not enforce all | Wave-specific seals and generic holdout state | Old holdout language predates current 30H run | One freeze manifest with timestamp boundary/hash/evaluation command; fixtures only until candidate exists | REQUIRES_NEW_DATA only at evaluation | P0 |
| Paper engines | Simulate orders without private API | Persistent daily `paper.py`; order lifecycle/depth application `paper_engine.py`; new local SQLite `paper_journal.py`; fail-closed `paper-readiness` bundle checker; disabled transport | Readiness branch includes this sprint's event validation, atomicity, journal, and eligibility report | `test_paper.py`, `test_paper_engine.py`, journal restart/idempotency/integrity tests, readiness synthetic bundle tests, end-to-end paper pipeline, transport safety | Replayed fills, malformed/overfilled results, mismatched side/market and failed multi-level fills are rejected or rolled back; SQLite persists order/portfolio snapshots and an append-only event key ledger. Still no single pipeline combining persistent partial orders, public stream, pending-order reconciliation, risk and observability. | Two paper abstractions overlap; the journal is storage for `paper_engine`, not a third trading engine | No | Integrate the journal with the selected paper runtime; produce post-30H evidence bundle; reconcile stale/pending orders; wire readiness to durable risk/observability | SAFE_NOW event safety, local recovery and aggregate readiness checker added; integration remains | P0 |
| Risk policy | Apply exposure, stale data and failure controls | `risk.py`, `risk_engine.py`, `TradingSettings` | `origin/main` | `test_risk.py`, `test_risk_engine.py`, safety tests | Policies are split; risk engine audit records are in-memory; configured set does not cover every requested limit | Two risk modules and settings-level gates | `fix_risk_engine.py` is a root-level helper, not part of package path | Unified validated limits and durable audit integration, with private/live fail-closed | SAFE_NOW | P0 |
| Live/private safety | Ensure offline checkout cannot trade | `DisabledLiveTransport`, config env gate and explicit live execution script | `origin/main` | Disabled transport/autonomous trader safety tests | Settings allow `LIVE` only if env flag true; user-level explicit approval mechanism remains separate | Other scripts may have distinct live guard patterns | None | Whole-repo default-path proof, secret log scan in changed code, no real-order tests | NOT_NEEDED for paper; REQUIRES_PRIVATE_API for live | P0 safety |
| Dashboard | Inspect evidence and reliability state | React/Vite app, evidence console and API | Current dashboard branches plus `origin/main`; no active dashboard PR | Dashboard Vitest, lint/typecheck/build scripts | Current evidence source is validation-focused, not a unified paper portfolio/orders/fills/risk view | Dashboard API and CLI reports partly overlap | `dashboard/README.md`, handoffs and current data include prior schema/research states | Add paper views only after stable paper contracts; do not fork a second monitor | SAFE_NOW design; runtime actuals WAIT_FOR_30H | P1 |
| Research CLI | Build data, run hypotheses, paper-readiness and reports | `research_infra/cli.py`; readiness branch adds atomic canonical JSONL build and fail-closed paper-evidence checker | Readiness branch | Synthetic build/readiness bundle tests; existing infrastructure/CLI suites | `hypotheses run` remains a placeholder; canonical build is not DQ qualification and is not resumable | CLI overlaps standalone scripts | CLI description remains ahead of full orchestration | DQ-bound current dataset, feature persistence, resumable batch and report pipeline | SAFE_NOW local build/readiness commands implemented; actual evidence waits for 30H/candidate | P0 |
| CI and static checks | Prevent regressions and enforce reproducible validation | No `.github/workflows` is present on `origin/main`; Python test config is in `pyproject.toml`; dashboard scripts define test/lint/typecheck/build | `origin/main` | Local Python suite; dashboard scripts available | No repository CI gate was found in the audited base; dashboard dependencies are not installed in this worktree | Per-branch scripts are not a common pipeline | Older reports describe checks not enforced on current base | Add least-privilege CI for Python and dashboard after selecting supported runners and artifact policy | SAFE_NOW | P1 |
| External BitMEX lane | Hypothesis generation from external attributed data | Draft PR #17 branch adds source manifests, firewall and external analysis | `gpt/external-bitmex-prep-20260927` only; not in `origin/main` | Branch-local tests/docs; no claims adopted in this inventory | Ownership/provenance differs from publisher attribution; not a Bithumb execution model | Older PR #15 and gap-closure branch overlap portions | External gap closure is a distinct follow-up | Never promote candidates or select holdout from this data | NOT_NEEDED for core pipeline; HYPOTHESIS_GENERATION_ONLY | P2 |

## Branch and PR topology

Verified local/remote refs at audit time:

| Ref | Observed head | Relationship and disposition |
|---|---|---|
| `origin/main` | `39e76ce` | Clean base used for this isolated readiness branch. |
| local `main` | `0b819ea` | Two commits ahead of remote base and diverged; do not use as an implicit integration base. |
| `gpt/fresh-6h-v5r1-failure-fix-20260926` | `22e06b9` remotely; local root extends to `de1fc32` | Runtime source revision is `22e06b9`; local `de1fc32` contains later Fresh 6H-v6r1 evidence. Do not copy its evidence into the live run or overwrite either record. |
| `fix/closed-hour-restart-idempotency-20260927` | `48cfa0a` | Parent is exactly runtime `22e06b9`; retain isolated until terminal evidence is frozen. |
| `gpt/post-30h-integration-20260921` | `ee6d57c` | Draft PR #12 to main records Fresh 30H-v2 FAIL and a scheduler fix. Preserve the FAIL; review changes individually and do not treat it as v3 evidence. |
| `gpt/bithumb-gap-forensics-20260921` | `e8e6811` | Draft PR #13 stacks on #12; gap forensics/reliability. Keep stacked until its base is resolved after v3. |
| `gpt/bithumb-redundancy-hardening-20260921` | `680a5f4` | Draft PR #14 stacks on #13; active-active redundancy. |
| `gpt/fresh-6h-v4r1-failure-fix-20260922` | `94b9293` remote | Draft PR #16 stacks on #14. It is a separate 6H remediation lineage; do not substitute it for the v3 runtime lineage. |
| `gpt/external-bitmex-expert-dataset-prep-20260923` | `9f3aaca` | Draft PR #15 stacks on the older #16 chain; decompose before any merge. |
| `gpt/external-bitmex-prep-20260927` | `ebe8159` | Draft PR #17 targets main. It includes extensive unrelated/historical reliability files plus external research work; do not merge wholesale into the v3 reliability lineage. Split reviewable research/firewall work after 30H. |
| `research/bitmex-gap-closure-20260927` | `b593c18` | Separate five-file external-data gap/bootstrap lane. Review independently after PR #17 decomposition; retain the HYPOTHESIS_GENERATION_ONLY boundary. |
| readiness branch | `39e76ce` plus this sprint | Isolated from the runtime commit; has no runtime/AWS/holdout lineage. |

Open PR metadata was read-only: #12 non-draft; #13, #14, #15, #16, #17 draft. No PR was created or merged for this sprint. After a terminal audit PASS: (1) freeze original run evidence and select an explicit `origin/main` SHA; (2) create a clean reliability integration branch and submit the exact v3 runtime-source delta as its own PR; (3) cherry-pick `48cfa0a` as a separate commit/stacked PR only after its parent source is integrated and terminal evidence is sealed; (4) run pre-merge and post-merge reliability suites; (5) compare #12's scheduler patch to current source and port only still-needed source changes—keep its v2 FAIL artifact immutable; (6) rebase #13 then #14 one at a time onto the integrated reliability branch only if their source deltas are still absent, otherwise mark those patches superseded; (7) keep #16's older 6H remediation separate and do not merge it into v3; (8) integrate dashboard remediation as a later independent PR; (9) decompose #17 into external provenance/firewall-only research changes, never merge its inherited reliability files wholesale; (10) compare #15 against #17, then close/archive #15 as superseded if no unique change remains; (11) review `research/bitmex-gap-closure` independently as hypothesis-generation-only work. Never mix external research with reliability runtime, candidate selection, or prospective holdout evidence.

## Dependency graphs and prioritization

### Automated PAPER path

```text
natural Fresh 30H-v3 end
  → offline terminal audit PASS
  → immutable v3 reliability evidence + explicit integration decision
  → integrate reliability source and isolated closed-hour fix; pass pre/post merge suites
  → canonical current-public dataset build + source hash + DQ PASS
  → validated features and point-in-time research dataset
  → preregistered reproducible retrospective batch with conservative cost scenarios
  → generic purged/embargoed walk-forward + placebo/baseline and robustness reports
  → machine-verifiable candidate evidence and freeze manifest
  → paper readiness report: every required gate PASS
  → separate human authorization to begin prospective PAPER
  → durable order/fill/position journal, restart replay, risk checks and monitoring
```

The evaluator is not a candidate approval. The existing code has pieces for most nodes, but the central handoffs, cost semantics, reproducibility identity, candidate state machine, and paper readiness/start path are not complete.

### LIVE/private path

```text
PAPER candidate + frozen definition
  → prospective PAPER started only after separate authorization
  → minimum duration/trade/accounting/risk evidence defined by governance
  → live eligibility evidence + security review + explicit private API authorization
  → secret configuration and credential-scoped validation
  → separate live approval and controlled activation
```

This path is BLOCKED by design. `LIVE=DISABLED` and `PRIVATE_API=DISABLED` remain the truth. A repository feature or test never authorizes private endpoints, paper starts, or live funds.

## SAFE_NOW engineering update — 2026-09-27

This addendum records isolated readiness-branch work after the inventory above;
the opening tables remain the earlier audit snapshot. The working branch does
not alter the Fresh 30H runtime lineage.

| Area | Current readiness-branch result | Remaining SAFE_NOW work |
|---|---|---|
| Backtest authority | New single-market research is routed through `SpotResearchBacktester` → `RebalanceBacktester`, with explicit scenario costs and standardized results. The engine comparison and decision are in `docs/BACKTEST_ENGINE_COMPARISON_2026-09-27.md`. Signal-based, multi-asset, maker, and order-book engines are preserved as legacy/specialized paths. | Expand governed adapters beyond the four daily strategies; audit specialized fill semantics before accepting their candidate results. Maker fills and book-depth execution do not belong to the candle authority. |
| Cost sensitivity | `research-batch` runs explicit base/conservative/stress/extreme scenarios and records fees/slippage. Positive latency and nonzero partial-fill probabilities are marked unsupported by the candle engine and cannot pass freeze. A randomized close/rebalance regression now preserves lot-aligned quantity accounting. | Source current exchange schedules and model stochastic partial fills only when verified inputs and an appropriate engine exist. |
| Walk-forward, batch, result, controls | Generic rolling/expanding train-only walk-forward with purge/embargo, content-derived resumable batches, standardized result records, per-attempt evidence, cash/buy-and-hold/placebo comparisons, and a persistent source hypothesis/family inventory are present. | Broaden strategy adapters and decide/freeze candidate acceptance rules from real development evidence. No synthetic fixture qualifies a strategy. |
| Candidate freeze | `candidate-freeze` verifies complete batch/lifecycle/cost/fold/control evidence and writes a hash-bound immutable artifact only for the four supported daily strategy adapters. It requires an existing `CANDIDATE` lifecycle decision; it does not select candidates or start PAPER. | Connect every strategy selection path to the registry and review explicit comparative acceptance thresholds after real retrospective results exist. No candidate has been frozen from project market data. |
| PAPER runtime and start gate | Existing `paper.py`, `paper_engine.py`, `paper_journal.py`, risk code, and readiness checker remain separate components. No local public-feed → frozen strategy → risk → paper executor → journal → reconciled accounting runtime or governed `paper-start` command is wired by this addendum. | Integrate/test the public-only event loop, order state machine, fixed-point accounting, deterministic conservative fill model, durable kill switch, observability, replay/crash recovery, and a fail-closed but uninvoked start command. |
| Post-30H orchestration | No command automates the terminal audit → source integration → data qualification → batch → candidate report/freeze sequence. | Build a stop-on-failure local orchestrator with no automatic PAPER invocation; do not target the running 30H runtime. |
| Hypothesis/feature/strategy registries | Persistent hypothesis catalog and candidate-family inventory are present; hypotheses remain unpromoted. | FeatureDefinition and StrategyDefinition registries with immutable content hashes remain SAFE_NOW work. |

Focused source verification for this update: `tests/research_infra/` plus daily,
composite, and multi-asset backtest tests. These establish software behavior on
synthetic fixtures only; they do not establish 30H reliability, dataset
qualification, alpha, paper readiness, or authorization.

### P0 blockers

| Blocker | Why it blocks | Can work now? | Current completion |
|---|---|---|---|
| 30H terminal evidence and reliability decision | No current PASS/FAIL from the running v3 run has been inspected; source tests do not prove runtime reliability | WAIT_FOR_30H | Auditor implemented; evidence remains pending |
| Reliability integration and closed-hour restart fix | The runtime does not contain `48cfa0a`; integration before terminal seal could change the evidence lineage | WAIT_FOR_30H | Isolated fix verified from Git topology; no merge |
| Real current-public canonical dataset and DQ | Qualified v3-derived public source and separate DQ PASS are still required | WAIT_FOR_30H / REQUIRES_NEW_DATA | Local offline source-bound canonical build command added; no qualified v3 source or DQ result is available |
| Authoritative execution semantics and costs | Flat fee/slippage is not enough for latency, partial fill, exchange increments, and maker/taker; several simulators coexist | SAFE_NOW audit; REQUIRES verified exchange parameters for production values | Backtest bad-input gaps fixed; broad audit remains |
| Reproducible batch and walk-forward handoff | Manifest, ledger and folds exist but don't yield one deterministic batch/result contract or generic fit/evaluate runner | SAFE_NOW | Content-derived experiment ID, strict provenance/split completeness flag, and purge/embargo fold boundaries added; resumable batch/result contract and train-only evaluator remain |
| Candidate registry, promotion transitions, and sealed freeze | Family-specific promotions do not enforce one auditable lifecycle | SAFE_NOW | Append-only evidence-gated registry and readiness-bound chain verification added; family promotions and freeze command remain unintegrated |
| One paper engine/readiness gate | Daily persistent simulator and in-memory order engine do not provide one integrated public-only prospective system | SAFE_NOW | Local atomic SQLite snapshots/event dedup and a hashed-evidence `paper-readiness` command added; public feed, stale-order reconciliation, integrated risk/observability and production evidence bundle remain |
| Risk controls and observability | Risk policies overlap and aren't fully bound to paper orders/metrics | SAFE_NOW | Not complete |

### P1 and P2

P1: complete the source-by-source hypothesis catalog; generic train-only walk-forward runner and aggregate reporting; dashboard paper views after execution contracts exist; cross-simulator equivalence benchmarks; performance baselines for dataset build/features/backtest/batches; neutral, pre-registered metrics schema and report aggregation; implement/fence the remaining research CLI hypothesis-run placeholder. P2: external BitMEX hypothesis ingestion (only after provenance review and strictly hypothesis-only), convenience UI/visual polish, and nonessential research acceleration. September-end is an engineering target, not permission to lower a gate or claim alpha.

## Current scientific and operational state

```text
ALPHA = UNPROVEN
PAPER = NOT_STARTED
LIVE = DISABLED
PRIVATE_API = DISABLED
Fresh 30H-v3 = IN_PROGRESS / terminal result NOT_VERIFIED in this audit
```

Historical PASS/FAIL evidence is retained. Existing 30H-v2 evidence includes a FAIL and is not v3 evidence. Prospective future holdout data was not accessed. The remaining true external blockers are natural 30H-v3 completion/evidence, availability of a qualified current-public dataset after a terminal PASS, and future candidate evidence. Private API/real-money decisions are outside this task and remain disabled.

## Sprint validation

- Focused synthetic terminal-auditor and backtest guard tests: **25 passed**.
- Focused paper order/state and persistent-ledger suites: **33 passed**.
- Research manifest reproducibility tests: **110 passed**.
- Paper journal and paper-engine restart/idempotency tests: **18 passed**.
- Canonical-build synthetic tests: **4 passed**.
- Focused combined research, walk-forward, paper, canonical-build, backtest and terminal-auditor suites: **184 passed**.
- On the final cost-model tree, the full Python suite excluding the one reproduced unchanged supervisor test is **1,469 passed, 2 skipped, 1 deselected**. The earlier run including it was **1,463 passed, 2 skipped, 1 failed** before the six cost-related tests were added. The failure is `test_bounded_supervisor.py::BoundedSupervisorTests::test_sigint_and_sigterm_reach_collector_and_are_durably_recorded`; isolated rerun reproduces `PermissionError` from `os.killpg` followed by a missing result file. Neither that test nor `bounded_supervisor.py` differs from `origin/main`. No reliability fix was mixed into this research/readiness branch.
- Candidate registry, PAPER readiness and cost-model focused suites: **15 passed**. Changed-module Pyright: **0 errors, 0 warnings**.
- Earlier full suite before the final chain verifier also passed **1,464 tests, 2 skipped**; current result is reported above without hiding the reproduced failure.
- Pyright on changed production Python modules and new focused tests: **0 errors, 0 warnings**. Repository-wide Pyright has existing diagnostics and is not a green baseline.
- Repository-wide Pyright: **598 errors** on this worktree; the same command against the exact `origin/main` base produced **599 errors**. This is not a repository-wide green static-analysis baseline.
- Dashboard test/lint/typecheck/build: **not run** because `dashboard/node_modules` is absent in this isolated worktree; no dashboard source changed.
- `git diff --check` and `py_compile` for changed Python modules passed.
- Protected root `test-results/` pre/post SHA-256 inventory was identical.
- No runtime, AWS/S3, exchange, holdout, or trading operation was made.

## SAFE_NOW engineering update — 2026-09-28

This branch-only addendum updates the 2026-09-27 readiness snapshot. No claim is
made about Fresh 30H-v3 terminal state; no runtime or AWS evidence was read in
this local engineering pass.

| Area | Verified branch capability | Remaining SAFE_NOW work |
|---|---|---|
| Backtest, costs, walk-forward, batch, results | `SpotResearchBacktester` → `RebalanceBacktester` is the new single-market authority. Scenario fees, slippage, venue increments, sensitivity grid, train-only rolling/expanding folds, purge/embargo, resumable content IDs, `ExperimentResult` v1, and cash/buy-hold/placebo controls are in the governed CLI. Candle latency and partial-fill semantics fail closed when unsupported. V3 E9 updates its long-lookback state one completed candle at a time and is regression-checked against the existing batch generator. | Adapt additional inventoried candidate sources only after their causal, execution, and accounting contracts are reviewed; specialized maker/book/multi-asset engines remain separate. |
| Candidate families and freeze | Persistent hypothesis/family catalog maps historical source classes and preserves untrusted/failed results. Candidate selection stays explicit. `candidate-freeze` binds code/config/data/cost/result evidence and accepts 23 immutable single-market IDs across the daily, V3, V4/V4b, V5, V6, and 70/30 V4-core/V6-satellite families. Multi-asset candidates remain unsupported. | Adapt only remaining paths whose data/accounting contract fits the candle authority. All candidates still require the same governed retest; none is promoted by this adapter work. |
| PAPER engine | Local runtime joins a frozen candidate, public normalized events, unified risk, conservative visible-book taker fills, Decimal accounting, SQLite journal, durable halt latch, replay/recovery, and crash-point tests. `paper-start` requires all readiness gates and was not invoked. | Add retained provider/source receipts and a persistent process supervisor; the foreground feed stops on disconnect and requires explicit recovery. |
| Public feed and observability | A public-only Bithumb v1 trade/book adapter builds completed KST candles, rejects partial/gapped/invalid data, and durably halts on disconnect. The local dashboard API reads SQLite in read-only mode; the Trading view polls only after explicit opt-in and marks snapshot integrity `NOT_VERIFIED`. | Keep live feed operation separate from this preparation; no stream was opened and no session receipt/completeness seal exists. |
| Post-30H pipeline and CI | `scripts/post30h_orchestrator.py` is offline and fail-stop through audit/seal, reviewed source and DQ receipts, batch/report/freeze, and readiness; it never starts PAPER. A least-privilege workflow covers the synthetic Python/PAPER and dashboard suites. | Expand reviewed strategy adapters and retain provider-bound process receipts; changes to workflow scope will rerun the focused suite. |

Focused branch verification: **157 Python tests passed**; changed-scope Pyright:
**0 errors, 0 warnings**; dashboard: **86 tests passed**, lint/typecheck/build
passed; workflow YAML parsed. GitHub Actions push and pull-request runs both
passed at `8273dfb600c905db3468998ee19e53a111761d0c` (runs `36329074694` and
`36329076460`). The synthetic benchmark at that code revision (1,000 candles,
5 folds, 10 experiments) completed with 0.0051s single backtest, 0.0181s
walk-forward, 0.2811s batch (35.58 experiments/s), and 0.0284s paper recovery.
This is synthetic software evidence, not reliability, dataset qualification,
strategy performance, candidate selection, or PAPER authorization. Full
repository tests were not run. `git diff --check` passed. No Fresh 30H runtime,
AWS/S3, private API, prospective holdout, or trading operation was made.

Remaining SAFE_NOW items are adapters for specialized multi-asset, intraday,
maker/order-book, and legacy paths where the single-market candle authority is
not semantically adequate, plus provider-bound durable feed/session receipts
and explicit process supervision.
True post-30H evidence blockers remain terminal evidence/reliability seal, qualified
current-public data, governed retrospective results and human candidate
selection. PAPER would still require a separate authorized start and future
prospective observation; none of those stages is automatic.
