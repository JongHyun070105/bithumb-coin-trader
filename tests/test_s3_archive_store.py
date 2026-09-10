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


class S3ClientError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": status},
            "Error": {"Code": code, "Message": message},
        }


class MalformedS3Error(Exception):
    def __init__(self, response) -> None:
        super().__init__("malformed S3 error response")
        self.response = response


class RacingS3Client(FakeS3Client):
    def put_object(self, **kwargs):
        body = kwargs["Body"].read()
        self.objects[kwargs["Key"]] = body
        raise S3ClientError(412, "PreconditionFailed", "object already exists")


class ScriptedConditionalS3Client(FakeS3Client):
    def __init__(self, outcomes: list[object]) -> None:
        super().__init__()
        self.outcomes = outcomes
        self.body_handles = []

    def put_object(self, **kwargs):
        self.body_handles.append(kwargs["Body"])
        body = kwargs["Body"].read()
        self.put_requests.append({key: value for key, value in kwargs.items() if key != "Body"})
        outcome = self.outcomes[len(self.put_requests) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, tuple):
            status, code = outcome
            raise S3ClientError(status, code, f"{status}/{code}")
        if outcome == "409":
            raise S3ClientError(409, "ConditionalRequestConflict", "concurrent delete conflict")
        if outcome == "412":
            raise S3ClientError(412, "PreconditionFailed", "object already exists")
        if outcome == "access-denied":
            raise S3ClientError(403, "AccessDenied", "put denied")
        if outcome != "success":
            raise AssertionError(f"unsupported scripted outcome: {outcome}")
        self.objects[kwargs["Key"]] = body
        return {"VersionId": "v1"}


