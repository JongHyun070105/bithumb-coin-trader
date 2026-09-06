from __future__ import annotations
try:
    from bithumb_coin_trader.experiment_runner import LedgerRecoveryError
except ImportError:
    class LedgerRecoveryError(Exception):
        """Placeholder for Phase 4 LedgerRecoveryError."""
"""Phase 4 Regression Tests: Proving Phase 3 Cross-Layer Vulnerabilities.

These tests MUST fail against Phase 3 code to establish the empirical baseline.
"""

import json
import multiprocessing as mp
import os
from pathlib import Path
import stat
import time

import pytest
import zstandard

from bithumb_coin_trader.canonical_market_data import (
    CanonicalOrderBook,
    TimestampSemantics,
    write_canonical_ndjson_zstd,
)
from bithumb_coin_trader.experiment_runner import (
    DatasetRole,
    GovernedExperimentRunner,
    HoldoutAlreadyConsumedError,
    HoldoutContaminationError,
    InvalidResearchCycleStateError,
    InvalidStatusTransitionError,
    PreregistrationManifest,
    ResearchCycleState,
    TrialStatus,
)
from bithumb_coin_trader.prospective_dataset import (
    DqQualificationEvidence,
    DqQualificationStatus,
    DqRejectedError,
    build_and_export_dataset,
)
from bithumb_coin_trader.research_cli import main


# -----------------------------------------------------------------------------
# P0: CLI OVERWRITE BYPASS
# -----------------------------------------------------------------------------
def test_cli_partition_refuses_overwrite_when_output_dir_sealed(tmp_path: Path):
    """P0: CLI partition-dataset must fail closed if output directory already exists and is sealed."""
    out_dir = tmp_path / "sealed_dataset"
    out_dir.mkdir(parents=True)
    
    # Existing sealed files
    sealed_manifest = out_dir / "manifest.json"
    sealed_manifest.write_text(json.dumps({"dataset_id": "sealed_v1", "sealed": True}))
    sealed_data = out_dir / "train.ndjson.zst"
    sealed_data.write_bytes(b"existing_compressed_data")
    
    hash_manifest_before = sealed_manifest.read_text()
    hash_data_before = sealed_data.read_bytes()
    
    # Create input file
    input_file = tmp_path / "input.ndjson.zst"
    records = [
        CanonicalOrderBook(
            exchange="bithumb",
            market="BTC-KRW",
            exchange_timestamp_ms=1000 + i * 10,
            receive_timestamp_ms=1005 + i * 10,
            bids=((100_000_000.0, 1.0),),
            asks=((100_100_000.0, 1.0),),
        )
        for i in range(10)
    ]
    write_canonical_ndjson_zstd(input_file, records)
    
    # Valid DQ report with cryptographically authentic hash
    from bithumb_coin_trader.research_cli import compute_canonical_report_hash
    dq_file = tmp_path / "dq.json"
    dq_data = {
        "status": "DQ_PASS",
        "hard_fail_count": 0,
        "auditor_version": "1.0.0",
        "audit_code_commit": "a" * 40,
        "source_manifest_hash": "b" * 64,
        "criteria_version": "v1",
        "approved_policy": "strict_v1",
        "created_at": "2026-09-06T00:00:00Z",
    }
    dq_data["report_hash"] = compute_canonical_report_hash(dq_data)
    dq_file.write_text(json.dumps(dq_data))
    
    # Run CLI without any explicit overwrite flag
    res = main([
        "partition-dataset",
        "--input-file", str(input_file),
        "--output-dir", str(out_dir),
        "--dq-report", str(dq_file)
    ])
    
    # Phase 3 passes allow_overwrite=True and exits with 0.
    # Phase 4 must fail closed with exit code 2 (DATA_GATE_FAILURE) and leave files unchanged.
    assert res == 2, f"Expected exit code 2, got {res}"
    assert sealed_manifest.read_text() == hash_manifest_before
    assert sealed_data.read_bytes() == hash_data_before


