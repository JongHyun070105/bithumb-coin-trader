"""Generate manifests offline for finalized partitions without touching running collector."""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

from bithumb_coin_trader.microstructure_storage import RawMicrostructureStorage

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "microstructure"
RAW_DIR = DATA_DIR / "raw"
MANIFESTS_DIR = DATA_DIR / "manifests"


def main() -> None:
    print("=" * 80)
    print("  STRATEGY V9: OFFLINE MANIFEST GENERATOR FOR FINALIZED PARTITIONS")
    print("=" * 80)

    now = time.time()
    storage = RawMicrostructureStorage(RAW_DIR)
    raw_files = list(RAW_DIR.glob("**/*.jsonl"))

    # Only process finalized files (mtime > 10 minutes ago) and missing manifest
    finalized = [f for f in raw_files if (now - f.stat().st_mtime) >= 600]
    print(f"Total raw files: {len(raw_files)}")
    print(f"Finalized partitions eligible for manifest: {len(finalized)}")

    generated = 0
    skipped = 0

    for f in finalized:
        manifest_file = MANIFESTS_DIR / f"manifest_{f.stem}.json"
        if manifest_file.exists() and manifest_file.stat().st_size > 0:
            skipped += 1
            continue

        try:
            storage.generate_partition_manifest(f)
            generated += 1
            if generated % 100 == 0:
                print(f"  - Generated {generated} manifests...")
        except Exception as e:
            print(f"  - Failed {f.name}: {e}")

    print(f"\nManifest Generation Complete:")
    print(f"  - Newly Generated: {generated}")
    print(f"  - Already Existed: {skipped}")
    print(f"  - Total Manifests Now: {len(list(MANIFESTS_DIR.glob('*.json')))}")
    print("=" * 80)


if __name__ == "__main__":
    main()
