from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class TradingMode(StrEnum):
    PAPER = "paper"
    LIVE = "live"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _parse_allowlist(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    items = []
    for item in raw.split(","):
        clean = item.strip().upper().removeprefix("KRW-")
        if clean:
            items.append(clean)
    return tuple(items)


@dataclass(frozen=True, slots=True)
class TradingSettings:
    initial_capital_krw: int = 20_000
    fee_rate: float = 0.0025
    slippage_bps: float = 5.0
    allocation_fraction: float = 0.50
    minimum_order_krw: int = 5_000
    maximum_order_krw: int = 10_000
    maximum_daily_entries: int = 1
    cash_reserve_krw: int = 5_000
    mode: TradingMode = TradingMode.PAPER
    live_trading_enabled: bool = False
    manual_holdings_allowlist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.initial_capital_krw <= 0:
            raise ValueError("initial capital must be positive")
        if not 0 <= self.fee_rate < 0.1:
            raise ValueError("fee rate is outside the supported range")
        if not 0 <= self.slippage_bps <= 1_000:
            raise ValueError("slippage is outside the supported range")
        if not 0 < self.allocation_fraction <= 1:
            raise ValueError("allocation fraction must be in (0, 1]")
        if self.minimum_order_krw <= 0 or self.cash_reserve_krw < 0:
            raise ValueError("order minimum and reserve must be non-negative")
        if self.maximum_order_krw < self.minimum_order_krw:
            raise ValueError("maximum order cannot be less than minimum order")
        if self.maximum_daily_entries <= 0:
            raise ValueError("maximum daily entries must be positive")
        if self.initial_capital_krw - self.cash_reserve_krw < self.minimum_order_krw:
            raise ValueError("capital after reserve is below the minimum order")
        if self.mode is TradingMode.LIVE and not self.live_trading_enabled:
            raise ValueError("live mode requires BITHUMB_LIVE_TRADING=true")

    @classmethod
    def from_env(cls) -> "TradingSettings":
        mode = TradingMode(os.getenv("TRADING_MODE", TradingMode.PAPER.value).strip().lower())
        return cls(
            initial_capital_krw=int(os.getenv("INITIAL_CAPITAL_KRW", "20000")),
            fee_rate=float(os.getenv("BITHUMB_FEE_RATE", "0.0025")),
            slippage_bps=float(os.getenv("SLIPPAGE_BPS", "5")),
            allocation_fraction=float(os.getenv("ALLOCATION_FRACTION", "0.50")),
            minimum_order_krw=int(os.getenv("MINIMUM_ORDER_KRW", "5000")),
            maximum_order_krw=int(os.getenv("MAXIMUM_ORDER_KRW", "10000")),
            maximum_daily_entries=int(os.getenv("MAXIMUM_DAILY_ENTRIES", "1")),
            cash_reserve_krw=int(os.getenv("CASH_RESERVE_KRW", "5000")),
            mode=mode,
            live_trading_enabled=_env_bool("BITHUMB_LIVE_TRADING", False),
            manual_holdings_allowlist=_parse_allowlist(os.getenv("MANUAL_HOLDINGS_ALLOWLIST")),
        )