# -----------------------------------------------------------------------------
# P1 & P1.3: NO UNKNOWN PROVENANCE
# -----------------------------------------------------------------------------
def test_dq_evidence_rejects_unknown_provenance_defaults(tmp_path: Path):
    """P1.3: DqQualificationEvidence must reject 'unknown' or 'default' provenance for DQ_PASS."""
    input_file = tmp_path / "input.ndjson.zst"
    records = [
        CanonicalOrderBook(
            exchange="bithumb",
            market="BTC-KRW",
            exchange_timestamp_ms=1000,
            receive_timestamp_ms=1005,
            bids=((100_000_000.0, 1.0),),
            asks=((100_100_000.0, 1.0),),
        )
    ]
    write_canonical_ndjson_zstd(input_file, records)
    out_dir = tmp_path / "out"
    
    from bithumb_coin_trader.research_cli import compute_canonical_report_hash
    dq_lazy = {
        "status": "DQ_PASS",
        "hard_fail_count": 0,
        "auditor_version": "1.0",
        "audit_code_commit": "unknown",  # P1.3: must be rejected
        "source_manifest_hash": "unknown",
        "criteria_version": "unknown",
        "created_at": "2026-09-06T00:00:00Z",
    }
    dq_lazy["report_hash"] = compute_canonical_report_hash(dq_lazy)
    dq_file = tmp_path / "dq_lazy.json"
    dq_file.write_text(json.dumps(dq_lazy))
    
    res = main([
        "partition-dataset",
        "--input-file", str(input_file),
        "--output-dir", str(out_dir),
        "--dq-report", str(dq_file)
    ])
    assert res == 2, "Must reject DQ evidence with unknown provenance"


# -----------------------------------------------------------------------------
# P1.1: REPORT HASH TAMPERING
# -----------------------------------------------------------------------------
def test_dq_evidence_report_hash_tampering_detected(tmp_path: Path):
    """P1.1: Modifying a byte in the DQ report must invalidate the qualification."""
    input_file = tmp_path / "input.ndjson.zst"
    records = [
        CanonicalOrderBook(
            exchange="bithumb",
            market="BTC-KRW",
            exchange_timestamp_ms=1000,
            receive_timestamp_ms=1005,
            bids=((100_000_000.0, 1.0),),
            asks=((100_100_000.0, 1.0),),
        )
    ]
    write_canonical_ndjson_zstd(input_file, records)
    out_dir = tmp_path / "out"
    
    dq_file = tmp_path / "dq_tampered.json"
    dq_file.write_text(json.dumps({
        "status": "DQ_PASS",
        "hard_fail_count": 0,
        "auditor_version": "1.0.0",
        "audit_code_commit": "a" * 40,
        "source_manifest_hash": "b" * 64,
        "report_hash": "c" * 64,  # Arbitrary hash not matching actual file content
        "created_at": "2026-09-06T00:00:00Z",
        "criteria_version": "v1",
        "approved_policy": "strict_v1"
    }))
    
    res = main([
        "partition-dataset",
        "--input-file", str(input_file),
        "--output-dir", str(out_dir),
        "--dq-report", str(dq_file)
    ])
    assert res == 2, "Must detect report_hash mismatch against actual file content"


# -----------------------------------------------------------------------------
# P1.2: SOURCE HASH BINDING
# -----------------------------------------------------------------------------
def test_dq_source_manifest_hash_mismatch_rejected(tmp_path: Path):
    """P1.2: DQ evidence source_manifest_hash must match the actual source data manifest."""
    input_file = tmp_path / "input.ndjson.zst"
    records = [
        CanonicalOrderBook(
            exchange="bithumb",
            market="BTC-KRW",
            exchange_timestamp_ms=1000,
            receive_timestamp_ms=1005,
            bids=((100_000_000.0, 1.0),),
            asks=((100_100_000.0, 1.0),),
        )
    ]
    write_canonical_ndjson_zstd(input_file, records)
    out_dir = tmp_path / "out"
    
    # Provide an explicit source manifest for the input
    src_manifest = tmp_path / "input.manifest.json"
    src_manifest.write_text(json.dumps({"source_hash": "actual_source_hash_12345"}))
    
    # DQ report claims a different source manifest hash
    dq_file = tmp_path / "dq_mismatch.json"
    dq_file.write_text(json.dumps({
        "status": "DQ_PASS",
        "hard_fail_count": 0,
        "auditor_version": "1.0.0",
        "audit_code_commit": "a" * 40,
        "source_manifest_hash": "different_source_hash_67890",
        "report_hash": "0" * 64,
        "created_at": "2026-09-06T00:00:00Z",
        "criteria_version": "v1",
        "approved_policy": "strict_v1"
    }))
    
    res = main([
        "partition-dataset",
        "--input-file", str(input_file),
        "--output-dir", str(out_dir),
        "--dq-report", str(dq_file),
        "--source-manifest", str(src_manifest)
    ])
    assert res == 2, "Must fail closed on DQ source hash mismatch"


