# Future Runtime Only: 30H Evidence and Reliability Patches

These are frozen requirements for a later runtime release. **Do not deploy, stage, or apply them to the current run or sealed artifacts.** Current live runtime stays at `b4d482363e2f988dad9c6d29053f97e1e4160883` / `5c96ed79fee107c1604ee7018621835910221fdf`.

| Area | Future change | Required regression evidence | Current run treatment |
|---|---|---|---|
| Finalization traces | Emit versioned, append-only native events for scheduler/finalizer retries, recovery, duplicate/idempotent calls, stable `closed_at_utc`, stable evidence hashes, and restart path exposure. Include exact run/epoch and monotonic sequence. | Each event persisted before success; process interruption/restart reproduces sequence; tamper/hash-chain negative tests. | Native trace absent: `NOT_VERIFIABLE`; receipts/journals remain explicitly reconstructed. |
| Terminal witness ordering | Persist `s3_uploaded=false`; perform `put_object`; only after success persist `s3_uploaded=true`. On error/kill, retain false and exact error class. | Success, AccessDenied, timeout, network error, process kill, retry, duplicate version. | Do not change deployed witness; require exact S3 GET bytes/hash and VersionId. |
| Observer unit | Select the exact 30H collector unit for 108000 seconds and bind the observer to its invocation ID; do not fall back to `bitcoin-trader-transient-*`. | Unit selection for all supported duration boundaries; wrong-unit fail-closed test. | Do not repair observer during this run; observer reports are diagnostic only. |
| Child progress liveness | Track a bounded progress heartbeat from archive scheduler/finalizer, distinguish idle from stuck, and surface age/last successful operation. | Healthy idle, healthy work, blocked queue, dead child, heartbeat expiry tests. | No runtime changes; report existing child telemetry only. |
| Binance redundancy | Add and declare physical connection redundancy, independent health, conflict/dedup instrumentation, and failure behavior. | Physical socket counts, one-leg disconnect, both-leg failure, duplicate events, conflict accounting. | Current one-socket design is a body risk; never lower disconnect tolerance. |
| Upbit redundancy | Same explicit redundancy and conflict/dedup contract for Upbit. | Physical socket counts and single/both-leg failure, duplicates, conflicts. | Current one-socket design is a body risk; never lower disconnect tolerance. |
| Sealed bundle exporter | Include a complete deterministic allowlisted payload inventory, explicit provenance and source classification, bind to an externally preserved seal hash, and never accept an index as its own trust root. | Omitted/extra/renamed/symlinked payloads, altered seal, rebuilt local index, duplicate destination, partial copy. | Offline v2 exporter is available for post-run only; no current incomplete bundle adjudication. |
| Prelaunch evidence producers | Retain producer scripts, exact argv, stdout/stderr, principal identity, raw response, capture time, evidence hashes, and externally anchored manifest for every check. Independently recompute critical S3/identity/runtime assertions. | Rewrite `status=PASS`, swap principal, AccessDenied versus 404, stale snapshot, rehash bundle, absent producer log. | Preserve both 09:26 35/36 FAIL and 09:29 36/36 PASS; gate weakness alone does not invalidate the run. |

Additional contract corrections frozen for a future runtime/gate version:

- Duration-to-hour coverage derives from actual collector observation bounds and producer semantics; the opening cohort is partial even at `:00`.
- Receipt parity applies to declared durable receipt classes and an exact complete prefix listing, not whole-tree equality.
- Terminal systemd evidence comes from durable journal rows scoped to the exact InvocationID, including exit, timing, watchdog, signal and restart fields.
- `AccessDenied`, expired auth, timeout, and incomplete listings remain `NOT_VERIFIABLE`; only authorized confirmed absence can be `FAIL`.
- Current and future verdict records keep original and amended auditor results side by side. No threshold or acceptance rule is tuned after terminal outcome.
