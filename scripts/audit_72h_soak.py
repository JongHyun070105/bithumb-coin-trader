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
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

try:
    import zstandard
except ImportError:
    zstandard = None

# Frozen 72H feed universe: 76 feeds per hour
EXPECTED_BITHUMB_20 = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE",
    "KRW-ADA", "KRW-XLM", "KRW-LINK", "KRW-AVAX", "KRW-BCH",
    "KRW-ETC", "KRW-NEAR", "KRW-SUI", "KRW-APT", "KRW-TRX",
    "KRW-SHIB", "KRW-SAND", "KRW-MANA", "KRW-AXS", "KRW-DOT"
]
EXPECTED_BINANCE_4 = ["btcusdt", "ethusdt", "solusdt", "xrpusdt"]
EXPECTED_UPBIT_4 = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP"]


def derive_expected_raw_cohorts(start_dt: datetime, end_dt: datetime) -> list[str]:
    """Derive expected raw hour cohorts between start_dt and end_dt.

    The raw collector produces raw partitions for any hour touched by [start_dt, end_dt).
    If end_dt touches past the hour boundary, that hour is also included.
    For example: 03:40 -> 03:40 three days later produces 73 raw cohorts (Day 1 03 through Day 4 03).
    """
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    start_hour = datetime(start_dt.year, start_dt.month, start_dt.day, start_dt.hour, tzinfo=start_dt.tzinfo)
    if (end_dt.minute, end_dt.second, end_dt.microsecond) == (0, 0, 0):
        last_hour = end_dt - timedelta(hours=1)
    else:
        last_hour = datetime(end_dt.year, end_dt.month, end_dt.day, end_dt.hour, tzinfo=end_dt.tzinfo)

    cohorts: list[str] = []
    cur = start_hour
    while cur <= last_hour:
        cohorts.append(cur.strftime("%Y%m%d-%H"))
        cur += timedelta(hours=1)
    return cohorts


def derive_expected_archive_cohorts(
    start_dt: datetime, end_dt: datetime, grace_seconds: int = 600
) -> list[str]:
    """Derive expected archive receipt cohorts between start_dt and end_dt.

    An hour cohort [H, H+1hr) closes at H+1hr. Autonomous archiving runs after grace_seconds.
    Therefore, an archive receipt is required if and only if:
        H + 1hr + grace_seconds <= end_dt.
    For example: 03:40 -> 03:40 three days later produces 72 archive cohorts (Day 1 03 through Day 4 02).
    Day 4 03 was active at shutdown (03:40), never closed under the scheduler, so requires NO receipt.
    """
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    start_hour = datetime(start_dt.year, start_dt.month, start_dt.day, start_dt.hour, tzinfo=start_dt.tzinfo)
    cohorts: list[str] = []
    cur = start_hour
    while True:
        h_end = cur + timedelta(hours=1)
        archive_ready_time = h_end + timedelta(seconds=grace_seconds)
        if archive_ready_time <= end_dt:
            cohorts.append(cur.strftime("%Y%m%d-%H"))
            cur += timedelta(hours=1)
        else:
            break
    return cohorts