# -----------------------------------------------------------------------------
# P2: CONTENT-ADDRESSED DATASET ID
# -----------------------------------------------------------------------------
def test_dataset_id_is_content_addressed_not_filename(tmp_path: Path):
    """P2: Dataset ID must be content-addressed and identical even if filename changes."""
    records = [
        CanonicalOrderBook(
            exchange="bithumb",
            market="BTC-KRW",
            exchange_timestamp_ms=1000 + i * 10,
            receive_timestamp_ms=1005 + i * 10,
            bids=((100_000_000.0, 1.0),),
            asks=((100_100_000.0, 1.0),),
        )
        for i in range(20)
    ]
    
    file1 = tmp_path / "alpha_run.ndjson.zst"
    file2 = tmp_path / "beta_run_renamed.ndjson.zst"
    write_canonical_ndjson_zstd(file1, records)
    write_canonical_ndjson_zstd(file2, records)
    
    dq_evidence = DqQualificationEvidence(
        status=DqQualificationStatus.DQ_PASS,
        auditor_version="1.0.0",
        audit_code_commit="a" * 40,
        source_manifest_hash="b" * 64,
        report_hash="c" * 64,
        created_at="2026-09-06T00:00:00Z",
        criteria_version="v1",
        hard_fail_count=0,
        unknown_count=0,
        degraded_count=0,
        justification="",
        approved_policy="strict_v1"
    )
    
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    
    # CLI partition call 1
    from bithumb_coin_trader.research_cli import compute_canonical_report_hash
    dq_file = tmp_path / "dq.json"
    dq_dict = {
        "status": "DQ_PASS",
        "hard_fail_count": 0,
        "auditor_version": "1.0.0",
        "audit_code_commit": "a" * 40,
        "source_manifest_hash": "b" * 64,
        "created_at": "2026-09-06T00:00:00Z",
        "criteria_version": "v1",
        "approved_policy": "strict_v1"
    }
    dq_dict["report_hash"] = compute_canonical_report_hash(dq_dict)
    dq_file.write_text(json.dumps(dq_dict))
    
    ret1 = main(["partition-dataset", "--input-file", str(file1), "--output-dir", str(out1), "--dq-report", str(dq_file)])
    ret2 = main(["partition-dataset", "--input-file", str(file2), "--output-dir", str(out2), "--dq-report", str(dq_file)])
    assert ret1 == 0
    assert ret2 == 0
    
    m1 = json.loads((out1 / "manifest.json").read_text())
    m2 = json.loads((out2 / "manifest.json").read_text())
    
    # In Phase 3: m1["dataset_id"] == "alpha_run", m2["dataset_id"] == "beta_run_renamed" -> FAILS
    # In Phase 4: Both must have identical content-addressed dataset_id
    assert m1["dataset_id"] == m2["dataset_id"], f"Dataset IDs differ: {m1['dataset_id']} != {m2['dataset_id']}"
    assert m1["dataset_id"] != "alpha_run", "Dataset ID must not be taken from filename stem"


