"""Bounded, evidence-labelled status monitor for the Strategy V9 collector.

This command is intentionally not a data-integrity audit. It reads file
metadata plus bounded tails from a balanced set of recent partitions. Every
derived value is labelled as measured, estimated, sampled, or not verifiable.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "microstructure"
RAW_DIR = DATA_DIR / "raw"
MANIFESTS_DIR = DATA_DIR / "manifests"
QUARANTINE_DIR = DATA_DIR / "quarantine"
METRICS_FILE = DATA_DIR / "collector_metrics.json"


def get_collector_pids() -> list[int]:
    """Return all matching collector PIDs so duplicate processes are visible."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "scripts/run_cross_market_collector.py"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    return sorted({int(value) for value in result.stdout.split() if value.isdigit()})


def tail_lines(
    path: Path,
    limit: int,
    *,
    block_size: int = 64 * 1024,
    max_bytes: int = 4 * 1024 * 1024,
) -> list[str]:
    """Read at most ``limit`` complete trailing lines without ``readlines()``."""
    if limit <= 0 or path.stat().st_size == 0:
        return []
    chunks: deque[bytes] = deque()
    with path.open("rb") as handle:
        position = handle.seek(0, 2)
        newline_count = 0
        bytes_read = 0
        while position > 0 and newline_count <= limit and bytes_read < max_bytes:
            size = min(block_size, position, max_bytes - bytes_read)
            position -= size
            handle.seek(position)
            chunk = handle.read(size)
            chunks.appendleft(chunk)
            newline_count += chunk.count(b"\n")
            bytes_read += size
    decoded = b"".join(chunks).decode("utf-8", errors="replace").splitlines()
    return decoded[-limit:]


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.floor(len(ordered) * fraction))
    return ordered[index]


def relative_group(path: Path) -> tuple[str, str]:
    parts = path.relative_to(RAW_DIR).parts
    return (parts[1].lower(), parts[2].lower()) if len(parts) >= 4 else ("unknown", "unknown")


def is_closed_hour_partition(path: Path, now: datetime) -> bool:
    try:
        date_part, hour_part = path.stem.rsplit("_", 2)[-2:]
        partition = datetime.strptime(f"{date_part}_{hour_part}", "%Y-%m-%d_%H").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return False
    current_hour = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return partition < current_hour


def manifest_coverage(
    raw_files: Iterable[Path],
    manifest_files: Iterable[Path],
    now: datetime,
    *,
    collector_is_running: bool = True,
) -> dict[str, int]:
    closed = {
        path.stem: path
        for path in raw_files
        if not collector_is_running or is_closed_hour_partition(path, now)
    }
    manifests: dict[str, tuple[Path, dict[str, Any] | None]] = {}
    invalid = 0
    for path in manifest_files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            payload = None
            invalid += 1
        if payload is not None and not isinstance(payload, dict):
            payload = None
            invalid += 1
        manifests[path.stem.removeprefix("manifest_")] = (path, payload)
    missing = 0
    stale = 0
    required_manifest_fields = {
        "monotonic_missing_count",
        "monotonic_invalid_count",
        "monotonic_reversal_count",
        "latency_parseable_observation_count",
        "latency_out_of_range_count",
        "exchange_timestamp_present_count",
    }
    for stem, raw_path in closed.items():
        candidate = manifests.get(stem)
        if candidate is None:
            missing += 1
            continue
        payload = candidate[1]
        if (
            payload is None
            or payload.get("schema_version") != 4
            or not required_manifest_fields.issubset(payload)
            or payload.get("bytes") != raw_path.stat().st_size
            or payload.get("partition_path") != str(raw_path.relative_to(ROOT / "data"))
            or candidate[0].stat().st_mtime < raw_path.stat().st_mtime
        ):
            stale += 1
    orphan = sum(1 for stem in manifests if stem not in closed)
    return {
        "closed_raw": len(closed),
        "covered": len(closed) - missing - stale,
        "missing": missing,
        "stale_or_mismatch": stale,
        "orphan_or_active_hour": orphan,
        "invalid_manifest_json": invalid,
    }


