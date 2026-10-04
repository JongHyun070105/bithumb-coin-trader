from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
from typing import Callable, Any

import pytest

from scripts.capture_fresh_30h_v2_receipts import capture_frozen_v2_receipts


RUN_ID = "receipt-capture-test"
EPOCH = "epoch-receipt-test"
BUCKET = "research-evidence-test"
PREFIX = f"market-data/temporary/{EPOCH}"
COHORT = "2026-10-03_10"
FEED = "bithumb:trade:KRW-BTC"


COMMIT = "c" * 40


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


class FakeS3:
    def __init__(self, objects: dict[str, bytes], *, mutate_on_second_read: bool = False) -> None:
        self.objects = objects
        self.get_calls: dict[str, int] = {}
        self.list_calls: list[dict[str, object]] = []
        self.mutate_on_second_read = mutate_on_second_read

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        self.list_calls.append(kwargs)
        keys = sorted(self.objects)
        if kwargs.get("ContinuationToken") is None:
            return {
                "Contents": [{"Key": keys[0]}], "IsTruncated": True,
                "NextContinuationToken": "page-2",
                "ResponseMetadata": {"RequestId": "list-1"},
            }
        return {
            "Contents": [{"Key": key} for key in keys[1:]], "IsTruncated": False,
            "ResponseMetadata": {"RequestId": "list-2"},
        }

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        call = self.get_calls.get(Key, 0) + 1
        self.get_calls[Key] = call
        value = self.objects[Key]
        version = "v1"
        if self.mutate_on_second_read and Key.endswith(f"cohort_{COHORT}_finalized.json") and call >= 2:
            value = value + b" "
            version = "v2"
        return {
            "Body": BytesIO(value), "VersionId": version,
            "ContentLength": len(value), "ETag": '"etag"',
            "ResponseMetadata": {"RequestId": f"get-{call}", "HTTPStatusCode": 200},
        }

    def list_object_versions(self, *, Bucket: str, Prefix: str, **kwargs: object) -> dict[str, object]:
        assert Prefix == f"{PREFIX}/terminal/terminal-receipt.json"
        return {"Versions": [{"Key": Prefix, "VersionId": "v1", "IsLatest": True}], "DeleteMarkers": []}


class FakeSTS:
    def get_caller_identity(self) -> dict[str, str]:
        return {"Arn": "arn:aws:iam::123456789012:role/test-capture"}


