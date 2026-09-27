from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bithumb_coin_trader.paper_feed_receipt import (
    PaperFeedReceiptError,
    PaperFeedReceiptWriter,
    verify_paper_feed_receipt,
)


_CANDIDATE_BINDING = {
    "candidate_id": "candidate-1",
    "experiment_id": "experiment-1",
    "freeze_hash": "a" * 64,
}
_SUBSCRIPTION = [
    {"ticket": "local-test"},
    {"type": "trade", "codes": ["KRW-BTC"], "is_only_realtime": True},
    {"type": "orderbook", "codes": ["KRW-BTC"], "is_only_realtime": True},
    {"format": "DEFAULT"},
]


def test_public_feed_receipt_binds_provider_candidate_frames_and_terminal_state(tmp_path: Path) -> None:
    path = tmp_path / "feed-session.jsonl"
    with PaperFeedReceiptWriter(
        path,
        session_id="session-1",
        market="KRW-BTC",
        candidate_binding=_CANDIDATE_BINDING,
        subscription=_SUBSCRIPTION,
        started_at_ms=100,
    ) as receipt:
        receipt.record({
            "record_type": "CONNECTION",
            "connected": True,
            "received_at_ms": 101,
        })
        receipt.record({
            "record_type": "OBSERVATION",
            "raw_payload_sha256": "b" * 64,
            "observation_type": "TradeObservation",
            "received_at_ms": 102,
            "validation_status": "ACCEPTED",
            "normalized_event_id": None,
            "source_observation_sha256": [],
        })
        receipt.finish(status="HALTED", reason_code="PUBLIC_FEED_DISCONNECTED", ended_at_ms=103)

    result = verify_paper_feed_receipt(path)
    assert result["complete"] is True
    assert result["provider"] == "BITHUMB_PUBLIC_WEBSOCKET_V1"
    assert result["candidate_id"] == "candidate-1"
    assert result["candidate_freeze_sha256"] == "a" * 64
    assert result["observation_count"] == 1
    assert result["terminal_status"] == "HALTED"
    assert len(result["subscription_sha256"]) == 64
    assert len(result["head_sha256"]) == 64


def test_incomplete_receipt_remains_open_after_process_interruption(tmp_path: Path) -> None:
    path = tmp_path / "interrupted-feed.jsonl"
    receipt = PaperFeedReceiptWriter(
        path,
        session_id="session-interrupted",
        market="KRW-BTC",
        candidate_binding=_CANDIDATE_BINDING,
        subscription=_SUBSCRIPTION,
        started_at_ms=100,
    )
    receipt.record({
        "record_type": "CONNECTION",
        "connected": True,
        "received_at_ms": 101,
    })
    os.close(receipt._fd)
    receipt._closed = True

    result = verify_paper_feed_receipt(path)
    assert result["complete"] is False
    assert result["terminal_status"] == "OPEN"
    assert result["record_count"] == 2


def test_receipt_chain_rejects_tampering_and_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "sealed-feed.jsonl"
    receipt = PaperFeedReceiptWriter(
        path,
        session_id="session-sealed",
        market="KRW-BTC",
        candidate_binding=_CANDIDATE_BINDING,
        subscription=_SUBSCRIPTION,
        started_at_ms=100,
    )
    receipt.finish(status="STOPPED", reason_code="REQUESTED_STOP", ended_at_ms=101)
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    records[0]["candidate_id"] = "altered"
    path.write_text(
        "".join(
            json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    with pytest.raises(PaperFeedReceiptError, match="chain integrity"):
        verify_paper_feed_receipt(path)

    with pytest.raises(PaperFeedReceiptError, match="must be new"):
        PaperFeedReceiptWriter(
            path,
            session_id="another-session",
            market="KRW-BTC",
            candidate_binding=_CANDIDATE_BINDING,
            subscription=_SUBSCRIPTION,
        )