def derive_expected_fullscan_cohorts(start_dt: datetime, end_dt: datetime) -> dict[str, Any]:
    """Derive fullscan requirements and cohorts for a soak interval."""
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    dur_sec = (end_dt - start_dt).total_seconds()
    archive_cohorts = derive_expected_archive_cohorts(start_dt, end_dt, grace_seconds=600)
    return {
        "hourly_fullscan_cohorts": archive_cohorts,
        "terminal_fullscan_required": dur_sec >= 259200,
    }


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
    """Extract (exchange, stream, market, hour) from partition path and manifest metadata."""
    p = Path(rel_path)
    hour_match = re.search(r"(\d{4}-?\d{2}-?\d{2}[-_]\d{2}|\d{8}[-_]\d{2})", str(rel_path))
    hour = hour_match.group(1).replace("/", "_") if hour_match else "unknown"

    if manifest_meta:
        exch = str(manifest_meta.get("exchange", "")).lower()
        strm = str(manifest_meta.get("stream", "")).lower()
        mkt = str(manifest_meta.get("market", "")).upper()
        if exch and strm and mkt:
            return exch, strm, mkt, hour

    # Hive style: exchange=.../stream=.../market=.../part-*.zst
    exch = None
    strm = None
    mkt = None
    for part in p.parts:
        if part.startswith("exchange="):
            exch = part.split("=", 1)[1].lower()
        elif part.startswith("stream="):
            strm = part.split("=", 1)[1].lower()
        elif part.startswith("market="):
            mkt = part.split("=", 1)[1].upper()

    if exch and strm and mkt:
        return exch, strm, mkt, hour

    # Legacy style 1: exchange_stream_market_date_hour.jsonl
    name_stem = p.name
    for ext in (".ndjson.zst", ".jsonl.zst", ".jsonl", ".ndjson", ".zst"):
        if name_stem.endswith(ext):
            name_stem = name_stem[:-len(ext)]
            break

    subparts = name_stem.split("_")
    if len(subparts) >= 5:
        exch = subparts[0].lower()
        strm = subparts[1].lower()
        mkt = subparts[2].upper()
        date_hour = f"{subparts[3]}_{subparts[4]}"
        return exch, strm, mkt, date_hour

    # Legacy style 2: raw/exchange/stream/market/hour.jsonl
    parts = p.parts
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
    is_zst = path.name.endswith(".zst")
    decomp_obj = zstandard.ZstdDecompressor().decompressobj() if (is_zst and zstandard) else None
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            total_bytes += len(chunk)
            hasher.update(chunk)
            if decomp_obj:
                try:
                    dec = decomp_obj.decompress(chunk)
                    lines += dec.count(b"\n")
                except Exception:
                    pass
            elif not is_zst:
                lines += chunk.count(b"\n")
    return hasher.hexdigest(), total_bytes, lines