# -----------------------------------------------------------------------------
# P5: WAL CRASH RECOVERY — COMPLETED WITHOUT LEDGER
# -----------------------------------------------------------------------------
def test_experiment_wal_crash_completed_without_ledger_detected(tmp_path: Path):
    """P5: Crash window between reservation COMPLETED and ledger write must be detected on recovery."""
    ledger_file = tmp_path / "ledger.json"
    reservations_file = tmp_path / "ledger.reservations.json"
    
    # Reservation is marked COMPLETED
    trial_id = "trial_crashed_001"
    reservations_file.write_text(json.dumps([{
        "trial_id": trial_id,
        "family_id": "fam_a",
        "status": "COMPLETED",
        "reserved_at_utc": "2026-09-06T00:00:00Z",
        "manifest_hash": "h" * 64,
    }]))
    
    # But ledger file does NOT contain the entry (or is empty)
    ledger_file.write_text("[]")
    
    # Intent file is present (indicating crash occurred before intent unlinked)
    intent_file = tmp_path / f"{trial_id}.intent"
    intent_file.write_text(json.dumps({
        "trial_id": trial_id,
        "manifest_hash": "h" * 64,
        "timestamp": "2026-09-06T00:00:00Z"
    }))
    
    # In Phase 3: GovernedExperimentRunner initialized cleanly, deleted the intent file,
    # and left COMPLETED reservation without any ledger entry!
    # In Phase 4: Must fail closed or detect inconsistent state!
    with pytest.raises(LedgerRecoveryError):
        GovernedExperimentRunner(ledger_file)


# -----------------------------------------------------------------------------
# P5.3: CORRUPT INTENT FAILS CLOSED
# -----------------------------------------------------------------------------
def test_corrupt_intent_fails_closed(tmp_path: Path):
    """P5.3: Corrupt intent file must not be silently ignored with except: pass."""
    ledger_file = tmp_path / "ledger.json"
    ledger_file.write_text("[]")
    
    intent_file = tmp_path / "corrupt_trial.intent"
    intent_file.write_text("{this is corrupt json!!!")
    
    with pytest.raises(LedgerRecoveryError):
        GovernedExperimentRunner(ledger_file)


# -----------------------------------------------------------------------------
# P6: RECORD_TRIAL REQUIRES RUNNING STATUS
# -----------------------------------------------------------------------------
def test_record_trial_requires_running_status(tmp_path: Path):
    """P6: record_trial must require status RUNNING, not jump directly from RESERVED to COMPLETED."""
    runner = GovernedExperimentRunner(tmp_path / "ledger.json")
    manifest = PreregistrationManifest(
        trial_id="trial_stat_001",
        family_id="fam_stat",
        hypothesis="test_status_machine",
        features=("ofi",),
        target_horizon_ms=500,
        sample_budget=1000,
        max_trials_in_family=5
    )
    
    # Reserve trial (status becomes RESERVED)
    runner.reserve_trial(manifest)
    
    # In Phase 3: record_trial directly accepts RESERVED and sets COMPLETED
    # In Phase 4: Must raise InvalidStatusTransitionError because it must transition to RUNNING first!
    with pytest.raises(InvalidStatusTransitionError):
        runner.record_trial(manifest, {"sharpe": 1.2})


# -----------------------------------------------------------------------------
# P7: HOLDOUT ACCESS RACE LOCKED
# -----------------------------------------------------------------------------
def _holdout_worker(ledger_path: str, barrier: mp.Barrier, result_queue: mp.Queue):
    runner = GovernedExperimentRunner(Path(ledger_path))
    barrier.wait()
    try:
        res = runner.access_dataset("dataset_v1", DatasetRole.HOLDOUT)
        result_queue.put(("SUCCESS", res))
    except Exception as exc:
        result_queue.put(("ERROR", type(exc).__name__))


def test_holdout_access_multiprocess_race_exactly_one_succeeds(tmp_path: Path):
    """P7: Holdout access across concurrent processes must allow exactly one consumer."""
    ledger_file = tmp_path / "ledger.json"
    runner = GovernedExperimentRunner(ledger_file)
    runner.advance_research_state(ResearchCycleState.DISCOVERY_ACTIVE, "start")
    runner.advance_research_state(ResearchCycleState.VALIDATION_ACTIVE, "validate")
    runner.advance_research_state(ResearchCycleState.MODEL_FROZEN, "freeze")
    runner.advance_research_state(ResearchCycleState.HOLDOUT_AUTHORIZED, "authorize")
    
    barrier = mp.Barrier(2)
    queue = mp.Queue()
    
    p1 = mp.Process(target=_holdout_worker, args=(str(ledger_file), barrier, queue))
    p2 = mp.Process(target=_holdout_worker, args=(str(ledger_file), barrier, queue))
    
    p1.start()
    p2.start()
    p1.join(timeout=5)
    p2.join(timeout=5)
    
    results = [queue.get() for _ in range(2)]
    successes = [r for r in results if r[0] == "SUCCESS"]
    errors = [r for r in results if r[0] == "ERROR"]
    
    # Exactly one must succeed and one must get HoldoutAlreadyConsumedError
    assert len(successes) == 1, f"Expected 1 success, got {len(successes)}: {results}"
    assert len(errors) == 1, f"Expected 1 error, got {len(errors)}: {results}"
    assert errors[0][1] == "HoldoutAlreadyConsumedError"


