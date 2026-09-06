#!/usr/bin/env python3
"""Unified Mid-Soak Monitor, Disk Forecaster, and Backlog Tracker.

Implements Sections 43, 44, and 45 of the 72H post-soak specification:
- Section 43: Mid-soak checkpoint tool (T+1h, T+6h, T+12h, T+24h, T+48h)
- Section 44: Live disk usage forecast & threshold ETA calculator
- Section 45: Archive & Full-scan backlog monitor
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any


class SoakProgressMonitor:
    def __init__(self, epoch_dir: Path, target_duration_sec: float = 259200.0):
        self.epoch_dir = epoch_dir
        self.target_duration_sec = target_duration_sec
        self.raw_dir = epoch_dir / "raw"
        self.manifests_dir = epoch_dir / "manifests"
        self.receipts_dir = epoch_dir / "archive-receipts"
        self.metrics_file = epoch_dir / "collector_metrics.json"

    def collect_checkpoint(self) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
        if self.metrics_file.exists():
            try:
                metrics = json.loads(self.metrics_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        uptime_sec = metrics.get("uptime_seconds", 0.0)
        recs = metrics.get("total_records_persisted", 0)
        raw_bytes = metrics.get("total_bytes_persisted", 0)

        # Disk usage
        total, used, free = shutil.disk_usage(self.epoch_dir if self.epoch_dir.exists() else Path("/"))
        used_pct = (used / total) * 100.0 if total > 0 else 0.0
        total_gib = total / (1024 ** 3)
        used_gib = used / (1024 ** 3)
        free_gib = free / (1024 ** 3)

        # Disk forecast (Section 44)
        rate_gib_per_hour = (raw_bytes / (1024 ** 3)) / (uptime_sec / 3600.0) if uptime_sec > 300 else 0.0
        remaining_sec = max(0.0, self.target_duration_sec - uptime_sec)
        remaining_hours = remaining_sec / 3600.0
        projected_additional_gib = rate_gib_per_hour * remaining_hours
        projected_total_gib = used_gib + projected_additional_gib
        projected_pct = (projected_total_gib / total_gib) * 100.0 if total_gib > 0 else 0.0

        if projected_pct >= 90.0:
            disk_safety_status = "CRITICAL"
        elif projected_pct >= 70.0:
            disk_safety_status = "WARNING"
        else:
            disk_safety_status = "SAFE"

        # Backlog tracking (Section 45)
        # Parse archive receipts and full-scan reports
        receipt_files = list(self.receipts_dir.glob("**/receipt_*.json")) if self.receipts_dir.exists() else []
        full_scan_reports = list(self.receipts_dir.glob("**/full_scan_*_report.json")) if self.receipts_dir.exists() else []

        completed_scans = 0
        failed_scans = 0
        for fs in full_scan_reports:
            try:
                d = json.loads(fs.read_text(encoding="utf-8"))
                if d.get("status") == "PASS":
                    completed_scans += 1
                else:
                    failed_scans += 1
            except Exception:
                failed_scans += 1

        # Check running flock
        lock_file = self.receipts_dir / ".full_scan_runner.lock"
        running_scans = 1 if lock_file.exists() else 0

        # Pending scans estimation: cohorts in receipts without terminal report
        receipt_hours = {r.stem.split("_")[-2] for r in receipt_files if len(r.stem.split("_")) >= 2}
        scanned_hours = {fs.stem.replace("full_scan_", "").replace("_report", "") for fs in full_scan_reports}
        pending_scans = max(0, len(receipt_hours - scanned_hours))

        if failed_scans > 0:
            backlog_status = "STALLED"
        elif pending_scans > 2:
            backlog_status = "SLOW"
        else:
            backlog_status = "NORMAL"

        return {
            "checkpoint_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "epoch": self.epoch_dir.name,
            "uptime_seconds": uptime_sec,
            "uptime_hours": round(uptime_sec / 3600.0, 2),
            "progress_percent": round((uptime_sec / self.target_duration_sec) * 100.0, 2),
            "records": recs,
            "bytes_persisted": raw_bytes,
            "writer_errors": metrics.get("writer_errors", 0),
            "queue_drops": metrics.get("queue_drops", 0),
            "unpersisted": metrics.get("unpersisted_events", 0),
            "reconnects": metrics.get("websocket_reconnects", 0),
            "disk": {
                "total_gib": round(total_gib, 2),
                "used_gib": round(used_gib, 2),
                "free_gib": round(free_gib, 2),
                "used_percent": round(used_pct, 2),
                "growth_rate_gib_per_hour": round(rate_gib_per_hour, 4),
                "projected_end_usage_gib": round(projected_total_gib, 2),
                "projected_end_usage_percent": round(projected_pct, 2),
                "classification": disk_safety_status,
            },
            "backlog": {
                "archive_receipts_count": len(receipt_files),
                "running_full_scans": running_scans,
                "pending_full_scans": pending_scans,
                "completed_full_scans": completed_scans,
                "failed_full_scans": failed_scans,
                "classification": backlog_status,
            },
        }

    def render_markdown(self, data: dict[str, Any]) -> str:
        d = data["disk"]
        b = data["backlog"]
        return f"""# 72H Soak Progress Checkpoint: {data['epoch']}

- **Timestamp (UTC):** `{data['checkpoint_timestamp_utc']}`
- **Uptime:** `{data['uptime_hours']} h` ({data['progress_percent']}% of 72h)
- **Records Persisted:** `{data['records']:,}`
- **Bytes Persisted:** `{data['bytes_persisted']:,}`
- **Operational Errors:** WriterErrors: `{data['writer_errors']}`, QueueDrops: `{data['queue_drops']}`, Unpersisted: `{data['unpersisted']}`

## Disk Forecast
- **Current Used:** `{d['used_gib']} GiB / {d['total_gib']} GiB` ({d['used_percent']}%)
- **Rolling Growth Rate:** `{d['growth_rate_gib_per_hour']} GiB/h`
- **T+72 Projected Usage:** `{d['projected_end_usage_gib']} GiB` ({d['projected_end_usage_percent']}%)
- **Safety Status:** **`{d['classification']}`**

## Archive & Full-Scan Backlog
- **Running Scans:** `{b['running_full_scans']}`
- **Pending Scans:** `{b['pending_full_scans']}`
- **Completed Scans:** `{b['completed_full_scans']}`
- **Failed Scans:** `{b['failed_full_scans']}`
- **Backlog Health:** **`{b['classification']}`**
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor 72H soak checkpoint, disk, and backlog.")
    parser.add_argument("--epoch-dir", required=True, type=Path, help="Epoch directory path")
    parser.add_argument("--output-json", type=Path, default=None, help="Output JSON path")
    args = parser.parse_args()

    monitor = SoakProgressMonitor(args.epoch_dir)
    res = monitor.collect_checkpoint()

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(res, indent=2), encoding="utf-8")

    print(monitor.render_markdown(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