def balanced_recent_files(
    raw_files: Iterable[Path], *, files_per_group: int = 2, now_ts: float | None = None, recent_seconds: int = 7200
) -> dict[tuple[str, str], list[Path]]:
    now_ts = time.time() if now_ts is None else now_ts
    grouped: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for path in raw_files:
        grouped[relative_group(path)].append(path)
    return {
        group: [
            path
            for path in sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True)
            if now_ts - path.stat().st_mtime <= recent_seconds
        ][:files_per_group]
        for group, paths in grouped.items()
    }


def sample_clock_offsets(
    raw_files: Iterable[Path], *, files_per_group: int = 2, records_per_file: int = 200,
    now_ts: float | None = None, recent_seconds: int = 7200
) -> dict[tuple[str, str], dict[str, Any]]:
    """Sample exchange/local clock differences evenly by exchange and stream."""
    output: dict[tuple[str, str], dict[str, Any]] = {}
    now_ts = time.time() if now_ts is None else now_ts
    for group, files in balanced_recent_files(
        raw_files, files_per_group=files_per_group, now_ts=now_ts, recent_seconds=recent_seconds
    ).items():
        values: list[float] = []
        offset_out_of_range_count = 0
        parse_errors = 0
        timestamp_missing = 0
        for path in files:
            for line in tail_lines(path, records_per_file):
                try:
                    record = json.loads(line)
                    exchange_ts = record.get("exchange_ts")
                    local_ts = record.get("local_recv_ts")
                    if not exchange_ts or not local_ts:
                        timestamp_missing += 1
                        continue
                    delta = (
                        datetime.fromisoformat(local_ts) - datetime.fromisoformat(exchange_ts)
                    ).total_seconds() * 1000.0
                    if math.isfinite(delta):
                        values.append(delta)
                        if not -60_000.0 <= delta <= 60_000.0:
                            offset_out_of_range_count += 1
                except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
                    parse_errors += 1
        output[group] = {
            "sample_count": len(values),
            "offset_out_of_range_count": offset_out_of_range_count,
            "files_sampled": len(files),
            "newest_append_age_seconds": min(
                (max(0.0, now_ts - path.stat().st_mtime) for path in files), default=None
            ),
            "records_per_file_limit": records_per_file,
            "missing_exchange_or_local_timestamp": timestamp_missing,
            "parse_errors": parse_errors,
            "p50_ms": percentile(values, 0.50),
            "p90_ms": percentile(values, 0.90),
            "p95_ms": percentile(values, 0.95),
            "negative_fraction": (sum(value < 0 for value in values) / len(values)) if values else None,
        }
    return output