def _fixture(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    data = tmp_path / "guest-data"
    receipt_root = data / "archive-receipts"
    slot_dir = receipt_root / "slot-receipts" / COHORT
    slot_dir.mkdir(parents=True)
    cohort_payload = {
        "run_id": RUN_ID, "epoch": EPOCH, "cohort": COHORT,
        "cohort_qualification": "QUALIFYING_FULL_HOUR", "status": "PASS",
    }
    cohort = receipt_root / f"cohort_{COHORT}_finalized.json"
    cohort_bytes = _json_bytes(cohort_payload)
    cohort.write_bytes(cohort_bytes)
    feed_identity = FEED.replace(":", "/")
    slot_payload = {
        "receipt_type": "SLOT_RECEIPT", "durability": "BOTH_REQUIRED",
        "receipt_id": f"slot-{COHORT}-{__import__('hashlib').sha256(feed_identity.encode()).hexdigest()}",
        "run_id": RUN_ID, "epoch": EPOCH,
        "cohort_id": COHORT, "feed_id": FEED, "feed_identity": feed_identity,
        "runtime_commit": COMMIT, "coverage_state": "DATA_PRESENT",
        "coverage_archive": {"source_sha256": "a" * 64, "remote_key": f"{PREFIX}/coverage/x.json.zst"},
        "s3_key": f"{PREFIX}/coverage/archive-receipts/slot-receipts/{COHORT}/slot.json",
    }
    slot = slot_dir / "slot.json.slot-receipt.json"
    slot_bytes = _json_bytes(slot_payload)
    slot.write_bytes(slot_bytes)

    archive_sidecar = receipt_root / "bithumb/trade/market/file.jsonl.archive-receipt.json"
    archive_sidecar.parent.mkdir(parents=True)
    compressed = data / "compressed/bithumb/trade/market/file.jsonl.zst"
    compressed.parent.mkdir(parents=True)
    compressed_bytes = b"zstd-compressed-fixture"
    compressed.write_bytes(compressed_bytes)
    archive_sidecar.write_text(json.dumps({
        "schema_version": 3, "artifact_kind": "RAW_DATA", "run_id": RUN_ID,
        "collector_epoch": EPOCH, "source_path": "bithumb/trade/market/file.jsonl",
        "remote_key": f"{PREFIX}/bithumb/trade/market/file.jsonl.zst",
        "compressed_sha256": __import__("hashlib").sha256(compressed_bytes).hexdigest(),
    }))

    journal = {
        "schema_version": 1, "cohort_utc": COHORT, "slot_count": 1,
        "observations": [{
            "cohort_utc": COHORT, "cohort_qualification": "QUALIFYING_FULL_HOUR",
            "observation_start_utc": "2026-10-03T10:00:00Z",
            "observation_end_utc": "2026-10-03T11:00:00Z",
            "feed": {"exchange": "bithumb", "stream": "trade", "market": "KRW-BTC"},
        }],
    }
    journal_path = data / "coverage/journals" / f"journal_{COHORT}.json"
    journal_path.parent.mkdir(parents=True)
    journal_path.write_bytes(_json_bytes(journal))

    terminal = data / "terminal/terminal-receipt.json"
    terminal.parent.mkdir(parents=True)
    terminal_bytes = _json_bytes({"run_id": RUN_ID, "epoch": EPOCH, "s3_uploaded": True})
    terminal.write_bytes(terminal_bytes)
    (terminal.parent / "terminal-witness.json").write_bytes(_json_bytes({
        "run_id": RUN_ID, "epoch": EPOCH, "s3_key": f"{PREFIX}/terminal/terminal-receipt.json",
    }))
    objects = {
        f"{PREFIX}/archive-receipts/cohort_{COHORT}_finalized.json": cohort_bytes,
        slot_payload["s3_key"]: slot_bytes,
        f"{PREFIX}/bithumb/trade/market/file.jsonl.zst": compressed_bytes,
        f"{PREFIX}/terminal/terminal-receipt.json": terminal_bytes,
        f"{PREFIX}/observability/minute/20261003T110000Z.json": b"diagnostic",
    }
    return data, objects


def _clock() -> Callable[[], datetime]:
    current = datetime(2026, 10, 3, 11, 1, tzinfo=timezone.utc)

    def now() -> datetime:
        nonlocal current
        current += timedelta(seconds=1)
        return current

    return now


def test_exports_receipt_scope_journals_provenance_and_two_real_reads(tmp_path: Path) -> None:
    data, objects = _fixture(tmp_path)
    s3 = FakeS3(objects)
    sleeps: list[float] = []
    bundle = tmp_path / "bundle"
    result = capture_frozen_v2_receipts(
        data_dir=data, expected_runtime_commit=COMMIT, bundle_root=bundle, run_id=RUN_ID, epoch=EPOCH,
        bucket=BUCKET, prefix=PREFIX, s3=s3, sts=FakeSTS(),
        observation_interval_seconds=1800, sleep_fn=sleeps.append, now=_clock(),
    )

    inventory = json.loads((bundle / "terminal/receipt-inventory.json").read_text())
    cohorts = json.loads((bundle / "terminal/cohorts.json").read_text())
    assert sleeps == [1800]
    assert inventory["s3_prefix_listing"]["request_ids"] == ["list-1", "list-2"]
    assert inventory["s3_prefix_listing"]["caller_arn"].endswith("role/test-capture")
    assert inventory["unexpected_s3_objects"] == [f"{PREFIX}/observability/minute/20261003T110000Z.json"]
    assert inventory["required_immutable_receipt_ids"] == [f"cohort-{COHORT}"]
    cohort_observations = [row for row in inventory["observations"] if row["receipt_id"] == f"cohort-{COHORT}"]
    assert len(cohort_observations) == 2
    assert cohort_observations[0]["captured_at_utc"] != cohort_observations[1]["captured_at_utc"]
    assert s3.get_calls[f"{PREFIX}/archive-receipts/cohort_{COHORT}_finalized.json"] == 2
    assert len(s3.list_calls) == 2
    assert cohorts["source_journals"] == [{
        "cohort_id": COHORT, "path": f"terminal/journals/journal_{COHORT}.json",
        "sha256": __import__("hashlib").sha256((bundle / f"terminal/journals/journal_{COHORT}.json").read_bytes()).hexdigest(),
    }]
    assert cohorts["cohorts"][0]["feed_slots"][0]["s3_receipt_sha256"]
    assert (bundle / "terminal/s3-readback/terminal-receipt.json").read_bytes() == objects[f"{PREFIX}/terminal/terminal-receipt.json"]
    assert json.loads((bundle / "terminal/s3-readback.json").read_text())["http_status"] == 200
    assert result["receipts"] == inventory["receipts"]


def test_requires_full_runtime_commit_anchor_before_receipt_capture(tmp_path: Path) -> None:
    data, objects = _fixture(tmp_path)
    with pytest.raises(ValueError, match="full 40-hex runtime commit anchor is required"):
        capture_frozen_v2_receipts(
            data_dir=data, expected_runtime_commit="missing", bundle_root=tmp_path / "bundle",
            run_id=RUN_ID, epoch=EPOCH, bucket=BUCKET, prefix=PREFIX,
            s3=FakeS3(objects), sts=FakeSTS(), observation_interval_seconds=1800,
            sleep_fn=lambda _: None, now=_clock(),
        )


def test_refuses_terminal_get_if_a_newer_s3_version_appears_during_capture(tmp_path: Path) -> None:
    data, objects = _fixture(tmp_path)

    class OverwrittenTerminal(FakeS3):
        def list_object_versions(self, *, Bucket: str, Prefix: str,
                                 **kwargs: object) -> dict[str, object]:
            assert Prefix == f"{PREFIX}/terminal/terminal-receipt.json"
            return {
                "Versions": [
                    {"Key": Prefix, "VersionId": "v2", "IsLatest": True},
                    {"Key": Prefix, "VersionId": "v1", "IsLatest": False},
                ],
                "DeleteMarkers": [],
            }

    with pytest.raises(ValueError, match="no longer identifies the latest S3 object version"):
        capture_frozen_v2_receipts(
            data_dir=data, expected_runtime_commit=COMMIT, bundle_root=tmp_path / "bundle",
            run_id=RUN_ID, epoch=EPOCH, bucket=BUCKET, prefix=PREFIX,
            s3=OverwrittenTerminal(objects), sts=FakeSTS(),
            observation_interval_seconds=1800, sleep_fn=lambda _: None, now=_clock(),
        )


def test_second_cohort_read_change_is_preserved_for_frozen_auditor_to_reject(tmp_path: Path) -> None:
    data, objects = _fixture(tmp_path)
    bundle = tmp_path / "bundle"
    result = capture_frozen_v2_receipts(
        data_dir=data, expected_runtime_commit=COMMIT, bundle_root=bundle, run_id=RUN_ID, epoch=EPOCH,
        bucket=BUCKET, prefix=PREFIX,
        s3=FakeS3(objects, mutate_on_second_read=True), sts=FakeSTS(),
        observation_interval_seconds=1800, sleep_fn=lambda _seconds: None, now=_clock(),
    )
    reads = [row for row in result["observations"] if row["receipt_id"] == f"cohort-{COHORT}"]
    assert reads[0]["sha256"] != reads[1]["sha256"]
    assert reads[0]["version_id"] != reads[1]["version_id"]


def _write_sealed_identity(bundle: Path, *, run_id: str = RUN_ID, epoch: str = EPOCH,
                           bucket: str = BUCKET, prefix: str = PREFIX) -> None:
    identity = bundle / "sealed/identity.json"
    identity.parent.mkdir(parents=True, exist_ok=True)
    identity.write_bytes(_json_bytes({
        "run_id": run_id, "epoch": epoch,
        "software_commit_sha": COMMIT,
        "s3_bucket": bucket, "s3_prefix": prefix,
    }))


def test_refuses_existing_unbound_bundle_output(tmp_path: Path) -> None:
    data, objects = _fixture(tmp_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "preserve.txt").write_text("existing")
    with pytest.raises(FileExistsError, match="exact sealed identity"):
        capture_frozen_v2_receipts(
            data_dir=data, expected_runtime_commit=COMMIT, bundle_root=bundle, run_id=RUN_ID, epoch=EPOCH,
            bucket=BUCKET, prefix=PREFIX, s3=FakeS3(objects), sts=FakeSTS(),
            sleep_fn=lambda _seconds: None, now=_clock(),
        )
    assert (bundle / "preserve.txt").read_text() == "existing"


def test_refuses_existing_bundle_with_mismatched_sealed_identity(tmp_path: Path) -> None:
    data, objects = _fixture(tmp_path)
    bundle = tmp_path / "bundle"
    _write_sealed_identity(bundle, run_id="other-run")
    capture_path = bundle / "terminal/systemd-terminal.json"
    capture_path.parent.mkdir(parents=True)
    capture_path.write_text('{"result":"success"}\n')

    with pytest.raises(ValueError, match="identity differs"):
        capture_frozen_v2_receipts(
            data_dir=data, expected_runtime_commit=COMMIT, bundle_root=bundle, run_id=RUN_ID, epoch=EPOCH,
            bucket=BUCKET, prefix=PREFIX, s3=FakeS3(objects), sts=FakeSTS(),
            sleep_fn=lambda _seconds: None, now=_clock(),
        )
    assert capture_path.read_text() == '{"result":"success"}\n'


def test_refuses_runtime_anchor_that_differs_from_sealed_identity(tmp_path: Path) -> None:
    data, objects = _fixture(tmp_path)
    bundle = tmp_path / "bundle"
    _write_sealed_identity(bundle)
    with pytest.raises(ValueError, match="sealed identity differs from the runtime commit anchor"):
        capture_frozen_v2_receipts(
            data_dir=data, expected_runtime_commit="d" * 40, bundle_root=bundle,
            run_id=RUN_ID, epoch=EPOCH, bucket=BUCKET, prefix=PREFIX,
            s3=FakeS3(objects), sts=FakeSTS(), observation_interval_seconds=1800,
            sleep_fn=lambda _: None, now=_clock(),
        )


def test_refuses_symlinks_inside_identity_bound_staging_tree(tmp_path: Path) -> None:
    data, objects = _fixture(tmp_path)
    bundle = tmp_path / "bundle"
    _write_sealed_identity(bundle)
    outside = tmp_path / "outside"
    outside.mkdir()
    (bundle / "terminal").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="must not contain symlinks"):
        capture_frozen_v2_receipts(
            data_dir=data, expected_runtime_commit=COMMIT, bundle_root=bundle, run_id=RUN_ID, epoch=EPOCH,
            bucket=BUCKET, prefix=PREFIX, s3=FakeS3(objects), sts=FakeSTS(),
            sleep_fn=lambda _seconds: None, now=_clock(),
        )
    assert list(outside.iterdir()) == []


