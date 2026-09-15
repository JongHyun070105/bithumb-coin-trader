# AWS 30H V3 Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace V2's ambiguous start parsing, history-sized shutdown work, and RAW-count coverage assumption with strict actual-start normalization, bounded incremental finalization, and exact 76-slot full-hour evidence.

**Architecture:** Focused domain modules own hashing, actual-start normalization, the one-time schedule, durable progress, WebSocket sessions, and coverage. A closed-hour finalizer enforces RAW receipt/restore before positive coverage; existing collector, archive, contract, and audit entry points delegate to these modules while retaining explicit V2 read compatibility.

**Tech Stack:** Python >=3.11 (current project venv: 3.14), `asyncio`, `websockets>=12`, `zstandard>=0.22,<1`, file-backed JSON with `fcntl`/`fsync`, pytest, Pyright.

**Spec:** `docs/superpowers/specs/2026-09-14-aws-30h-v3-remediation-design.md`

## Global Constraints

- V2 remains `FAIL — IMMUTABLE`; never rewrite V2 evidence, seals, receipts, or reports.
- V3 requires exactly 76 coverage slots in each of exactly 30 candidate full UTC hours.
- `qualification_start_utc = strictly_next_utc_hour(actual_start_utc)`, even at an exact boundary.
- Seal `required_qualifying_full_hours=30` and `maximum_collection_window_seconds=111600`; V3 never receives fixed `108000`.
- Capture UTC and monotonic start once, derive the monotonic stop once, and never move it after wall-clock changes.
- RAW and coverage use the same `local_write_ts`; coverage increments only after append success.
- Subscription/session/heartbeat/writer-health gates apply to `DATA_PRESENT` and `VERIFIED_ZERO_EVENT`.
- Heartbeat probe interval is 10 seconds, timeout is 10 seconds, and maximum gap is 30 seconds for all three exchanges.
- Zero-event evidence never creates a zero-byte RAW file.
- RAW receipt plus restore precede `DATA_PRESENT` coverage; zero-event coverage skips RAW.
- `entries/*.json` is finalization authority; bounded caches reconcile through one `transaction.json`.
- Terminal history must report `historical_raw_files_opened=0` and `historical_raw_bytes_read=0` at shutdown.
- Canonical JSON is UTF-8 `json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)` without BOM/newline.
- V3 receipts use schema v3 neutral `source_*` fields and `artifact_kind`; v2 is read-only legacy input.
- Pyright 1.1.414 must report 0 errors and 0 warnings for every changed Python path. The unchanged full-repository baseline currently has 550 errors, so a repo-wide zero claim is forbidden and unrelated typing debt is outside scope.
- This plan performs no dependency addition, AWS/IAM/Terraform write, runtime launch, systemd launch, or trading action.

## File Responsibility Map

**Create**

- `src/bithumb_coin_trader/evidence_hashing.py` — sole canonical JSON/SHA implementation.
- `src/bithumb_coin_trader/actual_start_evidence.py` — exact canonical-v2 and V2-legacy-v1 adapter.
- `src/bithumb_coin_trader/qualification_schedule.py` — strictly-next boundary and fixed monotonic deadline.
- `src/bithumb_coin_trader/incremental_finalizer.py` — entries, bounded indexes, WAL recovery, dirty-tail work.
- `src/bithumb_coin_trader/session_evidence.py` — owning-connection subscription/liveness segments.
- `src/bithumb_coin_trader/feed_hour_coverage.py` — journals, common gate, immutable coverage.
- `src/bithumb_coin_trader/closed_hour_finalizer.py` — non-circular state-dependent finalization.
- `tests/fixtures/aws_30h_v2_actual_start_evidence.json` — sanitized real V2 structure.
- Focused tests matching every new module.
- `scripts/benchmark_incremental_finalization.py` — deterministic 1/10/30 scale report.

**Modify**

- `scripts/evidence_contract.py`, `scripts/compose_epoch_contract.py` — shared hashing and normalized start.
- `src/bithumb_coin_trader/bounded_supervisor.py` plus bounded/transient launch scripts — V3 schedule mode.
- `src/bithumb_coin_trader/microstructure_storage.py` — identity-bound schema-v5 manifests.
- `src/bithumb_coin_trader/cross_market_collector.py` and its runner — sessions, fences, progress, bounded shutdown.
- `src/bithumb_coin_trader/pre_soak_archive.py` — generic immutable artifacts and receipt v3.
- `src/bithumb_coin_trader/archive_scheduler.py` and orchestrator — frozen-journal discovery and ordered finalization.
- `scripts/audit_72h_soak.py`, `scripts/build_epoch_manifest.py` — V3 exact-slot branch with V2 preservation.
- Existing focused tests named in each task.

---

### Task 1: Canonical Hashing and Strict Actual-Start Normalization

**Files:**
- Create: `src/bithumb_coin_trader/evidence_hashing.py`
- Create: `src/bithumb_coin_trader/actual_start_evidence.py`
- Create: `tests/fixtures/aws_30h_v2_actual_start_evidence.json`
- Create: `tests/test_actual_start_evidence.py`
- Modify: `scripts/evidence_contract.py:1-36`
- Modify: `scripts/compose_epoch_contract.py:17-48,110-132`
- Modify: `tests/test_phase6_3_contract.py`
- Modify: `tests/test_phase6_runbook_e2e.py`

**Interfaces:**
- Consumes: `ActualStartIdentity` and untrusted `Mapping[str, object]`.
- Produces: `canonical_json_bytes`, `canonical_sha256`, `file_sha256`, and `normalize_actual_start_evidence`.

- [ ] **Step 1: Add the sanitized legacy fixture**

Copy `evidence/aws-validation-30h-20260912-6576f63/actual-start-evidence.json` to the fixture unchanged in discriminator, identities, timestamps, unit, and timing contract. Check it:

~~~bash
rg -ni 'secret|token|password|access.?key|private.?key' tests/fixtures/aws_30h_v2_actual_start_evidence.json
~~~

Expected: no output.

- [ ] **Step 2: Write failing canonicalization and adapter tests**

~~~python
def test_canonical_bytes_are_exact_and_reject_non_finite() -> None:
    assert canonical_json_bytes({"한글": 1, "b": [2, 1], "a": True}) == (
        b'{"a":true,"b":[2,1],"\\ud55c\\uae00":1}'
    )
    with pytest.raises(ValueError):
        canonical_json_bytes({"bad": float("nan")})


def test_exact_v2_legacy_fixture_normalizes() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    normalized = normalize_actual_start_evidence(payload, expected_identity(payload))
    assert normalized.runtime_commit == payload["runtime_code_commit"]
    assert normalized.source == payload["systemd_unit"]
    assert normalized.captured_at_utc == "2026-09-12T11:24:51Z"


@pytest.mark.parametrize("field,value,reason", [
    ("runtime_commit", "6576f632b3f44eb68645bad4304665c0ea87512d", "ACTUAL_START_ALIAS_MIXED"),
    ("schema_version", 3, "ACTUAL_START_SCHEMA_UNSUPPORTED"),
    ("actual_start_time_utc", "2026-09-12T20:24:51+09:00", "ACTUAL_START_TIMESTAMP_NOT_UTC"),
])
def test_legacy_mutations_fail_closed(field: str, value: object, reason: str) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload[field] = value
    with pytest.raises(ValueError, match=reason):
        normalize_actual_start_evidence(payload, expected_identity(payload))
~~~

Also enumerate canonical-v2 success; every identity mismatch; naive/non-zero-offset timestamps; unexpected/missing legacy keys; wrong evidence kind; and equal-valued duplicate aliases.

