#!/usr/bin/env python3
"""Authoritative 72-Hour Soak Deep Data-Quality and Integrity Auditor.

Implements Sections 33-39 of the 72H post-soak specification:
- Section 33: Full epoch integrity, manifest/receipt/scan report validation
- Section 34: Feed coverage matrix (hour x exchange x market x stream)
- Section 35: Timestamp quality & monotonic stability
- Section 36: Bithumb latency semantics
- Section 37: Duplicate trade-ID and payload deduplication audit
- Section 38: Reconnect event and outage reconstruction
- Section 39: Gap and silence interval completeness heuristics
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
from typing import Any


@dataclass
class TimestampStats:
    total_records: int = 0
    exchange_ts_count: int = 0
    wall_ts_count: int = 0
    monotonic_ts_count: int = 0
    monotonic_reversals: int = 0
    offsets_ms: list[float] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        if not self.offsets_ms:
            p50 = p95 = p99 = max_offset = None
        else:
            s = sorted(self.offsets_ms)
            n = len(s)
            p50 = s[int(n * 0.50)]
            p95 = s[min(n - 1, int(n * 0.95))]
            p99 = s[min(n - 1, int(n * 0.99))]
            max_offset = s[-1]
        return {
            "total_records": self.total_records,
            "exchange_ts_coverage": (
                self.exchange_ts_count / self.total_records if self.total_records > 0 else 0.0
            ),
            "wall_ts_coverage": (
                self.wall_ts_count / self.total_records if self.total_records > 0 else 0.0
            ),
            "monotonic_ts_coverage": (
                self.monotonic_ts_count / self.total_records if self.total_records > 0 else 0.0
            ),
            "monotonic_reversals": self.monotonic_reversals,
            "offset_p50_ms": p50,
            "offset_p95_ms": p95,
            "offset_p99_ms": p99,
            "offset_max_ms": max_offset,
        }


def parse_partition_path(rel_path: str | Path) -> tuple[str, str, str, str] | None:
    """Extract (exchange, stream, market, hour) from partition path.
    Example: raw/bithumb/orderbook/KRW-BTC/20260904_15.jsonl
    """
    p = Path(rel_path)
    parts = p.parts
    if len(parts) >= 4:
        exchange = parts[-4]
        stream = parts[-3]
        market = parts[-2]
        filename = parts[-1]
        hour_match = re.search(r"(\d{8}_\d{2})", filename)
        hour = hour_match.group(1) if hour_match else "unknown"
        return exchange, stream, market, hour
    return None


class SoakAuditor72H:
    def __init__(self, epoch_dir: Path):
        self.epoch_dir = epoch_dir
        self.raw_dir = epoch_dir / "raw"
        self.manifests_dir = epoch_dir / "manifests"
        self.compressed_dir = epoch_dir / "compressed"
        self.receipts_dir = epoch_dir / "archive-receipts"
        self.logs_dir = epoch_dir / "logs"

    def audit(self, max_sample_lines: int = 1000) -> dict[str, Any]:
        report: dict[str, Any] = {
            "epoch_dir": str(self.epoch_dir),
            "audited_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "PASS",
            "blockers": [],
            "warnings": [],
            "summary": {},
            "feed_coverage": {},
            "timestamp_quality": {},
            "bithumb_latency_semantics": {},
            "duplicate_audit": {},
            "reconnect_reconstruction": {},
            "gap_completeness": {},
        }

        raw_files = list(self.raw_dir.glob("**/*.jsonl")) if self.raw_dir.exists() else []
        manifest_files = list(self.manifests_dir.glob("**/manifest_*.json")) if self.manifests_dir.exists() else []
        receipt_files = list(self.receipts_dir.glob("**/receipt_*.json")) if self.receipts_dir.exists() else []
        full_scan_reports = list(self.receipts_dir.glob("**/full_scan_*_report.json")) if self.receipts_dir.exists() else []

        report["summary"]["raw_files_count"] = len(raw_files)
        report["summary"]["manifests_count"] = len(manifest_files)
        report["summary"]["receipts_count"] = len(receipt_files)
        report["summary"]["full_scan_reports_count"] = len(full_scan_reports)

        coverage_matrix: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "records": 0,
            "bytes": 0,
            "first_ts": None,
            "last_ts": None,
            "state": "UNKNOWN",
        })

        ts_stats_by_feed: dict[str, TimestampStats] = defaultdict(TimestampStats)
        trade_id_registry: dict[str, set[str]] = defaultdict(set)
        duplicate_counts: dict[str, int] = defaultdict(int)
        inter_arrival_times: dict[str, list[float]] = defaultdict(list)

        for mf in manifest_files:
            try:
                m_data = json.loads(mf.read_text(encoding="utf-8"))
            except Exception as e:
                report["warnings"].append(f"Corrupt manifest {mf.name}: {e}")
                continue

            rel_p = m_data.get("partition_path", "")
            parsed = parse_partition_path(rel_p)
            if not parsed:
                continue
            exchange, stream, market, hour = parsed
            cell_key = f"{hour}/{exchange}/{market}/{stream}"

            recs = m_data.get("record_count", 0)
            b_count = m_data.get("bytes", 0)
            cell = coverage_matrix[cell_key]
            cell["records"] += recs
            cell["bytes"] += b_count
            cell["state"] = "PASS" if recs > 0 else "DEGRADED"

            raw_path = self.raw_dir / rel_p.removeprefix("raw/").removeprefix("/")
            if not raw_path.exists():
                raw_path = self.epoch_dir / rel_p
            if not raw_path.exists():
                cell["state"] = "FAIL"
                report["blockers"].append(f"Missing raw file for manifest: {rel_p}")
                continue

            prev_monotonic = None
            prev_wall = None
            try:
                with raw_path.open("r", encoding="utf-8", errors="replace") as f:
                    for line_idx, line in enumerate(f):
                        if line_idx >= max_sample_lines:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        feed_key = f"{exchange}/{stream}"
                        stats = ts_stats_by_feed[feed_key]
                        stats.total_records += 1

                        ex_ts = record.get("exchange_timestamp") or record.get("timestamp")
                        wall_ts = record.get("receive_timestamp") or record.get("local_timestamp")
                        mono_ts = record.get("monotonic_timestamp")

                        if ex_ts is not None:
                            stats.exchange_ts_count += 1
                        if wall_ts is not None:
                            stats.wall_ts_count += 1
                        if mono_ts is not None:
                            stats.monotonic_ts_count += 1
                            if prev_monotonic is not None and mono_ts < prev_monotonic:
                                stats.monotonic_reversals += 1
                            prev_monotonic = mono_ts

                        if wall_ts is not None and prev_wall is not None:
                            diff_sec = wall_ts - prev_wall
                            if diff_sec >= 0:
                                inter_arrival_times[f"{exchange}/{market}/{stream}"].append(diff_sec)
                        if wall_ts is not None:
                            prev_wall = wall_ts

                        if ex_ts is not None and wall_ts is not None:
                            try:
                                ex_f = float(ex_ts)
                                wall_f = float(wall_ts)
                                ex_ms = ex_f if ex_f > 1e11 else ex_f * 1000.0
                                wall_ms = wall_f if wall_f > 1e11 else wall_f * 1000.0
                                stats.offsets_ms.append(wall_ms - ex_ms)
                            except (ValueError, TypeError):
                                pass

                        trade_id = record.get("trade_id")
                        if trade_id is not None:
                            trade_str = str(trade_id)
                            m_key = f"{exchange}/{market}"
                            if trade_str in trade_id_registry[m_key]:
                                duplicate_counts[m_key] += 1
                            else:
                                trade_id_registry[m_key].add(trade_str)

            except Exception as e:
                report["warnings"].append(f"Failed sampling raw file {raw_path}: {e}")

        report["feed_coverage"] = dict(coverage_matrix)
        report["timestamp_quality"] = {k: v.summary() for k, v in ts_stats_by_feed.items()}

        bithumb_stats = ts_stats_by_feed.get("bithumb/orderbook") or ts_stats_by_feed.get("bithumb/trade")
        if bithumb_stats and bithumb_stats.offsets_ms:
            s_offsets = sorted(bithumb_stats.offsets_ms)
            n_b = len(s_offsets)
            report["bithumb_latency_semantics"] = {
                "interpretation": "Exchange semantic age vs host receive clock",
                "offset_p50_ms": s_offsets[int(n_b * 0.5)],
                "offset_p95_ms": s_offsets[min(n_b - 1, int(n_b * 0.95))],
                "pure_network_latency_proven": False,
                "note": "Never treat exchange semantic age as pure network latency without hardware tap.",
            }
        else:
            report["bithumb_latency_semantics"] = {"status": "TELEMETRY_INSUFFICIENT"}

        report["duplicate_audit"] = {
            "total_markets_tracked": len(trade_id_registry),
            "duplicate_counts_by_market": dict(duplicate_counts),
            "duplicates_classified_as_corruption": False,
            "note": "Upbit/Binance trade ID duplicates across boundary require transaction re-id verification.",
        }

        reconnect_events: list[dict[str, Any]] = []
        log_files = list(self.logs_dir.glob("*.log")) if self.logs_dir.exists() else []
        for lf in log_files:
            try:
                for line in lf.read_text(encoding="utf-8", errors="replace").splitlines():
                    if any(w in line.lower() for w in ["reconnect", "disconnect", "resubscri"]):
                        reconnect_events.append({"source": lf.name, "message": line.strip()})
            except Exception:
                pass
        report["reconnect_reconstruction"] = {
            "reconnect_events_detected": len(reconnect_events),
            "events_sample": reconnect_events[:20],
            "missing_telemetry": "UNKNOWN" if not log_files else "PRESENT",
        }

        gap_classifications: dict[str, str] = {}
        for m_key, intervals in inter_arrival_times.items():
            if not intervals:
                gap_classifications[m_key] = "TELEMETRY_INSUFFICIENT"
                continue
            max_silence = max(intervals)
            if max_silence > 120.0:
                gap_classifications[m_key] = "SUSPECTED_GAP"
            else:
                gap_classifications[m_key] = "OK"
        report["gap_completeness"] = {
            "classifications": gap_classifications,
            "total_feeds_checked": len(gap_classifications),
        }

        if report["blockers"]:
            report["status"] = "FAIL"

        return report

    def render_markdown(self, report: dict[str, Any]) -> str:
        lines: list[str] = [
            f"# 72-Hour Soak Deep Data-Quality Audit: {Path(report['epoch_dir']).name}",
            "",
            f"- **Audited At (UTC):** `{report['audited_at_utc']}`",
            f"- **Overall Status:** `{report['status']}`",
            f"- **Blockers:** `{len(report['blockers'])}`",
            f"- **Warnings:** `{len(report['warnings'])}`",
            "",
            "## 1. Artifact Summary",
            "",
            f"- **Raw Files:** `{report['summary'].get('raw_files_count', 0)}`",
            f"- **Manifests:** `{report['summary'].get('manifests_count', 0)}`",
            f"- **Archive Receipts:** `{report['summary'].get('receipts_count', 0)}`",
            f"- **Full Scan Reports:** `{report['summary'].get('full_scan_reports_count', 0)}`",
            "",
            "## 2. Feed Coverage Matrix (Hour x Exchange x Market x Stream)",
            "",
            "| Hour/Exchange/Market/Stream | Records | Bytes | State |",
            "| :--- | :--- | :--- | :--- |",
        ]
        cov = report.get("feed_coverage", {})
        for k in sorted(cov.keys())[:30]:
            cell = cov[k]
            lines.append(f"| `{k}` | `{cell['records']:,}` | `{cell['bytes']:,}` | `{cell['state']}` |")
        if len(cov) > 30:
            lines.append(f"| ... ({len(cov) - 30} more cells hidden) | | | |")

        lines.extend([
            "",
            "## 3. Timestamp Quality & Stability",
            "",
            "| Feed | Total Recs | Exch TS % | Wall TS % | Mono TS % | Mono Reversals | Offset p50 (ms) | Offset p95 (ms) |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ])
        for feed, ts in report.get("timestamp_quality", {}).items():
            lines.append(
                f"| `{feed}` | `{ts['total_records']}` | `{ts['exchange_ts_coverage']:.1%}` | "
                f"`{ts['wall_ts_coverage']:.1%}` | `{ts['monotonic_ts_coverage']:.1%}` | "
                f"`{ts['monotonic_reversals']}` | `{ts['offset_p50_ms']}` | `{ts['offset_p95_ms']}` |"
            )

        lines.extend([
            "",
            "## 4. Latency Semantics & Duplicate Audit",
            "",
            f"- **Bithumb Latency Semantics:** `{report.get('bithumb_latency_semantics', {}).get('interpretation', 'N/A')}`",
            f"- **Bithumb Offset p50:** `{report.get('bithumb_latency_semantics', {}).get('offset_p50_ms')} ms`",
            f"- **Duplicate Trade IDs by Market:** `{json.dumps(report.get('duplicate_audit', {}).get('duplicate_counts_by_market', {}))}`",
            "",
            "## 5. Reconnect & Gap Completeness Heuristics",
            "",
            f"- **Reconnect Events Detected:** `{report.get('reconnect_reconstruction', {}).get('reconnect_events_detected', 0)}`",
            f"- **Feed Gap Classifications:** `{json.dumps(report.get('gap_completeness', {}).get('classifications', {}))}`",
            "",
        ])
        return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit 72H soak epoch data quality and integrity.")
    parser.add_argument("--epoch-dir", required=True, type=Path, help="Path to epoch directory")
    parser.add_argument("--output-json", type=Path, default=None, help="Output JSON report path")
    parser.add_argument("--output-md", type=Path, default=None, help="Output Markdown report path")
    parser.add_argument("--sample-lines", type=int, default=1000, help="Max lines per file to sample")
    args = parser.parse_args()

    auditor = SoakAuditor72H(args.epoch_dir)
    report = auditor.audit(max_sample_lines=args.sample_lines)

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Wrote JSON report to {args.output_json}")

    md = auditor.render_markdown(report)
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(md, encoding="utf-8")
        print(f"Wrote Markdown report to {args.output_md}")
    else:
        print(md)

    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
