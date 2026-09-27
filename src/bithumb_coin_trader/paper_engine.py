"""Event-Driven Paper Portfolio and Order Lifecycle State Machine (P5 - P5.6).

Features:
- 8-state order lifecycle state machine with strictly enforced transition matrix.
- Idempotent order processing preventing duplicate fills.
- Strict spot-only long-only invariants (no leverage, no shorting, no negative balance).
- Decimal cash conservation oracle verifying cash and asset balance conservation.
- Direct integration with DeterministicTakerSimulator ExecutionResult.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping

from .execution_simulator import ExecutionResult


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    ACCEPTED = "ACCEPTED"
    SUBMITTED = "SUBMITTED"
    PENDING_FILL = "PENDING_FILL"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


TERMINAL_STATES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELLED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
}

VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED: {
        OrderStatus.RISK_APPROVED,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.RISK_APPROVED: {
        OrderStatus.ACCEPTED,
        OrderStatus.SUBMITTED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
    },
    OrderStatus.ACCEPTED: {
        OrderStatus.SUBMITTED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
    },
    OrderStatus.SUBMITTED: {
        OrderStatus.PENDING_FILL,
        OrderStatus.CANCEL_PENDING,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.PENDING_FILL: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCEL_PENDING,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
        OrderStatus.EXPIRED,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCEL_PENDING,
        OrderStatus.CANCELLED,
        OrderStatus.EXPIRED,
    },
    OrderStatus.CANCEL_PENDING: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.EXPIRED,
    },
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.EXPIRED: set(),
}


class PaperEngineError(Exception):
    """Base exception for paper engine errors."""


class IllegalOrderStateTransitionError(PaperEngineError):
    """Raised when an illegal transition is attempted on an order."""


class NegativeBalanceError(PaperEngineError):
    """Raised when a transaction would cause cash or asset balance to go negative."""


class CashConservationError(PaperEngineError):
    """Raised when a balance update violates cash conservation invariants."""


@dataclass
class PaperOrder:
    order_id: str
    idempotency_key: str
    market: str
    side: str
    status: OrderStatus = OrderStatus.CREATED
    requested_amount_krw: Decimal | None = None
    requested_quantity_btc: Decimal | None = None
    filled_quantity: Decimal = field(default_factory=lambda: Decimal("0"))
    filled_amount_krw: Decimal = field(default_factory=lambda: Decimal("0"))
    fee_paid_krw: Decimal = field(default_factory=lambda: Decimal("0"))
    transitions: list[tuple[OrderStatus, int]] = field(default_factory=list)
    processed_idempotency_keys: set[str] = field(default_factory=set)
    processed_execution_keys: set[str] = field(default_factory=set)

    def transition_to(self, new_status: OrderStatus, timestamp_ms: int, idempotency_key: str | None = None) -> bool:
        """Transitions the order to new_status with strict validation and idempotency."""
        if idempotency_key is not None:
            if idempotency_key in self.processed_idempotency_keys:
                # Idempotent no-op
                return False

        if self.status == new_status:
            # Self-transition is a no-op unless it is PARTIALLY_FILLED
            if new_status != OrderStatus.PARTIALLY_FILLED:
                if idempotency_key is not None:
                    self.processed_idempotency_keys.add(idempotency_key)
                return False

        allowed = VALID_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise IllegalOrderStateTransitionError(
                f"Cannot transition order {self.order_id} from {self.status.value} to {new_status.value}"
            )

        self.status = new_status
        self.transitions.append((new_status, timestamp_ms))
        if idempotency_key is not None:
            self.processed_idempotency_keys.add(idempotency_key)
        return True


@dataclass
class PaperPortfolio:
    cash_krw: Decimal = Decimal("20000000.0")  # 20M KRW default
    base_quantity: Decimal = Decimal("0.0")
    cost_basis_krw: Decimal = Decimal("0.0")
    realized_pnl_krw: Decimal = Decimal("0.0")
    total_fees_paid_krw: Decimal = Decimal("0.0")

    def __post_init__(self) -> None:
        self._assert_invariants()

    def _assert_invariants(self) -> None:
        values = (
            self.cash_krw,
            self.base_quantity,
            self.cost_basis_krw,
            self.realized_pnl_krw,
            self.total_fees_paid_krw,
        )
        if any(not value.is_finite() for value in values):
            raise ValueError("paper accounting values must be finite")
        if self.cash_krw < Decimal("0"):
            raise NegativeBalanceError(f"Cash balance cannot be negative: {self.cash_krw} KRW")
        if self.base_quantity < Decimal("0"):
            raise NegativeBalanceError(f"Base asset balance cannot be negative: {self.base_quantity}")
        if self.cost_basis_krw < Decimal("0"):
            raise CashConservationError(f"Cost basis cannot be negative: {self.cost_basis_krw} KRW")
        if self.total_fees_paid_krw < Decimal("0"):
            raise CashConservationError(f"Fees cannot be negative: {self.total_fees_paid_krw} KRW")

    def apply_fill(
        self,
        side: str,
        fill_price: Decimal,
        fill_qty: Decimal,
        fee_krw: Decimal,
    ) -> Decimal:
        """Applies an individual fill slice atomically with cash conservation verification."""
        if any(not value.is_finite() for value in (fill_price, fill_qty, fee_krw)):
            raise ValueError("fill price, quantity, and fee must be finite")
        if fill_qty <= Decimal("0") or fill_price <= Decimal("0"):
            raise ValueError("fill_price and fill_qty must be strictly positive")
        if fee_krw < Decimal("0"):
            raise ValueError("fee_krw must be non-negative")

        side_norm = side.upper()
        notional_krw = fill_price * fill_qty

        cash_before = self.cash_krw
        base_before = self.base_quantity
        cost_basis_before = self.cost_basis_krw
        realized_pnl_before = self.realized_pnl_krw

        pnl_delta = Decimal("0")

        if side_norm == "BUY":
            total_deduction = notional_krw + fee_krw
            if self.cash_krw < total_deduction:
                raise NegativeBalanceError(
                    f"Insufficient cash for BUY fill: available {self.cash_krw} < required {total_deduction}"
                )
            self.cash_krw -= total_deduction
            self.base_quantity += fill_qty
            self.cost_basis_krw += notional_krw
            self.total_fees_paid_krw += fee_krw

            # Cash conservation check for BUY:
            # cash_delta + notional + fee == 0
            if abs((cash_before - self.cash_krw) - total_deduction) > Decimal("0.0001"):
                raise CashConservationError("BUY cash conservation invariant violated")

        elif side_norm == "SELL":
            if self.base_quantity < fill_qty:
                raise NegativeBalanceError(
                    f"Insufficient base quantity for SELL fill: available {self.base_quantity} < required {fill_qty}"
                )
            # Calculate cost of goods sold
            cogs = (cost_basis_before * (fill_qty / base_before)) if base_before > 0 else Decimal("0")
            pnl_delta = notional_krw - cogs - fee_krw

            self.cash_krw += (notional_krw - fee_krw)
            self.base_quantity -= fill_qty
            self.cost_basis_krw -= cogs
            self.realized_pnl_krw += pnl_delta
            self.total_fees_paid_krw += fee_krw

            # Cash conservation check for SELL:
            # (cash_after - cash_before) + (cost_basis_after - cost_basis_before) == pnl_delta
            net_wealth_change = (self.cash_krw - cash_before) + (self.cost_basis_krw - cost_basis_before)
            if abs(net_wealth_change - pnl_delta) > Decimal("0.0001"):
                raise CashConservationError("SELL cash conservation invariant violated")
        else:
            raise ValueError(f"Unknown side: {side}")

        self._assert_invariants()
        return pnl_delta

    def apply_execution_result(
        self,
        order: PaperOrder,
        result: ExecutionResult,
        timestamp_ms: int,
        *,
        idempotency_key: str | None = None,
    ) -> bool:
        """Apply one execution event once, atomically, with fail-closed binding."""
        self._validate_execution_result(order, result)
        event_key = (
            self._execution_fingerprint(order, result, timestamp_ms)
            if idempotency_key is None
            else idempotency_key
        )
        if not isinstance(event_key, str) or not event_key.strip():
            raise ValueError("execution idempotency key must be a non-empty string")
        if event_key in order.processed_execution_keys:
            return False

        staged_order = copy.deepcopy(order)
        staged_portfolio = copy.deepcopy(self)
        staged_portfolio._apply_execution_result(staged_order, result, timestamp_ms)
        staged_order.processed_execution_keys.add(event_key)

        self.__dict__.update(staged_portfolio.__dict__)
        order.__dict__.update(staged_order.__dict__)
        return True

    def _apply_execution_result(
        self,
        order: PaperOrder,
        result: ExecutionResult,
        timestamp_ms: int,
    ) -> None:
        """Mutating helper; callers apply it only to staged state."""
        if result.is_rejected:
            order.transition_to(OrderStatus.REJECTED, timestamp_ms)
            return

        if result.filled_quantity <= 0:
            raise ValueError("non-rejected execution must contain positive fills")

        # Advance order through SUBMITTED -> PENDING_FILL if needed
        if order.status == OrderStatus.CREATED:
            order.transition_to(OrderStatus.RISK_APPROVED, timestamp_ms)
            order.transition_to(OrderStatus.ACCEPTED, timestamp_ms)
            order.transition_to(OrderStatus.SUBMITTED, timestamp_ms)
            order.transition_to(OrderStatus.PENDING_FILL, timestamp_ms)
        elif order.status == OrderStatus.RISK_APPROVED:
            order.transition_to(OrderStatus.ACCEPTED, timestamp_ms)
            order.transition_to(OrderStatus.SUBMITTED, timestamp_ms)
            order.transition_to(OrderStatus.PENDING_FILL, timestamp_ms)
        elif order.status == OrderStatus.ACCEPTED:
            order.transition_to(OrderStatus.SUBMITTED, timestamp_ms)
            order.transition_to(OrderStatus.PENDING_FILL, timestamp_ms)
        elif order.status == OrderStatus.SUBMITTED:
            order.transition_to(OrderStatus.PENDING_FILL, timestamp_ms)

        target_status = OrderStatus.PARTIALLY_FILLED if result.is_partial else OrderStatus.FILLED
        order.transition_to(target_status, timestamp_ms)

        # Apply fills
        for f in result.fills:
            p = Decimal(str(f.price))
            s = Decimal(str(f.size))
            lvl_fee = Decimal(str(f.notional_krw)) * Decimal(str(result.fee_rate))
            self.apply_fill(order.side, p, s, lvl_fee)
            order.filled_quantity += s
            order.filled_amount_krw += Decimal(str(f.notional_krw))
            order.fee_paid_krw += lvl_fee

    @staticmethod
    def _execution_fingerprint(
        order: PaperOrder,
        result: ExecutionResult,
        timestamp_ms: int,
    ) -> str:
        payload = {
            "order_id": order.order_id,
            "timestamp_ms": timestamp_ms,
            "status": result.status,
            "side": result.side,
            "market": result.request.market,
            "fee_rate": result.fee_rate,
            "fills": [
                [fill.level_index, fill.price, fill.size, fill.notional_krw]
                for fill in result.fills
            ],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_execution_result(order: PaperOrder, result: ExecutionResult) -> None:
        side = order.side.upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("paper order side must be BUY or SELL")
        if result.side.upper() != side or result.request.side.upper() != side:
            raise ValueError("execution side does not match paper order")
        if result.request.market != order.market:
            raise ValueError("execution market does not match paper order")
        if order.requested_quantity_btc is not None:
            if result.request.requested_quantity_btc is None or Decimal(str(result.request.requested_quantity_btc)) != order.requested_quantity_btc:
                raise ValueError("execution quantity request does not match paper order")
        if order.requested_amount_krw is not None:
            if result.request.requested_amount_krw is None or Decimal(str(result.request.requested_amount_krw)) != order.requested_amount_krw:
                raise ValueError("execution notional request does not match paper order")
        if result.status not in {"FILLED", "PARTIALLY_FILLED", "REJECTED"}:
            raise ValueError("execution result status is invalid")
        summary_values = (
            result.filled_quantity,
            result.filled_amount_krw,
            result.fee_rate,
            result.fee_paid_krw,
        )
        if any(not math.isfinite(value) or value < 0 for value in summary_values):
            raise ValueError("execution result contains invalid summary values")
        if result.is_rejected:
            if (
                result.fills
                or result.filled_quantity != 0
                or result.filled_amount_krw != 0
                or result.fee_paid_krw != 0
            ):
                raise ValueError("rejected execution result cannot contain fills")
            return
        if not result.fills:
            raise ValueError("filled execution result must contain fill evidence")

        quantity = 0.0
        notional = 0.0
        for fill in result.fills:
            values = (fill.price, fill.size, fill.notional_krw)
            if any(not math.isfinite(value) or value <= 0 for value in values):
                raise ValueError("execution fill contains invalid values")
            if not math.isclose(
                fill.price * fill.size,
                fill.notional_krw,
                rel_tol=1e-9,
                abs_tol=1e-6,
            ):
                raise ValueError("execution fill notional does not match price and quantity")
            quantity += fill.size
            notional += fill.notional_krw
        if not math.isclose(quantity, result.filled_quantity, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("execution fill quantities do not match result summary")
        if not math.isclose(notional, result.filled_amount_krw, rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError("execution fill notionals do not match result summary")
        expected_fee = notional * result.fee_rate
        if not math.isclose(expected_fee, result.fee_paid_krw, rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError("execution fill fees do not match result summary")
        if order.requested_quantity_btc is not None:
            total_quantity = order.filled_quantity + Decimal(str(result.filled_quantity))
            if total_quantity > order.requested_quantity_btc + Decimal("1e-12"):
                raise ValueError("execution result overfills requested quantity")
        if order.requested_amount_krw is not None:
            total_notional = order.filled_amount_krw + Decimal(str(result.filled_amount_krw))
            if total_notional > order.requested_amount_krw + Decimal("0.000001"):
                raise ValueError("execution result overfills requested notional")
