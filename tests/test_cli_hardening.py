import pytest
from pathlib import Path
import json
import zstandard
from bithumb_coin_trader.canonical_market_data import CanonicalOrderBook, write_canonical_ndjson_zstd
from bithumb_coin_trader.research_cli import main
from bithumb_coin_trader.prospective_dataset import DqQualificationStatus, DqQualificationEvidence, build_and_export_dataset, DqRejectedError

def test_partition_cli_rejects_unsorted_input(tmp_path):
    records = [
        CanonicalOrderBook(exchange="bithumb", market="BTC-KRW", receive_timestamp_ms=1000, exchange_timestamp_ms=1, bids=[], asks=[]),
        CanonicalOrderBook(exchange="bithumb", market="BTC-KRW", receive_timestamp_ms=3000, exchange_timestamp_ms=3, bids=[], asks=[]),
        CanonicalOrderBook(exchange="bithumb", market="BTC-KRW", receive_timestamp_ms=2000, exchange_timestamp_ms=2, bids=[], asks=[])
    ]
    input_file = tmp_path / "input.ndjson.zst"
    write_canonical_ndjson_zstd(input_file, records)
    
    out_dir = tmp_path / "out"
    dq_file = tmp_path / "dq.json"
    dq_file.write_text(json.dumps({"status": "DQ_PASS", "hard_fail_count": 0}))

    res = main(["partition-dataset", "--input-file", str(input_file), "--output-dir", str(out_dir), "--dq-report", str(dq_file)])
    assert res == 2

def test_partition_cli_fails_on_malformed_record(tmp_path):
    input_file = tmp_path / "input.ndjson.zst"
    dctx = zstandard.ZstdCompressor()
    with open(input_file, "wb") as f:
        with dctx.stream_writer(f) as writer:
            writer.write(b'{"exchange": "bithumb", "market": "BTC-KRW", "receive_timestamp_ms": 1000, "timestamp": 1, "bids": [], "asks": []}\n')
            writer.write(b'{"malformed": true}\n')
    
    out_dir = tmp_path / "out"
    dq_file = tmp_path / "dq.json"
    dq_file.write_text(json.dumps({"status": "DQ_PASS", "hard_fail_count": 0}))
    
    res = main(["partition-dataset", "--input-file", str(input_file), "--output-dir", str(out_dir), "--dq-report", str(dq_file)])
    assert res == 2

def test_partition_cli_requires_dq_evidence(tmp_path):
    input_file = tmp_path / "input.ndjson.zst"
    write_canonical_ndjson_zstd(input_file, [CanonicalOrderBook(exchange="bithumb", market="BTC-KRW", receive_timestamp_ms=1000, exchange_timestamp_ms=1, bids=[], asks=[])])
    out_dir = tmp_path / "out"
    
    res = main(["partition-dataset", "--input-file", str(input_file), "--output-dir", str(out_dir)])
    assert res == 2

def test_partition_cli_rejects_invalid_dq_evidence(tmp_path):
    input_file = tmp_path / "input.ndjson.zst"
    write_canonical_ndjson_zstd(input_file, [CanonicalOrderBook(exchange="bithumb", market="BTC-KRW", receive_timestamp_ms=1000, exchange_timestamp_ms=1, bids=[], asks=[])])
    out_dir = tmp_path / "out"
    dq_file = tmp_path / "dq.json"
    dq_file.write_text(json.dumps({"status": "DQ_FAIL"}))
    
    res = main(["partition-dataset", "--input-file", str(input_file), "--output-dir", str(out_dir), "--dq-report", str(dq_file)])
    assert res == 2

def test_partition_cli_respects_train_frac(tmp_path):
    records = [CanonicalOrderBook(exchange="bithumb", market="BTC-KRW", receive_timestamp_ms=1000 + i, exchange_timestamp_ms=1+i, bids=[], asks=[]) for i in range(100)]
    input_file = tmp_path / "input.ndjson.zst"
    write_canonical_ndjson_zstd(input_file, records)
    out_dir = tmp_path / "out"
    dq_file = tmp_path / "dq.json"
    dq_file.write_text(json.dumps({"status": "DQ_PASS", "hard_fail_count": 0}))
    
    res = main(["partition-dataset", "--input-file", str(input_file), "--output-dir", str(out_dir), "--dq-report", str(dq_file), "--train-frac", "0.50", "--val-frac", "0.20", "--purge-window-ms", "0"])
    assert res == 0
    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["train_records"] == 50

def test_audit_quality_empty_dir(tmp_path):
    res = main(["audit-quality", "--input-dir", str(tmp_path), "--report-out", str(tmp_path / "out.json")])
    assert res == 2
    report = json.loads((tmp_path / "out.json").read_text())
    assert report["status"] == "INCOMPLETE"

def test_audit_quality_no_data(tmp_path):
    (tmp_path / "manifest.json").write_text("{}")
    res = main(["audit-quality", "--input-dir", str(tmp_path), "--report-out", str(tmp_path / "out.json")])
    assert res == 2
    report = json.loads((tmp_path / "out.json").read_text())
    assert report["status"] == "INCOMPLETE"
    
def test_transform_stub_exit_code(tmp_path):
    res = main(["transform-canonical", "--input-dir", str(tmp_path), "--output-dir", str(tmp_path / "out"), "--exchange", "unsupported"])
    assert res == 3
    
def test_build_dataset_mixed_markets(tmp_path):
    records = [
        CanonicalOrderBook(exchange="bithumb", market="BTC-KRW", receive_timestamp_ms=1000, exchange_timestamp_ms=1, bids=[], asks=[]),
        CanonicalOrderBook(exchange="bithumb", market="ETH-KRW", receive_timestamp_ms=2000, exchange_timestamp_ms=2, bids=[], asks=[])
    ]
    dq = DqQualificationEvidence(
        status=DqQualificationStatus.DQ_PASS, auditor_version="1", audit_code_commit="1", source_manifest_hash="1", report_hash="1", created_at="1", criteria_version="1", hard_fail_count=0, unknown_count=0, degraded_count=0, justification="", approved_policy=""
    )
    with pytest.raises(ValueError, match="MIXED_DATASET"):
        build_and_export_dataset("id", tmp_path / "out", records, dq)
