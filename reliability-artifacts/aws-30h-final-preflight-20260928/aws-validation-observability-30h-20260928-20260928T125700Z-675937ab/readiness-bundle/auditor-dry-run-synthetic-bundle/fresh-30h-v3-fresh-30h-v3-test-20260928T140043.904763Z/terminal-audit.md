# Fresh 30H-v3 Terminal Audit

**Overall:** PASS
**Epoch:** fresh-30h-v3-test
**Run ID:** fresh-30h-v3-run-test
**Expected runtime commit:** 22e06b9527798567e185fb0dd41dca3a448f444e
**Expected runtime tree:** a44c9591045f3634cda1066d582927f277b79e2d

| Check | Status | Result |
|---|---|---|
| runtime_identity | **PASS** | Runtime identity matches the Fresh 30H-v3 seal. |
| actual_start_end_and_duration | **PASS** | Actual timestamps and supervisor duration satisfy the sealed runtime duration. |
| component_exit_codes | **PASS** | Collector, archive scheduler, and publisher all started and exited cleanly. |
| systemd_result_and_restarts | **PASS** | Systemd reports success, zero restarts, and zero main-process exit status. |
| terminal_witness | **PASS** | Terminal witness is clean and bound to the sealed run. |
| collector_lifecycle | **PASS** | Collector reached COMPLETE and flushed final manifests. |
| queue_persistence_and_writer | **PASS** | Final queue, unpersisted records, writer errors, and drops are zero; backpressure is reported. |
| active_active_dedup_and_conflicts | **PASS** | Two Bithumb sockets were active, deduplication was enabled, and no conflicting duplicate was observed. |
| qualifying_cohorts_and_feed_counts | **PASS** | Every sealed qualifying cohort has exactly one passing receipt with the expected slot count. |
| all_feed_slots_and_receipts | **PASS** | Every sealed feed slot has exactly one valid local coverage receipt. |
| local_s3_receipt_hash_equality | **PASS** | Local and S3 receipt mirrors match byte-for-byte. |
| receipt_immutability | **PASS** | Every local receipt retained the same hash across observations. |
| evidence_hash_index | **PASS** | Every indexed evidence file matches its SHA-256. |
| scheduler_retries | **PASS** | No scheduler retries were observed. |
| finalizer_retries | **PASS** | No finalizer retry events were observed. |
| recovery_invocations | **PASS** | No recovery invocation was observed. |
| duplicate_finalization | **PASS** | No feed slot was finalized more than once. |
| closed_at_utc_stability | **PASS** | Repeated finalizations preserve closed_at_utc. |
| finalization_evidence_hash_stability | **PASS** | Repeated finalizations preserve evidence hashes. |
| restart_idempotency_path | **PASS** | The known restart-sensitive finalizer path was not exercised. |

This report is derived from a local evidence export. The input evidence was not modified.
