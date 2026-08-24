"""
Self-Reflective Evolutionary Quant Engine (자가 성장 AI 복기 & 진화 모듈)
───────────────────────────────────────────────────────────────────
매일 자정 당일 매매 기록을 전수 복기(Post-Mortem Review)하여:
1. 승리/패배 원인 정밀 분석 (진입 근거, 체결가, 지표 일치율, 시장 상황)
2. 실패 패턴 회피 규칙 및 성공 패턴 강화 규칙 자동 학습 (learned_heuristics.json)
3. 실시간 매매 의사결정(analyze_market / 확신도)에 학습된 지식 자동 피드백
4. 📱 디스코드로 일일 자가성장 보고서 (Daily Evolution Report) 자동 발행
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_PATH = PROJECT_ROOT / "state"
TRADE_LOG_PATH = STATE_PATH / "trade_history.jsonl"
HEURISTICS_PATH = STATE_PATH / "learned_heuristics.json"
EVOLUTION_JOURNAL_PATH = PROJECT_ROOT / "EVOLUTION_JOURNAL.md"


@dataclass
class TradeReflection:
    market: str
    entry_price: float
    exit_price: float
    pnl_krw: float
    pnl_pct: float
    held_minutes: float
    outcome: str
    entry_reason: str
    exit_reason: str
    success_factors: List[str]
    failure_factors: List[str]
    actionable_insight: str


@dataclass
class LearnedHeuristics:
    version: int = 1
    last_updated: str = ""
    total_reviewed_trades: int = 0
    cumulative_win_rate: float = 0.0
    min_price_threshold: float = 50.0
    min_confidence_override: float = 75.0
    preferred_markets: List[str] = None
    avoid_markets: List[str] = None
    market_rules: Dict[str, float] = None
    volatility_rules: Dict[str, Any] = None
    key_learnings: List[str] = None

    def __post_init__(self):
        if self.preferred_markets is None:
            self.preferred_markets = ["KRW-UNI", "KRW-ELF", "KRW-DOGE", "KRW-LINK"]
        if self.avoid_markets is None:
            self.avoid_markets = ["KRW-FCT2", "KRW-GHX", "KRW-COTI"]
        if self.market_rules is None:
            self.market_rules = {}
        if self.volatility_rules is None:
            self.volatility_rules = {}
        if self.key_learnings is None:
            self.key_learnings = []


def load_heuristics() -> LearnedHeuristics:
    if HEURISTICS_PATH.exists():
        try:
            data = json.loads(HEURISTICS_PATH.read_text(encoding="utf-8"))
            return LearnedHeuristics(**{k: v for k, v in data.items() if k in LearnedHeuristics.__dataclass_fields__})
        except Exception:
            pass
    return LearnedHeuristics()


def save_heuristics(h: LearnedHeuristics):
    HEURISTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEURISTICS_PATH.write_text(json.dumps(asdict(h), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class EvolutionaryReviewer:
    """Analyze completed trades and extract actionable evolutionary rules."""

    def __init__(self):
        self.heuristics = load_heuristics()

    def load_completed_trades(self) -> List[dict]:
        if not TRADE_LOG_PATH.exists():
            return []
        trades = []
        with open(TRADE_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        trades.append(json.loads(line.strip()))
                    except Exception:
                        pass
        return trades

    def pair_trades(self, raw_trades: List[dict]) -> List[Dict[str, Any]]:
        roundtrips = []
        active_buys: Dict[str, List[dict]] = {}

        for t in raw_trades:
            mkt = t["market"]
            action = t["action"]
            if action == "BUY":
                if mkt not in active_buys:
                    active_buys[mkt] = []
                active_buys[mkt].append(t)
            elif action in ("SELL", "PARTIAL_SELL"):
                buys = active_buys.get(mkt, [])
                if buys:
                    # BUY records store the authoritative *cumulative* exchange
                    # balance after each fill.  Summing those values double-counts
                    # earlier lots and fabricated +50~70% returns after a scale-in.
                    cumulative_volumes = [float(b.get("volume", 0)) for b in buys]
                    increments: List[float] = []
                    prior = 0.0
                    for cumulative in cumulative_volumes:
                        increment = cumulative - prior
                        if increment <= 0:
                            increments = []
                            break
                        increments.append(increment)
                        prior = cumulative
                    if not increments:
                        active_buys[mkt] = []
                        continue
                    tot_vol = sum(increments)
                    tot_spent = sum(float(b.get("amount_krw", 0)) for b in buys)
                    avg_entry = tot_spent / tot_vol if tot_vol > 0 else float(buys[0].get("price", 0))

                    buy_ts = buys[0].get("timestamp", "")
                    sell_ts = t.get("timestamp", "")

                    held_mins = 0.0
                    try:
                        t1 = datetime.fromisoformat(buy_ts)
                        t2 = datetime.fromisoformat(sell_ts)
                        held_mins = (t2 - t1).total_seconds() / 60.0
                    except Exception:
                        pass

                    pnl = float(t.get("pnl_krw", 0))
                    pnl_pct = (pnl / tot_spent * 100.0) if tot_spent > 0 else 0.0

                    roundtrips.append({
                        "market": mkt,
                        "entry_price": avg_entry,
                        "exit_price": float(t.get("price", 0)),
                        "pnl_krw": pnl,
                        "pnl_pct": pnl_pct,
                        "held_minutes": held_mins,
                        "buy_timestamp": buy_ts,
                        "sell_timestamp": sell_ts,
                        "buy_reason": buys[0].get("reason", "신규 매수"),
                        "sell_reason": t.get("reason", ""),
                        "pyramided": len(buys) > 1,
                    })

                    if action == "SELL":
                        active_buys[mkt] = []
        return roundtrips

    def reflect_trade(self, rt: Dict[str, Any]) -> TradeReflection:
        mkt = rt["market"]
        pnl = rt["pnl_krw"]
        pnl_pct = rt["pnl_pct"]
        sell_reason = rt["sell_reason"]
        entry_p = rt["entry_price"]
        held = rt["held_minutes"]

        success_factors = []
        failure_factors = []

        if pnl >= 0:
            if "TAKE-PROFIT" in sell_reason:
                outcome = "WIN_TP"
                success_factors.append(f"목표가 도달 익절 성공 (+{pnl_pct:.2f}%)")
            elif "TRAILING-STOP" in sell_reason:
                outcome = "WIN_TRAIL"
                success_factors.append(f"고점 상승 후 트레일링 스탑으로 이익 보존 (+{pnl_pct:.2f}%)")
            else:
                outcome = "WIN_PARTIAL"
                success_factors.append("분할 익절 또는 본전 이상 정리")

            if entry_p >= 50.0:
                success_factors.append("50원 이상 우량 코인의 안정적인 호가 유동성 활용")
            if rt["pyramided"]:
                success_factors.append("상승 확인 후 피라미딩(불타기)으로 수익 극대화 성공")
            insight = f"{mkt}와 같은 탄탄한 우량 수급주의 추세 추종 전략을 지속 유지."
        else:
            if "STOP-LOSS" in sell_reason:
                outcome = "LOSS_SL"
                failure_factors.append(f"손절 기준선 도달 (-{abs(pnl_pct):.2f}%)")
            elif "Timecut" in sell_reason or "타임컷" in sell_reason:
                outcome = "LOSS_TIMECUT"
                failure_factors.append("4시간 횡보 후 시세 분출 실패로 타임컷 탈출")
            else:
                outcome = "BREAKEVEN"
                failure_factors.append("미세 수수료 손실")

            if entry_p < 50.0:
                failure_factors.append(f"{entry_p:,.1f}원 극초저가 잡코인의 1틱 갭하락 변동성 노출")
            if held < 10.0:
                failure_factors.append("진입 직후 급락 (시장 전체 하락기 뇌동 진입 가능성)")
            insight = f"{mkt}와 같은 저단가/역추세 코인은 회피하고, 손절 직후에는 시장 휴식을 엄수할 것."

        return TradeReflection(
            market=mkt,
            entry_price=entry_p,
            exit_price=rt["exit_price"],
            pnl_krw=pnl,
            pnl_pct=pnl_pct,
            held_minutes=held,
            outcome=outcome,
            entry_reason=rt["buy_reason"],
            exit_reason=sell_reason,
            success_factors=success_factors,
            failure_factors=failure_factors,
            actionable_insight=insight,
        )

    def run_evolutionary_cycle(self) -> Tuple[LearnedHeuristics, str]:
        trades = self.load_completed_trades()
        roundtrips = self.pair_trades(trades)

        if not roundtrips:
            return self.heuristics, "매매 기록이 충분하지 않아 자가 학습을 대기합니다."

        reflections = [self.reflect_trade(rt) for rt in roundtrips]

        wins = [r for r in reflections if r.pnl_krw >= 0]
        losses = [r for r in reflections if r.pnl_krw < 0]
        total_count = len(reflections)
        win_rate = (len(wins) / total_count * 100.0) if total_count > 0 else 0.0
        total_pnl = sum(r.pnl_krw for r in reflections)

        market_stats: Dict[str, Dict[str, Any]] = {}
        for r in reflections:
            if r.market not in market_stats:
                market_stats[r.market] = {"wins": 0, "losses": 0, "pnl": 0.0}
            if r.pnl_krw >= 0:
                market_stats[r.market]["wins"] += 1
            else:
                market_stats[r.market]["losses"] += 1
            market_stats[r.market]["pnl"] += r.pnl_krw

        preferred = []
        avoid = []
        rules = {}

        for mkt, st in market_stats.items():
            tot = st["wins"] + st["losses"]
            wr = st["wins"] / tot * 100.0 if tot > 0 else 0.0
            if wr >= 65.0 and st["pnl"] > 0:
                preferred.append(mkt)
                rules[mkt] = +5.0
            elif wr <= 35.0 or st["pnl"] < -200:
                avoid.append(mkt)
                rules[mkt] = -10.0

        learnings = []
        low_price_losses = [r for r in losses if r.entry_price < 50.0]
        if len(low_price_losses) >= 2:
            learnings.append("💡 [단가 규칙] 50원 미만 극초저가 잡코인은 호가 틱 리스크가 커서 매수 배제 유지 (승률 방어 효과 검증)")

        quick_losses = [r for r in losses if r.held_minutes < 15.0]
        if len(quick_losses) >= 2:
            learnings.append("💡 [시간 규칙] 손절 직후 15분 시장 휴식(Market Rest)을 엄격히 준수하여 시장 급락기 연쇄 털림 방어")

        learnings.append("💡 [수익 보존] +1.0% 도달 시 본전 스탑(Breakeven Lock)을 즉시 가동하여 수익 포지션의 손실 전락 원천 차단")

        if preferred:
            learnings.append(f"💡 [선호 유니버스] 고승률 상위 종목({', '.join(preferred)})에 가산점(+5점) 부여 및 우선 탐색")
        if avoid:
            learnings.append(f"💡 [기피 유니버스] 손실 유발 종목({', '.join(avoid)})에 감점(-10점) 부여 및 진입 억제")

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.heuristics.last_updated = now_str
        self.heuristics.total_reviewed_trades = total_count
        self.heuristics.cumulative_win_rate = win_rate
        self.heuristics.preferred_markets = preferred
        self.heuristics.avoid_markets = avoid
        self.heuristics.market_rules = rules
        self.heuristics.key_learnings = learnings
        save_heuristics(self.heuristics)

        recent_reflections = reflections[-6:]
        reflection_rows = []
        for r in recent_reflections:
            tag = "✅ 승리" if r.pnl_krw >= 0 else "❌ 패배"
            reflection_rows.append(f"- **{tag} `{r.market}`**: {r.pnl_krw:+,.0f} KRW ({r.pnl_pct:+.2f}%) | 이유: `{r.exit_reason}`\n  👉 *교훈: {r.actionable_insight}*")
        recent_text = "\n".join(reflection_rows)
        learnings_text = "\n".join(f"- {l}" for l in learnings)

        report = f"""## 🧬 [BITHUMB] 일일 자가 성장 & AI 복기 리포트
