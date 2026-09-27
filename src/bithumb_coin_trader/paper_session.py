"""Fail-closed finite local PAPER session over normalized public-event JSONL."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import json
import math
from pathlib import Path
from typing import Any, Iterator, Mapping

from .execution_simulator import OrderBookSnapshot
from .models import Candle
from .paper_journal import PaperEventJournal
from .paper_runtime import NormalizedPaperEvent, PaperRuntime, PaperRuntimeError
from .research_infra.costs import SpotCostScenario
from .risk_engine import RiskEngine, RiskEngineConfig


class PaperSessionError(ValueError):
    """Raised when local PAPER session inputs fail closed validation."""


def run_local_paper_session(
    *,
    candidate_freeze: Path,
    research_root: Path,
    warmup_csv: Path,
    events_jsonl: Path,
    cost_scenario_json: Path,
    risk_config_json: Path,
    journal_path: Path,
    initial_cash_krw: str,
    market: str,
    allow_resume: bool,
) -> Iterator[dict[str, Any]]:
    """Yield event results and final metrics; no exchange or cloud client."""
    events_path = _regular_file(events_jsonl, "normalized public event JSONL")
    event_count = sum(1 for _ in _iter_events(events_path))
    if event_count == 0:
        raise PaperSessionError("normalized public event stream must contain at least one event")
    runtime = create_local_paper_runtime(
        candidate_freeze=candidate_freeze,
        research_root=research_root,
        warmup_csv=warmup_csv,
        cost_scenario_json=cost_scenario_json,
        risk_config_json=risk_config_json,
        journal_path=journal_path,
        initial_cash_krw=initial_cash_krw,
        market=market,
        allow_resume=allow_resume,
    )
    last_event_id: str | None = None
    last_book: OrderBookSnapshot | None = None
    for event in _iter_events(events_path):
        result = runtime.process_event(event)
        yield {"record_type": "event_result", "result": result}
        last_event_id = event.event_id
        last_book = event.orderbooks[-1]
        if runtime.is_halted:
            break
    if last_event_id is not None and last_book is not None:
        yield {
            "record_type": "metrics_snapshot",
            "as_of_event_id": last_event_id,
            "metrics": runtime.metrics(last_book),
        }


def create_local_paper_runtime(
    *,
    candidate_freeze: Path,
    research_root: Path,
    warmup_csv: Path,
    cost_scenario_json: Path,
    risk_config_json: Path,
    journal_path: Path,
    initial_cash_krw: str,
    market: str,
    allow_resume: bool,
) -> PaperRuntime:
    """Validate local PAPER setup inputs, then construct the journal-backed runtime."""
    candidate_path = _regular_file(candidate_freeze, "candidate freeze")
    warmup_path = _regular_file(warmup_csv, "warmup candle CSV")
    costs_path = _regular_file(cost_scenario_json, "cost scenario")
    risk_path = _regular_file(risk_config_json, "risk configuration")
    journal_path = Path(journal_path)
    if journal_path.is_symlink():
        raise PaperSessionError("paper journal path must not be a symlink")
    if journal_path.exists() and not allow_resume:
        raise PaperSessionError("paper journal already exists; explicit --resume is required")
    if allow_resume and not journal_path.exists():
        raise PaperSessionError("--resume requires an existing paper journal")
    cash = Decimal(initial_cash_krw)
    if not cash.is_finite() or cash <= 0:
        raise PaperSessionError("initial cash must be a finite positive Decimal string")
    candidate = _read_object(candidate_path, "candidate freeze")
    scenario = SpotCostScenario.from_dict(_read_object(costs_path, "cost scenario"))
    risk_config = RiskEngineConfig(**_read_object(risk_path, "risk configuration"))
    from .data import load_candles_csv

    warmup = load_candles_csv(warmup_path)
    runtime = PaperRuntime.from_frozen_artifact(
        candidate_artifact=candidate,
        research_root=Path(research_root),
        warmup_candles=warmup,
        cost_scenario=scenario,
        accept_depth_partials=True,
        journal=PaperEventJournal(journal_path),
        risk_engine=RiskEngine(risk_config),
        initial_cash_krw=cash,
        market=market,
    )
    if runtime.is_halted:
        raise PaperSessionError(
            "durable PAPER halt is active; inspect state and acknowledge recovery explicitly before starting"
        )
    return runtime


def _iter_events(path: Path) -> Iterator[NormalizedPaperEvent]:
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PaperSessionError(f"invalid event JSON at {path}:{line_number}") from exc
            yield _parse_event(payload, path, line_number)


def _parse_event(payload: Any, path: Path, line_number: int) -> NormalizedPaperEvent:
    fields = {"event_id", "received_at_ms", "candle", "orderbooks"}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise PaperSessionError(f"normalized event fields are invalid at {path}:{line_number}")
    received = payload.get("received_at_ms")
    if isinstance(received, bool) or not isinstance(received, int) or received < 0:
        raise PaperSessionError(f"normalized event timestamp is invalid at {path}:{line_number}")
    raw_candle = payload.get("candle")
    candle: Candle | None = None
    if raw_candle is not None:
        if not isinstance(raw_candle, dict) or set(raw_candle) != {
            "timestamp", "open", "high", "low", "close", "volume", "market",
        }:
            raise PaperSessionError(f"normalized candle fields are invalid at {path}:{line_number}")
        timestamp = raw_candle.get("timestamp")
        if not isinstance(timestamp, str):
            raise PaperSessionError(f"normalized candle timestamp is invalid at {path}:{line_number}")
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("candle timestamp must have a timezone")
            market = raw_candle.get("market")
            if not isinstance(market, str) or not market.startswith("KRW-"):
                raise ValueError("candle market must be an explicit KRW spot market")
            candle = Candle(
                timestamp=parsed,
                open=_finite_number(raw_candle.get("open")),
                high=_finite_number(raw_candle.get("high")),
                low=_finite_number(raw_candle.get("low")),
                close=_finite_number(raw_candle.get("close")),
                volume=_finite_number(raw_candle.get("volume")),
                market=market,
            )
        except (ValueError, TypeError) as exc:
            raise PaperSessionError(f"normalized candle is invalid at {path}:{line_number}") from exc
    raw_books = payload.get("orderbooks")
    if not isinstance(raw_books, list) or not raw_books:
        raise PaperSessionError(f"normalized order-book list is empty at {path}:{line_number}")
    books: list[OrderBookSnapshot] = []
    for book in raw_books:
        if not isinstance(book, dict) or set(book) != {"timestamp_ms", "bids", "asks", "market"}:
            raise PaperSessionError(f"normalized order-book fields are invalid at {path}:{line_number}")
        timestamp_ms = book.get("timestamp_ms")
        if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or timestamp_ms < 0:
            raise PaperSessionError(f"order-book timestamp is invalid at {path}:{line_number}")
        bids = _levels(book.get("bids"), path, line_number)
        asks = _levels(book.get("asks"), path, line_number)
        market = book.get("market")
        if not isinstance(market, str) or not market.startswith("KRW-"):
            raise PaperSessionError(f"order-book market is invalid at {path}:{line_number}")
        try:
            books.append(OrderBookSnapshot(
                timestamp=timestamp_ms / 1000.0,
                bids=bids,
                asks=asks,
                market=market,
            ))
        except ValueError as exc:
            raise PaperSessionError(f"normalized order book is invalid at {path}:{line_number}") from exc
    event_id = payload.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        raise PaperSessionError(f"normalized event id is invalid at {path}:{line_number}")
    return NormalizedPaperEvent(event_id, received, candle, tuple(books))


def _levels(value: Any, path: Path, line_number: int) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, list) or not value:
        raise PaperSessionError(f"book levels are empty at {path}:{line_number}")
    levels: list[tuple[float, float]] = []
    for level in value:
        if not isinstance(level, list) or len(level) != 2:
            raise PaperSessionError(f"book level is malformed at {path}:{line_number}")
        levels.append((_finite_number(level[0]), _finite_number(level[1])))
    return tuple(levels)


def _finite_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PaperSessionError("normalized market values must be finite numbers")
    return float(value)


def _regular_file(path: Path, label: str) -> Path:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise PaperSessionError(f"{label} must be a regular non-symlink file: {candidate}")
    return candidate


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PaperSessionError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise PaperSessionError(f"{label} must contain a JSON object")
    return value