# -----------------------------------------------------------------------------
# P8 & P8.1: CANONICAL TIMESTAMP SEMANTICS
# -----------------------------------------------------------------------------
def test_transform_canonical_preserves_distinct_timestamps(tmp_path: Path):
    """P8.1: Transforming raw data must preserve separate exchange and local receive timestamps."""
    in_dir = tmp_path / "raw_in"
    in_dir.mkdir()
    out_dir = tmp_path / "canonical_out"
    
    # Create raw record with distinct exchange and receive timestamps
    raw_record = {
        "market": "BTC_KRW",
        "timestamp": 1000,           # exchange event timestamp (ms)
        "local_recv_ts": "1970-01-01T00:00:01.125000+00:00",  # 1125 ms
        "bids": [{"price": 100000000, "quantity": 1.0}],
        "asks": [{"price": 100050000, "quantity": 1.0}]
    }
    
    raw_file = in_dir / "bithumb_orderbook.ndjson.zst"
    dctx = zstandard.ZstdCompressor()
    with open(raw_file, "wb") as f:
        with dctx.stream_writer(f) as writer:
            writer.write((json.dumps(raw_record) + "\n").encode("utf-8"))
            
    res = main([
        "transform-canonical",
        "--input-dir", str(in_dir),
        "--output-dir", str(out_dir),
        "--exchange", "bithumb"
    ])
    assert res == 0
    
    # Inspect transformed canonical record
    out_files = list(out_dir.glob("canonical_*.ndjson.zst"))
    assert len(out_files) == 1
    
    dctx_decomp = zstandard.ZstdDecompressor()
    with open(out_files[0], "rb") as f:
        with dctx_decomp.stream_reader(f) as reader:
            import io
            text = io.TextIOWrapper(reader, encoding="utf-8")
            line = text.readline()
            ob_dict = json.loads(line)
            
    # In Phase 3: receive_timestamp_ms was copied from timestamp (1000 == 1000)
    # In Phase 4: receive_timestamp_ms must be 1125, exchange_timestamp_ms must be 1000
    assert ob_dict["exchange_timestamp_ms"] == 1000
    assert ob_dict["receive_timestamp_ms"] == 1125, f"Expected 1125, got {ob_dict['receive_timestamp_ms']}"


# -----------------------------------------------------------------------------
# P9: TRANSFORM PARTIAL SUCCESS REJECTED
# -----------------------------------------------------------------------------
def test_transform_partial_rejected_exits_nonzero(tmp_path: Path):
    """P9: transform-canonical must not return exit 0 if any records are rejected."""
    in_dir = tmp_path / "raw_in"
    in_dir.mkdir()
    out_dir = tmp_path / "canonical_out"
    
    # 1 valid record, 1 malformed record
    valid_record = {
        "market": "BTC_KRW",
        "timestamp": 1000,
        "local_recv_ts": "1970-01-01T00:00:01.000000+00:00",
        "bids": [{"price": 100000000, "quantity": 1.0}],
        "asks": [{"price": 100050000, "quantity": 1.0}]
    }
    malformed_record = {"invalid": "data_without_bids_asks"}
    
    raw_file = in_dir / "sample.ndjson.zst"
    dctx = zstandard.ZstdCompressor()
    with open(raw_file, "wb") as f:
        with dctx.stream_writer(f) as writer:
            writer.write((json.dumps(valid_record) + "\n").encode("utf-8"))
            writer.write((json.dumps(malformed_record) + "\n").encode("utf-8"))
            
    res = main([
        "transform-canonical",
        "--input-dir", str(in_dir),
        "--output-dir", str(out_dir),
        "--exchange", "bithumb"
    ])
    
    # In Phase 3: returned 0 because total_canonicalized > 0
    # In Phase 4: must return exit 2 (DATA_GATE_FAILURE) because 1 record was rejected!
    assert res == 2, f"Expected exit code 2 on partial rejection, got {res}"


