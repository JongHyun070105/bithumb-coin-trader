# Fresh 30H-v2 Bithumb gap: forensic matrix

**Scope.** The historical `2026-09-19_12` UTC receipt remains `FAIL` for 60 Bithumb feeds. The collector supervisor's 108,000-second success and the archive scheduler's later head-of-line blockage are separate facts. This document records evidence and its limits before changing collector code. Times below are UTC unless marked KST. The preserved source artifacts are under `reliability-artifacts/aws-30h-v2/terminal/`.

## Initial hypothesis matrix

Statuses are provisional pending raw event, host, and external checks. `SUPPORTED` identifies an observed mechanism; it does not prove the ultimate remote cause.

| ID | Description | Evidence for | Evidence against | Missing evidence | Confidence | Status |
| --- | --- | --- | --- | --- | --- | --- |
| H1 | Bithumb server, load balancer, or session incident | Bithumb stream went stale; later reconnect succeeded | No server-side incident record yet | Bithumb status and server logs | Low | NOT_VERIFIABLE |
| H2 | Network path transient specific to Bithumb | One exchange socket disconnected | No path telemetry | TCP resets, route/peer telemetry | Low | NOT_VERIFIABLE |
| H3 | EC2 host or kernel socket issue | A network socket was affected | Collector and other exchange processes ended normally | Kernel/network event log at incident | Low | NOT_VERIFIABLE |
| H4 | Python event-loop stall | Prior collector versions had boundary stalls | Incident is not at UTC cohort boundary; heartbeat task logged at 12:24:48 | Per-exchange frame rates and loop lag at 12:23–12:26 | Low | WEAKLY_SUPPORTED |
| H5 | Collector backpressure, CPU, or IO stall | A receive silence can arise from local pressure | Final queue, drops, and writer errors are zero | Incident-time queue/CPU/IO metrics | Low | NOT_VERIFIABLE |
| H6 | GC or synchronous local operation | Could pause an event loop | No synchronous stall identified at incident | GC/loop lag trace | Low | NOT_VERIFIABLE |
| H7 | `websockets` ping/pong semantics | Ping timed out while market frames still arrived | Timeout did not itself disconnect at 12:24:48 | Pong trace and exact library behavior/version | Moderate for timeout, low for cause | WEAKLY_SUPPORTED |
| H8 | Custom heartbeat defect | Heartbeat and recv loop independently own teardown | At 12:24:48 it deliberately retained an active session | Deterministic race replay | Moderate for design risk | WEAKLY_SUPPORTED |
| H9 | Heartbeat and stale detector race | Both detectors can act on one socket in current code | Historical log shows the stale detector performed reconnect; no simultaneous heartbeat teardown shown | Per-detector monotonic transitions | Moderate for design risk | WEAKLY_SUPPORTED |
| H10 | `ws.close()` delays reconnect | Context manager exit could wait on close | No close start/end telemetry | Exact context-exit duration and library close behavior | Low | NOT_VERIFIABLE |
| H11 | Single socket magnifies one failure to 60 feeds | One `websockets.connect` carries all 20 markets × 3 stream subscriptions; all 60 failed together | None for blast radius | Exchange connection-limit guidance for a safe alternative | High | SUPPORTED |
| H12 | Frames arrived but client failed to consume/persist | Possible local receive/write stall | Final writer errors, queue drops, unpersisted count are zero | Incident-time per-exchange receive and persistence timestamps | Low | NOT_VERIFIABLE |
| H13 | Coverage/session false positive | Some Bithumb data exists on both sides of the hour | Finalized receipt says both `COLLECTION_GAP` and `HEARTBEAT_GAP_EXCEEDED`; session disconnected interval observed | Full frozen journal and actual raw-event timing | Low | WEAKLY_SUPPORTED |
| H14 | DNS, TLS, or TCP reconnection latency | Log reconnects at 12:25:06 after stale warning at 12:24:56 | No per-stage connect timing | DNS/connect/TLS/subscribe timestamps | Low | NOT_VERIFIABLE |
| H15 | Half-open Bithumb client connection | 30-second receive timeout and missing pong can fit | No socket-level proof | TCP state/peer close metadata | Low | NOT_VERIFIABLE |

