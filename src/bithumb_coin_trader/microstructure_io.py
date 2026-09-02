"""Streaming JSONL readers shared by raw and compressed integrity tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterator, Optional, Set

import zstandard


class CompressedInputError(ValueError):
    """Raised when a zstd input is truncated, corrupt, or has trailing bytes."""


@dataclass(frozen=True)
class JsonlScanResult:
    path: str
    compression: str
    logical_bytes: int
    sha256: str
    records: int
    valid_records: int
    invalid_json: int
    schema_mismatch: int
    missing_required_fields: int
    non_finite_numeric: int
    malformed_timestamps: int
    unknown_market: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _iter_raw_chunks(handle: BinaryIO, chunk_size: int) -> Iterator[bytes]:
    while True:
        chunk = handle.read(chunk_size)
        if not chunk:
            return
        yield chunk


def iter_zstd_decompressed_chunks(
    handle: BinaryIO,
    chunk_size: int = 1024 * 1024,
) -> Iterator[bytes]:
    """Yield decompressed bytes and reject incomplete or concatenated/trailing data."""

    decompressor = zstandard.ZstdDecompressor().decompressobj()
    try:
        for compressed in _iter_raw_chunks(handle, chunk_size):
            output = decompressor.decompress(compressed)
            if output:
                yield output
        tail = decompressor.flush()
        if tail:
            yield tail
    except zstandard.ZstdError as exc:
        raise CompressedInputError("zstd decompression failed") from exc
    if not decompressor.eof:
        raise CompressedInputError("zstd stream is truncated")
    if decompressor.unused_data:
        raise CompressedInputError("zstd stream contains trailing data")


def iter_logical_chunks(path: Path, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    if path.is_symlink():
        raise ValueError("symlink inputs are not allowed")
    if path.name.endswith(".jsonl.zst"):
        with path.open("rb") as handle:
            yield from iter_zstd_decompressed_chunks(handle, chunk_size=chunk_size)
        return
    if path.name.endswith(".jsonl"):
        with path.open("rb") as handle:
            yield from _iter_raw_chunks(handle, chunk_size)
        return
    raise ValueError("input must end in .jsonl or .jsonl.zst")


def iter_jsonl_lines(
    path: Path,
    chunk_size: int = 1024 * 1024,
    max_line_bytes: int = 16 * 1024 * 1024,
) -> Iterator[bytes]:
    buffer = b""
    for chunk in iter_logical_chunks(path, chunk_size=chunk_size):
        buffer += chunk
        while True:
            marker = buffer.find(b"\n")
            if marker < 0:
                break
            yield buffer[: marker + 1]
            buffer = buffer[marker + 1 :]
        if len(buffer) > max_line_bytes:
            raise ValueError("JSONL record exceeds the bounded line limit")
    if buffer:
        yield buffer


def scan_jsonl(
    path: Path,
    required_fields: Optional[Set[str]] = None,
) -> JsonlScanResult:
    """FULL-SCAN logical JSONL content without materializing decompressed files."""

    required = required_fields or {
        "exchange",
        "stream",
        "market",
        "exchange_ts",
        "local_recv_ts",
        "local_write_ts",
        "payload",
    }
    digest = hashlib.sha256()
    logical_bytes = 0
    records = 0
    valid_records = 0
    invalid_json = 0
    schema_mismatch = 0
    missing_required_fields = 0
    non_finite_numeric = 0
    malformed_timestamps = 0
    unknown_market = 0

    for raw_line in iter_jsonl_lines(path):
        digest.update(raw_line)
        logical_bytes += len(raw_line)
        records += 1
        non_finite_in_record = 0
        record_has_error = False

        def mark_non_finite(_: str) -> None:
            nonlocal non_finite_in_record
            non_finite_in_record += 1
            return None

        try:
            payload = json.loads(
                raw_line.decode("utf-8", errors="strict"),
                parse_float=str,
                parse_constant=mark_non_finite,
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            invalid_json += 1
            continue
        non_finite_numeric += non_finite_in_record
        if non_finite_in_record:
            record_has_error = True
        if not isinstance(payload, dict):
            schema_mismatch += 1
            continue
        missing = len(required - set(payload))
        missing_required_fields += missing
        record_has_error = record_has_error or missing > 0
        if not isinstance(payload.get("payload"), dict):
            schema_mismatch += 1
            record_has_error = True
        if str(payload.get("market", "")).upper() == "UNKNOWN":
            unknown_market += 1
            record_has_error = True
        for field in ("exchange_ts", "local_recv_ts", "local_write_ts"):
            value = payload.get(field)
            if value is None and field == "exchange_ts":
                continue
            if not isinstance(value, str):
                malformed_timestamps += 1
                record_has_error = True
                continue
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                malformed_timestamps += 1
                record_has_error = True
                continue
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                malformed_timestamps += 1
                record_has_error = True
        if not record_has_error:
            valid_records += 1

    return JsonlScanResult(
        path=str(path),
        compression="zstd" if path.name.endswith(".zst") else "none",
        logical_bytes=logical_bytes,
        sha256=digest.hexdigest(),
        records=records,
        valid_records=valid_records,
        invalid_json=invalid_json,
        schema_mismatch=schema_mismatch,
        missing_required_fields=missing_required_fields,
        non_finite_numeric=non_finite_numeric,
        malformed_timestamps=malformed_timestamps,
        unknown_market=unknown_market,
    )
