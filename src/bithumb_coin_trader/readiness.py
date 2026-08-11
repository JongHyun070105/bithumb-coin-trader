"""Read-only, fail-closed assessment for whether live activation may be considered."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from .state import BotState


_MARKET_PATTERN = re.compile(r"^KRW-[A-Z0-9]{2,12}$")
_DISCORD_PATTERN = re.compile(r"^discord:[0-9]{6,30}$")
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class ReadOnlyProbe(Protocol):
    def call_read_tool(
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class PaperEvidence:
    started_at: datetime
    observed_at: datetime
    decision_count: int
    completed_round_trips: int
    accounting_mismatches: int


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    status: str
    checks: tuple[ReadinessCheck, ...]

    @property
    def ready(self) -> bool:
        return self.status == "READY"

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe report containing no configuration values."""
        return {
            "status": self.status,
            "checks": [
                {"name": check.name, "passed": check.passed, "detail": check.detail}
                for check in self.checks
            ],
        }


def assess_live_readiness(
    *,
    research_report: Mapping[str, Any],
    paper: PaperEvidence,
    bot_state: BotState,
    env: Mapping[str, str],
    market: str = "KRW-BTC",
    mcp_probe: ReadOnlyProbe | None = None,
) -> ReadinessReport:
    """Assess evidence without changing flags, state, credentials, or orders."""
    checks = [
        _promotion_check(research_report),
        _duration_check(paper),
        _integer_threshold_check("paper_decisions", paper.decision_count, 100),
        _integer_threshold_check(
            "completed_round_trips", paper.completed_round_trips, 30
        ),
        _accounting_check(paper.accounting_mismatches),
        _bot_state_check(bot_state),
        _discord_check(env),
        _api_key_check(env),
        _live_flags_off_check(env),
    ]
    checks.append(
        _mcp_check(mcp_probe, market)
        if mcp_probe is not None
        else ReadinessCheck(
            "mcp_account_probe",
            False,
            "read-only account probe is required for READY",
        )
    )
    return ReadinessReport(
        status="READY" if all(check.passed for check in checks) else "NOT_READY",
        checks=tuple(checks),
    )


def _promotion_check(report: Mapping[str, Any]) -> ReadinessCheck:
    promotion = report.get("promotion") if isinstance(report, Mapping) else None
    if isinstance(promotion, Mapping):
        promotion = promotion.get("status")
    passed = promotion == "PAPER_CANDIDATE"
    return ReadinessCheck(
        "research_promotion",
        passed,
        "research promotion is PAPER_CANDIDATE"
        if passed
        else "research promotion is not PAPER_CANDIDATE",
    )


def _duration_check(paper: PaperEvidence) -> ReadinessCheck:
    valid = (
        isinstance(paper.started_at, datetime)
        and isinstance(paper.observed_at, datetime)
        and paper.started_at.tzinfo is not None
        and paper.observed_at.tzinfo is not None
        and paper.observed_at >= paper.started_at
    )
    passed = valid and paper.observed_at - paper.started_at >= timedelta(days=30)
    return ReadinessCheck(
        "paper_duration",
        passed,
        "paper observation duration is at least 30 days"
        if passed
        else "paper observation duration is missing, invalid, or below 30 days",
    )


def _integer_threshold_check(name: str, value: Any, minimum: int) -> ReadinessCheck:
    passed = isinstance(value, int) and not isinstance(value, bool) and value >= minimum
    return ReadinessCheck(
        name,
        passed,
        f"{name} meets the required threshold"
        if passed
        else f"{name} is invalid or below the required threshold",
    )


def _accounting_check(value: Any) -> ReadinessCheck:
    passed = isinstance(value, int) and not isinstance(value, bool) and value == 0
    return ReadinessCheck(
        "accounting_consistency",
        passed,
        "paper accounting has no mismatches"
        if passed
        else "paper accounting has mismatches or invalid evidence",
    )


def _discord_check(env: Mapping[str, str]) -> ReadinessCheck:
    configured = env.get("BITHUMB_DISCORD_TARGET")
    passed = isinstance(configured, str) and _DISCORD_PATTERN.fullmatch(configured) is not None
    return ReadinessCheck(
        "discord_target",
        passed,
        "Discord target is configured"
        if passed
        else "Discord target is missing or malformed",
    )


def _bot_state_check(state: Any) -> ReadinessCheck:
    passed = (
        isinstance(state, BotState)
        and state.active_client_order_id is None
        and not state.untracked_order
    )
    return ReadinessCheck(
        "no_pending_order",
        passed,
        "bot state has no active or untracked order"
        if passed
        else "bot state is invalid or has an active or untracked order",
    )


