"""Fail-closed, one-partition-at-a-time archive pipeline for prospective AWS epochs."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import base64
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import time
from typing import Any, BinaryIO, ContextManager, Dict, Iterator, Optional, Protocol, Tuple

import zstandard

from .microstructure_io import CompressedInputError, iter_zstd_decompressed_chunks


RECEIPT_SCHEMA_VERSION = 1
PARTITION_PATTERN = re.compile(r"_(\d{4}-\d{2}-\d{2})_(\d{2})\.jsonl$")
MAX_S3_PUT_OBJECT_BYTES = 5 * 1024**3


class ArchiveState(str, Enum):
    DISCOVERED = "DISCOVERED"
    RAW_VERIFIED = "RAW_VERIFIED"
    COMPRESSED = "COMPRESSED"
    COMPRESSED_VERIFIED = "COMPRESSED_VERIFIED"
    ARCHIVED = "ARCHIVED"
    REMOTE_VERIFIED = "REMOTE_VERIFIED"
    RESTORE_VERIFIED = "RESTORE_VERIFIED"
    CLEANUP_ELIGIBLE = "CLEANUP_ELIGIBLE"
    CLEANED = "CLEANED"
    FAILED = "FAILED"


class OwnershipViolationError(RuntimeError):
    """Raised when runtime artifact or lock file violates expected process ownership."""
    pass


def verify_runtime_ownership(
    paths: Iterable[Path],
    expected_owner: Optional[str] = None,
) -> None:
    """Fail-closed check that runtime files/directories match expected user ownership."""
    try:
        import pwd
    except ImportError:
        return

    if expected_owner:
        try:
            expected_uid = pwd.getpwnam(expected_owner).pw_uid
        except KeyError:
            raise OwnershipViolationError(
                f"Fail-closed ownership check: expected owner '{expected_owner}' does not exist on this host"
            )
    else:
        expected_uid = os.getuid()

    for item in paths:
        path = Path(item)
        if not path.exists():
            continue
        if path.is_symlink():
            raise ValueError(f"symlink runtime path is not allowed: {path}")
        try:
            st = path.stat()
        except OSError as exc:
            raise OwnershipViolationError(f"Fail-closed ownership check failed on {path}: {exc}") from exc
        if st.st_uid != expected_uid:
            try:
                owner_name = pwd.getpwuid(st.st_uid).pw_name
            except KeyError:
                owner_name = str(st.st_uid)
            try:
                expected_name = pwd.getpwuid(expected_uid).pw_name
            except KeyError:
                expected_name = str(expected_uid)
            raise OwnershipViolationError(
                f"Fail-closed ownership violation: {path} is owned by UID {st.st_uid} ({owner_name}), "
                f"expected UID {expected_uid} ({expected_name})"
            )
        if path.is_dir():
            for child in path.rglob("*"):
                if child.is_symlink():
                    raise ValueError(f"symlink runtime path is not allowed: {child}")
                try:
                    cst = child.stat()
                except OSError as exc:
                    raise OwnershipViolationError(f"Fail-closed ownership check failed on {child}: {exc}") from exc
                if cst.st_uid != expected_uid:
                    try:
                        owner_name = pwd.getpwuid(cst.st_uid).pw_name
                    except KeyError:
                        owner_name = str(cst.st_uid)
                    try:
                        expected_name = pwd.getpwuid(expected_uid).pw_name
                    except KeyError:
                        expected_name = str(expected_uid)
                    raise OwnershipViolationError(
                        f"Fail-closed ownership violation: {child} is owned by UID {cst.st_uid} ({owner_name}), "
                        f"expected UID {expected_uid} ({expected_name})"
                    )


@dataclass(frozen=True)
class RemoteObject:
    key: str
    size: int
    checksum_sha256_base64: Optional[str]
    version_id: Optional[str] = None


class ArchiveStore(Protocol):
    def upload(self, local_path: Path, key: str, checksum_sha256_hex: str) -> RemoteObject:
        ...

    def head(self, key: str) -> RemoteObject:
        ...

    def exists(self, key: str) -> bool:
        ...

    def open_download(self, key: str) -> ContextManager[BinaryIO]:
        ...


@dataclass
class ArchiveReceipt:
    schema_version: int
    state: str
    environment_id: str
    run_id: str
    collector_epoch: str
    partition: str
    raw_size: Optional[int] = None
    raw_sha256: Optional[str] = None
    raw_record_count: Optional[int] = None
    compressed_size: Optional[int] = None
    compressed_sha256: Optional[str] = None
    compression_algorithm: str = "zstd"
    compression_level: int = 1
    compression_version: str = field(default_factory=lambda: zstandard.__version__)
    remote_key: Optional[str] = None
    remote_size: Optional[int] = None
    remote_checksum: Optional[str] = None
    remote_version_id: Optional[str] = None
    raw_verified_at: Optional[str] = None
    compressed_verified_at: Optional[str] = None
    remote_verified_at: Optional[str] = None
    restore_verified_at: Optional[str] = None
    cleanup_eligible: bool = False
    cleanup_completed_at: Optional[str] = None
    failure_stage: Optional[str] = None
    failure_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ArchiveReceipt":
        if payload.get("schema_version") != RECEIPT_SCHEMA_VERSION:
            raise ValueError("unsupported archive receipt schema")
        return cls(**payload)


class MemoryArchiveStore:
    """In-memory store for deterministic tests; never contacts AWS."""

    def __init__(self) -> None:
        self.objects: Dict[str, bytes] = {}
        self.upload_calls = 0
        self.head_override: Optional[RemoteObject] = None

    def upload(self, local_path: Path, key: str, checksum_sha256_hex: str) -> RemoteObject:
        validate_archive_key(key)
        data = local_path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != checksum_sha256_hex:
            raise ValueError("upload source checksum changed")
        self.upload_calls += 1
        self.objects[key] = data
        return self.head(key)

    def head(self, key: str) -> RemoteObject:
        validate_archive_key(key)
        if self.head_override is not None:
            return self.head_override
        if key not in self.objects:
            raise FileNotFoundError(key)
        data = self.objects[key]
        return RemoteObject(
            key=key,
            size=len(data),
            checksum_sha256_base64=base64.b64encode(hashlib.sha256(data).digest()).decode("ascii"),
            version_id="memory-v1",
        )

    def exists(self, key: str) -> bool:
        validate_archive_key(key)
        return key in self.objects

    @contextmanager
    def open_download(self, key: str) -> Iterator[BinaryIO]:
        validate_archive_key(key)
        if key not in self.objects:
            raise FileNotFoundError(key)
        handle = io.BytesIO(self.objects[key])
        try:
            yield handle
        finally:
            handle.close()


class FileArchiveStore:
    """Local streaming archive store used by dry-runs and fixture E2E tests."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        validate_archive_key(key)
        candidate = (self.root / PurePosixPath(key)).resolve()
        if self.root not in candidate.parents:
            raise ValueError("archive key escapes store root")
        return candidate

    def upload(self, local_path: Path, key: str, checksum_sha256_hex: str) -> RemoteObject:
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            return self.head(key)
        temporary = _temporary_path(destination)
        source_hash = hashlib.sha256()
        try:
            with local_path.open("rb") as source, _exclusive_binary_file(temporary) as target:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    source_hash.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
        except Exception:
            _safe_unlink(temporary)
            raise
        if source_hash.hexdigest() != checksum_sha256_hex:
            _safe_unlink(temporary)
            raise ValueError("upload source checksum changed")
        os.replace(str(temporary), str(destination))
        _fsync_directory(destination.parent)
        return self.head(key)

    def head(self, key: str) -> RemoteObject:
        path = self._path(key)
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(key)
        checksum = _hash_file(path)[0]
        return RemoteObject(
            key=key,
            size=path.stat().st_size,
            checksum_sha256_base64=_hex_to_base64(checksum),
            version_id=None,
        )

    def exists(self, key: str) -> bool:
        path = self._path(key)
        return path.is_file() and not path.is_symlink()

    @contextmanager
    def open_download(self, key: str) -> Iterator[BinaryIO]:
        path = self._path(key)
        with path.open("rb") as handle:
            yield handle


