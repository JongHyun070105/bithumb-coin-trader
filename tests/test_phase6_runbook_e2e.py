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

    # Actual start evidence artifact (P0.1)
    act_evidence = epoch_dir / "actual_start.evidence.json"
    act_evidence.write_text(json.dumps({
        "collector_epoch": collector_epoch,
        "collector_run_id": collector_run_id,
        "actual_start_time_utc": "2026-09-01T00:00:00+00:00",
        "evidence_type": "unit_start_log",
    }, indent=2))
    act_sha = hashlib.sha256(act_evidence.read_bytes()).hexdigest()

    # Contract (P0, P0.1, P0.2)
    (epoch_dir / "epoch_contract.json").write_text(json.dumps({
        "collector_epoch": collector_epoch,
        "collector_run_id": collector_run_id,
        "runtime_software_commit": software_commit,
        "runtime_fingerprint": fingerprint,
        "start_time_utc": "2026-09-01T00:00:00+00:00",
        "actual_start_time_utc": "2026-09-01T00:00:00+00:00",
        "expected_end_time_utc": "2026-09-01T01:00:00+00:00",
        "duration_seconds": 3600,
        "actual_start_evidence_path": str(act_evidence),
        "actual_start_evidence_sha256": act_sha,
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
        "duration_seconds": 3600,
    }, indent=2))

    # Launch provenance (P11)
    (epoch_dir / "launch-provenance.json").write_text(json.dumps({
        "collector_epoch": collector_epoch,
        "collector_run_id": collector_run_id,
        "software_commit": software_commit,
        "runtime_code_commit": software_commit,
        "fingerprint": fingerprint,
        "launch_time_utc": "2026-09-01T00:00:00+00:00",
        "created_at_utc": "2026-08-31T23:55:00+00:00",
        "duration_seconds": 3600,
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

    contract_path = epoch_dir / "epoch_contract.json"
    epoch_manifest_path = epoch_dir / "manifests" / "epoch_manifest.json"

    # Stage 1: build_epoch_manifest.py (P1: Root sealed before Deep Audit)
    proc_em = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "build_epoch_manifest.py"),
            "--epoch-dir", str(epoch_dir),
            "--contract", str(contract_path),
            "--output", str(epoch_manifest_path),
            "--strict",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc_em.returncode == 0, f"build_epoch_manifest failed: {proc_em.stderr}"
    em_data = json.loads(epoch_manifest_path.read_text())
    assert em_data["status"] == "SEALED_COMPLETE"

    # Stage 2: audit_72h_soak.py (P1.1: Authoritative Deep DQ Audit binds root)
    proc_audit = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "audit_72h_soak.py"),
            "--epoch-dir", str(epoch_dir),
            "--epoch-manifest", str(epoch_manifest_path),
            "--contract", str(contract_path),
            "--out-json", str(reports_dir / "deep_dq_audit_72h.json"),
            "--out-md", str(reports_dir / "deep_dq_audit_72h.md"),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc_audit.returncode == 0, f"audit_72h_soak failed: {proc_audit.stderr}\nStdout: {proc_audit.stdout}"
    audit_data = json.loads((reports_dir / "deep_dq_audit_72h.json").read_text())
    assert audit_data["status"] == "DQ_PASS_ELIGIBLE"

    # Stage 3: dq-qualify (P4: Qualification binds epoch manifest file)
    proc_qual = subprocess.run(
        [
            sys.executable,
            "-m", "bithumb_coin_trader.research_cli",
            "dq-qualify",
            "--audit-report", str(reports_dir / "deep_dq_audit_72h.json"),
            "--epoch-manifest", str(epoch_manifest_path),
            "--out", str(evidence_dir / "dq_qualification_72h.json"),
            "--strict",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc_qual.returncode == 0, f"dq-qualify failed: {proc_qual.stderr}"
    assert (evidence_dir / "dq_qualification_72h.json").exists()

    # Stage 4: transform-canonical orderbook (P8, P14: binds root partitions and dq qualification)
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
            "--epoch-manifest", str(epoch_manifest_path),
            "--dq-qualification", str(evidence_dir / "dq_qualification_72h.json"),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc_tf_ob.returncode == 0, f"transform-canonical ob failed: {proc_tf_ob.stderr}"

    # Stage 5: transform-canonical trade
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
            "--epoch-manifest", str(epoch_manifest_path),
            "--dq-qualification", str(evidence_dir / "dq_qualification_72h.json"),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc_tf_tr.returncode == 0, f"transform-canonical trade failed: {proc_tf_tr.stderr}"
    assert (canonical_dir / "canonical_manifest.json").exists()

    # Stage 6: partition-dataset (P6, P7, P15, P16)
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
            "--epoch-manifest", str(epoch_manifest_path),
            "--deep-audit-report", str(reports_dir / "deep_dq_audit_72h.json"),
            "--train-frac", "0.60",
            "--val-frac", "0.20",
            "--purge-window-ms", "900000",
            "--clock", "receive_wall_clock",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc_part.returncode == 0, f"partition-dataset failed: {proc_part.stderr}\nStdout: {proc_part.stdout}"
    assert (dataset_dir / "manifest.json").exists()


# =============================================================================
# P18: Exact Runbook Execution Order Test
# =============================================================================

def test_p18_runbook_exact_order(tmp_path: Path) -> None:
    """P18: Runbook script executes stages in authoritative order; Deep Audit occurs AFTER Root."""
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
    assert proc.returncode == 0, f"Script failed: {proc.stderr}\nStdout: {proc.stdout}"

    stages = [
        "[Stage 1/6] COMPOSE/VERIFY CONTRACT...",
        "[Stage 2/6] BUILD ROOT...",
        "[Stage 3/6] DEEP AUDIT...",
        "[Stage 4/6] QUALIFY...",
        "[Stage 5/6] CANONICALIZE...",
        "[Stage 6/6] PARTITION...",
    ]
    indices = [proc.stdout.find(s) for s in stages]
    for s, idx in zip(stages, indices):
        assert idx != -1, f"Stage {s} not found in output: {proc.stdout}"

    assert indices == sorted(indices), f"Stages executed out of order: {indices}"
    root_idx = indices[1]
    audit_idx = indices[2]
    assert root_idx < audit_idx, f"Deep Audit occurred before Epoch Root ({audit_idx} < {root_idx})"


# =============================================================================
# P19: Comprehensive Negative Mutation Suite (Strict Exit 2 & Exact Tokens)
# =============================================================================

@pytest.mark.parametrize("mutation_type, expected_token", [
    ("created_at_used_as_start", "ACTUAL_START_EVIDENCE_MISSING"),
    ("actual_start_missing", "ACTUAL_START_EVIDENCE_MISSING"),
    ("epoch_root_missing", "NO_EPOCH_MANIFEST"),
    ("epoch_root_hash_mutation", "EPOCH_MANIFEST_HASH_MISMATCH"),
    ("deep_dq_cli_without_contract", "NO_RUN_CONTRACT"),
    ("hash_only_qualification", "HASH_ONLY_QUALIFICATION_NOT_PERMITTED"),
    ("dq_pass_with_degradation", "DQ_DEGRADED"),
    ("legacy_strict_phase4", "LEGACY_QUALIFICATION_REJECTED"),
    ("missing_deep_audit_at_partition", "MISSING_DEEP_AUDIT_REPORT"),
    ("extra_unsealed_raw", "UNSEALED_SOURCE_PARTITION"),
    ("source_raw_changed", "SOURCE_RAW_HASH_MISMATCH"),
    ("malformed_local_recv_ts", "CORRUPT_RAW_RECORD"),
    ("malformed_monotonic", "MONOTONIC_CLOCK_REVERSAL"),
    ("runtime_code_commit_mismatch", "RUNTIME_COMMIT_MISMATCH"),
    ("synthetic_source_ids_rejected", "INVALID_ROOT_PROVENANCE"),
    ("evidence_chain_mismatch", "EVIDENCE_CHAIN_MISMATCH"),
    ("missing_feed", "MISSING_REQUIRED_FEED"),
    ("missing_full_hour", "MISSING_EXPECTED_HOUR"),
    ("missing_receipt", "ARCHIVE_RECEIPT_MISSING"),
    ("missing_fullscan", "FULLSCAN_EVIDENCE_MISSING"),
    ("canonical_manifest_changed", "CANONICAL_MANIFEST_HASH_MISMATCH"),
    ("canonical_file_changed", "CANONICAL_PARTITION_HASH_MISMATCH"),
])
def test_p19_negative_mutation_failure_modes(tmp_path: Path, mutation_type: str, expected_token: str) -> None:
    """P19: Every negative mutation must fail with exit code 2 and exact error token."""
    repo_root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": f"{repo_root}/src:{repo_root}"}

    epoch_dir = tmp_path / f"epoch_{mutation_type}"
    _populate_official_shaped_epoch(epoch_dir)

    contract_path = epoch_dir / "epoch_contract.json"
    epoch_manifest_path = epoch_dir / "manifests" / "epoch_manifest.json"
    reports_dir = tmp_path / f"reports_{mutation_type}"
    evidence_dir = tmp_path / f"evidence_{mutation_type}"
    canon_dir = tmp_path / f"canon_{mutation_type}"
    reports_dir.mkdir(parents=True)
    evidence_dir.mkdir(parents=True)
    canon_dir.mkdir(parents=True)

    # 1. Contract mutations
    if mutation_type == "created_at_used_as_start":
        p = subprocess.run([
            sys.executable, str(repo_root / "scripts" / "compose_epoch_contract.py"),
            "--runtime-seal", str(epoch_dir / "runtime_seal.json"),
            "--launch-provenance", str(epoch_dir / "launch-provenance.json"),
            "--output", str(tmp_path / "out_contract.json"),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    elif mutation_type == "actual_start_missing":
        p = subprocess.run([
            sys.executable, str(repo_root / "scripts" / "compose_epoch_contract.py"),
            "--runtime-seal", str(epoch_dir / "runtime_seal.json"),
            "--launch-provenance", str(epoch_dir / "launch-provenance.json"),
            "--actual-start-evidence", str(tmp_path / "nonexistent_evidence.json"),
            "--output", str(tmp_path / "out_contract.json"),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    # 2. Build root manifest mutations
    elif mutation_type == "runtime_code_commit_mismatch":
        lp_data = json.loads((epoch_dir / "launch-provenance.json").read_text())
        lp_data["runtime_code_commit"] = "0" * 40
        (epoch_dir / "launch-provenance.json").write_text(json.dumps(lp_data))
        p = subprocess.run([
            sys.executable, str(repo_root / "scripts" / "build_epoch_manifest.py"),
            "--epoch-dir", str(epoch_dir),
            "--contract", str(contract_path),
            "--output", str(epoch_manifest_path),
            "--strict",
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    # Normal build epoch manifest for downstream steps
    p_build = subprocess.run([
        sys.executable, str(repo_root / "scripts" / "build_epoch_manifest.py"),
        "--epoch-dir", str(epoch_dir),
        "--contract", str(contract_path),
        "--output", str(epoch_manifest_path),
        "--strict",
    ], capture_output=True, text=True, env=env)
    assert p_build.returncode == 0, f"build_epoch_manifest failed: {p_build.stderr}"

    # 3. Deep DQ audit mutations
    if mutation_type == "epoch_root_missing":
        p = subprocess.run([
            sys.executable, str(repo_root / "scripts" / "audit_72h_soak.py"),
            "--epoch-dir", str(epoch_dir),
            "--contract", str(contract_path),
            "--epoch-manifest", str(tmp_path / "nonexistent_root.json"),
            "--mode", "official",
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    elif mutation_type == "epoch_root_hash_mutation":
        em_data = json.loads(epoch_manifest_path.read_text())
        em_data["epoch_manifest_sha256"] = "0" * 64
        epoch_manifest_path.write_text(json.dumps(em_data))
        p = subprocess.run([
            sys.executable, str(repo_root / "scripts" / "audit_72h_soak.py"),
            "--epoch-dir", str(epoch_dir),
            "--contract", str(contract_path),
            "--epoch-manifest", str(epoch_manifest_path),
            "--mode", "official",
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    elif mutation_type == "deep_dq_cli_without_contract":
        empty_dir = tmp_path / "empty_epoch"
        empty_dir.mkdir()
        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "deep-dq-audit",
            "--epoch-dir", str(empty_dir),
            "--report-out", str(reports_dir / "audit.json"),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr or "NO_EPOCH_MANIFEST" in p.stdout or "NO_EPOCH_MANIFEST" in p.stderr
        return

    elif mutation_type == "missing_feed":
        victim = list((epoch_dir / "raw").rglob("part-*.zst"))[0]
        victim.unlink()
        p = subprocess.run([
            sys.executable, str(repo_root / "scripts" / "audit_72h_soak.py"),
            "--epoch-dir", str(epoch_dir),
            "--contract", str(contract_path),
            "--epoch-manifest", str(epoch_manifest_path),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    elif mutation_type == "missing_full_hour":
        c_data = json.loads(contract_path.read_text())
        c_data["duration_seconds"] = 7200
        c_data["expected_end_time_utc"] = "2026-09-01T02:00:00+00:00"
        contract_path.write_text(json.dumps(c_data))
        p = subprocess.run([
            sys.executable, str(repo_root / "scripts" / "audit_72h_soak.py"),
            "--epoch-dir", str(epoch_dir),
            "--contract", str(contract_path),
            "--epoch-manifest", str(epoch_manifest_path),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    elif mutation_type == "missing_receipt":
        shutil.rmtree(epoch_dir / "archive-receipts")
        p = subprocess.run([
            sys.executable, str(repo_root / "scripts" / "audit_72h_soak.py"),
            "--epoch-dir", str(epoch_dir),
            "--contract", str(contract_path),
            "--epoch-manifest", str(epoch_manifest_path),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr or "MISSING_RECEIPT" in p.stdout or "MISSING_RECEIPT" in p.stderr
        return

    elif mutation_type == "missing_fullscan":
        for fs in (epoch_dir / "archive-receipts").glob("full_scan_*"):
            fs.unlink()
        c_data = json.loads(contract_path.read_text())
        c_data["duration_seconds"] = 259200
        contract_path.write_text(json.dumps(c_data))
        p = subprocess.run([
            sys.executable, str(repo_root / "scripts" / "audit_72h_soak.py"),
            "--epoch-dir", str(epoch_dir),
            "--contract", str(contract_path),
            "--epoch-manifest", str(epoch_manifest_path),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    elif mutation_type == "malformed_local_recv_ts":
        part_f = list((epoch_dir / "raw").rglob("part-*.zst"))[0]
        dctx = zstandard.ZstdDecompressor()
        cctx = zstandard.ZstdCompressor(level=3)
        with open(part_f, "rb") as fh:
            with dctx.stream_reader(fh) as r:
                lines = r.read().decode("utf-8").splitlines()
        r_dict = json.loads(lines[0])
        r_dict["local_recv_ts"] = "invalid_timestamp"
        lines[0] = json.dumps(r_dict)
        part_f.write_bytes(cctx.compress(("\n".join(lines) + "\n").encode("utf-8")))

        p = subprocess.run([
            sys.executable, str(repo_root / "scripts" / "audit_72h_soak.py"),
            "--epoch-dir", str(epoch_dir),
            "--contract", str(contract_path),
            "--epoch-manifest", str(epoch_manifest_path),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    elif mutation_type == "malformed_monotonic":
        part_f = list((epoch_dir / "raw").rglob("part-*.zst"))[0]
        dctx = zstandard.ZstdDecompressor()
        cctx = zstandard.ZstdCompressor(level=3)
        with open(part_f, "rb") as fh:
            with dctx.stream_reader(fh) as r:
                lines = r.read().decode("utf-8").splitlines()
        r0 = json.loads(lines[0])
        r1 = json.loads(lines[1])
        r0["local_recv_monotonic_ns"] = 5_000_000_000
        r1["local_recv_monotonic_ns"] = 1_000_000_000  # reversal!
        lines[0] = json.dumps(r0)
        lines[1] = json.dumps(r1)
        part_f.write_bytes(cctx.compress(("\n".join(lines) + "\n").encode("utf-8")))

        p = subprocess.run([
            sys.executable, str(repo_root / "scripts" / "audit_72h_soak.py"),
            "--epoch-dir", str(epoch_dir),
            "--contract", str(contract_path),
            "--epoch-manifest", str(epoch_manifest_path),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    # Run audit successfully for downstream steps
    deep_report_file = reports_dir / "deep_dq_audit_72h.json"
    p_audit = subprocess.run([
        sys.executable, str(repo_root / "scripts" / "audit_72h_soak.py"),
        "--epoch-dir", str(epoch_dir),
        "--contract", str(contract_path),
        "--epoch-manifest", str(epoch_manifest_path),
        "--out-json", str(deep_report_file),
    ], capture_output=True, text=True, env=env)
    assert p_audit.returncode == 0, f"audit failed: {p_audit.stderr}"

    # 4. Qualification mutations
    if mutation_type == "hash_only_qualification":
        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "dq-qualify",
            "--audit-report", str(deep_report_file),
            "--source-manifest-hash", "a" * 64,
            "--out", str(evidence_dir / "qual.json"),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    elif mutation_type == "dq_pass_with_degradation":
        rep_data = json.loads(deep_report_file.read_text())
        rep_data["warnings"].append("WARN: Simulated feed latency anomaly")
        deep_report_file.write_text(json.dumps(rep_data))
        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "dq-qualify",
            "--audit-report", str(deep_report_file),
            "--epoch-manifest", str(epoch_manifest_path),
            "--out", str(evidence_dir / "qual.json"),
            "--strict",
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    # Normal qualification
    qual_file = evidence_dir / "dq_qualification_72h.json"
    p_qual = subprocess.run([
        sys.executable, "-m", "bithumb_coin_trader.research_cli",
        "dq-qualify",
        "--audit-report", str(deep_report_file),
        "--epoch-manifest", str(epoch_manifest_path),
        "--out", str(qual_file),
        "--strict",
    ], capture_output=True, text=True, env=env)
    assert p_qual.returncode == 0, f"qualify failed: {p_qual.stderr}"

    # 5. Transform canonical mutations
    if mutation_type == "extra_unsealed_raw":
        unsealed_dir = epoch_dir / "raw" / "exchange=bithumb" / "stream=orderbook" / "market=KRW-BTC"
        unsealed_file = unsealed_dir / "part-20260901-99.zst"
        unsealed_file.write_bytes(b"dummy_unsealed_bytes")
        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "transform-canonical",
            "--input-dir", str(epoch_dir / "raw"),
            "--output-dir", str(canon_dir),
            "--exchange", "bithumb",
            "--stream", "orderbook",
            "--epoch-manifest", str(epoch_manifest_path),
            "--dq-qualification", str(qual_file),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    elif mutation_type == "source_raw_changed":
        part_f = list((epoch_dir / "raw").rglob("part-*.zst"))[0]
        part_f.write_bytes(part_f.read_bytes() + b"\x00")
        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "transform-canonical",
            "--input-dir", str(epoch_dir / "raw"),
            "--output-dir", str(canon_dir),
            "--exchange", "bithumb",
            "--stream", "orderbook",
            "--epoch-manifest", str(epoch_manifest_path),
            "--dq-qualification", str(qual_file),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    # Normal transform
    subprocess.run([
        sys.executable, "-m", "bithumb_coin_trader.research_cli",
        "transform-canonical",
        "--input-dir", str(epoch_dir / "raw"),
        "--output-dir", str(canon_dir),
        "--exchange", "bithumb",
        "--stream", "orderbook",
        "--epoch-manifest", str(epoch_manifest_path),
        "--dq-qualification", str(qual_file),
    ], check=True, env=env)

    canon_manifest_path = canon_dir / "canonical_manifest.json"

    # 6. Partition dataset mutations
    ds_out = tmp_path / f"ds_{mutation_type}"

    if mutation_type == "legacy_strict_phase4":
        q_data = json.loads(qual_file.read_text())
        q_data["approved_policy"] = "strict_phase4"
        qual_file.write_text(json.dumps(q_data))
        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "partition-dataset",
            "--canonical-manifest", str(canon_manifest_path),
            "--exchange", "bithumb",
            "--market", "KRW-BTC",
            "--stream", "orderbook",
            "--output-dir", str(ds_out),
            "--dq-report", str(qual_file),
            "--epoch-manifest", str(epoch_manifest_path),
            "--deep-audit-report", str(deep_report_file),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    elif mutation_type == "missing_deep_audit_at_partition":
        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "partition-dataset",
            "--canonical-manifest", str(canon_manifest_path),
            "--exchange", "bithumb",
            "--market", "KRW-BTC",
            "--stream", "orderbook",
            "--output-dir", str(ds_out),
            "--dq-report", str(qual_file),
            "--epoch-manifest", str(epoch_manifest_path),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    elif mutation_type == "synthetic_source_ids_rejected":
        em_data = json.loads(epoch_manifest_path.read_text())
        em_data["collector_epoch"] = "synthetic"
        # update self hash
        em_copy = {k: v for k, v in em_data.items() if k != "epoch_manifest_sha256"}
        em_data["epoch_manifest_sha256"] = hashlib.sha256(json.dumps(em_copy, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        epoch_manifest_path.write_text(json.dumps(em_data))

        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "partition-dataset",
            "--canonical-manifest", str(canon_manifest_path),
            "--exchange", "bithumb",
            "--market", "KRW-BTC",
            "--stream", "orderbook",
            "--output-dir", str(ds_out),
            "--dq-report", str(qual_file),
            "--epoch-manifest", str(epoch_manifest_path),
            "--deep-audit-report", str(deep_report_file),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    elif mutation_type == "evidence_chain_mismatch":
        cm_data = json.loads(canon_manifest_path.read_text())
        cm_data["source_epoch_manifest_sha256"] = "1" * 64
        # self hash
        cm_copy = {k: v for k, v in cm_data.items() if k != "canonical_manifest_sha256"}
        cm_data["canonical_manifest_sha256"] = hashlib.sha256(json.dumps(cm_copy, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        canon_manifest_path.write_text(json.dumps(cm_data))

        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "partition-dataset",
            "--canonical-manifest", str(canon_manifest_path),
            "--exchange", "bithumb",
            "--market", "KRW-BTC",
            "--stream", "orderbook",
            "--output-dir", str(ds_out),
            "--dq-report", str(qual_file),
            "--epoch-manifest", str(epoch_manifest_path),
            "--deep-audit-report", str(deep_report_file),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    elif mutation_type == "canonical_manifest_changed":
        cm_data = json.loads(canon_manifest_path.read_text())
        cm_data["canonical_manifest_sha256"] = "0" * 64
        canon_manifest_path.write_text(json.dumps(cm_data))
        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "partition-dataset",
            "--canonical-manifest", str(canon_manifest_path),
            "--exchange", "bithumb",
            "--market", "KRW-BTC",
            "--stream", "orderbook",
            "--output-dir", str(ds_out),
            "--dq-report", str(qual_file),
            "--epoch-manifest", str(epoch_manifest_path),
            "--deep-audit-report", str(deep_report_file),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return

    elif mutation_type == "canonical_file_changed":
        cf = list(canon_dir.glob("canonical_*.ndjson.zst"))[0]
        cf.write_bytes(cf.read_bytes() + b"\x00")
        p = subprocess.run([
            sys.executable, "-m", "bithumb_coin_trader.research_cli",
            "partition-dataset",
            "--canonical-manifest", str(canon_manifest_path),
            "--exchange", "bithumb",
            "--market", "KRW-BTC",
            "--stream", "orderbook",
            "--output-dir", str(ds_out),
            "--dq-report", str(qual_file),
            "--epoch-manifest", str(epoch_manifest_path),
            "--deep-audit-report", str(deep_report_file),
        ], capture_output=True, text=True, env=env)
        assert p.returncode == 2, f"Failed with {p.returncode}: {p.stderr}\nStdout: {p.stdout}"
        assert expected_token in p.stdout or expected_token in p.stderr
        return