class SoakAuditor72H:
    def __init__(
        self,
        epoch_dir: Path,
        contract_path: Path | None = None,
        strict: bool = False,
        mode: str = "lenient",
    ):
        self.epoch_dir = epoch_dir
        self.contract_path = contract_path
        self.mode = mode
        self.strict = strict or (mode == "official")
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

        raw_files = []
        if self.raw_dir.exists():
            for pat in ("**/*.jsonl", "**/*.zst", "**/*.ndjson", "**/*.jsonl.zst", "**/*.ndjson.zst"):
                raw_files.extend(list(self.raw_dir.glob(pat)))
        elif self.epoch_dir.exists():
            for pat in ("raw/**/*.jsonl", "raw/**/*.zst", "raw/**/*.ndjson", "raw/**/*.jsonl.zst", "raw/**/*.ndjson.zst"):
                raw_files.extend(list(self.epoch_dir.glob(pat)))
        raw_files = sorted(set(raw_files))

        manifest_files = []
        if self.manifests_dir.exists():
            for pat in ("**/manifest_*.json", "**/*.manifest.json", "**/epoch_manifest.json", "**/manifest.json"):
                manifest_files.extend(list(self.manifests_dir.glob(pat)))
        elif self.epoch_dir.exists():
            for pat in ("manifests/**/manifest_*.json", "manifests/**/*.manifest.json", "manifests/**/epoch_manifest.json", "manifests/**/manifest.json"):
                manifest_files.extend(list(self.epoch_dir.glob(pat)))
        manifest_files = sorted(set(manifest_files))

        receipt_files = []
        full_scan_reports = []
        for r_dir in [self.epoch_dir / "archive-receipts", self.epoch_dir / "receipts"]:
            if r_dir.exists():
                receipt_files.extend(list(r_dir.glob("**/*.archive-receipt.json")))
                receipt_files.extend(list(r_dir.glob("**/receipt_*.json")))
                full_scan_reports.extend(list(r_dir.glob("**/full_scan_*_report.json")))
        receipt_files = sorted(set(receipt_files))
        full_scan_reports = sorted(set(full_scan_reports))

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
        default_hour = "20260901-00"
        for rf in receipt_files:
            try:
                rd = json.loads(rf.read_text(encoding="utf-8"))
                c = rd.get("hour_cohort") or rf.name.split(".")[0]
                observed_hours.add(c)
                default_hour = c
            except Exception:
                pass

        for raw_f in raw_files:
            rel = raw_f.relative_to(self.raw_dir if self.raw_dir.exists() else self.epoch_dir)
            parsed = parse_partition_path(rel)
            if parsed:
                e, s, m, h = parsed
                if h == "unknown":
                    h = default_hour
                observed_hours.add(h)
                cell_k = f"{h}/{e}/{m}/{s}"
                if cell_k not in coverage_matrix:
                    coverage_matrix[cell_k]["records"] = 1
                    coverage_matrix[cell_k]["state"] = "PASS"

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
                # Try matching by relative path suffix in raw_dir
                rel_clean = rel_p.removeprefix("raw/").removeprefix("/")
                candidates = [c for c in self.raw_dir.glob(f"**/{Path(rel_p).name}") if str(c).endswith(rel_clean)]
                if candidates:
                    raw_path = candidates[0]

            if not raw_path.exists():
                cell["state"] = "FAIL"
                report["blockers"].append(f"MISSING_RAW_FILE: Raw partition missing for manifest {mf.name}: {rel_p}")
                report["blockers"].append(f"MISSING_REQUIRED_FEED: Feed partition missing: {rel_p}")
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

            # P0.2, P0.7, P4: Full integrity scan of all records in raw partition
            prev_monotonic_by_run: dict[str, int] = {}
            prev_wall_dt: datetime | None = None

            def _open_raw_stream(p: Path):
                if p.name.endswith(".zst"):
                    if not zstandard:
                        raise RuntimeError("zstandard package required to decompress .zst")
                    dctx = zstandard.ZstdDecompressor()
                    fh = open(p, "rb")
                    reader = dctx.stream_reader(fh)
                    return io.TextIOWrapper(reader, encoding="utf-8", errors="strict"), fh, reader
                else:
                    fh = open(p, "r", encoding="utf-8", errors="strict")
                    return fh, fh, None

            text_stream = None
            raw_fh = None
            z_reader = None
            try:
                text_stream, raw_fh, z_reader = _open_raw_stream(raw_path)
                with text_stream:
                    for line_idx, line in enumerate(text_stream, start=1):
                        line_s = line.strip()
                        if not line_s:
                            continue
                        try:
                            rec = json.loads(line_s)
                        except Exception as e:
                            cell["state"] = "FAIL"
                            report["blockers"].append(f"CORRUPT_RAW_RECORD: {raw_path.name} line {line_idx} invalid JSON: {e}")
                            break

                        feed_key = f"{exchange}/{stream}"
                        stats = ts_stats_by_feed[feed_key]
                        stats.total_records += 1

                        # Validate envelope fields
                        rec_exch = rec.get("exchange")
                        rec_strm = rec.get("stream")
                        rec_mkt = rec.get("market")
                        run_id = str(rec.get("collector_run_id") or "default")

                        if not rec_exch or not rec_strm or not rec_mkt:
                            cell["state"] = "FAIL"
                            report["blockers"].append(f"CORRUPT_RAW_RECORD: {raw_path.name} line {line_idx} missing envelope keys")
                            break

                        # Monotonic stability checked within collector_run_id across EVERY record
                        mono_ns = rec.get("local_recv_monotonic_ns") or rec.get("monotonic_timestamp")
                        if mono_ns is not None:
                            stats.monotonic_ts_count += 1
                            try:
                                mono_val = int(mono_ns)
                                prev_mono = prev_monotonic_by_run.get(run_id)
                                if prev_mono is not None and mono_val < prev_mono:
                                    stats.monotonic_reversals += 1
                                    cell["state"] = "FAIL"
                                    report["blockers"].append(
                                        f"MONOTONIC_CLOCK_REVERSAL: Monotonic clock decreased in run {run_id} from {prev_mono} to {mono_val} at record {line_idx} in {raw_path.name}"
                                    )
                                    break
                                prev_monotonic_by_run[run_id] = mono_val
                            except (ValueError, TypeError):
                                cell["state"] = "FAIL"
                                report["blockers"].append(f"CORRUPT_RAW_RECORD: {raw_path.name} line {line_idx} invalid monotonic timestamp {mono_ns}")
                                break

                        # Timestamps & optional sampling
                        ex_ts_str = rec.get("exchange_ts") or rec.get("exchange_timestamp")
                        wall_ts_str = rec.get("local_recv_ts") or rec.get("receive_timestamp")

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
                                elif line_idx <= max_sample_lines:
                                    inter_arrival_times[f"{exchange}/{market}/{stream}"].append(delta_sec)
                            prev_wall_dt = wall_dt

                        if line_idx <= max_sample_lines:
                            if ex_dt is not None and wall_dt is not None:
                                offset_ms = (wall_dt - ex_dt).total_seconds() * 1000.0
                                stats.offsets_ms.append(offset_ms)

                            if stream == "trade":
                                payload = rec.get("payload", {})
                                if isinstance(payload, dict):
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
                cell["state"] = "FAIL"
                if "zstandard" in str(type(e)).lower() or "zstd" in str(e).lower():
                    report["blockers"].append(f"CORRUPT_ZSTD_STREAM: Failed decompressing {raw_path.name}: {e}")
                else:
                    report["blockers"].append(f"CORRUPT_RAW_RECORD: Failed reading {raw_path.name}: {e}")
            finally:
                if raw_fh and not raw_fh.closed:
                    try:
                        raw_fh.close()
                    except Exception:
                        pass

        # P0.6 & P0.1: 76-feed expected universe coverage
        expected_universe = self.get_expected_feed_universe()
        for hour in sorted(observed_hours):
            hour_report: dict[str, str] = {}
            for exch, strm, mkt in expected_universe:
                cell_k = f"{hour}/{exch}/{mkt}/{strm}"
                if cell_k not in coverage_matrix:
                    alt_upper = f"{hour}/{exch}/{mkt.upper()}/{strm}"
                    alt_lower = f"{hour}/{exch}/{mkt.lower()}/{strm}"
                    if alt_upper in coverage_matrix:
                        cell_k = alt_upper
                    elif alt_lower in coverage_matrix:
                        cell_k = alt_lower
                if cell_k in coverage_matrix:
                    hour_report[f"{exch}/{mkt}/{strm}"] = coverage_matrix[cell_k]["state"]
                else:
                    hour_report[f"{exch}/{mkt}/{strm}"] = "MISSING"
                    # P0.1: Missing required feed is a hard blocker for DQ_PASS_ELIGIBLE
                    report["warnings"].append(f"MISSING_FEED: Feed {exch}/{mkt}/{strm} missing in hour {hour}")
                    report["blockers"].append(f"MISSING_REQUIRED_FEED: Feed {exch}/{mkt}/{strm} missing in hour {hour}")
            report["hourly_cohorts"][hour] = hour_report

        # P1.1 & P1.3: Run Contract & Expected Hour Cohorts Verification
        contract_file = self.contract_path
        if contract_file is None or not contract_file.exists():
            candidates = [
                self.epoch_dir / "epoch_contract.json",
                self.epoch_dir / "runtime_seal.json",
                self.epoch_dir / "aws-72h-soak.runtime.json",
            ]
            for c in candidates:
                if c.exists():
                    contract_file = c
                    break

        contract_data: dict[str, Any] = {}
        if contract_file and contract_file.exists():
            try:
                contract_data = json.loads(contract_file.read_text(encoding="utf-8"))
            except Exception as e:
                report["blockers"].append(f"CORRUPT_RUN_CONTRACT: {e}")
        elif self.strict:
            report["blockers"].append("NO_RUN_CONTRACT: Run contract (epoch_contract.json / runtime_seal.json) required for authoritative DQ audit")

        # P7: Bind to epoch root manifest if present
        epoch_manifest_path = self.epoch_dir / "manifests" / "epoch_manifest.json"
        if epoch_manifest_path.exists():
            try:
                em_data = json.loads(epoch_manifest_path.read_text(encoding="utf-8"))
                report["epoch_manifest_sha256"] = em_data.get("epoch_manifest_sha256", "")
                if not em_data.get("sealed_complete", False) and em_data.get("status") != "SEALED_COMPLETE":
                    report["warnings"].append("EPOCH_MANIFEST_INCOMPLETE: epoch_manifest.json is not sealed complete")
            except Exception as e:
                report["warnings"].append(f"Unreadable epoch_manifest.json: {e}")

        expected_raw_cohorts: list[str] = []
        expected_archive_cohorts: list[str] = []
        fullscan_spec: dict[str, Any] = {"hourly_fullscan_cohorts": [], "terminal_fullscan_required": False}

        if contract_data:
            start_str = contract_data.get("start_time_utc")
            end_str = contract_data.get("expected_end_time_utc")
            dur_sec = contract_data.get("duration_seconds")
            if start_str and (end_str or dur_sec):
                try:
                    start_dt = datetime.fromisoformat(start_str)
                    if end_str:
                        end_dt = datetime.fromisoformat(end_str)
                    else:
                        end_dt = start_dt + timedelta(seconds=dur_sec)

                    expected_raw_cohorts = derive_expected_raw_cohorts(start_dt, end_dt)
                    expected_archive_cohorts = derive_expected_archive_cohorts(start_dt, end_dt, grace_seconds=600)
                    fullscan_spec = derive_expected_fullscan_cohorts(start_dt, end_dt)
                except Exception as e:
                    report["warnings"].append(f"Could not compute expected cohorts: {e}")

        # P1.3 & P3: Verify that every expected hour cohort was observed
        norm_observed = {re.sub(r"[-_]", "", h) for h in observed_hours}
        for exp_h in expected_raw_cohorts:
            if exp_h not in observed_hours and re.sub(r"[-_]", "", exp_h) not in norm_observed:
                report["blockers"].append(f"MISSING_EXPECTED_HOUR: Expected cohort {exp_h} has no raw partition files")

        # P1.4 & P3: Archive receipt verification for closed cohorts
        cohorts_for_receipts = expected_archive_cohorts
        if not cohorts_for_receipts and (len(observed_hours) > 1 or contract_data.get("require_receipts", False)):
            cohorts_for_receipts = sorted(observed_hours)

        if cohorts_for_receipts:
            receipt_cohort_names = set()
            for rf in receipt_files:
                try:
                    rd = json.loads(rf.read_text(encoding="utf-8"))
                    rc = rd.get("hour_cohort") or rd.get("cohort") or rf.name.split(".")[0]
                    receipt_cohort_names.add(rc)
                except Exception:
                    pass
            norm_receipt_names = {re.sub(r"[-_]", "", rc) for rc in receipt_cohort_names}
            for ch in cohorts_for_receipts:
                norm_ch = re.sub(r"[-_]", "", ch)
                if (
                    ch not in receipt_cohort_names
                    and norm_ch not in norm_receipt_names
                    and not any(ch in rf.name or norm_ch in re.sub(r"[-_]", "", rf.name) for rf in receipt_files)
                ):
                    report["blockers"].append(f"ARCHIVE_RECEIPT_MISSING: Missing archive receipt for cohort {ch}")

        # P1.5 & P3: Terminal full-scan report requirement
        if contract_data.get("require_fullscan", False) or fullscan_spec["terminal_fullscan_required"]:
            if not full_scan_reports:
                report["blockers"].append("FULLSCAN_EVIDENCE_MISSING: Terminal full-scan report required for authoritative 72H DQ")

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

        degraded_cells = sum(1 for c in coverage_matrix.values() if c.get("state") == "DEGRADED")
        failed_cells = sum(1 for c in coverage_matrix.values() if c.get("state") in ("FAIL", "MISSING"))
        if degraded_cells > 0:
            report["blockers"].append(f"DEGRADED_FEED_PARTITIONS: {degraded_cells} partitions had 0 records or degraded status")
        if failed_cells > 0:
            report["blockers"].append(f"FAILED_FEED_PARTITIONS: {failed_cells} partitions failed integrity or missing")

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

        if report.get("blockers"):
            lines.extend([
                "## 6. Blockers",
                "",
            ])
            for b in report["blockers"]:
                lines.append(f"- `BLOCKER`: {b}")
            lines.append("")

        return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit 72H soak epoch data quality and integrity.")
    parser.add_argument("--epoch-dir", required=True, type=Path, help="Path to epoch directory")
    parser.add_argument("--output-json", "--out-json", type=Path, default=None, help="Output JSON report path")
    parser.add_argument("--output-md", "--out-md", type=Path, default=None, help="Output Markdown report path")
    parser.add_argument("--sample-lines", type=int, default=1000, help="Max lines per file to sample")
    parser.add_argument("--contract", "--epoch-contract", type=Path, default=None, help="Run contract path")
    parser.add_argument("--strict", action="store_true", default=False, help="Strict verification mode")
    parser.add_argument("--mode", choices=["official", "lenient", "adhoc"], default="official", help="Audit mode (default: official)")
    args = parser.parse_args()

    auditor = SoakAuditor72H(args.epoch_dir, contract_path=args.contract, strict=args.strict, mode=args.mode)
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

    if report["status"] != "DQ_PASS_ELIGIBLE":
        for b in report.get("blockers", []):
            print(f"BLOCKER: {b}", file=sys.stderr)
        for e in report.get("errors", []):
            print(f"ERROR: {e}", file=sys.stderr)

    return 0 if report["status"] == "DQ_PASS_ELIGIBLE" else 2


if __name__ == "__main__":
    sys.exit(main())