## Established chronology and missing measurements

The supervisor log records `12:24:48` ping timeout with active data frames and explicit session retention, `12:24:56` connection-level stale timeout, and `12:25:06` new Bithumb connection. Its log timestamps have second precision. The frozen journal's previously audited representative session interval was `12:24:56`–`12:25:06`; this is a **recorded disconnected interval**, not the no-frame or persistence interval. Ping send, last pong, close start/end, dial start, TLS completion, subscription send, first frame, and first persisted record were not emitted by the current code at sufficient granularity. Subsequent sections will update these findings with any available read-only evidence.

## Review items already identified

PR #12 has four open review comments: two on historical 90m wrappers omitting witness/data-root flags; one on `launch_freshness.py` accepting naive timestamps; one on out-of-order heartbeat pruning in `session_evidence.py`. These are code-review findings, not a reason to rewrite sealed historical artifacts. The scheduler's finalized-FAIL skip still needs wrong-identity, malformed-receipt, and restart tests. No review comment establishes the Bithumb server as the root cause.

## Evidence custody and method

All times below are UTC. The historical runtime was `4fcdd819366918fa86e5597ed7d2271454d926c7`; this branch changes local code only. The guest data was inspected through the established `scripts/ssm_exec.py` interactive SSM StartSession helper, as `bitcoin-trader`, with read-only commands. No SendCommand, cloud mutation, launch, finalization, or private API call occurred. The source cohort is `2026-09-19_12` under `/var/lib/bitcoin-trader/30h-validation/aws-validation-observability-30h-20260919-20260919T095000Z-v2`.

| Immutable source | SHA-256 checked on guest | Use |
| --- | --- | --- |
| `coverage/journals/journal_2026-09-19_12.json` | `482a50a6d9939a12a095bf12a237b8a162e23844414f85e72e12f912a62f9c02` | 76 feed observations, session segments and health |
| `raw/2026-09-19/bithumb/orderbook/bithumb_orderbook_krw-btc_2026-09-19_12.jsonl` | `912c7b1453661428885be4c562afe8a37804c739de0be19054da51f957a2bb64` | Bithumb receive/write timing |
| `raw/2026-09-19/binance/orderbook/binance_orderbook_btcusdt_2026-09-19_12.jsonl` | `87da1127e943bf48faf67f331d9e3fee4ef7c15ba70b1e288f42ba756de7d666` | control stream |
| `raw/2026-09-19/upbit/orderbook/upbit_orderbook_krw-btc_2026-09-19_12.jsonl` | `a076518fd1ae0b9f7ec800ff75f4004ad3e87e2ea731fc5a6f9dcc2453c79a40` | control stream |

The incident fixture at `tests/fixtures/bithumb_gap_20260919.json` contains only derived timestamps, controls, hashes, and explicitly unknown stages. It does not replace the original data. The local preserved `reliability-artifacts/aws-30h-v2/terminal/` receipt and supervisor log were read unchanged. S3 minute observer objects for `12:23:46`, `12:24:47`, `12:25:47`, and `12:26:47` were read via the guest role without writing them.

## Exact chronology and four distinct gaps

The 12:15–12:35 raw event scan found 13,710 Bithumb BTC orderbook records, 11,999 Binance BTC orderbook records, and 7,824 Upbit BTC orderbook records. Adjacent receive timestamps, not minute totals, determine the measured gap. The Bithumb BTC trade and ticker streams are naturally sparse and cannot independently define socket silence; their incident-adjacent gaps were 44.936677 and 44.933888 seconds respectively. The dense orderbook stream provides the better timing bound.

