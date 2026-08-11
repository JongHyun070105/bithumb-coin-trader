"""Discord notifications delivered through the locally configured Hermes gateway."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Mapping


TARGET_ENV = "BITHUMB_DISCORD_TARGET"
DEFAULT_SOURCE_CRON_ENV = "TOSS_MONITOR_DISCORD_TARGET"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "bithumb-coin-trader" / "env"
_TARGET_PATTERN = re.compile(r"^discord:[0-9]{6,30}$")


class TradeEvent(StrEnum):
    TEST = "test"
    PAPER = "paper"
    BLOCKED = "blocked"
    ACCEPTED = "accepted"
    AMBIGUOUS = "ambiguous"
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class TradeNotification:
    event: TradeEvent
    market: str
    side: str | None = None
    client_order_id: str | None = None
    notional_krw: str | None = None
    volume: str | None = None
    detail: str = ""
    occurred_at: datetime | None = None


_EVENT_LABELS = {
    TradeEvent.TEST: "Finance Chat 연결 테스트 (실주문 없음)",
    TradeEvent.PAPER: "페이퍼 일일 실행 (실주문 없음)",
    TradeEvent.BLOCKED: "주문 차단",
    TradeEvent.ACCEPTED: "주문 접수 (체결 아님)",
    TradeEvent.AMBIGUOUS: "주문 결과 불명확 (재주문 금지)",
    TradeEvent.PENDING: "주문 미체결 대기",
    TradeEvent.FILLED: "주문 체결 확인",
    TradeEvent.CANCELLED: "주문 취소 확인",
}


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def configured_discord_target(
    *, env: Mapping[str, str] | None = None, env_path: Path | None = None
) -> str | None:
    source = os.environ if env is None else env
    target = source.get(TARGET_ENV, "").strip()
    if not target:
        target = _read_env_file(env_path or DEFAULT_CONFIG_PATH).get(TARGET_ENV, "").strip()
    if not target:
        return None
    if not _TARGET_PATTERN.fullmatch(target):
        raise ValueError(f"{TARGET_ENV} must look like discord:<numeric_channel_id>")
    return target


def target_from_crontab(
    crontab_text: str, *, source_env: str = DEFAULT_SOURCE_CRON_ENV
) -> str:
    assignment = re.compile(rf"^{re.escape(source_env)}=(discord:[0-9]{{6,30}})\s*$")
    for line in crontab_text.splitlines():
        matched = assignment.fullmatch(line.strip())
        if matched:
            return matched.group(1)
    raise ValueError(f"{source_env} was not found in crontab")


def save_local_target(target: str, *, env_path: Path | None = None) -> Path:
    if not _TARGET_PATTERN.fullmatch(target):
        raise ValueError("Discord target must look like discord:<numeric_channel_id>")
    destination = env_path or DEFAULT_CONFIG_PATH
    original = destination.read_text(encoding="utf-8").splitlines() if destination.exists() else []
    replacement = f"{TARGET_ENV}={target}"
    lines: list[str] = []
    replaced = False
    for line in original:
        if line.strip().startswith(f"{TARGET_ENV}="):
            if not replaced:
                lines.append(replacement)
                replaced = True
            continue
        lines.append(line)
    if not replaced:
        lines.append(replacement)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, destination)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return destination


def format_trade_notification(notification: TradeNotification) -> str:
    occurred_at = notification.occurred_at or datetime.now(UTC)
    lines = [
        f"[빗썸 자동매매] {_EVENT_LABELS[notification.event]}",
        f"- 시간: {occurred_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- 마켓: {notification.market}",
    ]
    if notification.side:
        lines.append(f"- 방향: {'매수' if notification.side == 'bid' else '매도'}")
    if notification.notional_krw:
        lines.append(f"- 주문금액: {notification.notional_krw}원")
    if notification.volume:
        lines.append(f"- 수량: {notification.volume}")
    if notification.client_order_id:
        lines.append(f"- client_order_id: {notification.client_order_id}")
    if notification.detail:
        lines.append(f"- 상태: {notification.detail}")
    if notification.event is TradeEvent.AMBIGUOUS:
        lines.append("- 안전조치: 동일 주문 자동 재시도 금지 / 주문 조회 전 신규 주문 차단")
    return "\n".join(lines)


class DiscordNotifier:
    """Best-effort notifier whose failure never changes an order outcome."""

    def __init__(
        self,
        *,
        target: str | None = None,
        env: Mapping[str, str] | None = None,
        env_path: Path | None = None,
        hermes_bin: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.target = target or configured_discord_target(env=env, env_path=env_path)
        if self.target is not None and not _TARGET_PATTERN.fullmatch(self.target):
            raise ValueError("Discord target must look like discord:<numeric_channel_id>")
        selected_hermes = hermes_bin or str(Path.home() / ".local" / "bin" / "hermes")
        hermes_path = Path(selected_hermes).expanduser()
        if not hermes_path.is_absolute():
            raise ValueError("Hermes executable must be an absolute path")
        self.hermes_bin = str(hermes_path)
        self.timeout = timeout

    def send(self, notification: TradeNotification) -> bool:
        if not self.target:
            return False
        message = format_trade_notification(notification)
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", delete=False, suffix=".md"
            ) as handle:
                handle.write(message)
                path = Path(handle.name)
            result = subprocess.run(
                [
                    self.hermes_bin,
                    "send",
                    "--quiet",
                    "--to",
                    self.target,
                    "--file",
                    str(path),
                ],
                check=False,
                cwd=tempfile.gettempdir(),
                env=_minimal_hermes_env(),
                timeout=self.timeout,
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
        finally:
            if path is not None:
                path.unlink(missing_ok=True)


def _minimal_hermes_env() -> dict[str, str]:
    environment = {
        "HOME": str(Path.home()),
        "PATH": os.defpath,
    }
    for name in ("LANG", "LC_ALL", "TMPDIR"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def status_test_notification() -> TradeNotification:
    return TradeNotification(
        event=TradeEvent.TEST,
        market="KRW-BTC",
        detail="Discord finance-chat 연결 테스트 / 실주문 없음",
    )