def load_metrics_snapshot(
    path: Path = METRICS_FILE, *, now: datetime | None = None, max_age_seconds: float = 15.0
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if not path.exists():
        return {"status": "MISSING"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return {"status": "MALFORMED"}
    if not isinstance(payload, dict):
        return {"status": "INVALID_SCHEMA"}
    written_at_value = payload.get("written_at")
    collector_run_id = payload.get("collector_run_id")
    process_id = payload.get("process_id")
    exchanges = payload.get("exchanges")
    if (
        payload.get("schema_version") != 1
        or not isinstance(written_at_value, str)
        or not isinstance(collector_run_id, str)
        or not collector_run_id
        or not isinstance(process_id, int)
        or process_id <= 0
        or not isinstance(exchanges, dict)
        or not all(isinstance(metric, dict) for metric in exchanges.values())
    ):
        return {"status": "INVALID_SCHEMA"}
    try:
        written_at = datetime.fromisoformat(written_at_value)
        if written_at.tzinfo is None:
            return {"status": "INVALID_SCHEMA"}
        age = max(0.0, (now - written_at).total_seconds())
    except (TypeError, ValueError):
        return {"status": "INVALID_SCHEMA"}
    return {
        "status": "FRESH" if age <= max_age_seconds else "STALE",
        "age_seconds": age,
        "payload": payload,
    }


def metrics_counter_mode(snapshot: dict[str, Any], collector_pids: list[int]) -> str | None:
    """Classify valid counters as live only when one PID matches the snapshot."""
    if snapshot.get("status") not in {"FRESH", "STALE"}:
        return None
    payload = snapshot["payload"]
    if (
        snapshot["status"] == "FRESH"
        and len(collector_pids) == 1
        and payload["process_id"] == collector_pids[0]
    ):
        return "LIVE"
    return "HISTORICAL"


def projected_free_at_target(
    *, current_free_bytes: int, total_bytes: int, elapsed_hours: float, target_hours: float
) -> tuple[int, int]:
    """Return projected total and free bytes without double-counting stored data."""
    if elapsed_hours <= 0:
        return total_bytes, current_free_bytes
    projected_total = int((total_bytes / elapsed_hours) * target_hours)
    future_required = max(0, projected_total - total_bytes)
    return projected_total, current_free_bytes - future_required


def main() -> None:
    now_ts = time.time()
    now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    print("=" * 80)
    print("  STRATEGY V9: BOUNDED COLLECTOR STATUS (NOT A FULL INTEGRITY AUDIT)")
    print(f"  Timestamp: {now_dt.isoformat()}")
    print("=" * 80)

    if not RAW_DIR.exists():
        print("No raw microstructure directory found.")
        return

    pids = get_collector_pids()
    raw_files = list(RAW_DIR.glob("**/*.jsonl"))
    manifest_files = list(MANIFESTS_DIR.glob("*.json")) if MANIFESTS_DIR.exists() else []
    quarantine_files = list(QUARANTINE_DIR.glob("**/*.jsonl")) if QUARANTINE_DIR.exists() else []
    stats = [(path, path.stat()) for path in raw_files]
    total_bytes = sum(stat.st_size for _, stat in stats)
    first_created = min((stat.st_birthtime for _, stat in stats), default=now_ts)
    latest_mtime = max((stat.st_mtime for _, stat in stats), default=now_ts)
    elapsed_hours = max(0.001, (latest_mtime - first_created) / 3600.0)
    stale_seconds = max(0.0, now_ts - latest_mtime)

    print("\n[1. Process and append status — MEASURED]")
    print(f"  - Matching collector PIDs   : {pids or 'NONE'}")
    print(f"  - Duplicate process status  : {'PASS' if len(pids) <= 1 else 'FAIL'}")
    print(f"  - Seconds since last append : {stale_seconds:.1f}")
    print(f"  - Raw append activity       : {'ACTIVE' if stale_seconds < 120 else 'STALE'}")
    metrics_snapshot = load_metrics_snapshot(now=now_dt)
    print(f"  - Durable metrics snapshot  : {metrics_snapshot['status']}")
    counter_label = metrics_counter_mode(metrics_snapshot, pids)
    if counter_label is not None:
        metrics_payload = metrics_snapshot["payload"]
        print(f"  - Metrics age seconds       : {metrics_snapshot['age_seconds']:.1f}")
        print(f"  - Metrics collector run ID  : {metrics_payload.get('collector_run_id', 'UNKNOWN')}")
        if counter_label == "HISTORICAL":
            print("  - Current epoch counters    : NOT-VERIFIABLE; snapshot is not tied to exactly one live PID")
        for exchange, metric in sorted(metrics_payload["exchanges"].items()):
            print(
                f"  - {exchange:8s} counters ({counter_label}): disconnects={metric.get('disconnect_count')}, "
                f"reconnects={metric.get('reconnect_count')}, drops={metric.get('queue_dropped_events')}, "
                f"backpressure={metric.get('queue_backpressure_events')}, writer_errors={metric.get('writer_errors')}"
            )
    else:
        print("  - Queue/reconnect counters  : NOT-VERIFIABLE from current epoch")

    latest_manifest_mtime = max((path.stat().st_mtime for path in manifest_files), default=0.0)
    coverage = manifest_coverage(raw_files, manifest_files, now_dt, collector_is_running=bool(pids))
    print("\n[2. Manifest status — MEASURED METADATA ONLY]")
    print(f"  - Raw partitions            : {len(raw_files)}")
    print(f"  - Closed UTC-hour raw       : {coverage['closed_raw']}")
    print(f"  - Manifest files            : {len(manifest_files)}")
    print(f"  - Closed raw covered        : {coverage['covered']}")
    print(f"  - Missing manifests         : {coverage['missing']}")
    print(f"  - Stale/path-size mismatch  : {coverage['stale_or_mismatch']}")
    print(f"  - Active-hour/orphan        : {coverage['orphan_or_active_hour']}")
    print(f"  - Invalid manifest JSON     : {coverage['invalid_manifest_json']}")
    print(
        "  - Latest manifest mtime     : "
        + (datetime.fromtimestamp(latest_manifest_mtime, tz=timezone.utc).isoformat() if latest_manifest_mtime else "NONE")
    )

    mib_per_hour = (total_bytes / 1024**2) / elapsed_hours
    gib_per_day = mib_per_hour * 24 / 1024
    print("\n[3. Storage throughput — ESTIMATED FROM BYTES / FILE-LIFETIME]")
    print(f"  - Total raw bytes           : {total_bytes:,} ({total_bytes / 1024**3:.2f} GiB)")
    print(f"  - File-lifetime span        : {elapsed_hours:.2f} hours")
    print(f"  - Estimated ingestion rate  : {mib_per_hour:.2f} MiB/hour ({gib_per_day:.2f} GiB/day)")
    print("  - Event count/rate          : NOT-MEASURED by this bounded status command")

    group_bytes: dict[tuple[str, str], int] = defaultdict(int)
    for path, stat in stats:
        group_bytes[relative_group(path)] += stat.st_size
    print("\n[4. Exchange/stream contribution — MEASURED BYTES]")
    for (exchange, stream), byte_count in sorted(group_bytes.items(), key=lambda item: item[1], reverse=True):
        fraction = (byte_count / total_bytes * 100) if total_bytes else 0.0
        print(f"  - {exchange:8s}/{stream:10s}: {byte_count / 1024**2:9.1f} MiB ({fraction:5.1f}%)")

    print("\n[5. Exchange/local timestamp difference — BALANCED SAMPLE]")
    print("  Values combine clock offset, event timestamp semantics, network, and queue delay.")
    samples = sample_clock_offsets(raw_files)
    for group in sorted(samples):
        sample = samples[group]
        prefix = f"{group[0]:8s}/{group[1]:10s}"
        if sample["sample_count"] == 0:
            print(
                f"  - {prefix}: NOT-AVAILABLE; files={sample['files_sampled']}, "
                f"newest_age={sample['newest_append_age_seconds']}, "
                f"missing_ts={sample['missing_exchange_or_local_timestamp']}, parse_errors={sample['parse_errors']}"
            )
            continue
        print(
            f"  - {prefix}: N={sample['sample_count']:4d}, p50={sample['p50_ms']:.1f}ms, "
                f"p90={sample['p90_ms']:.1f}ms, p95={sample['p95_ms']:.1f}ms, "
                f"negative={sample['negative_fraction'] * 100:.1f}%, "
                f"offset_outliers={sample['offset_out_of_range_count']}, "
                f"newest_age={sample['newest_append_age_seconds']:.1f}s"
        )

    disk = shutil.disk_usage(ROOT)
    projected_total, projected_free = projected_free_at_target(
        current_free_bytes=disk.free,
        total_bytes=total_bytes,
        elapsed_hours=elapsed_hours,
        target_hours=72.0,
    )
    future_required = max(0, projected_total - total_bytes)
    hours_to_full = (disk.free / (mib_per_hour * 1024**2)) if mib_per_hour > 0 else math.inf
    print("\n[6. Disk projection — ESTIMATED; GiB units]")
    print(f"  - Current free              : {disk.free / 1024**3:.1f} GiB")
    print(f"  - Projected 72h total raw   : {projected_total / 1024**3:.1f} GiB")
    print(f"  - Future bytes still needed : {future_required / 1024**3:.1f} GiB")
    print(f"  - Projected free at 72h     : {projected_free / 1024**3:.1f} GiB")
    print(f"  - Estimated hours to full   : {hours_to_full:.1f}")

    print("\n[7. Integrity scope]")
    print(f"  - Quarantine files          : {len(quarantine_files)} (file count only)")
    print("  - Malformed/duplicates/SHA  : NOT-VERIFIABLE here; run the offline audit")
    print("  - Exchange feed completeness: NOT-DIRECTLY-VERIFIABLE")
    print("  - Alpha research ready      : false")
    print("  - Live trading ready        : false")
    print("=" * 80)


if __name__ == "__main__":
    main()
