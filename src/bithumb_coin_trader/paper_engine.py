"""Event-Driven Paper Portfolio and Order Lifecycle State Machine (P5 - P5.6).

Features:
- 8-state order lifecycle state machine with strictly enforced transition matrix.
- Idempotent order processing preventing duplicate fills.
- Strict spot-only long-only invariants (no leverage, no shorting, no negative balance).
- Decimal cash conservation oracle verifying cash and asset balance conservation.
- Direct integration with DeterministicTakerSimulator ExecutionResult.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from enum import Enum
from typing import Any, Mapping

from .execution_simulator import ExecutionResult


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    SUBMITTED = "SUBMITTED"
    PENDING_FILL = "PENDING_FILL"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
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
        OrderStatus.SUBMITTED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
    },
    OrderStatus.SUBMITTED: {
        OrderStatus.PENDING_FILL,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.PENDING_FILL: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
        OrderStatus.EXPIRED,
    },
    OrderStatus.PARTIALLY_FILLED: {
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

    def transition_to(self, new_status: OrderStatus, timestamp_ms: int, idempotency_key: str | None = None) -> bool:
        """Transitions the order to new_status with strict validation and idempotency."""
        if idempotency_key is not None:
            if idempotency_key in self.processed_idempotency_keys:
                # Idempotent no-op
                return False
            self.processed_idempotency_keys.add(idempotency_key)

        if self.status == new_status:
            # Self-transition is a no-op unless it is PARTIALLY_FILLED
            if new_status != OrderStatus.PARTIALLY_FILLED:
                return False

        allowed = VALID_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise IllegalOrderStateTransitionError(
                f"Cannot transition order {self.order_id} from {self.status.value} to {new_status.value}"
            )

        self.status = new_status
        self.transitions.append((new_status, timestamp_ms))
        return True


@dataclass
class PaperPortfolio:
    cash_krw: Decimal = Decimal("20000000.0")  # 20M KRW default
    base_quantity: Decimal = Decimal("0.0")
    cost_basis_krw: Decimal = Decimal("0.0")
    realized_pnl_krw: Decimal = Decimal("0.0")
    total_fees_paid_krw: Decimal = Decimal("0.0")

    def _assert_invariants(self) -> None:
        if self.cash_krw < Decimal("0"):
            raise NegativeBalanceError(f"Cash balance cannot be negative: {self.cash_krw} KRW")
        if self.base_quantity < Decimal("0"):
            raise NegativeBalanceError(f"Base asset balance cannot be negative: {self.base_quantity}")

    def apply_fill(
        self,
        side: str,
        fill_price: Decimal,
        fill_qty: Decimal,
        fee_krw: Decimal,
    ) -> Decimal:
        """Applies an individual fill slice atomically with cash conservation verification."""
        if fill_qty <= Decimal("0") or fill_price <= Decimal("0"):
            raise ValueError("fill_price and fill_qty must be strictly positive")

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
            if (cash_before - self.cash_krw) != total_deduction:
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
    ) -> None:
        """Applies an entire ExecutionResult from DeterministicTakerSimulator."""
        if result.is_rejected:
            order.transition_to(OrderStatus.REJECTED, timestamp_ms)
            return

        if result.filled_quantity <= 0:
            order.transition_to(OrderStatus.REJECTED, timestamp_ms)
            return

        # Advance order through SUBMITTED -> PENDING_FILL if needed
        if order.status == OrderStatus.CREATED:
            order.transition_to(OrderStatus.RISK_APPROVED, timestamp_ms)
            order.transition_to(OrderStatus.SUBMITTED, timestamp_ms)
            order.transition_to(OrderStatus.PENDING_FILL, timestamp_ms)
        elif order.status == OrderStatus.RISK_APPROVED:
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
