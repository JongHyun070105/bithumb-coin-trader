from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import base64
import fcntl
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

import pytest

import zstandard

from bithumb_coin_trader.evidence_hashing import canonical_sha256, file_sha256
from bithumb_coin_trader.pre_soak_archive import (
    ArchivePipeline,
    ArchiveReceiptV3,
    ArchiveState,
    ArtifactKind,
    ImmutableArtifact,
    MemoryArchiveStore,
    RemoteObject,
    S3ArchiveStore,
    adapt_legacy_v2_receipt,
    is_closed_stable_partition,
    validate_archive_key,
)


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


class FailingStore(MemoryArchiveStore):
    def upload(self, local_path: Path, key: str, checksum_sha256_hex: str) -> RemoteObject:
        raise TimeoutError("injected upload failure")


class DownloadFailStore(MemoryArchiveStore):
    @contextmanager
    def open_download(self, key: str):
        raise OSError("injected download failure")
        yield


class S3ClientError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": status},
            "Error": {"Code": code, "Message": message},
        }


class ObjectOnlyS3Client:
    """Models Get/Put object access without ListBucket missing-key disclosure."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_requests: list[dict] = []
        self.head_requests: list[dict] = []
        self.deny_post_write_head = False

    def put_object(self, **kwargs):
        key = kwargs["Key"]
        payload = kwargs["Body"].read()
        self.put_requests.append({name: value for name, value in kwargs.items() if name != "Body"})
        if key in self.objects:
            raise S3ClientError(412, "PreconditionFailed", "immutable object already exists")
        self.objects[key] = payload
        return {"VersionId": "created-v1"}

    def head_object(self, **kwargs):
        self.head_requests.append(kwargs)
        key = kwargs["Key"]
        if self.deny_post_write_head or key not in self.objects:
            raise S3ClientError(403, "AccessDenied", "Forbidden")
        payload = self.objects[key]
        return {
            "ContentLength": len(payload),
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(payload).digest()).decode("ascii"),
            "VersionId": "existing-v1",
        }

    def get_object(self, **kwargs):
        return {"Body": io.BytesIO(self.objects[kwargs["Key"]])}


class RacingObjectOnlyS3Client(ObjectOnlyS3Client):
    def put_object(self, **kwargs):
        key = kwargs["Key"]
        payload = kwargs["Body"].read()
        self.put_requests.append({name: value for name, value in kwargs.items() if name != "Body"})
        if len(self.put_requests) == 1:
            raise S3ClientError(409, "ConditionalRequestConflict", "concurrent delete conflict")
        self.objects[key] = payload
        raise S3ClientError(412, "PreconditionFailed", "concurrent writer won")


class ArchivePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.raw_root = self.root / "raw"
        self.manifest_root = self.root / "manifests"
        self.compressed_root = self.root / "compressed"
        self.receipt_root = self.root / "receipts"
        self.raw = (
            self.raw_root
            / "2026-09-01"
            / "binance"
            / "trade"
            / "binance_trade_btcusdt_2026-09-01_10.jsonl"
        )
        self.raw.parent.mkdir(parents=True)
        records = [
            {
                "exchange": "binance",
                "stream": "trade",
                "market": "BTCUSDT",
                "exchange_ts": "2026-09-01T10:00:00+00:00",
                "local_recv_ts": "2026-09-01T10:00:00.001000+00:00",
                "local_write_ts": "2026-09-01T10:00:00.002000+00:00",
                "payload": {"trade_id": index},
            }
            for index in range(20)
        ]
        self.raw.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
        self._write_manifest()
        self.store = MemoryArchiveStore()
        self.pipeline = self._pipeline(self.store)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_manifest(self) -> None:
        self.manifest_root.mkdir(parents=True, exist_ok=True)
        data = self.raw.read_bytes()
        payload = {
            "schema_version": 4,
            "partition_path": str(self.raw.relative_to(self.root)),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "record_count": data.count(b"\n"),
        }
        (self.manifest_root / ("manifest_" + self.raw.stem + ".json")).write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def _pipeline(self, store, disk_critical_percent: float = 99.9) -> ArchivePipeline:
        return ArchivePipeline(
            raw_root=self.raw_root,
            manifest_root=self.manifest_root,
            compressed_root=self.compressed_root,
            receipt_root=self.receipt_root,
            store=store,
            environment_id="aws-apne2-research",
            run_id="local-test-run",
            collector_epoch="aws-v9.1-test",
            remote_prefix="market-data/temporary/aws-v9.1-test",
            compression_level=1,
            disk_critical_percent=disk_critical_percent,
        )

    def _finalize(self, **kwargs):
        return self.pipeline.finalize(
            self.raw,
            now=NOW,
            grace_period=__import__("datetime").timedelta(0),
            stability_wait_seconds=0,
            **kwargs,
        )

    def _use_object_only_s3(self, client: ObjectOnlyS3Client) -> None:
        self.pipeline = self._pipeline(S3ArchiveStore("example-bucket", client=client))

    def _seed_remote_from_compressed(self, client: ObjectOnlyS3Client, payload: bytes | None = None) -> tuple[str, bytes]:
        compressed = self.pipeline._compress(self.raw)
        final = self.pipeline.compressed_path(self.raw)
        if compressed != final:
            compressed.replace(final)
        expected = final.read_bytes()
        key = self.pipeline.remote_key(self.raw)
        client.objects[key] = expected if payload is None else payload
        return key, expected

    def test_absent_s3_object_without_listbucket_reaches_conditional_upload(self) -> None:
        client = ObjectOnlyS3Client()
        self._use_object_only_s3(client)

        receipt = self._finalize()

        self.assertEqual(receipt.state, ArchiveState.CLEANUP_ELIGIBLE.value)
        self.assertEqual(len(client.put_requests), 1)
        self.assertEqual(client.put_requests[0]["IfNoneMatch"], "*")

    def test_existing_identical_s3_object_is_reused_after_precondition_failure(self) -> None:
        client = ObjectOnlyS3Client()
        self._use_object_only_s3(client)
        key, expected = self._seed_remote_from_compressed(client)

        receipt = self._finalize()

        self.assertEqual(receipt.state, ArchiveState.CLEANUP_ELIGIBLE.value)
        self.assertEqual(client.objects[key], expected)
        self.assertEqual(len(client.put_requests), 1)

    def test_existing_wrong_s3_object_fails_closed_after_precondition_failure(self) -> None:
        client = ObjectOnlyS3Client()
        self._use_object_only_s3(client)
        key, _ = self._seed_remote_from_compressed(client, payload=b"wrong-object")

        with self.assertRaisesRegex(ValueError, "remote object size mismatch"):
            self._finalize()

        self.assertEqual(client.objects[key], b"wrong-object")
        self.assertEqual(len(client.put_requests), 1)
        receipt = self.pipeline._load_receipt(self.pipeline.receipt_path(self.raw))
        assert receipt is not None
        self.assertEqual(receipt.state, ArchiveState.FAILED.value)

    def test_conditional_s3_write_race_reuses_winner_deterministically(self) -> None:
        client = RacingObjectOnlyS3Client()
        self._use_object_only_s3(client)

        receipt = self._finalize()

        self.assertEqual(receipt.state, ArchiveState.CLEANUP_ELIGIBLE.value)
        self.assertEqual(len(client.put_requests), 2)

    def test_access_denied_on_post_write_head_remains_a_failure(self) -> None:
        client = ObjectOnlyS3Client()
        client.deny_post_write_head = True
        self._use_object_only_s3(client)

        with self.assertRaisesRegex(S3ClientError, "Forbidden"):
            self._finalize()

        self.assertEqual(len(client.put_requests), 1)
        receipt = self.pipeline._load_receipt(self.pipeline.receipt_path(self.raw))
        assert receipt is not None
        self.assertEqual(receipt.state, ArchiveState.FAILED.value)
        self.assertEqual(receipt.failure_stage, ArchiveState.COMPRESSED_VERIFIED.value)

    def test_end_to_end_without_cleanup_preserves_raw(self) -> None:
        receipt = self._finalize()
        self.assertEqual(receipt.state, ArchiveState.CLEANUP_ELIGIBLE.value)
        self.assertTrue(receipt.cleanup_eligible)
        self.assertTrue(self.raw.exists())
        self.assertTrue(self.pipeline.compressed_path(self.raw).exists())
        self.assertEqual(receipt.raw_sha256, hashlib.sha256(self.raw.read_bytes()).hexdigest())
        self.assertNotEqual(receipt.raw_sha256, receipt.compressed_sha256)

    def test_receipt_binds_canonical_date_hour_cohort(self) -> None:
        receipt = self._finalize()

        self.assertEqual(receipt.cohort, "2026-09-01_10")
        persisted = json.loads(self.pipeline.receipt_path(self.raw).read_text(encoding="utf-8"))
        self.assertEqual(persisted["cohort"], "2026-09-01_10")

        persisted["cohort"] = "2026-09-02_10"
        self.pipeline.receipt_path(self.raw).write_text(json.dumps(persisted), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "provenance does not match"):
            self._finalize()

    def test_explicit_verified_cleanup_removes_only_raw(self) -> None:
        receipt = self._finalize(cleanup_verified=True)
        self.assertEqual(receipt.state, ArchiveState.CLEANED.value)
        self.assertFalse(self.raw.exists())
        self.assertTrue(self.pipeline.compressed_path(self.raw).exists())
        rerun = self.pipeline.finalize(self.raw, now=NOW, stability_wait_seconds=0)
        self.assertEqual(rerun.state, ArchiveState.CLEANED.value)

    def test_raw_sha_mismatch_fails_and_keeps_raw(self) -> None:
        self.raw.write_bytes(self.raw.read_bytes() + b"{}\n")
        with self.assertRaisesRegex(ValueError, "manifest"):
            self._finalize()
        self.assertTrue(self.raw.exists())

    def test_compression_exception_leaves_no_final_and_keeps_raw(self) -> None:
        with patch("bithumb_coin_trader.pre_soak_archive.shutil.copyfileobj", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self._finalize()
        self.assertTrue(self.raw.exists())
        self.assertFalse(self.pipeline.compressed_path(self.raw).exists())

    def test_critical_disk_refuses_new_work_and_keeps_raw(self) -> None:
        self.pipeline = self._pipeline(self.store, disk_critical_percent=90.0)
        usage = shutil._ntuple_diskusage(total=100, used=90, free=10)
        with patch("bithumb_coin_trader.pre_soak_archive.shutil.disk_usage", return_value=usage):
            with self.assertRaisesRegex(OSError, "disk critical"):
                self._finalize()
        self.assertTrue(self.raw.exists())
        self.assertFalse(self.pipeline.compressed_path(self.raw).exists())

    def test_corrupted_existing_zstd_is_not_accepted(self) -> None:
        compressed = self.pipeline.compressed_path(self.raw)
        compressed.parent.mkdir(parents=True)
        compressed.write_bytes(b"not-zstd")
        with self.assertRaises(Exception):
            self._finalize()
        self.assertTrue(self.raw.exists())

    def test_compressed_hash_change_before_verify_is_rejected(self) -> None:
        original = self.pipeline._compress

        def mutate_after_compress(raw_path):
            path = original(raw_path)
            with path.open("ab") as handle:
                handle.write(b"trailing")
            return path

        with patch.object(self.pipeline, "_compress", side_effect=mutate_after_compress):
            with self.assertRaises(Exception):
                self._finalize()
        self.assertTrue(self.raw.exists())

    def test_raw_change_after_compression_is_rejected_before_upload(self) -> None:
        original = self.pipeline._verify_compressed

        def mutate_after_verification(path, receipt):
            original(path, receipt)
            with self.raw.open("ab") as handle:
                handle.write(b"{}\n")

        with patch.object(self.pipeline, "_verify_compressed", side_effect=mutate_after_verification):
            with self.assertRaisesRegex(ValueError, "changed after verification"):
                self._finalize()
        self.assertEqual(self.store.upload_calls, 0)
        self.assertTrue(self.raw.exists())

    def test_raw_change_after_archive_is_rejected_before_cleanup(self) -> None:
        self._finalize()
        with self.raw.open("ab") as handle:
            handle.write(b"{}\n")
        with self.assertRaisesRegex(ValueError, "changed after verification"):
            self.pipeline.cleanup(self.raw, verified_only=True)
        self.assertTrue(self.raw.exists())

    def test_truncated_zstd_is_not_accepted(self) -> None:
        data = zstandard.ZstdCompressor(level=1).compress(self.raw.read_bytes())
        compressed = self.pipeline.compressed_path(self.raw)
        compressed.parent.mkdir(parents=True)
        compressed.write_bytes(data[:-3])
        with self.assertRaises(Exception):
            self._finalize()
        self.assertTrue(self.raw.exists())

    def test_upload_failure_keeps_raw(self) -> None:
        self.pipeline = self._pipeline(FailingStore())
        with self.assertRaises(TimeoutError):
            self._finalize()
        self.assertTrue(self.raw.exists())

    def test_remote_size_mismatch_keeps_raw(self) -> None:
        self.store.head_override = RemoteObject("wrong", 1, base64.b64encode(b"x" * 32).decode())
        with self.assertRaisesRegex(ValueError, "size"):
            self._finalize()
        self.assertTrue(self.raw.exists())

    def test_remote_checksum_mismatch_keeps_raw(self) -> None:
        compressed = self.pipeline.compressed_path(self.raw)
        self._finalize()
        receipt = self.pipeline._load_receipt(self.pipeline.receipt_path(self.raw))
        assert receipt and receipt.remote_key
        self.raw.write_bytes(self.raw.read_bytes())
        self.store.head_override = RemoteObject(
            receipt.remote_key,
            compressed.stat().st_size,
            base64.b64encode(b"x" * 32).decode("ascii"),
        )
        receipt.state = ArchiveState.COMPRESSED_VERIFIED.value
        receipt.cleanup_eligible = False
        self.pipeline._write_receipt(self.pipeline.receipt_path(self.raw), receipt)
        with self.assertRaisesRegex(ValueError, "checksum"):
            self._finalize()
        self.assertTrue(self.raw.exists())

    def test_download_failure_keeps_raw(self) -> None:
        store = DownloadFailStore()
        self.pipeline = self._pipeline(store)
        with self.assertRaises(OSError):
            self._finalize()
        self.assertTrue(self.raw.exists())

    def test_restore_raw_mismatch_keeps_raw(self) -> None:
        class MutatingDownloadStore(MemoryArchiveStore):
            @contextmanager
            def open_download(self, key: str):
                data = bytearray(self.objects[key])
                data[-1] ^= 1
                handle = __import__("io").BytesIO(bytes(data))
                try:
                    yield handle
                finally:
                    handle.close()

        self.pipeline = self._pipeline(MutatingDownloadStore())
        with self.assertRaises(Exception):
            self._finalize()
        self.assertTrue(self.raw.exists())

    def test_receipt_write_failure_keeps_raw(self) -> None:
        with patch.object(self.pipeline, "_write_receipt", side_effect=OSError("receipt fsync failed")):
            with self.assertRaises(OSError):
                self._finalize()
        self.assertTrue(self.raw.exists())

    def test_cleanup_unlink_failure_keeps_raw(self) -> None:
        self._finalize()
        with patch.object(Path, "unlink", side_effect=OSError("unlink denied")):
            with self.assertRaises(OSError):
                self.pipeline.cleanup(self.raw, verified_only=True)
        self.assertTrue(self.raw.exists())

    def test_duplicate_finalize_and_upload_are_idempotent(self) -> None:
        first = self._finalize()
        second = self._finalize()
        self.assertEqual(first.compressed_sha256, second.compressed_sha256)
        self.assertEqual(self.store.upload_calls, 1)

    def test_worker_crash_restart_resumes_without_duplicate_upload(self) -> None:
        with patch.object(self.pipeline, "_upload_or_reuse", side_effect=KeyboardInterrupt("crash")):
            with self.assertRaises(KeyboardInterrupt):
                self._finalize()
        self.assertTrue(self.raw.exists())
        receipt = self._finalize()
        self.assertEqual(receipt.state, ArchiveState.CLEANUP_ELIGIBLE.value)
        self.assertEqual(self.store.upload_calls, 1)

    def test_second_worker_cannot_claim_same_partition(self) -> None:
        lock_path = self.pipeline.receipt_path(self.raw).with_suffix(".json.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = __import__("os").open(str(lock_path), __import__("os").O_RDWR | __import__("os").O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, "already claimed"):
                self._finalize()
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            __import__("os").close(descriptor)

    def test_active_partition_is_never_processed(self) -> None:
        with self.assertRaisesRegex(ValueError, "active"):
            self.pipeline.finalize(
                self.raw,
                now=NOW,
                grace_period=__import__("datetime").timedelta(0),
                active_paths=(self.raw,),
                stability_wait_seconds=0,
            )
        self.assertTrue(self.raw.exists())

    def test_rotated_previous_hour_is_eligible_while_current_active_is_excluded(self) -> None:
        previous = self.raw_root / "2026-09-02" / "binance" / "trade" / "binance_trade_btcusdt_2026-09-02_09.jsonl"
        current = previous.with_name("binance_trade_btcusdt_2026-09-02_10.jsonl")
        previous.parent.mkdir(parents=True, exist_ok=True)
        previous.write_text("{}\n", encoding="utf-8")
        current.write_text("{}\n", encoding="utf-8")
        now = datetime(2026, 9, 4, 11, 0, tzinfo=timezone.utc)
        self.assertTrue(
            is_closed_stable_partition(
                previous,
                self.raw_root,
                now=now,
                grace_period=__import__("datetime").timedelta(0),
                active_paths=(current,),
            )
        )
        self.assertFalse(
            is_closed_stable_partition(
                current,
                self.raw_root,
                now=now,
                grace_period=__import__("datetime").timedelta(0),
                active_paths=(current,),
            )
        )

    def test_current_hour_is_never_processed(self) -> None:
        current = self.raw.with_name("binance_trade_btcusdt_2026-09-02_12.jsonl")
        current.write_bytes(self.raw.read_bytes())
        manifest = self.manifest_root / ("manifest_" + current.stem + ".json")
        manifest.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "active"):
            self.pipeline.finalize(current, now=NOW, stability_wait_seconds=0)

    def test_path_traversal_archive_key_rejected(self) -> None:
        for value in ("../escape", "/absolute", "a//b", "a\\b"):
            with self.assertRaises(ValueError):
                validate_archive_key(value)

    def test_symlink_raw_rejected(self) -> None:
        link = self.raw.with_name("binance_trade_btcusdt_2026-09-01_09.jsonl")
        link.symlink_to(self.raw)
        with self.assertRaises(ValueError):
            self.pipeline.finalize(link, now=NOW, stability_wait_seconds=0)


def pipeline(tmp_path: Path, store: Any = None) -> ArchivePipeline:
    raw_root = tmp_path / "raw"
    manifest_root = tmp_path / "manifests"
    compressed_root = tmp_path / "compressed"
    receipt_root = tmp_path / "receipts"
    coverage_root = tmp_path / "coverage"
    for d in (raw_root, manifest_root, compressed_root, receipt_root, coverage_root):
        d.mkdir(parents=True, exist_ok=True)
    return ArchivePipeline(
        raw_root=raw_root,
        manifest_root=manifest_root,
        compressed_root=compressed_root,
        receipt_root=receipt_root,
        store=store or MemoryArchiveStore(),
        environment_id="aws-apne2-research",
        run_id="test-run-v3",
        collector_epoch="aws-v3-epoch",
        remote_prefix="market-data/temporary/aws-v3-epoch",
        compression_level=1,
        disk_critical_percent=99.9,
    )


def raw_artifact(
    tmp_path: Path,
    manifest_sha256: Any = "AUTO",
    manifest_path: Any = "AUTO",
    source_record_count: Any = "AUTO",
    relative_path: str | None = None,
    tamper_file: bool = False,
) -> ImmutableArtifact:
    raw_root = tmp_path / "raw"
    manifest_root = tmp_path / "manifests"
    rel = relative_path or "2026-09-01/binance/trade/binance_trade_btcusdt_2026-09-01_10.jsonl"
    source_file = raw_root / rel
    source_file.parent.mkdir(parents=True, exist_ok=True)
    if not source_file.exists():
        records = [
            {"exchange": "binance", "stream": "trade", "market": "BTCUSDT", "data": i}
            for i in range(10)
        ]
        source_file.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")

    data = source_file.read_bytes()
    raw_sha = hashlib.sha256(data).hexdigest()
    raw_size = len(data)
    rec_count = data.count(b"\n")

    manifest_file = manifest_root / f"manifest_{source_file.stem}.json"
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    if not manifest_file.exists():
        payload = {
            "schema_version": 4,
            "partition_path": rel,
            "bytes": raw_size,
            "sha256": raw_sha,
            "record_count": rec_count,
        }
        manifest_file.write_text(json.dumps(payload), encoding="utf-8")

    m_path: Path | None = manifest_file if manifest_path == "AUTO" else manifest_path
    if manifest_sha256 == "AUTO":
        m_sha = file_sha256(manifest_file) if m_path else None
    else:
        m_sha = manifest_sha256

    s_count = rec_count if source_record_count == "AUTO" else source_record_count

    if tamper_file:
        source_file.write_bytes(data + b"{}\n")

    return ImmutableArtifact(
        kind=ArtifactKind.RAW_DATA,
        source_path=source_file,
        relative_path=rel,
        environment_id="aws-apne2-research",
        collector_epoch="aws-v3-epoch",
        collector_run_id="test-run-v3",
        cohort="2026-09-01_10",
        exchange="binance",
        stream="trade",
        market="BTCUSDT",
        source_sha256=raw_sha,
        source_size=raw_size,
        source_record_count=s_count,
        manifest_path=m_path,
        manifest_sha256=m_sha,
    )


def coverage_artifact(
    tmp_path: Path,
    source_record_count: int | None = None,
    tamper_hash: bool = False,
    relative_path: str | None = None,
) -> ImmutableArtifact:
    cov_root = tmp_path / "coverage"
    rel = relative_path or "coverage/2026-09-01_10/binance/trade/BTCUSDT.coverage.json"
    source_file = cov_root / rel
    source_file.parent.mkdir(parents=True, exist_ok=True)

    cov_data = {
        "schema_version": 1,
        "artifact_kind": "COVERAGE_EVIDENCE",
        "environment_id": "aws-apne2-research",
        "collector_epoch": "aws-v3-epoch",
        "collector_run_id": "test-run-v3",
        "cohort_utc": "2026-09-01_10",
        "exchange": "binance",
        "stream": "trade",
        "market": "BTCUSDT",
        "coverage_state": "DATA_PRESENT",
        "event_count": 10,
    }
    sha = canonical_sha256(cov_data, excluded=("evidence_sha256",))
    if tamper_hash:
        cov_data["evidence_sha256"] = "0" * 64
    else:
        cov_data["evidence_sha256"] = sha

    encoded = json.dumps(cov_data, indent=2, sort_keys=True).encode("utf-8")
    source_file.write_bytes(encoded)

    file_sha = hashlib.sha256(encoded).hexdigest()
    file_size = len(encoded)

    return ImmutableArtifact(
        kind=ArtifactKind.COVERAGE_EVIDENCE,
        source_path=source_file,
        relative_path=rel,
        environment_id="aws-apne2-research",
        collector_epoch="aws-v3-epoch",
        collector_run_id="test-run-v3",
        cohort="2026-09-01_10",
        exchange="binance",
        stream="trade",
        market="BTCUSDT",
        source_sha256=file_sha,
        source_size=file_size,
        source_record_count=source_record_count,
        manifest_path=None,
        manifest_sha256=None,
    )


@pytest.fixture
def v2_path(tmp_path: Path) -> Path:
    p = tmp_path / "sample_v2_receipt.json"
    payload = {
        "schema_version": 2,
        "state": "FAILED",
        "environment_id": "aws-apne2-research",
        "run_id": "aws-45m-val-run",
        "collector_epoch": "aws-45m-epoch",
        "cohort": "2026-09-09_04",
        "partition": "2026-09-09/binance/orderbook/binance_orderbook_btcusdt_2026-09-09_04.jsonl",
        "raw_size": 19317326,
        "raw_sha256": "78aefbec393c22904b2f3f65dd2a041189c58b63fed7a8380abeabe34ba45081",
        "raw_record_count": 11939,
        "raw_verified_at": "2026-09-09T05:24:14.439215+00:00",
        "compressed_size": 734945,
        "compressed_sha256": "b1384b0c92aa5eabea5d86cf165829ce465d736271a5bb8dbc4e92f40465d6e0",
        "compressed_verified_at": "2026-09-09T05:24:14.528746+00:00",
        "compression_algorithm": "zstd",
        "compression_level": 1,
        "compression_version": "0.25.0",
        "remote_key": "market-data/temporary/key.zst",
        "remote_size": None,
        "remote_checksum": None,
        "remote_version_id": None,
        "remote_verified_at": None,
        "restore_verified_at": None,
        "cleanup_eligible": False,
        "cleanup_completed_at": None,
        "failure_stage": "COMPRESSED_VERIFIED",
        "failure_reason": "Forbidden",
    }
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def test_raw_v3_requires_manifest(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="RAW_MANIFEST_BINDING_REQUIRED"):
        pipeline(tmp_path).finalize_artifact(raw_artifact(tmp_path, manifest_sha256=None))


def test_raw_v3_requires_manifest_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="RAW_MANIFEST_BINDING_REQUIRED"):
        pipeline(tmp_path).finalize_artifact(raw_artifact(tmp_path, manifest_path=None))


def test_raw_v3_requires_record_count(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="raw record count is required"):
        pipeline(tmp_path).finalize_artifact(raw_artifact(tmp_path, source_record_count=None))



def test_coverage_v3_has_no_record_count(tmp_path: Path) -> None:
    receipt = pipeline(tmp_path).finalize_artifact(coverage_artifact(tmp_path))
    assert receipt.artifact_kind == "COVERAGE_EVIDENCE"
    assert receipt.source_record_count is None
    assert receipt.restore_verified_at is not None


def test_coverage_v3_forbids_record_count(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="COVERAGE_RECORD_COUNT_FORBIDDEN"):
        pipeline(tmp_path).finalize_artifact(coverage_artifact(tmp_path, source_record_count=100))


def test_v2_adapter_does_not_rewrite(v2_path: Path) -> None:
    before = v2_path.read_bytes()
    normalized = adapt_legacy_v2_receipt(json.loads(before))
    assert normalized.artifact_kind == "RAW_DATA"
    assert normalized.schema_version == 3
    assert normalized.source_path == "2026-09-09/binance/orderbook/binance_orderbook_btcusdt_2026-09-09_04.jsonl"
    assert normalized.source_size == 19317326
    assert normalized.source_sha256 == "78aefbec393c22904b2f3f65dd2a041189c58b63fed7a8380abeabe34ba45081"
    assert normalized.source_record_count == 11939
    assert normalized.exchange == "binance"
    assert normalized.stream == "orderbook"
    assert normalized.market == "btcusdt"
    assert v2_path.read_bytes() == before


def test_v2_adapter_requires_schema_v2() -> None:
    with pytest.raises(ValueError, match="schema_version 2"):
        adapt_legacy_v2_receipt({"schema_version": 3})


def test_v3_from_dict_rejects_v2_directly() -> None:
    with pytest.raises(ValueError, match="adapt_legacy_v2_receipt"):
        ArchiveReceiptV3.from_dict({"schema_version": 2})


def test_wrong_artifact_kind_raises(tmp_path: Path) -> None:
    art = coverage_artifact(tmp_path)
    object.__setattr__(art, "kind", "UNKNOWN_KIND")
    with pytest.raises(ValueError, match="unsupported artifact kind"):
        pipeline(tmp_path).finalize_artifact(art)


def test_path_escape_relative_path(tmp_path: Path) -> None:
    art = raw_artifact(tmp_path, relative_path="../escaped.jsonl")
    with pytest.raises(ValueError, match="escape|invalid"):
        pipeline(tmp_path).finalize_artifact(art)


def test_manifest_hash_mismatch_fails(tmp_path: Path) -> None:
    art = raw_artifact(tmp_path, manifest_sha256="0" * 64)
    with pytest.raises(ValueError, match="manifest"):
        pipeline(tmp_path).finalize_artifact(art)


def test_coverage_canonical_hash_mismatch_fails(tmp_path: Path) -> None:
    art = coverage_artifact(tmp_path, tamper_hash=True)
    with pytest.raises(ValueError, match="coverage hash mismatch|TAMPERED_COVERAGE_HASH"):
        pipeline(tmp_path).finalize_artifact(art)


def test_remote_checksum_mismatch_artifact(tmp_path: Path) -> None:
    store = MemoryArchiveStore()
    pipe = pipeline(tmp_path, store=store)
    art = coverage_artifact(tmp_path)
    pipe.finalize_artifact(art)
    compressed_path = pipe.artifact_compressed_path(art)
    store.head_override = RemoteObject(
        pipe.artifact_remote_key(art),
        compressed_path.stat().st_size,
        base64.b64encode(b"x" * 32).decode("ascii"),
    )
    rec = pipe._load_receipt(pipe.artifact_receipt_path(art))
    assert rec is not None
    rec.state = ArchiveState.COMPRESSED_VERIFIED.value
    rec.cleanup_eligible = False
    pipe._write_receipt(pipe.artifact_receipt_path(art), rec)
    with pytest.raises(ValueError, match="remote object checksum mismatch|checksum"):
        pipe.finalize_artifact(art)


def test_restore_mismatch_artifact(tmp_path: Path) -> None:
    class MutatingDownloadStore(MemoryArchiveStore):
        @contextmanager
        def open_download(self, key: str):
            data = bytearray(self.objects[key])
            data[-1] ^= 1
            handle = io.BytesIO(bytes(data))
            try:
                yield handle
            finally:
                handle.close()

    pipe = pipeline(tmp_path, store=MutatingDownloadStore())
    art = coverage_artifact(tmp_path)
    with pytest.raises(Exception):
        pipe.finalize_artifact(art)


def test_idempotent_reuse_artifact(tmp_path: Path) -> None:
    store = MemoryArchiveStore()
    pipe = pipeline(tmp_path, store=store)
    art = coverage_artifact(tmp_path)
    rec1 = pipe.finalize_artifact(art)
    assert rec1.state == ArchiveState.CLEANUP_ELIGIBLE.value
    assert store.upload_calls == 1

    rec2 = pipe.finalize_artifact(art)
    assert rec2.state == ArchiveState.CLEANUP_ELIGIBLE.value
    assert store.upload_calls == 1


def test_legacy_finalize_produces_v3_receipt(tmp_path: Path) -> None:
    pipe = pipeline(tmp_path)
    art = raw_artifact(tmp_path)
    receipt = pipe.finalize(
        art.source_path,
        now=datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
        grace_period=timedelta(0),
        stability_wait_seconds=0,
    )
    assert receipt.schema_version == 3
    assert receipt.artifact_kind == "RAW_DATA"
    assert receipt.source_record_count == 10
    assert receipt.restore_verified_at is not None


def test_manifest_schema_version_5_accepted(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    manifest_root = tmp_path / "manifests"
    rel = "2026-09-01/binance/trade/binance_trade_btcusdt_2026-09-01_10.jsonl"
    source_file = raw_root / rel
    source_file.parent.mkdir(parents=True, exist_ok=True)
    records = [{"exchange": "binance", "stream": "trade", "market": "BTCUSDT", "data": i} for i in range(10)]
    source_file.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    data = source_file.read_bytes()
    raw_sha = hashlib.sha256(data).hexdigest()

    manifest_file = manifest_root / f"manifest_{source_file.stem}.json"
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 5,
        "partition_path": rel,
        "bytes": len(data),
        "sha256": raw_sha,
        "record_count": 10,
        "environment_id": "aws-apne2-research",
        "collector_epoch": "test-epoch",
        "collector_run_id": "test-run",
        "cohort": "2026-09-01_10",
        "feed_identity": "binance/trade/btcusdt",
    }
    manifest_file.write_text(json.dumps(payload), encoding="utf-8")

    art = ImmutableArtifact(
        kind=ArtifactKind.RAW_DATA,
        source_path=source_file,
        relative_path=rel,
        environment_id="aws-apne2-research",
        collector_epoch="test-epoch",
        collector_run_id="test-run",
        cohort="2026-09-01_10",
        exchange="binance",
        stream="trade",
        market="btcusdt",
        source_sha256=raw_sha,
        source_size=len(data),
        source_record_count=10,
        manifest_path=manifest_file,
        manifest_sha256=file_sha256(manifest_file),
    )
    receipt = pipeline(tmp_path).finalize_artifact(art)
    assert receipt.artifact_kind == "RAW_DATA"
    assert receipt.source_record_count == 10
    assert receipt.state == ArchiveState.CLEANUP_ELIGIBLE.value


def _create_test_raw_and_manifest(
    tmp_path: Path,
    *,
    schema_version: int = 5,
    payload_overrides: dict[str, Any] | None = None,
    corrupt_hash: bool = False,
    corrupt_count: bool = False,
) -> tuple[ArchivePipeline, ImmutableArtifact]:
    raw_root = tmp_path / "raw"
    manifest_root = tmp_path / "manifests"
    rel = "2026-09-01/binance/trade/binance_trade_btcusdt_2026-09-01_10.jsonl"
    source_file = raw_root / rel
    source_file.parent.mkdir(parents=True, exist_ok=True)
    records = [{"exchange": "binance", "stream": "trade", "market": "BTCUSDT", "data": i} for i in range(10)]
    source_file.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    data = source_file.read_bytes()
    raw_sha = hashlib.sha256(data).hexdigest()

    manifest_file = manifest_root / f"manifest_{source_file.stem}.json"
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    if schema_version == 4:
        payload: dict[str, Any] = {
            "schema_version": 4,
            "partition_path": rel,
            "bytes": len(data),
            "sha256": raw_sha if not corrupt_hash else "badhash" * 8,
            "record_count": 10 if not corrupt_count else 99,
        }
    else:
        payload = {
            "schema_version": schema_version,
            "partition_path": rel,
            "bytes": len(data),
            "sha256": raw_sha if not corrupt_hash else "badhash" * 8,
            "record_count": 10 if not corrupt_count else 99,
            "environment_id": "aws-apne2-research",
            "collector_epoch": "test-epoch",
            "collector_run_id": "test-run",
            "cohort": "2026-09-01_10",
            "feed_identity": "binance/trade/btcusdt",
        }
    if payload_overrides:
        for k, v in payload_overrides.items():
            if v is None:
                payload.pop(k, None)
            else:
                payload[k] = v

    manifest_file.write_text(json.dumps(payload), encoding="utf-8")

    art = ImmutableArtifact(
        kind=ArtifactKind.RAW_DATA,
        source_path=source_file,
        relative_path=rel,
        environment_id="aws-apne2-research",
        collector_epoch="test-epoch",
        collector_run_id="test-run",
        cohort="2026-09-01_10",
        exchange="binance",
        stream="trade",
        market="btcusdt",
        source_sha256=raw_sha,
        source_size=len(data),
        source_record_count=10,
        manifest_path=manifest_file,
        manifest_sha256=file_sha256(manifest_file),
    )
    pipe = pipeline(tmp_path)
    return pipe, art


def test_schema4_legacy_accepted(tmp_path: Path) -> None:
    pipe, art = _create_test_raw_and_manifest(tmp_path, schema_version=4)
    receipt = pipe.finalize_artifact(art)
    assert receipt.artifact_kind == "RAW_DATA"
    assert receipt.source_record_count == 10
    assert receipt.state == ArchiveState.CLEANUP_ELIGIBLE.value


def test_schema5_correct_identity_accepted(tmp_path: Path) -> None:
    pipe, art = _create_test_raw_and_manifest(tmp_path, schema_version=5)
    receipt = pipe.finalize_artifact(art)
    assert receipt.artifact_kind == "RAW_DATA"
    assert receipt.source_record_count == 10
    assert receipt.state == ArchiveState.CLEANUP_ELIGIBLE.value


def test_schema5_missing_identity_field_rejected(tmp_path: Path) -> None:
    required = ["environment_id", "collector_epoch", "collector_run_id", "cohort", "feed_identity"]
    for missing_field in required:
        sub = tmp_path / missing_field
        pipe, art = _create_test_raw_and_manifest(sub, schema_version=5, payload_overrides={missing_field: None})
        with pytest.raises(ValueError, match="missing required identity field"):
            pipe.finalize_artifact(art)


def test_schema5_wrong_environment_id_rejected(tmp_path: Path) -> None:
    pipe, art = _create_test_raw_and_manifest(tmp_path, schema_version=5, payload_overrides={"environment_id": "wrong-env"})
    with pytest.raises(ValueError, match="manifest environment_id mismatch"):
        pipe.finalize_artifact(art)


def test_schema5_wrong_collector_epoch_rejected(tmp_path: Path) -> None:
    pipe, art = _create_test_raw_and_manifest(tmp_path, schema_version=5, payload_overrides={"collector_epoch": "wrong-epoch"})
    with pytest.raises(ValueError, match="manifest collector_epoch mismatch"):
        pipe.finalize_artifact(art)


def test_schema5_wrong_collector_run_id_rejected(tmp_path: Path) -> None:
    pipe, art = _create_test_raw_and_manifest(tmp_path, schema_version=5, payload_overrides={"collector_run_id": "wrong-run"})
    with pytest.raises(ValueError, match="manifest collector_run_id mismatch"):
        pipe.finalize_artifact(art)


def test_schema5_wrong_cohort_rejected(tmp_path: Path) -> None:
    pipe, art = _create_test_raw_and_manifest(tmp_path, schema_version=5, payload_overrides={"cohort": "2026-09-01_99"})
    with pytest.raises(ValueError, match="manifest cohort mismatch"):
        pipe.finalize_artifact(art)


def test_schema5_wrong_feed_identity_rejected(tmp_path: Path) -> None:
    pipe, art = _create_test_raw_and_manifest(tmp_path, schema_version=5, payload_overrides={"feed_identity": "bithumb/orderbook/KRW-BTC"})
    with pytest.raises(ValueError, match="manifest feed_identity mismatch"):
        pipe.finalize_artifact(art)


def test_schema6_rejected(tmp_path: Path) -> None:
    pipe, art = _create_test_raw_and_manifest(tmp_path, schema_version=6)
    with pytest.raises(ValueError, match="raw manifest is missing or unsupported"):
        pipe.finalize_artifact(art)


def test_raw_hash_or_count_mismatch_still_rejected(tmp_path: Path) -> None:
    sub1 = tmp_path / "bad_hash"
    pipe1, art1 = _create_test_raw_and_manifest(sub1, schema_version=5, corrupt_hash=True)
    with pytest.raises(ValueError, match="raw partition does not match its manifest"):
        pipe1.finalize_artifact(art1)

    sub2 = tmp_path / "bad_count"
    pipe2, art2 = _create_test_raw_and_manifest(sub2, schema_version=5, corrupt_count=True)
    with pytest.raises(ValueError, match="raw partition does not match its manifest"):
        pipe2.finalize_artifact(art2)


if __name__ == "__main__":
    unittest.main()