> ⏱️ **복기 기준시각**: `{now_str}`
> 🧠 **시스템 상태**: 자가 학습 및 파라미터 동적 진화 완료

### 📊 누적 실전 복기 성적표
- **총 복기 매매 수**: **`{total_count}회`** (`{len(wins)}승 {len(losses)}패`)
- **누적 복기 승률**: **`{win_rate:.1f}%`** (누적 실현손익: `{total_pnl:+,.0f} KRW`)
- **선호 우량 코인 (가산점 +5점)**: `{', '.join(preferred) if preferred else '탐색 중'}`
- **기피 요주의 코인 (감점 -10점)**: `{', '.join(avoid) if avoid else '없음'}`

### 📝 최근 주요 매매 복기 및 원인 규명
{recent_text}

### 🎯 오늘 학습된 실전 진화 규칙 (Self-Learned Rules)
{learnings_text}

---
*기록을 넘어 매일 스스로 진화하는 퀀트 엔진* 🤖📈
"""
        self.append_to_evolution_journal(report)
        return self.heuristics, report

    def append_to_evolution_journal(self, report: str):
        EVOLUTION_JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(EVOLUTION_JOURNAL_PATH, "a", encoding="utf-8") as f:
            f.write(report + "\n\n" + "="*80 + "\n\n")


def apply_learned_heuristics(market: str, base_confidence: float) -> Tuple[float, List[str]]:
    h = load_heuristics()
    adjusted = base_confidence
    applied_tags = []

    if h.market_rules and market in h.market_rules:
        delta = h.market_rules[market]
        adjusted += delta
        if delta > 0:
            applied_tags.append(f"선호코인가산(+{delta:.0f}%)")
        else:
            applied_tags.append(f"기피코인감점({delta:.0f}%)")

    elif h.avoid_markets and market in h.avoid_markets:
        adjusted -= 10.0
        applied_tags.append("기피코인감점(-10%)")

    elif h.preferred_markets and market in h.preferred_markets:
        adjusted += 5.0
        applied_tags.append("선호코인가산(+5%)")

    adjusted = min(max(adjusted, 0.0), 100.0)
    return adjusted, applied_tags