class S3ArchiveStore:
    """S3 adapter using full-object SHA-256 with a single streaming PutObject."""

    def __init__(self, bucket: str, client: Optional[Any] = None) -> None:
        if not bucket or "/" in bucket:
            raise ValueError("invalid S3 bucket name")
        if client is None:
            import boto3

            client = boto3.client("s3")
        self.bucket = bucket
        self.client = client

    def upload(self, local_path: Path, key: str, checksum_sha256_hex: str) -> RemoteObject:
        validate_archive_key(key)
        size = local_path.stat().st_size
        if size > MAX_S3_PUT_OBJECT_BYTES:
            raise ValueError("partition exceeds fail-closed single PutObject limit")
        try:
            with local_path.open("rb") as handle:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=handle,
                    ContentLength=size,
                    ChecksumSHA256=_hex_to_base64(checksum_sha256_hex),
                    IfNoneMatch="*",
                )
        except Exception as exc:
            response = getattr(exc, "response", {})
            status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = response.get("Error", {}).get("Code")
            if status not in {409, 412} and code not in {"ConditionalRequestConflict", "PreconditionFailed"}:
                raise
            # Another worker won the immutable-key race. The caller's normal
            # size/checksum verification decides whether it is the same object.
        return self.head(key)

    def head(self, key: str) -> RemoteObject:
        validate_archive_key(key)
        response = self.client.head_object(Bucket=self.bucket, Key=key, ChecksumMode="ENABLED")
        return RemoteObject(
            key=key,
            size=int(response["ContentLength"]),
            checksum_sha256_base64=response.get("ChecksumSHA256"),
            version_id=response.get("VersionId"),
        )

    def exists(self, key: str) -> bool:
        try:
            self.head(key)
            return True
        except Exception as exc:
            response = getattr(exc, "response", {})
            status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = response.get("Error", {}).get("Code")
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    @contextmanager
    def open_download(self, key: str) -> Iterator[BinaryIO]:
        validate_archive_key(key)
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        try:
            yield body
        finally:
            body.close()


