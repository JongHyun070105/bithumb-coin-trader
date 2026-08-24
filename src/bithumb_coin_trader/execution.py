"""Fail-closed planning and execution for Bithumb spot positions."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Protocol

from .config import TradingMode, TradingSettings
from .discord_notify import DiscordNotifier, TradeEvent, TradeNotification
from .fill_ledger import FillLedger
from .models import Signal
from .risk import RiskContext, RiskLimits, evaluate_pretrade
from .state import STATE_VERSION, BotState, load_state, save_state


LIVE_ENV_VAR = "BITHUMB_LIVE_TRADING"
NEW_EXPOSURE_ENV_VAR = "BITHUMB_NEW_ENTRIES"
LIVE_CONFIRMATION_TOKEN = "CONFIRM_BITHUMB_LIVE_ORDER"
_MARKET_PATTERN = re.compile(r"^KRW-[A-Z0-9]{1,12}$")
_CLIENT_ORDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,36}$")


class ExecutionError(RuntimeError):
    """Base class for safe execution failures."""


class UnsupportedPositionError(ExecutionError):
    """The requested position cannot be represented on Bithumb spot."""


class LiveTradingDisabledError(ExecutionError):
    """One or more required live-order gates were not satisfied."""


class RiskRejectedError(ExecutionError):
    """Fresh state or pre-trade checks rejected an order."""


class OrderChanceError(ExecutionError):
    """The authenticated order-chance response was unsafe or malformed."""


# Compatibility-friendly name for the execution layer while keeping the shared
# strategy signal as the sole position enum.
Position = Signal


def _position(value: Signal | str, field: str) -> Signal:
    if isinstance(value, Signal):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field} must be LONG, FLAT, or SHORT")
    try:
        return Signal[value]
    except KeyError as exc:
        raise ValueError(f"{field} must be exactly LONG, FLAT, or SHORT") from exc


def _positive_decimal(value: Decimal | int | str | None, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (bool, float)):
        raise ValueError(f"{field} must use Decimal, int, or a decimal string")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a valid decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{field} must be finite and greater than zero")
    return parsed


@dataclass(frozen=True)
class TradeIntent:
    """A strictly validated desired spot position.

    LONG requires ``quote_amount`` when entering from FLAT. FLAT requires
    ``base_volume`` when exiting LONG. Extra, ambiguous order fields are not
    accepted because dataclass construction rejects unknown keywords.
    """

    market: str
    target: Signal | str
    quote_amount: Decimal | int | str | None = None
    base_volume: Decimal | int | str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.market, str) or not _MARKET_PATTERN.fullmatch(self.market):
            raise ValueError("market must match KRW-ASSET using uppercase letters or digits")
        object.__setattr__(self, "target", _position(self.target, "target"))
        object.__setattr__(
            self, "quote_amount", _positive_decimal(self.quote_amount, "quote_amount")
        )
        object.__setattr__(
            self, "base_volume", _positive_decimal(self.base_volume, "base_volume")
        )
        if not isinstance(self.reason, str):
            raise ValueError("reason must be a string")


@dataclass(frozen=True)
class ExecutionPlan:
    market: str
    current: Signal
    target: Signal
    tool_name: str | None
    arguments: Mapping[str, str]
    client_order_id: str | None
    allow_partial_exit: bool = False
    position_policy_version: int = 0

    @property
    def is_noop(self) -> bool:
        return self.tool_name is None


@dataclass(frozen=True)
class ExecutionResult:
    plan: ExecutionPlan
    submitted: bool
    response: Any = None


class ToolClient(Protocol):
    def call_read_tool(
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> Any: ...

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> Any: ...


class NotificationSink(Protocol):
    def send(self, notification: TradeNotification) -> bool: ...


def plan_execution(
    intent: TradeIntent,
    current: Signal | str,
    *,
    client_order_id: str | None = None,
    allow_partial_exit: bool = False,
    position_policy_version: int = 0,
) -> ExecutionPlan:
    current_position = _position(current, "current")
    if (
        isinstance(position_policy_version, bool)
        or not isinstance(position_policy_version, int)
        or position_policy_version < 0
    ):
        raise ValueError("position_policy_version must be a non-negative integer")
    if position_policy_version and intent.target is not Signal.LONG:
        raise ValueError("position policy version can be assigned only on entry")
    if current_position is Signal.SHORT:
        raise UnsupportedPositionError("Bithumb spot execution does not support SHORT positions")
    if intent.target is Signal.SHORT:
        raise UnsupportedPositionError("Bithumb spot execution does not support SHORT positions")
    if current_position is intent.target:
        return ExecutionPlan(
            intent.market,
            current_position,
            intent.target,
            None,
            {},
            None,
            allow_partial_exit,
            position_policy_version,
        )

    order_id = client_order_id or f"btc-trader-{uuid.uuid4().hex[:24]}"
    if not _CLIENT_ORDER_ID_PATTERN.fullmatch(order_id):
        raise ValueError("client_order_id must be 1-36 letters, digits, '-' or '_'")

    if intent.target is Signal.LONG:
        if intent.quote_amount is None:
            raise ValueError("quote_amount is required to enter LONG")
        arguments = {
            "market": intent.market,
            "side": "bid",
            "order_type": "price",
            "price": format(intent.quote_amount, "f"),
            "client_order_id": order_id,
        }
    else:
        if intent.base_volume is None:
            raise ValueError("base_volume is required to exit LONG")
        arguments = {
            "market": intent.market,
            "side": "ask",
            "order_type": "market",
            "volume": format(intent.base_volume, "f"),
            "client_order_id": order_id,
        }
    return ExecutionPlan(
        intent.market,
        current_position,
        intent.target,
        "trade_place_order",
        arguments,
        order_id,
        allow_partial_exit,
        position_policy_version,
    )


class BithumbExecutor:
    """Submit a validated plan only when every live-order gate is open."""

    def __init__(
        self,
        client: ToolClient,
        *,
        state_path: Path,
        settings: TradingSettings | None = None,
        env: Mapping[str, str] | None = None,
        notifier: NotificationSink | None = None,
        fill_ledger: FillLedger | None = None,
    ) -> None:
        self.client = client
        self.state_path = state_path
        self.settings = settings or TradingSettings()
        self.env = os.environ if env is None else env
        self.notifier = notifier
        self.fill_ledger = fill_ledger
        if (
            self.notifier is None
            and env is None
            and self.settings.mode is TradingMode.LIVE
        ):
            self.notifier = DiscordNotifier()

    def execute(
        self,
        plan: ExecutionPlan,
        *,
        risk_context: RiskContext,
        bot_state: BotState,
        confirmation_token: str | None = None,
    ) -> ExecutionResult:
        if plan.is_noop:
            return ExecutionResult(plan=plan, submitted=False)
        if self.settings.mode is not TradingMode.LIVE:
            return ExecutionResult(plan=plan, submitted=False)
        if not self.settings.live_trading_enabled:
            raise LiveTradingDisabledError("live submission is disabled by TradingSettings")
        if self.env.get(LIVE_ENV_VAR) != "true":
            raise LiveTradingDisabledError(
                f"live submission requires {LIVE_ENV_VAR}=true exactly"
            )
        if plan.target is Signal.LONG and self.env.get(NEW_EXPOSURE_ENV_VAR) != "true":
            raise LiveTradingDisabledError(
                f"new exposure requires {NEW_EXPOSURE_ENV_VAR}=true exactly"
            )
        if confirmation_token != LIVE_CONFIRMATION_TOKEN:
            raise LiveTradingDisabledError(
                "live submission requires the exact runtime confirmation token"
            )
        try:
            _validate_live_plan(plan)
            persisted_state = load_state(self.state_path)
            if persisted_state != bot_state:
                raise RiskRejectedError(
                    "provided BotState is stale relative to persisted state"
                )
            _validate_pretrade(plan, risk_context, persisted_state, self.settings)
            # Re-check market-specific balance, fees, limits, and order availability
            # immediately before the sole mutating call. Failure aborts submission.
            chance_result = self.client.call_read_tool(
                "account_get_order_chance", {"market": plan.market}
            )
            _validate_order_chance(plan, risk_context, chance_result, self.settings)
        except Exception as exc:
            detail = str(exc) if isinstance(exc, ExecutionError) else type(exc).__name__
            self._notify(TradeEvent.BLOCKED, plan, detail=detail)
            raise
        active_state = replace(
            persisted_state,
            active_client_order_id=plan.client_order_id,
            pending_order_side=plan.arguments["side"],
            pending_market=plan.market,
            pending_order_volume=(
                plan.arguments.get("volume")
                if plan.arguments["side"] == "ask"
                else None
            ),
            position_policy_version=(
                plan.position_policy_version
                if plan.arguments["side"] == "bid"
                else persisted_state.position_policy_version
            ),
            untracked_order=False,
        )
        save_state(self.state_path, active_state)
        try:
            response = self.client.call_tool(plan.tool_name, plan.arguments)
        except Exception as exc:
            save_state(self.state_path, replace(active_state, untracked_order=True))
            self._notify(TradeEvent.AMBIGUOUS, plan, detail=type(exc).__name__)
            raise
        self._notify(TradeEvent.ACCEPTED, plan, detail="거래소 접수 응답 수신")
        return ExecutionResult(plan=plan, submitted=True, response=response)

    def reconcile_active_order(self) -> BotState:
        """Read one active order by client id and clear only known terminal states."""
        state = load_state(self.state_path)
        client_order_id = state.active_client_order_id
        if client_order_id is None:
            raise ExecutionError("there is no active client_order_id to reconcile")
        result = self.client.call_read_tool(
            "trade_get_order", {"client_order_id": client_order_id}
        )
        payload = _tool_json_payload(result, "trade_get_order")
        if payload.get("client_order_id") != client_order_id:
            raise ExecutionError("reconciled order client_order_id does not match state")
        if payload.get("market") != state.pending_market:
            raise ExecutionError("reconciled order market does not match pending state")
        side = payload.get("side")
        if side != state.pending_order_side:
            raise ExecutionError("reconciled order side does not match pending state")
        status = payload.get("state")
        if status in {"done", "cancel"}:
            executed = _decimal_value(
                payload.get("executed_volume"), "executed_volume", allow_zero=True
            )
            reconciled = _apply_terminal_fill(state, side, status, executed)
            if self.fill_ledger is not None and executed > 0:
                existing = self.fill_ledger.position(state.pending_market or "")
                # Pre-ledger holdings may be liquidated, but all newly opened
                # positions and their exits require immutable exchange fills.
                if (
                    side == "bid"
                    or existing.volume > 0
                    or (side == "ask" and state.position_policy_version > 0)
                ):
                    self.fill_ledger.append_order(payload)
            save_state(self.state_path, reconciled)
            event = TradeEvent.FILLED if status == "done" else TradeEvent.CANCELLED
            self._notify_state(
                event,
                state,
                volume=format(executed, "f"),
                detail=f"executed_volume={format(executed, 'f')}",
            )
            return reconciled
        if status == "wait":
            self._notify_state(TradeEvent.PENDING, state, detail="state=wait")
            return state
        raise ExecutionError(f"unknown order reconciliation state: {status!r}")

    def reconcile_until_terminal(
        self,
        *,
        timeout_seconds: float = 15.0,
        poll_interval_seconds: float = 0.25,
    ) -> BotState:
        """Poll a submitted order until done/cancel without ever resubmitting it."""
        if timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("reconciliation timeout and poll interval must be positive")
        deadline = time.monotonic() + timeout_seconds
        while True:
            reconciled = self.reconcile_active_order()
            if reconciled.active_client_order_id is None:
                return reconciled
            if time.monotonic() >= deadline:
                raise ExecutionError("order remained pending after reconciliation timeout")
            time.sleep(poll_interval_seconds)

    def _notify(
        self, event: TradeEvent, plan: ExecutionPlan, *, detail: str = ""
    ) -> bool:
        side = plan.arguments.get("side")
        return self._send_notification(
            TradeNotification(
                event=event,
                market=plan.market,
                side=side,
                client_order_id=plan.client_order_id,
                notional_krw=plan.arguments.get("price") if side == "bid" else None,
                volume=plan.arguments.get("volume") if side == "ask" else None,
                detail=detail,
            )
        )

    def _notify_state(
        self,
        event: TradeEvent,
        state: BotState,
        *,
        volume: str | None = None,
        detail: str = "",
    ) -> bool:
        return self._send_notification(
            TradeNotification(
                event=event,
                market=state.pending_market or "KRW-UNKNOWN",
                side=state.pending_order_side,
                client_order_id=state.active_client_order_id,
                volume=volume,
                detail=detail,
            )
        )

    def _send_notification(self, notification: TradeNotification) -> bool:
        if self.notifier is None:
            return False
        try:
            return bool(self.notifier.send(notification))
        except Exception:
            # A notification failure after an exchange response must never be
            # exposed as an order failure that a caller might retry.
            return False


def _apply_terminal_fill(
    state: BotState, side: Any, status: str, executed: Decimal
) -> BotState:
    tracked = Decimal(state.position_volume)
    if side == "bid":
        if state.position != "flat" or tracked != 0:
            raise ExecutionError("pending buy is inconsistent with tracked position")
        if status == "done" and executed == 0:
            raise ExecutionError("completed buy reported zero executed_volume")
        position = "long" if executed > 0 else "flat"
        volume = executed
    elif side == "ask":
        if state.position != "long" or tracked <= 0:
            raise ExecutionError("pending sell is inconsistent with tracked position")
        requested = (
            Decimal(state.pending_order_volume)
            if state.pending_order_volume is not None
            else tracked
        )
        if requested > tracked:
            raise ExecutionError("pending sell volume exceeds tracked position")
        if executed > requested:
            raise ExecutionError("sell executed_volume exceeds requested order volume")
        if executed > tracked:
            raise ExecutionError("sell executed_volume exceeds tracked position")
        if status == "done" and executed != requested:
            detail = (
                "requested order volume"
                if state.pending_order_volume is not None
                else "full tracked position"
            )
            raise ExecutionError(f"completed sell did not execute the {detail}")
        volume = tracked - executed
        position = "flat" if volume == 0 else "long"
    else:
        raise ExecutionError("terminal order has an unsupported side")
    return replace(
        state,
        position=position,
        position_volume="0" if volume == 0 else format(volume, "f"),
        active_client_order_id=None,
        pending_order_side=None,
        pending_market=None,
        pending_order_volume=None,
        position_policy_version=(0 if position == "flat" else state.position_policy_version),
        untracked_order=False,
    )


def _validate_live_plan(plan: ExecutionPlan) -> None:
    """Revalidate the complete order payload at the final mutation boundary."""
    if plan.tool_name != "trade_place_order" or not plan.client_order_id:
        raise ExecutionError("refusing an unrecognized or unidentifiable live plan")
    if not _MARKET_PATTERN.fullmatch(plan.market):
        raise ExecutionError("refusing a live plan with an invalid market")
    if not _CLIENT_ORDER_ID_PATTERN.fullmatch(plan.client_order_id):
        raise ExecutionError("refusing a live plan with an invalid client_order_id")
    if plan.arguments.get("market") != plan.market:
        raise ExecutionError("live plan market does not match its order payload")
    if plan.arguments.get("client_order_id") != plan.client_order_id:
        raise ExecutionError("live plan client_order_id does not match its order payload")

    if plan.current is Signal.FLAT and plan.target is Signal.LONG:
        expected_keys = {"market", "side", "order_type", "price", "client_order_id"}
        amount_field = "price"
        expected_side, expected_type = "bid", "price"
    elif plan.current is Signal.LONG and plan.target is Signal.FLAT:
        expected_keys = {"market", "side", "order_type", "volume", "client_order_id"}
        amount_field = "volume"
        expected_side, expected_type = "ask", "market"
    else:
        raise UnsupportedPositionError(
            "live execution supports only FLAT-to-LONG or LONG-to-FLAT transitions"
        )
    if plan.allow_partial_exit and not (
        plan.current is Signal.LONG and plan.target is Signal.FLAT
    ):
        raise ExecutionError("partial-exit authorization is valid only for a sell transition")
    if set(plan.arguments) != expected_keys:
        raise ExecutionError("live order payload contains missing or unexpected fields")
    if (
        plan.arguments.get("side") != expected_side
        or plan.arguments.get("order_type") != expected_type
    ):
        raise ExecutionError("live order side or order_type does not match its transition")
    try:
        _positive_decimal(plan.arguments.get(amount_field), amount_field)
    except ValueError as exc:
        raise ExecutionError(f"live order has an invalid {amount_field}") from exc


def _validate_pretrade(
    plan: ExecutionPlan,
    context: RiskContext,
    state: BotState,
    settings: TradingSettings,
) -> None:
    if not isinstance(context, RiskContext) or not isinstance(state, BotState):
        raise RiskRejectedError("fresh RiskContext and BotState instances are required")
    if state.version != STATE_VERSION:
        raise RiskRejectedError("bot state version is unsupported")
    if not isinstance(context.daily_entries, int) or isinstance(context.daily_entries, bool):
        raise RiskRejectedError("risk context daily_entries must be an integer")
    if context.daily_entries < 0:
        raise RiskRejectedError("risk context daily_entries cannot be negative")
    if not isinstance(context.data_is_fresh, bool) or not isinstance(
        context.has_untracked_order, bool
    ):
        raise RiskRejectedError("risk context flags must be booleans")
    for field in (
        "current_equity_krw",
        "start_of_day_equity_krw",
        "peak_equity_krw",
    ):
        value = _risk_decimal(getattr(context, field), field)
        if value <= 0:
            raise RiskRejectedError(f"risk context {field} must be positive")
    expected_position = plan.current.name.lower()
    if state.position != expected_position:
        raise RiskRejectedError("bot state position does not match the execution plan")
    if context.requested_side is not plan.target:
        raise RiskRejectedError("risk context side does not match the execution plan")
    requested_notional = _risk_decimal(
        context.requested_notional_krw, "requested_notional_krw"
    )
    if requested_notional <= 0:
        raise RiskRejectedError("risk context notional must be finite and positive")
    if requested_notional < settings.minimum_order_krw:
        raise RiskRejectedError("requested order is below the configured minimum")
    max_order = Decimal(str(settings.maximum_order_krw))
    if plan.target is Signal.LONG and requested_notional > max_order:
        raise RiskRejectedError(f"requested order exceeds the hard {settings.maximum_order_krw:,} KRW cap")
    if plan.target is Signal.LONG:
        planned_notional = Decimal(plan.arguments["price"])
        if requested_notional != planned_notional:
            raise RiskRejectedError("risk context notional does not match the buy order")
    else:
        position_volume = _risk_decimal(state.position_volume, "position_volume")
        planned_volume = Decimal(plan.arguments["volume"])
        if planned_volume > position_volume:
            raise RiskRejectedError("sell volume exceeds the tracked position volume")
        if planned_volume != position_volume and not plan.allow_partial_exit:
            raise RiskRejectedError("sell volume does not match the tracked position volume")
        if context.reference_price_krw is None:
            raise RiskRejectedError("sell risk context requires reference_price_krw")
        reference_price = _risk_decimal(
            context.reference_price_krw, "reference_price_krw"
        )
        if reference_price <= 0:
            raise RiskRejectedError("reference_price_krw must be positive")
        if planned_volume != position_volume:
            remaining_notional = (position_volume - planned_volume) * reference_price
            if remaining_notional < Decimal(str(settings.minimum_order_krw)):
                raise RiskRejectedError("partial exit would leave a position below the minimum order")
        calculated_notional = planned_volume * reference_price
        if abs(requested_notional - calculated_notional) > Decimal("1.0"):
            raise RiskRejectedError(
                "sell notional does not equal tracked volume times reference price"
            )

    effective_context = replace(
        context,
        has_untracked_order=(
            context.has_untracked_order
            or state.untracked_order
            or state.active_client_order_id is not None
        ),
    )
    limits = RiskLimits(
        minimum_order_krw=settings.minimum_order_krw,
        maximum_order_krw=100_000 if plan.target is Signal.FLAT else settings.maximum_order_krw,
        maximum_daily_entries=settings.maximum_daily_entries,
        short_execution_enabled=False,
    )
    decision = evaluate_pretrade(effective_context, limits)
    if not decision.allowed:
        raise RiskRejectedError("pre-trade risk rejected order: " + "; ".join(decision.reasons))


def _risk_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise RiskRejectedError(f"risk context {field} is malformed")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RiskRejectedError(f"risk context {field} is malformed") from exc
    if not parsed.is_finite():
        raise RiskRejectedError(f"risk context {field} must be finite")
    return parsed


def _validate_order_chance(
    plan: ExecutionPlan,
    context: RiskContext,
    result: Any,
    settings: TradingSettings,
) -> None:
    payload = _order_chance_payload(result)
    market_info = _mapping(payload.get("market"), "market")
    if market_info.get("id") != plan.market:
        raise OrderChanceError("order chance market does not match the planned market")
    if market_info.get("state") != "active":
        raise OrderChanceError("order chance market is not active")

    is_buy = plan.target is Signal.LONG
    side = "bid" if is_buy else "ask"
    supported_sides = market_info.get("order_sides")
    supported_types = market_info.get("bid_types" if is_buy else "ask_types")
    if (
        not isinstance(supported_sides, list)
        or side not in supported_sides
        or not all(isinstance(item, str) for item in supported_sides)
    ):
        raise OrderChanceError("planned side is not supported by the market")
    if (
        not isinstance(supported_types, list)
        or plan.arguments.get("order_type") not in supported_types
        or not all(isinstance(item, str) for item in supported_types)
    ):
        raise OrderChanceError("planned order type is not supported by the market")
    fee = _decimal_field(payload, f"{side}_fee", allow_zero=True)
    maximum_fee = Decimal(str(settings.fee_rate))
    if fee > maximum_fee:
        raise OrderChanceError(
            f"order chance {side}_fee exceeds the configured fee assumption"
        )
    side_info = _mapping(market_info.get(side), f"market.{side}")
    minimum = _decimal_field(side_info, "min_total")
    requested_notional = _decimal_value(
        context.requested_notional_krw, "requested_notional_krw"
    )
    if requested_notional < minimum:
        raise OrderChanceError("requested order is below the exchange minimum")

    account_name = "bid_account" if is_buy else "ask_account"
    account = _mapping(payload.get(account_name), account_name)
    expected_currency = plan.market.split("-", 1)[0 if is_buy else 1]
    if account.get("currency") != expected_currency:
        raise OrderChanceError(f"{account_name} currency does not match the planned market")
    balance = _decimal_field(account, "balance", allow_zero=True)
    if is_buy:
        required = _decimal_value(plan.arguments["price"], "price") * (1 + fee)
    else:
        required = _decimal_value(plan.arguments["volume"], "volume")
    if balance < required:
        raise OrderChanceError(f"insufficient available {expected_currency} balance")


def _order_chance_payload(result: Any) -> Mapping[str, Any]:
    return _tool_json_payload(result, "account_get_order_chance")


def _tool_json_payload(result: Any, tool_name: str) -> Mapping[str, Any]:
    outer = _mapping(result, "MCP CallToolResult")
    if outer.get("isError") is True:
        raise OrderChanceError(f"{tool_name} reported an MCP tool error")
    content = outer.get("content")
    if not isinstance(content, list) or len(content) != 1:
        raise OrderChanceError(f"{tool_name} must contain exactly one MCP content block")
    block = _mapping(content[0], "MCP content block")
    if block.get("type") != "text" or not isinstance(block.get("text"), str):
        raise OrderChanceError(f"{tool_name} MCP content must be one text block")
    try:
        payload = json.loads(block["text"])
    except json.JSONDecodeError as exc:
        raise OrderChanceError(f"{tool_name} text is not valid JSON") from exc
    payload = _mapping(payload, f"{tool_name} JSON")
    for depth in range(2):
        if "data" not in payload:
            break
        payload = _mapping(payload["data"], f"{tool_name} data level {depth + 1}")
    if "data" in payload:
        raise OrderChanceError(f"{tool_name} has unsupported nested data wrappers")
    return payload


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OrderChanceError(f"{field} must be an object")
    return value


def _decimal_field(
    payload: Mapping[str, Any], field: str, *, allow_zero: bool = False
) -> Decimal:
    if field not in payload:
        raise OrderChanceError(f"order chance is missing {field}")
    return _decimal_value(payload[field], field, allow_zero=allow_zero)


def _decimal_value(value: Any, field: str, *, allow_zero: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise OrderChanceError(f"{field} must be an exact decimal string or integer")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise OrderChanceError(f"{field} is not a valid decimal") from exc
    if not parsed.is_finite() or parsed < 0 or (not allow_zero and parsed == 0):
        raise OrderChanceError(f"{field} must be finite and positive")
    return parsed
