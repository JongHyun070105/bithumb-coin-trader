"""Fail-closed, one-partition-at-a-time archive pipeline for prospective AWS epochs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta, timezone
from enum import Enum
import base64
import binascii
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

from .archive_cohort import ArchiveCohortId
from .evidence_hashing import canonical_sha256, file_sha256
from .microstructure_io import CompressedInputError, iter_zstd_decompressed_chunks
from .session_evidence import FeedIdentity, normalize_feed_str


RECEIPT_SCHEMA_VERSION = 3
LEGACY_RECEIPT_SCHEMA_VERSION = 2
PARTITION_PATTERN = re.compile(r"_(\d{4}-\d{2}-\d{2})_(\d{2})\.jsonl$")
MAX_S3_PUT_OBJECT_BYTES = 5 * 1024**3
MAX_S3_CONDITIONAL_PUT_ATTEMPTS = 3


class ArtifactKind(str, Enum):
    RAW_DATA = "RAW_DATA"
    COVERAGE_EVIDENCE = "COVERAGE_EVIDENCE"


@dataclass(frozen=True)
class ImmutableArtifact:
    kind: ArtifactKind
    source_path: Path
    relative_path: str
    environment_id: str
    collector_epoch: str
    collector_run_id: str
    cohort: str
    exchange: str
    stream: str
    market: str
    source_sha256: str
    source_size: int
    source_record_count: int | None = None
    manifest_path: Path | None = None
    manifest_sha256: str | None = None


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
class ArchiveReceiptV3:
    schema_version: int = RECEIPT_SCHEMA_VERSION
    artifact_kind: str = ArtifactKind.RAW_DATA.value
    state: str = ""
    environment_id: str = ""
    run_id: str = ""
    collector_epoch: str = ""
    cohort: str = ""
    exchange: str = ""
    stream: str = ""
    market: str = ""
    source_path: str = ""
    source_size: int = 0
    source_sha256: str = ""
    source_record_count: Optional[int] = None
    manifest_path: Optional[str] = None
    manifest_sha256: Optional[str] = None
    compressed_size: Optional[int] = None
    compressed_sha256: Optional[str] = None
    compression_algorithm: str = "zstd"
    compression_level: int = 1
    compression_version: str = field(default_factory=lambda: zstandard.__version__)
    remote_key: Optional[str] = None
    remote_size: Optional[int] = None
    remote_checksum: Optional[str] = None
    remote_version_id: Optional[str] = None
    source_verified_at: Optional[str] = None
    compressed_verified_at: Optional[str] = None
    remote_verified_at: Optional[str] = None
    restore_verified_at: Optional[str] = None
    cleanup_eligible: bool = False
    cleanup_completed_at: Optional[str] = None
    failure_stage: Optional[str] = None
    failure_reason: Optional[str] = None

    @property
    def raw_sha256(self) -> Optional[str]:
        return self.source_sha256

    @raw_sha256.setter
    def raw_sha256(self, val: Optional[str]) -> None:
        self.source_sha256 = val or ""

    @property
    def raw_size(self) -> Optional[int]:
        return self.source_size

    @raw_size.setter
    def raw_size(self, val: Optional[int]) -> None:
        self.source_size = val or 0

    @property
    def raw_record_count(self) -> Optional[int]:
        return self.source_record_count

    @raw_record_count.setter
    def raw_record_count(self, val: Optional[int]) -> None:
        self.source_record_count = val

    @property
    def raw_verified_at(self) -> Optional[str]:
        return self.source_verified_at

    @raw_verified_at.setter
    def raw_verified_at(self, val: Optional[str]) -> None:
        self.source_verified_at = val

    @property
    def partition(self) -> str:
        return self.source_path

    @partition.setter
    def partition(self, val: str) -> None:
        self.source_path = val

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ArchiveReceiptV3:
        if payload.get("schema_version") == LEGACY_RECEIPT_SCHEMA_VERSION:
            raise ValueError(
                "unsupported archive receipt schema: legacy v2 receipt must be loaded via adapt_legacy_v2_receipt"
            )
        if payload.get("schema_version") != RECEIPT_SCHEMA_VERSION:
            raise ValueError(f"unsupported archive receipt schema version: {payload.get('schema_version')}")
        valid_field_names = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in payload.items() if k in valid_field_names}
        return cls(**filtered)


ArchiveReceipt = ArchiveReceiptV3


def _parse_exchange_stream_market(partition: str) -> tuple[str, str, str]:
    if not partition:
        return ("", "", "")
    parts = PurePosixPath(partition).parts
    filename = parts[-1] if parts else ""

    fn_match = re.match(
        r"^([A-Za-z0-9]+)_([A-Za-z0-9]+)_(.+)_\d{4}-\d{2}-\d{2}_\d{2}(?:\.jsonl|\.zst)?$",
        filename,
    )
    if fn_match:
        return (fn_match.group(1), fn_match.group(2), fn_match.group(3))

    exchange = ""
    stream = ""
    market = ""
    for i, p in enumerate(parts[:-1]):
        if p.lower() in ("bithumb", "binance", "upbit"):
            exchange = p.lower()
            if i + 1 < len(parts) - 1:
                stream = parts[i + 1].lower()
            if i + 2 < len(parts) - 1:
                cand = parts[i + 2]
                if not cand.endswith(".jsonl") and not cand.endswith(".zst"):
                    market = cand
            break

    if not exchange and len(parts) >= 3:
        exchange = parts[0]
        stream = parts[1]
        if len(parts) >= 4 and not parts[2].endswith(".jsonl") and not parts[2].endswith(".zst"):
            market = parts[2]

    return (exchange, stream, market)


def adapt_legacy_v2_receipt(payload: Mapping[str, Any]) -> ArchiveReceiptV3:
    if payload.get("schema_version") != LEGACY_RECEIPT_SCHEMA_VERSION:
        raise ValueError(f"expected schema_version 2, got {payload.get('schema_version')}")
    partition_str = str(payload.get("partition", ""))
    exchange, stream, market = _parse_exchange_stream_market(partition_str)
    return ArchiveReceiptV3(
        schema_version=RECEIPT_SCHEMA_VERSION,
        artifact_kind=ArtifactKind.RAW_DATA.value,
        state=str(payload.get("state", "")),
        environment_id=str(payload.get("environment_id", "")),
        run_id=str(payload.get("run_id", "")),
        collector_epoch=str(payload.get("collector_epoch", "")),
        cohort=str(payload.get("cohort", "")),
        exchange=exchange,
        stream=stream,
        market=market,
        source_path=partition_str,
        source_size=int(payload.get("raw_size", 0) or 0),
        source_sha256=str(payload.get("raw_sha256", "") or ""),
        source_record_count=payload.get("raw_record_count"),
        manifest_path=None,
        manifest_sha256=None,
        compressed_size=payload.get("compressed_size"),
        compressed_sha256=payload.get("compressed_sha256"),
        compression_algorithm=str(payload.get("compression_algorithm", "zstd")),
        compression_level=int(payload.get("compression_level", 1)),
        compression_version=str(payload.get("compression_version", zstandard.__version__)),
        remote_key=payload.get("remote_key"),
        remote_size=payload.get("remote_size"),
        remote_checksum=payload.get("remote_checksum"),
        remote_version_id=payload.get("remote_version_id"),
        source_verified_at=payload.get("raw_verified_at"),
        compressed_verified_at=payload.get("compressed_verified_at"),
        remote_verified_at=payload.get("remote_verified_at"),
        restore_verified_at=payload.get("restore_verified_at"),
        cleanup_eligible=bool(payload.get("cleanup_eligible", False)),
        cleanup_completed_at=payload.get("cleanup_completed_at"),
        failure_stage=payload.get("failure_stage"),
        failure_reason=payload.get("failure_reason"),
    )


class MemoryArchiveStore:
    """In-memory store for deterministic tests; never contacts AWS."""

    def __init__(self) -> None:
        self.objects: Dict[str, bytes] = {}
        self.upload_calls = 0
        self.head_override: Optional[RemoteObject] = None

    def upload(self, local_path: Path, key: str, checksum_sha256_hex: str) -> RemoteObject:
        validate_archive_key(key)
        if key in self.objects:
            return self.head(key)
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
            import boto3  # pyright: ignore[reportMissingImports]

            client = boto3.client("s3")
        self.bucket = bucket
        self.client: Any = client

    def upload(self, local_path: Path, key: str, checksum_sha256_hex: str) -> RemoteObject:
        validate_archive_key(key)
        size = local_path.stat().st_size
        if size > MAX_S3_PUT_OBJECT_BYTES:
            raise ValueError("partition exceeds fail-closed single PutObject limit")
        for attempt in range(MAX_S3_CONDITIONAL_PUT_ATTEMPTS):
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
                break
            except Exception as exc:
                response = getattr(exc, "response", None)
                if not isinstance(response, Mapping):
                    raise
                metadata = response.get("ResponseMetadata")
                error = response.get("Error")
                if not isinstance(metadata, Mapping) or not isinstance(error, Mapping):
                    raise
                status = metadata.get("HTTPStatusCode")
                code = error.get("Code")
                error_pair = (status, code)
                if error_pair == (412, "PreconditionFailed"):
                    break
                if error_pair != (409, "ConditionalRequestConflict"):
                    raise
                if attempt + 1 == MAX_S3_CONDITIONAL_PUT_ATTEMPTS:
                    raise
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


def _normalize_partition_relative_path(path_str: str) -> str:
    clean = path_str.strip().lstrip("/")
    if clean.startswith("data/microstructure/raw/"):
        clean = clean[len("data/microstructure/raw/"):]
    elif clean.startswith("raw/"):
        clean = clean[len("raw/"):]
    return Path(clean).as_posix()


def _verify_manifest_identity(
    payload: dict[str, Any],
    *,
    environment_id: str,
    collector_epoch: str,
    collector_run_id: str,
    cohort: str,
    exchange: str,
    stream: str,
    market: str,
    source_path: Path,
    relative_path: str | None = None,
) -> None:
    schema_version = payload.get("schema_version")
    if schema_version not in (4, 5):
        raise ValueError("raw manifest is missing or unsupported")
    if schema_version == 4:
        return

    # Schema 5: enforce all 8 mandatory identity fields
    required_fields = (
        "environment_id",
        "collector_epoch",
        "collector_run_id",
        "cohort",
        "exchange",
        "stream",
        "market",
        "feed_identity",
    )
    for field_name in required_fields:
        val = payload.get(field_name)
        if val is None or (isinstance(val, str) and not val.strip()):
            raise ValueError(f"schema 5 manifest missing required identity field: {field_name}")

    if str(payload["environment_id"]).strip() != environment_id:
        raise ValueError(
            f"manifest environment_id mismatch: {payload['environment_id']} != {environment_id}"
        )
    if str(payload["collector_epoch"]).strip() != collector_epoch:
        raise ValueError(
            f"manifest collector_epoch mismatch: {payload['collector_epoch']} != {collector_epoch}"
        )
    if str(payload["collector_run_id"]).strip() != collector_run_id:
        raise ValueError(
            f"manifest collector_run_id mismatch: {payload['collector_run_id']} != {collector_run_id}"
        )
    if str(payload["cohort"]).strip() != cohort:
        raise ValueError(
            f"manifest cohort mismatch: {payload['cohort']} != {cohort}"
        )

    expected_feed = FeedIdentity(exchange=exchange, stream=stream, market=market)

    p_exch = str(payload["exchange"]).strip().lower()
    if p_exch != expected_feed.exchange:
        raise ValueError(f"manifest exchange mismatch: {payload['exchange']} != {expected_feed.exchange}")

    p_stream = str(payload["stream"]).strip().lower()
    if p_stream != expected_feed.stream:
        raise ValueError(f"manifest stream mismatch: {payload['stream']} != {expected_feed.stream}")

    p_market_raw = str(payload["market"]).strip()
    try:
        p_market_norm = FeedIdentity(exchange=exchange, stream=stream, market=p_market_raw).market
    except Exception as exc:
        raise ValueError(f"manifest market malformed: {p_market_raw}") from exc
    if p_market_norm != expected_feed.market:
        raise ValueError(f"manifest market mismatch: {payload['market']} != {expected_feed.market}")

    manifest_feed_str = str(payload["feed_identity"]).strip()
    parts = manifest_feed_str.split("/")
    if len(parts) != 3:
        raise ValueError(f"invalid manifest feed_identity format: {manifest_feed_str}")

    try:
        manifest_feed = FeedIdentity(exchange=parts[0], stream=parts[1], market=parts[2])
    except Exception as exc:
        raise ValueError(f"malformed feed_identity in manifest: {manifest_feed_str}") from exc

    if manifest_feed != expected_feed:
        raise ValueError(
            f"manifest feed_identity mismatch: {manifest_feed.canonical} != {expected_feed.canonical}"
        )
    if normalize_feed_str(manifest_feed_str) != expected_feed.canonical:
        raise ValueError(
            f"manifest feed_identity normalization mismatch: {manifest_feed_str} != {expected_feed.canonical}"
        )

    # Validate partition_path binding
    partition_path = payload.get("partition_path")
    if partition_path is None or not isinstance(partition_path, str) or not partition_path.strip():
        raise ValueError("manifest partition_path is required for schema 5")

    # Filename must match
    if Path(partition_path).name != source_path.name:
        raise ValueError(
            f"manifest partition_path filename mismatch: {Path(partition_path).name} != {source_path.name}"
        )

    # When relative_path is available, normalized relative paths must bind exactly
    if relative_path is not None:
        norm_manifest_rel = _normalize_partition_relative_path(partition_path)
        norm_expected_rel = _normalize_partition_relative_path(relative_path)
        if norm_manifest_rel != norm_expected_rel:
            raise ValueError(
                f"manifest partition_path mismatch: {norm_manifest_rel} != {norm_expected_rel}"
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
        coverage_root: Optional[Path] = None,
    ) -> None:
        self.raw_root = raw_root.resolve()
        self.manifest_root = manifest_root.resolve()
        self.compressed_root = compressed_root.resolve()
        self.receipt_root = receipt_root.resolve()
        self.coverage_root = (
            coverage_root.resolve()
            if coverage_root is not None
            else (self.raw_root.parent / "coverage").resolve()
        )
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
            (self.raw_root, self.manifest_root, self.compressed_root, self.receipt_root, self.coverage_root),
            expected_owner=self.expected_owner,
        )

    def _relative_raw(self, raw_path: Path) -> Path:
        resolved = raw_path.resolve()
        if raw_path.is_symlink() or self.raw_root not in resolved.parents:
            raise ValueError("raw partition escapes configured root or is a symlink")
        relative = resolved.relative_to(self.raw_root)
        if not relative.name.endswith(".jsonl") or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError("invalid raw partition path")
        return relative

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

    def _validate_artifact_relative_path(self, rel_str: str) -> PurePosixPath:
        if not rel_str or rel_str.startswith("/") or "\\" in rel_str or "//" in rel_str:
            raise ValueError("invalid artifact relative path: {}".format(rel_str))
        rel = PurePosixPath(rel_str)
        if any(part in {"", ".", ".."} for part in rel.parts):
            raise ValueError("artifact relative path escapes root: {}".format(rel_str))
        return rel

    def artifact_receipt_path(self, artifact: ImmutableArtifact) -> Path:
        rel = self._validate_artifact_relative_path(artifact.relative_path)
        return self.receipt_root / Path(*rel.parts[:-1]) / (rel.name + ".archive-receipt.json")

    def artifact_compressed_path(self, artifact: ImmutableArtifact) -> Path:
        rel = self._validate_artifact_relative_path(artifact.relative_path)
        return self.compressed_root / Path(*rel.parts[:-1]) / (rel.name + ".zst")

    def artifact_remote_key(self, artifact: ImmutableArtifact) -> str:
        rel = self._validate_artifact_relative_path(artifact.relative_path)
        key = "{}/{}.zst".format(self.remote_prefix, rel.as_posix())
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
    ) -> ArchiveReceiptV3:
        if raw_path.is_symlink():
            raise ValueError("raw partition symlinks are not allowed")
        raw_path = raw_path.resolve()
        relative = self._relative_raw(raw_path)
        receipt_path = self.receipt_path(raw_path)

        if not raw_path.exists():
            existing = self._load_receipt(receipt_path)
            if existing is not None and existing.state == ArchiveState.CLEANED.value:
                return existing
            raise FileNotFoundError("raw partition is missing and no CLEANED receipt exists")

        manifest_path = self._manifest_path(raw_path)
        manifest_sha256 = file_sha256(manifest_path) if manifest_path.exists() and not manifest_path.is_symlink() else None

        digest, size, records = _hash_file(raw_path, count_records=True)
        cohort_str = ArchiveCohortId.from_partition_name(raw_path.name).key
        exchange, stream, market = _parse_exchange_stream_market(relative.as_posix())

        artifact = ImmutableArtifact(
            kind=ArtifactKind.RAW_DATA,
            source_path=raw_path,
            relative_path=relative.as_posix(),
            environment_id=self.environment_id,
            collector_epoch=self.collector_epoch,
            collector_run_id=self.run_id,
            cohort=cohort_str,
            exchange=exchange,
            stream=stream,
            market=market,
            source_sha256=digest,
            source_size=size,
            source_record_count=records,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
        )

        return self.finalize_artifact(
            artifact=artifact,
            cleanup_verified=cleanup_verified,
            now=now,
            grace_period=grace_period,
            active_paths=active_paths,
            stability_wait_seconds=stability_wait_seconds,
        )

    def finalize_artifact(
        self,
        artifact: ImmutableArtifact,
        cleanup_verified: bool = False,
        now: Optional[datetime] = None,
        grace_period: timedelta = timedelta(minutes=10),
        active_paths: Tuple[Path, ...] = (),
        stability_wait_seconds: float = 1.0,
    ) -> ArchiveReceiptV3:
        if not isinstance(artifact.kind, ArtifactKind) or artifact.kind not in (
            ArtifactKind.RAW_DATA,
            ArtifactKind.COVERAGE_EVIDENCE,
        ):
            raise ValueError("unsupported artifact kind")

        if artifact.kind == ArtifactKind.RAW_DATA:
            if artifact.manifest_path is None or artifact.manifest_sha256 is None:
                raise ValueError("RAW_MANIFEST_BINDING_REQUIRED")
            if artifact.source_record_count is None:
                raise ValueError("raw record count is required")
        elif artifact.kind == ArtifactKind.COVERAGE_EVIDENCE:
            if artifact.source_record_count is not None:
                raise ValueError("COVERAGE_RECORD_COUNT_FORBIDDEN")

        source_path = artifact.source_path.resolve()
        if artifact.source_path.is_symlink():
            raise ValueError("artifact source path symlinks are not allowed")

        self._validate_artifact_relative_path(artifact.relative_path)
        if artifact.kind == ArtifactKind.RAW_DATA:
            if self.raw_root not in source_path.parents:
                raise ValueError("raw partition escapes configured root")

        receipt_path = self.artifact_receipt_path(artifact)
        compressed_path = self.artifact_compressed_path(artifact)
        remote_key = self.artifact_remote_key(artifact)
        lock_path = receipt_path.with_suffix(receipt_path.suffix + ".lock")
        receipt_path.parent.mkdir(parents=True, exist_ok=True)

        with _partition_lock(lock_path, expected_owner=self.expected_owner):
            existing = self._load_receipt(receipt_path)
            if existing is not None:
                self._assert_receipt_identity_v3(existing, artifact)
                if existing.state == ArchiveState.CLEANED.value:
                    return existing
                if existing.state in (ArchiveState.RESTORE_VERIFIED.value, ArchiveState.CLEANUP_ELIGIBLE.value):
                    if cleanup_verified:
                        self._cleanup_artifact(artifact.source_path, receipt_path, existing)
                    return existing

            if not artifact.source_path.exists():
                raise FileNotFoundError("artifact source file is missing and no CLEANED receipt exists")

            if artifact.kind == ArtifactKind.RAW_DATA:
                if not is_closed_stable_partition(
                    artifact.source_path,
                    self.raw_root,
                    now=now,
                    grace_period=grace_period,
                    active_paths=active_paths,
                    stability_wait_seconds=stability_wait_seconds,
                ):
                    raise ValueError("partition is active, unstable, outside raw root, or not past grace")

            m_path_str: Optional[str] = None
            if artifact.manifest_path:
                try:
                    m_path_str = str(artifact.manifest_path.relative_to(self.manifest_root))
                except ValueError:
                    m_path_str = str(artifact.manifest_path)

            receipt = existing or ArchiveReceiptV3(
                schema_version=RECEIPT_SCHEMA_VERSION,
                artifact_kind=artifact.kind.value,
                state=ArchiveState.DISCOVERED.value,
                environment_id=self.environment_id,
                run_id=self.run_id,
                collector_epoch=self.collector_epoch,
                cohort=artifact.cohort,
                exchange=artifact.exchange,
                stream=artifact.stream,
                market=artifact.market,
                source_path=artifact.relative_path,
                source_size=artifact.source_size,
                source_sha256=artifact.source_sha256,
                source_record_count=artifact.source_record_count,
                manifest_path=m_path_str,
                manifest_sha256=artifact.manifest_sha256,
                compression_level=self.compression_level,
                remote_key=remote_key,
            )
            self._assert_receipt_identity_v3(receipt, artifact)
            temp_compressed_path: Optional[Path] = None
            try:
                self._verify_source(artifact, receipt)
                self._write_receipt(receipt_path, receipt)
                self._assert_disk_safe()
                try:
                    temp_compressed_path = self._compress(artifact.source_path, destination=compressed_path)
                except TypeError:
                    temp_compressed_path = self._compress(artifact.source_path)
                receipt.state = ArchiveState.COMPRESSED.value
                self._write_receipt(receipt_path, receipt)
                self._verify_compressed(temp_compressed_path, receipt)
                if temp_compressed_path != compressed_path:
                    os.replace(str(temp_compressed_path), str(compressed_path))
                    _fsync_directory(compressed_path.parent)
                    temp_compressed_path = compressed_path
                self._write_receipt(receipt_path, receipt)
                self._assert_source_unchanged(artifact.source_path, receipt)
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
                    self._cleanup_artifact(artifact.source_path, receipt_path, receipt)
                return receipt
            except Exception as exc:
                if temp_compressed_path is not None and temp_compressed_path != compressed_path:
                    _safe_unlink(temp_compressed_path)
                failed_stage = receipt.state
                receipt.state = ArchiveState.FAILED.value
                receipt.failure_stage = failed_stage
                receipt.failure_reason = "{}: {}".format(type(exc).__name__, str(exc)[:500])
                try:
                    self._write_receipt(receipt_path, receipt)
                except OSError:
                    pass
                raise

    def verify_restore(self, raw_path: Path) -> ArchiveReceiptV3:
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

    def verify_compressed(self, raw_path: Path) -> ArchiveReceiptV3:
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
            self._assert_source_unchanged(raw_path, receipt)
            self._write_receipt(receipt_path, receipt)
            return receipt

    def cleanup(self, raw_path: Path, verified_only: bool = False) -> ArchiveReceiptV3:
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
            self._cleanup_artifact(raw_path, receipt_path, receipt)
        return receipt

    def _manifest_path(self, raw_path: Path) -> Path:
        candidate = self.manifest_root / ("manifest_" + raw_path.stem + ".json")
        if candidate.is_symlink():
            raise ValueError("manifest symlinks are not allowed")
        return candidate

    def _verify_source(self, artifact: ImmutableArtifact, receipt: ArchiveReceiptV3) -> None:
        if artifact.kind == ArtifactKind.RAW_DATA:
            assert artifact.manifest_path is not None
            if artifact.manifest_path.is_symlink():
                raise ValueError("manifest symlinks are not allowed")
            if not artifact.manifest_path.is_file():
                raise ValueError("raw manifest is missing or unsupported")
            actual_m_sha = file_sha256(artifact.manifest_path)
            if artifact.manifest_sha256 != actual_m_sha:
                raise ValueError("manifest hash mismatch")
            payload = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("raw manifest is missing or unsupported")
            _verify_manifest_identity(
                payload,
                environment_id=artifact.environment_id,
                collector_epoch=artifact.collector_epoch,
                collector_run_id=artifact.collector_run_id,
                cohort=artifact.cohort,
                exchange=artifact.exchange,
                stream=artifact.stream,
                market=artifact.market,
                source_path=artifact.source_path,
                relative_path=artifact.relative_path,
            )
            digest, size, records = _hash_file(artifact.source_path, count_records=True)
            if (
                payload.get("sha256") != digest
                or payload.get("bytes") != size
                or payload.get("record_count") != records
            ):
                raise ValueError("raw partition does not match its manifest")
            if (
                digest != artifact.source_sha256
                or size != artifact.source_size
                or records != artifact.source_record_count
            ):
                raise ValueError("raw partition does not match artifact descriptor")
            receipt.source_sha256 = digest
            receipt.source_size = size
            receipt.source_record_count = records
            receipt.source_verified_at = _utc_now()
            receipt.state = ArchiveState.RAW_VERIFIED.value

        elif artifact.kind == ArtifactKind.COVERAGE_EVIDENCE:
            try:
                coverage_data = json.loads(artifact.source_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError("invalid JSON in coverage evidence") from exc
            if not isinstance(coverage_data, dict):
                raise ValueError("coverage artifact must be a JSON object")
            stored_hash = coverage_data.get("evidence_sha256")
            if not stored_hash:
                raise ValueError("coverage evidence_sha256 is missing")
            computed_hash = canonical_sha256(coverage_data, excluded=("evidence_sha256",))
            if stored_hash != computed_hash:
                raise ValueError("coverage hash mismatch: stored={}, computed={}".format(stored_hash, computed_hash))
            digest, size, _ = _hash_file(artifact.source_path)
            if digest != artifact.source_sha256 or size != artifact.source_size:
                raise ValueError("coverage artifact file does not match artifact descriptor")
            receipt.source_sha256 = digest
            receipt.source_size = size
            receipt.source_record_count = None
            receipt.source_verified_at = _utc_now()
            receipt.state = ArchiveState.RAW_VERIFIED.value

    def _verify_raw(self, raw_path: Path, receipt: ArchiveReceiptV3) -> None:
        manifest_path = self._manifest_path(raw_path)
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("raw manifest is missing or unsupported")

        _verify_manifest_identity(
            payload,
            environment_id=receipt.environment_id,
            collector_epoch=receipt.collector_epoch,
            collector_run_id=receipt.run_id,
            cohort=receipt.cohort,
            exchange=receipt.exchange,
            stream=receipt.stream,
            market=receipt.market,
            source_path=raw_path,
            relative_path=receipt.source_path,
        )

        digest, size, records = _hash_file(raw_path, count_records=True)
        if (
            payload.get("sha256") != digest
            or payload.get("bytes") != size
            or payload.get("record_count") != records
        ):
            raise ValueError("raw partition does not match its manifest")
        receipt.source_sha256 = digest
        receipt.source_size = size
        receipt.source_record_count = records
        receipt.source_verified_at = _utc_now()
        receipt.state = ArchiveState.RAW_VERIFIED.value

    def _compress(self, raw_path: Path, destination: Optional[Path] = None) -> Path:
        dest = destination or self.compressed_path(raw_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            if dest.is_symlink():
                raise ValueError("compressed artifact symlink is not allowed")
            return dest
        temporary = _temporary_path(dest)
        try:
            compressor = zstandard.ZstdCompressor(level=self.compression_level)
            with raw_path.open("rb") as source, _exclusive_binary_file(temporary) as target:
                with compressor.stream_writer(target, closefd=False) as writer:
                    shutil.copyfileobj(source, writer, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            return temporary
        except Exception:
            _safe_unlink(temporary)
            raise

    def _verify_compressed(self, path: Path, receipt: ArchiveReceiptV3) -> None:
        compressed_hash, compressed_size, _ = _hash_file(path)
        raw_hash, raw_size, raw_records = _verify_zstd_stream(path.open("rb"))
        if (
            raw_hash != receipt.source_sha256
            or raw_size != receipt.source_size
            or (receipt.source_record_count is not None and raw_records != receipt.source_record_count)
        ):
            raise ValueError("decompressed artifact does not match raw manifest")
        receipt.compressed_sha256 = compressed_hash
        receipt.compressed_size = compressed_size
        receipt.compressed_verified_at = _utc_now()
        receipt.state = ArchiveState.COMPRESSED_VERIFIED.value

    def _upload_or_reuse(self, compressed_path: Path, receipt: ArchiveReceiptV3) -> RemoteObject:
        assert receipt.remote_key and receipt.compressed_sha256
        return self.store.upload(compressed_path, receipt.remote_key, receipt.compressed_sha256)

    def _verify_remote(self, remote: RemoteObject, receipt: ArchiveReceiptV3) -> None:
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

    def _verify_restore(self, receipt: ArchiveReceiptV3) -> None:
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
                        if receipt.source_record_count is not None:
                            raw_records += output.count(b"\n")
                tail = decompressor.flush()
            except zstandard.ZstdError as exc:
                raise CompressedInputError("remote zstd decompression failed") from exc
            if tail:
                raw_hash.update(tail)
                raw_size += len(tail)
                if receipt.source_record_count is not None:
                    raw_records += tail.count(b"\n")
            if not decompressor.eof or decompressor.unused_data:
                raise CompressedInputError("remote zstd stream is incomplete or has trailing data")
        if compressed_hash.hexdigest() != receipt.compressed_sha256:
            raise ValueError("restored compressed checksum mismatch")
        if (
            raw_hash.hexdigest() != receipt.source_sha256
            or raw_size != receipt.source_size
            or (receipt.source_record_count is not None and raw_records != receipt.source_record_count)
        ):
            raise ValueError("restored raw content mismatch")
        receipt.restore_verified_at = _utc_now()
        receipt.state = ArchiveState.RESTORE_VERIFIED.value

    def _cleanup_artifact(self, source_path: Path, receipt_path: Path, receipt: ArchiveReceiptV3) -> None:
        if not (
            receipt.cleanup_eligible
            and receipt.source_verified_at
            and receipt.compressed_verified_at
            and receipt.remote_verified_at
            and receipt.restore_verified_at
        ):
            raise ValueError("cleanup gates are incomplete")
        if source_path.exists():
            self._assert_source_unchanged(source_path, receipt)
            source_path.unlink()
            _fsync_directory(source_path.parent)
        receipt.cleanup_completed_at = _utc_now()
        receipt.state = ArchiveState.CLEANED.value
        self._write_receipt(receipt_path, receipt)

    def _cleanup(self, raw_path: Path, receipt_path: Path, receipt: ArchiveReceiptV3) -> None:
        self._cleanup_artifact(raw_path, receipt_path, receipt)

    def _assert_source_unchanged(self, source_path: Path, receipt: ArchiveReceiptV3) -> None:
        """Re-check the deletion/upload source after compression to close TOCTOU gaps."""
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError("verified raw partition is missing or no longer a regular file")
        digest, size, records = _hash_file(source_path, count_records=(receipt.source_record_count is not None))
        if (
            digest != receipt.source_sha256
            or size != receipt.source_size
            or (receipt.source_record_count is not None and records != receipt.source_record_count)
        ):
            raise ValueError("verified raw partition changed after verification")

    def _assert_raw_unchanged(self, raw_path: Path, receipt: ArchiveReceiptV3) -> None:
        self._assert_source_unchanged(raw_path, receipt)

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

    def _load_receipt(self, path: Path) -> Optional[ArchiveReceiptV3]:
        if not path.exists():
            return None
        if path.is_symlink():
            raise ValueError("receipt symlinks are not allowed")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("archive receipt must be an object")
        return ArchiveReceiptV3.from_dict(payload)

    def _write_receipt(self, path: Path, receipt: ArchiveReceiptV3) -> None:
        _atomic_json(path, receipt.to_dict())

    def _assert_receipt_identity(self, receipt: ArchiveReceiptV3, raw_path: Path) -> None:
        expected = (
            self.environment_id,
            self.run_id,
            self.collector_epoch,
            self._relative_raw(raw_path).as_posix(),
            ArchiveCohortId.from_partition_name(raw_path.name).key,
            self.remote_key(raw_path),
            self.compression_level,
        )
        actual = (
            receipt.environment_id,
            receipt.run_id,
            receipt.collector_epoch,
            receipt.source_path,
            receipt.cohort,
            receipt.remote_key,
            receipt.compression_level,
        )
        if actual != expected:
            raise ValueError("archive receipt provenance does not match this run")

    def _assert_receipt_identity_v3(self, receipt: ArchiveReceiptV3, artifact: ImmutableArtifact) -> None:
        expected = (
            self.environment_id,
            self.run_id,
            self.collector_epoch,
            artifact.relative_path,
            artifact.cohort,
            self.artifact_remote_key(artifact),
            self.compression_level,
            artifact.kind.value,
        )
        actual = (
            receipt.environment_id,
            receipt.run_id,
            receipt.collector_epoch,
            receipt.source_path,
            receipt.cohort,
            receipt.remote_key,
            receipt.compression_level,
            receipt.artifact_kind,
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
    except (ValueError, binascii.Error) as exc:
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
