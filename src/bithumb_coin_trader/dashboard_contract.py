"""Python TradingSnapshot v1 structural contract validator.

Mirror of dashboard/src/trading/snapshotValidation.ts.
Fail-closed: guarantees that neither the API server nor the offline snapshot
builder knowingly produces or serves malformed TradingSnapshot v1 data.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

VALID_MODES = {"OFF", "PAPER", "LIVE"}
VALID_SOURCE_KINDS = {"synthetic", "authoritative", "local_snapshot"}
VALID_MARKET_DATA = {"PENDING", "READY"}
VALID_ORDER_EXECUTION = {"DISABLED", "PAPER", "LIVE"}
VALID_RISK_GUARD = {"LOCKED", "ACTIVE"}
DATE_YMD_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_finite_num(val: Any) -> bool:
    if val is None or isinstance(val, bool):
        return False
    if not isinstance(val, (int, float)):
        return False
    return math.isfinite(val)


def _is_num_or_null(val: Any) -> bool:
    if val is None:
        return True
    return _is_finite_num(val)


def _is_non_neg_or_null(val: Any) -> bool:
    if val is None:
        return True
    return _is_finite_num(val) and val >= 0


def _is_valid_iso_timestamp(val: Any) -> bool:
    if not isinstance(val, str) or not val.strip():
        return False
    clean = val.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(clean)
    except Exception:
        return False
    return dt.tzinfo is not None


def _is_valid_date_ymd(val: Any) -> bool:
    if not isinstance(val, str) or not DATE_YMD_PATTERN.fullmatch(val):
        return False
    try:
        datetime.strptime(val, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def validate_portfolio_summary(val: Any, prefix: str = "portfolio") -> list[str]:
    errors: list[str] = []
    if not isinstance(val, dict):
        return [f"{prefix}: 객체 형식이어야 합니다."]

    fields = [
        "equity", "cash", "exposure", "todayPnl", "todayReturnPct",
        "totalPnl", "totalReturnPct", "realizedPnl", "unrealizedPnl", "fees",
    ]
    for f in fields:
        if f not in val or not _is_num_or_null(val[f]):
            errors.append(f"{prefix}.{f}: 유한한 숫자 또는 null이어야 합니다.")
    return errors


def validate_positions(val: Any, prefix: str = "positions") -> list[str]:
    errors: list[str] = []
    if not isinstance(val, list):
        return [f"{prefix}: 배열 형식이어야 합니다."]

    for idx, pos in enumerate(val):
        pfx = f"{prefix}[{idx}]"
        if not isinstance(pos, dict):
            errors.append(f"{pfx}: 객체 형식이어야 합니다.")
            continue

        if not isinstance(pos.get("id"), str) or not pos["id"].strip():
            errors.append(f"{pfx}.id: 비어있지 않은 문자열이어야 합니다.")
        if not isinstance(pos.get("asset"), str) or not pos["asset"].strip():
            errors.append(f"{pfx}.asset: 비어있지 않은 문자열이어야 합니다.")
        if not isinstance(pos.get("name"), str):
            errors.append(f"{pfx}.name: 문자열이어야 합니다.")
        if not isinstance(pos.get("pair"), str) or not pos["pair"].strip():
            errors.append(f"{pfx}.pair: 비어있지 않은 문자열이어야 합니다.")
        if pos.get("side") != "LONG":
            errors.append(f"{pfx}.side: 'LONG'이어야 합니다.")

        num_fields = ["entry", "current", "quantity", "exposure", "pnl", "pnlPct", "entryFee"]
        for f in num_fields:
            if f not in pos or not _is_num_or_null(pos[f]):
                errors.append(f"{pfx}.{f}: 유한한 숫자 또는 null이어야 합니다.")

        if not _is_valid_iso_timestamp(pos.get("openedAt")):
            errors.append(f"{pfx}.openedAt: 유효한 ISO 8601 타임스탬프 형식이어야 합니다.")

        strat = pos.get("strategy")
        if strat is not None and not isinstance(strat, str):
            errors.append(f"{pfx}.strategy: 문자열 또는 null이어야 합니다.")

    return errors


def validate_trades(val: Any, prefix: str = "recentTrades") -> list[str]:
    errors: list[str] = []
    if not isinstance(val, list):
        return [f"{prefix}: 배열 형식이어야 합니다."]

    for idx, trade in enumerate(val):
        pfx = f"{prefix}[{idx}]"
        if not isinstance(trade, dict):
            errors.append(f"{pfx}: 객체 형식이어야 합니다.")
            continue

        if not isinstance(trade.get("id"), str) or not trade["id"].strip():
            errors.append(f"{pfx}.id: 비어있지 않은 문자열이어야 합니다.")
        if not isinstance(trade.get("asset"), str) or not trade["asset"].strip():
            errors.append(f"{pfx}.asset: 비어있지 않은 문자열이어야 합니다.")
        if not isinstance(trade.get("pair"), str) or not trade["pair"].strip():
            errors.append(f"{pfx}.pair: 비어있지 않은 문자열이어야 합니다.")
        if trade.get("side") != "LONG":
            errors.append(f"{pfx}.side: 'LONG'이어야 합니다.")

        num_fields = ["entry", "exit", "quantity", "pnl", "pnlPct", "fee"]
        for f in num_fields:
            if f not in trade or not _is_num_or_null(trade[f]):
                errors.append(f"{pfx}.{f}: 유한한 숫자 또는 null이어야 합니다.")

        has_opened = _is_valid_iso_timestamp(trade.get("openedAt"))
        has_closed = _is_valid_iso_timestamp(trade.get("closedAt"))
        if not has_opened:
            errors.append(f"{pfx}.openedAt: 유효한 ISO 8601 타임스탬프 형식이어야 합니다.")
        if not has_closed:
            errors.append(f"{pfx}.closedAt: 유효한 ISO 8601 타임스탬프 형식이어야 합니다.")

        if has_opened and has_closed:
            op_dt = datetime.fromisoformat(trade["openedAt"].replace("Z", "+00:00"))
            cl_dt = datetime.fromisoformat(trade["closedAt"].replace("Z", "+00:00"))
            if op_dt > cl_dt:
                errors.append(f"{pfx}: 진입 시각(openedAt)이 청산 시각(closedAt)보다 미래일 수 없습니다.")

        reason = trade.get("exitReason")
        if reason is not None and not isinstance(reason, str):
            errors.append(f"{pfx}.exitReason: 문자열 또는 null이어야 합니다.")

    return errors


def validate_bot_status(val: Any, prefix: str = "botStatus") -> list[str]:
    errors: list[str] = []
    if not isinstance(val, dict):
        return [f"{prefix}: 객체 형식이어야 합니다."]

    mode = val.get("mode")
    if mode not in VALID_MODES:
        errors.append(f"{prefix}.mode: 'OFF', 'PAPER', 'LIVE' 중 하나여야 합니다.")

    strategy = val.get("strategy")
    if strategy is not None and not isinstance(strategy, str):
        errors.append(f"{prefix}.strategy: 문자열 또는 null이어야 합니다.")

    market_data = val.get("marketData")
    if market_data not in VALID_MARKET_DATA:
        errors.append(f"{prefix}.marketData: 'PENDING', 'READY' 중 하나여야 합니다.")

    order_execution = val.get("orderExecution")
    if order_execution not in VALID_ORDER_EXECUTION:
        errors.append(f"{prefix}.orderExecution: 'DISABLED', 'PAPER', 'LIVE' 중 하나여야 합니다.")

    risk_guard = val.get("riskGuard")
    if risk_guard not in VALID_RISK_GUARD:
        errors.append(f"{prefix}.riskGuard: 'LOCKED', 'ACTIVE' 중 하나여야 합니다.")

    last_activity = val.get("lastActivity")
    if last_activity is not None and not _is_valid_iso_timestamp(last_activity):
        errors.append(f"{prefix}.lastActivity: 유효한 ISO 8601 타임스탬프 또는 null이어야 합니다.")

    non_neg_fields = ["uptimeSeconds", "todayTrades", "errors"]
    for f in non_neg_fields:
        if f not in val or not _is_num_or_null(val[f]):
            errors.append(f"{prefix}.{f}: 유한한 숫자 또는 null이어야 합니다.")
        elif val[f] is not None and val[f] < 0:
            errors.append(f"{prefix}.{f}: 음수일 수 없습니다. (값: {val[f]})")

    return errors


def validate_performance(val: Any, prefix: str = "performance") -> list[str]:
    errors: list[str] = []
    if not isinstance(val, dict):
        return [f"{prefix}: 객체 형식이어야 합니다."]

    fields = ["return7d", "return30d", "totalReturn", "maxDrawdown", "winRate", "profitFactor", "averageTrade"]
    for f in fields:
        if f not in val or not _is_num_or_null(val[f]):
            errors.append(f"{prefix}.{f}: 유한한 숫자 또는 null이어야 합니다.")
    return errors


def validate_today(val: Any, prefix: str = "today") -> list[str]:
    errors: list[str] = []
    if not isinstance(val, dict):
        return [f"{prefix}: 객체 형식이어야 합니다."]

    num_fields = ["realizedPnl", "unrealizedPnl", "fees", "exposurePct"]
    for f in num_fields:
        if f not in val or not _is_num_or_null(val[f]):
            errors.append(f"{prefix}.{f}: 유한한 숫자 또는 null이어야 합니다.")

    non_neg_fields = ["trades", "wins", "losses"]
    for f in non_neg_fields:
        if f not in val or not _is_num_or_null(val[f]):
            errors.append(f"{prefix}.{f}: 유한한 숫자 또는 null이어야 합니다.")
        elif val[f] is not None and val[f] < 0:
            errors.append(f"{prefix}.{f}: 음수일 수 없습니다. (값: {val[f]})")

    return errors


def validate_daily_baseline(val: Any, prefix: str = "dailyBaseline") -> list[str]:
    errors: list[str] = []
    if val is None:
        return errors

    if not isinstance(val, dict):
        return [f"{prefix}: 객체 또는 null이어야 합니다."]

    if "equity" not in val or not _is_num_or_null(val["equity"]):
        errors.append(f"{prefix}.equity: 유한한 숫자 또는 null이어야 합니다.")
    if "netCashFlow" not in val or not _is_num_or_null(val["netCashFlow"]):
        errors.append(f"{prefix}.netCashFlow: 유한한 숫자 또는 null이어야 합니다.")
    if not _is_valid_date_ymd(val.get("tradingDay")):
        errors.append(f"{prefix}.tradingDay: 유효한 YYYY-MM-DD 날짜 형식이어야 합니다.")
    if val.get("timeZone") != "Asia/Seoul":
        errors.append(f"{prefix}.timeZone: 반드시 'Asia/Seoul'이어야 합니다.")

    return errors


def validate_trading_snapshot(raw: Any) -> list[str]:
    """Validate raw dictionary against TradingSnapshot v1 wire contract.

    Returns an empty list if valid, or a list of descriptive Korean error strings.
    """
    errors: list[str] = []

    if not isinstance(raw, dict):
        return ["스냅샷 최상위 데이터가 올바른 JSON 객체가 아닙니다."]

    # 0. schemaVersion (exact wire contract key required; snake_case not permitted)
    if "schemaVersion" not in raw:
        errors.append("schemaVersion: 필드가 누락되었습니다 (지원 버전: 1).")
    elif raw["schemaVersion"] != 1:
        errors.append(f"schemaVersion: 지원하지 않는 스키마 버전입니다. (지원 버전: 1, 입력값: {raw['schemaVersion']})")

    # 1. timestamp
    if not _is_valid_iso_timestamp(raw.get("timestamp")):
        errors.append("timestamp: 유효하지 않거나 누락된 ISO 8601 타임스탬프 형식입니다.")

    # 2. mode
    mode = raw.get("mode")
    if mode not in VALID_MODES:
        errors.append(f"mode: 'OFF', 'PAPER', 'LIVE' 중 하나여야 합니다. (입력값: {mode})")

    # 3. source
    source = raw.get("source")
    if not isinstance(source, dict):
        errors.append("source: 데이터 출처 메타데이터 객체가 누락되었습니다.")
    else:
        kind = source.get("kind")
        if kind not in VALID_SOURCE_KINDS:
            errors.append(f"source.kind: 'synthetic', 'authoritative', 'local_snapshot' 중 하나여야 합니다. (입력값: {kind})")
        label = source.get("label")
        if not isinstance(label, str) or not label.strip():
            errors.append("source.label: 비어있지 않은 문자열이어야 합니다.")

    # 4. portfolio
    if "portfolio" not in raw:
        errors.append("portfolio: 필드가 누락되었습니다.")
    else:
        errors.extend(validate_portfolio_summary(raw["portfolio"], "portfolio"))

    # 5. positions
    if "positions" not in raw:
        errors.append("positions: 필드가 누락되었습니다.")
    else:
        errors.extend(validate_positions(raw["positions"], "positions"))

    # 6. recentTrades
    if "recentTrades" not in raw:
        errors.append("recentTrades: 필드가 누락되었습니다.")
    else:
        errors.extend(validate_trades(raw["recentTrades"], "recentTrades"))

    # 7. equityCurve
    if "equityCurve" not in raw or not isinstance(raw["equityCurve"], list):
        errors.append("equityCurve: 배열 형식이어야 합니다.")
    else:
        for idx, pt in enumerate(raw["equityCurve"]):
            pfx = f"equityCurve[{idx}]"
            if not isinstance(pt, dict):
                errors.append(f"{pfx}: 객체 형식이어야 합니다.")
                continue
            if not _is_valid_iso_timestamp(pt.get("timestamp")):
                errors.append(f"{pfx}.timestamp: 유효한 ISO 8601 타임스탬프여야 합니다.")
            if "equity" not in pt or not _is_num_or_null(pt["equity"]):
                errors.append(f"{pfx}.equity: 유한한 숫자 또는 null이어야 합니다.")
            if "returnPct" not in pt or not _is_num_or_null(pt["returnPct"]):
                errors.append(f"{pfx}.returnPct: 유한한 숫자 또는 null이어야 합니다.")
            if "drawdownPct" not in pt or not _is_num_or_null(pt["drawdownPct"]):
                errors.append(f"{pfx}.drawdownPct: 유한한 숫자 또는 null이어야 합니다.")

    # 8. dailyPerformance
    if "dailyPerformance" not in raw or not isinstance(raw["dailyPerformance"], list):
        errors.append("dailyPerformance: 배열 형식이어야 합니다.")
    else:
        for idx, dp in enumerate(raw["dailyPerformance"]):
            pfx = f"dailyPerformance[{idx}]"
            if not isinstance(dp, dict):
                errors.append(f"{pfx}: 객체 형식이어야 합니다.")
                continue
            if not _is_valid_date_ymd(dp.get("date")):
                errors.append(f"{pfx}.date: 유효한 YYYY-MM-DD 날짜 형식이어야 합니다.")
            if "pnl" not in dp or not _is_num_or_null(dp["pnl"]):
                errors.append(f"{pfx}.pnl: 유한한 숫자 또는 null이어야 합니다.")
            if "returnPct" not in dp or not _is_num_or_null(dp["returnPct"]):
                errors.append(f"{pfx}.returnPct: 유한한 숫자 또는 null이어야 합니다.")

    # 9. botStatus
    if "botStatus" not in raw:
        errors.append("botStatus: 필드가 누락되었습니다.")
    else:
        errors.extend(validate_bot_status(raw["botStatus"], "botStatus"))

    # 10. performance
    if "performance" not in raw:
        errors.append("performance: 필드가 누락되었습니다.")
    else:
        errors.extend(validate_performance(raw["performance"], "performance"))

    # 11. today
    if "today" not in raw:
        errors.append("today: 필드가 누락되었습니다.")
    else:
        errors.extend(validate_today(raw["today"], "today"))

    # 12. dailyBaseline
    if "dailyBaseline" not in raw:
        errors.append("dailyBaseline: 필드가 누락되었습니다 (DailyBaseline 객체 또는 null 필요).")
    else:
        errors.extend(validate_daily_baseline(raw["dailyBaseline"], "dailyBaseline"))

    return errors