def test_adds_receipt_evidence_to_exact_identity_bound_staging_tree(tmp_path: Path) -> None:
    data, objects = _fixture(tmp_path)
    bundle = tmp_path / "bundle"
    _write_sealed_identity(bundle)
    systemd_path = bundle / "terminal/systemd-terminal.json"
    journal_path = bundle / "terminal/systemd-invocation.jsonl"
    witness_path = bundle / "terminal/terminal-witness.json"
    canonical_readback_path = bundle / "terminal/s3-readback/terminal-receipt.json"
    systemd_bytes = b'{"result":"success","exit_status":0}\n'
    journal_bytes = b'{"_SYSTEMD_INVOCATION_ID":"invocation"}\n'
    witness_bytes = (data / "terminal/terminal-witness.json").read_bytes()
    terminal_bytes = objects[f"{PREFIX}/terminal/terminal-receipt.json"]
    systemd_path.parent.mkdir(parents=True, exist_ok=True)
    systemd_path.write_bytes(systemd_bytes)
    journal_path.write_bytes(journal_bytes)
    witness_path.write_bytes(witness_bytes)
    canonical_readback_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_readback_path.write_bytes(terminal_bytes)

    capture_frozen_v2_receipts(
        data_dir=data, expected_runtime_commit=COMMIT, bundle_root=bundle, run_id=RUN_ID, epoch=EPOCH,
        bucket=BUCKET, prefix=PREFIX, s3=FakeS3(objects), sts=FakeSTS(),
        observation_interval_seconds=1800, sleep_fn=lambda _seconds: None,
        now=_clock(),
    )

    assert systemd_path.read_bytes() == systemd_bytes
    assert journal_path.read_bytes() == journal_bytes
    assert witness_path.read_bytes() == witness_bytes
    assert canonical_readback_path.read_bytes() == terminal_bytes
    assert (bundle / "terminal/receipt-inventory.json").is_file()
    assert (bundle / "terminal/cohorts.json").is_file()


