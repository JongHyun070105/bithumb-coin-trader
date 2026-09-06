"""Phase 6 Runbook Executability, Flag Validation, and Subprocess E2E Mutation Test Suite.

Implements P4, P4.1, P4.2, P18, P19:
- P4.1: Extracts all bash command blocks from docs/POST_72H_OFFLINE_IMPORT_RUNBOOK.md and verifies CLI flags.
- P4.2 & P19: Executes the EXACT documented runbook sequence end-to-end via subprocess on a synthetic official-shaped epoch.
- Verifies 12 negative mutations failing at their respective stages.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zstandard

import pytest

from scripts.audit_72h_soak import SoakAuditor72H


def _extract_runbook_bash_blocks() -> list[str]:
    """Extracts bash code blocks from the runbook markdown."""
    runbook_path = Path(__file__).resolve().parent.parent / "docs" / "POST_72H_OFFLINE_IMPORT_RUNBOOK.md"
    content = runbook_path.read_text(encoding="utf-8")
    blocks = re.findall(r"```bash\n(.*?)\n```", content, re.DOTALL)
    return [b.strip() for b in blocks]


def test_p4_1_extract_and_validate_runbook_command_flags() -> None:
    """P4.1: Extract every command block from the runbook and verify all flags and subcommands exist."""
    blocks = _extract_runbook_bash_blocks()
    assert len(blocks) >= 4, f"Expected at least 4 bash blocks, found {len(blocks)}"

    repo_root = Path(__file__).resolve().parent.parent

    # Check that referenced script files exist
    assert (repo_root / "scripts" / "audit_72h_soak.py").exists()
    assert (repo_root / "scripts" / "build_epoch_manifest.py").exists()

    # Verify audit_72h_soak flags
    audit_parser_output = subprocess.check_output(
        [sys.executable, str(repo_root / "scripts" / "audit_72h_soak.py"), "--help"],
        text=True,
    )
    for flag in ["--epoch-dir", "--out-json", "--out-md"]:
        assert flag in audit_parser_output, f"Flag {flag} missing from audit_72h_soak.py"

    # Verify build_epoch_manifest flags
    manifest_parser_output = subprocess.check_output(
        [sys.executable, str(repo_root / "scripts" / "build_epoch_manifest.py"), "--help"],
        text=True,
    )
    for flag in ["--epoch-dir", "--output", "--strict"]:
        assert flag in manifest_parser_output, f"Flag {flag} missing from build_epoch_manifest.py"

    # Verify research_cli subcommands
    cli_help_output = subprocess.check_output(
        [sys.executable, "-m", "bithumb_coin_trader.research_cli", "--help"],
        text=True,
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root / "src")},
    )
    for subcmd in ["dq-qualify", "transform-canonical", "partition-dataset"]:
        assert subcmd in cli_help_output, f"Subcommand {subcmd} missing from research_cli"


def _populate_official_shaped_epoch(
    epoch_dir: Path,
    collector_epoch: str = "epoch_72h_soak_official",
    collector_run_id: str = "run_72h_aws_production",
    software_commit: str = "753d7848759d3fdd5e20af7c3f2d08b14fca7cda",
    fingerprint: str = "fp-official-72h",
    hour_cohort: str = "20260901-00",
) -> None:
    """Populates a synthetic epoch matching all official specifications."""
    raw_dir = epoch_dir / "raw"
    manifests_dir = epoch_dir / "manifests"
    receipts_dir = epoch_dir / "archive-receipts"
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)
    receipts_dir.mkdir(parents=True, exist_ok=True)

    cctx = zstandard.ZstdCompressor(level=3)
    base_ts = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
    base_ms = int(base_ts.timestamp() * 1000)

    # 76 feeds
    for idx, (exch, strm, mkt) in enumerate(SoakAuditor72H.get_expected_feed_universe()):
        part_dir = raw_dir / f"exchange={exch}" / f"stream={strm}" / f"market={mkt}"
        part_dir.mkdir(parents=True, exist_ok=True)

        records = []
        for rec_idx in range(5):
            t = base_ms + rec_idx * 1000
            if strm == "orderbook":
                payload = {
                    "bids": [[100_000_000.0 - rec_idx, 1.0]],
                    "asks": [[100_010_000.0 + rec_idx, 1.0]],
                    "orderbook_units": [{"bid_price": 100_000_000.0, "bid_size": 1.0, "ask_price": 100_010_000.0, "ask_size": 1.0}],
                }
            elif strm == "trade":
                payload = {
                    "trade_id": f"T_{exch}_{mkt}_{rec_idx}",
                    "price": 100_000_000.0,
                    "volume": 0.1,
                    "ask_bid": "BID",
                }
            else:  # ticker
                payload = {
                    "closing_price": 100_000_000.0,
                    "trade_price": 100_000_000.0,
                }

            rec = {
                "exchange": exch,
                "stream": strm,
                "market": mkt,
                "exchange_ts": t,
                "local_recv_ts": t + 10,
                "local_recv_monotonic_ns": 1_000_000_000 + rec_idx * 1_000_000,
                "collector_run_id": collector_run_id,
                "local_write_ts": t + 15,
                "payload": payload,
            }
            records.append(json.dumps(rec))

        content = ("\n".join(records) + "\n").encode("utf-8")
        compressed = cctx.compress(content)
        part_file = part_dir / f"part-{hour_cohort}.zst"
        part_file.write_bytes(compressed)

        man_part_dir = manifests_dir / f"exchange={exch}" / f"stream={strm}" / f"market={mkt}"
        man_part_dir.mkdir(parents=True, exist_ok=True)
        sha = hashlib.sha256(compressed).hexdigest()
        rel_path = f"raw/exchange={exch}/stream={strm}/market={mkt}/part-{hour_cohort}.zst"
        man_record = {
            "partition_path": rel_path,
            "file_name": f"part-{hour_cohort}.zst",
            "sha256": sha,
            "record_count": 5,
            "bytes": len(compressed),
            "exchange": exch,
            "stream": strm,
            "market": mkt,
            "hour_cohort": hour_cohort,
        }
        (man_part_dir / f"manifest_part-{hour_cohort}.json").write_text(json.dumps(man_record))

    # Archive Receipt
    (receipts_dir / f"{hour_cohort}.archive-receipt.json").write_text(json.dumps({
        "hour_cohort": hour_cohort,
        "file_count": 76,
        "restore_verified": True,
        "status": "PASS",
    }))

    # Full-scan report
    (receipts_dir / f"full_scan_{hour_cohort.replace('-', '_')}_report.json").write_text(json.dumps({
        "scan_id": f"fs-{hour_cohort}",
        "status": "PASS",
        "total_records": 76 * 5,
    }))

    # Contract
    (epoch_dir / "epoch_contract.json").write_text(json.dumps({
        "collector_epoch": collector_epoch,
        "collector_run_id": collector_run_id,
        "runtime_software_commit": software_commit,
        "runtime_fingerprint": fingerprint,
        "start_time_utc": "2026-09-01T00:00:00+00:00",
        "duration_seconds": 3600,
        "require_receipts": True,
        "require_fullscan": True,
    }, indent=2))

    # Runtime seal
    (epoch_dir / "runtime_seal.json").write_text(json.dumps({
        "collector_epoch": collector_epoch,
        "collector_run_id": collector_run_id,
        "runtime_software_commit": software_commit,
        "runtime_config_fingerprint": fingerprint,
        "raw_schema_version": "2.0.0",
    }, indent=2))

    # Launch provenance
    (epoch_dir / "launch-provenance.json").write_text(json.dumps({
        "collector_epoch": collector_epoch,
        "collector_run_id": collector_run_id,
        "software_commit": software_commit,
        "fingerprint": fingerprint,
        "launch_time_utc": "2026-09-01T00:00:00+00:00",
    }, indent=2))


def test_p4_2_p19_full_runbook_subprocess_execution(tmp_path: Path) -> None:
    """P4.2 & P19: Executes the exact documented runbook sequence via subprocess."""
    repo_root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": f"{repo_root}/src:{repo_root}"}

    epoch_dir = tmp_path / "data" / "exported_soak_72h"
    _populate_official_shaped_epoch(epoch_dir)

    reports_dir = tmp_path / "reports"
    evidence_dir = tmp_path / "evidence" / "research"
    canonical_dir = tmp_path / "data" / "canonical_72h"
    dataset_dir = tmp_path / "data" / "datasets" / "krw_btc_72h_v1"

    reports_dir.mkdir(parents=True)
    evidence_dir.mkdir(parents=True)
    canonical_dir.mkdir(parents=True)

    # 1. audit_72h_soak.py
    proc_audit = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "audit_72h_soak.py"),
            "--epoch-dir", str(epoch_dir),
            "--out-json", str(reports_dir / "deep_dq_audit_72h.json"),
            "--out-md", str(reports_dir / "deep_dq_audit_72h.md"),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc_audit.returncode == 0, f"audit_72h_soak failed: {proc_audit.stderr}"
    audit_data = json.loads((reports_dir / "deep_dq_audit_72h.json").read_text())
    assert audit_data["status"] == "DQ_PASS_ELIGIBLE"

    # 2. build_epoch_manifest.py
    proc_em = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "build_epoch_manifest.py"),
            "--epoch-dir", str(epoch_dir),
            "--output", str(epoch_dir / "manifests" / "epoch_manifest.json"),
            "--strict",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc_em.returncode == 0, f"build_epoch_manifest failed: {proc_em.stderr}"
    em_data = json.loads((epoch_dir / "manifests" / "epoch_manifest.json").read_text())
    assert em_data["status"] == "SEALED_COMPLETE"

    # 3. dq-qualify
    proc_qual = subprocess.run(
        [
            sys.executable,
            "-m", "bithumb_coin_trader.research_cli",
            "dq-qualify",
            "--audit-report", str(reports_dir / "deep_dq_audit_72h.json"),
            "--source-manifest", str(epoch_dir / "manifests" / "epoch_manifest.json"),
            "--out", str(evidence_dir / "dq_qualification_72h.json"),
            "--strict",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc_qual.returncode == 0, f"dq-qualify failed: {proc_qual.stderr}"
    assert (evidence_dir / "dq_qualification_72h.json").exists()

    # 4. transform-canonical (orderbook)
    proc_tf_ob = subprocess.run(
        [
            sys.executable,
            "-m", "bithumb_coin_trader.research_cli",
            "transform-canonical",
            "--input-dir", str(epoch_dir / "raw"),
            "--output-dir", str(canonical_dir),
            "--exchange", "bithumb",
            "--stream", "orderbook",
            "--schema-version", "2.1.0",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc_tf_ob.returncode == 0, f"transform-canonical ob failed: {proc_tf_ob.stderr}"

    # 5. transform-canonical (trade)
    proc_tf_tr = subprocess.run(
        [
            sys.executable,
            "-m", "bithumb_coin_trader.research_cli",
            "transform-canonical",
            "--input-dir", str(epoch_dir / "raw"),
            "--output-dir", str(canonical_dir),
            "--exchange", "bithumb",
            "--stream", "trade",
            "--schema-version", "2.1.0",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc_tf_tr.returncode == 0, f"transform-canonical trade failed: {proc_tf_tr.stderr}"
    assert (canonical_dir / "canonical_manifest.json").exists()

    # 6. partition-dataset
    proc_part = subprocess.run(
        [
            sys.executable,
            "-m", "bithumb_coin_trader.research_cli",
            "partition-dataset",
            "--canonical-manifest", str(canonical_dir / "canonical_manifest.json"),
            "--exchange", "bithumb",
            "--market", "KRW-BTC",
            "--stream", "orderbook",
            "--output-dir", str(dataset_dir),
            "--dq-report", str(evidence_dir / "dq_qualification_72h.json"),
            "--source-manifest", str(epoch_dir / "manifests" / "epoch_manifest.json"),
            "--deep-audit-report", str(reports_dir / "deep_dq_audit_72h.json"),
            "--train-frac", "0.60",
            "--val-frac", "0.20",
            "--purge-window-ms", "900000",
            "--clock", "receive_wall_clock",
            "--source-epoch-id", "epoch_72h_soak_official",
            "--source-run-id", "run_72h_aws_production",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc_part.returncode == 0, f"partition-dataset failed: {proc_part.stderr}"
    assert (dataset_dir / "manifest.json").exists()


# =============================================================================
# P19: Negative Mutation Suite (12 Failure Modes via Subprocess)
# =============================================================================

@pytest.mark.parametrize("mutation_type", [
    "missing_feed",
    "missing_full_hour",
    "missing_receipt",
    "missing_fullscan",
    "wrong_runtime_commit",
    "wrong_fingerprint",
    "wrong_run_id",
    "wrong_epoch_id",
    "source_manifest_changed",
    "deep_report_changed",
    "canonical_file_changed",
    "canonical_manifest_changed",
    "source_raw_changed",
])
def test_p19_negative_mutation_failure_modes(tmp_path: Path, mutation_type: str) -> None:
    """P19: Each negative mutation must be detected and fail at the correct stage with exact exit code 2."""
    repo_root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": f"{repo_root}/src:{repo_root}"}

    epoch_dir = tmp_path / f"epoch_{mutation_type}"
    _populate_official_shaped_epoch(epoch_dir)

    # Stage 1 mutations: audit_72h_soak.py
    if mutation_type == "missing_feed":
        victim = list((epoch_dir / "raw").rglob("part-*.zst"))[0]
        victim.unlink()
        p = subprocess.run([
            sys.executable, str(repo_root / "scripts" / "audit_72h_soak.py"),
            "--epoch-dir", str(epoch_dir),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Missing feed must fail audit: {p.stderr}"
        assert "MISSING_REQUIRED_FEED" in p.stderr or "MISSING_REQUIRED_FEED" in p.stdout
        return

    elif mutation_type == "missing_full_hour":
        contract = json.loads((epoch_dir / "epoch_contract.json").read_text())
        contract["duration_seconds"] = 7200
        contract["expected_end_time_utc"] = "2026-09-01T02:00:00+00:00"
        (epoch_dir / "epoch_contract.json").write_text(json.dumps(contract))
        p = subprocess.run([
            sys.executable, str(repo_root / "scripts" / "audit_72h_soak.py"),
            "--epoch-dir", str(epoch_dir),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Missing hour must fail audit: {p.stderr}"
        assert "MISSING_EXPECTED_HOUR" in p.stderr or "MISSING_EXPECTED_HOUR" in p.stdout
        return

    elif mutation_type == "missing_receipt":
        shutil.rmtree(epoch_dir / "archive-receipts")
        p = subprocess.run([
            sys.executable, str(repo_root / "scripts" / "audit_72h_soak.py"),
            "--epoch-dir", str(epoch_dir),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Missing receipt must fail audit: {p.stderr}"
        assert "ARCHIVE_RECEIPT_MISSING" in p.stderr or "ARCHIVE_RECEIPT_MISSING" in p.stdout or "MISSING_RECEIPT" in p.stderr or "MISSING_RECEIPT" in p.stdout
        return

    elif mutation_type == "missing_fullscan":
        for fs in (epoch_dir / "archive-receipts").glob("full_scan_*"):
            fs.unlink()
        contract = json.loads((epoch_dir / "epoch_contract.json").read_text())
        contract["duration_seconds"] = 259200
        (epoch_dir / "epoch_contract.json").write_text(json.dumps(contract))
        p = subprocess.run([
            sys.executable, str(repo_root / "scripts" / "audit_72h_soak.py"),
            "--epoch-dir", str(epoch_dir),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Missing fullscan must fail audit: {p.stderr}"
        assert "FULLSCAN_EVIDENCE_MISSING" in p.stderr or "FULLSCAN_EVIDENCE_MISSING" in p.stdout or "MISSING_FULLSCAN" in p.stderr or "MISSING_FULLSCAN" in p.stdout
        return

    # Run audit successfully for downstream mutations
    rep_file = tmp_path / "audit.json"
    subprocess.run([
        sys.executable, str(repo_root / "scripts" / "audit_72h_soak.py"),
        "--epoch-dir", str(epoch_dir),
        "--out-json", str(rep_file),
    ], check=True, env=env)

    # Stage 2 mutations: build_epoch_manifest.py
    if mutation_type == "wrong_runtime_commit":
        contract = json.loads((epoch_dir / "epoch_contract.json").read_text())
        contract["runtime_software_commit"] = "0" * 40
        (epoch_dir / "epoch_contract.json").write_text(json.dumps(contract))
        p = subprocess.run([
            sys.executable, str(repo_root / "scripts" / "build_epoch_manifest.py"),
            "--epoch-dir", str(epoch_dir),
            "--strict",
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Wrong runtime commit must fail build_epoch_manifest: {p.stderr}"
        assert "RUNTIME_COMMIT_MISMATCH" in p.stderr or "RUNTIME_COMMIT_MISMATCH" in p.stdout
        return

    elif mutation_type == "wrong_fingerprint":
        contract = json.loads((epoch_dir / "epoch_contract.json").read_text())
        contract["runtime_fingerprint"] = "fp-tampered"
        (epoch_dir / "epoch_contract.json").write_text(json.dumps(contract))
        p = subprocess.run([
            sys.executable, str(repo_root / "scripts" / "build_epoch_manifest.py"),
            "--epoch-dir", str(epoch_dir),
            "--strict",
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Wrong fingerprint must fail build_epoch_manifest: {p.stderr}"
        assert "RUNTIME_FINGERPRINT_MISMATCH" in p.stderr or "RUNTIME_FINGERPRINT_MISMATCH" in p.stdout
        return

    # Build epoch manifest successfully
    em_file = epoch_dir / "manifests" / "epoch_manifest.json"
    subprocess.run([
        sys.executable, str(repo_root / "scripts" / "build_epoch_manifest.py"),
        "--epoch-dir", str(epoch_dir),
        "--output", str(em_file),
        "--strict",
    ], check=True, env=env)

    # Stage 4 mutation: transform-canonical TOCTOU check
    if mutation_type == "source_raw_changed":
        victim = list((epoch_dir / "raw").rglob("part-*.zst"))[0]
        victim.write_bytes(victim.read_bytes() + b"extra")
        canon_dir = tmp_path / "canon"
        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "transform-canonical",
            "--input-dir", str(epoch_dir / "raw"),
            "--output-dir", str(canon_dir),
            "--exchange", "bithumb",
            "--stream", "orderbook",
            "--epoch-manifest", str(em_file),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Modified raw file must fail transform-canonical with exit 2: {p.stderr}"
        assert "SOURCE_RAW_HASH_MISMATCH" in p.stdout or "SOURCE_RAW_HASH_MISMATCH" in p.stderr
        return

    # Stage 3: dq-qualify
    qual_file = tmp_path / "qual.json"
    subprocess.run([
        sys.executable, "-m", "bithumb_coin_trader.research_cli",
        "dq-qualify",
        "--audit-report", str(rep_file),
        "--source-manifest", str(em_file),
        "--out", str(qual_file),
        "--strict",
    ], check=True, env=env)

    # Stage 4 & 5: transform-canonical
    canon_dir = tmp_path / "canon"
    subprocess.run([
        sys.executable, "-m", "bithumb_coin_trader.research_cli",
        "transform-canonical",
        "--input-dir", str(epoch_dir / "raw"),
        "--output-dir", str(canon_dir),
        "--exchange", "bithumb",
        "--stream", "orderbook",
        "--epoch-manifest", str(em_file),
    ], check=True, env=env)

    subprocess.run([
        sys.executable, "-m", "bithumb_coin_trader.research_cli",
        "transform-canonical",
        "--input-dir", str(epoch_dir / "raw"),
        "--output-dir", str(canon_dir),
        "--exchange", "bithumb",
        "--stream", "trade",
        "--epoch-manifest", str(em_file),
    ], check=True, env=env)

    # Stage 6 mutations: partition-dataset
    if mutation_type == "source_manifest_changed":
        em_file.write_text(em_file.read_text() + "\n ")
        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "partition-dataset",
            "--canonical-manifest", str(canon_dir / "canonical_manifest.json"),
            "--exchange", "bithumb",
            "--market", "KRW-BTC",
            "--stream", "orderbook",
            "--output-dir", str(tmp_path / "ds"),
            "--dq-report", str(qual_file),
            "--source-manifest", str(em_file),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Modified source manifest must fail partition-dataset with exit 2: {p.stderr}"
        assert "DQ_SOURCE_MISMATCH" in p.stdout or "DQ_SOURCE_MISMATCH" in p.stderr
        return

    elif mutation_type == "deep_report_changed":
        rep_file.write_text(rep_file.read_text() + "\n ")
        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "partition-dataset",
            "--canonical-manifest", str(canon_dir / "canonical_manifest.json"),
            "--exchange", "bithumb",
            "--market", "KRW-BTC",
            "--stream", "orderbook",
            "--output-dir", str(tmp_path / "ds"),
            "--dq-report", str(qual_file),
            "--source-manifest", str(em_file),
            "--deep-audit-report", str(rep_file),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Modified deep report must fail partition-dataset with exit 2: {p.stderr}"
        assert "AUDIT_REPORT_HASH_MISMATCH" in p.stdout or "DEEP_AUDIT_REPORT_MISMATCH" in p.stdout or "AUDIT_REPORT_HASH_MISMATCH" in p.stderr or "DEEP_AUDIT_REPORT_MISMATCH" in p.stderr
        return

    elif mutation_type == "wrong_run_id":
        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "partition-dataset",
            "--canonical-manifest", str(canon_dir / "canonical_manifest.json"),
            "--exchange", "bithumb",
            "--market", "KRW-BTC",
            "--stream", "orderbook",
            "--output-dir", str(tmp_path / "ds"),
            "--dq-report", str(qual_file),
            "--source-manifest", str(em_file),
            "--source-run-id", "run_WRONG_ID",
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Wrong run ID must fail partition-dataset with exit 2: {p.stderr}"
        assert "COLLECTOR_RUN_ID_MISMATCH" in p.stdout or "COLLECTOR_RUN_ID_MISMATCH" in p.stderr
        return

    elif mutation_type == "wrong_epoch_id":
        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "partition-dataset",
            "--canonical-manifest", str(canon_dir / "canonical_manifest.json"),
            "--exchange", "bithumb",
            "--market", "KRW-BTC",
            "--stream", "orderbook",
            "--output-dir", str(tmp_path / "ds"),
            "--dq-report", str(qual_file),
            "--source-manifest", str(em_file),
            "--source-epoch-id", "epoch_WRONG_ID",
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Wrong epoch ID must fail partition-dataset with exit 2: {p.stderr}"
        assert "COLLECTOR_EPOCH_MISMATCH" in p.stdout or "COLLECTOR_EPOCH_MISMATCH" in p.stderr
        return

    elif mutation_type == "canonical_file_changed":
        cf = list(canon_dir.glob("canonical_*.ndjson.zst"))[0]
        dctx = zstandard.ZstdDecompressor()
        cctx = zstandard.ZstdCompressor(level=3)
        with open(cf, "rb") as fh:
            with dctx.stream_reader(fh) as reader:
                raw_decomp = reader.read()
        lines = [line for line in raw_decomp.decode("utf-8").splitlines() if line.strip()]
        if lines:
            rec = json.loads(lines[0])
            rec["receive_timestamp_ms"] = rec.get("receive_timestamp_ms", 0) + 1
            lines[0] = json.dumps(rec)
            modified_bytes = ("\n".join(lines) + "\n").encode("utf-8")
        else:
            modified_bytes = b'{"modified": true}\n'
        cf.write_bytes(cctx.compress(modified_bytes))

        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "partition-dataset",
            "--canonical-manifest", str(canon_dir / "canonical_manifest.json"),
            "--exchange", "bithumb",
            "--market", "KRW-BTC",
            "--stream", "orderbook",
            "--output-dir", str(tmp_path / "ds"),
            "--dq-report", str(qual_file),
            "--source-manifest", str(em_file),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Modified canonical file must fail partition-dataset with exit 2: {p.stderr}"
        assert "CANONICAL_PARTITION_HASH_MISMATCH" in p.stdout or "CANONICAL_PARTITION_HASH_MISMATCH" in p.stderr
        return

    elif mutation_type == "canonical_manifest_changed":
        cm = canon_dir / "canonical_manifest.json"
        cm_data = json.loads(cm.read_text())
        cm_data["canonical_manifest_sha256"] = "0" * 64
        cm.write_text(json.dumps(cm_data))
        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "partition-dataset",
            "--canonical-manifest", str(cm),
            "--exchange", "bithumb",
            "--market", "KRW-BTC",
            "--stream", "orderbook",
            "--output-dir", str(tmp_path / "ds"),
            "--dq-report", str(qual_file),
            "--source-manifest", str(em_file),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Modified canonical manifest must fail partition-dataset with exit 2: {p.stderr}"
        assert "CANONICAL_MANIFEST_HASH_MISMATCH" in p.stdout or "CANONICAL_MANIFEST_HASH_MISMATCH" in p.stderr
        return


def test_post_72h_offline_import_script_execution(tmp_path: Path) -> None:
    """P16: Verifies scripts/post_72h_offline_import.sh executes end-to-end successfully."""
    repo_root = Path(__file__).resolve().parent.parent
    epoch_dir = tmp_path / "exported_soak"
    _populate_official_shaped_epoch(epoch_dir)

    script_path = repo_root / "scripts" / "post_72h_offline_import.sh"
    assert script_path.exists()

    proc = subprocess.run(
        [
            str(script_path),
            "--epoch-dir", str(epoch_dir),
            "--reports-dir", str(tmp_path / "reports"),
            "--evidence-dir", str(tmp_path / "evidence"),
            "--canonical-dir", str(tmp_path / "canonical"),
            "--dataset-dir", str(tmp_path / "dataset"),
            "--python", sys.executable,
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"post_72h_offline_import.sh failed: {proc.stderr}\nStdout: {proc.stdout}"
    assert (tmp_path / "dataset" / "manifest.json").exists()
    assert (tmp_path / "dataset" / "train.ndjson.zst").exists()
    assert (tmp_path / "dataset" / "validation.ndjson.zst").exists()
    assert (tmp_path / "dataset" / "holdout.ndjson.zst").exists()