- [ ] **Step 3: Verify RED**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_actual_start_evidence.py tests/test_phase6_3_contract.py -q
~~~

Expected: import failure for both new modules.

- [ ] **Step 4: Implement the exact public types/functions**

~~~python
def canonical_json_bytes(
    value: Mapping[str, object], excluded: Collection[str] = (),
) -> bytes:
    blocked = set(excluded)
    body = {key: item for key, item in value.items() if key not in blocked}
    return json.dumps(
        body, ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(
    value: Mapping[str, object], excluded: Collection[str] = (),
) -> str:
    return hashlib.sha256(canonical_json_bytes(value, excluded)).hexdigest()


@dataclass(frozen=True)
class ActualStartIdentity:
    collector_epoch: str
    collector_run_id: str
    runtime_commit: str
    runtime_config_fingerprint: str


@dataclass(frozen=True)
class NormalizedActualStartEvidence:
    schema_version: int
    evidence_kind: str
    collector_epoch: str
    collector_run_id: str
    runtime_commit: str
    runtime_config_fingerprint: str
    actual_start_time_utc: str
    source: str
    captured_at_utc: str


def normalize_actual_start_evidence(
    payload: Mapping[str, object],
    expected: ActualStartIdentity,
) -> NormalizedActualStartEvidence:
    discriminator = (payload.get("schema_version"), payload.get("evidence_kind"))
    if discriminator == (2, EVIDENCE_KIND):
        return _normalize_canonical_v2(payload, expected)
    if discriminator == (1, EVIDENCE_KIND):
        return _normalize_v2_legacy_v1(payload, expected)
    raise ValueError("ACTUAL_START_SCHEMA_UNSUPPORTED")
~~~

Declare this exact 20-key V2 shape as both required and allowed:

~~~python
V2_LEGACY_V1_REQUIRED_KEYS = frozenset({
    "schema_version", "evidence_kind", "collector_epoch", "collector_run_id",
    "runtime_code_commit", "runtime_git_tree", "runtime_config_fingerprint",
    "v2_preparation_commit", "authorization_commit", "authorization_evidence_path",
    "systemd_unit", "main_pid", "actual_start_time_utc", "observed_pre_launch_utc",
    "observed_post_launch_utc", "systemd_start_timestamp", "systemd_until_timestamp",
    "launch_exit_code", "timing_contract", "scientific_status",
})
V2_LEGACY_V1_ALLOWED_KEYS = V2_LEGACY_V1_REQUIRED_KEYS
~~~

A shared timestamp helper accepts only `Z` or `+00:00`, requires zero offset, normalizes to `Z`, and emits stable reason codes without payload contents.

- [ ] **Step 5: Delegate both scripts**

`scripts/evidence_contract.py` re-exports the package functions. `compose_epoch_contract.py` constructs `ActualStartIdentity`, calls the normalizer, and removes its local `validate_actual_start` and alias chains.

- [ ] **Step 6: Verify and commit**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_actual_start_evidence.py tests/test_phase6_3_contract.py tests/test_phase6_runbook_e2e.py -q
git diff --check
git add src/bithumb_coin_trader/evidence_hashing.py src/bithumb_coin_trader/actual_start_evidence.py scripts/evidence_contract.py scripts/compose_epoch_contract.py tests/fixtures/aws_30h_v2_actual_start_evidence.json tests/test_actual_start_evidence.py tests/test_phase6_3_contract.py tests/test_phase6_runbook_e2e.py
git commit -m "feat: normalize actual start evidence"
~~~

Expected: all listed tests pass and historical evidence files are untouched.

---

### Task 2: Strictly-Next Schedule and Fixed Monotonic Stop

**Files:**
- Create: `src/bithumb_coin_trader/qualification_schedule.py`
- Create: `tests/test_qualification_schedule.py`
- Modify: `src/bithumb_coin_trader/bounded_supervisor.py`
- Modify: `scripts/run_bounded_short_smoke.py`
- Modify: `scripts/launch_short_smoke_transient.py`
- Modify: `scripts/run_cross_market_collector.py`
- Modify: `tests/test_bounded_supervisor.py`
- Modify: `tests/test_transient_launch.py`

**Interfaces:**
- Consumes: paired UTC/monotonic observation and `V3ScheduleConfig(30, 111600, path)`.
- Produces: `QualificationSchedule` whose `collection_stop_monotonic` is shared with the collector.

- [ ] **Step 1: Write failing boundary/clock tests**

~~~python
@pytest.mark.parametrize("actual,start,stop", [
    ("2026-09-14T11:24:00Z", "2026-09-14T12:00:00Z", "2026-09-15T18:00:00Z"),
    ("2026-09-14T12:00:00Z", "2026-09-14T13:00:00Z", "2026-09-15T19:00:00Z"),
])
def test_strict_next_has_exactly_30_candidates(actual, start, stop):
    schedule = build_qualification_schedule(parse_utc(actual), 500.0, 30, 111600)
    assert schedule.qualification_start_utc == start
    assert schedule.collection_stop_utc == stop
    assert len(schedule.candidate_cohorts) == 30


def test_exact_boundary_uses_fixed_111600_second_monotonic_stop() -> None:
    schedule = build_qualification_schedule(parse_utc("2026-09-14T12:00:00Z"), 100.0, 30, 111600)
    assert schedule.collection_stop_monotonic == 111700.0
~~~

Add elapsed `>108000` and `<=111600`, readiness failure without shifting, no 31st replacement, wall-clock jump, V3 fixed-duration rejection, and legacy duration preservation.

- [ ] **Step 2: Verify RED**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_qualification_schedule.py tests/test_bounded_supervisor.py tests/test_transient_launch.py -q
~~~

Expected: new module missing and V3 CLI assertions failing.

- [ ] **Step 3: Implement and atomically persist the schedule**

~~~python
@dataclass(frozen=True)
class QualificationSchedule:
    schema_version: int
    actual_start_utc: str
    actual_start_monotonic: float
    qualification_start_utc: str
    collection_stop_utc: str
    collection_stop_monotonic: float
    required_qualifying_full_hours: int
    maximum_collection_window_seconds: int
    candidate_cohorts: Sequence[str]


def strictly_next_utc_hour(value: datetime) -> datetime:
    current = require_utc(value)
    return current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def parse_utc(value: str) -> datetime:
    if not (value.endswith("Z") or value.endswith("+00:00")):
        raise ValueError("TIMESTAMP_NOT_UTC")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return require_utc(parsed)


def format_utc(value: datetime) -> str:
    return require_utc(value).isoformat().replace("+00:00", "Z")


def build_qualification_schedule(actual_utc, actual_mono, hours, max_window):
    start = strictly_next_utc_hour(actual_utc)
    stop = start + timedelta(hours=hours)
    elapsed = (stop - actual_utc).total_seconds()
    if hours != 30 or max_window != 111600 or not 108000 < elapsed <= max_window:
        raise ValueError("V3_QUALIFICATION_WINDOW_INVALID")
    return QualificationSchedule(
        1, format_utc(actual_utc), actual_mono, format_utc(start), format_utc(stop),
        actual_mono + elapsed, 30, 111600,
        tuple((start + timedelta(hours=i)).strftime("%Y-%m-%d_%H") for i in range(30)),
    )
~~~

Writer/loader use mode 0600, file/directory `fsync`, `os.replace`, schema and candidate-order validation.

- [ ] **Step 4: Add mutually exclusive supervisor modes**

~~~python
@dataclass(frozen=True)
class V3ScheduleConfig:
    required_qualifying_full_hours: int
    maximum_collection_window_seconds: int
    schedule_path: Path

# SupervisorConfig
collection_duration_seconds: float | None
v3_schedule: V3ScheduleConfig | None = None
~~~

Require exactly one mode. In V3, sample UTC and monotonic back-to-back before child spawn, persist once, use the saved monotonic stop, require hard ceiling `>=111600 + finalization_timeout`, and record `deadline_recomputed=false`.

- [ ] **Step 5: Update CLI and collector consumption**

V3 supervisor flags are exactly:

~~~text
--required-qualifying-full-hours 30
--maximum-collection-window-seconds 111600
--qualification-schedule-path /run/bitcoin-trader/v3/qualification-schedule.json
~~~

The transient launcher requires those flags once and forbids `--collection-duration-seconds` for V3. The collector loads the schedule and computes `max(0.0, collection_stop_monotonic - time.monotonic())` once; it never uses later wall time to move the stop.

- [ ] **Step 6: Verify and commit**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_qualification_schedule.py tests/test_bounded_supervisor.py tests/test_transient_launch.py tests/test_short_smoke_runtime_config.py -q
git diff --check
git add src/bithumb_coin_trader/qualification_schedule.py src/bithumb_coin_trader/bounded_supervisor.py scripts/run_bounded_short_smoke.py scripts/launch_short_smoke_transient.py scripts/run_cross_market_collector.py tests/test_qualification_schedule.py tests/test_bounded_supervisor.py tests/test_transient_launch.py
git commit -m "feat: add V3 qualification schedule"
~~~

Expected: exact-boundary V3 takes 111600 seconds, arbitrary starts stay inside the cap, and legacy modes remain green.

---

### Task 3: Durable Incremental Finalization and WAL Recovery

**Files:**
- Create: `src/bithumb_coin_trader/incremental_finalizer.py`
- Create: `tests/test_incremental_finalizer.py`
- Modify: `src/bithumb_coin_trader/microstructure_storage.py`
- Modify: `tests/test_cross_market_collector.py`
- Modify: `tests/test_pre_soak_archive.py`

**Interfaces:**
- Consumes: `FinalizationIdentity`, pending RAW path, schema-v5 manifest, and terminal schema-v3 RAW receipt binding.
- Produces: `FinalizationProgressStore` and `IncrementalManifestFinalizer.finalize_pending()`.

- [ ] **Step 1: Write failing lifecycle/recovery tests**

~~~python
def test_pending_allows_null_bindings(tmp_path: Path) -> None:
    store = FinalizationProgressStore(tmp_path / "finalization-progress")
    entry = store.register_pending(identity(
        "2026-09-14_12",
        "raw/2026-09-14/bithumb/trade/bithumb_trade_krw-btc_2026-09-14_12.jsonl",
    ))
    assert entry.state is FinalizationState.PENDING
    assert entry.source_sha256 is None
    assert entry.manifest_file_sha256 is None
    assert entry.receipt_file_sha256 is None


@pytest.mark.parametrize("crash_after", ["intent", "entry", "pending", "summary"])
def test_reconcile_every_transaction_boundary(tmp_path: Path, crash_after: str) -> None:
    store = fault_injected_store(tmp_path, crash_after)
    entry = store.register_pending(identity(
        "2026-09-14_12",
        "raw/2026-09-14/bithumb/trade/bithumb_trade_krw-btc_2026-09-14_12.jsonl",
    ))
    with pytest.raises(SimulatedCrash):
        store.mark_recomputed(entry.entry_id, recomputed_binding())
    recovered = FinalizationProgressStore(tmp_path / "finalization-progress")
    recovered.reconcile()
    assert recovered.summary().total_count == 1
~~~

Add terminal-left-pending, newly written pending omitted from cache, idempotent re-entry, missing target bindings, contradictory receipt without overwrite, unexplained generation jump, and neither-hash match.

- [ ] **Step 2: Verify RED**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_incremental_finalizer.py -q
~~~

Expected: import failure.

- [ ] **Step 3: Implement state and binding models**

~~~python
class FinalizationState(str, Enum):
    PENDING = "PENDING"
    REUSED = "REUSED"
    RECOMPUTED = "RECOMPUTED"
    FAILED = "FAILED"


class FinalizationEvidenceError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class FinalizationIdentity:
    environment_id: str
    collector_epoch: str
    collector_run_id: str
    cohort: str
    exchange: str
    stream: str
    market: str
    feed_identity: str
    raw_relative_path: str

    @property
    def entry_id(self) -> str:
        return canonical_sha256(asdict(self))


@dataclass(frozen=True)
class ArtifactBinding:
    source_size: int
    source_sha256: str
    source_record_count: int
    manifest_relative_path: str
    manifest_file_sha256: str
    receipt_relative_path: str | None = None
    receipt_file_sha256: str | None = None
    receipt_state: str | None = None
    artifact_kind: str = "RAW_DATA"


@dataclass(frozen=True)
class FinalizationEntry:
    schema_version: int
    entry_id: str
    identity: FinalizationIdentity
    state: FinalizationState
    created_at_utc: str
    started_at_utc: str | None
    completed_at_utc: str | None
    source_size: int | None
    source_sha256: str | None
    source_record_count: int | None
    manifest_relative_path: str | None
    manifest_file_sha256: str | None
    receipt_relative_path: str | None
    receipt_file_sha256: str | None
    receipt_state: str | None
    artifact_kind: str | None
    failure_reason_code: str | None
    entry_sha256: str


@dataclass(frozen=True)
class FinalizationSummary:
    schema_version: int
    state: str
    generation: int
    total_count: int
    pending_count: int
    reused_count: int
    recomputed_count: int
    failed_count: int
    historical_raw_files_opened: int
    historical_raw_bytes_read: int
    last_applied_transaction_id: str | None
~~~

`RECOMPUTED` requires source/manifest; `REUSED` also requires terminal receipt and `RAW_DATA`. `FAILED` retains last observations and one stable reason code. `FinalizationEntry.to_dict()` flattens the `identity` fields into the persisted entry object so every required identity is directly inspectable on disk.

- [ ] **Step 4: Implement the single-intent transaction**

~~~text
FinalizationProgressStore.reconcile() -> FinalizationSummary
FinalizationProgressStore.register_pending(identity: FinalizationIdentity) -> FinalizationEntry
FinalizationProgressStore.mark_recomputed(entry_id: str, binding: ArtifactBinding) -> FinalizationEntry
FinalizationProgressStore.mark_reused(entry_id: str, binding: ArtifactBinding) -> FinalizationEntry
FinalizationProgressStore.mark_failed(entry_id: str, reason_code: str) -> FinalizationEntry
FinalizationProgressStore.pending_entries() -> Sequence[FinalizationEntry]
FinalizationProgressStore.summary() -> FinalizationSummary
~~~

Each mutation locks `.lock`, reconciles, writes intent, then entry, pending, summary, then removes intent and fsyncs the directory. Intent stores before/after hashes/states/membership/counters/generations/transaction ID. `ABSENT` exists only in registration intent. Recovery reads only the referenced entry and raises `FINALIZATION_PROGRESS_CORRUPT` on unexplained state.

- [ ] **Step 5: Add dirty-tail finalization and manifest v5 identity**

~~~python
class IncrementalManifestFinalizer:
    def __init__(
        self,
        store: FinalizationProgressStore,
        storage: RawMicrostructureStorage,
        receipt_root: Path,
    ) -> None:
        self.store = store
        self.storage = storage
        self.receipt_root = receipt_root

    def finalize_pending(self) -> FinalizationSummary:
        for entry in self.store.pending_entries():
            try:
                terminal = self._load_deterministic_terminal_receipt(entry)
                if terminal is not None:
                    self.store.mark_reused(entry.entry_id, self._validate_reuse(entry, terminal))
                    continue
                raw_path = self.storage.resolve_raw(entry.identity.raw_relative_path)
                self._require_stable_owned_source(entry, raw_path)
                manifest = self.storage.generate_partition_manifest(raw_path)
                self.store.mark_recomputed(entry.entry_id, binding_from_manifest(manifest))
            except FinalizationEvidenceError as error:
                self.store.mark_failed(entry.entry_id, error.reason_code)
        return self.store.complete_if_terminal()
~~~

Reject path escape/symlink/unstable/identity mismatch; never enumerate RAW. Manifest schema 5 binds environment, epoch, run, cohort, feed, path, size, SHA, and atomic manifest bytes.

- [ ] **Step 6: Verify and commit**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_incremental_finalizer.py tests/test_cross_market_collector.py tests/test_pre_soak_archive.py -q
git diff --check
git add src/bithumb_coin_trader/incremental_finalizer.py src/bithumb_coin_trader/microstructure_storage.py tests/test_incremental_finalizer.py tests/test_cross_market_collector.py tests/test_pre_soak_archive.py
git commit -m "feat: add bounded incremental finalization"
~~~

Expected: all injected crash positions recover, corruption fails closed, and terminal history RAW is never opened.

---

### Task 4: Owning Sessions and Feed-Hour Coverage

**Files:**
- Create: `src/bithumb_coin_trader/session_evidence.py`
- Create: `src/bithumb_coin_trader/feed_hour_coverage.py`
- Create: `tests/test_session_evidence.py`
- Create: `tests/test_feed_hour_coverage.py`

**Interfaces:**
- Consumes: sealed feed universe, connection events, writer events, heartbeat policy, optional `DataArtifactBinding`.
- Produces: `SessionEvidenceTracker`, `FeedHourCoverageTracker`, `FrozenFeedHourObservation`, immutable `FeedHourCoverage`.

- [ ] **Step 1: Write failing common-gate tests**

~~~python
def test_reconnect_requires_new_owner_confirmation() -> None:
    tracker = SessionEvidenceTracker(epoch="epoch", run_id="run")
    first = tracker.open_session("upbit", requested_feeds(), at("11:50"))
    tracker.confirm(first, requested_feeds(), "LIST_SUBSCRIPTIONS", at("11:51"), {"result": []})
    tracker.close_session(first, at("12:20"), "socket_closed")
    second = tracker.open_session("upbit", requested_feeds(), at("12:21"))
    assert not tracker.is_confirmed(second)


def test_data_present_fails_at_31_second_gap() -> None:
    frozen = observation(event_count=10, maximum_heartbeat_gap_seconds=31)
    result = materialize_feed_hour_coverage(frozen, heartbeat_policy(), raw_binding())
    assert result.coverage_state == "FAILED"
    assert "HEARTBEAT_GAP_EXCEEDED" in result.failure_reason_codes


def test_zero_event_has_null_raw_binding_and_stable_hash() -> None:
    coverage = materialize_feed_hour_coverage(observation(event_count=0), heartbeat_policy(), None)
    assert coverage.coverage_state == "VERIFIED_ZERO_EVENT"
    assert coverage.data_artifact_binding is None
    assert coverage.evidence_sha256 == canonical_sha256(coverage.to_dict(), ("evidence_sha256",))
~~~

Cover sorted subscriptions, session chronology, 30-second acceptance, missing threshold, both edge gaps, late confirmation, connection gap, health failures, partial-hour zero, and positive count without RAW binding.

- [ ] **Step 2: Verify RED**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_session_evidence.py tests/test_feed_hour_coverage.py -q
~~~

Expected: both modules missing.

- [ ] **Step 3: Implement session ownership**

~~~python
@dataclass(frozen=True, order=True)
class FeedIdentity:
    exchange: str
    stream: str
    market: str

    @property
    def canonical(self) -> str:
        return f"{self.exchange}/{self.stream}/{self.market}"


@dataclass(frozen=True)
class HeartbeatPolicy:
    heartbeat_probe_interval_seconds: int
    heartbeat_timeout_seconds: int
    max_allowed_heartbeat_gap_seconds: Mapping[str, int]


@dataclass(frozen=True)
class WriterHealthSnapshot:
    writer_error_count: int
    queue_dropped_events: int
    unpersisted_event_count: int
    fatal_writer_error_type: str | None


@dataclass(frozen=True)
class SessionSegment:
    exchange: str
    session_id: str
    connected_at_utc: str
    disconnected_at_utc: str | None
    requested_feeds: Sequence[str]
    requested_subscription_sha256: str
    confirmation_method: str | None
    confirmed_at_utc: str | None
    confirmed_feeds: Sequence[str]
    confirmed_subscription_sha256: str | None
    response_evidence_sha256: str | None
    heartbeat_observations_utc: Sequence[str]
    maximum_heartbeat_gap_seconds: float | None
    disconnect_reason: str | None
    reconnect_successor_id: str | None
    collector_epoch: str
    collector_run_id: str


SESSION_EVIDENCE_API = (
    "open_session(exchange, requested, connected_at_utc) -> str",
    "confirm(session_id, confirmed, method, confirmed_at_utc, response) -> None",
    "record_heartbeat(session_id, observed_at_utc, kind) -> None",
    "close_session(session_id, disconnected_at_utc, reason, reconnect_successor_id=None) -> None",
    "segments_for(feed, interval_start, interval_end) -> Sequence[SessionSegment]",
)
~~~

Normalize Bithumb/Upbit markets uppercase and Binance markets lowercase before constructing `FeedIdentity`. Hash sorted unique feed strings. Confirmation is session-local; closed segments are immutable and sorted by `(connected_at_utc, session_id)`.

- [ ] **Step 4: Implement journals and common gate**

~~~text
FeedHourCoverageTracker.record_persisted_event(feed: FeedIdentity, local_write_ts: datetime) -> None
FeedHourCoverageTracker.freeze_completed(boundary_utc, sessions, health) -> Sequence[FrozenFeedHourObservation]
FeedHourCoverageTracker.freeze_shutdown(observation_end_utc, sessions, health) -> Sequence[FrozenFeedHourObservation]
materialize_feed_hour_coverage(observation, heartbeat_policy, data_binding) -> FeedHourCoverage
~~~

Use these immutable handoff types:

~~~python
@dataclass(frozen=True)
class DataArtifactBinding:
    raw_relative_path: str
    raw_size: int
    raw_sha256: str
    manifest_relative_path: str
    manifest_file_sha256: str
    manifest_record_count: int
    receipt_relative_path: str
    receipt_file_sha256: str
    receipt_source_record_count: int


@dataclass(frozen=True)
class FrozenFeedHourObservation:
    feed: FeedIdentity
    cohort_utc: str
    interval_start_utc: str
    interval_end_utc: str
    cohort_qualification: str
    observation_start_utc: str
    observation_end_utc: str
    event_count: int
    first_event_timestamp: str | None
    last_event_timestamp: str | None
    session_segments: Sequence[SessionSegment]
    disconnect_count: int
    reconnect_count: int
    health: WriterHealthSnapshot
    progress_entry_id: str | None


@dataclass(frozen=True)
class FeedHourCoverage:
    schema_version: int
    artifact_kind: str
    environment_id: str
    collector_epoch: str
    collector_run_id: str
    runtime_commit: str
    runtime_config_fingerprint: str
    cohort_utc: str
    interval_start_utc: str
    interval_end_utc: str
    cohort_qualification: str
    observation_start_utc: str
    observation_end_utc: str
    exchange: str
    stream: str
    market: str
    feed_identity: str
    configured: bool
    coverage_state: str
    event_count: int
    first_event_timestamp: str | None
    last_event_timestamp: str | None
    session_segments: Sequence[SessionSegment]
    disconnect_count: int
    reconnect_count: int
    writer_error_count: int
    queue_dropped_events: int
    unpersisted_event_count: int
    fatal_writer_error_type: str | None
    data_artifact_binding: DataArtifactBinding | None
    failure_reason_codes: Sequence[str]
    closed_at_utc: str
    evidence_sha256: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
~~~

Create every sealed slot in every touched hour, reject backward writer time, freeze once, and mark opening/abnormal-ending intersections `TOUCHED_PARTIAL`. Apply common failures first; only then select positive/zero state.

- [ ] **Step 5: Persist immutable coverage**

Write `coverage/YYYY-MM-DD_HH/exchange/stream/market.coverage.json` via private atomic replace. Reload and verify schema, identity, canonical hash, finite numbers, and exact file binding.

- [ ] **Step 6: Verify and commit**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_session_evidence.py tests/test_feed_hour_coverage.py -q
git diff --check
git add src/bithumb_coin_trader/session_evidence.py src/bithumb_coin_trader/feed_hour_coverage.py tests/test_session_evidence.py tests/test_feed_hour_coverage.py
git commit -m "feat: model exact feed hour coverage"
~~~

Expected: common completeness gates are identical for positive and zero slots.

---

### Task 5: Collector Confirmation, Heartbeats, and Writer Fences

**Files:**
- Modify: `src/bithumb_coin_trader/cross_market_collector.py`
- Modify: `scripts/run_cross_market_collector.py`
- Modify: `tests/test_cross_market_collector.py`
- Modify: `tests/test_bithumb_websocket.py`
- Modify: `tests/test_short_smoke_runtime_config.py`

**Interfaces:**
- Consumes: Tasks 2-4 schedule/progress/session/coverage APIs.
- Produces: session segments, frozen journals, pending RAW entries, post-append counts, bounded lifecycle summary.

- [ ] **Step 1: Write failing fake-socket tests**

~~~python
async def test_upbit_list_subscriptions_confirms_same_owner(fake_ws) -> None:
    fake_ws.queue_json({"method": "LIST_SUBSCRIPTIONS", "result": upbit_result(), "ticket": "t"})
    collector = configured_collector(fake_ws=fake_ws)
    await collector._confirm_upbit_subscriptions(fake_ws, "session-1", "t")
    assert json.loads(fake_ws.sent[-1]) == [
        {"ticket": "t"}, {"method": "LIST_SUBSCRIPTIONS"}, {"format": "DEFAULT"},
    ]
    assert collector.session_evidence.is_confirmed("session-1")


async def test_binance_requires_exact_list_response(fake_ws) -> None:
    fake_ws.queue_json({"result": ["btcusdt@trade"], "id": 7})
    with pytest.raises(ValueError, match="SUBSCRIPTION_SET_MISMATCH"):
        await collector._confirm_binance_subscriptions(fake_ws, "session-1", 7)


async def test_append_failure_keeps_count_zero(collector) -> None:
    with patch.object(collector.storage, "append_raw_record", side_effect=OSError("disk full")):
        await collector._writer_worker_once(queued_event())
    assert collector.coverage_tracker.event_count(feed(), cohort()) == 0
~~~

Add Bithumb 60/60 snapshot/event confirmation, reconnect invalidation, Ping timeout, frame heartbeat, hour-crossing append, queue fence, and shutdown drain-before-freeze.

- [ ] **Step 2: Verify RED**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_cross_market_collector.py tests/test_bithumb_websocket.py -q
~~~

Expected: missing confirmation/fence helpers and pre-append count failures.

- [ ] **Step 3: Implement official same-session queries**

Protocol references: [Binance official Spot WebSocket Streams](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md#listing-subscriptions), [Upbit List Subscriptions](https://global-docs.upbit.com/reference/list-subscriptions), and [Bithumb Public WebSocket snapshot change](https://apidocs.bithumb.com/v1.2.0/changelog/%EC%97%85%EB%8D%B0%EC%9D%B4%ED%8A%B8-public-websocket-snapshot-%EA%B8%B0%EB%8A%A5-%EC%A7%80%EC%9B%90-%EC%95%88%EB%82%B4). Do not substitute the authenticated Binance WebSocket API `session.subscriptions` method; this collector uses the public market-stream connection.

~~~python
def upbit_list_subscriptions_request(ticket: str) -> list[dict[str, str]]:
    return [
        {"ticket": ticket},
        {"method": "LIST_SUBSCRIPTIONS"},
        {"format": "DEFAULT"},
    ]


def binance_list_subscriptions_request(request_id: int) -> dict[str, str | int]:
    return {"method": "LIST_SUBSCRIPTIONS", "id": request_id}
~~~

Normalize returned types/codes or stream names to the sealed feed set and demand exact equality. Bithumb confirms each requested feed only after that socket yields matching `stream_type=SNAPSHOT` or a valid event. Reconnect closes the old segment and repeats one subscription request.

- [ ] **Step 4: Implement explicit liveness**

~~~python
async def _heartbeat_loop(self, ws, exchange: str, session_id: str) -> None:
    while self.is_running:
        await asyncio.sleep(10)
        waiter = await ws.ping()
        await asyncio.wait_for(waiter, timeout=10)
        self.session_evidence.record_heartbeat(session_id, self._utc_now(), "PING_PONG")
~~~

Disable library auto-ping for evidence sessions. Record valid frames as `FRAME`. Timeout closes the session with `HEARTBEAT_TIMEOUT` and invalidates both qualifying states for affected slots.

- [ ] **Step 5: Serialize append and closure**

Before first append, register `PENDING`. Capture one UTC `write_ts`, append with it, and only on return record the event. Process a writer-internal boundary fence before any new-hour append, freeze the old 76 journals, and hand off the frozen file. Shutdown stops producers, drains, disables writes, freezes full/partial tails, then calls `finalize_pending()`.

Replace `generate_all_manifests()` in the runner. `final_manifest_flush_observed=true` requires complete summary, zero pending/failed, and no collector error. Persist all six finalization counters.

- [ ] **Step 6: Verify and commit**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_cross_market_collector.py tests/test_bithumb_websocket.py tests/test_short_smoke_runtime_config.py tests/test_incremental_finalizer.py tests/test_session_evidence.py tests/test_feed_hour_coverage.py -q
git diff --check
git add src/bithumb_coin_trader/cross_market_collector.py scripts/run_cross_market_collector.py tests/test_cross_market_collector.py tests/test_bithumb_websocket.py tests/test_short_smoke_runtime_config.py
git commit -m "feat: capture collector session completeness"
~~~

Expected: RAW and coverage share timestamp domain; failed append never counts; reconnect creates a new proof domain.

---

### Task 6: Generic Immutable Archive and Receipt v3

**Files:**
- Modify: `src/bithumb_coin_trader/pre_soak_archive.py`
- Modify: `scripts/manage_pre_soak_archive.py`
- Modify: `tests/test_pre_soak_archive.py`
- Modify: `tests/test_s3_archive_store.py`
- Modify: `tests/test_archive_ownership.py`
- Modify: `tests/test_archive_concurrency.py`

**Interfaces:**
- Consumes: `ImmutableArtifact` for `RAW_DATA` or `COVERAGE_EVIDENCE`.
- Produces: `ArchiveReceiptV3`, read-only `adapt_legacy_v2_receipt`, shared archive pipeline.

- [ ] **Step 1: Write failing kind/legacy tests**

~~~python
def test_raw_v3_requires_manifest(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="RAW_MANIFEST_BINDING_REQUIRED"):
        pipeline(tmp_path).finalize_artifact(raw_artifact(tmp_path, manifest_sha256=None))


def test_coverage_v3_has_no_record_count(tmp_path: Path) -> None:
    receipt = pipeline(tmp_path).finalize_artifact(coverage_artifact(tmp_path))
    assert receipt.artifact_kind == "COVERAGE_EVIDENCE"
    assert receipt.source_record_count is None
    assert receipt.restore_verified_at is not None


def test_v2_adapter_does_not_rewrite(v2_path: Path) -> None:
    before = v2_path.read_bytes()
    normalized = adapt_legacy_v2_receipt(json.loads(before))
    assert normalized.artifact_kind == "RAW_DATA"
    assert v2_path.read_bytes() == before
~~~

Cover wrong kind, path escape, manifest/coverage hash mismatch, remote/restore mismatch, idempotent reuse, and v2 byte preservation.

- [ ] **Step 2: Verify RED**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_pre_soak_archive.py tests/test_s3_archive_store.py tests/test_archive_ownership.py tests/test_archive_concurrency.py -q
~~~

Expected: descriptor/v3 APIs missing.

- [ ] **Step 3: Add neutral types**

~~~python
class ArtifactKind(str, Enum):
    RAW_DATA = "RAW_DATA"
    COVERAGE_EVIDENCE = "COVERAGE_EVIDENCE"


@dataclass(frozen=True)
class ImmutableArtifact:
    kind: ArtifactKind
    source_path: Path
    relative_path: str
    environment_id: str
    collector_epoch: str
    collector_run_id: str
    cohort: str
    exchange: str
    stream: str
    market: str
    source_sha256: str
    source_size: int
    source_record_count: int | None
    manifest_path: Path | None
    manifest_sha256: str | None


@dataclass
class ArchiveReceiptV3:
    schema_version: int
    artifact_kind: str
    state: str
    environment_id: str
    run_id: str
    collector_epoch: str
    cohort: str
    exchange: str
    stream: str
    market: str
    source_path: str
    source_size: int
    source_sha256: str
    source_record_count: int | None
    manifest_path: str | None
    manifest_sha256: str | None
    compressed_size: int | None
    compressed_sha256: str | None
    compression_algorithm: str
    compression_level: int
    compression_version: str
    remote_key: str | None
    remote_size: int | None
    remote_checksum: str | None
    remote_version_id: str | None
    source_verified_at: str | None
    compressed_verified_at: str | None
    remote_verified_at: str | None
    restore_verified_at: str | None
    cleanup_eligible: bool
    cleanup_completed_at: str | None
    failure_stage: str | None
    failure_reason: str | None
~~~

`ArchiveReceiptV3` uses `source_path/size/sha256/record_count` plus manifest binding, existing compressed/remote/restore fields, and `artifact_kind`.

- [ ] **Step 4: Refactor kind-specific verification only**

Rename public work to `finalize_artifact(artifact, cleanup_verified=False, now=None, grace_period=timedelta(minutes=10), active_paths=(), stability_wait_seconds=1.0)`. `_verify_source` dispatches RAW manifest/count checks or coverage canonical-hash checks, then reuses compression, immutable upload/reuse, remote HEAD checksum, streamed restore, TOCTOU, and cleanup. Keep a legacy wrapper until Task 7 migrates callers.

- [ ] **Step 5: Verify and commit**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_pre_soak_archive.py tests/test_s3_archive_store.py tests/test_archive_ownership.py tests/test_archive_concurrency.py -q
git diff --check
git add src/bithumb_coin_trader/pre_soak_archive.py scripts/manage_pre_soak_archive.py tests/test_pre_soak_archive.py tests/test_s3_archive_store.py tests/test_archive_ownership.py tests/test_archive_concurrency.py
git commit -m "refactor: generalize immutable artifact archive"
~~~

Expected: both kinds share the terminal restore path and v2 bytes remain unchanged.

---

### Task 7: Ordered Closed-Hour Finalization and Scheduler Integration

**Files:**
- Create: `src/bithumb_coin_trader/closed_hour_finalizer.py`
- Create: `tests/test_closed_hour_finalizer.py`
- Modify: `src/bithumb_coin_trader/archive_scheduler.py`
- Modify: `scripts/orchestrate_closed_hour_archive.py`
- Modify: `tests/test_archive_scheduler.py`
- Modify: `tests/test_full_scan_supervision.py`

**Interfaces:**
- Consumes: frozen 76-slot journal, progress entries, coverage materializer, generic archive.
- Produces: RAW terminal receipts before positive coverage and coverage receipts for all slots.

- [ ] **Step 1: Write failing order tests**

~~~python
def test_positive_order(tmp_path: Path) -> None:
    calls: list[str] = []
    result = finalizer(tmp_path, calls).finalize_slot(positive_observation())
    assert calls == [
        "manifest_raw", "archive_raw", "verify_raw_restore",
        "materialize_data_present", "archive_coverage", "verify_coverage_restore",
    ]
    assert result.coverage.coverage_state == "DATA_PRESENT"


def test_zero_skips_raw(tmp_path: Path) -> None:
    calls: list[str] = []
    result = finalizer(tmp_path, calls).finalize_slot(zero_observation())
    assert calls == [
        "materialize_verified_zero_event", "archive_coverage", "verify_coverage_restore",
    ]
    assert result.raw_receipt is None


def test_count_mismatch_fails(tmp_path: Path) -> None:
    result = finalizer(tmp_path, []).finalize_slot(
        positive_observation(event_count=10), manifest_count=9,
    )
    assert "RECORD_COUNT_MISMATCH" in result.coverage.failure_reason_codes
~~~

Add 76 mixed slots, RAW-trigger independence, archive failure, restored binding, `REUSED` transition, restart idempotency, missing/duplicate/foreign journal slot.

- [ ] **Step 2: Verify RED**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_closed_hour_finalizer.py tests/test_archive_scheduler.py -q
~~~

Expected: new module absent and old RAW-discovery behavior exposed.

- [ ] **Step 3: Implement one-slot state machine**

~~~python
@dataclass(frozen=True)
class ClosedSlotResult:
    coverage: FeedHourCoverage
    coverage_receipt: ArchiveReceiptV3 | None
    raw_receipt: ArchiveReceiptV3 | None
    failure_reason_codes: Sequence[str]


class ClosedHourFinalizer:
    def finalize_slot(self, observation: FrozenFeedHourObservation) -> ClosedSlotResult:
        failures = evaluate_common_gate(observation, self.heartbeat_policy)
        if failures:
            return self._persist_failed(observation, failures)
        if observation.event_count == 0:
            coverage = materialize_feed_hour_coverage(observation, self.heartbeat_policy, None)
            return self._archive_coverage(coverage, raw_receipt=None)
        manifest = self._manifest_for_registered_raw(observation)
        raw_receipt = self.raw_archive.finalize_artifact(raw_descriptor(manifest))
        self._require_terminal_restore(raw_receipt)
        self._require_equal_counts(
            observation.event_count, manifest.record_count, raw_receipt.source_record_count,
        )
        self.progress.mark_reused(observation.progress_entry_id, binding_from(raw_receipt, manifest))
        coverage = materialize_feed_hour_coverage(
            observation, self.heartbeat_policy, data_binding_from(manifest, raw_receipt),
        )
        return self._archive_coverage(coverage, raw_receipt)
~~~

`finalize_cohort` requires exactly 76 sealed identities before processing. Any failure fails the cohort but still emits durable diagnostic coverage.

- [ ] **Step 4: Make V3 scheduler journal-driven**

Discover append-closed frozen journals rather than non-empty RAW count. For V3, the durable writer fence plus empty active-path check is the closure authority, so do not add the legacy 600-second time grace; retain that grace only in the explicit legacy branch. The orchestrator loads the exact map, builds RAW/coverage pipelines, and delegates. Remove V3's old missing-manifest RAW-tree loop. V3 dry-run lists ordered actions without upload/terminal receipt/progress mutation.

- [ ] **Step 5: Verify and commit**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_closed_hour_finalizer.py tests/test_archive_scheduler.py tests/test_full_scan_supervision.py tests/test_pre_soak_archive.py -q
git diff --check
git add src/bithumb_coin_trader/closed_hour_finalizer.py src/bithumb_coin_trader/archive_scheduler.py scripts/orchestrate_closed_hour_archive.py tests/test_closed_hour_finalizer.py tests/test_archive_scheduler.py tests/test_full_scan_supervision.py
git commit -m "feat: finalize ordered V3 coverage chains"
~~~

Expected: positive coverage cannot precede RAW restore; zero never touches RAW; cohort result covers 76 slots.

---

### Task 8: V3 Contract, Epoch Manifest, and Exact-Slot Audit

**Files:**
- Modify: `scripts/compose_epoch_contract.py`
- Modify: `scripts/build_epoch_manifest.py`
- Modify: `scripts/audit_72h_soak.py`
- Modify: `tests/test_archive_audit_coverage.py`
- Modify: `tests/test_phase6_3_contract.py`
- Modify: `tests/test_phase6_runbook_e2e.py`
- Modify: `tests/test_post72h_remediation_regressions.py`

**Interfaces:**
- Consumes: normalized start, schedule/policy seal, coverage objects/receipts, state-dependent RAW evidence.
- Produces: V3 contract schema 2, coverage index, `validate_v3_coverage_evidence` diagnostics.

- [ ] **Step 1: Write failing exact-slot scenarios**

~~~python
@pytest.mark.parametrize("present,zero,failed,status", [
    (76, 0, 0, "PASS"), (74, 2, 0, "PASS"),
    (74, 1, 0, "FAIL"), (75, 0, 1, "FAIL"),
])
def test_verdict_uses_coverage_slots(present, zero, failed, status, tmp_path):
    result = validate_v3_coverage_evidence(**v3_bundle(tmp_path, present, zero, failed))
    assert result["status"] == status


def test_positive_data_still_requires_early_subscription(tmp_path: Path) -> None:
    result = validate_v3_coverage_evidence(
        **one_positive_bundle(tmp_path, confirmation="2026-09-14T12:00:01Z")
    )
    assert "SUBSCRIPTION_NOT_CONFIRMED_BEFORE_INTERVAL" in result["blockers"]


def test_zero_does_not_add_market_records(tmp_path: Path) -> None:
    result = validate_v3_coverage_evidence(**one_zero_bundle(tmp_path))
    assert result["scientific_record_count"] == 0
~~~

Add duplicate/foreign/unknown/missing slot, receipt/restore/hash/count mismatch, every common positive gate, heartbeat boundary, and no replacement cohort.

- [ ] **Step 2: Verify RED**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_archive_audit_coverage.py tests/test_phase6_3_contract.py tests/test_phase6_runbook_e2e.py tests/test_post72h_remediation_regressions.py -q
~~~

Expected: current auditor's 76-RAW/152-input assumptions fail the V3 scenarios.

- [ ] **Step 3: Emit explicit V3 schema 2**

~~~python
v3_contract = {
    "schema_version": 2,
    "contract_type": "OFFICIAL_30H_V3_COVERAGE_CONTRACT",
    "collector_epoch": collector_epoch,
    "collector_run_id": collector_run_id,
    "actual_start_time_utc": normalized.actual_start_time_utc,
    "qualification_start_utc": schedule.qualification_start_utc,
    "qualification_end_utc": schedule.collection_stop_utc,
    "required_qualifying_full_hours": 30,
    "maximum_collection_window_seconds": 111600,
    "candidate_cohorts": list(schedule.candidate_cohorts),
    "expected_coverage_slots_per_cohort": 76,
    "heartbeat_policy": sealed_heartbeat_policy,
    "feed_universe": feed_universe,
    "require_coverage_receipts": True,
    "require_state_dependent_fullscan": True,
}
v3_contract["contract_sha256"] = canonical_sha256(v3_contract)
~~~

Reject wrong order/count/thresholds and a 108000-derived end. Preserve schema 1 for historical inputs.

- [ ] **Step 4: Build/audit the coverage map first**

~~~text
validate_v3_coverage_evidence(
    contract: Mapping[str, object],
    coverage_files: Sequence[Path],
    receipt_files: Sequence[Path],
    full_scan_reports: Sequence[Path],
) -> dict[str, Any]
~~~

Key by cohort/exchange/stream/market, reject duplicates/foreign/missing, verify coverage canonical/file hash and receipt, then predicates. Positive slots require RAW manifest/receipt/restore/fullscan and count equality. Zero slots require no RAW and coverage scan only. Keep coverage counts separate from scientific records.

`build_epoch_manifest.py` records 30×76 coverage index, state counts, receipt hashes, positive RAW bindings, and normalized start hashes without synthetic zero RAW.

- [ ] **Step 5: Keep legacy behavior explicitly selected**

Schema-1 contracts use existing `validate_archive_evidence_coverage`. Only schema-2 V3 contracts use the new validator. A v2 receipt cannot satisfy V3 and V3 artifacts cannot alter V2 decisions.

- [ ] **Step 6: Verify and commit**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_archive_audit_coverage.py tests/test_phase6_3_contract.py tests/test_phase6_runbook_e2e.py tests/test_post72h_remediation_regressions.py tests/test_closed_hour_finalizer.py -q
git diff --check
git add scripts/compose_epoch_contract.py scripts/build_epoch_manifest.py scripts/audit_72h_soak.py tests/test_archive_audit_coverage.py tests/test_phase6_3_contract.py tests/test_phase6_runbook_e2e.py tests/test_post72h_remediation_regressions.py
git commit -m "feat: audit exact V3 coverage slots"
~~~

Expected: 74 positive + 2 proven zero passes; any missing/failed/unproven slot fails; V2 tests retain meaning.

---

### Task 9: Bounded Scale and Cross-Layer Gate

**Files:**
- Create: `scripts/benchmark_incremental_finalization.py`
- Create: `tests/test_incremental_finalization_scale.py`
- Modify: `tests/test_phase5_synthetic_e2e.py`
- Modify: `tests/test_phase6_crosslayer_regressions.py`
- Modify: `tests/test_72h_property_invariants.py`

**Interfaces:**
- Consumes: Tasks 1-8.
- Produces: deterministic scale report and regression evidence used later to set the V3 finalization ceiling.

- [ ] **Step 1: Write the failing history-independence test**

~~~python
@pytest.mark.parametrize("history", [1, 10, 30])
def test_only_identical_dirty_tail_is_opened(tmp_path: Path, history: int) -> None:
    fixture = build_progress_history(tmp_path, terminal_cohorts=history, dirty_tail=2)
    with count_raw_opens() as opened:
        summary = fixture.finalizer.finalize_pending()
    assert summary.recomputed_count == 2
    assert summary.historical_raw_files_opened == 0
    assert summary.historical_raw_bytes_read == 0
    assert opened.paths == set(fixture.dirty_tail_paths)


def test_slow_cost_depends_only_on_dirty_tail(tmp_path: Path) -> None:
    samples = run_scale_matrix(tmp_path, (1, 10, 30), dirty_tail=2, delay=0.05)
    assert {sample.recomputed_count for sample in samples} == {2}
    assert max(s.elapsed_seconds for s in samples) - min(s.elapsed_seconds for s in samples) < 0.10
~~~

- [ ] **Step 2: Verify RED and implement benchmark**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_incremental_finalization_scale.py -q
~~~

Expected: scale helpers/report absent.

The script runs at least five repetitions per history, records samples and p50/p95/p99 plus all counters, writes its explicit output only to this plan's SDD workspace, and exits nonzero unless all histories have identical dirty work and zero historical reads.

- [ ] **Step 3: Run focused V3 matrix**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_actual_start_evidence.py tests/test_qualification_schedule.py tests/test_incremental_finalizer.py tests/test_incremental_finalization_scale.py tests/test_session_evidence.py tests/test_feed_hour_coverage.py tests/test_closed_hour_finalizer.py tests/test_cross_market_collector.py tests/test_pre_soak_archive.py tests/test_archive_scheduler.py tests/test_archive_audit_coverage.py -q
~~~

Expected: all pass.

- [ ] **Step 4: Produce local deterministic scale evidence**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python scripts/benchmark_incremental_finalization.py --repetitions 7 --output .superpowers/sdd/2026-09-14-aws-30h-v3-remediation/aws-30h-v3-finalization-scale.json
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m json.tool .superpowers/sdd/2026-09-14-aws-30h-v3-remediation/aws-30h-v3-finalization-scale.json >/dev/null
~~~

Expected: `PASS` and zero historical reads at 1/10/30. Record p95/p99 in `.superpowers/sdd/2026-09-14-aws-30h-v3-remediation/aws-30h-v3-finalization-scale.json`; change no timeout here. The benchmark neither reads nor writes `test-results/`.

- [ ] **Step 5: Run cross-layer tests and commit**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest tests/test_phase5_synthetic_e2e.py tests/test_phase6_crosslayer_regressions.py tests/test_72h_property_invariants.py tests/test_post72h_remediation_regressions.py -q
git diff --check
git add scripts/benchmark_incremental_finalization.py tests/test_incremental_finalization_scale.py tests/test_phase5_synthetic_e2e.py tests/test_phase6_crosslayer_regressions.py tests/test_72h_property_invariants.py
git commit -m "test: prove bounded V3 finalization"
~~~

Expected: green; the benchmark report remains only in this plan's git-ignored SDD workspace. Existing user-owned `test-results/` is not read, written, moved, deleted, staged, or committed.

---

### Task 10: Full Verification, Independent Review, Regular Merge, and Preparation Handoff

**Files:**
- Modify only when a gate exposes a defect: the smallest owning source/test pair above.
- Create in a later planning task: `docs/superpowers/plans/2026-09-14-aws-30h-v3-preparation.md`, bound to the actual merge SHA and measured ceiling.

**Interfaces:**
- Consumes: complete remediation branch and scale report.
- Produces: zero-finding review, regular merge, post-merge gates, exact downstream inputs.

- [ ] **Step 1: Run static/syntax/JSON/diff gates**

~~~bash
git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.py' | tr '\n' '\0' | xargs -0 npx --yes pyright@1.1.414
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m compileall -q src scripts tests
find tests/fixtures -name '*.json' -print0 | xargs -0 -n1 /Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m json.tool >/dev/null
git diff --check origin/main...HEAD
git status --short --branch
~~~

Expected: Pyright 1.1.414 reports 0 errors/0 warnings for changed Python paths; every command exits 0; only intended changes are tracked. Retain the measured unchanged full-repository baseline of 550 errors as out-of-scope evidence rather than labeling this a repo-wide pass.

- [ ] **Step 2: Run the full suite twice unchanged**

~~~bash
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest -q
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest -q
~~~

Expected: both pass. If the known supervisor signal test flakes, retain output and run that exact test 20 times; correlate before changing code.

- [ ] **Step 3: Review every invariant**

Review `git diff origin/main...HEAD` and require evidence for:

~~~text
Root C: canonical v2 plus exact V2 legacy v1, no alias heuristics
Root A: bounded pending index, WAL reconciliation, zero historical reads
Root B: exactly 30 candidate hours times exactly 76 slots
Common gate: session/subscription/heartbeat/writer health for both states
Order: RAW terminal restore before DATA_PRESENT coverage
Zero path: coverage only, no RAW
Compatibility: V2 evidence and receipt bytes unchanged
Safety: no AWS/IAM/Terraform/runtime/trading side effect
~~~

Use the execution mode's review workflow. Do not proceed with any Critical/Important finding.

- [ ] **Step 4: Correct accepted findings and repeat gates**

~~~bash
git add src/bithumb_coin_trader scripts tests
git commit -m "fix: close V3 remediation review findings"
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest -q
/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m compileall -q src scripts tests
git diff --check origin/main...HEAD
~~~

Expected: Critical 0, Important 0. If no correction exists, create no commit.

- [ ] **Step 5: Push, create PR, and regular-merge**

~~~bash
git push -u origin codex/aws-30h-v3-remediation-20260914
gh pr create --base main --head codex/aws-30h-v3-remediation-20260914 --title "Remediate AWS 30H V3 evidence architecture" --body $'V2 remains immutable FAIL.\n\nThis PR implements strict actual-start normalization, bounded WAL-backed finalization, exact 76-slot full-hour coverage, common completeness gates, and RAW-receipt-before-coverage ordering.\n\nFocused, scale, full-suite, compileall, changed-file Pyright, JSON, and diff gates passed. Independent review: Critical 0 / Important 0.\n\nNo AWS, IAM, Terraform, systemd, collection, or trading action is authorized by this PR.'
gh pr checks --watch
gh pr merge --merge --delete-branch=false
~~~

PR body records V2 immutable failure, three roots, focused/scale/full/static evidence, review result, and launch prohibition. Do not squash/rebase.

- [ ] **Step 6: Re-sync authoritative main and verify**

~~~bash
git -C /Users/macintosh/Documents/ChatGPT/bitcoin-trader fetch origin
git -C /Users/macintosh/Documents/ChatGPT/bitcoin-trader merge --ff-only origin/main
git -C /Users/macintosh/Documents/ChatGPT/bitcoin-trader rev-parse HEAD
git -C /Users/macintosh/Documents/ChatGPT/bitcoin-trader status --short --branch
~~~

Then run `/Users/macintosh/Documents/ChatGPT/bitcoin-trader/.venv/bin/python -m pytest -q` with working directory `/Users/macintosh/Documents/ChatGPT/bitcoin-trader`. Preserve `/Users/macintosh/Documents/ChatGPT/bitcoin-trader/test-results/`. Expected: local/remote main equal the regular merge SHA; full suite green; only pre-existing user artifacts remain.

- [ ] **Step 7: Write the separate V3 preparation plan from known facts**

After merge only, compute a fresh V3 epoch/run/namespace from authoritative main and derive finalization ceiling from Task 9 p95/p99 plus documented margin. That plan creates fresh seals/provenance with `launch_authorized=false` and `actual_start_time_utc=null`, performs read-only AWS/Terraform/process/capacity gates, pushes an unmerged prep branch, and ends `NEW 30H STARTED=NO`. It never reuses V2 identity.

---

## Completion Evidence

All tasks must be checked; focused/scale/full/static gates must be fresh and green; review must be `Critical 0 / Important 0`; remediation must be regular-merged; main must be reverified; and downstream preparation must be planned from the resulting merge SHA and measured ceiling. This plan never authorizes or starts V3.