def test_refuses_two_point_interval_shorter_than_thirty_minutes(tmp_path: Path) -> None:
    data, objects = _fixture(tmp_path)
    with pytest.raises(ValueError, match="at least 30 minutes"):
        capture_frozen_v2_receipts(
            data_dir=data, expected_runtime_commit=COMMIT, bundle_root=tmp_path / "bundle", run_id=RUN_ID,
            epoch=EPOCH, bucket=BUCKET, prefix=PREFIX, s3=FakeS3(objects),
            sts=FakeSTS(), observation_interval_seconds=1799,
            sleep_fn=lambda _seconds: None, now=_clock(),
        )


def test_refuses_slot_receipt_with_non_contract_feed_identity(tmp_path: Path) -> None:
    data, objects = _fixture(tmp_path)
    slot = next((data / "archive-receipts/slot-receipts").rglob("*.slot-receipt.json"))
    payload = json.loads(slot.read_text())
    payload["feed_id"] = "bithumb/trade/KRW-BTC"
    slot.write_bytes(_json_bytes(payload))
    with pytest.raises(ValueError, match="exchange:stream:market"):
        capture_frozen_v2_receipts(
            data_dir=data, expected_runtime_commit=COMMIT, bundle_root=tmp_path / "bundle", run_id=RUN_ID, epoch=EPOCH,
            bucket=BUCKET, prefix=PREFIX, s3=FakeS3(objects), sts=FakeSTS(),
            observation_interval_seconds=1800, sleep_fn=lambda _: None, now=_clock(),
        )


