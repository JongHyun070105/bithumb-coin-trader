"""Create write-once local and remote receipts for finalized feed-hour slots."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from bithumb_coin_trader.feed_hour_coverage import FeedHourCoverage
from bithumb_coin_trader.pre_soak_archive import ArchiveReceiptV3, ArchiveStore, validate_archive_key


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COHORT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}$")


@dataclass(frozen=True)
class SlotReceiptWriteResult:
    receipt_id: str
    local_path: Path
    remote_key: str
    sha256: str
    remote_version_id: str | None


class SlotReceiptWriter:
    """Persist one deterministic SLOT_RECEIPT per qualifying feed-hour slot.

    Local receipts are create-only. Remote uploads use the archive store's
    conditional-write contract and must return matching size and SHA-256.
    """

    def __init__(
        self,
        *,
        local_root: Path,
        store: ArchiveStore,
        remote_prefix: str,
        run_id: str,
        epoch: str,
    ) -> None:
        prefix = remote_prefix.rstrip("/")
        validate_archive_key(f"{prefix}/sentinel")
        if not run_id or not epoch:
            raise ValueError("slot receipt run_id and epoch are required")
        self.local_root = Path(local_root)
        self.store = store
        self.remote_prefix = prefix
        self.run_id = run_id
        self.epoch = epoch

    def write(
        self,
        coverage: FeedHourCoverage,
        *,
        raw_receipt: ArchiveReceiptV3 | None,
        coverage_receipt: ArchiveReceiptV3,
    ) -> SlotReceiptWriteResult:
        if coverage.cohort_qualification != "QUALIFYING_FULL_HOUR":
            raise ValueError("slot receipts are only emitted for qualifying full-hour slots")
        if coverage.collector_run_id != self.run_id or coverage.collector_epoch != self.epoch:
            raise ValueError("slot receipt identity differs from the configured run")
        cohort_id = coverage.cohort_utc
        feed_id = coverage.feed_identity
        if not isinstance(cohort_id, str) or not _COHORT_RE.fullmatch(cohort_id):
            raise ValueError("slot receipt cohort identity is invalid")
        if not isinstance(feed_id, str) or not feed_id or not _SHA256_RE.fullmatch(coverage.evidence_sha256):
            raise ValueError("slot receipt feed identity or coverage hash is invalid")
        if not coverage_receipt.remote_key or not _SHA256_RE.fullmatch(coverage_receipt.source_sha256):
            raise ValueError("coverage archive receipt is missing its remote identity or source hash")

        feed_hash = hashlib.sha256(feed_id.encode("utf-8")).hexdigest()
        receipt_id = f"slot-{cohort_id}-{feed_hash}"
        filename = f"{feed_hash}.slot-receipt.json"
        relative = Path(cohort_id) / filename
        remote_key = (
            f"{self.remote_prefix}/archive-receipts/slot-receipts/"
            f"{cohort_id}/{filename}"
        )
        validate_archive_key(remote_key)
        payload: dict[str, Any] = {
            "schema_version": 1,
            "receipt_type": "SLOT_RECEIPT",
            "durability": "BOTH_REQUIRED",
            "receipt_id": receipt_id,
            "run_id": self.run_id,
            "epoch": self.epoch,
            "cohort_id": cohort_id,
            "feed_id": feed_id,
            "coverage_state": coverage.coverage_state,
            "coverage_evidence_sha256": coverage.evidence_sha256,
            "closed_at_utc": coverage.closed_at_utc,
            "coverage_archive": self._archive_reference(coverage_receipt),
            "raw_archive": self._archive_reference(raw_receipt) if raw_receipt is not None else None,
            "failure_reason_codes": list(coverage.failure_reason_codes),
            "s3_key": remote_key,
            "producer": "bithumb_coin_trader.slot_receipt.SlotReceiptWriter",
        }
        body = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
        local_path = self.local_root / relative
        self._write_immutable(local_path, body)

        digest = hashlib.sha256(body).hexdigest()
        remote = self.store.upload(local_path, remote_key, digest)
        expected_checksum = base64.b64encode(bytes.fromhex(digest)).decode("ascii")
        if remote.key != remote_key or remote.size != len(body):
            raise ValueError("remote slot receipt identity or size mismatch")
        if remote.checksum_sha256_base64 != expected_checksum:
            raise ValueError("remote slot receipt checksum mismatch")
        return SlotReceiptWriteResult(
            receipt_id=receipt_id,
            local_path=local_path,
            remote_key=remote_key,
            sha256=digest,
            remote_version_id=remote.version_id,
        )

    @staticmethod
    def _archive_reference(receipt: ArchiveReceiptV3) -> dict[str, Any]:
        if not receipt.remote_key or not _SHA256_RE.fullmatch(receipt.source_sha256):
            raise ValueError("archive receipt is missing its remote key or source hash")
        return {
            "source_sha256": receipt.source_sha256,
            "remote_key": receipt.remote_key,
            "remote_version_id": receipt.remote_version_id,
            "restore_verified": receipt.restore_verified_at is not None,
        }

    def _write_immutable(self, path: Path, body: bytes) -> None:
        if self.local_root.is_symlink():
            raise ValueError("slot receipt directory must not be a symlink")
        self.local_root.mkdir(parents=True, exist_ok=True)
        if self.local_root.is_symlink() or not self.local_root.is_dir():
            raise ValueError("slot receipt directory must be a real directory")
        cohort_dir = path.parent
        if cohort_dir.exists() and cohort_dir.is_symlink():
            raise ValueError("slot receipt cohort directory must not be a symlink")
        cohort_dir.mkdir(parents=True, exist_ok=True)
        if cohort_dir.is_symlink() or not cohort_dir.is_dir():
            raise ValueError("slot receipt cohort directory must be a real directory")

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            if path.is_symlink():
                raise ValueError("existing slot receipt must not be a symlink")
            read_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, read_flags)
            with os.fdopen(descriptor, "rb") as existing_file:
                metadata = os.fstat(existing_file.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise ValueError("existing slot receipt must be a singly-linked regular file")
                existing = existing_file.read()
            if existing != body:
                raise ValueError("immutable slot receipt already exists with different bytes")
            return

        with os.fdopen(descriptor, "wb") as receipt_file:
            written = receipt_file.write(body)
            if written != len(body):
                raise OSError("short write while persisting slot receipt")
            receipt_file.flush()
            os.fsync(receipt_file.fileno())
        directory_fd = os.open(cohort_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