def _api_key_check(env: Mapping[str, str]) -> ReadinessCheck:
    names = ("BITHUMB_ACCESS_KEY", "BITHUMB_SECRET_KEY")
    missing = tuple(name for name in names if not isinstance(env.get(name), str) or not env[name])
    return ReadinessCheck(
        "api_key_names",
        not missing,
        "required API key names are present"
        if not missing
        else "missing required API key names: " + ", ".join(missing),
    )


def _live_flags_off_check(env: Mapping[str, str]) -> ReadinessCheck:
    mode = env.get("TRADING_MODE")
    live = env.get("BITHUMB_LIVE_TRADING")
    mode_off = mode is None or mode.strip().lower() == "paper"
    live_off = live is None or live.strip().lower() in _FALSE_VALUES
    passed = mode_off and live_off
    return ReadinessCheck(
        "live_flags_off",
        passed,
        "live trading flags remain off"
        if passed
        else "one or more live trading flags are enabled or malformed",
    )


def _mcp_check(probe: ReadOnlyProbe, market: str) -> ReadinessCheck:
    try:
        if not isinstance(market, str) or _MARKET_PATTERN.fullmatch(market) is None:
            raise ValueError("invalid market")
        result = probe.call_read_tool("account_get_order_chance", {"market": market})
        payload = _tool_payload(result)
        market_info = _object(payload.get("market"))
        if market_info.get("id") != market:
            raise ValueError("market mismatch")
        if market_info.get("state") != "active":
            raise ValueError("market is not active")
        order_sides = market_info.get("order_sides")
        bid_types = market_info.get("bid_types")
        ask_types = market_info.get("ask_types")
        if (
            not isinstance(order_sides, list)
            or not {"bid", "ask"}.issubset(order_sides)
            or not isinstance(bid_types, list)
            or "price" not in bid_types
            or not isinstance(ask_types, list)
            or "market" not in ask_types
        ):
            raise ValueError("required spot order capabilities are unavailable")
        bid = _object(market_info.get("bid"))
        ask = _object(market_info.get("ask"))
        bid_minimum = _decimal(bid.get("min_total"), positive=True)
        _decimal(ask.get("min_total"), positive=True)
        bid_fee = _decimal(payload.get("bid_fee"), positive=False)
        ask_fee = _decimal(payload.get("ask_fee"), positive=False)
        if bid_fee > Decimal("0.01") or ask_fee > Decimal("0.01"):
            raise ValueError("fee out of range")
        quote_currency, asset_currency = market.split("-", 1)
        if bid.get("currency") != quote_currency or ask.get("currency") != asset_currency:
            raise ValueError("market currency mismatch")
        bid_account = _object(payload.get("bid_account"))
        ask_account = _object(payload.get("ask_account"))
        if (
            bid_account.get("currency") != quote_currency
            or ask_account.get("currency") != asset_currency
        ):
            raise ValueError("account currency mismatch")
        balance = _decimal(bid_account.get("balance"), positive=False)
        _decimal(ask_account.get("balance"), positive=False)
        if balance < bid_minimum * (1 + bid_fee):
            raise ValueError("insufficient balance for exchange minimum and fee")
    except Exception:
        return ReadinessCheck(
            "mcp_account_probe",
            False,
            "read-only account probe was missing, malformed, or insufficient",
        )
    return ReadinessCheck(
        "mcp_account_probe",
        True,
        "read-only account probe passed sanitized checks",
    )


def _tool_payload(result: Any) -> Mapping[str, Any]:
    outer = _object(result)
    if outer.get("isError") is True:
        raise ValueError("tool error")
    content = outer.get("content")
    if not isinstance(content, list) or len(content) != 1:
        raise ValueError("invalid content")
    block = _object(content[0])
    if block.get("type") != "text" or not isinstance(block.get("text"), str):
        raise ValueError("invalid text block")
    payload = json.loads(block["text"])
    payload = _object(payload)
    for _ in range(2):
        if "data" not in payload:
            break
        payload = _object(payload["data"])
    if "data" in payload:
        raise ValueError("unsupported nested data wrapper")
    return payload


def _object(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("expected object")
    return value


def _decimal(value: Any, *, positive: bool) -> Decimal:
    if isinstance(value, (bool, float)):
        raise ValueError("inexact decimal")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid decimal") from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed == 0):
        raise ValueError("decimal out of range")
    return parsed
