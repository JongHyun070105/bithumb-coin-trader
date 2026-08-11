"""Persistent, deterministic paper execution for Bithumb spot candles."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from .models import Candle, Signal


STATE_VERSION = 2
PENDING_VERSION = 1
INITIAL_CASH_KRW = Decimal("20000")
MAX_ORDER_KRW = Decimal("10000")
MIN_ORDER_KRW = Decimal("5000")
CASH_RESERVE_KRW = Decimal("5000")
FEE_RATE = Decimal("0.0025")
SLIPPAGE_RATE = Decimal("0.0005")
KST = ZoneInfo("Asia/Seoul")


class PaperError(ValueError):
    """Raised when paper execution cannot proceed without guessing."""


def _decimal(
    value: str,
    field: str,
    *,
    positive: bool = False,
    allow_negative: bool = False,
) -> Decimal:
    if not isinstance(value, str):
        raise PaperError(f"{field} must be an exact decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise PaperError(f"{field} must be a valid decimal") from exc
    if not parsed.is_finite() or (not allow_negative and parsed < 0) or (positive and parsed <= 0):
        qualifier = "positive" if positive else "non-negative"
        raise PaperError(f"{field} must be finite and {qualifier}")
    return parsed


def _timestamp(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PaperError(f"{field} must be an ISO timestamp string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PaperError(f"{field} must be a valid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PaperError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _exact(value: Decimal) -> str:
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PaperState:
    version: int = STATE_VERSION
    market: str = "KRW-BTC"
    cash_krw: str = "20000"
    position: str = "flat"
    strategy_position: str = "flat"
    quantity: str = "0"
    cost_basis_krw: str = "0"
    realized_pnl_krw: str = "0"
    last_decision_at: str | None = None
    last_execution_at: str | None = None
    daily_entry_date: str | None = None
    daily_entries: int = 0
    decision_count: int = 0

    def __post_init__(self) -> None:
        if self.version != STATE_VERSION:
            raise PaperError("unsupported paper state version")
        if not self.market.startswith("KRW-"):
            raise PaperError("paper state requires a KRW market")
        cash = _decimal(self.cash_krw, "cash_krw")
        quantity = _decimal(self.quantity, "quantity")
        cost_basis = _decimal(self.cost_basis_krw, "cost_basis_krw")
        _decimal(self.realized_pnl_krw, "realized_pnl_krw", allow_negative=True)
        if self.position not in {"flat", "long"}:
            raise PaperError("paper position must be flat or long")
        if self.strategy_position not in {"flat", "long"}:
            raise PaperError("paper strategy_position must be flat or long")
        if self.position == "flat" and (quantity != 0 or cost_basis != 0):
            raise PaperError("flat paper state must have zero quantity and cost basis")
        if self.position == "long" and (quantity <= 0 or cost_basis <= 0):
            raise PaperError("long paper state requires positive quantity and cost basis")
        if cash < 0:
            raise PaperError("paper cash cannot be negative")
        decision_at = _timestamp(self.last_decision_at, "last_decision_at")
        execution_at = _timestamp(self.last_execution_at, "last_execution_at")
        if (decision_at is None) != (execution_at is None):
            raise PaperError("paper decision and execution timestamps must be set together")
        if decision_at is not None and execution_at != decision_at + timedelta(days=1):
            raise PaperError("paper execution timestamp must follow its decision by one day")
        if self.daily_entry_date is not None:
            try:
                datetime.strptime(self.daily_entry_date, "%Y-%m-%d")
            except ValueError as exc:
                raise PaperError("daily_entry_date must be YYYY-MM-DD") from exc
        if isinstance(self.daily_entries, bool) or not isinstance(self.daily_entries, int):
            raise PaperError("daily_entries must be an integer")
        if self.daily_entries not in {0, 1}:
            raise PaperError("daily_entries must be zero or one")
        if self.daily_entries and self.daily_entry_date is None:
            raise PaperError("daily entries require a daily_entry_date")
        if isinstance(self.decision_count, bool) or not isinstance(self.decision_count, int):
            raise PaperError("decision_count must be an integer")
        if self.decision_count < 0:
            raise PaperError("decision_count cannot be negative")


@dataclass(frozen=True, slots=True)
class PaperResult:
    processed: bool
    action: str
    state: PaperState
    decision_at: str
    execution_at: str
    execution_price: str | None
    fee_krw: str
    realized_pnl_krw: str
    trade_realized_pnl_krw: str
    unrealized_pnl_krw: str
    equity_krw: str


@dataclass(frozen=True, slots=True)
class AuditEvidence:
    record_count: int
    decision_count: int
    buy_count: int
    sell_count: int
    hold_count: int
    round_trip_count: int
    winning_round_trips: int
    losing_round_trips: int
    realized_pnl_krw: str
    total_fees_krw: str
    first_decision_at: str | None
    last_decision_at: str | None
    final_state: PaperState


def load_paper_state(path: str | Path) -> PaperState:
    source = Path(path)
    if not source.exists():
        return PaperState()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperError("paper state is unreadable") from exc
    if not isinstance(payload, dict):
        raise PaperError("paper state must be a JSON object")
    allowed = set(PaperState.__dataclass_fields__)
    if set(payload) != allowed:
        raise PaperError("paper state fields do not match the current schema")
    try:
        return PaperState(**payload)
    except TypeError as exc:
        raise PaperError("paper state has invalid field types") from exc


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(_canonical_json(payload))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def save_paper_state(path: str | Path, state: PaperState) -> None:
    destination = Path(path)
    _atomic_json(destination, asdict(state))


def _validate_candles(candles: Sequence[Candle]) -> None:
    if len(candles) < 2:
        raise PaperError("at least two completed daily candles are required")
    market = candles[0].market
    if not market.startswith("KRW-") or any(candle.market != market for candle in candles):
        raise PaperError("paper candles must use one KRW market")
    if any(
        candle.timestamp.astimezone(KST).time().replace(tzinfo=None)
        != datetime.min.time()
        for candle in candles
    ):
        raise PaperError("paper daily candles must start at KST midnight")
    for previous, current in zip(candles, candles[1:]):
        if current.timestamp <= previous.timestamp:
            raise PaperError("paper candles contain duplicate or non-chronological timestamps")
        if current.timestamp.astimezone(timezone.utc) - previous.timestamp.astimezone(timezone.utc) != timedelta(days=1):
            raise PaperError("paper candles contain a daily gap")


def _append_audit(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = _canonical_json(record) + "\n"
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        remaining = memoryview(line.encode("utf-8"))
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("audit append made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _buy_terms(cash: Decimal) -> tuple[Decimal, Decimal]:
    available_total = cash - CASH_RESERVE_KRW
    capped_total = MAX_ORDER_KRW * (Decimal("1") + FEE_RATE)
    if available_total >= capped_total:
        notional = MAX_ORDER_KRW
        return notional, notional * FEE_RATE
    notional = available_total / (Decimal("1") + FEE_RATE)
    return notional, available_total - notional


def _audit_records(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PaperError("paper audit is unreadable") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        records.append(_decode_audit_record(line, line_number))
    return records


def _decode_audit_record(line: str, line_number: int | str) -> dict[str, Any]:
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        raise PaperError(f"paper audit line {line_number} is invalid JSON") from exc
    if not isinstance(record, dict):
        raise PaperError(f"paper audit line {line_number} must be an object")
    canonical_sha256 = record.get("canonical_sha256")
    unsigned = {key: value for key, value in record.items() if key != "canonical_sha256"}
    if not isinstance(canonical_sha256, str) or canonical_sha256 != _canonical_sha256(unsigned):
        raise PaperError(f"paper audit line {line_number} has an invalid canonical hash")
    return record


def _repair_pending_audit_tail(path: Path, pending_sha256: str) -> None:
    """Repair only an unterminated final record while a verified WAL exists."""

    if not path.exists():
        return
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise PaperError("paper audit is unreadable") from exc
    if not payload or payload.endswith(b"\n"):
        return

    last_newline = payload.rfind(b"\n")
    prefix_end = last_newline + 1
    prefix = payload[:prefix_end]
    tail = payload[prefix_end:]
    try:
        prefix_text = prefix.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PaperError("paper audit history is not valid UTF-8") from exc
    for line_number, line in enumerate(prefix_text.splitlines(), start=1):
        _decode_audit_record(line, line_number)

    complete_tail: dict[str, Any] | None = None
    try:
        tail_text = tail.decode("utf-8")
        complete_tail = _decode_audit_record(tail_text, "tail")
    except (UnicodeDecodeError, PaperError):
        complete_tail = None

    if complete_tail is not None:
        if complete_tail.get("canonical_sha256") != pending_sha256:
            raise PaperError("paper audit has an unexpected complete tail record")
        descriptor = os.open(path, os.O_APPEND | os.O_WRONLY)
        try:
            os.write(descriptor, b"\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return

    descriptor = os.open(path, os.O_WRONLY)
    try:
        os.ftruncate(descriptor, prefix_end)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def audit_evidence(audit_path: str | Path) -> AuditEvidence:
    """Replay and verify the append-only audit without mutating any file."""

    state = PaperState()
    records = _audit_records(audit_path)
    buy_count = sell_count = hold_count = 0
    winning_round_trips = losing_round_trips = 0
    total_fees = Decimal("0")
    verified_realized = Decimal("0")
    first_decision_at: str | None = None

    required = {
        "action",
        "decision_at",
        "execution_at",
        "execution_price",
        "fee_krw",
        "realized_pnl_krw",
        "trade_realized_pnl_krw",
        "unrealized_pnl_krw",
        "equity_krw",
        "decision_count",
        "mark_price_krw",
        "requested_signal",
        "state_after",
        "canonical_sha256",
    }
    for index, record in enumerate(records, start=1):
        if set(record) != required:
            raise PaperError(f"paper audit record {index} has an invalid schema")
        state_payload = record["state_after"]
        if not isinstance(state_payload, dict):
            raise PaperError(f"paper audit record {index} state_after must be an object")
        try:
            after = PaperState(**state_payload)
        except TypeError as exc:
            raise PaperError(f"paper audit record {index} state_after is invalid") from exc
        requested = record["requested_signal"]
        action = record["action"]
        if requested not in {"flat", "long"} or after.strategy_position != requested:
            raise PaperError(f"paper audit record {index} has an invalid requested signal")
        if action not in {"buy", "sell", "hold"}:
            raise PaperError(f"paper audit record {index} has an invalid action")
        if after.market != state.market or after.decision_count != state.decision_count + 1:
            raise PaperError(f"paper audit record {index} breaks the state chain")
        if record["decision_count"] != after.decision_count:
            raise PaperError(f"paper audit record {index} has an invalid decision count")
        if record["decision_at"] != after.last_decision_at or record["execution_at"] != after.last_execution_at:
            raise PaperError(f"paper audit record {index} has mismatched timestamps")
        prior_decision = _timestamp(state.last_decision_at, "last_decision_at")
        after_decision = _timestamp(after.last_decision_at, "last_decision_at")
        if prior_decision is not None and after_decision != prior_decision + timedelta(days=1):
            raise PaperError(f"paper audit record {index} skips a decision day")
        if first_decision_at is None:
            first_decision_at = after.last_decision_at

        before_cash = _decimal(state.cash_krw, "cash_krw")
        before_quantity = _decimal(state.quantity, "quantity")
        before_cost = _decimal(state.cost_basis_krw, "cost_basis_krw")
        before_realized = _decimal(state.realized_pnl_krw, "realized_pnl_krw", allow_negative=True)
        after_cash = _decimal(after.cash_krw, "cash_krw")
        after_quantity = _decimal(after.quantity, "quantity")
        after_cost = _decimal(after.cost_basis_krw, "cost_basis_krw")
        after_realized = _decimal(after.realized_pnl_krw, "realized_pnl_krw", allow_negative=True)
        fee = _decimal(record["fee_krw"], "fee_krw")
        trade_realized = _decimal(
            record["trade_realized_pnl_krw"],
            "trade_realized_pnl_krw",
            allow_negative=True,
        )
        mark_price = _decimal(record["mark_price_krw"], "mark_price_krw", positive=True)
        execution_price_raw = record["execution_price"]
        execution_price = (
            None
            if execution_price_raw is None
            else _decimal(execution_price_raw, "execution_price", positive=True)
        )
        execution_at = _timestamp(after.last_execution_at, "last_execution_at")
        if execution_at is None:
            raise PaperError(f"paper audit record {index} is missing execution time")
        daily_date = execution_at.astimezone(KST).date().isoformat()
        prior_daily_entries = state.daily_entries if state.daily_entry_date == daily_date else 0
        available_notional, _ = _buy_terms(before_cash)
        can_buy = available_notional >= MIN_ORDER_KRW and prior_daily_entries == 0
        if state.position == "flat" and requested == "long" and can_buy:
            expected_action = "buy"
        elif state.position == "long" and requested == "flat":
            expected_action = "sell"
        else:
            expected_action = "hold"
        if action != expected_action:
            raise PaperError(f"paper audit record {index} has an impossible action")
        expected_daily_entries = prior_daily_entries + int(action == "buy")
        if after.daily_entry_date != daily_date or after.daily_entries != expected_daily_entries:
            raise PaperError(f"paper audit record {index} has invalid daily entry accounting")

        if action == "buy":
            expected_notional, expected_fee = _buy_terms(before_cash)
            if state.position != "flat" or requested != "long" or execution_price is None:
                raise PaperError(f"paper audit record {index} has an invalid buy transition")
            if expected_notional < MIN_ORDER_KRW or fee != expected_fee:
                raise PaperError(f"paper audit record {index} has invalid buy constraints")
            if after_cash != before_cash - expected_notional - fee:
                raise PaperError(f"paper audit record {index} has invalid buy cash")
            if after_quantity != expected_notional / execution_price or after_cost != expected_notional + fee:
                raise PaperError(f"paper audit record {index} has invalid buy position accounting")
            if after.position != "long" or after_realized != before_realized or trade_realized != 0:
                raise PaperError(f"paper audit record {index} has invalid buy state")
            if after_cash < CASH_RESERVE_KRW:
                raise PaperError(f"paper audit record {index} violates the cash reserve")
            buy_count += 1
        elif action == "sell":
            if state.position != "long" or requested != "flat" or execution_price is None:
                raise PaperError(f"paper audit record {index} has an invalid sell transition")
            gross = before_quantity * execution_price
            expected_fee = gross * FEE_RATE
            expected_trade_realized = gross - expected_fee - before_cost
            if fee != expected_fee or trade_realized != expected_trade_realized:
                raise PaperError(f"paper audit record {index} has invalid sell fees or PnL")
            if after_cash != before_cash + gross - fee:
                raise PaperError(f"paper audit record {index} has invalid sell cash")
            if after.position != "flat" or after_quantity != 0 or after_cost != 0:
                raise PaperError(f"paper audit record {index} has invalid sell position")
            if after_realized != before_realized + trade_realized:
                raise PaperError(f"paper audit record {index} has invalid cumulative realized PnL")
            verified_realized += trade_realized
            winning_round_trips += int(trade_realized > 0)
            losing_round_trips += int(trade_realized < 0)
            sell_count += 1
        else:
            if execution_price is not None or fee != 0 or trade_realized != 0:
                raise PaperError(f"paper audit record {index} has invalid hold execution values")
            unchanged = (
                after_cash == before_cash
                and after_quantity == before_quantity
                and after_cost == before_cost
                and after_realized == before_realized
                and after.position == state.position
            )
            if not unchanged:
                raise PaperError(f"paper audit record {index} has an invalid hold transition")
            hold_count += 1

        expected_unrealized = after_quantity * mark_price - after_cost if after_quantity else Decimal("0")
        expected_equity = after_cash + after_quantity * mark_price
        if _decimal(record["unrealized_pnl_krw"], "unrealized_pnl_krw", allow_negative=True) != expected_unrealized:
            raise PaperError(f"paper audit record {index} has invalid unrealized PnL")
        if _decimal(record["equity_krw"], "equity_krw", allow_negative=True) != expected_equity:
            raise PaperError(f"paper audit record {index} has invalid equity")
        if _decimal(record["realized_pnl_krw"], "realized_pnl_krw", allow_negative=True) != after_realized:
            raise PaperError(f"paper audit record {index} has invalid realized PnL")
        total_fees += fee
        state = after

    if _decimal(state.realized_pnl_krw, "realized_pnl_krw", allow_negative=True) != verified_realized:
        raise PaperError("paper audit final realized PnL does not match verified sells")

    return AuditEvidence(
        record_count=len(records),
        decision_count=state.decision_count,
        buy_count=buy_count,
        sell_count=sell_count,
        hold_count=hold_count,
        round_trip_count=sell_count,
        winning_round_trips=winning_round_trips,
        losing_round_trips=losing_round_trips,
        realized_pnl_krw=_exact(verified_realized),
        total_fees_krw=_exact(total_fees),
        first_decision_at=first_decision_at,
        last_decision_at=state.last_decision_at,
        final_state=state,
    )


def verify_audit(audit_path: str | Path, state_path: str | Path) -> AuditEvidence:
    """Verify the audit replay and require it to equal the persisted state."""

    evidence = audit_evidence(audit_path)
    if evidence.final_state != load_paper_state(state_path):
        raise PaperError("paper audit final state does not match persisted state")
    return evidence


class PaperEngine:
    """Execute one persisted long/flat paper decision per completed day."""

    def __init__(self, state_path: str | Path, audit_path: str | Path) -> None:
        self.state_path = Path(state_path)
        self.audit_path = Path(audit_path)
        self.pending_path = self.state_path.with_name(f"{self.state_path.name}.pending")

    def _audit_contains(self, canonical_sha256: str) -> bool:
        for record in _audit_records(self.audit_path):
            if record.get("canonical_sha256") == canonical_sha256:
                return True
        return False

    def _recover_pending(self) -> None:
        if not self.pending_path.exists():
            return
        try:
            payload = json.loads(self.pending_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PaperError("paper pending transaction is unreadable") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "version",
            "state_after",
            "audit_record",
        }:
            raise PaperError("paper pending transaction has an invalid schema")
        if payload["version"] != PENDING_VERSION:
            raise PaperError("unsupported paper pending transaction version")
        state_payload = payload["state_after"]
        audit_record = payload["audit_record"]
        if not isinstance(state_payload, dict) or not isinstance(audit_record, dict):
            raise PaperError("paper pending transaction payload is invalid")
        try:
            state = PaperState(**state_payload)
        except TypeError as exc:
            raise PaperError("paper pending state has invalid field types") from exc
        if audit_record.get("state_after") != asdict(state):
            raise PaperError("paper pending audit does not match state_after")
        canonical_sha256 = audit_record.get("canonical_sha256")
        unsigned = {key: value for key, value in audit_record.items() if key != "canonical_sha256"}
        if not isinstance(canonical_sha256, str) or canonical_sha256 != _canonical_sha256(unsigned):
            raise PaperError("paper pending audit hash is invalid")

        _repair_pending_audit_tail(self.audit_path, canonical_sha256)
        save_paper_state(self.state_path, state)
        if not self._audit_contains(canonical_sha256):
            _append_audit(self.audit_path, audit_record)
        self.pending_path.unlink()
        _fsync_directory(self.pending_path.parent)

    def process(
        self,
        candles: Sequence[Candle],
        signals: Sequence[Signal],
        *,
        as_of: datetime | None = None,
    ) -> PaperResult:
        self._recover_pending()
        _validate_candles(candles)
        if len(signals) != len(candles):
            raise PaperError("signals and candles must have the same length")
        try:
            requested = Signal(signals[-2])
        except (TypeError, ValueError) as exc:
            raise PaperError("penultimate signal is invalid") from exc
        if requested is Signal.SHORT:
            raise PaperError("paper execution supports long and flat signals only")
        requested_position = "long" if requested is Signal.LONG else "flat"

        decision_at = candles[-2].timestamp.astimezone(timezone.utc)
        execution_at = candles[-1].timestamp.astimezone(timezone.utc)
        observed_at = as_of or datetime.now(timezone.utc)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise PaperError("as_of must be timezone-aware")
        if execution_at + timedelta(days=1) > observed_at.astimezone(timezone.utc):
            raise PaperError("latest daily candle is not completed")
        state = load_paper_state(self.state_path)
        if state.market != candles[-1].market:
            raise PaperError("paper state market does not match candle market")
        prior_decision = _timestamp(state.last_decision_at, "last_decision_at")
        if prior_decision == decision_at:
            return self._result(False, "already_processed", state, candles[-1], None, Decimal("0"))
        if prior_decision is not None:
            if prior_decision > decision_at:
                raise PaperError("paper state is newer than the supplied candles")
            if decision_at != prior_decision + timedelta(days=1):
                raise PaperError("paper state is stale; missing decisions must not be skipped")
        cash = _decimal(state.cash_krw, "cash_krw")
        quantity = _decimal(state.quantity, "quantity")
        cost_basis = _decimal(state.cost_basis_krw, "cost_basis_krw")
        cumulative_realized = _decimal(
            state.realized_pnl_krw,
            "realized_pnl_krw",
            allow_negative=True,
        )
        daily_date = execution_at.astimezone(KST).date().isoformat()
        daily_entries = state.daily_entries if state.daily_entry_date == daily_date else 0
        action = "hold"
        execution_price: Decimal | None = None
        fee = Decimal("0")
        realized = Decimal("0")

        if requested is Signal.LONG and state.position == "flat":
            notional, fee = _buy_terms(cash)
            if notional >= MIN_ORDER_KRW and daily_entries == 0:
                execution_price = Decimal(str(candles[-1].open)) * (Decimal("1") + SLIPPAGE_RATE)
                total_debit = notional + fee
                if total_debit > cash:
                    raise PaperError("paper buy would make cash negative")
                cash -= total_debit
                quantity = notional / execution_price
                cost_basis = total_debit
                daily_entries = 1
                action = "buy"
        elif requested is Signal.FLAT and state.position == "long":
            execution_price = Decimal(str(candles[-1].open)) * (Decimal("1") - SLIPPAGE_RATE)
            gross = quantity * execution_price
            fee = gross * FEE_RATE
            proceeds = gross - fee
            realized = proceeds - cost_basis
            cumulative_realized += realized
            cash += proceeds
            quantity = Decimal("0")
            cost_basis = Decimal("0")
            action = "sell"

        position = "long" if quantity > 0 else "flat"
        next_state = PaperState(
            market=state.market,
            cash_krw=_exact(cash),
            position=position,
            strategy_position=requested_position,
            quantity=_exact(quantity),
            cost_basis_krw=_exact(cost_basis),
            realized_pnl_krw=_exact(cumulative_realized),
            last_decision_at=decision_at.isoformat(),
            last_execution_at=execution_at.isoformat(),
            daily_entry_date=daily_date,
            daily_entries=daily_entries,
            decision_count=state.decision_count + 1,
        )
        result = self._result(True, action, next_state, candles[-1], execution_price, fee, realized)
        audit_record = {
            "action": action,
            "decision_at": result.decision_at,
            "execution_at": result.execution_at,
            "execution_price": result.execution_price,
            "fee_krw": result.fee_krw,
            "realized_pnl_krw": result.realized_pnl_krw,
            "trade_realized_pnl_krw": result.trade_realized_pnl_krw,
            "unrealized_pnl_krw": result.unrealized_pnl_krw,
            "equity_krw": result.equity_krw,
            "decision_count": next_state.decision_count,
            "mark_price_krw": _exact(Decimal(str(candles[-1].close))),
            "requested_signal": requested_position,
            "state_after": asdict(next_state),
        }
        audit_record["canonical_sha256"] = _canonical_sha256(audit_record)
        _atomic_json(
            self.pending_path,
            {
                "version": PENDING_VERSION,
                "state_after": asdict(next_state),
                "audit_record": audit_record,
            },
        )
        save_paper_state(self.state_path, next_state)
        if not self._audit_contains(audit_record["canonical_sha256"]):
            _append_audit(self.audit_path, audit_record)
        self.pending_path.unlink()
        _fsync_directory(self.pending_path.parent)
        return result

    @staticmethod
    def _result(
        processed: bool,
        action: str,
        state: PaperState,
        mark_candle: Candle,
        execution_price: Decimal | None,
        fee: Decimal,
        realized: Decimal = Decimal("0"),
    ) -> PaperResult:
        cash = _decimal(state.cash_krw, "cash_krw")
        quantity = _decimal(state.quantity, "quantity")
        cost_basis = _decimal(state.cost_basis_krw, "cost_basis_krw")
        market_value = quantity * Decimal(str(mark_candle.close))
        unrealized = market_value - cost_basis if quantity else Decimal("0")
        equity = cash + market_value
        decision_at = state.last_decision_at or mark_candle.timestamp.isoformat()
        execution_at = state.last_execution_at or mark_candle.timestamp.isoformat()
        return PaperResult(
            processed=processed,
            action=action,
            state=state,
            decision_at=decision_at,
            execution_at=execution_at,
            execution_price=_exact(execution_price) if execution_price is not None else None,
            fee_krw=_exact(fee),
            realized_pnl_krw=state.realized_pnl_krw,
            trade_realized_pnl_krw=_exact(realized),
            unrealized_pnl_krw=_exact(unrealized),
            equity_krw=_exact(equity),
        )
