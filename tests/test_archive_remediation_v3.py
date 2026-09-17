from __future__ import annotations

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
for d in (ROOT / "src", ROOT / "scripts"):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from bithumb_coin_trader.archive_cohort import ArchiveCohortId
from bithumb_coin_trader.archive_scheduler import (
    ArchiveSchedulerConfig,
    ClosedHourArchiveScheduler,
)
from bithumb_coin_trader.closed_hour_finalizer import SEALED_FEED_UNIVERSE
from bithumb_coin_trader.feed_hour_coverage import (
    FrozenFeedHourObservation,
    save_frozen_journal,
)
from bithumb_coin_trader.session_evidence import (
    SessionSegment,
    WriterHealthSnapshot,
)
from scripts.orchestrate_closed_hour_archive import (
    orchestrate_closed_hour_archive,
)


class ArchiveRemediationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.tmp_dir.name)
        self.raw_root = self.base_dir / "raw"
        self.manifest_root = self.base_dir / "manifests"
        self.compressed_root = self.base_dir / "compressed"
        self.receipt_root = self.base_dir / "archive-receipts"
        self.metrics_path = self.base_dir / "collector_metrics.json"
        self.journals_dir = self.base_dir / "coverage" / "journals"

        for d in (self.raw_root, self.manifest_root, self.compressed_root, self.receipt_root, self.journals_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.epoch = "aws-validation-observability-90m-20260917-20260917T062500Z-v3"
        self.run_id = "aws-validation-observability-90m-run-20260917T062500Z-v3"
        self._write_metrics([])

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _write_metrics(self, active_paths: list[str]) -> None:
        self.metrics_path.write_text(json.dumps({"active_partition_files": active_paths}), encoding="utf-8")

    def _config(self, **kwargs) -> ArchiveSchedulerConfig:
        defaults = {
            "epoch": self.epoch,
            "run_id": self.run_id,
            "base_dir": self.base_dir,
            "raw_root": self.raw_root,
            "manifest_root": self.manifest_root,
            "compressed_root": self.compressed_root,
            "receipt_root": self.receipt_root,
            "metrics_path": self.metrics_path,
            "poll_interval_seconds": 30.0,
            "grace_seconds": 600,
            "run_full_scan": False,
            "store_type": "file",
            "file_store_root": self.base_dir / "local-store",
            "dry_run": False,
        }
        defaults.update(kwargs)
        return ArchiveSchedulerConfig(**defaults)

    def _create_v3_journal(
        self,
        date_str: str = "2026-09-17",
        hour_str: str = "06",
        qualification: str = "QUALIFYING_FULL_HOUR",
        event_count: int = 10,
        writer_error_count: int = 0,
    ) -> Path:
        cohort_key = f"{date_str}_{hour_str}"
        dt_start = datetime.fromisoformat(f"{date_str}T{hour_str}:00:00+00:00")
        dt_end = dt_start + timedelta(hours=1)
        interval_start = dt_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        interval_end = dt_end.strftime("%Y-%m-%dT%H:%M:%SZ")

        observations = []
        for feed in SEALED_FEED_UNIVERSE:
            hb_list = [
                datetime.fromtimestamp(dt_start.timestamp() + s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                for s in range(0, 3601, 10)
            ]
            seg = SessionSegment(
                exchange=feed.exchange,
                session_id=f"sess-{feed.exchange}",
                connected_at_utc=f"{date_str}T00:00:00Z",
                disconnected_at_utc=None,
                requested_feeds=(feed.canonical,),
                requested_subscription_sha256="req-hash",
                confirmation_method="LIST_SUBSCRIPTIONS",
                confirmed_at_utc=f"{date_str}T00:01:00Z",
                confirmed_feeds=(feed.canonical,),
                confirmed_subscription_sha256="conf-hash",
                response_evidence_sha256="resp-hash",
                heartbeat_observations_utc=tuple(hb_list),
                maximum_heartbeat_gap_seconds=10.0,
                disconnect_reason=None,
                reconnect_successor_id=None,
                collector_epoch=self.epoch,
                collector_run_id=self.run_id,
            )
            obs = FrozenFeedHourObservation(
                feed=feed,
                cohort_utc=cohort_key,
                interval_start_utc=interval_start,
                interval_end_utc=interval_end,
                cohort_qualification=qualification,
                observation_start_utc=interval_start,
                observation_end_utc=interval_end,
                event_count=event_count,
                first_event_timestamp=interval_start if event_count > 0 else None,
                last_event_timestamp=interval_end if event_count > 0 else None,
                session_segments=(seg,),
                disconnect_count=0,
                reconnect_count=0,
                health=WriterHealthSnapshot(writer_error_count=writer_error_count),
            )
            observations.append(obs)

        return save_frozen_journal(observations, self.journals_dir)

    # ==================================================================
    # DEFECT A: Journal-Driven Grace Contract
    # ==================================================================
    def test_defect_a_journal_before_grace_expiry_not_eligible(self) -> None:
        """A frozen journal exists, but now is before cohort_close + grace_seconds -> NOT eligible."""
        # Cohort 2026-09-17_06 closes at 07:00:00 UTC. Grace is 600s -> expiry is 07:10:00 UTC.
        self._create_v3_journal("2026-09-17", "06")

        # 1. Test at 07:03:00 UTC (well before 07:10:00 UTC)
        now_0703 = datetime(2026, 9, 17, 7, 3, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(grace_seconds=600), now_fn=lambda: now_0703)
        eligible = scheduler.discover_eligible_hours()
        self.assertEqual(len(eligible), 0, "Cohort must NOT be eligible before grace expiry")

        # Orchestrator direct call must also respect grace
        res = orchestrate_closed_hour_archive(
            epoch=self.epoch,
            run_id=self.run_id,
            base_dir=self.base_dir,
            target_cohort=ArchiveCohortId("2026-09-17", "06"),
            grace_seconds=600,
            now=now_0703,
        )
        self.assertIn(res.get("status"), ("WAITING_FOR_GRACE", "IDLE"), "Direct orchestrator call must reject pre-grace finalization")
        self.assertFalse((self.receipt_root / "cohort_2026-09-17_06_finalized.json").exists())

    def test_defect_a_journal_one_second_before_grace_expiry_not_eligible(self) -> None:
        """1 second before grace expiry -> NOT eligible."""
        self._create_v3_journal("2026-09-17", "06")
        now_070959 = datetime(2026, 9, 17, 7, 9, 59, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(grace_seconds=600), now_fn=lambda: now_070959)
        eligible = scheduler.discover_eligible_hours()
        self.assertEqual(len(eligible), 0, "Cohort must NOT be eligible 1 second before grace expiry")

    def test_defect_a_journal_at_and_after_grace_expiry_is_eligible(self) -> None:
        """At exact grace expiry and after -> IS eligible."""
        self._create_v3_journal("2026-09-17", "06")

        # Exact boundary: 07:10:00 UTC
        now_071000 = datetime(2026, 9, 17, 7, 10, 0, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(grace_seconds=600), now_fn=lambda: now_071000)
        eligible = scheduler.discover_eligible_hours()
        self.assertEqual(len(eligible), 1, "Cohort MUST be eligible at exact grace expiry")
        self.assertEqual(eligible[0].cohort.key, "2026-09-17_06")

        # After boundary: 07:10:01 UTC
        now_071001 = datetime(2026, 9, 17, 7, 10, 1, tzinfo=timezone.utc)
        scheduler = ClosedHourArchiveScheduler(self._config(grace_seconds=600), now_fn=lambda: now_071001)
        eligible = scheduler.discover_eligible_hours()
        self.assertEqual(len(eligible), 1, "Cohort MUST be eligible after grace expiry")

    # ==================================================================
    # DEFECT B: Partial Cohort Eligibility
    # ==================================================================
    def test_defect_b_partial_cohort_excluded_from_full_hour_finalization(self) -> None:
        """TOUCHED_PARTIAL cohort must NOT undergo full-hour finalization generating 76 fake failures."""
        self._create_v3_journal("2026-09-17", "06", qualification="TOUCHED_PARTIAL")

        now_0715 = datetime(2026, 9, 17, 7, 15, 0, tzinfo=timezone.utc)
        res = orchestrate_closed_hour_archive(
            epoch=self.epoch,
            run_id=self.run_id,
            base_dir=self.base_dir,
            target_cohort=ArchiveCohortId("2026-09-17", "06"),
            grace_seconds=600,
            now=now_0715,
        )

        # Must NOT produce status="FAIL" with 76 failed slots
        self.assertNotEqual(res.get("status"), "FAIL", "TOUCHED_PARTIAL must not be marked as integrity FAIL")
        self.assertEqual(res.get("failed_count", 0), 0, "TOUCHED_PARTIAL must not record 76 fake slot failures")
        self.assertIn(res.get("status"), ("SKIPPED_NON_QUALIFYING", "INELIGIBLE_PARTIAL"))

        # Receipt must reflect non-qualifying skip, not failure
        receipt_path = self.receipt_root / "cohort_2026-09-17_06_finalized.json"
        self.assertTrue(receipt_path.exists())
        data = json.loads(receipt_path.read_text())
        self.assertIn(data.get("status"), ("SKIPPED_NON_QUALIFYING", "INELIGIBLE_PARTIAL"))
        self.assertEqual(data.get("failed_count", 0), 0)
        self.assertEqual(data.get("cohort_qualification"), "TOUCHED_PARTIAL")

    def test_defect_b_qualifying_full_hour_evaluates_normally(self) -> None:
        """QUALIFYING_FULL_HOUR cohort continues to receive normal strict 76-slot evaluation."""
        self._create_v3_journal("2026-09-17", "06", qualification="QUALIFYING_FULL_HOUR")

        now_0715 = datetime(2026, 9, 17, 7, 15, 0, tzinfo=timezone.utc)
        res = orchestrate_closed_hour_archive(
            epoch=self.epoch,
            run_id=self.run_id,
            base_dir=self.base_dir,
            target_cohort=ArchiveCohortId("2026-09-17", "06"),
            grace_seconds=600,
            now=now_0715,
        )
        self.assertEqual(res.get("total_slots"), 76)
        self.assertIn(res.get("status"), ("PASS", "FAIL"))

    def test_defect_b_unknown_qualification_fails_closed(self) -> None:
        """Unknown or ambiguous qualification fails closed."""
        self._create_v3_journal("2026-09-17", "06", qualification="UNKNOWN_GARBAGE")

        now_0715 = datetime(2026, 9, 17, 7, 15, 0, tzinfo=timezone.utc)
        with self.assertRaises((ValueError, RuntimeError)):
            orchestrate_closed_hour_archive(
                epoch=self.epoch,
                run_id=self.run_id,
                base_dir=self.base_dir,
                target_cohort=ArchiveCohortId("2026-09-17", "06"),
                grace_seconds=600,
                now=now_0715,
            )

    # ==================================================================
    # DEFECT C: Final Receipt Immutability
    # ==================================================================
    def test_defect_c_receipt_not_mutated_on_repeated_poll(self) -> None:
        """Once finalized, subsequent calls do not mutate the receipt or update finalized_at_utc."""
        self._create_v3_journal("2026-09-17", "06", qualification="TOUCHED_PARTIAL")

        t1 = datetime(2026, 9, 17, 7, 15, 0, tzinfo=timezone.utc)
        res1 = orchestrate_closed_hour_archive(
            epoch=self.epoch,
            run_id=self.run_id,
            base_dir=self.base_dir,
            target_cohort=ArchiveCohortId("2026-09-17", "06"),
            grace_seconds=600,
            now=t1,
        )
        receipt_path = self.receipt_root / "cohort_2026-09-17_06_finalized.json"
        content_first = receipt_path.read_bytes()
        first_payload = json.loads(content_first.decode("utf-8"))
        finalized_at_first = first_payload["finalized_at_utc"]

        # 30 seconds later: poll 2
        t2 = datetime(2026, 9, 17, 7, 15, 30, tzinfo=timezone.utc)
        res2 = orchestrate_closed_hour_archive(
            epoch=self.epoch,
            run_id=self.run_id,
            base_dir=self.base_dir,
            target_cohort=ArchiveCohortId("2026-09-17", "06"),
            grace_seconds=600,
            now=t2,
        )

        content_second = receipt_path.read_bytes()
        self.assertEqual(content_first, content_second, "Final receipt MUST be byte-identical across polls")
        second_payload = json.loads(content_second.decode("utf-8"))
        self.assertEqual(second_payload["finalized_at_utc"], finalized_at_first, "finalized_at_utc must NOT be mutated")

    def test_defect_c_identity_mismatch_fails_closed(self) -> None:
        """If existing receipt has mismatched identity (e.g. different cohort/run), fail closed."""
        receipt_path = self.receipt_root / "cohort_2026-09-17_06_finalized.json"
        receipt_path.write_text(
            json.dumps({"cohort": "WRONG_COHORT", "status": "PASS", "finalized_at_utc": "2026-09-17T07:15:00Z"}),
            encoding="utf-8",
        )
        self._create_v3_journal("2026-09-17", "06", qualification="TOUCHED_PARTIAL")

        t1 = datetime(2026, 9, 17, 7, 15, 0, tzinfo=timezone.utc)
        with self.assertRaises((ValueError, RuntimeError)):
            orchestrate_closed_hour_archive(
                epoch=self.epoch,
                run_id=self.run_id,
                base_dir=self.base_dir,
                target_cohort=ArchiveCohortId("2026-09-17", "06"),
                grace_seconds=600,
                now=t1,
            )

    def test_defect_c_corrupt_existing_receipt_fails_closed(self) -> None:
        """If existing receipt is corrupt json, fail closed rather than silently overwriting."""
        receipt_path = self.receipt_root / "cohort_2026-09-17_06_finalized.json"
        receipt_path.write_text("NOT_JSON_GARBAGE", encoding="utf-8")
        self._create_v3_journal("2026-09-17", "06", qualification="TOUCHED_PARTIAL")

        t1 = datetime(2026, 9, 17, 7, 15, 0, tzinfo=timezone.utc)
        with self.assertRaises((ValueError, RuntimeError)):
            orchestrate_closed_hour_archive(
                epoch=self.epoch,
                run_id=self.run_id,
                base_dir=self.base_dir,
                target_cohort=ArchiveCohortId("2026-09-17", "06"),
                grace_seconds=600,
                now=t1,
            )

    # ==================================================================
    # DEFECT D: Remote Durability of Failure Evidence
    # ==================================================================
    def test_defect_d_failure_produces_remote_evidence(self) -> None:
        """When an archive failure occurs, immutable remote failure evidence is emitted."""
        self._create_v3_journal("2026-09-17", "06", qualification="QUALIFYING_FULL_HOUR", event_count=10, writer_error_count=1)

        from io import BytesIO
        from contextlib import contextmanager
        from bithumb_coin_trader.pre_soak_archive import RemoteObject, _hex_to_base64
        mock_store = MagicMock()
        uploaded_files = {}
        def _mock_upload(local_path, key, sha):
            size = local_path.stat().st_size
            uploaded_files[key] = local_path.read_bytes()
            return RemoteObject(key=key, size=size, checksum_sha256_base64=_hex_to_base64(sha), version_id="v-001")

        @contextmanager
        def _mock_open_download(key):
            yield BytesIO(uploaded_files.get(key, b""))

        mock_store.upload = MagicMock(side_effect=_mock_upload)
        mock_store.open_download = MagicMock(side_effect=_mock_open_download)

        now_0715 = datetime(2026, 9, 17, 7, 15, 0, tzinfo=timezone.utc)
        with patch("scripts.orchestrate_closed_hour_archive.S3ArchiveStore", return_value=mock_store):
            res = orchestrate_closed_hour_archive(
                epoch=self.epoch,
                run_id=self.run_id,
                base_dir=self.base_dir,
                target_cohort=ArchiveCohortId("2026-09-17", "06"),
                grace_seconds=600,
                now=now_0715,
                store_type="s3",
                s3_bucket="test-bucket",
                allow_aws_write=True,
                remote_prefix=f"market-data/temporary/{self.epoch}",
            )

        self.assertEqual(res.get("status"), "FAIL")
        # Ensure remote failure evidence was pushed to store
        calls = mock_store.upload.call_args_list
        failure_keys = [c[0][1] for c in calls if "archive-failures" in c[0][1] or "cohort_2026-09-17_06_finalized" in c[0][1]]
        self.assertTrue(len(failure_keys) > 0, f"Expected remote failure evidence in S3 calls, got: {[c[0][1] for c in calls]}")



    def test_defect_d_success_does_not_produce_failure_evidence(self) -> None:
        """When an archive succeeds, NO failure evidence is emitted."""
        self._create_v3_journal("2026-09-17", "06", qualification="QUALIFYING_FULL_HOUR", event_count=0, writer_error_count=0)

        from io import BytesIO
        from contextlib import contextmanager
        from bithumb_coin_trader.pre_soak_archive import RemoteObject, _hex_to_base64
        mock_store = MagicMock()
        uploaded_files = {}

        def _mock_upload(local_path, key, sha):
            size = local_path.stat().st_size
            uploaded_files[key] = local_path.read_bytes()
            return RemoteObject(key=key, size=size, checksum_sha256_base64=_hex_to_base64(sha), version_id="v-001")

        @contextmanager
        def _mock_open_download(key):
            yield BytesIO(uploaded_files.get(key, b""))

        mock_store.upload = MagicMock(side_effect=_mock_upload)
        mock_store.open_download = MagicMock(side_effect=_mock_open_download)

        now_0715 = datetime(2026, 9, 17, 7, 15, 0, tzinfo=timezone.utc)
        with patch("scripts.orchestrate_closed_hour_archive.S3ArchiveStore", return_value=mock_store):
            res = orchestrate_closed_hour_archive(
                epoch=self.epoch,
                run_id=self.run_id,
                base_dir=self.base_dir,
                target_cohort=ArchiveCohortId("2026-09-17", "06"),
                grace_seconds=600,
                now=now_0715,
                store_type="s3",
                s3_bucket="test-bucket",
                allow_aws_write=True,
                remote_prefix=f"market-data/temporary/{self.epoch}",
            )

        self.assertEqual(res.get("status"), "PASS")
        calls = mock_store.upload.call_args_list
        failure_keys = [c[0][1] for c in calls if "archive-failures" in c[0][1]]
        self.assertEqual(len(failure_keys), 0, "Success must NOT emit archive-failures")

    def test_defect_d_remote_persistence_failure_is_reported_explicitly(self) -> None:
        """When S3 upload fails during failure evidence write, it is reported in archive_errors."""
        self._create_v3_journal("2026-09-17", "06", qualification="QUALIFYING_FULL_HOUR", event_count=10, writer_error_count=1)

        from io import BytesIO
        from contextlib import contextmanager
        from bithumb_coin_trader.pre_soak_archive import RemoteObject, _hex_to_base64
        mock_store = MagicMock()
        uploaded_files = {}

        def _mock_upload(local_path, key, sha):
            if "archive-failures" in key:
                raise RuntimeError("S3 PutObject Connection Timeout")
            size = local_path.stat().st_size
            uploaded_files[key] = local_path.read_bytes()
            return RemoteObject(key=key, size=size, checksum_sha256_base64=_hex_to_base64(sha), version_id="v-001")

        @contextmanager
        def _mock_open_download(key):
            yield BytesIO(uploaded_files.get(key, b""))

        mock_store.upload = MagicMock(side_effect=_mock_upload)
        mock_store.open_download = MagicMock(side_effect=_mock_open_download)

        now_0715 = datetime(2026, 9, 17, 7, 15, 0, tzinfo=timezone.utc)
        with patch("scripts.orchestrate_closed_hour_archive.S3ArchiveStore", return_value=mock_store):
            res = orchestrate_closed_hour_archive(
                epoch=self.epoch,
                run_id=self.run_id,
                base_dir=self.base_dir,
                target_cohort=ArchiveCohortId("2026-09-17", "06"),
                grace_seconds=600,
                now=now_0715,
                store_type="s3",
                s3_bucket="test-bucket",
                allow_aws_write=True,
                remote_prefix=f"market-data/temporary/{self.epoch}",
            )

        self.assertEqual(res.get("status"), "FAIL")
        # Ensure error is explicitly reported
        upload_errs = [e for e in res.get("archive_errors", []) if "FAILED_REMOTE_FAILURE_UPLOAD" in e]
        self.assertTrue(len(upload_errs) > 0, "S3 upload failure must be reported explicitly in archive_errors")

if __name__ == "__main__":
    unittest.main()
