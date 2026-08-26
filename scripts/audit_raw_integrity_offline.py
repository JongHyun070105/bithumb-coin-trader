"""Offline validator for microstructure raw files & quarantine events."""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "microstructure"
RAW_DIR = DATA_DIR / "raw"
QUARANTINE_DIR = DATA_DIR / "quarantine"


def main() -> None:
    print("=" * 80)
    print("  STRATEGY V9: OFFLINE RAW DATA INTEGRITY & QUARANTINE AUDIT")
    print("=" * 80)

    now = time.time()
    raw_files = list(RAW_DIR.glob("**/*.jsonl"))
    quarantine_files = list(QUARANTINE_DIR.glob("**/*.jsonl")) if QUARANTINE_DIR.exists() else []

    zero_byte_files = []
    active_files = []
    finalized_files = []
    malformed_lines = 0
    total_valid_records = 0

    print(f"Total Raw Files Found: {len(raw_files)}")
    print(f"Total Quarantine Files Found: {len(quarantine_files)}")

    # Check quarantine files
    total_quarantined_records = 0
    quarantine_reasons: dict[str, int] = {}
    for qf in quarantine_files:
        with qf.open("r", encoding="utf-8") as qh:
            for line in qh:
                if line.strip():
                    total_quarantined_records += 1
                    try:
                        rec = json.loads(line)
                        reason = rec.get("error_reason", "unknown")
                        quarantine_reasons[reason] = quarantine_reasons.get(reason, 0) + 1
                    except Exception:
                        pass

    print(f"\n[Quarantine Storage Check]")
    print(f"  - Quarantined Records Total : {total_quarantined_records}")
    print(f"  - Quarantine Error Reasons  : {quarantine_reasons}")

    # Inspect all raw files
    for f in raw_files:
        st = f.stat()
        if st.st_size == 0:
            zero_byte_files.append(str(f.relative_to(RAW_DIR)))
            continue

        # Active if modified within last 10 minutes
        if (now - st.st_mtime) < 600:
            active_files.append(f)
        else:
            finalized_files.append(f)

    print(f"\n[Partition Classification]")
    print(f"  - Active Partitions (mod < 10m)    : {len(active_files)}")
    print(f"  - Finalized Partitions (closed)    : {len(finalized_files)}")
    print(f"  - Zero-Byte Empty Files            : {len(zero_byte_files)}")

    # Sample check 50 finalized files for malformed lines
    sample_finalized = finalized_files[:50]
    for ff in sample_finalized:
        with ff.open("r", encoding="utf-8") as h:
            for idx, line in enumerate(h):
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    json.loads(line_str)
                    total_valid_records += 1
                except Exception:
                    malformed_lines += 1

    print(f"\n[JSONL Line-by-Line Integrity Sampling]")
    print(f"  - Sampled Finalized Partitions Checked : {len(sample_finalized)}")
    print(f"  - Valid Parsed Records in Sample       : {total_valid_records:,}")
    print(f"  - Malformed / Truncated Lines Found    : {malformed_lines}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