@pytest.mark.parametrize("mutation,match", [
    ({"coverage_state": "FAILED"}, "coverage_state"),
    ({"runtime_commit": "HEAD"}, "runtime_commit is not a full commit"),
    ({"runtime_commit": "d" * 40}, "differs from the run anchor"),
    ({"receipt_id": "slot-forged"}, "receipt_id"),
    ({"feed_identity": "bithumb/trade/KRW-ETH"}, "feed_identity"),
    ({"coverage_archive": {"source_sha256": "zz", "remote_key": "x"}}, "coverage_archive"),
])
def test_refuses_slot_receipt_with_unbound_content(tmp_path: Path, mutation: dict, match: str) -> None:
    data, objects = _fixture(tmp_path)
    slot = next((data / "archive-receipts/slot-receipts").rglob("*.slot-receipt.json"))
    payload = json.loads(slot.read_text())
    payload.update(mutation)
    slot.write_bytes(_json_bytes(payload))
    with pytest.raises(ValueError, match=match):
        capture_frozen_v2_receipts(
            data_dir=data, expected_runtime_commit=COMMIT, bundle_root=tmp_path / "bundle", run_id=RUN_ID, epoch=EPOCH,
            bucket=BUCKET, prefix=PREFIX, s3=FakeS3(objects), sts=FakeSTS(),
            observation_interval_seconds=1800, sleep_fn=lambda _: None, now=_clock(),
        )


def test_undeclared_non_observer_object_is_not_laundered_as_optional(tmp_path: Path) -> None:
    data, objects = _fixture(tmp_path)
    stray = f"{PREFIX}/archive-receipts/slot-receipts/2026-10-03_09/stale.slot-receipt.json"
    objects[stray] = b"stale-epoch-object"
    inventory = capture_frozen_v2_receipts(
        data_dir=data, expected_runtime_commit=COMMIT, bundle_root=tmp_path / "bundle", run_id=RUN_ID, epoch=EPOCH,
        bucket=BUCKET, prefix=PREFIX, s3=FakeS3(objects), sts=FakeSTS(),
        observation_interval_seconds=1800, sleep_fn=lambda _: None, now=_clock(),
    )
    declared = json.loads((tmp_path / "bundle/terminal/receipt-inventory.json").read_text())
    assert all(row.get("s3_key") != stray for row in declared["receipts"])
    assert stray in {o["key"] for o in declared["s3_prefix_listing"]["objects"]}


def test_transient_second_read_error_is_retried_not_recorded_as_mismatch(tmp_path: Path) -> None:
    data, objects = _fixture(tmp_path)

    class Flaky(FakeS3):
        failed = False

        def get_object(self, *, Bucket: str, Key: str, **kwargs: Any) -> dict[str, object]:
            if self.get_calls.get(Key, 0) >= 1 and "cohort_" in Key and not self.failed:
                self.failed = True
                raise RuntimeError("transient")
            return super().get_object(Bucket=Bucket, Key=Key, **kwargs)

    s3 = Flaky(objects)
    capture_frozen_v2_receipts(
        data_dir=data, expected_runtime_commit=COMMIT, bundle_root=tmp_path / "bundle", run_id=RUN_ID, epoch=EPOCH,
        bucket=BUCKET, prefix=PREFIX, s3=s3, sts=FakeSTS(),
        observation_interval_seconds=1800, sleep_fn=lambda _: None, now=_clock(),
    )
    assert s3.failed
    obs = json.loads((tmp_path / "bundle/terminal/receipt-inventory.json").read_text())["observations"]
    assert len(obs) == 2 and all(o["sha256"] for o in obs)