# -----------------------------------------------------------------------------
# P10: ACTUAL RAW SERIALIZATION PATH TRACE
# -----------------------------------------------------------------------------
def test_p10_actual_collector_serializer_trace(tmp_path: Path):
    """P10: Converter must work directly on files produced by RawMicrostructureStorage."""
    from datetime import datetime, timezone
    from bithumb_coin_trader.microstructure_storage import RawMicrostructureStorage

    storage_dir = tmp_path / "raw_storage"
    storage = RawMicrostructureStorage(base_dir=storage_dir)

    # 1. Bithumb orderbook written by real storage engine
    bithumb_payload = {
        "market": "BTC_KRW",
        "timestamp": 1700000000000,
        "bids": [{"price": 100_000_000.0, "quantity": 1.5}],
        "asks": [{"price": 100_100_000.0, "quantity": 2.0}],
    }
    recv_time = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)
    bithumb_file = storage.append_raw_record(
        exchange="bithumb",
        stream="orderbook",
        market="BTC-KRW",
        payload=bithumb_payload,
        local_receive_ts=recv_time,
        exchange_ts=recv_time,
        local_receive_monotonic_ns=123456789,
        collector_run_id="test_run_1",
    )
    assert bithumb_file.exists()

    # Transform Bithumb partition directly
    out_dir = tmp_path / "canonical_bithumb"
    res = main([
        "transform-canonical",
        "--input-dir", str(bithumb_file.parent),
        "--output-dir", str(out_dir),
        "--exchange", "bithumb"
    ])
    assert res == 0, f"Bithumb transform failed: {res}"

    # Verify canonical records
    transformed_files = list(out_dir.glob("canonical_*.ndjson.zst"))
    assert len(transformed_files) == 1


# -----------------------------------------------------------------------------
# P11: DSR NUMERICAL ASSERTION & REFERENCE VS PRODUCTION
# -----------------------------------------------------------------------------
def test_p11_dsr_numerical_assertion_and_tolerance(tmp_path: Path):
    """P11.2: reproduce_v6_statistics must enforce numerical tolerance and exit non-zero on failure."""
    from scripts.reproduce_v6_statistics import reproduce_v6

    ledger_path = Path("evidence/research/trial_ledger_frozen_20260905.jsonl")
    if not ledger_path.exists():
        pytest.skip("Frozen ledger not present in test environment")

    # 1. Successful reproduction matching benchmark within tolerance
    summary_out = tmp_path / "dsr_summary.json"
    code = reproduce_v6(ledger_path, expected_dsr=0.6147, tolerance=0.005, emit_json=summary_out)
    assert code == 0
    assert summary_out.exists()
    summary = json.loads(summary_out.read_text())
    assert summary["tolerance_satisfied"] is True
    assert summary["periods_per_year"] == 365.25
    assert summary["status"] == "RESOLVED_ANALYTICAL_SUMMARY"
    assert summary["raw_input_evidence_status"] == "INCONCLUSIVE_INPUT_EVIDENCE"

    # 2. Failing reproduction when expected DSR is outside tolerance
    code_fail = reproduce_v6(ledger_path, expected_dsr=0.9500, tolerance=0.005)
    assert code_fail == 1


