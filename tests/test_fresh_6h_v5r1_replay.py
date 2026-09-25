"""Deterministic regression tests reproducing Fresh 6H-v5r1 defects.

Defect 1:
When a Bithumb WebSocket connection reconnected, Bithumb emitted an initial
SNAPSHOT message for each market containing the latest trade with
stream_type="SNAPSHOT". The primary socket had already recorded this trade with
stream_type="REALTIME". Because stream_type was not excluded in
BITHUMB_TRADE_NON_SEMANTIC_FIELDS, the deduplication filter flagged all 20
markets as CONFLICT, resulting in BITHUMB_CONFLICTING_DUPLICATE.

Defect 2:
In orchestrate_closed_hour_archive.py, cohort_{cohort}_finalized.json was only
uploaded to the remote archive store when status == "FAIL". When status == "PASS",
the finalized receipt was written locally but omitted from remote store upload.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest

from bithumb_coin_trader.bithumb_redundancy import (
    BITHUMB_TRADE_NON_SEMANTIC_FIELDS,
    BithumbRedundancyFilter,
)


def _bithumb_trade(
    sequential_id: int,
    envelope_timestamp: int,
    stream_type: str = "REALTIME",
    price: int = 100_000_000,
    volume: str = "0.01",
) -> dict[str, Any]:
    return {
        "type": "trade",
        "code": "KRW-BTC",
        "trade_price": price,
        "trade_volume": volume,
        "ask_bid": "BID",
        "prev_closing_price": 99_000_000,
        "change": "RISE",
        "change_price": 1_000_000,
        "trade_date": "2026-09-23",
        "trade_time": "14:39:25",
        "trade_timestamp": 1_790_141_965_516,
        "sequential_id": sequential_id,
        "timestamp": envelope_timestamp,
        "stream_type": stream_type,
    }


def test_v5r1_trade_snapshot_vs_realtime_is_duplicate_not_conflict() -> None:
    cache = BithumbRedundancyFilter()
    seq_id = 1_080_236_804_948_399_783

    # Primary connection receives realtime trade frame
    realtime_trade = _bithumb_trade(
        sequential_id=seq_id,
        envelope_timestamp=1_790_141_965_746,
        stream_type="REALTIME",
    )
    dec1 = cache.observe("trade", "KRW-BTC", realtime_trade, "primary", 100.0)
    assert dec1.disposition == "canonical"

    # Secondary connection reconnects and receives initial snapshot trade frame
    snapshot_trade = _bithumb_trade(
        sequential_id=seq_id,
        envelope_timestamp=1_790_141_965_744,
        stream_type="SNAPSHOT",
    )
    dec2 = cache.observe("trade", "KRW-BTC", snapshot_trade, "secondary", 100.1)

    # In v5r1 unpatched code, dec2.disposition == "conflict" (RED)
    # The patch must ensure it is classified as "duplicate"
    assert dec2.disposition == "duplicate"
    assert dec2.equivalence in ("trade_timestamp_ignored", "trade_transport_ignored")


def test_v5r1_semantic_trade_differences_remain_conflict() -> None:
    """True semantic differences (price, volume, side) must still fail-closed as conflict."""
    cache = BithumbRedundancyFilter()
    seq_id = 1_080_236_804_948_399_784

    first = _bithumb_trade(seq_id, 1_790_141_965_000, "REALTIME", price=100_000_000)
    assert cache.observe("trade", "KRW-BTC", first, "primary", 1.0).disposition == "canonical"

    # Price difference with same sequential_id is a real data conflict
    diff_price = _bithumb_trade(seq_id, 1_790_141_965_001, "SNAPSHOT", price=100_000_500)
    dec_price = cache.observe("trade", "KRW-BTC", diff_price, "secondary", 1.1)
    assert dec_price.disposition == "conflict"

    # Volume difference with same sequential_id is a real data conflict
    diff_volume = _bithumb_trade(seq_id, 1_790_141_965_002, "REALTIME", volume="0.05")
    dec_volume = cache.observe("trade", "KRW-BTC", diff_volume, "secondary", 1.2)
    assert dec_volume.disposition == "conflict"


def test_v5r1_non_semantic_fields_contains_stream_type() -> None:
    """BITHUMB_TRADE_NON_SEMANTIC_FIELDS must contain both timestamp and stream_type."""
    assert "stream_type" in BITHUMB_TRADE_NON_SEMANTIC_FIELDS
    assert "timestamp" in BITHUMB_TRADE_NON_SEMANTIC_FIELDS


def test_v5r1_pass_receipt_uploaded_to_archive_store(tmp_path: Path) -> None:
    """Verify that when a cohort PASSES, its finalized receipt is uploaded to S3/store."""
    from contextlib import contextmanager
    from datetime import datetime, timezone
    from io import BytesIO
    from unittest.mock import MagicMock, patch
    from bithumb_coin_trader.archive_cohort import ArchiveCohortId
    from bithumb_coin_trader.pre_soak_archive import RemoteObject, _hex_to_base64
    from scripts.orchestrate_closed_hour_archive import orchestrate_closed_hour_archive

    epoch = "test-v5r1-epoch"
    run_id = "test-v5r1-run"
    base_dir = tmp_path / "base"
    base_dir.mkdir(parents=True)
    raw_dir = base_dir / "raw"
    raw_dir.mkdir(parents=True)
    manifest_dir = base_dir / "manifests"
    manifest_dir.mkdir(parents=True)
    journals_dir = base_dir / "coverage" / "journals"
    journals_dir.mkdir(parents=True)

    # Create valid journal for passing cohort using SEALED_FEED_UNIVERSE
    from bithumb_coin_trader.closed_hour_finalizer import SEALED_FEED_UNIVERSE
    from bithumb_coin_trader.feed_hour_coverage import (
        FrozenFeedHourObservation,
        SessionSegment,
        WriterHealthSnapshot,
        save_frozen_journal,
    )
    from datetime import timedelta

    dt_start = datetime.fromisoformat("2026-09-23T01:00:00+00:00")
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
            connected_at_utc="2026-09-23T00:00:00Z",
            disconnected_at_utc=None,
            requested_feeds=(feed.canonical,),
            requested_subscription_sha256="req-hash",
            confirmation_method="LIST_SUBSCRIPTIONS",
            confirmed_at_utc="2026-09-23T00:01:00Z",
            confirmed_feeds=(feed.canonical,),
            confirmed_subscription_sha256="conf-hash",
            response_evidence_sha256="resp-hash",
            heartbeat_observations_utc=tuple(hb_list),
            maximum_heartbeat_gap_seconds=10.0,
            disconnect_reason=None,
            reconnect_successor_id=None,
            collector_epoch=epoch,
            collector_run_id=run_id,
        )
        obs = FrozenFeedHourObservation(
            feed=feed,
            cohort_utc="2026-09-23_01",
            interval_start_utc=interval_start,
            interval_end_utc=interval_end,
            cohort_qualification="QUALIFYING_FULL_HOUR",
            observation_start_utc=interval_start,
            observation_end_utc=interval_end,
            event_count=0,
            first_event_timestamp=None,
            last_event_timestamp=None,
            session_segments=(seg,),
            disconnect_count=0,
            reconnect_count=0,
            health=WriterHealthSnapshot(writer_error_count=0),
        )
        observations.append(obs)

    save_frozen_journal(observations, journals_dir)

    mock_store = MagicMock()
    uploaded_files: dict[str, bytes] = {}

    def _mock_upload(local_path: Path, key: str, sha: str) -> RemoteObject:
        size = local_path.stat().st_size
        uploaded_files[key] = local_path.read_bytes()
        return RemoteObject(
            key=key,
            size=size,
            checksum_sha256_base64=_hex_to_base64(sha),
            version_id="v-001",
        )

    @contextmanager
    def _mock_open_download(key: str):
        yield BytesIO(uploaded_files.get(key, b""))

    mock_store.upload = MagicMock(side_effect=_mock_upload)
    mock_store.open_download = MagicMock(side_effect=_mock_open_download)

    now_0215 = datetime(2026, 9, 23, 2, 15, 0, tzinfo=timezone.utc)
    with patch("scripts.orchestrate_closed_hour_archive.S3ArchiveStore", return_value=mock_store):
        res = orchestrate_closed_hour_archive(
            epoch=epoch,
            run_id=run_id,
            base_dir=base_dir,
            target_cohort=ArchiveCohortId("2026-09-23", "01"),
            grace_seconds=600,
            now=now_0215,
            store_type="s3",
            s3_bucket="test-bucket",
            allow_aws_write=True,
            remote_prefix=f"market-data/temporary/{epoch}",
        )

    assert res.get("status") == "PASS"
    calls = mock_store.upload.call_args_list
    receipt_keys = [c[0][1] for c in calls if "archive-receipts" in c[0][1] and "cohort_2026-09-23_01_finalized" in c[0][1]]
    assert len(receipt_keys) == 1, f"Expected finalized PASS receipt to be uploaded to store, got calls: {[c[0][1] for c in calls]}"

