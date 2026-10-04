# State Separation: reliability, dataset, research, alpha, paper, live

These states are independent. Evidence for one never implies another.

| State | Question it answers | Evidence that can move it | Does NOT imply |
|---|---|---|---|
| Infrastructure reliability (`RELIABILITY_CLOSED`) | Did the unchanged Frozen V2 auditor accept a real 30H run? | Real 30H systemd unit, exact InvocationID journal, receipts, terminal witness, S3 version provenance | data is research-grade, any alpha |
| Dataset qualification (`DATASET_QUALIFIED`) | Is a canonical dataset built twice identically and complete over the expected feed/hour universe? | Formal DQ after reliability closes; `dataset_scan` reports are structural diagnostics only | reliability, alpha |
| Retrospective research (`RETROSPECTIVE`) | May hypotheses be explored on qualified data? | Starts only after `DATASET_QUALIFIED = YES` | profitability |
| Alpha (`ALPHA`) | Does a frozen candidate survive an untouched prospective holdout net of costs? | Frozen candidate + single-use holdout | paper/live readiness |
| Paper (`PAPER`) | Simulated forward execution | Explicit human authorization | live readiness |
| Live (`LIVE`) / Private API | Real orders | Explicit human authorization | n/a |

Current: ALPHA=UNPROVEN, PAPER=NOT_STARTED, LIVE=DISABLED, PRIVATE_API=DISABLED.

## Short / 6H runs
A 6H unit is a runtime/evidence **diagnostic qualification**. Its systemd evidence is labelled
`SYSTEMD_ACCEPTANCE_EVIDENCE = FALSE`; the unchanged Frozen V2 auditor accepts only the real 30H unit.

## Tooling (offline, no verdicts)
- `research_infra/dataset_scan.py`: deterministic file-level scan + build-twice comparator. Always
  emits `dataset_qualified=false`, `qualification_authority="NONE"`.
- `research_infra/baseline_harness.py`: embargoed splits, single-use fingerprint-bound holdout vault,
  perturbation leakage guard, backtester with explicit fee/slippage, metrics and cost sensitivity.
- External ideas (Wonyotti/AOA/BitMEX) are hypothesis generators only; they never promote a candidate or touch a holdout.
