from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import io
from pathlib import Path
import tempfile
import unittest

from bithumb_coin_trader.pre_soak_archive import S3ArchiveStore


class FakeS3Client:
    def __init__(self) -> None:
        self.objects = {}
        self.put_requests = []
        self.head_requests = []
        self.get_requests = []

    def put_object(self, **kwargs):
        body = kwargs["Body"].read()
        self.put_requests.append({key: value for key, value in kwargs.items() if key != "Body"})
        self.objects[kwargs["Key"]] = body
        return {"VersionId": "v1"}

    def head_object(self, **kwargs):
        self.head_requests.append(kwargs)
        data = self.objects[kwargs["Key"]]
        return {
            "ContentLength": len(data),
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(data).digest()).decode("ascii"),
            "VersionId": "v1",
        }

    def get_object(self, **kwargs):
        self.get_requests.append(kwargs)
        return {"Body": io.BytesIO(self.objects[kwargs["Key"]])}


class ConditionalConflict(Exception):
    def __init__(self) -> None:
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": 412},
            "Error": {"Code": "PreconditionFailed"},
        }


class RacingS3Client(FakeS3Client):
    def put_object(self, **kwargs):
        body = kwargs["Body"].read()
        self.objects[kwargs["Key"]] = body
        raise ConditionalConflict()


class S3ArchiveStoreTests(unittest.TestCase):
    def test_full_object_sha256_and_stream_restore_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.zst"
            path.write_bytes(b"compressed-fixture")
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            client = FakeS3Client()
            store = S3ArchiveStore("example-bucket", client=client)
            remote = store.upload(path, "market-data/temporary/epoch/fixture.jsonl.zst", checksum)
            self.assertEqual(remote.size, path.stat().st_size)
            self.assertEqual(client.put_requests[0]["ChecksumSHA256"], base64.b64encode(bytes.fromhex(checksum)).decode())
            self.assertEqual(client.put_requests[0]["IfNoneMatch"], "*")
            self.assertEqual(client.head_requests[0]["ChecksumMode"], "ENABLED")
            with store.open_download(remote.key) as handle:
                self.assertEqual(handle.read(), path.read_bytes())

    def test_etag_is_never_used_as_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.zst"
            path.write_bytes(b"data")
            client = FakeS3Client()
            store = S3ArchiveStore("example-bucket", client=client)
            remote = store.upload(path, "prefix/fixture.jsonl.zst", hashlib.sha256(b"data").hexdigest())
            self.assertIsNotNone(remote.checksum_sha256_base64)
            self.assertFalse(any("ETag" in request for request in client.put_requests))

    def test_conditional_write_race_is_verified_by_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.zst"
            path.write_bytes(b"same-immutable-object")
            client = RacingS3Client()
            store = S3ArchiveStore("example-bucket", client=client)
            remote = store.upload(
                path,
                "prefix/fixture.jsonl.zst",
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            self.assertEqual(remote.size, path.stat().st_size)
            self.assertEqual(len(client.head_requests), 1)


if __name__ == "__main__":
    unittest.main()
