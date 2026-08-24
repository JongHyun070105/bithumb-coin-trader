from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


STATE_VERSION = 1


@dataclass(frozen=True, slots=True)
class BotState:
    version: int = STATE_VERSION
    position: str = "flat"
    position_volume: str = "0"
    last_signal_at: str | None = None
    active_client_order_id: str | None = None
    pending_order_side: str | None = None
    pending_market: str | None = None
    pending_order_volume: str | None = None
    untracked_order: bool = False
    position_policy_version: int = 0

    def __post_init__(self) -> None:
        if self.position not in {"flat", "long"}:
            raise ValueError("state position must be flat or long")
        if not isinstance(self.position_volume, str):
            raise ValueError("position_volume must be an exact decimal string")
        try:
            volume = Decimal(self.position_volume)
        except InvalidOperation as exc:
            raise ValueError("position_volume must be a valid decimal") from exc
        if not volume.is_finite() or volume < 0:
            raise ValueError("position_volume must be finite and non-negative")
        if self.position == "flat" and volume != 0:
            raise ValueError("flat state must have zero position_volume")
        if self.position == "long" and volume <= 0:
            raise ValueError("long state must have positive position_volume")
        if (
            isinstance(self.position_policy_version, bool)
            or not isinstance(self.position_policy_version, int)
            or self.position_policy_version < 0
        ):
            raise ValueError("position_policy_version must be a non-negative integer")
        if self.position == "flat" and self.position_policy_version != 0:
            raise ValueError("flat state cannot retain a position policy version")
        if self.active_client_order_id is None:
            if (
                self.pending_order_side is not None
                or self.pending_market is not None
                or self.pending_order_volume is not None
            ):
                raise ValueError("pending order metadata requires an active_client_order_id")
        elif self.pending_order_side not in {"bid", "ask"}:
            raise ValueError("active order requires pending_order_side bid or ask")
        elif not isinstance(self.pending_market, str) or not self.pending_market:
            raise ValueError("active order requires pending_market")
        elif self.pending_order_side == "bid" and self.pending_order_volume is not None:
            raise ValueError("pending buy cannot declare a base volume")
        elif self.pending_order_side == "ask" and self.pending_order_volume is not None:
            if not isinstance(self.pending_order_volume, str):
                raise ValueError("pending sell order volume must be an exact decimal string")
            try:
                pending_volume = Decimal(self.pending_order_volume)
            except InvalidOperation as exc:
                raise ValueError("pending order volume must be a valid decimal") from exc
            if not pending_volume.is_finite() or pending_volume <= 0:
                raise ValueError("pending order volume must be finite and positive")
            if self.position != "long" or pending_volume > volume:
                raise ValueError("pending sell volume cannot exceed the tracked position")


def load_state(path: Path) -> BotState:
    if not path.exists():
        return BotState()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != STATE_VERSION:
        raise ValueError("unsupported state version")
    allowed = {field for field in BotState.__dataclass_fields__}
    if set(payload) - allowed:
        raise ValueError("state contains unknown fields")
    return BotState(**payload)


def save_state(path: Path, state: BotState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(asdict(state), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def append_event(path: Path, event: str, details: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "observed_at": datetime.now(UTC).isoformat(),
        "event": event,
        "details": details,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
