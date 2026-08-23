"""
AI Brain Memory System (Antigravity LLM 지식 주입 모듈)
────────────────────────────────────────────────────
메인 에이전트(Antigravity)와 서브 에이전트들이 심층 추론(Reasoning)하여
도출한 전략적 지침(ai_strategy_memory.json)을 실시간 트레이더에 주입합니다.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_PATH = PROJECT_ROOT / "state"
AI_MEMORY_PATH = STATE_PATH / "ai_strategy_memory.json"
EVOLUTION_JOURNAL_PATH = PROJECT_ROOT / "EVOLUTION_JOURNAL.md"


@dataclass
class AIStrategyMemory:
    version: int = 1
    last_updated: str = ""
    analyst_model: str = "Antigravity Super-Agent (Google DeepMind)"
    market_regime: str = "NEUTRAL_CHOP" # "BULL_MOMENTUM", "BEAR_DEFENSE", "NEUTRAL_CHOP", "VOLATILE_SHAKEOUT"
    market_regime_summary: str = ""
    min_coin_price_krw: float = 50.0 # 잡코인 차단 기준
    min_entry_confidence: float = 75.0 # 최소 진입 확신도
    loss_cooldown_minutes: int = 15 # 손절 후 시장 휴식 시간
    breakeven_lock_pct: float = 1.0 # 본전 스탑 가동 기준 (+1.0%)
    
    preferred_sectors: List[str] = None # 선호 섹터/코인
    banned_markets: List[str] = None # 진입 금지 코인
    market_score_biases: Dict[str, float] = None # 코인별 가감점
    
    strategic_commandments: List[str] = None # 퀀트 행동 지침
    post_mortem_lessons: List[str] = None # 사후 복기 교훈

    def __post_init__(self):
        if self.preferred_sectors is None:
            self.preferred_sectors = ["DeFi", "Layer1", "High-Liquidity"]
        if self.banned_markets is None:
            self.banned_markets = ["KRW-FCT2", "KRW-GHX", "KRW-COTI"]
        if self.market_score_biases is None:
            self.market_score_biases = {
                "KRW-LINK": +5.0,
                "KRW-ETC": +5.0,
                "KRW-A": +5.0,
                "KRW-TRX": +5.0,
                "KRW-UNI": +5.0,
                "KRW-FCT2": -20.0,
                "KRW-GHX": -20.0,
                "KRW-COTI": -15.0,
            }
        if self.strategic_commandments is None:
            self.strategic_commandments = []
        if self.post_mortem_lessons is None:
            self.post_mortem_lessons = []


def load_ai_memory() -> AIStrategyMemory:
    if AI_MEMORY_PATH.exists():
        try:
            data = json.loads(AI_MEMORY_PATH.read_text(encoding="utf-8"))
            return AIStrategyMemory(**{k: v for k, v in data.items() if k in AIStrategyMemory.__dataclass_fields__})
        except Exception:
            pass
    return AIStrategyMemory()


def save_ai_memory(mem: AIStrategyMemory):
    AI_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    AI_MEMORY_PATH.write_text(json.dumps(asdict(mem), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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
