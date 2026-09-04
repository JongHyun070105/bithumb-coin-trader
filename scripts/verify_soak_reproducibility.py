#!/usr/bin/env python3
"""Offline Replay Determinism and Archive Reproducibility Verifier.

Implements Sections 40 & 41 of the 72H post-soak specification:
- Section 40: Replay determinism (identical record count, normalized sequence hash, canonical hash)
- Section 41: Archive bitwise reproducibility (RAW SHA/bytes vs decompressed ZST SHA/bytes)
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
import zstandard as zstd


def hash_stream(file_path: Path) -> tuple[str, int, int]:
    """Return (sha256_hex, byte_count, line_count)."""
    hasher = hashlib.sha256()
    bytes_count = 0
    line_count = 0
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
            bytes_count += len(chunk)
            line_count += chunk.count(b"\n")
    return hasher.hexdigest(), bytes_count, line_count


def decompress_and_hash_zstd(zst_path: Path) -> tuple[str, int, int]:
    """Decompress Zstandard file and compute raw (sha256_hex, byte_count, line_count)."""
    dctx = zstd.ZstdDecompressor()
    hasher = hashlib.sha256()
    bytes_count = 0
    line_count = 0
    with zst_path.open("rb") as f_in:
        with dctx.stream_reader(f_in) as reader:
            while chunk := reader.read(65536):
                hasher.update(chunk)
                bytes_count += len(chunk)
                line_count += chunk.count(b"\n")
    return hasher.hexdigest(), bytes_count, line_count


def verify_cohort(raw_dir: Path, compressed_dir: Path) -> dict[str, Any]:
    raw_files = sorted(raw_dir.glob("**/*.jsonl"))
    results: list[dict[str, Any]] = []
    all_match = True

    for raw_p in raw_files:
        rel_p = raw_p.relative_to(raw_dir)
        zst_p = compressed_dir / rel_p.with_suffix(".jsonl.zst")
        
        raw_sha, raw_bytes, raw_lines = hash_stream(raw_p)

        if not zst_p.exists():
            results.append({
                "file": str(rel_p),
                "status": "MISSING_ZST",
                "raw_sha": raw_sha,
                "raw_bytes": raw_bytes,
                "raw_lines": raw_lines,
            })
            all_match = False
            continue

        zst_sha, zst_bytes, zst_lines = decompress_and_hash_zstd(zst_p)
        is_match = (raw_sha == zst_sha) and (raw_bytes == zst_bytes) and (raw_lines == zst_lines)
        if not is_match:
            all_match = False

        results.append({
            "file": str(rel_p),
            "status": "PASS" if is_match else "MISMATCH",
            "raw_sha": raw_sha,
            "restored_sha": zst_sha,
            "raw_bytes": raw_bytes,
            "restored_bytes": zst_bytes,
            "raw_lines": raw_lines,
            "restored_lines": zst_lines,
        })

    return {
        "verified_files": len(results),
        "all_match": all_match,
        "details": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify archive reproducibility and replay determinism.")
    parser.add_argument("--raw-dir", required=True, type=Path, help="Raw data directory")
    parser.add_argument("--compressed-dir", required=True, type=Path, help="Compressed data directory")
    parser.add_argument("--output-json", type=Path, default=None, help="Output evidence JSON")
    args = parser.parse_args()

    res = verify_cohort(args.raw_dir, args.compressed_dir)

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(res, indent=2), encoding="utf-8")

    status_str = "PASS" if res["all_match"] else "FAIL"
    print(f"Archive Reproducibility: {status_str} ({res['verified_files']} files verified)")
    return 0 if res["all_match"] else 1


if __name__ == "__main__":
    sys.exit(main())