def validate_archive_key(key: str) -> None:
    if not key or key.startswith("/") or "//" in key or "\\" in key or "\x00" in key:
        raise ValueError("invalid archive key")
    path = PurePosixPath(key)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("invalid archive key")


def partition_hour(path: Path) -> datetime:
    name = path.name[:-4] if path.name.endswith(".zst") else path.name
    match = PARTITION_PATTERN.search(name)
    if match is None:
        raise ValueError("partition filename does not contain a UTC hour")
    return datetime.strptime("{}_{}".format(match.group(1), match.group(2)), "%Y-%m-%d_%H").replace(
        tzinfo=timezone.utc
    )


def is_closed_stable_partition(
    path: Path,
    raw_root: Path,
    now: Optional[datetime] = None,
    grace_period: timedelta = timedelta(minutes=10),
    active_paths: Tuple[Path, ...] = (),
    stability_wait_seconds: float = 1.0,
) -> bool:
    resolved_root = raw_root.resolve()
    resolved = path.resolve()
    if path.is_symlink() or resolved_root not in resolved.parents or not path.name.endswith(".jsonl"):
        return False
    active = {item.resolve() for item in active_paths}
    if resolved in active:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if partition_hour(path) + timedelta(hours=1) + grace_period > current.astimezone(timezone.utc):
        return False
    before = path.stat()
    if stability_wait_seconds > 0:
        time.sleep(stability_wait_seconds)
    after = path.stat()
    return (
        before.st_ino == after.st_ino
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
        and after.st_size > 0
    )


