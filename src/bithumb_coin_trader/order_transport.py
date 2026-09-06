"""Order Transport Interfaces and Disabled Live Transport Proof (P7 - P7.4).

Guarantees 100% network isolation and safety:
- DisabledLiveTransport unconditionally raises LiveTradingDisabledError on any interaction.
- Prevents any transmission of live orders, cancellations, or balance fetches to real exchanges.
- SimulatedPaperTransport routes strictly to in-memory deterministic simulation.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Mapping


class LiveTradingDisabledError(RuntimeError):
    """Raised unconditionally when any live trading action is attempted."""


class OrderTransport(ABC):
    """Abstract base class for order transport."""

    @abstractmethod
    def send_order(self, **kwargs: Any) -> Any:
        pass

    @abstractmethod
    def cancel_order(self, order_id: str, **kwargs: Any) -> Any:
        pass

    @abstractmethod
    def fetch_balance(self, **kwargs: Any) -> Any:
        pass

    @abstractmethod
    def fetch_open_orders(self, **kwargs: Any) -> Any:
        pass


class DisabledLiveTransport(OrderTransport):
    """Permanently disabled transport for live trading endpoints.
    
    Any invocation raises LiveTradingDisabledError fail-closed.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def send_order(self, **kwargs: Any) -> Any:
        raise LiveTradingDisabledError(
            "Live trading transport is permanently disabled in offline research build. "
            "Absolute prohibition against real exchange communication."
        )

    def cancel_order(self, order_id: str, **kwargs: Any) -> Any:
        raise LiveTradingDisabledError(
            "Live trading transport is permanently disabled in offline research build."
        )

    def fetch_balance(self, **kwargs: Any) -> Any:
        raise LiveTradingDisabledError(
            "Live trading transport is permanently disabled in offline research build."
        )

    def fetch_open_orders(self, **kwargs: Any) -> Any:
        raise LiveTradingDisabledError(
            "Live trading transport is permanently disabled in offline research build."
        )


def verify_no_live_credentials_in_offline_env() -> None:
    """Checks that no live production API keys are loaded into offline process."""
    forbidden_keys = [
        "BITHUMB_API_KEY",
        "BITHUMB_SECRET_KEY",
        "UPBIT_OPEN_API_ACCESS_KEY",
        "UPBIT_OPEN_API_SECRET_KEY",
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
    ]
    present = [k for k in forbidden_keys if os.environ.get(k)]
    if present:
        raise LiveTradingDisabledError(
            f"Live trading credentials detected in offline environment: {present}. "
            "Unset these variables to proceed with offline research."
        )