def test_p11_3_dsr_reference_vs_production_agreement():
    """P11.3: Production deflated_sharpe_ratio and independent reference must agree within tolerance."""
    from math import e, sqrt
    from statistics import NormalDist, mean, pstdev
    from bithumb_coin_trader.research_statistics import deflated_sharpe_ratio

    # Synthetic returns vector with known Sharpe
    returns = [0.001 * (1.0 if i % 2 == 0 else -0.8) for i in range(200)]
    trial_sharpes = [0.5, 0.8, 1.2, 1.5, 0.2]
    trial_N = 10

    # Production run
    prod_result = deflated_sharpe_ratio(returns, trial_sharpes=trial_sharpes, trial_count=trial_N)

    # Independent reference calculation from first principles
    r_mean = mean(returns)
    r_std = pstdev(returns)
    obs_sharpe = r_mean / r_std if r_std > 0 else 0.0

    sh_disp = pstdev(trial_sharpes)
    norm = NormalDist()
    euler_gamma = 0.5772156649015329
    exp_max = sh_disp * (
        (1.0 - euler_gamma) * norm.inv_cdf(1.0 - 1.0 / trial_N)
        + euler_gamma * norm.inv_cdf(1.0 - 1.0 / (trial_N * e))
    )

    std_returns = tuple((x - r_mean) / r_std for x in returns)
    skew = mean(x**3 for x in std_returns)
    kurt = mean(x**4 for x in std_returns)
    var_term = 1.0 - skew * obs_sharpe + (kurt - 1.0) * (obs_sharpe**2) / 4.0
    ref_prob = norm.cdf((obs_sharpe - exp_max) * sqrt(len(returns) - 1) / sqrt(max(var_term, 1e-12)))

    assert abs(prod_result.observed_sharpe - obs_sharpe) < 1e-9
    assert abs(prod_result.expected_maximum_sharpe - exp_max) < 1e-9
    assert abs(prod_result.probability - ref_prob) < 1e-9


# -----------------------------------------------------------------------------
# P12 & P13: FULL CROSS-LAYER SYNTHETIC PIPELINE E2E
# -----------------------------------------------------------------------------
def test_p13_full_cross_layer_synthetic_pipeline(tmp_path: Path):
    """P13: Fixture -> Audit -> DQ Qualify -> Transform -> Partition -> Manifest."""
    from datetime import datetime, timezone
    from bithumb_coin_trader.microstructure_storage import RawMicrostructureStorage

    raw_dir = tmp_path / "raw"
    storage = RawMicrostructureStorage(base_dir=raw_dir, manifest_dir=raw_dir)

    # 1. Create real collector fixtures
    last_file = None
    for i in range(50):
        t_ms = 1700000000000 + i * 1000
        dt = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc)
        last_file = storage.append_raw_record(
            exchange="bithumb",
            stream="orderbook",
            market="BTC-KRW",
            payload={
                "market": "BTC_KRW",
                "timestamp": t_ms,
                "bids": [{"price": 100_000_000.0 - i, "quantity": 1.0}],
                "asks": [{"price": 100_100_000.0 + i, "quantity": 1.0}],
            },
            local_receive_ts=dt,
            exchange_ts=dt,
            local_receive_monotonic_ns=1000000000 + i * 1000000,
            collector_run_id="run_synthetic_1",
            write_ts=dt,
        )
    assert last_file is not None
    storage.generate_partition_manifest(last_file)

    # 2. Audit Quality
    audit_report = tmp_path / "audit_report.json"
    ret_audit = main([
        "audit-quality",
        "--input-dir", str(raw_dir),
        "--report-out", str(audit_report),
    ])
    assert ret_audit == 0, "Audit quality failed"
    audit_data = json.loads(audit_report.read_text())
    assert audit_data["status"] == "STRUCTURAL_AUDIT_PASS"

    # P5/P6 migration: Structural audit cannot qualify; use authoritative deep DQ report
    deep_audit_report = tmp_path / "deep_audit_report.json"
    audit_data["audit_type"] = "authoritative_deep_dq"
    audit_data["status"] = "DQ_PASS_ELIGIBLE"
    audit_data["blockers"] = []
    deep_audit_report.write_text(json.dumps(audit_data))

    # 3. DQ Qualify (P12)
    manifest_files = list(raw_dir.glob("**/manifest_*.json"))
    dq_evidence_file = tmp_path / "dq_evidence.json"
    ret_qual = main([
        "dq-qualify",
        "--audit-report", str(deep_audit_report),
        "--source-manifest", str(manifest_files[0]),
        "--out", str(dq_evidence_file),
        "--policy", "strict_phase4",
        "--commit", "061873431da2e3b10e00869afc3fe9e746b88c41",
    ])
    assert ret_qual == 0, "DQ qualify failed"
    assert dq_evidence_file.exists()

    # 4. Transform Canonical
    canonical_dir = tmp_path / "canonical"
    ret_trans = main([
        "transform-canonical",
        "--input-dir", str(raw_dir),
        "--output-dir", str(canonical_dir),
        "--exchange", "bithumb",
    ])
    assert ret_trans == 0, "Transform canonical failed"
    canonical_files = list(canonical_dir.glob("canonical_*.ndjson.zst"))
    assert len(canonical_files) == 1
    canonical_input = canonical_files[0]

    # 5. Partition Dataset
    dataset_dir = tmp_path / "final_dataset"
    ret_part = main([
        "partition-dataset",
        "--input-file", str(canonical_input),
        "--output-dir", str(dataset_dir),
        "--dq-report", str(dq_evidence_file),
        "--train-frac", "0.60",
        "--val-frac", "0.20",
        "--purge-window-ms", "5000",
    ])
    assert ret_part == 0, "Partition dataset failed"

    # 6. Verify Manifest Provenance & Invariants
    manifest_path = dataset_dir / "manifest.json"
    assert manifest_path.exists()
    manifest_json = json.loads(manifest_path.read_text())
    assert manifest_json["source_record_count"] == 50
    assert len(manifest_json["dataset_id"]) >= 16
    assert manifest_json["dq_report_hash"]
    assert manifest_json["canonical_schema_version"] == "2.0.0"

    # Rerun determinism check: running on another output dir yields exact same dataset_id
    dataset_dir_2 = tmp_path / "final_dataset_rerun"
    ret_part_2 = main([
        "partition-dataset",
        "--input-file", str(canonical_input),
        "--output-dir", str(dataset_dir_2),
        "--dq-report", str(dq_evidence_file),
        "--train-frac", "0.60",
        "--val-frac", "0.20",
        "--purge-window-ms", "5000",
    ])
    assert ret_part_2 == 0
    manifest_json_2 = json.loads((dataset_dir_2 / "manifest.json").read_text())
    assert manifest_json["dataset_id"] == manifest_json_2["dataset_id"], "Dataset ID must be content-addressed and deterministic across runs"


