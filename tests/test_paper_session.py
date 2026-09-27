from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from bithumb_coin_trader.paper_session import PaperSessionError, _parse_event, run_local_paper_session


def _event() -> dict[str, Any]:
    return {
        "event_id": "public-event-1",
        "received_at_ms": 1_735_689_600_000,
        "candle": {
            "timestamp": "2025-01-01T00:00:00Z",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 2.0,
            "market": "KRW-BTC",
        },
        "orderbooks": [
            {
                "timestamp_ms": 1_735_689_600_000,
                "bids": [[100.0, 10.0]],
                "asks": [[101.0, 10.0]],
                "market": "KRW-BTC",
            }
        ],
    }


def test_parse_normalized_public_market_event() -> None:
    parsed = _parse_event(_event(), Path("events.jsonl"), 1)

    assert parsed.event_id == "public-event-1"
    assert parsed.candle is not None
    assert parsed.candle.market == "KRW-BTC"
    assert parsed.orderbooks[0].market == "KRW-BTC"
    assert parsed.orderbooks[0].timestamp == 1_735_689_600.0


@pytest.mark.parametrize(
    "change",
    [
        lambda event: event.pop("received_at_ms"),
        lambda event: event["orderbooks"].clear(),
        lambda event: event["candle"].update(close=float("nan")),
        lambda event: event["candle"].update(timestamp="2025-01-01T00:00:00"),
        lambda event: event["orderbooks"][0].update(market="BTC-USDT"),
    ],
)
def test_parse_rejects_ambiguous_or_invalid_normalized_events(
    change: Callable[[dict[str, Any]], None],
) -> None:
    event = _event()
    change(event)

    with pytest.raises(PaperSessionError):
        _parse_event(event, Path("events.jsonl"), 1)


def test_invalid_event_stream_fails_before_creating_a_paper_journal(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.json"
    candidate.write_text("{}", encoding="utf-8")
    warmup = tmp_path / "warmup.csv"
    warmup.write_text("timestamp,open,high,low,close,volume,market\n", encoding="utf-8")
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({"event_id": "missing-required-fields"}) + "\n", encoding="utf-8")
    costs = tmp_path / "costs.json"
    costs.write_text(
        json.dumps(
            {
                "name": "paper-conservative",
                "maker_fee_bps": 40,
                "taker_fee_bps": 40,
                "slippage_bps": 30,
                "latency_ms": 1000,
                "minimum_order_notional": 5000,
                "tick_size": 1,
                "lot_size": 0.00000001,
                "partial_fill_probability": None,
                "partial_fill_status": "UNSUPPORTED",
            }
        ),
        encoding="utf-8",
    )
    risk = tmp_path / "risk.json"
    risk.write_text("{}", encoding="utf-8")
    journal = tmp_path / "paper.sqlite"

    session = run_local_paper_session(
        candidate_freeze=candidate,
        research_root=tmp_path,
        warmup_csv=warmup,
        events_jsonl=events,
        cost_scenario_json=costs,
        risk_config_json=risk,
        journal_path=journal,
        initial_cash_krw="1000000",
        market="KRW-BTC",
        allow_resume=False,
    )

    with pytest.raises(PaperSessionError):
        next(session)
    assert not journal.exists()
