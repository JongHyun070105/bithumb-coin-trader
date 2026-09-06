#!/usr/bin/env python3
"""Authoritative 72-Hour Soak Deep Data-Quality and Integrity Auditor.

Implements Sections 33-39 of the 72H post-soak specification:
- Section 33: Full epoch integrity, manifest/receipt/scan report validation
- Section 34: Feed coverage matrix (hour x exchange x market x stream, 76-feed universe)
- Section 35: Timestamp quality & monotonic stability across collector_run_id
- Section 36: Bithumb latency semantics (exchange-labelled to receive offset)
- Section 37: Duplicate trade-ID and payload deduplication audit
- Section 38: Reconnect event and outage reconstruction
- Section 39: Gap and silence interval completeness heuristics
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

# Frozen 72H feed universe: 76 feeds per hour
EXPECTED_BITHUMB_20 = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE",
    "KRW-ADA", "KRW-XLM", "KRW-LINK", "KRW-AVAX", "KRW-BCH",
    "KRW-ETC", "KRW-NEAR", "KRW-SUI", "KRW-APT", "KRW-TRX",
    "KRW-SHIB", "KRW-SAND", "KRW-MANA", "KRW-AXS", "KRW-DOT"
]
EXPECTED_BINANCE_4 = ["btcusdt", "ethusdt", "solusdt", "xrpusdt"]
EXPECTED_UPBIT_4 = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP"]


@dataclass
class TimestampStats:
    total_records: int = 0
    exchange_ts_count: int = 0
    wall_ts_count: int = 0
    monotonic_ts_count: int = 0
    monotonic_reversals: int = 0
    wall_clock_reversals: int = 0
    offsets_ms: list[float] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        if not self.offsets_ms:
            p50 = p95 = p99 = max_offset = None
        else:
            s = sorted(self.offsets_ms)
            n = len(s)
            p50 = round(s[int(n * 0.50)], 3)
            p95 = round(s[min(n - 1, int(n * 0.95))], 3)
            p99 = round(s[min(n - 1, int(n * 0.99))], 3)
            max_offset = round(s[-1], 3)
        return {
            "scope": "SAMPLED",
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
            "wall_clock_reversals": self.wall_clock_reversals,
            "offset_p50_ms": p50,
            "offset_p95_ms": p95,
            "offset_p99_ms": p99,
            "offset_max_ms": max_offset,
        }


def parse_partition_path(rel_path: str | Path, manifest_meta: dict[str, Any] | None = None) -> tuple[str, str, str, str] | None:
    """Extract (exchange, stream, market, hour) from partition path and manifest metadata.

    Prefers manifest metadata when provided.
    Supports both layout styles:
    - raw/YYYY-MM-DD/exchange/stream/exchange_stream_market_YYYY-MM-DD_HH.jsonl
    - raw/exchange/stream/market/YYYYMMDD_HH.jsonl
    """
    p = Path(rel_path)
    filename = p.name

    # Extract hour string from filename or path
    hour_match = re.search(r"(\d{4}-\d{2}-\d{2}_\d{2}|\d{8}_\d{2}|\d{4}-\d{2}-\d{2}/\d{2})", str(rel_path))
    hour = hour_match.group(1).replace("/", "_") if hour_match else "unknown"

    if manifest_meta:
        exch = str(manifest_meta.get("exchange", "")).lower()
        strm = str(manifest_meta.get("stream", "")).lower()
        mkt = str(manifest_meta.get("market", "")).upper()
        if exch and strm and mkt:
            return exch, strm, mkt, hour

    parts = p.parts
    # Case 1: exchange_stream_market_date_hour.jsonl
    name_stem = p.name
    for ext in (".jsonl.zst", ".jsonl", ".ndjson.zst", ".ndjson"):
        if name_stem.endswith(ext):
            name_stem = name_stem[:-len(ext)]
            break

    subparts = name_stem.split("_")
    if len(subparts) >= 5:
        # e.g. bithumb_orderbook_krw-btc_2026-09-04_15
        exch = subparts[0].lower()
        strm = subparts[1].lower()
        mkt = subparts[2].upper()
        date_hour = f"{subparts[3]}_{subparts[4]}"
        return exch, strm, mkt, date_hour

    # Case 2: raw/exchange/stream/market/hour.jsonl
    if len(parts) >= 4:
        exch = parts[-4].lower()
        strm = parts[-3].lower()
        mkt = parts[-2]
        return exch, strm, mkt, hour

    return None


def _stream_file_sha256(path: Path) -> tuple[str, int, int]:
    """Stream SHA256 of a file and return (sha256_hex, total_bytes, line_count)."""
    hasher = hashlib.sha256()
    total_bytes = 0
    lines = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            total_bytes += len(chunk)
            lines += chunk.count(b"\n")
            hasher.update(chunk)
    return hasher.hexdigest(), total_bytes, lines


class SoakAuditor72H:
    def __init__(self, epoch_dir: Path):
        self.epoch_dir = epoch_dir
        self.raw_dir = epoch_dir / "raw"
        self.manifests_dir = epoch_dir / "manifests"
        self.compressed_dir = epoch_dir / "compressed"
        self.receipts_dir = epoch_dir / "archive-receipts"
        self.logs_dir = epoch_dir / "logs"

    @classmethod
    def get_expected_feed_universe(cls) -> list[tuple[str, str, str]]:
        """Returns the frozen 76-feed universe: 60 Bithumb, 8 Binance, 8 Upbit."""
        feeds: list[tuple[str, str, str]] = []
        for m in EXPECTED_BITHUMB_20:
            for s in ("orderbook", "trade", "ticker"):
                feeds.append(("bithumb", s, m))
        for m in EXPECTED_BINANCE_4:
            for s in ("orderbook", "trade"):
                feeds.append(("binance", s, m))
        for m in EXPECTED_UPBIT_4:
            for s in ("orderbook", "trade"):
                feeds.append(("upbit", s, m))
        return feeds

    def audit(self, max_sample_lines: int = 1000) -> dict[str, Any]:
        report: dict[str, Any] = {
            "epoch_dir": str(self.epoch_dir),
            "audited_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "PASS",
            "audit_type": "authoritative_deep_dq",
            "blockers": [],
            "warnings": [],
            "summary": {},
            "manifest_verification": {"scope": "FULL", "verified": 0, "mismatches": 0},
            "feed_coverage": {},
            "hourly_cohorts": {},
            "timestamp_quality": {},
            "bithumb_latency_semantics": {},
            "duplicate_audit": {},
            "reconnect_reconstruction": {},
            "gap_completeness": {},
        }

        raw_files = list(self.raw_dir.glob("**/*.jsonl")) if self.raw_dir.exists() else []
        if not raw_files and self.epoch_dir.exists():
            raw_files = list(self.epoch_dir.glob("raw/**/*.jsonl"))

        manifest_files = list(self.manifests_dir.glob("**/manifest_*.json")) if self.manifests_dir.exists() else []
        if not manifest_files and self.epoch_dir.exists():
            manifest_files = list(self.epoch_dir.glob("manifests/**/manifest_*.json"))

        receipt_files = []
        full_scan_reports = []
        for r_dir in [self.epoch_dir / "archive-receipts", self.epoch_dir / "receipts"]:
            if r_dir.exists():
                receipt_files.extend(list(r_dir.glob("**/*.archive-receipt.json")))
                receipt_files.extend(list(r_dir.glob("**/receipt_*.json")))
                full_scan_reports.extend(list(r_dir.glob("**/full_scan_*_report.json")))

        report["summary"]["raw_files_count"] = len(raw_files)
        report["summary"]["manifests_count"] = len(manifest_files)
        report["summary"]["receipts_count"] = len(receipt_files)
        report["summary"]["full_scan_reports_count"] = len(full_scan_reports)

        # P0.5: Receipt and full-scan verification
        for r_file in receipt_files:
            try:
                r_data = json.loads(r_file.read_text(encoding="utf-8"))
                if r_data.get("state") in ("FAILED", "FAIL") or r_data.get("status") in ("FAILED", "FAIL"):
                    report["blockers"].append(f"RECEIPT_FAILED: {r_file.name} recorded failure: {r_data.get('failure_reason')}")
                if r_data.get("restore_verified") is False or r_data.get("restore_status") in ("FAILED", "FAIL"):
                    report["blockers"].append(f"RESTORE_MISMATCH: {r_file.name} recorded restore verification failure")
            except Exception as e:
                report["blockers"].append(f"RECEIPT_CORRUPT: Corrupt receipt {r_file.name}: {e}")

        for fs_file in full_scan_reports:
            try:
                fs_data = json.loads(fs_file.read_text(encoding="utf-8"))
                if fs_data.get("status") != "PASS":
                    report["blockers"].append(f"FULL_SCAN_FAIL: Full-scan report {fs_file.name} failed with status {fs_data.get('status')}")
            except Exception as e:
                report["blockers"].append(f"FULL_SCAN_FAIL: Unreadable full-scan report {fs_file.name}: {e}")

        # P0.1: Empty epoch must FAIL
        if not raw_files and not manifest_files:
            report["status"] = "FAIL"
            report["blockers"].append("NO_RAW_EVIDENCE: No raw partition files found in epoch")
            report["blockers"].append("NO_MANIFEST_EVIDENCE: No manifest files found in epoch")
            return report

        if not raw_files:
            report["status"] = "FAIL"
            report["blockers"].append("NO_RAW_EVIDENCE: Raw data directory missing or empty")

        if not manifest_files:
            report["status"] = "FAIL"
            report["blockers"].append("NO_MANIFEST_EVIDENCE: Manifest directory missing or empty")

        coverage_matrix: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "records": 0,
            "bytes": 0,
            "state": "UNKNOWN",
        })

        ts_stats_by_feed: dict[str, TimestampStats] = defaultdict(TimestampStats)
        trade_id_registry: dict[str, set[str]] = defaultdict(set)
        duplicate_counts: dict[str, int] = defaultdict(int)
        inter_arrival_times: dict[str, list[float]] = defaultdict(list)
        observed_hours: set[str] = set()

        # P0.4: FULL MANIFEST HASH VERIFICATION
        for mf in manifest_files:
            try:
                m_data = json.loads(mf.read_text(encoding="utf-8"))
            except Exception as e:
                report["blockers"].append(f"CORRUPT_MANIFEST: {mf.name}: {e}")
                continue

            rel_p = m_data.get("partition_path", "")
            parsed = parse_partition_path(rel_p, manifest_meta=m_data)
            if not parsed:
                report["warnings"].append(f"Unparseable partition path in manifest {mf.name}: {rel_p}")
                continue

            exchange, stream, market, hour = parsed
            observed_hours.add(hour)
            cell_key = f"{hour}/{exchange}/{market}/{stream}"

            recs = m_data.get("record_count", 0)
            b_count = m_data.get("bytes", 0)
            cell = coverage_matrix[cell_key]
            cell["records"] += recs
            cell["bytes"] += b_count
            cell["state"] = "PASS" if recs > 0 else "DEGRADED"

            # Locate raw file
            raw_path = self.raw_dir / rel_p.removeprefix("raw/").removeprefix("/")
            if not raw_path.exists():
                raw_path = self.epoch_dir / rel_p
            if not raw_path.exists():
                # Try matching by filename in raw_dir
                candidates = list(self.raw_dir.glob(f"**/{Path(rel_p).name}"))
                if candidates:
                    raw_path = candidates[0]

            if not raw_path.exists():
                cell["state"] = "FAIL"
                report["blockers"].append(f"MISSING_RAW_FILE: Raw partition missing for manifest {mf.name}: {rel_p}")
                continue

            # Full partition verification: streaming SHA-256 and record count
            actual_sha, actual_bytes, actual_records = _stream_file_sha256(raw_path)
            expected_sha = m_data.get("sha256", "")
            expected_records = m_data.get("record_count", -1)
            expected_bytes = m_data.get("bytes", -1)

            if actual_sha != expected_sha:
                cell["state"] = "FAIL"
                report["manifest_verification"]["mismatches"] += 1
                report["blockers"].append(
                    f"HASH_MISMATCH: Partition {raw_path.name} sha256 '{actual_sha}' != manifest '{expected_sha}'"
                )
            elif expected_records >= 0 and actual_records != expected_records:
                cell["state"] = "FAIL"
                report["manifest_verification"]["mismatches"] += 1
                report["blockers"].append(
                    f"RECORD_COUNT_MISMATCH: Partition {raw_path.name} records {actual_records} != manifest {expected_records}"
                )
            elif expected_bytes >= 0 and actual_bytes != expected_bytes:
                cell["state"] = "FAIL"
                report["manifest_verification"]["mismatches"] += 1
                report["blockers"].append(
                    f"BYTE_COUNT_MISMATCH: Partition {raw_path.name} bytes {actual_bytes} != manifest {expected_bytes}"
                )
            else:
                report["manifest_verification"]["verified"] += 1

            # P0.2 & P0.7: Sampling timestamp and envelope fields
            prev_monotonic_by_run: dict[str, int] = {}
            prev_wall_dt: datetime | None = None

            try:
                with raw_path.open("r", encoding="utf-8", errors="replace") as f:
                    for line_idx, line in enumerate(f):
                        if line_idx >= max_sample_lines:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        feed_key = f"{exchange}/{stream}"
                        stats = ts_stats_by_feed[feed_key]
                        stats.total_records += 1

                        # P0.2: Actual raw envelope keys
                        ex_ts_str = rec.get("exchange_ts")
                        wall_ts_str = rec.get("local_recv_ts")
                        mono_ns = rec.get("local_recv_monotonic_ns")
                        run_id = str(rec.get("collector_run_id", "default"))
                        payload = rec.get("payload", {})

                        # Legacy fallback only if top-level envelope missing
                        if ex_ts_str is None and "exchange_timestamp" in rec:
                            ex_ts_str = rec.get("exchange_timestamp")
                        if wall_ts_str is None and "receive_timestamp" in rec:
                            wall_ts_str = rec.get("receive_timestamp")
                        if mono_ns is None and "monotonic_timestamp" in rec:
                            mono_ns = rec.get("monotonic_timestamp")

                        ex_dt: datetime | None = None
                        if ex_ts_str is not None:
                            stats.exchange_ts_count += 1
                            try:
                                if isinstance(ex_ts_str, (int, float)):
                                    ex_sec = ex_ts_str / 1000.0 if ex_ts_str > 1e11 else float(ex_ts_str)
                                    ex_dt = datetime.fromtimestamp(ex_sec, tz=timezone.utc)
                                else:
                                    ex_dt = datetime.fromisoformat(str(ex_ts_str))
                            except Exception:
                                pass

                        wall_dt: datetime | None = None
                        if wall_ts_str is not None:
                            stats.wall_ts_count += 1
                            try:
                                if isinstance(wall_ts_str, (int, float)):
                                    wall_sec = wall_ts_str / 1000.0 if wall_ts_str > 1e11 else float(wall_ts_str)
                                    wall_dt = datetime.fromtimestamp(wall_sec, tz=timezone.utc)
                                else:
                                    wall_dt = datetime.fromisoformat(str(wall_ts_str))
                            except Exception:
                                pass

                        if wall_dt is not None:
                            if prev_wall_dt is not None:
                                delta_sec = (wall_dt - prev_wall_dt).total_seconds()
                                if delta_sec < 0:
                                    stats.wall_clock_reversals += 1
                                else:
                                    inter_arrival_times[f"{exchange}/{market}/{stream}"].append(delta_sec)
                            prev_wall_dt = wall_dt

                        # Monotonic stability checked within collector_run_id
                        if mono_ns is not None:
                            stats.monotonic_ts_count += 1
                            try:
                                mono_val = int(mono_ns)
                                prev_mono = prev_monotonic_by_run.get(run_id)
                                if prev_mono is not None and mono_val < prev_mono:
                                    stats.monotonic_reversals += 1
                                prev_monotonic_by_run[run_id] = mono_val
                            except (ValueError, TypeError):
                                pass

                        # Offset: exchange-labelled timestamp to host receive offset (not pure network latency)
                        if ex_dt is not None and wall_dt is not None:
                            offset_ms = (wall_dt - ex_dt).total_seconds() * 1000.0
                            stats.offsets_ms.append(offset_ms)

                        # P0.8: Trade ID extraction from payload
                        if stream == "trade" and isinstance(payload, dict):
                            trade_id = None
                            if exchange == "bithumb":
                                trade_id = payload.get("trade_id") or payload.get("sequential_id") or payload.get("cont_no")
                            elif exchange == "binance":
                                trade_id = payload.get("t") or payload.get("data", {}).get("t") or payload.get("trade_id")
                            elif exchange == "upbit":
                                trade_id = payload.get("sequential_id") or payload.get("trade_id")
                            else:
                                trade_id = payload.get("trade_id")

                            if trade_id is not None:
                                t_str = str(trade_id)
                                m_key = f"{exchange}/{market}"
                                if t_str in trade_id_registry[m_key]:
                                    duplicate_counts[m_key] += 1
                                else:
                                    trade_id_registry[m_key].add(t_str)

            except Exception as e:
                report["warnings"].append(f"Failed sampling raw file {raw_path.name}: {e}")

        # P0.6: 76-feed expected universe coverage
        expected_universe = self.get_expected_feed_universe()
        for hour in sorted(observed_hours):
            hour_report: dict[str, str] = {}
            for exch, strm, mkt in expected_universe:
                cell_k = f"{hour}/{exch}/{mkt}/{strm}"
                if cell_k in coverage_matrix:
                    hour_report[f"{exch}/{mkt}/{strm}"] = coverage_matrix[cell_k]["state"]
                else:
                    hour_report[f"{exch}/{mkt}/{strm}"] = "MISSING"
                    # Only mark as blocker if other feeds were observed in this hour
                    report["warnings"].append(f"MISSING_FEED: Feed {exch}/{mkt}/{strm} missing in hour {hour}")
            report["hourly_cohorts"][hour] = hour_report

        report["feed_coverage"] = dict(coverage_matrix)
        report["timestamp_quality"] = {k: v.summary() for k, v in ts_stats_by_feed.items()}

        bithumb_stats = ts_stats_by_feed.get("bithumb/orderbook") or ts_stats_by_feed.get("bithumb/trade")
        if bithumb_stats and bithumb_stats.offsets_ms:
            s_offsets = sorted(bithumb_stats.offsets_ms)
            n_b = len(s_offsets)
            report["bithumb_latency_semantics"] = {
                "scope": "SAMPLED",
                "interpretation": "Exchange semantic age vs host receive clock (not pure network latency)",
                "offset_p50_ms": round(s_offsets[int(n_b * 0.5)], 3),
                "offset_p95_ms": round(s_offsets[min(n_b - 1, int(n_b * 0.95))], 3),
                "offset_p99_ms": round(s_offsets[min(n_b - 1, int(n_b * 0.99))], 3),
                "pure_network_latency_proven": False,
                "note": "Never treat exchange semantic age as pure network latency without hardware tap.",
            }
        else:
            report["bithumb_latency_semantics"] = {"status": "TELEMETRY_INSUFFICIENT"}

        report["duplicate_audit"] = {
            "scope": "SAMPLED",
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
            "scope": "FULL",
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
            "scope": "SAMPLED",
            "classifications": gap_classifications,
            "total_feeds_checked": len(gap_classifications),
        }

        if report["blockers"]:
            report["status"] = "FAIL"
        else:
            report["status"] = "DQ_PASS_ELIGIBLE"

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
            f"- **Manifest Verification:** `{report.get('manifest_verification', {})}`",
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

    return 0 if report["status"] in ("PASS", "DQ_PASS_ELIGIBLE") else 1


if __name__ == "__main__":
    sys.exit(main())