class ArchivePipeline:
    def __init__(
        self,
        raw_root: Path,
        manifest_root: Path,
        compressed_root: Path,
        receipt_root: Path,
        store: ArchiveStore,
        environment_id: str,
        run_id: str,
        collector_epoch: str,
        remote_prefix: str,
        compression_level: int = 1,
        disk_critical_percent: float = 90.0,
        expected_owner: Optional[str] = None,
    ) -> None:
        self.raw_root = raw_root.resolve()
        self.manifest_root = manifest_root.resolve()
        self.compressed_root = compressed_root.resolve()
        self.receipt_root = receipt_root.resolve()
        self.store = store
        self.environment_id = _required_identifier(environment_id, "environment_id")
        self.run_id = _required_identifier(run_id, "run_id")
        self.collector_epoch = _required_identifier(collector_epoch, "collector_epoch")
        validate_archive_key(remote_prefix + "/sentinel")
        self.remote_prefix = remote_prefix.rstrip("/")
        if not -22 <= compression_level <= 22:
            raise ValueError("invalid zstd compression level")
        self.compression_level = compression_level
        if not 0 < disk_critical_percent < 100:
            raise ValueError("disk_critical_percent must be between 0 and 100")
        self.disk_critical_percent = float(disk_critical_percent)
        self.expected_owner = expected_owner

    def verify_storage_ownership(self) -> None:
        verify_runtime_ownership(
            (self.raw_root, self.manifest_root, self.compressed_root, self.receipt_root),
            expected_owner=self.expected_owner,
        )

    def receipt_path(self, raw_path: Path) -> Path:
        relative = self._relative_raw(raw_path)
        return self.receipt_root / relative.parent / (relative.name + ".archive-receipt.json")

    def compressed_path(self, raw_path: Path) -> Path:
        relative = self._relative_raw(raw_path)
        return self.compressed_root / relative.parent / (relative.name + ".zst")

    def remote_key(self, raw_path: Path) -> str:
        relative = self._relative_raw(raw_path)
        key = "{}/{}.zst".format(self.remote_prefix, relative.as_posix())
        validate_archive_key(key)
        return key

    def finalize(
        self,
        raw_path: Path,
        cleanup_verified: bool = False,
        now: Optional[datetime] = None,
        grace_period: timedelta = timedelta(minutes=10),
        active_paths: Tuple[Path, ...] = (),
        stability_wait_seconds: float = 1.0,
    ) -> ArchiveReceipt:
        if raw_path.is_symlink():
            raise ValueError("raw partition symlinks are not allowed")
        raw_path = raw_path.resolve()
        receipt_path = self.receipt_path(raw_path)
        lock_path = receipt_path.with_suffix(receipt_path.suffix + ".lock")
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        with _partition_lock(lock_path, expected_owner=self.expected_owner):
            existing = self._load_receipt(receipt_path)
            if existing is not None and existing.state == ArchiveState.CLEANED.value:
                return existing
            if not raw_path.exists():
                raise FileNotFoundError("raw partition is missing and no CLEANED receipt exists")
            if not is_closed_stable_partition(
                raw_path,
                self.raw_root,
                now=now,
                grace_period=grace_period,
                active_paths=active_paths,
                stability_wait_seconds=stability_wait_seconds,
            ):
                raise ValueError("partition is active, unstable, outside raw root, or not past grace")
            receipt = existing or ArchiveReceipt(
                schema_version=RECEIPT_SCHEMA_VERSION,
                state=ArchiveState.DISCOVERED.value,
                environment_id=self.environment_id,
                run_id=self.run_id,
                collector_epoch=self.collector_epoch,
                partition=self._relative_raw(raw_path).as_posix(),
                compression_level=self.compression_level,
                remote_key=self.remote_key(raw_path),
            )
            self._assert_receipt_identity(receipt, raw_path)
            try:
                self._verify_raw(raw_path, receipt)
                self._write_receipt(receipt_path, receipt)
                self._assert_disk_safe()
                compressed_path = self._compress(raw_path)
                final_compressed_path = self.compressed_path(raw_path)
                receipt.state = ArchiveState.COMPRESSED.value
                self._write_receipt(receipt_path, receipt)
                self._verify_compressed(compressed_path, receipt)
                if compressed_path != final_compressed_path:
                    os.replace(str(compressed_path), str(final_compressed_path))
                    _fsync_directory(final_compressed_path.parent)
                    compressed_path = final_compressed_path
                self._write_receipt(receipt_path, receipt)
                self._assert_raw_unchanged(raw_path, receipt)
                remote = self._upload_or_reuse(compressed_path, receipt)
                receipt.state = ArchiveState.ARCHIVED.value
                receipt.remote_version_id = remote.version_id
                self._write_receipt(receipt_path, receipt)
                self._verify_remote(remote, receipt)
                self._write_receipt(receipt_path, receipt)
                self._verify_restore(receipt)
                self._write_receipt(receipt_path, receipt)
                receipt.cleanup_eligible = True
                receipt.state = ArchiveState.CLEANUP_ELIGIBLE.value
                receipt.failure_stage = None
                receipt.failure_reason = None
                self._write_receipt(receipt_path, receipt)
                if cleanup_verified:
                    self._cleanup(raw_path, receipt_path, receipt)
                return receipt
            except Exception as exc:
                failed_stage = receipt.state
                receipt.state = ArchiveState.FAILED.value
                receipt.failure_stage = failed_stage
                receipt.failure_reason = "{}: {}".format(type(exc).__name__, str(exc)[:500])
                try:
                    self._write_receipt(receipt_path, receipt)
                except OSError:
                    pass
                raise

    def verify_restore(self, raw_path: Path) -> ArchiveReceipt:
        if raw_path.is_symlink():
            raise ValueError("raw partition symlinks are not allowed")
        receipt_path = self.receipt_path(raw_path.resolve())
        with _partition_lock(receipt_path.with_suffix(receipt_path.suffix + ".lock"), expected_owner=self.expected_owner):
            receipt = self._load_receipt(receipt_path)
            if receipt is None:
                raise FileNotFoundError("archive receipt does not exist")
            self._assert_receipt_identity(receipt, raw_path.resolve())
            self._verify_restore(receipt)
            self._write_receipt(receipt_path, receipt)
            return receipt

    def verify_compressed(self, raw_path: Path) -> ArchiveReceipt:
        if raw_path.is_symlink():
            raise ValueError("raw partition symlinks are not allowed")
        raw_path = raw_path.resolve()
        receipt_path = self.receipt_path(raw_path)
        with _partition_lock(receipt_path.with_suffix(receipt_path.suffix + ".lock"), expected_owner=self.expected_owner):
            receipt = self._load_receipt(receipt_path)
            if receipt is None:
                raise FileNotFoundError("archive receipt does not exist")
            self._assert_receipt_identity(receipt, raw_path)
            self._verify_compressed(self.compressed_path(raw_path), receipt)
            self._assert_raw_unchanged(raw_path, receipt)
            self._write_receipt(receipt_path, receipt)
            return receipt

    def cleanup(self, raw_path: Path, verified_only: bool = False) -> ArchiveReceipt:
        if not verified_only:
            raise ValueError("cleanup requires verified_only=True")
        if raw_path.is_symlink():
            raise ValueError("raw partition symlinks are not allowed")
        raw_path = raw_path.resolve()
        receipt_path = self.receipt_path(raw_path)
        with _partition_lock(receipt_path.with_suffix(receipt_path.suffix + ".lock"), expected_owner=self.expected_owner):
            receipt = self._load_receipt(receipt_path)
            if receipt is None or not receipt.cleanup_eligible:
                raise ValueError("partition is not cleanup eligible")
            self._assert_receipt_identity(receipt, raw_path)
            self._cleanup(raw_path, receipt_path, receipt)
        return receipt

    def _relative_raw(self, raw_path: Path) -> Path:
        resolved = raw_path.resolve()
        if raw_path.is_symlink() or self.raw_root not in resolved.parents:
            raise ValueError("raw partition escapes configured root or is a symlink")
        relative = resolved.relative_to(self.raw_root)
        if not relative.name.endswith(".jsonl") or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError("invalid raw partition path")
        return relative

    def _manifest_path(self, raw_path: Path) -> Path:
        candidate = self.manifest_root / ("manifest_" + raw_path.stem + ".json")
        if candidate.is_symlink():
            raise ValueError("manifest symlinks are not allowed")
        return candidate

    def _verify_raw(self, raw_path: Path, receipt: ArchiveReceipt) -> None:
        manifest_path = self._manifest_path(raw_path)
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 4:
            raise ValueError("raw manifest is missing or unsupported")
        digest, size, records = _hash_file(raw_path, count_records=True)
        if (
            payload.get("sha256") != digest
            or payload.get("bytes") != size
            or payload.get("record_count") != records
        ):
            raise ValueError("raw partition does not match its manifest")
        receipt.raw_sha256 = digest
        receipt.raw_size = size
        receipt.raw_record_count = records
        receipt.raw_verified_at = _utc_now()
        receipt.state = ArchiveState.RAW_VERIFIED.value

    def _compress(self, raw_path: Path) -> Path:
        destination = self.compressed_path(raw_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.is_symlink():
                raise ValueError("compressed artifact symlink is not allowed")
            return destination
        temporary = _temporary_path(destination)
        try:
            compressor = zstandard.ZstdCompressor(level=self.compression_level)
            with raw_path.open("rb") as source, _exclusive_binary_file(temporary) as target:
                with compressor.stream_writer(target, closefd=False) as writer:
                    shutil.copyfileobj(source, writer, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            # The caller verifies this temporary stream before atomic promotion.
            return temporary
        except Exception:
            _safe_unlink(temporary)
            raise

    def _verify_compressed(self, path: Path, receipt: ArchiveReceipt) -> None:
        compressed_hash, compressed_size, _ = _hash_file(path)
        raw_hash, raw_size, raw_records = _verify_zstd_stream(path.open("rb"))
        if (
            raw_hash != receipt.raw_sha256
            or raw_size != receipt.raw_size
            or raw_records != receipt.raw_record_count
        ):
            raise ValueError("decompressed artifact does not match raw manifest")
        receipt.compressed_sha256 = compressed_hash
        receipt.compressed_size = compressed_size
        receipt.compressed_verified_at = _utc_now()
        receipt.state = ArchiveState.COMPRESSED_VERIFIED.value

    def _upload_or_reuse(self, compressed_path: Path, receipt: ArchiveReceipt) -> RemoteObject:
        assert receipt.remote_key and receipt.compressed_sha256
        if self.store.exists(receipt.remote_key):
            return self.store.head(receipt.remote_key)
        return self.store.upload(compressed_path, receipt.remote_key, receipt.compressed_sha256)

    def _verify_remote(self, remote: RemoteObject, receipt: ArchiveReceipt) -> None:
        if remote.size != receipt.compressed_size:
            raise ValueError("remote object size mismatch")
        if remote.checksum_sha256_base64 is None:
            raise ValueError("remote object SHA-256 checksum is unavailable")
        if _base64_to_hex(remote.checksum_sha256_base64) != receipt.compressed_sha256:
            raise ValueError("remote object checksum mismatch")
        receipt.remote_size = remote.size
        receipt.remote_checksum = remote.checksum_sha256_base64
        receipt.remote_version_id = remote.version_id
        receipt.remote_verified_at = _utc_now()
        receipt.state = ArchiveState.REMOTE_VERIFIED.value

    def _verify_restore(self, receipt: ArchiveReceipt) -> None:
        if not receipt.remote_key:
            raise ValueError("receipt has no remote key")
        with self.store.open_download(receipt.remote_key) as handle:
            compressed_hash = hashlib.sha256()
            raw_hash = hashlib.sha256()
            raw_size = 0
            raw_records = 0
            decompressor = zstandard.ZstdDecompressor().decompressobj()
            try:
                while True:
                    compressed = handle.read(1024 * 1024)
                    if not compressed:
                        break
                    compressed_hash.update(compressed)
                    output = decompressor.decompress(compressed)
                    if output:
                        raw_hash.update(output)
                        raw_size += len(output)
                        raw_records += output.count(b"\n")
                tail = decompressor.flush()
            except zstandard.ZstdError as exc:
                raise CompressedInputError("remote zstd decompression failed") from exc
            if tail:
                raw_hash.update(tail)
                raw_size += len(tail)
                raw_records += tail.count(b"\n")
            if not decompressor.eof or decompressor.unused_data:
                raise CompressedInputError("remote zstd stream is incomplete or has trailing data")
        if compressed_hash.hexdigest() != receipt.compressed_sha256:
            raise ValueError("restored compressed checksum mismatch")
        if (
            raw_hash.hexdigest() != receipt.raw_sha256
            or raw_size != receipt.raw_size
            or raw_records != receipt.raw_record_count
        ):
            raise ValueError("restored raw content mismatch")
        receipt.restore_verified_at = _utc_now()
        receipt.state = ArchiveState.RESTORE_VERIFIED.value

    def _cleanup(self, raw_path: Path, receipt_path: Path, receipt: ArchiveReceipt) -> None:
        if not (
            receipt.cleanup_eligible
            and receipt.raw_verified_at
            and receipt.compressed_verified_at
            and receipt.remote_verified_at
            and receipt.restore_verified_at
        ):
            raise ValueError("cleanup gates are incomplete")
        if raw_path.exists():
            self._assert_raw_unchanged(raw_path, receipt)
            raw_path.unlink()
            _fsync_directory(raw_path.parent)
        receipt.cleanup_completed_at = _utc_now()
        receipt.state = ArchiveState.CLEANED.value
        self._write_receipt(receipt_path, receipt)

    def _assert_raw_unchanged(self, raw_path: Path, receipt: ArchiveReceipt) -> None:
        """Re-check the deletion/upload source after compression to close TOCTOU gaps."""
        if raw_path.is_symlink() or not raw_path.is_file():
            raise ValueError("verified raw partition is missing or no longer a regular file")
        digest, size, records = _hash_file(raw_path, count_records=True)
        if (
            digest != receipt.raw_sha256
            or size != receipt.raw_size
            or records != receipt.raw_record_count
        ):
            raise ValueError("verified raw partition changed after verification")

    def _assert_disk_safe(self) -> None:
        self.compressed_root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(self.compressed_root)
        if usage.total <= 0:
            raise ValueError("compressed filesystem capacity is unavailable")
        used_percent = 100.0 * float(usage.used) / float(usage.total)
        if used_percent >= self.disk_critical_percent:
            raise OSError(
                "disk critical threshold reached; refusing new archive work without deleting raw"
            )

    def _load_receipt(self, path: Path) -> Optional[ArchiveReceipt]:
        if not path.exists():
            return None
        if path.is_symlink():
            raise ValueError("receipt symlinks are not allowed")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("archive receipt must be an object")
        return ArchiveReceipt.from_dict(payload)

    def _write_receipt(self, path: Path, receipt: ArchiveReceipt) -> None:
        _atomic_json(path, receipt.to_dict())

    def _assert_receipt_identity(self, receipt: ArchiveReceipt, raw_path: Path) -> None:
        expected = (
            self.environment_id,
            self.run_id,
            self.collector_epoch,
            self._relative_raw(raw_path).as_posix(),
            self.remote_key(raw_path),
            self.compression_level,
        )
        actual = (
            receipt.environment_id,
            receipt.run_id,
            receipt.collector_epoch,
            receipt.partition,
            receipt.remote_key,
            receipt.compression_level,
        )
        if actual != expected:
            raise ValueError("archive receipt provenance does not match this run")


def _required_identifier(value: str, label: str) -> str:
    if not value or value in {"NOT-SEALED", "UNKNOWN"} or not re.fullmatch(r"[A-Za-z0-9._:-]+", value):
        raise ValueError("{} must be a sealed identifier".format(label))
    return value


def _hash_file(path: Path, count_records: bool = False) -> Tuple[str, int, int]:
    digest = hashlib.sha256()
    size = 0
    records = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            if count_records:
                records += chunk.count(b"\n")
    return digest.hexdigest(), size, records


def _verify_zstd_stream(handle: BinaryIO) -> Tuple[str, int, int]:
    digest = hashlib.sha256()
    size = 0
    records = 0
    try:
        for chunk in iter_zstd_decompressed_chunks(handle):
            digest.update(chunk)
            size += len(chunk)
            records += chunk.count(b"\n")
    finally:
        handle.close()
    return digest.hexdigest(), size, records


def _hex_to_base64(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("invalid SHA-256 hex value")
    return base64.b64encode(bytes.fromhex(value)).decode("ascii")


def _base64_to_hex(value: str) -> str:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("invalid base64 checksum") from exc
    if len(decoded) != 32:
        raise ValueError("remote checksum is not SHA-256")
    return decoded.hex()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _temporary_path(destination: Path) -> Path:
    return destination.with_name(".{}.{}.tmp".format(destination.name, os.getpid()))


@contextmanager
def _exclusive_binary_file(path: Path) -> Iterator[BinaryIO]:
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        yield handle


def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        with _exclusive_binary_file(temporary) as handle:
            encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
        _fsync_directory(path.parent)
    except Exception:
        _safe_unlink(temporary)
        raise


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists() and not path.is_symlink():
            path.unlink()
    except OSError:
        pass


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _partition_lock(path: Path, expected_owner: Optional[str] = None) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        verify_runtime_ownership((path,), expected_owner=expected_owner)
    descriptor = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("partition is already claimed by another worker") from exc
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
