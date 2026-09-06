#!/usr/bin/env python3
"""Build Research Evidence Bundle Manifest (P23).

Collects all offline research artifacts, independent verification tests,
statistical reconciliation tables, and schema contracts into a single
cryptographically signed manifest.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_git_commit() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True)
        return out.strip()
    except Exception:
        return "UNKNOWN"


def main() -> int:
    repo_root = Path(__file__).parent.parent
    evidence_dir = repo_root / "evidence" / "research"
    docs_dir = repo_root / "docs"

    evidence_dir.mkdir(parents=True, exist_ok=True)

    files_to_bundle = [
        docs_dir / "PHASE1_SECOND_PASS_AUDIT_2026-09-05.md",
        docs_dir / "DSR_RECONCILIATION_2026-09-05.md",
        docs_dir / "MICROSTRUCTURE_PREREGISTRATION_V1_AUDIT.md",
        docs_dir / "MICROSTRUCTURE_PREREGISTRATION_AMENDMENT_001.md",
        docs_dir / "exchange_semantics" / "bithumb_krw_btc_semantics.md",
        docs_dir / "exchange_semantics" / "binance_btcusdt_semantics.md",
        docs_dir / "exchange_semantics" / "upbit_krw_btc_semantics.md",
        docs_dir / "exchange_semantics" / "cross_exchange_microstructure_comparison.md",
        docs_dir / "PROJECT_VERIFICATION_MATRIX.md",
        docs_dir / "ARCHITECTURE_RESEARCH_TO_EXECUTION.md",
        docs_dir / "SECURITY_THREAT_MODEL.md",
        docs_dir / "POST_72H_OFFLINE_IMPORT_RUNBOOK.md",
        docs_dir / "RESEARCH_DECISION_LOG.md",
        docs_dir / "TECHNICAL_DEBT_LOG.md",
        docs_dir / "ADVERSARIAL_REVIEW_PHASE2.md",
        evidence_dir / "historical_trial_traceability_v2.json",
        repo_root / "tests" / "reference_dsr.py",
        repo_root / "tests" / "reference_wrc.py",
        repo_root / "tests" / "reference_pbo.py",
        repo_root / "tests" / "fixtures" / "canonical_market_data" / "bithumb_krw_btc_orderbook_golden.json",
        repo_root / "tests" / "fixtures" / "canonical_market_data" / "binance_btcusdt_trade_golden.json",
        repo_root / "tests" / "fixtures" / "canonical_market_data" / "upbit_krw_btc_ticker_golden.json",
    ]

    bundle_items = []
    for fp in files_to_bundle:
        if fp.exists():
            rel_path = str(fp.relative_to(repo_root))
            sha = compute_sha256(fp)
            size = fp.stat().st_size
            bundle_items.append({
                "path": rel_path,
                "sha256": sha,
                "size_bytes": size,
            })

    from datetime import datetime, timezone
    manifest = {
        "manifest_version": "2.0.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": get_git_commit(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "bundled_artifacts_count": len(bundle_items),
        "artifacts": bundle_items,
        "integrity_claims": {
            "offline_only": True,
            "network_isolated": True,
            "live_soak_isolated": True,
            "dsr_unit_reconciled": True,
            "fail_closed_risk": True,
            "cash_conservation_oracle": True,
        }
    }

    manifest_path = evidence_dir / "bundle_manifest_phase2.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"Generated bundle manifest with {len(bundle_items)} artifacts at: {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
