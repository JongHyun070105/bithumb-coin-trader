# Future Bithumb reliability validation plan (not authorized)

**State on 2026-09-21:** Fresh 30H-v2 remains a technical `FAIL`. The global new AWS launch budget is exhausted (10/10 used); this document grants no launch authority and assigns no future launch number. No AWS smoke or Bithumb connection is part of this task. Research readiness remains `FAIL`; alpha unproven; paper not started; live and private API disabled.

## Authorization and sealed identity prerequisites

1. Obtain new, explicit authority for any cloud execution and a separately approved launch budget. Define the precise environment, duration, start window, cost ceiling, and stop mechanism. Existing consumed identities and historical `FAIL` evidence remain immutable. A new authorization cannot be inferred from code review, PR approval, or this plan.
2. Review/merge the stacked code only after PR #12 and this branch pass review; create a fresh identity sealed to the then-authoritative commit, tree, configuration, test results and dependency lock. Bind guest copies to the same hashes. A code edit or main-head change requires resealing before any future launch.
3. Require the literal preflight to check collector subscription semantics, all 76 expected feeds, raw-root template placeholder, witness/data-root flags, finalized receipt locations, service user, IAM scope, clock, disk and network policy. Keep private API and trading disabled.
4. Before any run, use local deterministic tests to verify a hung close, simultaneous detectors, server close metadata, failed dial/backoff, queue pressure, event-loop pause, reconnect at UTC boundary, partial Bithumb confirmations, and scheduler restart after a finalized FAIL. Review the source fixture and the final failure gates; do not relax them to obtain PASS.

## Proposed duration and monitoring

If separately authorized, use a bounded **108,000-second (30-hour)** collector window with explicit hard stop and terminal witness. Assess each qualifying *full* UTC hour only; separately label partial start/end hours. The proposed collector must stay public-market-data-only and retain raw exchange, local receive, monotonic receive and local write timestamps. Watch at least minute-level resource and health snapshots during the run, with incident-triggered bounded diagnostics.

Persist a stable per-connection diagnostic on every Bithumb reconnect: connection UUID, source and first-wins reason, UTC and monotonic request/start/connected/subscribed/first-frame times, close start/end and code/reason, exception type, peer, last frame/ping/pong ages, queue depth, writer errors and other-exchange activity. Record process event-loop lag and collector CPU/RSS/FD/disk separately from observer CPU/RSS/FD. Maintain exact source hashes and receipt identities. Alert on a Bithumb dense-feed age approaching the 30-second continuity limit, incomplete feed confirmation, repeated dial failures, 429 responses, excessive connection attempt rate, writer queue pressure, and missing observer samples. Logs must not contain credentials.

## Gates for a future qualifying result

| Gate | Proposed acceptance check |
| --- | --- |
| Runtime | Natural 108,000-second end; no forced timeout or unplanned restarts; exit status and terminal witness independently verified. |
| Coverage | Every expected qualifying full-hour cohort and all 76 feed slots have sealed, matching-identity evidence and final verdicts. No missing finalized receipt; no unreviewed gap or `HEARTBEAT_GAP_EXCEEDED`. A legitimately missing stream remains `FAIL`. |
| Bithumb recovery | If a disconnect occurs, exactly one reconnect decision/teardown per physical connection; bounded close; stage timings; all expected Bithumb feed types and markets re-confirmed by actual frames. A successful reconnect does not erase the preceding data gap. |
| Other exchanges | Binance and Upbit keep independent session and writer evidence; compare per-second dense-feed receive/write rates during any Bithumb incident. |
| Scheduler | A matching finalized `FAIL` remains visible/immutable and does not block later cohorts. Corrupt, wrong-cohort, or explicitly foreign epoch/run receipts do not silently satisfy completion; restart, partial-hour and full-scan behavior remain deterministic. |
| Writer and resources | Zero queue drops, writer errors and unpersisted events; disk headroom above the approved hard stop; sustained CPU/RSS/FD and event-loop lag within reviewed bounds. Set numeric resource limits only from measured capacity and approved host budget. |
| Receipt integrity | Hashes, S3 object identity/metadata, restore verification and historical receipt immutability independently checked. No replacement of a finalized `FAIL` with `VERIFIED_ZERO` or a weaker threshold. |
| Safety | No order, private API, live trading, IAM/Terraform/S3 policy mutation, or extra cloud launch outside the new explicit authority. |

If any gate fails, publish `FAIL` with exact immutable evidence. If a required datum is unavailable, mark it `NOT_VERIFIABLE`; do not infer PASS from supervisor uptime or healthy minute snapshots. A local code/test PASS only qualifies the implementation for review, not the historical or future research data.

## Redundancy design selected locally

The local candidate selects two active-active public Bithumb connections with bounded first-arrival deduplication, conflict quarantine, per-source diagnostics, and union-based logical coverage. Connection attempts are serialized at 250 ms, below the documented 10 requests per IP per second. Newly generated runtime configurations bind the redundancy mode into the configuration fingerprint; historical sealed configurations retain their original behavior. See [BITHUMB_REDUNDANCY_DESIGN_20260921.md](BITHUMB_REDUNDANCY_DESIGN_20260921.md) for the architecture comparison, official-source findings, identity rules, teardown policy, and limits.

**Next single action:** review the local forensic report, collector changes, scheduler identity limit and redundancy decision; seek a new, separate authorization only after the design and launch budget have been decided. Do not run a validation from this document.