| UTC time | Historical observation | Precision and implication |
| --- | --- | --- |
| 12:24:22.827136 | Exchange timestamp on last pre-gap BTC orderbook event | Exchange timestamp, distinct from local receipt; it was delivered at 12:24:26.556048. |
| 12:24:26.556048 | Last local Bithumb BTC orderbook receive | Microsecond wall time and monotonic `1582676999468442` ns. Last local write at 12:24:26.556144. |
| 12:24:48 | Ping timeout logged, session retained as “data frames actively arriving” | Second-resolution log. At the BTC feed, last frame was already about 22 seconds old; the code's 25-second freshness test can label such a frame “active.” This log is not proof a frame arrived at 12:24:48. |
| 12:24:56 | Receive loop's 30-second stale timeout, session close recorded | Second-resolution log and frozen journal; one Bithumb disconnect/reconnect. |
| 12:25:06 | New Bithumb connection log and journal session start | Second-resolution only; dial, handshake, subscription-send and close durations were not recorded. |
| 12:25:06.952272 | First post-gap BTC orderbook receive | Exchange timestamp 12:25:06.730799, monotonic `1582717395691911` ns; first write at 12:25:06.952517. |

| Measure | Interval | Meaning |
| --- | --- | --- |
| Historical **no-frame receive interval**, dense representative feed | `12:24:26.556048` → `12:25:06.952272` = **40.396224 s**; monotonic difference **40.396223469 s** | Adjacent persisted BTC orderbook local receives. It bounds the observed dense-feed interruption, not the exact physical socket failure instant. |
| Historical **recorded disconnected interval** | `12:24:56Z` → `12:25:06Z` = **10 s at one-second granularity** | Session-evidence labels; actual edge times could differ within roughly one second per edge. |
| Historical **persistence gap** | `12:24:26.556144` → `12:25:06.952517` = **40.396373 s** | Adjacent raw writes for the same feed. It closely tracks receipt, so local write backlog is not supported for this feed. |
| Historical **exchange-timestamp gap** | `12:24:22.827136` → `12:25:06.730799` = **43.903663 s** | Source timestamps on adjacent received messages. It cannot tell whether unseen events existed at the exchange. |

There is no defensible exact timestamp for ping send, last pong, close start/end, TCP dial, TLS/WebSocket handshake, subscription send, or first frame for *all* 60 logical feeds. The approximately 10-second disconnected interval must not be substituted for the 40.396-second received-data gap. The latter exceeds the strict 30-second continuity gate, so the finalized `FAIL` is scientifically warranted.

## Cross-exchange controls and host observations

Within `12:24:27` through just before `12:25:06`, Binance BTC orderbook persisted **390** records and Upbit BTC orderbook **246** records. Across 12:15–12:35 their largest adjacent receive gaps were **0.316062 s** and **3.090052 s**, respectively. Their frozen journal sessions remained connected through the incident; both final metrics report zero disconnects/reconnects. Their continuous receipt and persistence strongly disfavor a process-wide event-loop freeze or shared host-wide data-write halt. They do not rule out a Bithumb-specific client task fault, route issue, remote load balancer, or server issue.

The minute observer snapshots around the gap report queue depth 0, writer errors 0, and unpersisted 0; the final collector counters also report no queue drops, backpressure, malformed quarantine or writer errors. These snapshots are minute-spaced and the observer's RSS/FD fields describe its own process, so they cannot prove the collector had no millisecond CPU, GC or kernel stall. Incident-time collector CPU, GC, event-loop lag, packet trace, TCP reset details, DNS/TLS timings, and peer close frame were not captured. A `journalctl` read as the permitted guest user returned insufficient permissions; host/kernel cause is therefore **not verifiable**, not excluded.

The frozen journal contains two Bithumb BTC orderbook session segments: `96f8e178b5a14384bae563cffb49a83e` ends at 12:24:56 with `connection_stale_30s`, and its successor `2c3b0eb12a714279b42ff8ce18a07878` begins at 12:25:06. Binance and Upbit have a single continuous segment each in the hour. The finalized receipt marks all 60 Bithumb slots `FAIL` with `COLLECTION_GAP` and `HEARTBEAT_GAP_EXCEEDED`, while the other 16 feeds are data-present. The supervisor completed 108,000 seconds with exit 0, which does not overturn this cohort failure.

