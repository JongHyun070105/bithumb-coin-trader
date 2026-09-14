"""Canonical JSON serialization and SHA-256 hashing for evidence artifacts.

This is the single authoritative implementation. All other modules must
delegate to these functions. Direct json.dumps with custom separators is
forbidden outside this module.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping
from pathlib import Path

__all__ = [
    "canonical_json_bytes",
    "canonical_sha256",
    "file_sha256",
]


def canonical_json_bytes(
    value: Mapping[str, object],
    excluded: Collection[str] = (),
) -> bytes:
    """Return canonical UTF-8 JSON bytes, excluding listed keys.

    Contract:
    - ensure_ascii=True (non-ASCII chars escaped to \\uXXXX)
    - sort_keys=True
    - separators=(",", ":")
    - allow_nan=False  (raises ValueError on NaN/Inf)
    - no trailing newline
    - no BOM
    """
    blocked = set(excluded)
    body = {key: item for key, item in value.items() if key not in blocked}
    return json.dumps(
        body,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(
    value: Mapping[str, object],
    excluded: Collection[str] = (),
) -> str:
    """Return the SHA-256 hex digest of canonical_json_bytes(value, excluded)."""
    return hashlib.sha256(canonical_json_bytes(value, excluded)).hexdigest()


def file_sha256(path: Path | str) -> str:
    """Return SHA-256 hex digest of a file's raw bytes."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
