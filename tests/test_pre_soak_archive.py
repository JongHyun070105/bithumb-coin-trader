from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import base64
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import zstandard

from bithumb_coin_trader.pre_soak_archive import (
    ArchivePipeline,
    ArchiveState,
    MemoryArchiveStore,
    RemoteObject,
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

    def test_end_to_end_without_cleanup_preserves_raw(self) -> None:
        receipt = self._finalize()
        self.assertEqual(receipt.state, ArchiveState.CLEANUP_ELIGIBLE.value)
        self.assertTrue(receipt.cleanup_eligible)
        self.assertTrue(self.raw.exists())
        self.assertTrue(self.pipeline.compressed_path(self.raw).exists())
        self.assertEqual(receipt.raw_sha256, hashlib.sha256(self.raw.read_bytes()).hexdigest())
        self.assertNotEqual(receipt.raw_sha256, receipt.compressed_sha256)

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


if __name__ == "__main__":
    unittest.main()
