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
        # Suppress raw internal execution noise (BLOCKED, ACCEPTED, PENDING) from Discord
        if notification.event in {TradeEvent.BLOCKED, TradeEvent.ACCEPTED, TradeEvent.PENDING}:
            return True
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


class SilentNotifier:
    """No-op sink to suppress low-level raw debug notifications when using rich custom alerts."""

    def send(self, notification: Any) -> bool:
        return True


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


# ── Rich Mobile Discord Briefings ─────────────────────────────

DISCORD_TARGET = "discord:1521513150682234900"
HERMES_BIN = str(Path.home() / ".local" / "bin" / "hermes")


def send_discord_message(text: str, target: str = DISCORD_TARGET) -> bool:
    """Send markdown text message to Discord target via Hermes."""
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".md") as handle:
            handle.write(text)
            path = Path(handle.name)
        result = subprocess.run(
            [HERMES_BIN, "send", "-t", target, "-f", str(path)],
            check=False,
            cwd=tempfile.gettempdir(),
            timeout=30.0,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception as exc:
        print(f"⚠️ Discord send error: {exc}")
        return False
    finally:
        if path is not None and path.exists():
            path.unlink(missing_ok=True)


def notify_buy_entry(
    market: str,
    price: float,
    amount_krw: int,
    volume: str,
    confidence: float,
    bid_ratio: float,
    take_profit: float,
    stop_loss: float,
) -> bool:
    """Send rich Buy Entry notification."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    coin_name = market.replace("KRW-", "")
    text = f"""## 🟢 [BITHUMB] 매수 진입 알림 ({coin_name})
> ⏱️ **체결시각**: `{now_str}`
> 🎯 **종목**: **`{market}`**

```yaml
진입단가: {price:,.0f} KRW
매수금액: {amount_krw:,} KRW
체결수량: {float(volume):.4f} {coin_name}
AI 확신도: {confidence:.1f}% (TARO/DIANA/NOVA/VIBE)
호가 매수벽: {bid_ratio*100:.1f}% (30호가 Imbalance)
```

- 🎯 **1차 목표가 (TP +4.0%)**: `{take_profit:,.0f} KRW`
- ⛔ **손절 기준선 (SL -2.0%)**: `{stop_loss:,.0f} KRW`
- 📈 **트레일링 스탑**: 고점 +1% 돌파 시 고점 대비 -1.5% 자동 추종
"""
    return send_discord_message(text)


def notify_sell_exit(
    market: str,
    price: float,
    volume: str,
    amount_krw: float,
    pnl_krw: float,
    pnl_pct: float,
    reason: str,
    total_capital: float,
    target_capital: float = 45000.0,
) -> bool:
    """Send rich Sell Exit & P&L notification."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    coin_name = market.replace("KRW-", "")
    status_tag = "익절 성공 🎉" if pnl_krw >= 0 else "손절 방어 🛡️"

    progress = min(max(total_capital / target_capital * 100.0, 0.0), 100.0)
    filled_bars = int(progress / 10)
    bar_str = "█" * filled_bars + "░" * (10 - filled_bars)

    text = f"""## 🔴 [BITHUMB] 포지션 청산 알림 ({status_tag})
> ⏱️ **청산시각**: `{now_str}`
> 🎯 **종목**: **`{market}`**

```yaml
청산단가: {price:,.0f} KRW
청산금액: {amount_krw:,.0f} KRW
실현손익: {pnl_krw:+,.0f} KRW ({pnl_pct:+.2f}%)
청산사유: {reason}
```

### 💰 포트폴리오 현황
- **현재 총 자산**: **`{total_capital:,.0f} KRW`**
- **9/1 목표 (45,000원)**: `[{bar_str}] {progress:.1f}%`
- **남은 목표 금액**: `{target_capital - total_capital:+,.0f} KRW`
"""
    return send_discord_message(text)


def notify_hourly_briefing(
    total_capital: float,
    cash_available: float,
    active_market: str,
    active_price: float,
    entry_price: float,
    active_pnl_pct: float,
    active_val_krw: float,
    top_candidates: list[dict[str, Any]],
    target_capital: float = 45000.0,
) -> bool:
    """Send periodic briefing with portfolio and market rankings."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    progress = min(max(total_capital / target_capital * 100.0, 0.0), 100.0)
    filled_bars = int(progress / 10)
    bar_str = "█" * filled_bars + "░" * (10 - filled_bars)

    pos_info = "현재 현금 100% 보유 대기 중 (다음 1위 코인 탐색)"
    if active_market and entry_price > 0:
        pos_info = f"**{active_market}** ({active_pnl_pct:+.2f}%) | 평가금: `{active_val_krw:,.0f} KRW`"

    rank_lines = []
    for i, c in enumerate(top_candidates[:3]):
        tag = "⭐ 1위" if i == 0 else f"#{i+1}"
        rank_lines.append(f"- {tag} **{c['market']}**: 확신도 `{c['confidence']:.1f}%` | 호가매수벽 `{c['bid_ratio']:.1f}%` | {c['status']}")
    ranks_text = "\n".join(rank_lines) if rank_lines else "- 스캔 진행 중"

    text = f"""## 📊 [BITHUMB] 정기 트레이딩 브리핑
> ⏱️ **기준시각**: `{now_str}`

### 💰 자산 및 목표 현황
- **총 자산**: **`{total_capital:,.0f} KRW`** (가용 현금: `{cash_available:,.0f} KRW`)
- **목표 달성률**: `[{bar_str}] {progress:.1f}%` (목표: 45,000 KRW)
- **보유 포지션**: {pos_info}

### 📡 빗썸 다이내믹 25-유니버스 실시간 랭킹 Top 3
{ranks_text}

---
*24시간 무중단 자율 운용 중* 🤖
"""
    return send_discord_message(text)
