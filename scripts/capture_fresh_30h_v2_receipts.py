#!/usr/bin/env python3
"""Build Frozen V2 receipt/cohort evidence using read-only S3 observations.

The exporter copies local archive bytes into an empty or exact-identity-bound
evidence staging directory, reads each declared S3 object, records complete
prefix-listing provenance, and performs two separately timed latest-version
reads of every qualifying cohort receipt. The two-point interval defaults to
30 minutes.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import time
from typing import Any, Callable, Mapping, Sequence


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COHORT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}$")
MIN_IMMUTABILITY_INTERVAL_SECONDS = 1800


@dataclass(frozen=True)
class ReceiptSource:
    kind: str
    durability: str
    local_source: Path | None
    s3_key: str | None
    receipt_id: str | None = None
    cohort_id: str | None = None
    feed_id: str | None = None
    source_sha256: str | None = None
    outcome: str | None = None


CONTRACT_FEED_ID_RE = re.compile(r"^(?:bithumb|binance|upbit):[a-z]+:[^:/\s]+$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
PASSING_SLOT_COVERAGE_STATES = frozenset({"DATA_PRESENT", "VERIFIED_ZERO_EVENT"})


@dataclass(frozen=True)
class Readback:
    outcome: str
    sha256: str | None
    version_id: str | None
    size: int | None
    etag: str | None
    request_id: str | None
    captured_at_utc: str
    confirmed_authorized: bool
    http_status: int | None = None


def _utc_iso(now: Callable[[], datetime]) -> str:
    value = now()
    if value.tzinfo is None:
        raise ValueError("capture clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return not path.is_absolute() and path.as_posix() == value and all(part not in ("", ".", "..") for part in path.parts)


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"receipt source must be a regular file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"receipt source must contain a JSON object: {path}")
    return value


def _copy_stable(source: Path, destination: Path, expected_sha256: str | None = None) -> tuple[int, str]:
    if source.is_symlink():
        raise ValueError(f"receipt source must not be a symlink: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.parent.is_symlink() or destination.is_symlink():
        raise ValueError(f"receipt export path must not be a symlink: {destination}")
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(source_fd, "rb") as src:
        before = os.fstat(src.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError(f"receipt source must be a singly-linked regular file: {source}")
        dest_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(dest_fd, "wb") as dst:
            for block in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(block)
                digest.update(block)
                size += len(block)
            dst.flush()
            os.fsync(dst.fileno())
        after = os.fstat(src.fileno())
    before_state = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink)
    after_state = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink)
    if before_state != after_state or size != after.st_size:
        raise ValueError(f"receipt source changed during copy: {source}")
    actual_hash = digest.hexdigest()
    if expected_sha256 is not None and actual_hash != expected_sha256:
        raise ValueError(f"receipt source hash differs from its archive receipt: {source}")
    dir_fd = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return size, actual_hash


def _hash_regular_file(path: Path) -> tuple[int, str]:
    if path.is_symlink():
        raise ValueError(f"evidence path must not be a symlink: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError(f"evidence path must be a singly-linked regular file: {path}")
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
        after = os.fstat(stream.fileno())
    before_state = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink)
    after_state = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink)
    if before_state != after_state or size != after.st_size:
        raise ValueError(f"evidence file changed while hashing: {path}")
    return size, digest.hexdigest()


def _copy_or_verify(source: Path, destination: Path, expected_sha256: str | None = None) -> tuple[int, str]:
    if destination.is_symlink():
        raise ValueError(f"evidence destination must not be a symlink: {destination}")
    if not destination.exists():
        return _copy_stable(source, destination, expected_sha256)
    _source_size, source_hash = _hash_regular_file(source)
    dest_size, dest_hash = _hash_regular_file(destination)
    if source_hash != dest_hash or (expected_sha256 is not None and source_hash != expected_sha256):
        raise ValueError(f"existing evidence bytes differ from the captured source: {destination}")
    return dest_size, dest_hash


def _validate_bundle_identity(bundle_root: Path, *, run_id: str, epoch: str, bucket: str, prefix: str) -> None:
    if bundle_root.is_symlink():
        raise ValueError("bundle output directory must not be a symlink")
    if not bundle_root.exists():
        return
    if not bundle_root.is_dir():
        raise ValueError("bundle output path is not a directory")
    if not any(bundle_root.iterdir()):
        return
    identity_path = bundle_root / "sealed/identity.json"
    if identity_path.is_symlink() or not identity_path.is_file():
        raise FileExistsError("non-empty bundle output must contain the exact sealed identity")
    _hash_regular_file(identity_path)
    identity = _read_json(identity_path)
    if (identity.get("run_id") != run_id or identity.get("epoch") != epoch
            or identity.get("s3_bucket") != bucket
            or str(identity.get("s3_prefix", "")).rstrip("/") != prefix.rstrip("/")):
        raise ValueError("existing sealed identity differs from the exact receipt capture target")
    for directory, subdirectories, filenames in os.walk(bundle_root, followlinks=False):
        current = Path(directory)
        if any((current / name).is_symlink() for name in (*subdirectories, *filenames)):
            raise ValueError("identity-bound evidence staging tree must not contain symlinks")


def _validate_slot_payload(payload: Mapping[str, Any], path: Path, prefix: str,
                           expected_runtime_commit: str) -> None:
    cohort, feed_id = payload["cohort_id"], payload["feed_id"]
    feed_identity = payload.get("feed_identity")
    if not isinstance(feed_identity, str) or feed_identity.replace("/", ":") != feed_id:
        raise ValueError(f"slot receipt feed_identity does not bind feed_id: {path}")
    expected_id = f"slot-{cohort}-{hashlib.sha256(feed_identity.encode('utf-8')).hexdigest()}"
    if payload.get("receipt_id") != expected_id:
        raise ValueError(f"slot receipt receipt_id does not bind cohort and feed identity: {path}")
    if payload.get("coverage_state") not in PASSING_SLOT_COVERAGE_STATES:
        raise ValueError(f"slot receipt coverage_state is not a passing state: {path}")
    commit = payload.get("runtime_commit")
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise ValueError(f"slot receipt runtime_commit is not a full commit id: {path}")
    if commit != expected_runtime_commit:
        raise ValueError(f"slot receipt runtime_commit differs from the run anchor: {path}")
    archive = payload.get("coverage_archive")
    if (not isinstance(archive, Mapping) or not SHA256_RE.fullmatch(str(archive.get("source_sha256", "")))
            or not str(archive.get("remote_key", "")).startswith(prefix + "/")):
        raise ValueError(f"slot receipt coverage_archive binding is invalid: {path}")


def _discover_sources(data_dir: Path, run_id: str, epoch: str, bucket: str, prefix: str,
                      expected_runtime_commit: str) -> list[ReceiptSource]:
    if not COMMIT_RE.fullmatch(expected_runtime_commit):
        raise ValueError("a full 40-hex runtime commit anchor is required")
    archive_root = data_dir / "archive-receipts"
    sources: list[ReceiptSource] = []
    if archive_root.exists():
        if archive_root.is_symlink():
            raise ValueError("archive receipt root must not be a symlink")
        for path in sorted(archive_root.glob("cohort_*_finalized.json")):
            payload = _read_json(path)
            cohort = payload.get("cohort")
            if payload.get("run_id") != run_id or payload.get("epoch") != epoch:
                continue
            if not isinstance(cohort, str) or not COHORT_RE.fullmatch(cohort):
                raise ValueError(f"cohort receipt has an invalid cohort identity: {path}")
            if payload.get("cohort_qualification") == "QUALIFYING_FULL_HOUR":
                sources.append(ReceiptSource(
                    "COHORT_RECEIPT", "BOTH_REQUIRED", path,
                    f"{prefix}/archive-receipts/cohort_{cohort}_finalized.json",
                    receipt_id=f"cohort-{cohort}", cohort_id=cohort,
                ))
            else:
                sources.append(ReceiptSource("SKIPPED_PARTIAL_HOUR_RECEIPT", "LOCAL_ONLY", path, None,
                                             receipt_id=f"cohort-{cohort}", cohort_id=cohort,
                                             outcome="not_required"))

        slot_root = archive_root / "slot-receipts"
        for path in sorted(slot_root.rglob("*.slot-receipt.json")) if slot_root.exists() else ():
            payload = _read_json(path)
            if payload.get("run_id") != run_id or payload.get("epoch") != epoch:
                continue
            cohort, feed_id, key = payload.get("cohort_id"), payload.get("feed_id"), payload.get("s3_key")
            if not isinstance(cohort, str) or not COHORT_RE.fullmatch(cohort) or not isinstance(feed_id, str) or not feed_id:
                raise ValueError(f"slot receipt identity is invalid: {path}")
            if not CONTRACT_FEED_ID_RE.fullmatch(feed_id):
                raise ValueError(f"slot receipt feed_id is not in the exchange:stream:market contract form: {path}")
            if not isinstance(key, str) or not key.startswith(prefix + "/"):
                raise ValueError(f"slot receipt S3 key is outside the run prefix: {path}")
            _validate_slot_payload(payload, path, prefix, expected_runtime_commit)
            sources.append(ReceiptSource("SLOT_RECEIPT", "BOTH_REQUIRED", path, key,
                                         receipt_id=payload.get("receipt_id"), cohort_id=cohort,
                                         feed_id=feed_id))

        for path in sorted(archive_root.rglob("*.archive-receipt.json")):
            if path.is_relative_to(archive_root / "slot-receipts") or path.name.startswith("cohort_"):
                continue
            payload = _read_json(path)
            if payload.get("run_id") != run_id or payload.get("collector_epoch") != epoch:
                continue
            kind = payload.get("artifact_kind")
            if kind not in {"RAW_DATA", "COVERAGE_EVIDENCE"}:
                continue
            source_rel = payload.get("source_path")
            key = payload.get("remote_key")
            digest = payload.get("compressed_sha256")
            if not isinstance(source_rel, str) or not _safe_relative(source_rel):
                raise ValueError(f"archive receipt source path is invalid: {path}")
            compressed_root = data_dir / "compressed" / ("coverage" if kind == "COVERAGE_EVIDENCE" else "")
            compressed = compressed_root / Path(*PurePosixPath(source_rel).parts[:-1]) / (PurePosixPath(source_rel).name + ".zst")
            if not isinstance(key, str) or not key.startswith(prefix + "/") or not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
                sources.append(ReceiptSource("FILE_RECEIPT", "BOTH_REQUIRED", compressed, None,
                                             source_sha256=None, outcome="NOT_CHECKED"))
            else:
                sources.append(ReceiptSource("FILE_RECEIPT", "BOTH_REQUIRED", compressed, key, source_sha256=digest))

    terminal = data_dir / "terminal" / "terminal-receipt.json"
    if terminal.exists():
        terminal_payload = _read_json(terminal)
        if terminal_payload.get("run_id") == run_id and terminal_payload.get("epoch") == epoch:
            sources.append(ReceiptSource("TERMINAL_RECEIPT", "BOTH_REQUIRED", terminal,
                                          f"{prefix}/terminal/terminal-receipt.json",
                                          receipt_id="terminal-receipt"))

    ids = [(row.kind, row.receipt_id) for row in sources if row.receipt_id]
    keys = [row.s3_key for row in sources if row.s3_key]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate typed receipt identities were discovered")
    if len(keys) != len(set(keys)):
        raise ValueError("multiple local receipts bind to the same S3 key")
    return sources


def _get_object(
    s3: Any, *, bucket: str, key: str, destination: Path | None,
    now: Callable[[], datetime],
) -> Readback:
    response = s3.get_object(Bucket=bucket, Key=key)
    response_meta = response.get("ResponseMetadata", {})
    request_id = response_meta.get("RequestId") if isinstance(response_meta, Mapping) else None
    body = response.get("Body")
    if body is None or not hasattr(body, "read"):
        raise ValueError(f"S3 GetObject returned no body for {key}")
    digest = hashlib.sha256()
    size = 0
    target_fd = None
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.parent.is_symlink() or destination.is_symlink():
            raise ValueError(f"S3 readback destination must not be a symlink: {destination}")
        target_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    target = os.fdopen(target_fd, "wb") if target_fd is not None else None
    try:
        while True:
            block = body.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
            size += len(block)
            if target is not None:
                target.write(block)
        if target is not None:
            target.flush()
            os.fsync(target.fileno())
    finally:
        if target is not None:
            target.close()
        close = getattr(body, "close", None)
        if callable(close):
            close()
    expected_size = response.get("ContentLength")
    if type(expected_size) is int and expected_size != size:
        raise ValueError(f"S3 object body size differs from ContentLength for {key}")
    if destination is not None:
        dir_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    return Readback(
        "success", digest.hexdigest(), response.get("VersionId"), size,
        response.get("ETag"), request_id, _utc_iso(now), True,
        response_meta.get("HTTPStatusCode") if isinstance(response_meta, Mapping) else None,
    )


def _list_complete_prefix(s3: Any, sts: Any, bucket: str, prefix: str, now: Callable[[], datetime]) -> dict[str, Any]:
    keys: list[str] = []
    request_ids: list[str] = []
    token: str | None = None
    while True:
        args: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix.rstrip("/") + "/"}
        if token:
            args["ContinuationToken"] = token
        response = s3.list_objects_v2(**args)
        meta = response.get("ResponseMetadata", {})
        request_id = meta.get("RequestId") if isinstance(meta, Mapping) else None
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("S3 prefix page has no request ID provenance")
        request_ids.append(request_id)
        for entry in response.get("Contents", []) or []:
            key = entry.get("Key") if isinstance(entry, Mapping) else None
            if not isinstance(key, str) or not key.startswith(prefix.rstrip("/") + "/"):
                raise ValueError("S3 prefix listing returned a malformed or out-of-scope key")
            keys.append(key)
        truncated = response.get("IsTruncated") is True
        next_token = response.get("NextContinuationToken")
        if truncated and (not isinstance(next_token, str) or not next_token or next_token == token):
            raise ValueError("S3 prefix listing pagination did not advance")
        if not truncated:
            token = None
            break
        token = next_token
    if len(keys) != len(set(keys)):
        raise ValueError("S3 prefix listing returned duplicate object keys")
    caller = sts.get_caller_identity()
    caller_arn = caller.get("Arn") if isinstance(caller, Mapping) else None
    if not isinstance(caller_arn, str) or not caller_arn:
        raise ValueError("STS caller identity did not return an ARN")
    return {
        "outcome": "success", "bucket": bucket, "prefix": prefix.rstrip("/"),
        "query_prefix": prefix.rstrip("/") + "/", "complete": True,
        "next_token": None, "objects": [{"key": key} for key in sorted(keys)],
        "request_id": request_ids[-1], "request_ids": request_ids,
        "caller_arn": caller_arn, "captured_at_utc": _utc_iso(now),
    }


def _copy_terminal_witness(data_dir: Path, bundle_root: Path) -> None:
    source = data_dir / "terminal" / "terminal-witness.json"
    if source.exists():
        _copy_or_verify(source, bundle_root / "terminal" / "terminal-witness.json")


def _capture_terminal_versions(s3: Any, *, bucket: str, key: str, readback: Readback) -> dict[str, Any]:
    versions: list[str] = []
    latest_versions: list[str] = []
    markers: list[dict[str, Any]] = []
    key_marker: str | None = None
    version_marker: str | None = None
    while True:
        args: dict[str, Any] = {"Bucket": bucket, "Prefix": key}
        if key_marker:
            args["KeyMarker"] = key_marker
        if version_marker:
            args["VersionIdMarker"] = version_marker
        response = s3.list_object_versions(**args)
        for item in response.get("Versions", []):
            if item.get("Key") != key or not isinstance(item.get("VersionId"), str):
                continue
            versions.append(item["VersionId"])
            if item.get("IsLatest") is True:
                latest_versions.append(item["VersionId"])
        markers.extend(item for item in response.get("DeleteMarkers", []) if item.get("Key") == key)
        if response.get("IsTruncated") is not True:
            break
        key_marker, version_marker = response.get("NextKeyMarker"), response.get("NextVersionIdMarker")
        if not isinstance(key_marker, str) or not key_marker:
            raise ValueError("S3 version pagination is truncated without a next key marker")
    if len(latest_versions) != 1 or latest_versions[0] != readback.version_id:
        raise ValueError("terminal unversioned GetObject no longer identifies the latest S3 object version")
    return {
        "outcome": readback.outcome, "bucket": bucket, "key": key,
        "VersionId": readback.version_id, "requested_version_id": None,
        "latest_delete_marker": any(marker.get("IsLatest") is True for marker in markers),
        "version_ids": versions, "sha256": readback.sha256,
        "byte_length": readback.size, "ContentLength": readback.size,
        "ETag": readback.etag, "request_id": readback.request_id,
        "http_status": readback.http_status,
        "caller_arn": None, "captured_at_utc": readback.captured_at_utc,
    }


def _write_json_create(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError(f"evidence JSON path must not be a symlink: {path}")
    data = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def capture_frozen_v2_receipts(
    *,
    data_dir: Path,
    bundle_root: Path,
    run_id: str,
    epoch: str,
    bucket: str,
    prefix: str,
    s3: Any,
    sts: Any,
    expected_runtime_commit: str,
    observation_interval_seconds: float = 1800,
    sleep_fn: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Create receipt-inventory.json, cohorts.json and receipt byte readbacks."""
    if observation_interval_seconds < MIN_IMMUTABILITY_INTERVAL_SECONDS:
        raise ValueError("immutability observations must be at least 30 minutes apart")
    if not run_id or not epoch or not bucket or not prefix:
        raise ValueError("exact run, epoch, bucket and prefix are required")
    prefix = prefix.rstrip("/")
    _validate_bundle_identity(bundle_root, run_id=run_id, epoch=epoch, bucket=bucket, prefix=prefix)
    sources = _discover_sources(data_dir, run_id, epoch, bucket, prefix, expected_runtime_commit)
    listing = _list_complete_prefix(s3, sts, bucket, prefix, now)
    listed_keys = {obj["key"] for obj in listing["objects"]}
    receipt_rows: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    first_cohort_reads: list[tuple[ReceiptSource, Readback]] = []
    declared_keys: set[str] = set()
    terminal_readback: Readback | None = None

    for index, source in enumerate(sources):
        row: dict[str, Any] = {"type": source.kind, "durability": source.durability}
        if source.receipt_id is not None:
            row["receipt_id"] = source.receipt_id
        if source.cohort_id is not None:
            row["cohort_id"] = source.cohort_id
        if source.feed_id is not None:
            row["feed_id"] = source.feed_id
        if source.outcome is not None:
            row["s3_get_outcome"] = source.outcome
        destination: Path | None = None
        local_hash: str | None = None
        if source.local_source is not None and source.local_source.exists():
            name = hashlib.sha256((source.s3_key or source.receipt_id or str(index)).encode("utf-8")).hexdigest()
            suffix = source.local_source.suffix or ".bin"
            rel = f"terminal/receipts/{name}{suffix}"
            destination = bundle_root / rel
            _size, local_hash = _copy_stable(source.local_source, destination, source.source_sha256)
            row["local_path"] = rel
            row["local_sha256"] = local_hash
        elif source.durability == "BOTH_REQUIRED":
            row["s3_get_outcome"] = "NOT_CHECKED"

        if source.kind == "SLOT_RECEIPT":
            row["local_receipt_path"] = row.get("local_path")
            row["local_receipt_sha256"] = row.get("local_sha256")
        if source.kind == "SKIPPED_PARTIAL_HOUR_RECEIPT":
            row["s3_get_outcome"] = "not_required"

        if source.s3_key is not None:
            declared_keys.add(source.s3_key)
            row["s3_key"] = source.s3_key
            if source.s3_key in listed_keys:
                remote_name = hashlib.sha256(source.s3_key.encode("utf-8")).hexdigest()
                readback_rel = f"terminal/s3-readback/receipts/{remote_name}.bin"
                readback_path = bundle_root / readback_rel
                try:
                    first = _get_object(s3, bucket=bucket, key=source.s3_key,
                                        destination=readback_path, now=now)
                except Exception as exc:
                    outcome = getattr(exc, "response", {}).get("Error", {}).get("Code", "ERROR")
                    row.update({"s3_get_outcome": outcome, "confirmed_authorized": False})
                else:
                    row.update({"s3_get_outcome": "success", "confirmed_authorized": True,
                                "s3_sha256": first.sha256, "version_id": first.version_id,
                                "s3_readback_path": readback_rel})
                    if source.kind == "SLOT_RECEIPT":
                        row["s3_receipt_sha256"] = first.sha256
                    if source.kind == "TERMINAL_RECEIPT":
                        terminal_readback = first
                    if source.kind == "COHORT_RECEIPT" and source.receipt_id:
                        first_cohort_reads.append((source, first))
                        observations.append({"receipt_id": source.receipt_id,
                                             "captured_at_utc": first.captured_at_utc,
                                             "sha256": first.sha256, "version_id": first.version_id,
                                             "request_id": first.request_id})
            else:
                row.update({"s3_get_outcome": "NoSuchKey", "confirmed_authorized": True})
        receipt_rows.append(row)

    # Each second point is a distinct latest-version GetObject after the stated delay.
    if first_cohort_reads:
        sleep_fn(observation_interval_seconds)
        for source, _first in first_cohort_reads:
            assert source.s3_key is not None and source.receipt_id is not None
            try:
                second = None
                for attempt in range(3):
                    try:
                        second = _get_object(s3, bucket=bucket, key=source.s3_key, destination=None, now=now)
                        break
                    except Exception as exc:
                        if attempt == 2 or getattr(exc, "response", {}).get("Error", {}).get("Code") in {"NoSuchKey", "AccessDenied"}:
                            raise
                        sleep_fn(5)
                assert second is not None
            except Exception as exc:
                second_outcome = getattr(exc, "response", {}).get("Error", {}).get("Code", "ERROR")
                observations.append({"receipt_id": source.receipt_id,
                                     "captured_at_utc": _utc_iso(now),
                                     "sha256": None, "version_id": None,
                                     "outcome": second_outcome})
            else:
                observations.append({"receipt_id": source.receipt_id,
                                     "captured_at_utc": second.captured_at_utc,
                                     "sha256": second.sha256, "version_id": second.version_id,
                                     "request_id": second.request_id})

    # Listed objects with no local counterpart are deliberately NOT declared: the frozen auditor
    # reports them as unexpected_s3_object instead of having capture launder them as OPTIONAL.
    unknown = sorted(listed_keys - declared_keys)
    observer_prefix = f"{prefix}/observability/"

    terminal_key = f"{prefix}/terminal/terminal-receipt.json"
    terminal_metadata: dict[str, Any] | None = None
    if terminal_readback is not None:
        terminal_metadata = _capture_terminal_versions(s3, bucket=bucket, key=terminal_key, readback=terminal_readback)
        identity = sts.get_caller_identity()
        terminal_metadata["caller_arn"] = identity.get("Arn") if isinstance(identity, Mapping) else None
        terminal_payload = bundle_root / "terminal/s3-readback/terminal-receipt.json"
        source_row = next((row for row in receipt_rows if row.get("type") == "TERMINAL_RECEIPT"), None)
        # The bytes were already copied to a hash-named receipt file above. Create the canonical
        # audit path from those captured remote bytes without a second S3 request.
        captured_path = bundle_root / str(source_row["s3_readback_path"]) if source_row else None
        if captured_path is not None:
            _copy_or_verify(captured_path, terminal_payload, terminal_readback.sha256)
        _write_json_create(bundle_root / "terminal/s3-readback.json", terminal_metadata)
    else:
        terminal_metadata = {
            "outcome": "NOT_CHECKED", "bucket": bucket, "key": terminal_key,
            "requested_version_id": None, "latest_delete_marker": False,
            "version_ids": [], "captured_at_utc": _utc_iso(now),
        }
        _write_json_create(bundle_root / "terminal/s3-readback.json", terminal_metadata)

    _copy_terminal_witness(data_dir, bundle_root)
    inventory = {
        "schema_version": 1,
        "receipts": receipt_rows,
        "unexpected_s3_objects": [key for key in unknown if key.startswith(observer_prefix)],
        "s3_prefix_listing": listing,
        "required_immutable_receipt_ids": sorted(
            row["receipt_id"] for row in receipt_rows
            if row.get("type") == "COHORT_RECEIPT" and isinstance(row.get("receipt_id"), str)
        ),
        "observations": observations,
        "immutability_observation_interval_seconds": observation_interval_seconds,
    }
    _write_json_create(bundle_root / "terminal/receipt-inventory.json", inventory)
    _build_cohort_summary(
        data_dir=data_dir, bundle_root=bundle_root, receipt_rows=receipt_rows,
        run_id=run_id, epoch=epoch,
    )
    return inventory