class S3ArchiveStoreTests(unittest.TestCase):
    def _fixture(self, tmp: str) -> tuple[Path, str]:
        path = Path(tmp) / "fixture.zst"
        path.write_bytes(b"same-immutable-object")
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def _assert_error_pair(self, error: S3ClientError, status: int, code: str) -> None:
        self.assertEqual(error.response["ResponseMetadata"]["HTTPStatusCode"], status)
        self.assertEqual(error.response["Error"]["Code"], code)

    def _assert_original_error_preserved(self, response, expected_puts: int = 1) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, checksum = self._fixture(tmp)
            original = MalformedS3Error(response)
            outcomes: list[object] = [original]
            if expected_puts == 2:
                outcomes.insert(0, "409")
            client = ScriptedConditionalS3Client(outcomes)
            store = S3ArchiveStore("example-bucket", client=client)

            with self.assertRaises(MalformedS3Error) as raised:
                store.upload(path, "prefix/fixture.jsonl.zst", checksum)

            self.assertIs(raised.exception, original)
            self.assertEqual(len(client.put_requests), expected_puts)
            self.assertEqual(client.head_requests, [])

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

    def test_409_then_success_retries_with_fresh_stream_before_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, checksum = self._fixture(tmp)
            client = ScriptedConditionalS3Client(["409", "success"])
            store = S3ArchiveStore("example-bucket", client=client)

            remote = store.upload(path, "prefix/fixture.jsonl.zst", checksum)

            self.assertEqual(remote.size, path.stat().st_size)
            self.assertEqual(len(client.put_requests), 2)
            self.assertEqual(len(client.head_requests), 1)
            self.assertIsNot(client.body_handles[0], client.body_handles[1])
            self.assertTrue(all(handle.closed for handle in client.body_handles))
            self.assertTrue(all(request["ContentLength"] == path.stat().st_size for request in client.put_requests))
            self.assertTrue(all(request["ChecksumSHA256"] == base64.b64encode(bytes.fromhex(checksum)).decode() for request in client.put_requests))
            self.assertTrue(all(request["IfNoneMatch"] == "*" for request in client.put_requests))

    def test_repeated_409_stops_after_three_total_attempts_without_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, checksum = self._fixture(tmp)
            client = ScriptedConditionalS3Client(["409", "409", "409"])
            store = S3ArchiveStore("example-bucket", client=client)

            with self.assertRaisesRegex(S3ClientError, "concurrent delete conflict"):
                store.upload(path, "prefix/fixture.jsonl.zst", checksum)

            self.assertEqual(len(client.put_requests), 3)
            self.assertEqual(client.head_requests, [])
            self.assertEqual(len({id(handle) for handle in client.body_handles}), 3)
            self.assertTrue(all(handle.closed for handle in client.body_handles))

    def test_412_reuses_matching_object_without_put_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, checksum = self._fixture(tmp)
            client = ScriptedConditionalS3Client(["412"])
            client.objects["prefix/fixture.jsonl.zst"] = path.read_bytes()
            store = S3ArchiveStore("example-bucket", client=client)

            remote = store.upload(path, "prefix/fixture.jsonl.zst", checksum)

            self.assertEqual(remote.size, path.stat().st_size)
            self.assertEqual(len(client.put_requests), 1)
            self.assertEqual(len(client.head_requests), 1)

    def test_409_then_412_reuses_verified_winner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, checksum = self._fixture(tmp)
            client = ScriptedConditionalS3Client(["409", "412"])
            original_put = client.put_object

            def put_and_seed_winner(**kwargs):
                if len(client.put_requests) == 1:
                    client.objects[kwargs["Key"]] = path.read_bytes()
                return original_put(**kwargs)

            client.put_object = put_and_seed_winner
            store = S3ArchiveStore("example-bucket", client=client)

            remote = store.upload(path, "prefix/fixture.jsonl.zst", checksum)

            self.assertEqual(remote.size, path.stat().st_size)
            self.assertEqual(len(client.put_requests), 2)
            self.assertEqual(len(client.head_requests), 1)

    def test_nonretryable_put_error_propagates_without_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, checksum = self._fixture(tmp)
            client = ScriptedConditionalS3Client(["access-denied"])
            store = S3ArchiveStore("example-bucket", client=client)

            with self.assertRaisesRegex(S3ClientError, "put denied"):
                store.upload(path, "prefix/fixture.jsonl.zst", checksum)

            self.assertEqual(len(client.put_requests), 1)
            self.assertEqual(client.head_requests, [])

    def test_409_access_denied_pair_propagates_without_retry_or_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, checksum = self._fixture(tmp)
            mismatch = (409, "AccessDenied")
            client = ScriptedConditionalS3Client([mismatch, mismatch, mismatch])
            store = S3ArchiveStore("example-bucket", client=client)

            with self.assertRaises(S3ClientError) as raised:
                store.upload(path, "prefix/fixture.jsonl.zst", checksum)

            self._assert_error_pair(raised.exception, *mismatch)
            self.assertEqual(len(client.put_requests), 1)
            self.assertEqual(client.head_requests, [])

    def test_500_conditional_conflict_code_propagates_without_retry_or_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, checksum = self._fixture(tmp)
            mismatch = (500, "ConditionalRequestConflict")
            client = ScriptedConditionalS3Client([mismatch, mismatch, mismatch])
            store = S3ArchiveStore("example-bucket", client=client)

            with self.assertRaises(S3ClientError) as raised:
                store.upload(path, "prefix/fixture.jsonl.zst", checksum)

            self._assert_error_pair(raised.exception, *mismatch)
            self.assertEqual(len(client.put_requests), 1)
            self.assertEqual(client.head_requests, [])

    def test_412_access_denied_pair_propagates_without_reuse_or_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, checksum = self._fixture(tmp)
            key = "prefix/fixture.jsonl.zst"
            mismatch = (412, "AccessDenied")
            client = ScriptedConditionalS3Client([mismatch])
            client.objects[key] = path.read_bytes()
            store = S3ArchiveStore("example-bucket", client=client)

            with self.assertRaises(S3ClientError) as raised:
                store.upload(path, key, checksum)

            self._assert_error_pair(raised.exception, *mismatch)
            self.assertEqual(len(client.put_requests), 1)
            self.assertEqual(client.head_requests, [])

    def test_500_precondition_failed_code_propagates_without_reuse_or_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, checksum = self._fixture(tmp)
            key = "prefix/fixture.jsonl.zst"
            mismatch = (500, "PreconditionFailed")
            client = ScriptedConditionalS3Client([mismatch])
            client.objects[key] = path.read_bytes()
            store = S3ArchiveStore("example-bucket", client=client)

            with self.assertRaises(S3ClientError) as raised:
                store.upload(path, key, checksum)

            self._assert_error_pair(raised.exception, *mismatch)
            self.assertEqual(len(client.put_requests), 1)
            self.assertEqual(client.head_requests, [])

    def test_exact_409_then_mismatched_conflict_code_stops_on_second_put(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, checksum = self._fixture(tmp)
            mismatch = (500, "ConditionalRequestConflict")
            client = ScriptedConditionalS3Client(["409", mismatch, mismatch])
            store = S3ArchiveStore("example-bucket", client=client)

            with self.assertRaises(S3ClientError) as raised:
                store.upload(path, "prefix/fixture.jsonl.zst", checksum)

            self._assert_error_pair(raised.exception, *mismatch)
            self.assertEqual(len(client.put_requests), 2)
            self.assertEqual(client.head_requests, [])

    def test_none_response_preserves_original_error(self) -> None:
        self._assert_original_error_preserved(None)

    def test_empty_response_mapping_preserves_original_error(self) -> None:
        self._assert_original_error_preserved({})

    def test_none_response_metadata_preserves_original_error(self) -> None:
        self._assert_original_error_preserved(
            {"ResponseMetadata": None, "Error": {"Code": "ConditionalRequestConflict"}}
        )

    def test_none_error_payload_preserves_original_error(self) -> None:
        self._assert_original_error_preserved(
            {"ResponseMetadata": {"HTTPStatusCode": 409}, "Error": None}
        )

    def test_non_mapping_response_metadata_preserves_original_error(self) -> None:
        for metadata in ("invalid", [], 409):
            with self.subTest(metadata=metadata):
                self._assert_original_error_preserved(
                    {"ResponseMetadata": metadata, "Error": {"Code": "ConditionalRequestConflict"}}
                )

    def test_non_mapping_error_payload_preserves_original_error(self) -> None:
        for error in ("invalid", [], 409):
            with self.subTest(error=error):
                self._assert_original_error_preserved(
                    {"ResponseMetadata": {"HTTPStatusCode": 409}, "Error": error}
                )

    def test_missing_http_status_preserves_original_error(self) -> None:
        self._assert_original_error_preserved(
            {"ResponseMetadata": {}, "Error": {"Code": "ConditionalRequestConflict"}}
        )

    def test_missing_error_code_preserves_original_error(self) -> None:
        self._assert_original_error_preserved(
            {"ResponseMetadata": {"HTTPStatusCode": 409}, "Error": {}}
        )

    def test_exact_409_then_malformed_response_preserves_original_error(self) -> None:
        self._assert_original_error_preserved(None, expected_puts=2)


if __name__ == "__main__":
    unittest.main()