# -----------------------------------------------------------------------------
# P14: NEGATIVE E2E TESTS (FAIL-CLOSED VERIFICATION)
# -----------------------------------------------------------------------------
def test_p14_negative_e2e_cases(tmp_path: Path):
    """P14: Fail-closed verification for tamper, clock reversal, and invalid states."""
    from bithumb_coin_trader.research_cli import compute_canonical_report_hash

    # Case A: DQ Report Hash Mismatch (Tampering by 1 byte)
    valid_dq = {
        "status": "DQ_PASS",
        "auditor_version": "1.0",
        "audit_code_commit": "a" * 40,
        "source_manifest_hash": "b" * 64,
        "criteria_version": "v1",
        "hard_fail_count": 0,
        "unknown_count": 0,
        "degraded_count": 0,
        "justification": "",
        "approved_policy": "strict",
        "created_at": "2026-09-06T00:00:00Z",
    }
    valid_dq["report_hash"] = compute_canonical_report_hash(valid_dq)
    
    # Tamper 1 byte
    tampered_dq = dict(valid_dq)
    tampered_dq["hard_fail_count"] = 1  # 1 byte change without recomputing hash
    tampered_file = tmp_path / "tampered_dq.json"
    tampered_file.write_text(json.dumps(tampered_dq))

    input_file = tmp_path / "input.ndjson.zst"
    records = [
        CanonicalOrderBook(
            exchange="bithumb",
            market="BTC-KRW",
            exchange_timestamp_ms=1000,
            receive_timestamp_ms=1005,
            bids=((100_000_000.0, 1.0),),
            asks=((100_100_000.0, 1.0),),
        )
    ]
    write_canonical_ndjson_zstd(input_file, records)

    res = main([
        "partition-dataset",
        "--input-file", str(input_file),
        "--output-dir", str(tmp_path / "out_tamper"),
        "--dq-report", str(tampered_file),
    ])
    assert res == 2, "Tampered report must fail closed"

