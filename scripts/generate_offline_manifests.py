"""Generate or repair manifests for closed UTC-hour raw partitions.

The writer partitions by ``local_write_ts`` UTC hour. A file is therefore
finalized only after its filename hour is strictly older than the current UTC
hour. Inactivity/mtime is not a safe finalization signal for quiet markets.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any

from bithumb_coin_trader.microstructure_storage import RawMicrostructureStorage

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "microstructure"
RAW_DIR = DATA_DIR / "raw"
MANIFESTS_DIR = DATA_DIR / "manifests"


def partition_hour(path: Path) -> datetime:
    date_part, hour_part = path.stem.rsplit("_", 2)[-2:]
    return datetime.strptime(f"{date_part}_{hour_part}", "%Y-%m-%d_%H").replace(tzinfo=timezone.utc)


def is_closed_partition(path: Path, now: datetime) -> bool:
    current_hour = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return partition_hour(path) < current_hour


def manifest_path(raw_path: Path) -> Path:
    return MANIFESTS_DIR / f"manifest_{raw_path.stem}.json"


def manifest_matches_raw(raw_path: Path, candidate: Path) -> bool:
    try:
        payload: dict[str, Any] = json.loads(candidate.read_text(encoding="utf-8"))
        required_fields = {
            "monotonic_missing_count",
            "monotonic_invalid_count",
            "monotonic_reversal_count",
            "latency_parseable_observation_count",
            "latency_out_of_range_count",
            "exchange_timestamp_present_count",
        }
        return (
            payload.get("partition_path") == str(raw_path.relative_to(ROOT / "data"))
            and payload.get("schema_version") == 4
            and required_fields.issubset(payload)
            and payload.get("bytes") == raw_path.stat().st_size
            and isinstance(payload.get("sha256"), str)
            and len(payload["sha256"]) == 64
            and candidate.stat().st_mtime_ns >= raw_path.stat().st_mtime_ns
        )
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return False


def collector_running() -> bool:
    result = subprocess.run(
        ["pgrep", "-f", "scripts/run_cross_market_collector.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    return any(value.isdigit() for value in result.stdout.split())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Inventory only; do not hash or write manifests")
    parser.add_argument(
        "--rehash-all",
        action="store_true",
        help="Re-read, re-hash, and replace every closed-hour manifest",
    )
    parser.add_argument(
        "--include-current-hour",
        action="store_true",
        help="Include current UTC-hour partitions only after the collector has stopped",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    storage = RawMicrostructureStorage(RAW_DIR)
    raw_files = sorted(RAW_DIR.glob("**/*.jsonl"))
    if args.include_current_hour and collector_running():
        parser.error("--include-current-hour requires the collector process to be stopped")
    finalized = raw_files if args.include_current_hour else [path for path in raw_files if is_closed_partition(path, now)]
    missing: list[Path] = []
    stale: list[Path] = []
    current: list[Path] = []
    for path in finalized:
        candidate = manifest_path(path)
        if not candidate.exists():
            missing.append(path)
        elif not manifest_matches_raw(path, candidate):
            stale.append(path)
        else:
            current.append(path)

    print("=" * 80)
    print("  STRATEGY V9: CLOSED-HOUR OFFLINE MANIFEST GENERATOR")
    print("=" * 80)
    print(f"Total raw files          : {len(raw_files)}")
    print(f"Closed UTC-hour files    : {len(finalized)}")
    print(f"Current manifests        : {len(current)}")
    print(f"Missing manifests        : {len(missing)}")
    print(f"Stale/invalid manifests  : {len(stale)}")

    if args.dry_run:
        print("Dry run: no files written.")
        return

    generated = 0
    failed = 0
    targets = finalized if args.rehash_all else missing + stale
    for path in targets:
        try:
            storage.generate_partition_manifest(path)
            generated += 1
            if generated % 100 == 0:
                print(f"Generated/repaired {generated} manifests...")
        except Exception as error:
            failed += 1
            print(f"FAILED {path}: {error}")
    print(f"Generated/repaired       : {generated}")
    print(f"Failed                   : {failed}")


if __name__ == "__main__":
    main()