def _build_cohort_summary(
    *, data_dir: Path, bundle_root: Path, receipt_rows: list[dict[str, Any]],
    run_id: str, epoch: str,
) -> None:
    journal_root = data_dir / "coverage" / "journals"
    journal_paths = sorted(journal_root.glob("journal_*.json")) if journal_root.exists() else []
    if not journal_paths:
        raise ValueError("source coverage journals are missing")
    full: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    source_journals: list[dict[str, Any]] = []
    cohort_rows = {row.get("cohort_id"): row for row in receipt_rows if row.get("type") == "COHORT_RECEIPT"}
    slot_rows = {(row.get("cohort_id"), row.get("feed_id")): row for row in receipt_rows if row.get("type") == "SLOT_RECEIPT"}
    parsed_bounds: list[tuple[str, str]] = []
    for source in journal_paths:
        if source.is_symlink():
            raise ValueError(f"source journal must not be a symlink: {source}")
        payload = _read_json(source)
        cohort_id = payload.get("cohort_utc")
        observations = payload.get("observations")
        if not isinstance(cohort_id, str) or not COHORT_RE.fullmatch(cohort_id) or not isinstance(observations, list) or not observations:
            raise ValueError(f"source journal identity/schema is invalid: {source}")
        starts = {row.get("observation_start_utc") for row in observations if isinstance(row, Mapping)}
        ends = {row.get("observation_end_utc") for row in observations if isinstance(row, Mapping)}
        qualifications = {row.get("cohort_qualification") for row in observations if isinstance(row, Mapping)}
        if len(starts) != 1 or len(ends) != 1 or len(qualifications) != 1:
            raise ValueError(f"source journal interval is inconsistent: {source}")
        start, end = next(iter(starts)), next(iter(ends))
        qualification = next(iter(qualifications))
        if not isinstance(start, str) or not isinstance(end, str) or qualification not in {"QUALIFYING_FULL_HOUR", "TOUCHED_PARTIAL"}:
            raise ValueError(f"source journal interval/qualification is invalid: {source}")
        for observation in observations:
            if not isinstance(observation, Mapping):
                raise ValueError(f"source journal has a non-object observation: {source}")
            segments = observation.get("session_segments")
            if segments is None:
                continue
            if not isinstance(segments, list):
                raise ValueError(f"source journal session_segments is malformed: {source}")
            for segment in segments:
                if not isinstance(segment, Mapping):
                    raise ValueError(f"source journal session segment is malformed: {source}")
                if segment.get("collector_run_id") != run_id or segment.get("collector_epoch") != epoch:
                    raise ValueError(f"source journal session identity differs from the exact run: {source}")
        dest_rel = f"terminal/journals/{source.name}"
        dest = bundle_root / dest_rel
        _size, digest = _copy_stable(source, dest)
        source_journals.append({"cohort_id": cohort_id, "path": dest_rel, "sha256": digest})
        parsed_bounds.append((start, end))
        row: dict[str, Any] = {
            "cohort_id": cohort_id, "cohort_qualification": qualification,
            "observation_start_utc": start, "observation_end_utc": end,
            "journal_path": dest_rel, "journal_sha256": digest,
        }
        if qualification == "QUALIFYING_FULL_HOUR":
            feed_slots: list[dict[str, Any]] = []
            for observation in observations:
                if not isinstance(observation, Mapping):
                    continue
                feed = observation.get("feed")
                if not isinstance(feed, Mapping) or not all(isinstance(feed.get(k), str) for k in ("exchange", "stream", "market")):
                    raise ValueError(f"journal feed identity is invalid: {source}")
                feed_id = f"{feed['exchange']}:{feed['stream']}:{feed['market']}"
                slot = slot_rows.get((cohort_id, feed_id), {})
                feed_slots.append({
                    "feed_id": feed_id,
                    "local_receipt_path": slot.get("local_path"),
                    "local_receipt_sha256": slot.get("local_sha256"),
                    "s3_get_outcome": slot.get("s3_get_outcome"),
                    "s3_receipt_sha256": slot.get("s3_sha256"),
                    "s3_readback_path": slot.get("s3_readback_path"),
                    "version_id": slot.get("version_id"),
                    "confirmed_authorized": slot.get("confirmed_authorized"),
                })
            cohort_receipt = cohort_rows.get(cohort_id, {})
            row.update({
                "feed_slots": feed_slots,
                "local_receipt_path": cohort_receipt.get("local_path"),
                "local_receipt_sha256": cohort_receipt.get("local_sha256"),
                "s3_get_outcome": cohort_receipt.get("s3_get_outcome"),
                "s3_receipt_sha256": cohort_receipt.get("s3_receipt_sha256", cohort_receipt.get("s3_sha256")),
                "s3_readback_path": cohort_receipt.get("s3_readback_path"),
                "version_id": cohort_receipt.get("version_id"),
                "confirmed_authorized": cohort_receipt.get("confirmed_authorized"),
            })
            full.append(row)
        else:
            partial.append(row)
    if len(source_journals) != len({row["cohort_id"] for row in source_journals}):
        raise ValueError("duplicate source cohort journal IDs")
    cohorts = {
        "schema_version": 1,
        "evidence_classification": "RECONSTRUCTED_OBSERVATION",
        "actual_start_utc": min(start for start, _ in parsed_bounds),
        "actual_end_utc": max(end for _, end in parsed_bounds),
        "source_journals": source_journals,
        "cohorts": full,
        "partial_cohorts": partial,
    }
    _write_json_create(bundle_root / "terminal/cohorts.json", cohorts)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--epoch", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--region")
    parser.add_argument("--observation-interval-seconds", type=float, default=1800)
    parser.add_argument("--runtime-commit", required=True,
                        help="40-hex sealed runtime commit every slot receipt must carry")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        import boto3  # pyright: ignore[reportMissingImports]
        s3 = boto3.client("s3", region_name=args.region)
        sts = boto3.client("sts", region_name=args.region)
        capture_frozen_v2_receipts(
            data_dir=args.data_dir, bundle_root=args.bundle_root,
            run_id=args.run_id, epoch=args.epoch, bucket=args.bucket,
            prefix=args.prefix, s3=s3, sts=sts,
            observation_interval_seconds=args.observation_interval_seconds,
            expected_runtime_commit=args.runtime_commit,
        )
    except Exception as exc:
        print(f"CAPTURE_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"CAPTURED: {args.bundle_root / 'terminal/receipt-inventory.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