## Final hypothesis matrix

The initial matrix above was written before code changes. This table updates its status with the raw and control evidence; `SUPPORTED` refers to the observed mechanism, not a proved remote fault location.

| ID | Final status | Confidence and reason |
| --- | --- | --- |
| H1 server/LB/session incident | NOT_VERIFIABLE | Bithumb-specific symptom is compatible; no server incident record or peer trace. |
| H2 Bithumb-specific network path | NOT_VERIFIABLE | Compatible with unaffected Binance/Upbit; no route/TCP evidence. |
| H3 EC2 host/kernel socket | NOT_VERIFIABLE | Host-wide failure disfavored by controls; one-socket kernel/path defect remains possible. |
| H4 process-wide event-loop stall | DISFAVORED | Other feeds continued at expected cadence across the gap. Per-task stall not excluded. |
| H5 collector backpressure/IO stall | DISFAVORED | Queue/writer counters zero and write lag stayed small on representative Bithumb data. Minute samples cannot exclude a short local stall. |
| H6 GC/synchronous operation | NOT_VERIFIABLE | No historical GC or lag trace; broad pause disfavored by controls. |
| H7 ping/pong protocol semantics | WEAKLY_SUPPORTED | Missing pong was observed; whether the peer or path withheld it is unknown. Bithumb documents ping/pong support. |
| H8 custom heartbeat defect | SUPPORTED as design risk | The 12:24:48 “active frames” statement reflects a 25-second old-activity threshold, not a frame observed at that instant; it retained the session. No proof it caused the original silence. |
| H9 detector race | SUPPORTED as design risk | Old code had separate close/count paths; historical overlap is not shown. |
| H10 close delay | NOT_VERIFIABLE | 10 seconds between stale log and connected log is compatible with old default close handling, but no close/dial timing exists. |
| H11 single-socket blast radius | SUPPORTED, high confidence | All 20 markets × 3 types subscribed on one physical connection; all 60 failed together. |
| H12 frames consumed but not persisted | DISFAVORED | Representative receive/write deltas and zero writer/queue faults do not support it. |
| H13 coverage false positive | REJECTED for representative dense feed | Measured 40.396-second receive/persistence gaps independently exceed the 30-second contract. |
| H14 DNS/TLS/TCP reconnect latency | NOT_VERIFIABLE | No stage timestamps in historical collector. |
| H15 half-open connection | WEAKLY_SUPPORTED | 30-second receive silence and missed pong fit; no TCP/peer proof. |

**Root-cause verdict: PARTIALLY_SUPPORTED.** A Bithumb-specific connection or path liveness failure followed by a 30-second client stale decision is supported. The precise initiating layer (exchange server/load balancer, intermediate network, host socket, or Bithumb client task) is **not verifiable** from the preserved trace. The single physical socket made that failure affect 60 logical feeds. The recorded 10-second teardown/reconnect period and the old dual-owner logic were additional recovery/observability weaknesses, but causation of the initial silence is unproved.

## External protocol and incident research

