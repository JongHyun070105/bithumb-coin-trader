"""Deterministic raw-dataset structural scanner and build-twice comparator.

Scope: file-level integrity and schema/timestamp/duplicate sanity over raw
collector JSONL (.jsonl, .jsonl.gz, .jsonl.zst). It produces a reproducible
scan report with per-file provenance.

This module NEVER decides dataset qualification. Reports always carry
``dataset_qualified = False`` / ``qualification_authority = "NONE"``: formal
DQ requires closed reliability and an explicit, separate qualification step.
No network, AWS, or exchange access.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

SCAN_SCHEMA_VERSION = 1
RAW_SUFFIXES = (".jsonl", ".jsonl.gz", ".jsonl.zst")
CRITICAL_STRING_FIELDS = ("exchange", "stream", "market")
CRITICAL_TS_FIELDS = ("local_recv_ts", "local_write_ts")
CRITICAL_PRESENT_FIELDS = ("payload",)
MAX_SAMPLES = 5


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ts_to_ms(value: Any) -> int | None:
    """Accept integer epoch milliseconds or an ISO-8601 string; None if unusable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value == value and abs(value) != float("inf") else None
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            return None
        return int(dt.astimezone(timezone.utc).timestamp() * 1000)
    return None


def feed_id(record: Mapping[str, Any]) -> str:
    return f"{str(record.get('exchange', '')).lower()}:{str(record.get('stream', '')).lower()}:{str(record.get('market', '')).upper()}"


def _zstd_lines(path: Path) -> Iterator[bytes]:
    import zstandard

    dobj = zstandard.ZstdDecompressor().decompressobj()
    frame_open = False
    pending = b""
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            while chunk:
                frame_open = True
                pending += dobj.decompress(chunk)
                if dobj.eof:
                    chunk, frame_open = dobj.unused_data, False
                    dobj = zstandard.ZstdDecompressor().decompressobj()
                else:
                    chunk = b""
                *lines, pending = pending.split(b"\n")
                for line in lines:
                    yield line + b"\n"
    if frame_open:
        raise EOFError("truncated zstd frame")
    if pending:
        yield pending


def _open_lines(path: Path) -> Iterator[bytes]:
    name = path.name
    if name.endswith(".zst"):
        yield from _zstd_lines(path)
    elif name.endswith(".gz"):
        with gzip.open(path, "rb") as f:
            yield from f
    else:
        with open(path, "rb") as f:
            yield from f


@dataclass
class FileScan:
    path: str
    size_bytes: int
    sha256: str
    rows: int = 0
    malformed_rows: int = 0
    null_critical_rows: int = 0
    timestamp_violation_rows: int = 0
    write_before_recv_rows: int = 0
    duplicate_identity_rows: int = 0
    zero_byte: bool = False
    truncated: bool = False
    unterminated_final_line: bool = False
    schema_hash: str = ""
    min_write_ms: int | None = None
    max_write_ms: int | None = None
    feeds: list[str] = field(default_factory=list)

    def issues(self) -> list[str]:
        out = []
        for name in ("zero_byte", "truncated", "unterminated_final_line"):
            if getattr(self, name):
                out.append(name)
        for name in ("malformed_rows", "null_critical_rows", "timestamp_violation_rows", "write_before_recv_rows", "duplicate_identity_rows"):
            if getattr(self, name):
                out.append(name)
        return out


def _scan_file(
    path: Path, rel: str, seen: set[bytes], *, min_ts_ms: int | None, max_ts_ms: int | None,
) -> FileScan:
    size = path.stat().st_size
    fs = FileScan(path=rel, size_bytes=size, sha256=_file_sha256(path))
    if size == 0:
        fs.zero_byte = True
        return fs
    key_sets: set[tuple[str, ...]] = set()
    feeds: set[str] = set()
    try:
        for raw in _open_lines(path):
            fs.unterminated_final_line = not raw.endswith(b"\n")
            line = raw.strip()
            if not line:
                continue
            fs.rows += 1
            try:
                rec = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                fs.malformed_rows += 1
                continue
            if not isinstance(rec, dict):
                fs.malformed_rows += 1
                continue
            key_sets.add(tuple(sorted(rec)))
            null_critical = any(not isinstance(rec.get(k), str) or not rec.get(k) for k in CRITICAL_STRING_FIELDS)
            null_critical |= any(rec.get(k) is None for k in CRITICAL_PRESENT_FIELDS)
            recv, write = ts_to_ms(rec.get("local_recv_ts")), ts_to_ms(rec.get("local_write_ts"))
            if recv is None or write is None:
                null_critical = True
            if null_critical:
                fs.null_critical_rows += 1
                continue
            feeds.add(feed_id(rec))
            if recv <= 0 or write <= 0 or (min_ts_ms is not None and write < min_ts_ms) or (max_ts_ms is not None and write > max_ts_ms):
                fs.timestamp_violation_rows += 1
            if write < recv:
                fs.write_before_recv_rows += 1
            fs.min_write_ms = write if fs.min_write_ms is None else min(fs.min_write_ms, write)
            fs.max_write_ms = write if fs.max_write_ms is None else max(fs.max_write_ms, write)
            ident = hashlib.sha256(canonical_json([
                str(rec["exchange"]).lower(), str(rec["stream"]).lower(), str(rec["market"]).upper(),
                rec.get("exchange_ts"), rec["payload"],
            ])).digest()[:16]
            if ident in seen:
                fs.duplicate_identity_rows += 1
            else:
                seen.add(ident)
    except Exception:  # decompressor/stream failure (gzip EOF, zstd frame error) => truncated or corrupt
        fs.truncated = True
    fs.schema_hash = sha256_hex(canonical_json(sorted(key_sets)))
    fs.feeds = sorted(feeds)
    return fs


