"""Durable local journal for synthetic paper order execution events.

This module never creates an exchange client. It stores paper portfolio/order
snapshots and idempotent execution events in one SQLite transaction so a
process restart cannot apply a committed fill twice.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from .execution_simulator import ExecutionResult
from .paper_engine import OrderStatus, PaperOrder, PaperPortfolio


class PaperJournalIntegrityError(ValueError):
    """Raised when persisted paper state is missing, inconsistent, or corrupt."""


class PaperEventJournal:
    """Persist paper fills atomically and recover the latest local snapshot."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_portfolio (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    payload_json TEXT NOT NULL,
                    state_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_orders (
                    order_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    state_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_execution_events (
                    idempotency_key TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    applied_at_ms INTEGER NOT NULL
                );
                """
            )

    def register_order(self, order: PaperOrder, portfolio: PaperPortfolio) -> None:
        """Persist initial order/account state; existing state must match exactly."""
        portfolio_json = _encode(_portfolio_to_dict(portfolio))
        order_json = _encode(_order_to_dict(order))
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing_portfolio = db.execute(
                    "SELECT payload_json FROM paper_portfolio WHERE singleton = 1"
                ).fetchone()
                if existing_portfolio is None:
                    db.execute(
                        "INSERT INTO paper_portfolio(singleton, payload_json, state_hash) VALUES (1, ?, ?)",
                        (portfolio_json, _sha256(portfolio_json)),
                    )
                elif existing_portfolio[0] != portfolio_json:
                    raise PaperJournalIntegrityError("initial portfolio differs from persisted state")

                existing_order = db.execute(
                    "SELECT payload_json FROM paper_orders WHERE order_id = ?", (order.order_id,)
                ).fetchone()
                if existing_order is None:
                    db.execute(
                        "INSERT INTO paper_orders(order_id, payload_json, state_hash) VALUES (?, ?, ?)",
                        (order.order_id, order_json, _sha256(order_json)),
                    )
                elif existing_order[0] != order_json:
                    raise PaperJournalIntegrityError("initial order differs from persisted state")
                db.commit()
            except Exception:
                db.rollback()
                raise

    def load_order(self, order_id: str) -> PaperOrder:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT payload_json, state_hash FROM paper_orders WHERE order_id = ?", (order_id,)
            ).fetchone()
        if row is None:
            raise PaperJournalIntegrityError(f"paper order {order_id!r} is not registered")
        _verify_hash(row[0], row[1], f"order {order_id}")
        return _order_from_dict(json.loads(row[0]))

    def load_portfolio(self) -> PaperPortfolio:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT payload_json, state_hash FROM paper_portfolio WHERE singleton = 1"
            ).fetchone()
        if row is None:
            raise PaperJournalIntegrityError("paper portfolio is not initialized")
        _verify_hash(row[0], row[1], "portfolio")
        return _portfolio_from_dict(json.loads(row[0]))

    def apply_execution_result(
        self,
        order: PaperOrder,
        portfolio: PaperPortfolio,
        result: ExecutionResult,
        timestamp_ms: int,
        *,
        idempotency_key: str,
    ) -> bool:
        """Apply and persist one execution event; require a stable event identity."""
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("durable execution requires a non-empty idempotency key")

        event_json = _encode({
            "order_id": order.order_id,
            "timestamp_ms": timestamp_ms,
            "result": asdict(result),
        })
        event_hash = _sha256(event_json)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                prior_event = db.execute(
                    "SELECT order_id, payload_hash FROM paper_execution_events WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if prior_event is not None:
                    if prior_event[0] != order.order_id or prior_event[1] != event_hash:
                        raise PaperJournalIntegrityError("execution idempotency key was reused for different input")
                    latest_order = self._read_order(db, order.order_id)
                    latest_portfolio = self._read_portfolio(db)
                    db.commit()
                    _replace_object(order, latest_order)
                    _replace_object(portfolio, latest_portfolio)
                    return False

                stored_order = self._read_order(db, order.order_id)
                stored_portfolio = self._read_portfolio(db)
                if _order_to_dict(stored_order) != _order_to_dict(order):
                    raise PaperJournalIntegrityError("in-memory order is stale; reload before applying an event")
                if _portfolio_to_dict(stored_portfolio) != _portfolio_to_dict(portfolio):
                    raise PaperJournalIntegrityError("in-memory portfolio is stale; reload before applying an event")

                staged_order = stored_order
                staged_portfolio = stored_portfolio
                staged_portfolio.apply_execution_result(
                    staged_order,
                    result,
                    timestamp_ms,
                    idempotency_key=idempotency_key,
                )
                portfolio_json = _encode(_portfolio_to_dict(staged_portfolio))
                order_json = _encode(_order_to_dict(staged_order))
                db.execute(
                    "UPDATE paper_portfolio SET payload_json = ?, state_hash = ? WHERE singleton = 1",
                    (portfolio_json, _sha256(portfolio_json)),
                )
                db.execute(
                    "UPDATE paper_orders SET payload_json = ?, state_hash = ? WHERE order_id = ?",
                    (order_json, _sha256(order_json), order.order_id),
                )
                db.execute(
                    "INSERT INTO paper_execution_events(idempotency_key, order_id, payload_hash, payload_json, applied_at_ms) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (idempotency_key, order.order_id, event_hash, event_json, timestamp_ms),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise

        _replace_object(order, staged_order)
        _replace_object(portfolio, staged_portfolio)
        return True

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _read_order(db: sqlite3.Connection, order_id: str) -> PaperOrder:
        row = db.execute(
            "SELECT payload_json, state_hash FROM paper_orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        if row is None:
            raise PaperJournalIntegrityError(f"paper order {order_id!r} is not registered")
        _verify_hash(row[0], row[1], f"order {order_id}")
        return _order_from_dict(json.loads(row[0]))

    @staticmethod
    def _read_portfolio(db: sqlite3.Connection) -> PaperPortfolio:
        row = db.execute(
            "SELECT payload_json, state_hash FROM paper_portfolio WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise PaperJournalIntegrityError("paper portfolio is not initialized")
        _verify_hash(row[0], row[1], "portfolio")
        return _portfolio_from_dict(json.loads(row[0]))


def _portfolio_to_dict(portfolio: PaperPortfolio) -> dict[str, str]:
    return {
        "cash_krw": str(portfolio.cash_krw),
        "base_quantity": str(portfolio.base_quantity),
        "cost_basis_krw": str(portfolio.cost_basis_krw),
        "realized_pnl_krw": str(portfolio.realized_pnl_krw),
        "total_fees_paid_krw": str(portfolio.total_fees_paid_krw),
    }


def _portfolio_from_dict(payload: dict[str, Any]) -> PaperPortfolio:
    return PaperPortfolio(**{key: Decimal(value) for key, value in payload.items()})


def _order_to_dict(order: PaperOrder) -> dict[str, Any]:
    return {
        "order_id": order.order_id,
        "idempotency_key": order.idempotency_key,
        "market": order.market,
        "side": order.side,
        "status": order.status.value,
        "requested_amount_krw": str(order.requested_amount_krw) if order.requested_amount_krw is not None else None,
        "requested_quantity_btc": str(order.requested_quantity_btc) if order.requested_quantity_btc is not None else None,
        "filled_quantity": str(order.filled_quantity),
        "filled_amount_krw": str(order.filled_amount_krw),
        "fee_paid_krw": str(order.fee_paid_krw),
        "transitions": [[status.value, timestamp] for status, timestamp in order.transitions],
        "processed_idempotency_keys": sorted(order.processed_idempotency_keys),
        "processed_execution_keys": sorted(order.processed_execution_keys),
    }


def _order_from_dict(payload: dict[str, Any]) -> PaperOrder:
    return PaperOrder(
        order_id=payload["order_id"],
        idempotency_key=payload["idempotency_key"],
        market=payload["market"],
        side=payload["side"],
        status=OrderStatus(payload["status"]),
        requested_amount_krw=Decimal(payload["requested_amount_krw"]) if payload["requested_amount_krw"] is not None else None,
        requested_quantity_btc=Decimal(payload["requested_quantity_btc"]) if payload["requested_quantity_btc"] is not None else None,
        filled_quantity=Decimal(payload["filled_quantity"]),
        filled_amount_krw=Decimal(payload["filled_amount_krw"]),
        fee_paid_krw=Decimal(payload["fee_paid_krw"]),
        transitions=[(OrderStatus(status), int(timestamp)) for status, timestamp in payload["transitions"]],
        processed_idempotency_keys=set(payload["processed_idempotency_keys"]),
        processed_execution_keys=set(payload["processed_execution_keys"]),
    )


def _encode(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default, allow_nan=False)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"unsupported paper journal value: {type(value).__name__}")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _verify_hash(payload: str, expected_hash: str, label: str) -> None:
    if _sha256(payload) != expected_hash:
        raise PaperJournalIntegrityError(f"persisted {label} checksum mismatch")


def _replace_object(target: Any, source: Any) -> None:
    target.__dict__.clear()
    target.__dict__.update(source.__dict__)