| Official source checked 2026-09-21 | Published/updated time shown by source | Claim supported | Relevance and limit |
| --- | --- | --- | --- |
| [Bithumb WebSocket basic information](https://apidocs.bithumb.com/reference/%EA%B8%B0%EB%B3%B8-%EC%A0%95%EB%B3%B4) | Current reference page | Public v1 supports ticker/trade/orderbook; connection requests are limited to 10 per IP per second, with 429 and possible 10-minute block on continued excess. | Future sharding/redundancy needs rate control. This does not identify the incident cause. |
| [Bithumb connection management](https://apidocs.bithumb.com/reference/%EC%97%B0%EA%B2%B0-%EA%B4%80%EB%A6%AC) | Current reference page | RFC 6455 ping/pong and client ping are supported; about 120 seconds without send/receive may cause idle timeout. | A missed pong after an active stream deserves diagnostics; this was far shorter than documented idle timeout. |
| [Bithumb orderbook format](https://apidocs.bithumb.com/reference/%ED%98%B8%EA%B0%80-orderbook) | Current reference page | One request can specify several type fields; orderbook carries microsecond timestamp. | Confirms the current multi-type subscription is an allowed shape; timestamp semantics matter to the measured exchange gap. |
| [Bithumb changelog](https://apidocs.bithumb.com/changelog) and [historic public WebSocket maintenance notice](https://apidocs.bithumb.com/changelog/%EA%B3%B5%EC%A7%80-public-websocket-%EC%97%B0%EA%B2%B0-%EC%9D%BC%EC%8B%9C-%EC%A4%91%EB%8B%A8-%EC%95%88%EB%82%B4) | Changelog items around September 2026; maintenance notice May 22, 2026 for June 9 | Public WebSocket sessions may be closed during maintenance, but the cited notice concerns June, not September 19. No September 19 public incident notice was found in the checked official changelog. | Absence of a notice neither proves nor disproves a transient server incident. |
| [Bithumb public WebSocket request-rate notice](https://apidocs.bithumb.com/changelog/%EC%97%85%EB%8D%B0%EC%9D%B4%ED%8A%B8-public-websocket-%EB%8D%B0%EC%9D%B4%ED%84%B0-%EC%9A%94%EC%B2%AD-%EC%88%98-%EC%A0%9C%ED%95%9Crate-limit-%EC%A0%81%EC%9A%A9-%EC%95%88%EB%82%B4) | Dec 8, 2025, explicitly for v2.1.5 Beta | Beta public request messages capped at 5/s and 100/min; no receive-count limit stated. | Version-specific notice; do not silently apply its message limits to public v1. Still motivates conservative subscription pacing. |
| [websockets 17.1 asyncio API](https://websockets.readthedocs.io/en/latest/reference/asyncio/client.html) and [connection API](https://websockets.readthedocs.io/en/stable/reference/asyncio/connection.html) | 17.1 reference, matching local lockfile/runtime | `close_timeout` exists; `close()` waits for handshake/TCP closure; canceling `recv()` is safe. | Justifies a 1-second library close timeout plus a 2-second outer teardown bound in the new code. The historical runtime's exact library behavior was not traced at the incident. |

No contemporaneous authoritative Bithumb or AWS notice proving a September 19, 12:24 UTC incident was found. `SERVER_SIDE_INCIDENT = NOT_VERIFIABLE` and `AWS_NETWORK_CAUSE = NOT_VERIFIABLE`. A public notice search is weaker evidence than exchange/server or VPC packet telemetry.

## Historical code path audit (`4fcdd819`)

1. `_bithumb_loop` built one request containing `orderbook`, `trade`, and `ticker` for every one of 20 markets, and sent it on one WebSocket. That creates a common failure domain of 60 logical feeds. Per-market connection errors cannot be inferred from the 60 receipt failures.
2. The session opened **before** `websockets.connect` completed. Its `connected_at_utc` could therefore include dial/handshake time. No stage durations or peer address were recorded. This is a provenance weakness, not proof of the outage.
3. `_heartbeat_loop` pinged every 10 seconds and waited up to 25 seconds. On timeout it treated a `last_websocket_activity` age under 25 seconds as active and retained the socket. The 12:24:48 message could be produced using a 12:24:26 frame. On an inactive socket it independently closed the session, incremented `disconnect_count`, and awaited `ws.close()`.
4. The receive loop independently imposed `wait_for(ws.recv(), 30)`. On stale timeout it closed the session and incremented both disconnect and reconnect counts. The exception path had another counter and backoff path. There was no shared first-wins decision or single teardown owner. Simultaneous detector outcomes could double count or race the evidence state; this possibility was not observed directly in the frozen incident.
5. Context-manager exit could wait on a close handshake. The 12:24:56 → 12:25:06 log interval includes unknown close, dial, handshake and subscription work. The exact 10 seconds cannot be attributed to `close()` without stage telemetry.
6. `last_websocket_activity` was updated on pong as well as frames. It could not alone discriminate live market-data flow from a control-frame-only socket. New diagnostics separate frame and pong ages.
7. Existing Bithumb feed confirmation is based on first actual stream frames, not merely sending a subscription. This is appropriate for a data collector, but sparse trade/ticker types can remain partially confirmed for a while; future validation must observe all expected feeds and avoid treating a send as success.

## Local engineering changes and what they can guarantee

The Bithumb path now has a per-connection `_ReconnectDecision` event. Heartbeat loss, frame-stale timeout and socket exceptions request the first reason; one owner closes the socket, closes the session and updates counters. The receive wait can be interrupted by the heartbeat signal while `recv()` is blocked. The WebSocket connection opens before a session's `connected_at_utc` is recorded. The library gets `close_timeout=1.0`, and context exit has a separate 2-second bound with transport abort fallback. These are recovery and evidence improvements; they cannot recreate exchange events missing during a remote outage.

Each Bithumb fault now yields a bounded, versioned `last_connection_diagnostic` in collector metrics and one structured log record: connection UUID, attempt UTC, connect/subscribe/first-frame durations when observed, frame/ping/pong ages, first trigger, close timing and timeout, close code/reason, exception type/repr, peer address when exposed, queue depth, writer errors, other-exchange last activity, and sampled process event-loop lag. A lightweight 1-second lag sampler tracks the maximum for the run. The historical run had none of these per-stage measurements, so no retroactive duration is claimed. No per-frame diagnostic log was added.

The PR #12 scheduler's finalized-FAIL skip has been tightened to reject explicit foreign `epoch`, `collector_epoch`, `run_id`, or `collector_run_id` fields; corrupt and wrong-cohort receipts remain unskippable. The same explicit-identity check now applies to partial/ineligible completion and full-scan PASS receipts. The historical finalized receipt lacks epoch/run fields, so its eligibility still relies on the run-scoped `receipt_root`. That is a documented residual identity limitation. The valid `FAIL` receipt stays visibly `FAIL` and is never rewritten; later cohorts can be discovered after it. Existing `SKIPPED_NON_QUALIFYING`, `INELIGIBLE_PARTIAL`, and full-scan status semantics were preserved and covered by the broader scheduler suite. Review comments on naive launch timestamps and heartbeat pruning were fixed in local source/tests. The two comments on sealed 90-minute historical wrappers remain as review findings because changing those artifacts would invalidate historical seals; future wrapper generation should include the witness and data-root arguments.

The historical `4fcdd819` scheduler's eligible-hour discovery skipped only completed cohorts, while `run_once()` always chose `eligible[0]` in oldest-first order. A finalized `FAIL` was explicitly *not* completed, so cohort `_12` remained the first eligible target. The terminal audit lists `_12` failed and 26 later finalized receipts missing (`_13` through next-day `_14`). This confirms the scheduler head-of-line mechanism as a second defect, separate from the Bithumb connection interruption. PR #12 introduced the matching finalized-FAIL skip; this branch audits and strengthens its identity behavior. None of this changes the failed historical receipt.

## Deterministic historical replay and outage model

The fixture records an exact *observation* sequence: last Bithumb BTC orderbook receive at 12:24:26.556048, ping-timeout log second 12:24:48, stale-decision log second 12:24:56, session successor second 12:25:06, and first receive 12:25:06.952272. It encodes the 390 Binance and 246 Upbit control events and lists unknown stages. The old code's behavior is the preserved 40.396-second receive gap and 10-second session-label interval. The new code was not present in that run; local fault injection proves one decision, interruptible receive, bounded teardown, and diagnostic serialization, but **does not produce an exact counterfactual historical gap**. Even idealized immediate reconnection after a 30-second stale detector leaves little or no room under a strict 30-second limit; this fix alone cannot guarantee PASS.

For a separate best-case *model* of an unavailable server, retry attempts at elapsed seconds `0, 1, 3, 7, 15, 31` (jitter, handshake and first-frame latency excluded) yield:

| Server unavailable | Earliest modeled successful dial | Attempts including initial | Minimum missing-data gap | Strict 30 s result |
| ---: | ---: | ---: | ---: | --- |
| 1 s | 1 s | 2 | ≥1 s | Cannot conclude without actual feed cadence |
| 3 s | 3 s | 3 | ≥3 s | Cannot conclude without actual feed cadence |
| 5 s | 7 s | 4 | ≥7 s | Cannot conclude without actual feed cadence |
| 10 s | 15 s | 5 | ≥15 s | Cannot conclude without actual feed cadence |
| 15 s | 15 s | 5 | ≥15 s | Cannot conclude without actual feed cadence |
| 30 s | 31 s | 6 | ≥31 s | FAIL even before handshake/frame delay |

The model counts one active socket at a time and therefore introduces no duplicate raw messages; actual duplicate events from exchange replay cannot be inferred. The number of missing exchange events is unquantified, and none can be recovered by faster reconnect if the exchange never delivered them. A 30-second server outage breaches the contract even in this optimistic model.

## Redundancy decision and scientific consequence

One physical socket cannot provide a zero-gap guarantee when the peer or path stops delivering frames. Splitting stream types over multiple sockets would reduce blast radius for a socket-specific failure, but a server-wide or common-route outage could still stop all of them. Overlapping redundant public sockets could improve resilience only if the exchange's concurrent use policy, rate limits, duplicate behavior, and event identity are validated first. The checked public docs give per-IP connection-attempt limits but no reliable incident-independent guarantee for multiple simultaneous sockets. **Redundancy is proposed for a separately authorized design/validation pass, not implemented here.** Any overlapping design needs deterministic duplicate detection by exchange, stream, market, exchange timestamp, message identity/sequence when present, and raw hash fallback, using only information available at receive time; source coverage must still mark truly missing intervals as gaps. A different source or a gap-tolerant research policy would be a separate scientific decision, not an implicit threshold change.

## Verification and residual limits

The local fault suite covers normal pong, pong timeout with fresh frames, combined frame/pong loss, heartbeat interruption of blocked receive, stale timeout, near-simultaneous first-wins decisions, hanging close, close metadata, abrupt socket error, failed first connect then success, partial feed confirmation, other-exchange controls, event-loop lag, queue pressure, cohort boundary evidence, corrupt/foreign/wrong-cohort FAIL receipts, and six modeled server-outage durations. The replay fixture verifies the four gap measures from immutable source values. No test uses live Bithumb, AWS launch, order submission, or `test-results/` output.

Remaining uncertainty: historical process-level lag, packet path, server logs, exact teardown stage timings, per-feed first-frame time and September 19 public incident attribution are not verifiable. A code fix cannot turn the 2026-09-19_12 `FAIL` into `PASS`. Fresh 30H-v2 remains `FAIL`, research readiness `FAIL`, alpha unproven, paper not started, live/private API disabled. See `docs/NEXT_BITHUMB_RELIABILITY_VALIDATION_PLAN.md` for future authorization and gates.

Final local checks on this branch: 24/24 Bithumb replay/fault-model tests, 109/109 targeted reliability tests, and full `pytest -q` **1616 passed, 2 skipped**. Targeted Pyright on every changed Python source/test module: **0 errors**. Changed-module `compileall` and `git diff --check`: **PASS**. The pre-existing untracked `test-results/.last-run.json` remained at SHA-256 `e22df5d0991eb28c09093b1e678b3fa8cd1fab48185d38e67cf79fb6e63ad5ea`; it was neither edited nor staged. These are local software checks, not a new validation run.