def scan_dataset(
    root: Path,
    *,
    dataset_label: str,
    expected_feeds: Sequence[str] | None = None,
    min_ts_ms: int | None = None,
    max_ts_ms: int | None = None,
) -> dict[str, Any]:
    """Scan ``root`` deterministically. Output has no wall-clock or absolute paths."""
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"dataset root missing: {dataset_label}")
    files: list[Path] = []
    ignored: list[str] = []
    for p in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix()):
        (files if p.name.endswith(RAW_SUFFIXES) else ignored).append(p)
    seen: set[bytes] = set()
    scans = [
        _scan_file(p, p.relative_to(root).as_posix(), seen, min_ts_ms=min_ts_ms, max_ts_ms=max_ts_ms)
        for p in files
    ]
    observed = sorted({f for s in scans for f in s.feeds})
    expected = sorted(set(expected_feeds)) if expected_feeds is not None else None
    missing = sorted(set(expected) - set(observed)) if expected is not None else None
    unexpected = sorted(set(observed) - set(expected)) if expected is not None else None
    totals = {
        "files": len(scans),
        "rows": sum(s.rows for s in scans),
        "bytes": sum(s.size_bytes for s in scans),
    }
    issue_files = {s.path: s.issues() for s in scans if s.issues()}
    body: dict[str, Any] = {
        "scan_schema_version": SCAN_SCHEMA_VERSION,
        "dataset_label": dataset_label,
        "dataset_qualified": False,
        "qualification_authority": "NONE",
        "parameters": {"min_ts_ms": min_ts_ms, "max_ts_ms": max_ts_ms, "expected_feed_count": None if expected is None else len(expected)},
        "totals": totals,
        "files": [s.__dict__ for s in scans],
        "ignored_files": [p.relative_to(root).as_posix() for p in ignored],
        "observed_feed_count": len(observed),
        "missing_feeds": missing,
        "unexpected_feeds": unexpected,
        "issue_files": issue_files,
        "scan_status": "SCAN_CLEAN" if not issue_files and not missing and not unexpected and scans else "SCAN_ISSUES",
    }
    body["manifest_sha256"] = sha256_hex(canonical_json(body))
    return body


def compare_scan_reports(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    """Build-twice comparison. ``identical`` only if every compared facet matches."""
    fa, fb = list(a["files"]), list(b["files"])
    paths_a, paths_b = [f["path"] for f in fa], [f["path"] for f in fb]
    by_b = {f["path"]: f for f in fb}
    diffs = {
        "file_count": a["totals"]["files"] != b["totals"]["files"],
        "row_count": a["totals"]["rows"] != b["totals"]["rows"],
        "ordering": paths_a != paths_b,
        "source_universe": sorted(paths_a) != sorted(paths_b),
        "schema_hashes": sorted((f["path"], f["schema_hash"]) for f in fa) != sorted((f["path"], f["schema_hash"]) for f in fb),
        "file_hashes": sorted((f["path"], f["sha256"]) for f in fa) != sorted((f["path"], f["sha256"]) for f in fb),
        "feeds": a["observed_feed_count"] != b["observed_feed_count"] or a["missing_feeds"] != b["missing_feeds"],
        "manifest_hash": a["manifest_sha256"] != b["manifest_sha256"],
    }
    per_file = sorted(
        f["path"] for f in fa
        if f["path"] in by_b and any(f[k] != by_b[f["path"]][k] for k in ("rows", "sha256", "schema_hash", "size_bytes"))
    )
    return {"identical": not any(diffs.values()) and not per_file, "differences": sorted(k for k, v in diffs.items() if v), "differing_files": per_file}


def scan_twice(root: Path, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    first = scan_dataset(root, **kwargs)
    second = scan_dataset(root, **kwargs)
    return first, compare_scan_reports(first, second)


def write_report(report: Mapping[str, Any], path: Path) -> str:
    data = canonical_json(report) + b"\n"
    path.write_bytes(data)
    return sha256_hex(data)
