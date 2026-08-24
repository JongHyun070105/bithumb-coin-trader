"""
AI Brain Memory System (Antigravity LLM 지식 주입 모듈)
────────────────────────────────────────────────────
메인 에이전트(Antigravity)와 서브 에이전트들이 심층 추론(Reasoning)하여
도출한 전략적 지침(ai_strategy_memory.json)을 실시간 트레이더에 주입합니다.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_PATH = PROJECT_ROOT / "state"
AI_MEMORY_PATH = STATE_PATH / "ai_strategy_memory.json"
EVOLUTION_JOURNAL_PATH = STATE_PATH / "evolution_journal.md"
LIVE_AI_CONFIG_ENV = "BITHUMB_ENABLE_AI_LIVE_CONFIG"
_MARKET_PATTERN = re.compile(r"^KRW-[A-Z0-9]{1,20}$")
_MARKET_REGIMES = {
    "BULL_MOMENTUM",
    "BEAR_DEFENSE",
    "NEUTRAL_CHOP",
    "VOLATILE_CHOP",
    "VOLATILE_SHAKEOUT",
}


@dataclass(slots=True)
class AIStrategyMemory:
    UNTRUSTED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "market_regime",
            "market_regime_summary",
            "min_coin_price_krw",
            "min_entry_confidence",
            "loss_cooldown_minutes",
            "breakeven_lock_pct",
            "preferred_sectors",
            "banned_markets",
            "market_score_biases",
            "strategic_commandments",
            "post_mortem_lessons",
        }
    )

    version: int = 1
    last_updated: str = ""
    analyst_model: str = "Antigravity Super-Agent (Google DeepMind)"
    market_regime: str = "NEUTRAL_CHOP" # "BULL_MOMENTUM", "BEAR_DEFENSE", "NEUTRAL_CHOP", "VOLATILE_SHAKEOUT"
    market_regime_summary: str = ""
    min_coin_price_krw: float = 50.0 # 잡코인 차단 기준
    min_entry_confidence: float = 75.0 # 최소 진입 확신도
    loss_cooldown_minutes: int = 15 # 손절 후 시장 휴식 시간
    breakeven_lock_pct: float = 1.0 # 본전 스탑 가동 기준 (+1.0%)
    
    preferred_sectors: List[str] = field(default_factory=list)
    banned_markets: List[str] = field(default_factory=list)
    market_score_biases: Dict[str, float] = field(default_factory=dict)
    
    strategic_commandments: List[str] = field(default_factory=list)
    post_mortem_lessons: List[str] = field(default_factory=list)

    def __post_init__(self):
        _require_int("version", self.version, 1, 100)
        _require_text("last_updated", self.last_updated, 64, allow_empty=True)
        _require_text("analyst_model", self.analyst_model, 160)
        if self.market_regime not in _MARKET_REGIMES:
            raise ValueError(f"market_regime must be one of {sorted(_MARKET_REGIMES)}")
        _require_text("market_regime_summary", self.market_regime_summary, 2_000, allow_empty=True)
        _require_number("min_coin_price_krw", self.min_coin_price_krw, 1.0, 100_000_000.0)
        _require_number("min_entry_confidence", self.min_entry_confidence, 50.0, 100.0)
        _require_int("loss_cooldown_minutes", self.loss_cooldown_minutes, 1, 1_440)
        _require_number("breakeven_lock_pct", self.breakeven_lock_pct, 0.1, 20.0)
        _require_text_list("preferred_sectors", self.preferred_sectors, 20, 80)
        _require_market_list("banned_markets", self.banned_markets, 100)
        _require_score_biases(self.market_score_biases)
        _require_text_list("strategic_commandments", self.strategic_commandments, 50, 1_000)
        _require_text_list("post_mortem_lessons", self.post_mortem_lessons, 50, 1_000)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "AIStrategyMemory":
        if not isinstance(data, Mapping):
            raise TypeError("AI strategy memory must be a JSON object")
        accepted_fields = {item.name for item in fields(cls) if item.init}
        unknown = set(data) - accepted_fields
        if unknown:
            raise ValueError(f"unknown AI strategy fields: {sorted(unknown)}")
        return cls(**dict(data))

    @classmethod
    def from_untrusted_mapping(
        cls,
        data: Mapping[str, Any],
        *,
        analyst_model: str,
        last_updated: str,
    ) -> "AIStrategyMemory":
        if not isinstance(data, Mapping):
            raise TypeError("Gemini strategy output must be a JSON object")
        supplied = set(data)
        missing = cls.UNTRUSTED_FIELDS - supplied
        unknown = supplied - cls.UNTRUSTED_FIELDS
        if missing or unknown:
            raise ValueError(
                f"invalid Gemini strategy fields; missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        return cls(
            **dict(data),
            version=1,
            analyst_model=analyst_model,
            last_updated=last_updated,
        )


def _require_text(name: str, value: Any, max_length: int, *, allow_empty: bool = False) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if (not allow_empty and not value.strip()) or len(value) > max_length:
        raise ValueError(f"{name} must contain 1..{max_length} characters")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        raise ValueError(f"{name} contains control characters")


def _require_number(name: str, value: Any, minimum: float, maximum: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    if not math.isfinite(float(value)) or not minimum <= float(value) <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


def _require_int(name: str, value: Any, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


def _require_text_list(name: str, values: Any, max_items: int, max_length: int) -> None:
    if not isinstance(values, list):
        raise TypeError(f"{name} must be a list")
    if len(values) > max_items:
        raise ValueError(f"{name} must contain at most {max_items} items")
    for value in values:
        _require_text(f"{name} item", value, max_length)
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must not contain duplicates")


def _require_market_list(name: str, values: Any, max_items: int) -> None:
    _require_text_list(name, values, max_items, 24)
    invalid = [market for market in values if not _MARKET_PATTERN.fullmatch(market)]
    if invalid:
        raise ValueError(f"{name} contains invalid KRW markets: {invalid}")


def _require_score_biases(values: Any) -> None:
    if not isinstance(values, dict):
        raise TypeError("market_score_biases must be an object")
    if len(values) > 100:
        raise ValueError("market_score_biases must contain at most 100 markets")
    for market, bias in values.items():
        if not isinstance(market, str) or not _MARKET_PATTERN.fullmatch(market):
            raise ValueError(f"invalid market_score_biases market: {market!r}")
        _require_number(f"market_score_biases[{market}]", bias, -20.0, 20.0)


def read_ai_memory() -> AIStrategyMemory:
    if AI_MEMORY_PATH.exists():
        data = json.loads(AI_MEMORY_PATH.read_text(encoding="utf-8"))
        return AIStrategyMemory.from_mapping(data)
    return AIStrategyMemory()


def live_ai_config_enabled(*, env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get(LIVE_AI_CONFIG_ENV, "").strip().lower() in {"1", "true", "yes"}


def load_ai_memory(*, env: Mapping[str, str] | None = None) -> AIStrategyMemory:
    """Load reviewed memory for live use only after an explicit environment opt-in."""
    if not live_ai_config_enabled(env=env):
        return AIStrategyMemory()
    return read_ai_memory()


def save_ai_memory(mem: AIStrategyMemory):
    if not isinstance(mem, AIStrategyMemory):
        raise TypeError("mem must be an AIStrategyMemory")
    # Re-validate mutable list/dict fields in case callers changed them after construction.
    mem.__post_init__()
    AI_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{AI_MEMORY_PATH.name}.", dir=AI_MEMORY_PATH.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(asdict(mem), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, AI_MEMORY_PATH)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def evaluate_with_ai_brain(market: str, base_confidence: float) -> Tuple[float, bool, List[str]]:
    """
    Apply Antigravity AI LLM Strategic Memory to real-time confidence.
    Returns: (adjusted_confidence, is_allowed, reason_tags)
    """
    mem = load_ai_memory()
    tags = []
    
    # 1. Banned Markets check
    if mem.banned_markets and market in mem.banned_markets:
        tags.append("🚫 AI 금지종목(사후복기 퇴출)")
        return 0.0, False, tags

    adjusted = base_confidence

    # 2. Market Score Bias
    if mem.market_score_biases and market in mem.market_score_biases:
        bias = mem.market_score_biases[market]
        adjusted += bias
        if bias > 0:
            tags.append(f"⭐ AI선호우량가산(+{bias:.0f}%)")
        else:
            tags.append(f"⚠️ AI요주의감점({bias:.0f}%)")

    adjusted = min(max(adjusted, 0.0), 100.0)
    return adjusted, True, tags
