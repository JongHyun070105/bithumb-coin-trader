"""Deterministic canonical dataset builder scaffold (offline tooling only).

Reads raw collector JSONL (.jsonl/.jsonl.gz/.jsonl.zst), drops structurally
unusable rows into a counted reject tally (never silently), de-duplicates by
event identity (first occurrence in sorted-path order wins), and writes
hour-partitioned canonical JSONL sorted by a total order. Output bytes and the
manifest depend only on input bytes, so independent builds are comparable.

The builder does NOT qualify a dataset: manifests carry
``dataset_qualified = False``. No network, AWS, or exchange access.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .dataset_scan import RAW_SUFFIXES, _open_lines, canonical_json, feed_id, sha256_hex, ts_to_ms

BUILD_SCHEMA_VERSION = 1
HOUR_MS = 3_600_000


def _normalize(rec: Mapping[str, Any]) -> dict[str, Any] | None:
    for k in ("exchange", "stream", "market"):
        if not isinstance(rec.get(k), str) or not rec[k]:
            return None
    recv, write = ts_to_ms(rec.get("local_recv_ts")), ts_to_ms(rec.get("local_write_ts"))
    if recv is None or write is None or recv <= 0 or write <= 0 or rec.get("payload") is None:
        return None
    return {
        "exchange": rec["exchange"].lower(),
        "stream": rec["stream"].lower(),
        "market": rec["market"].upper(),
        "exchange_ts": rec.get("exchange_ts"),
        "local_recv_ts_ms": recv,
        "local_write_ts_ms": write,
        "payload": rec["payload"],
    }


def _identity(ev: Mapping[str, Any]) -> bytes:
    return hashlib.sha256(canonical_json([ev["exchange"], ev["stream"], ev["market"], ev["exchange_ts"], ev["payload"]])).digest()


def _sort_key(ev: Mapping[str, Any], ident: bytes) -> tuple:
    return (ev["local_write_ts_ms"], ev["local_recv_ts_ms"], ev["exchange"], ev["stream"], ev["market"], ident)


def build_canonical(raw_root: Path, out_root: Path, *, dataset_label: str) -> dict[str, Any]:
    raw_root, out_root = Path(raw_root), Path(out_root)
    if not raw_root.is_dir():
        raise NotADirectoryError(f"raw root missing: {dataset_label}")
    if out_root.exists() and any(out_root.iterdir()):
        raise FileExistsError("output root must be empty; builds never overwrite")
    out_root.mkdir(parents=True, exist_ok=True)

    sources = sorted(
        (p for p in raw_root.rglob("*") if p.is_file() and p.name.endswith(RAW_SUFFIXES)),
        key=lambda p: p.relative_to(raw_root).as_posix(),
    )
    seen: set[bytes] = set()
    parts: dict[int, list[tuple[tuple, dict[str, Any]]]] = defaultdict(list)
    rejects = {"malformed": 0, "unusable_fields": 0, "duplicate": 0, "unreadable_files": 0}
    source_hashes: dict[str, str] = {}
    for src in sources:
        rel = src.relative_to(raw_root).as_posix()
        h = hashlib.sha256()
        h.update(src.read_bytes())
        source_hashes[rel] = h.hexdigest()
        try:
            for raw in _open_lines(src):
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    rejects["malformed"] += 1
                    continue
                ev = _normalize(rec) if isinstance(rec, dict) else None
                if ev is None:
                    rejects["unusable_fields" if isinstance(rec, dict) else "malformed"] += 1
                    continue
                ident = _identity(ev)
                if ident in seen:
                    rejects["duplicate"] += 1
                    continue
                seen.add(ident)
                parts[ev["local_write_ts_ms"] // HOUR_MS].append((_sort_key(ev, ident), ev))
        except Exception:
            rejects["unreadable_files"] += 1

    files: list[dict[str, Any]] = []
    feeds: set[str] = set()
    for hour in sorted(parts):
        rows = [ev for _, ev in sorted(parts[hour], key=lambda t: t[0])]
        body = b"".join(canonical_json(ev) + b"\n" for ev in rows)
        rel = f"hour={hour * HOUR_MS}.jsonl"
        (out_root / rel).write_bytes(body)
        feeds.update(feed_id(ev) for ev in rows)
        files.append({"path": rel, "rows": len(rows), "sha256": sha256_hex(body), "bytes": len(body)})

    manifest: dict[str, Any] = {
        "build_schema_version": BUILD_SCHEMA_VERSION,
        "dataset_label": dataset_label,
        "dataset_qualified": False,
        "qualification_authority": "NONE",
        "source_files": source_hashes,
        "rejects": rejects,
        "feeds": sorted(feeds),
        "files": files,
        "total_rows": sum(f["rows"] for f in files),
    }
    manifest["manifest_sha256"] = sha256_hex(canonical_json(manifest))
    (out_root / "MANIFEST.json").write_bytes(canonical_json(manifest) + b"\n")
    return manifest


def compare_builds(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    fa, fb = a["files"], b["files"]
    diffs = {
        "source_universe": a["source_files"] != b["source_files"],
        "file_count": len(fa) != len(fb),
        "partitioning": [f["path"] for f in fa] != [f["path"] for f in fb],
        "row_counts": [(f["path"], f["rows"]) for f in fa] != [(f["path"], f["rows"]) for f in fb] or a["total_rows"] != b["total_rows"],
        "content_hashes": [(f["path"], f["sha256"]) for f in fa] != [(f["path"], f["sha256"]) for f in fb],
        "rejects": a["rejects"] != b["rejects"],
        "feeds": a["feeds"] != b["feeds"],
        "manifest_hash": a["manifest_sha256"] != b["manifest_sha256"],
    }
    return {"identical": not any(diffs.values()), "differences": sorted(k for k, v in diffs.items() if v)}
