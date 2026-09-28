============================================================
OVERNIGHT PRE-30H + EXPLORATORY RESEARCH REPORT
============================================================

CAPACITY
--------
DISK_FREE_BEFORE = 93,587,300,352 bytes immediately before verified raw-source removal
DISK_FREE_AFTER = 164,606,906,368 bytes immediately after removal
DISK_FREE_CURRENT = 164,599,357,440 bytes at 2026-09-28T13:29:22Z
NET_RECLAIM = 71,019,606,016 bytes (about 66.16 GiB)
ARCHIVES_CREATED = 1; `/var/lib/bitcoin-trader/72h-soak/aws-72h-soak-20260905-8017b83e/raw.tar.zst`; 2,227,736,742 bytes; SHA-256 `6e754657952aaf613838062cd27a20ed0335e81a328f38df84b5fb15126e090c`
ORIGINAL_RAW_REMOVED = only the verified 5,530-file raw payload; historical controls/evidence and `raw/ARCHIVED.txt` pointer preserved
ARCHIVE_SOURCE = epoch `aws-72h-soak-20260905-8017b83e`, run `aws-72h-soak-run-20260905T024039Z-8017b83e`; historical result remains INTERRUPTED, no final manifest flush
RESOURCE_HEADROOM = PASS; conservative 30H growth 41,977,538,393 bytes; projected current free 122,621,819,047 bytes (114.20 GiB); 63,566,018,727 bytes (59.20 GiB) over the unchanged 55 GiB target
EBS = unchanged 300 GiB gp3

30H READINESS
-------------
FINAL_RUNTIME = `b4d482363e2f988dad9c6d29053f97e1e4160883`
FINAL_TREE = `5c96ed79fee107c1604ee7018621835910221fdf`
FINAL_RUN_ID = `aws-validation-observability-30h-run-20260928T125700Z-675937ab`
FINAL_EPOCH = `aws-validation-observability-30h-20260928-20260928T125700Z-675937ab`
CREATED_AT = 2026-09-28T13:01:57Z
WITNESS_E2E = PASS for separate 120-second smoke only; not a 30H run
S3_PATH = smoke receipt SHA parity PASS; new final epoch prefix query returned KeyCount=0 using the existing provisioner. Guest runtime role lacks ListBucket; no IAM change.
IDEMPOTENCY = PASS for modeled `closed_at_utc` retry/restart cases in exact-runtime evidence
DEFAULT_LIVE_SAFE = PASS; LIVE/PAPER/private API remain disabled
GUEST_PROCESS_READY = PASS for fresh identity collision check; no matching process; historical observer PID 1652530 preserved
GUEST_UNIT_READY = PASS for fresh identity collision check; new unit not-found/inactive/dead
ARTIFACT_DETERMINISM = PASS; independent generation preserved identity/runtime/command semantics and deterministic payload hashes
READINESS_WINDOW = PASS; monitored 2026-09-28T13:10:27Z through 13:29:22Z without collector start or material disk/memory/observer/AWS/SSM drift
READINESS_BUNDLE = NOT_VERIFIABLE; one required terminal-auditor dry-run check lacks a separately captured execution record; the 120-second live smoke and malformed-witness tests are not substituted for it
PRELAUNCH_GATE = NOT_RUN at initial report commit; run result is recorded in the follow-up entry below
NEXT_30H_LAUNCH_READY = NO
NEXT_30H_LAUNCHED = NO

REPOSITORY HYGIENE
------------------
BRANCHES_INSPECTED = 67 local; 20 remote topic branches plus origin/HEAD
WORKTREES_INSPECTED = 26 total; 9 detached
WORKTREES_REMOVED = 0; evidence-bearing, open-PR, dirty, detached, or context-uncertain worktrees retained
REMOTE_BRANCHES_DELETED = 0; no high-confidence deletion candidate
PRS_UPDATED = 1 branch head updated: existing draft PR #23; PR body unchanged
PRS_CLOSED = 0
CLEANUP_CANDIDATES_REMAINING = local stale branches/worktrees remain for manual evidence/context review; no safe automatic cleanup identified
BRANCH_MAP = `docs/repository-branch-map-20260928.md`

EXPLORATORY RESEARCH
--------------------
DATASETS_INSPECTED = 96 local CSV filenames/sizes only; 4 registry entries as metadata; 1 KRW-BTC daily development prefix profiled
DATA_QUALIFICATION = NO
RESEARCH_ROLE = EXPLORATORY_ONLY
STRATEGIES_EXERCISED = Core70 V6 EMA Pullback + Sat30; Core70 V6 Fast Donchian + Sat30
EXPERIMENTS_RUN = 130 backtest evaluations: 10 strategy/cost; 15 baseline/cost; 100 fixed-seed placebos; 5 EMA chronological fold diagnostics
BASELINES_RUN = cash, buy-and-hold, daily SMA50/200 (each under 5 cost cases)
COST_SCENARIOS = 5 configured fee/slippage cases; no invented queue/latency precision
ENGINE_BUGS_FOUND = 2 provenance/registry defects (source confidence overwrite and blank explicit digest fallback); no PnL-accounting bug confirmed
ENGINE_BUGS_FIXED = 2 with regression coverage; atomic no-clobber experiment manifests and dataset build-manifest binding added
INTERESTING_HYPOTHESES = none promotion-ready; historical EMA composite claim reproduced on one unqualified development slice, while buy-and-hold had higher return and drawdown; fold results were mixed
REJECTED_HYPOTHESES = no stable positive fold outcome; no strategy winner selected
UNSUPPORTED_FAMILIES = order-book/maker queue, trade-stream microstructure, multi-asset execution, measured latency, queue position
PROSPECTIVE_HOLDOUT_CONSUMED = NO; only the first 2,220 CSV rows were read; sealed tail rows read = 0
RESEARCH_REPORT = `docs/exploratory-unqualified-research-20260928.md`

SCIENTIFIC STATE
----------------
ALPHA = UNPROVEN
CANDIDATE_FROZEN = NO
PAPER = NOT_STARTED
LIVE = DISABLED
PRIVATE_API = DISABLED

GIT
---
COMMITS_CREATED = research `a5c78c2d` plus overnight report/map/artifact commits on `codex/final-30h-preflight-20260928`
COMMITS_PUSHED = research branch `a5c78c2d`; final-preflight documentation/artifacts pushed to existing PR #23
PRS_CREATED_OR_UPDATED = #23 branch head updated; none created; PR body not edited
BRANCHES = `research/fix-asof-alignment-20260928`, `codex/final-30h-preflight-20260928`
PROTECTED_TEST_RESULTS = SHA-256 unchanged: `e22df5d0991eb28c09093b1e678b3fa8cd1fab48185d38e67cf79fb6e63ad5ea`

TRUE_BLOCKERS
1. Required terminal-auditor dry-run evidence is NOT VERIFIABLE; the fail-closed 36-check prelaunch gate therefore must not be treated as PASS.
2. Historical Fresh 30H terminal-contract result remains immutable FAIL; only a separate exact-identity human GO can authorize a new run.
3. Independent methodology reviewer timed out after 240 seconds; independent review is NOT VERIFIABLE.

NEXT HUMAN DECISIONS
1. Review the exact final run ID/epoch and decide separately whether to issue a 30H GO; no launch occurred.
2. Resolve/capture the required terminal-auditor dry-run check before claiming full readiness.
3. Review research PR and methodology when convenient; no alpha/candidate conclusion is made.
============================================================
