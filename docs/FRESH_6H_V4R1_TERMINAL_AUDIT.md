# Fresh 6H-v4r1 terminal audit

## Official decision

`FRESH_6H_V4R1_TECHNICAL_GATE = FAIL`

`GO_NO_GO = NO_GO`

The collector completed its sealed six-hour lifecycle cleanly, but every qualifying cohort naturally finalized `FAIL`. Supervisor `PASS` and terminal `CLEAN_SUCCESS` describe process completion only; they do not override the qualification receipts.

## Final morning report

- `PREVIOUS_EXPIRED_RUN = PRELAUNCH_WINDOW_EXPIRED / NOT_LAUNCHED`
- `NEW_RUN = aws-validation-observability-6h-20260922-20260922T035000Z-v4r1`
- `T0_KST = 2026-09-22 12:50:00 KST`
- `ACTUAL_LAUNCH_KST = 2026-09-22 12:50:04.055728 KST`
- `HOST_TIMER_TRIGGER_DELAY_SECONDS = 2`
- `LAUNCH_11_CONSUMED = YES`
- `RUNTIME_COMMIT = 525a7d339260481e63e36f1a3948ce08eb15d9be`
- `RUNTIME_TREE = 08c89c63e0a5de8e94450a2715ea19664211b091`
- `QUALIFYING_COHORTS = 2026-09-22_04 .. 2026-09-22_08`
- `RECEIPTS_PRESENT = 5 / 5`
- `COHORT_PASS = 0 / 5`
- `COHORT_FAIL = [_04, _05, _06, _07, _08]`
- `RECEIPT_IMMUTABILITY = 5 / 5`
- `PRIMARY_DISCONNECTS = 1`
- `SECONDARY_DISCONNECTS = 0`
- `SINGLE_SOURCE_FAILURES_WITHOUT_LOGICAL_GAP = 1`
- `DUAL_SOURCE_GAPS = 0`
- `DEDUPLICATED_FRAMES = 1,560,356`
- `CONFLICTING_DUPLICATES = 37,833`
- `DEDUP_CACHE_PEAK = NOT_VERIFIABLE; maximum directly observed 13,192, final 10,708`
- `SCHEDULER_HOL = PASS`
- `QUEUE = 0`
- `UNPERSISTED = 0`
- `WRITER_ERRORS = 0`
- `NRESTARTS = 0`
- `TERMINAL_WITNESS = PRESENT / CLEAN_SUCCESS`
- `TEST_RESULTS_TOUCHED = NO`
- `AWS_LAUNCHES_USED = 11 / 12`
- `30H_UNLOCKED = NO`
- `30H_LAUNCHED = NO`
- `ALPHA = UNPROVEN`
- `PAPER = NOT_STARTED`
- `LIVE = DISABLED`
- `PRIVATE_API = DISABLED`

## Terminal integrity

The supervisor ran for 21,624.000308 seconds with configured collection duration 21,600 seconds. `full_duration_satisfied=true`, `forced_timeout=false`, collector/scheduler/publisher exit codes were all zero, final metrics were valid, final manifest flush was observed, and finalization ended `COMPLETE` with 532 entries and zero pending or failed finalization entries.

The terminal receipt was recorded at `2026-09-22T09:50:28.292927Z` with SHA-256 `86b8ce12ef41eb420112df69de11ded942fbf9492272aacf76db363c2600de61`. Its top-level epoch is the generic `bitcoin-trader-6h`, rather than the sealed run epoch, although its run ID and embedded last-health identity are exact. Its S3 upload field is false, so the terminal receipt is locally evidenced on EC2 and journaled, but not independently preserved in S3.

The collector, supervisor, and archive scheduler exited. The separate read-only runtime observer remains active as PID 1227924 with `NRestarts=0`; this audit left it unchanged under the no-mutation contract.

## Cohort evidence

| Cohort | Status | Reasons | Events | S3 raw / coverage | Receipt SHA-256 |
| --- | --- | --- | ---: | ---: | --- |
| `_04` | FAIL 76/76 | conflict 76 | 941,976 | 76 / 76 | `986fd6872ff6223b60a02eec8322d871ac8b8d9d43aa6a5a6c30366062d9186f` |
| `_05` | FAIL 76/76 | conflict 76 | 962,296 | 76 / 76 | `acfc35a7acb7dbc7209ce4f8cc12312bbb306c0819f1ee584821960365de87c1` |
| `_06` | FAIL 76/76 | conflict 76; collection gap 60 | 723,864 | 16 / 76 | `a551e853b2bac1fab5483710ca8751bafea7416c3bf9a41a9bf46379c9e6c36a` |
| `_07` | FAIL 76/76 | conflict 76 | 793,655 | 76 / 76 | `7f4756c4cc506731b7031d5d8e7d724850fcf831329be0209f97fb773999ec23` |
| `_08` | FAIL 76/76 | conflict 76 | 981,459 | 76 / 76 | `08d2ef27a33374c56c53c209fd6db0561f296fd8387a73eaefdf09f81e553cce` |

All 380 expected local raw files and all 380 coverage files exist and are nonempty. The qualifying cohorts contain 4,403,250 events and 5,986,492,614 raw bytes. S3 contains 710 nonempty cohort objects: 320 raw, 380 coverage, and 10 failure/finalized evidence objects. Cohort `_06` has all 76 local raw files, but only the 16 Binance/Upbit raw objects were archived because the 60 Bithumb slots were incorrectly classified with `COLLECTION_GAP`.

The genuine hash observer recorded matching T1 and T2 hashes for all five receipts. The completed hash state has SHA-256 `6965001398bd52bd91972731c191207f43c7532ef99421a02efebd97ee29c3e1`.

## Root cause

1. Active-active Bithumb copies with the same native identity and matching trade fields commonly differed only in the source-local top-level arrival timestamp. Candidate `525a7d3` treated those copies as conflicting payloads.
2. The resulting cumulative Bithumb conflict count was copied into every logical feed's frozen writer snapshot. This made all 76 slots fail, including Binance and Upbit.
3. The closed-hour finalizer retained single-session disconnect semantics. A primary Bithumb reconnect at `06:54:34Z` therefore marked all 60 Bithumb slots in `_06` as `COLLECTION_GAP`, even though the secondary session preserved logical union continuity.

Local commit `a4fb7e5391fa370856160d57f95ac63c51764d29` fixes the first two defects and is not AWS validated. The union-aware finalizer defect remains unresolved.

## Additional launch-control blockers

A read-only artifact review found three future-launch blockers:

1. Timer arming creates the timer before all later checks and does not roll it back if a post-creation check fails.
2. The receipt hash observer does not validate the receipt payload's `epoch` and `run_id` before recording T1.
3. The host guard verifies that the pre-arm attestation has a timestamp but does not enforce a bounded attestation age.
4. The terminal receipt uses a generic top-level epoch and was not uploaded to S3.

The actual v4r1 receipts have the correct identity and stable hashes, so these findings do not change the official result from `FAIL`; they block reuse of the launch machinery without repair.

## Next action

Keep launch #12 locked. Fix the union-aware finalizer classification and the three launch-control findings, retain the existing duplicate-scoping fix, run the complete local gate, freeze a new candidate, and request separate authorization for another Fresh 6H. Do not launch Fresh 30H-v3.

The machine-readable audit and its SHA-256 seal are stored under `evidence/aws-validation-observability-6h-20260922-20260922T035000Z-v4r1/`.
